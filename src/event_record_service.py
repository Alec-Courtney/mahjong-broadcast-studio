"""Independent write boundary for event results, penalties and deletions.

Other-table corrections and record deletions survive table undo/redo. Result
changes are rebased into table snapshots without creating a table undo action.
Persistence succeeds before the new state is published.
"""
from __future__ import annotations

import copy
import uuid

import tournament
from event_metadata import now
from event_penalties import validate_penalties
from state_snapshots import freeze, copy_state


def replace_record(state, record, profiles):
    broadcast = state['broadcast']
    matches = broadcast['matches']
    index = next((index for index, match in enumerate(matches)
                  if match['id'] == record['id']), None)
    value = copy.deepcopy(record)
    if index is None:
        matches.append(value)
    else:
        matches[index] = value
    # Older table snapshots may predate adding an entrant to the event roster.
    # Keep referenced profiles so undo never creates dangling result identities.
    roster = broadcast['roster']
    known = {player['id'] for player in roster}
    for result in record['results']:
        if result['id'] not in known:
            roster.append(copy.deepcopy(profiles[result['id']]))
            known.add(result['id'])


def _penalty_history(stack, match, penalties, later_ids):
    """Keep table undo up to the boundary where this half-game was established."""
    from ranking_order import match_seats

    rebased = []
    match_players = {result['id'] for result in match['results']}
    seats = match_seats(match)
    for state, info in reversed(stack):
        archive = state['broadcast']['matches']
        index = next((i for i, record in enumerate(archive) if record['id'] == match['id']), None)
        if index is None:
            ids = [player['id'] for player in state['players']]
            if (state['id'] != match['id'] or set(ids) != match_players
                    or (seats and ids != seats) or len(archive) >= 10000):
                break
            # The half exists in this snapshot but is not yet confirmed.
            # Preserve the decision while keeping its points out of the ledger.
            position = next((i for i, record in enumerate(archive) if record['id'] in later_ids), len(archive))
            archive.insert(position, freeze(dict(match, committed=False, penalties=penalties)))
        else:
            record = dict(archive[index], penalties=penalties)
            try:
                validate_penalties(record)
            except ValueError:
                break
            archive[index] = freeze(record)
        rebased.append((state, info))
    return list(reversed(rebased))


class EventRecordService:
    def __init__(self, model):
        self.model = model

    def update_recorded_at(self, match_id, recorded_at, expected_record=None):
        """Edit record metadata independently of table undo and score changes."""
        from ranking_order import match_seats
        from ranking_reports import confirmed_matches

        model = self.model
        match = next((record for record in confirmed_matches(model.state) if record['id'] == match_id), None)
        if match is None:
            raise ValueError('该半庄记录已删除或尚未确认，请重新选择')
        if expected_record is not None and match != expected_record:
            raise ValueError('该半庄记录已改变，请重新打开时间编辑')
        before = copy_state(model.state)
        after = copy_state(before)
        seats = match_seats(match)

        def rebase(state):
            archive = state['broadcast']['matches']
            index = next((i for i, record in enumerate(archive) if record['id'] == match_id), None)
            if index is not None:
                archive[index] = freeze(dict(archive[index], recorded_at=recorded_at))
            elif (state['id'] == match_id and [player['id'] for player in state['players']] == seats
                    and len(archive) < 10000):
                # Keep the edited time if confirmation is undone and repeated.
                archive.append(freeze(dict(match, committed=False, recorded_at=recorded_at)))

        rebase(after)
        tournament.validate_broadcast(after)
        model.engine.validate(after)
        undo, redo = copy.deepcopy((model.undo, model.redo))
        for state, _ in undo + redo:
            rebase(state)
        transition = copy.deepcopy(model.last_transition)
        if transition:
            rebase(transition[0])
            rebase(transition[1])
        model._publish(before, after, copy.deepcopy(model.settings), model.last_info,
                       undo, redo, transition, 'match_record_time', display=True)

    def write_manual(self, record):
        model = self.model
        if record.get('source') != 'manual' or record['id'] == model.state['id']:
            raise ValueError('此入口只保存其他桌补录成绩')
        prior = next((item for item in model.state['broadcast']['matches'] if item['id'] == record['id']), None)
        record = dict(record)
        replacement = record.pop('_replaces_record', None)
        if replacement and (replacement != record['id'] or prior is None):
            raise ValueError('待更正半庄记录已删除，请重新打开成绩记录')
        record.pop('penalties', None)
        if prior and 'penalties' in prior:
            record['penalties'] = prior['penalties']
        record.pop('recorded_at', None)
        if prior is None:
            record['recorded_at'] = now()
        elif 'recorded_at' in prior:
            record['recorded_at'] = prior['recorded_at']
        record = freeze(copy.deepcopy(record))
        before = copy_state(model.state)
        after = copy_state(before)
        profiles = {player['id']: player for player in before['broadcast']['roster']}
        replace_record(after, record, profiles)
        tournament.validate_broadcast(after)
        model.engine.validate(after)
        # Rebase into copies first. On any write failure, state and both history
        # stacks remain untouched, and no observer sees an unpersisted result.
        undo, redo = copy.deepcopy((model.undo, model.redo))
        for state, _ in undo + redo:
            replace_record(state, record, profiles)
        model._save_snapshot(after, model.settings, model.last_info, model.event)
        model.state = after
        model.undo, model.redo = undo, redo
        model.changed.emit(before, after, 'manual_result')
        model.display_changed.emit()
        return record['id']

    def delete_match(self, match_id, expected_record=None):
        """Remove a confirmed result from the ledger and every table snapshot."""
        from event_metadata import validate_event
        from ranking_reports import confirmed_matches

        model = self.model
        match = next((item for item in confirmed_matches(model.state) if item['id'] == match_id), None)
        if match is None:
            raise ValueError('该半庄记录已删除或尚未确认，请重新选择')
        if expected_record is not None and match != expected_record:
            raise ValueError('该半庄记录已改变，请核对后重新删除')
        before = copy_state(model.state)
        after = copy_state(before)
        event = copy.deepcopy(model.event)
        node_id = match.get('bracket_node_id')
        if node_id and event:
            nodes = event['bracket']['nodes']
            descendants = {node_id}
            while True:
                linked = {node['id'] for node in nodes if any(
                    source.get('node') in descendants and str(index) not in node.get('source_overrides', {})
                    for index, source in enumerate(node['sources']))}
                if linked.issubset(descendants):
                    break
                descendants.update(linked)
            descendants.discard(node_id)
            if any(node['status'] != 'pending' for node in nodes if node['id'] in descendants) or any(
                    record.get('bracket_node_id') in descendants for record in confirmed_matches(before)):
                raise ValueError('下游淘汰桌已经开赛，不能删除上游半庄记录')
            node = next((node for node in nodes if node['id'] == node_id), None)
            if node is not None:
                node.update(status='running', winners=[])
                node.pop('result_signature', None)
                node.pop('ranking_rule', None)

        def remove(state):
            state['broadcast']['matches'] = [record for record in state['broadcast']['matches']
                                              if record['id'] != match_id]
            if state['id'] == match_id:
                state['broadcast']['result_discarded'] = True

        remove(after)
        tournament.validate_broadcast(after)
        model.engine.validate(after)
        if event:
            event['updated_at'] = now()
            validate_event(event, after)
        undo, redo = copy.deepcopy((model.undo, model.redo))
        for state, _ in undo + redo:
            remove(state)
        # Playback transitions also carry result ledgers for standings changes.
        transition = copy.deepcopy(model.last_transition)
        if transition:
            remove(transition[0])
            remove(transition[1])
        model._publish(before, after, copy.deepcopy(model.settings), model.last_info,
                       undo, redo, transition, 'delete_match_record', event=event, display=True)

    def penalty_match(self, match_id):
        from ranking_reports import confirmed_matches

        match = next((m for m in confirmed_matches(self.model.state) if m['id'] == match_id), None)
        if match is None:
            raise ValueError('请先确认该半庄的成绩，再登记判罚')
        return match

    def save_penalty(self, match_id, *, scope, target, points, time, reason, note='', penalty_id=None):
        match = self.penalty_match(match_id)
        if scope == 'team' and (self.model.event or {}).get('kind') != 'team_league':
            raise ValueError('只有团队赛可以直接扣除队伍积分')
        entry = dict(id=penalty_id or uuid.uuid4().hex, scope=scope, target=target,
                     points=points, time=time, reason=reason, note=note)
        entries = list(match.get('penalties', []))
        if penalty_id:
            index = self._active_penalty_index(entries, penalty_id)
            entries[index] = entry
        else:
            entries.append(entry)
        self._save_penalties(match, entries)
        return entry['id']

    def void_penalty(self, match_id, penalty_id):
        match = self.penalty_match(match_id)
        entries = list(match.get('penalties', []))
        index = self._active_penalty_index(entries, penalty_id)
        entries[index] = dict(entries[index], voided_at=now())
        self._save_penalties(match, entries)

    @staticmethod
    def _active_penalty_index(entries, penalty_id):
        index = next((i for i, entry in enumerate(entries) if entry['id'] == penalty_id), None)
        if index is None or entries[index].get('voided_at'):
            raise ValueError('该判罚已撤销或不存在，请重新选择')
        return index

    def _save_penalties(self, match, entries):
        model = self.model
        replacement = dict(match, penalties=entries)
        validate_penalties(replacement)
        replacement = freeze(replacement)
        before = copy_state(model.state)
        after = copy_state(before)
        archive = after['broadcast']['matches']
        index = next(i for i, item in enumerate(archive) if item['id'] == match['id'])
        archive[index] = replacement
        later_ids = {record['id'] for record in archive[index+1:]}
        undo, redo = copy.deepcopy((model.undo, model.redo))
        undo = _penalty_history(undo, match, replacement['penalties'], later_ids)
        redo = _penalty_history(redo, match, replacement['penalties'], later_ids)
        model._publish(before, after, copy.deepcopy(model.settings), model.last_info,
                       undo, redo, model.last_transition, 'match_penalty', display=True)
