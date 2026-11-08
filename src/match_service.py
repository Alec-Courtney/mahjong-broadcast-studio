"""Match lifecycle commands shared by every operator entry point.

This application service knows the table and event rules, but no Qt widgets.
The model remains the transaction/signalling boundary. Legacy model methods
delegate here so scripts and saved-event tools retain their existing API.
"""
from __future__ import annotations

import copy
import uuid

import tournament
from event_roster import require_visible_seats
from state_snapshots import copy_state


def begin_match(state, engine):
    """Replace the current table while retaining its event and archived matches."""
    broadcast = tournament.ensure_broadcast(state)
    if any(match['id'] == state['id'] and match.get('penalties') for match in broadcast['matches']):
        require_completed_match(state)
    if broadcast['hands']:
        tournament.archive_match(state, False)
    players = copy.deepcopy(state['players'])
    rules = copy.deepcopy(state['rules'])
    rules['startPoints'] = broadcast['config'].get('start_points', rules['startPoints'])
    fresh = engine.invoke('createState', {
        'players': [{'id': player['id'], 'name': player['name']} for player in players],
        'rules': rules,
        'title': state.get('title', ''),
        'initialDealer': state['initialDealer'],
    })
    fresh['manualDisplay'] = True
    for player, profile in zip(fresh['players'], players):
        for key in ('photo', 'team_logo', 'color', 'team_color', 'team', 'team_id'):
            player[key] = profile.get(key, '')
        player.update(tenpai=False, waits=[], remaining=None)
    fresh['broadcast'] = broadcast
    broadcast.update(
        hands=[], current_hand=uuid.uuid4().hex,
        initial_scores=[player['score'] for player in fresh['players']],
        initial_players=copy.deepcopy(fresh['players']),
        initial_label='开始', partial=False,
    )
    broadcast.pop('active_node_id', None)
    state.clear()
    state.update(fresh)


def require_completed_match(state):
    """Protect an existing live match before another table is loaded."""
    broadcast = state['broadcast']
    record = next((match for match in broadcast['matches'] if match['id'] == state['id']), {})
    if (broadcast['hands'] or state.get('lastResult') or record.get('penalties')) and not (
            record.get('committed') and state['phase'] == 'finished'):
        raise ValueError('当前半庄尚未结束并确认，请先处理当前半庄')


class MatchService:
    def __init__(self, model):
        self.model = model

    def commit(self):
        """Compatibility command; production controls use confirm_finished()."""
        model = self.model
        if not model.state.get('lastResult'):
            raise ValueError('请先结算本局，再将本半庄计入赛事')
        model.update(lambda state: tournament.archive_match(state, True), 'commit_match')
        model.display_changed.emit()

    def confirm_finished(self):
        if self.model.state['phase'] != 'finished':
            raise ValueError('请先完成半庄终局，再记录赛事积分')
        self.commit()

    def start(self):
        """Start a new free table, retaining the legacy script command."""
        model = self.model
        require_visible_seats(model.state, [player['id'] for player in model.state['players']])
        model.update(lambda state: begin_match(state, model.engine), 'new_match',
                     info=None, reset_transition=True, display=True)

    def start_next(self):
        """Return 'started', or 'schedule' when a knockout table must be chosen."""
        model = self.model
        if model.event_store is None:
            self.start()
            return 'started'
        require_completed_match(model.state)
        if (model.event or {}).get('kind') == 'individual_knockout':
            from event_schedule import node_matches, start_node
            identity = model.state['broadcast'].get('active_node_id')
            node = next((item for item in model.event['bracket']['nodes']
                         if item['id'] == identity), None)
            if (node and node['status'] != 'complete'
                    and len(node_matches(model.state, identity)) < node['planned_games']):
                start_node(model.event_store, identity)
                return 'started'
            return 'schedule'
        self.start()
        return 'started'

    def start_with_seats(self, seats, roster=None):
        """Validate and publish a selected league lineup as one transaction."""
        from event_metadata import validate_event
        from media_store import manage_photos
        model = self.model
        if (model.event or {}).get('kind') == 'individual_knockout':
            raise ValueError('淘汰赛请从淘汰赛程选择下一场')
        if not isinstance(seats,list) or len(seats)!=4 or any(not isinstance(identity,str) or not identity for identity in seats) or len(set(seats))!=4:
            raise ValueError('四个座位必须选择不同选手')
        require_completed_match(model.state)
        if not model.state['broadcast']['hands'] and (model.state.get('pot') or any(
                player.get('riichi') or player.get('melds') for player in model.state['players'])):
            raise ValueError('当前半庄尚未结束并确认，请先处理当前半庄')
        before = copy_state(model.state)
        state = copy_state(before)
        if roster is not None:tournament.configure(state, {'roster': roster})
        require_visible_seats(state, seats)
        begin_match(state, model.engine)
        tournament.configure(state, {'seats': seats})
        tournament.on_update(before, state, 'new_match')
        model.engine.validate(state)
        if model.event:validate_event(model.event, state)
        manage_photos(state, model.path)
        from transition_settings import settings_for_transition
        settings = settings_for_transition(model.settings, before, state, 'new_match')
        settings.update(show_yaku=False, final_points_mode='scores')
        model._save_snapshot(state, settings, None, model.event)
        model.state, model.settings = state, settings
        model.last_info = None
        model.last_transition = None
        model.undo.clear(); model.redo.clear()
        model.changed.emit(before, state, 'new_match')
        model.display_changed.emit()

    def start_scheduled(self, seats, node_id, qualification_slots, event):
        """Publish one complete scheduled table, never intermediate seat states.

        event_schedule validates the chosen node and its upstream results. This
        method commits the resulting table, node and bracket metadata together.
        """
        from event_metadata import validate_event
        model = self.model
        before = copy_state(model.state)
        state = copy_state(before)
        event = copy.deepcopy(event)
        require_visible_seats(state, seats)
        begin_match(state, model.engine)
        tournament.configure(state, {
            'seats': seats,
            'qualification_mode': 'individual',
            'qualification_slots': qualification_slots,
        })
        state['broadcast']['active_node_id'] = node_id
        tournament.on_update(before, state, 'new_match')
        model.engine.validate(state)
        validate_event(event, state)
        from transition_settings import settings_for_transition
        settings = settings_for_transition(model.settings, before, state, 'new_match')
        settings.update(show_yaku=False, final_points_mode='scores')
        model._save_snapshot(state, settings, None, event)
        model.state, model.event, model.settings = state, event, settings
        model.last_info = None
        model.last_transition = None
        model.undo.clear()
        model.redo.clear()
        model.changed.emit(before, state, 'new_match')
        model.display_changed.emit()
