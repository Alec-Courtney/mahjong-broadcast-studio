"""Host adapter for the replaceable JSON automation boundary.

Only this layer knows the existing Model and console. Detector and calculation
implementations are optional consumers of the same draft contract.
"""
from __future__ import annotations

import copy
import json
import uuid
import math
from statistics import median
from collections import Counter

from PySide6.QtCore import QObject, Signal
from automation_api import DraftError, DraftInbox, LocalDraftServer, TILES


class BroadcastAutomation(QObject):
    draft_received = Signal(str)
    context_changed = Signal()

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.inbox = DraftInbox()
        self.server = LocalDraftServer(self.inbox)
        self.inbox.on_submitted = self.draft_received.emit
        self._signature = None
        self._closed = False
        self._update_context()
        model.changed.connect(self._on_changed)

    def _on_changed(self, before, after, kind):
        force = kind in ('next_hand', 'clear', 'new_match', 'start_match', 'import', 'restore')
        self._update_context(force=force)

    def _update_context(self, force=False):
        state = self.model.state
        view = {key: copy.deepcopy(state.get(key)) for key in
                ('phase', 'roundIndex', 'dealer', 'honba', 'pot', 'rules', 'doraIndicators')}
        view['players'] = [{key: copy.deepcopy(player.get(key)) for key in
                            ('id', 'name', 'score', 'melds', 'riichi')}
                           for player in state['players']]
        signature = json.dumps([str(self.model.path), (self.model.event or {}).get('id'), view], sort_keys=True, ensure_ascii=False)
        if force or signature != self._signature:
            self._signature = signature
            from mahjong_values import YAKU
            self.inbox.set_context(dict(schema_version=1, context_id=uuid.uuid4().hex,
                state=view, capabilities={'draft_kinds': ['tiles', 'waits', 'win'],
                'confirmation': 'operator', 'yaku': [name for name, _, _ in YAKU]}))
            self.context_changed.emit()

    def context(self):
        return self.inbox.context()

    def submit(self, kind, player, data, *, source='manual', context_id=None, request_id=None):
        return self.inbox.submit(dict(schema_version=1, kind=kind, player=player, data=data,
            source=source, context_id=context_id or self.context()['context_id'], request_id=request_id))

    def analyse_tiles(self, identity, *, riichi=False):
        from hand_calculation import analyse_hand
        draft = self.inbox.get(identity, pending=True)
        if draft['kind'] != 'tiles':
            raise DraftError('请选择牌面草稿')
        result = analyse_hand(draft['data']['tiles'], state=self.context()['state'],
                              player=draft['player'], riichi=riichi)
        if not result['tenpai']:
            return result, None
        player = self.model.state['players'][draft['player']]
        waits = self.submit('waits', draft['player'], dict(waits=result['waits'],
            no_yaku=result.get('no_yaku', []), remaining=player.get('remaining'),
            furiten=bool(player.get('furiten', False)), analysis=result,
            tiles=list(draft['data']['tiles']), parent_id=identity),
            source=draft['source'], context_id=draft['context_id'])
        self.inbox.mark(identity, 'applied')
        return result, waits

    def calculate_win(self, identity, win_tile, *, tsumo=False, riichi=False, ippatsu=False,
                      ura_indicators=None, loser=None, win_tile_included=False, special_flags=None):
        from hand_calculation import calculate_win
        draft = self.inbox.get(identity, pending=True)
        tiles = draft['data'].get('tiles')
        if not tiles:
            raise DraftError('这份草稿没有暗手牌，请先识别或输入手牌')
        result = calculate_win(tiles, win_tile, state=self.context()['state'],
            player=draft['player'], tsumo=tsumo, riichi=riichi, ippatsu=ippatsu,
            ura_indicators=ura_indicators or [], win_tile_included=win_tile_included,
            special_flags=special_flags)
        if not result['valid']:
            raise DraftError(result.get('message') or result.get('error') or '无法计算和牌')
        data = dict(type='tsumo' if tsumo else 'ron', loser=(draft['player'] + 1) % 4 if loser is None else loser,
            han=result['han'], fu=(result['fu'] or 30) if result['yakuman'] else result['fu'], yakuman=result['yakuman'], yaku=result['yaku'],
            bonuses=result['bonuses'], calculation=result, win_tile=win_tile, tiles=list(tiles),
            ura_indicators=list(ura_indicators or []), parent_id=identity)
        suggestion = self.submit('win', draft['player'], data, source=draft['source'], context_id=draft['context_id'])
        self.inbox.mark(identity, 'applied')
        return suggestion

    def confirm_waits(self, identity, *, riichi=False, no_yaku=None, furiten=None, remaining=None):
        draft = self.inbox.get(identity, pending=True)
        if draft['kind'] != 'waits':
            raise DraftError('请选择听牌草稿')
        player_index = draft['player']
        current = self.model.state['players'][player_index]
        if not riichi and current.get('riichi'):
            raise DraftError('该选手已经立直；请在导播台更正立直状态后再提交普通听牌')
        if riichi and any(m['kind'] != 'ankan' for m in current['melds']):
            raise DraftError('有明副露，不能提交立直')
        data = copy.deepcopy(draft['data'])
        if no_yaku is not None:
            data['no_yaku'] = no_yaku
        if furiten is not None:
            data['furiten'] = furiten
        data['remaining'] = remaining
        # Reuse transport validation for the edited fields, without enqueuing.
        from automation_api import validate_payload
        validate_payload(dict(draft, data=data))

        def change(state):
            if riichi and not state['players'][player_index].get('riichi'):
                state.update(self.model.engine.apply(state, {'type': 'riichi', 'player': player_index}))
            player = state['players'][player_index]
            player.update(tenpai=True, waits=list(data['waits']), remaining=data.get('remaining'),
                          waits_no_yaku=list(data.get('no_yaku', [])), furiten=bool(data.get('furiten', False)))
        self.model.update(change, 'riichi' if riichi and not current.get('riichi') else 'waits')
        self.inbox.mark(identity, 'applied')
        self.draft_received.emit(identity)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.server.stop()
        self.inbox.on_submitted = None
        try:
            self.model.changed.disconnect(self._on_changed)
        except RuntimeError:
            pass


def hand_row_has_outlier(detections):
    """Reject an obvious stray river tile; never invent/remove detected tiles.

    A robust line is useful only for a predominantly single row of ten or more
    faces. Smaller open hands and providers without geometry keep count checks.
    """
    if len(detections) < 10:
        return False
    boxes = [getattr(d, 'bbox', None) for d in detections]
    if any(not box or len(box) != 4 or not all(math.isfinite(v) for v in box)
           or box[2] <= box[0] or box[3] <= box[1] for box in boxes):
        return False
    width = median(b[2] - b[0] for b in boxes)
    height = median(b[3] - b[1] for b in boxes)
    centers = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in boxes]
    slopes = [(y2 - y1) / (x2 - x1) for i, (x1, y1) in enumerate(centers)
              for x2, y2 in centers[i + 1:] if abs(x2 - x1) > width * .5]
    if not slopes:
        return False
    slope = median(slopes)
    intercept = median(y - slope * x for x, y in centers)
    distances = [abs(y - slope * x - intercept) / height for x, y in centers]
    return sum(d <= .65 for d in distances) >= .8 * len(centers) and max(distances) > 1.25


class StableHandGate:
    """Provider-neutral temporal gate; invalid frames break the consecutive run."""
    def __init__(self, stable_frames=3, minimum_confidence=.65):
        self.stable_frames = stable_frames
        self.minimum_confidence = minimum_confidence
        self.reset()

    def reset(self):
        self._last = None
        self._count = 0
        self._emitted = None
        self.valid_frame = False
        self.reason = ''

    def observe(self, detections, expected_count, *, melds=()):
        self.valid_frame = False
        self.reason = ''
        tiles = [getattr(d, 'tile', None) for d in detections]
        if len(tiles) != expected_count or any(t not in TILES or getattr(d, 'confidence', 0) < self.minimum_confidence for t, d in zip(tiles, detections)):
            self._last = None
            self._count = 0
            return None
        counts = Counter(('5' + t[1]) if t.startswith('0') else t for t in tiles)
        for meld in melds:
            counts.update(('5' + t[1]) if t.startswith('0') else t for t in meld['tiles'])
        if any(value > 4 for value in counts.values()) or any(tiles.count('0' + s) > 1 for s in 'mps'):
            self._last = None
            self._count = 0
            return None
        if hand_row_has_outlier(detections):
            self._last = None
            self._count = 0
            self.reason = '疑似混入牌河或举牌 · 请核对选区'
            return None
        self.valid_frame = True
        signature = tuple(sorted(tiles))
        self._count = self._count + 1 if signature == self._last else 1
        self._last = signature
        if self._count >= self.stable_frames and signature != self._emitted:
            self._emitted = signature
            return tiles
        return None
