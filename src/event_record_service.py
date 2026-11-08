"""Independent write boundary for results from other tables.

Other-table corrections must survive a director's table undo/redo. Their
records are rebased into table snapshots, without creating or clearing a live
table undo action. Persistence succeeds before the new state is published.
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

    def write_manual(self, record):
        model = self.model
        if record.get('source') != 'manual' or record['id'] == model.state['id']:
            raise ValueError('此入口只保存其他桌补录成绩')
        prior = next((item for item in model.state['broadcast']['matches'] if item['id'] == record['id']), None)
        record = dict(record)
        record.pop('penalties', None)
        if prior and 'penalties' in prior:
            record['penalties'] = prior['penalties']
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
