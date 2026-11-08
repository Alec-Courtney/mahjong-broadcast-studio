"""Recognition assists the original desk through its draft boundary.

This adapter owns transient observations and editable suggestions. It never
settles a hand or changes the selected player in response to a camera update.
"""
from __future__ import annotations

import copy
import json
import time
from collections import deque

from PySide6.QtCore import QObject, Qt, Signal, Slot, QSignalBlocker, QTimer
from PySide6.QtWidgets import QApplication, QMenu

from automation_api import DraftError
from automation_bridge import StableHandGate
from mahjong_values import compact, parse_tiles, tile_name
from recognition_config import normalize_recognition_config
import recognition_runtime
from tile_order import sort_hand_tiles

_FLAG_ROLES = {'立直', '两立直', '一发', '门前清自摸和'}
_LIFECYCLE = {'next_hand', 'clear', 'new_match', 'start_match', 'import', 'restore'}
_STALE_SECONDS = 4.0


class InlineAutomation(QObject):
    supports_parallel_preview = True
    preview_received = Signal(int, object, object)
    source_state_changed = Signal(int, str)

    def __init__(self, console):
        super().__init__(console)
        self.console = console
        self.service = console.automation
        self.model = console.model
        self.observations = {}
        self.wait_ids = {}
        self.wait_baselines = {}
        self.win_suggestions = {}
        self.win_errors = {}
        self.win_baseline = None
        self.win_owner = None
        self.win_inputs = {}
        self.win_states = {}
        self.manual_win_players = set()
        self.manual_hands = set()
        self.confirmed = {}
        self.workers = {}
        self.worker_info = {}
        self.gates = {}
        self._processed = set()
        self._order = deque()
        self._applying = False
        self._closed = False
        self._confirming = set()
        self._config = None
        self._source_signatures = {}
        self._source_revisions = [0] * 4
        self.latest_previews = {}
        self._fresh_hands = {}
        self._preview_paused = set()
        self.service.draft_received.connect(self.receive_draft, Qt.ConnectionType.QueuedConnection)
        self.model.changed.connect(self.model_changed)
        self.model.display_changed.connect(self.configure)
        for hand in console.recognized_hands:
            hand.hand_edited.connect(self.correct_hand)
            hand.source_requested.connect(self.source_menu)
            hand.hide()
        self._sync_hand_target()
        QTimer.singleShot(0, self.configure)
        self._freshness_timer = QTimer(self)
        self._freshness_timer.setInterval(750)
        self._freshness_timer.timeout.connect(self._check_freshness)

    def _remember(self, identity):
        if identity in self._processed:
            return False
        self._processed.add(identity)
        self._order.append(identity)
        while len(self._order) > 600:
            self._processed.discard(self._order.popleft())
        return True

    def _show(self, player, *, status=None, stale=None, pending=None):
        value = self.observations.get(player)
        if value is None:
            return
        if status is not None:
            value['status'] = status
        if stale is not None:
            value['stale'] = stale
        if pending is not None:
            value['pending'] = pending
        label = value.get('status', '')
        if player in self.manual_hands:
            label = '手动修正 · ' + label
        self.console.recognized_hands[player].set_observation(value['tiles'], source=value.get('source', ''),
            confidence=value.get('confidence'), status=label,
            stale=value.get('stale', False), pending=value.get('pending', False))
        self._set_hand_visible(player, not self._closed)

    def _sync_hand_target(self):
        c = self.console
        visible = not c.recognized_hands[c.selected].isHidden()
        c.target_buttons['hand'].setVisible(visible)
        if not visible and c.tile_target == 'hand':
            c.set_tile_target('waits')

    def _set_hand_visible(self, player, visible):
        hand = self.console.recognized_hands[player]
        changed = hand.isHidden() == visible
        hand.setVisible(visible)
        self._sync_hand_target()
        if changed:
            self.console.reflow_live_panels()

    def receive_tiles(self, player, tiles, *, source='external', confidence=None, context_id=None, operator=False):
        context_id = context_id or self.service.context()['context_id']
        draft = self.service.submit('tiles', player, {'tiles': list(tiles)}, source=source, context_id=context_id)
        self.receive_draft(draft['id'], operator=operator)
        if player in self.observations and confidence is not None and (player not in self.manual_hands or operator):
            self.observations[player]['confidence'] = confidence
            self._show(player)
        return draft

    @Slot(str)
    def receive_draft(self, identity, operator=False):
        if self._closed or not self._remember(identity):
            return
        try:
            draft = self.service.inbox.get(identity, pending=True)
        except DraftError:
            return
        player, data = draft['player'], draft['data']
        if player in self.manual_hands and not operator:
            self.service.inbox.mark(identity, 'dismissed')
            return
        if draft['kind'] == 'tiles':
            self.observations[player] = dict(tiles=sort_hand_tiles(data['tiles']), source=draft['source'],
                confidence=None, context_id=draft['context_id'], valid=True, stale=False,
                pending=False, status='已识别 ' + str(len(data['tiles'])) + ' 张')
            self._show(player)
            expected = 13 - 3 * len(self.model.state['players'][player]['melds'])
            if len(data['tiles']) == expected:
                try:
                    analysis, waits = self.service.analyse_tiles(identity, riichi=bool(self.model.state['players'][player].get('riichi')))
                    if waits:
                        self._remember(waits['id'])
                        self.stage_waits(waits)
                    else:
                        self.service.inbox.mark(identity, 'applied')
                        self._discard_auto_waits(player)
                        self._show(player, status=f"{analysis['shanten']} 向听", pending=False)
                except (ValueError, RuntimeError) as error:
                    self.service.inbox.mark(identity, 'dismissed')
                    self._discard_auto_waits(player)
                    self.observations[player]['valid'] = False
                    self._show(player, status='需核对：' + str(error), pending=False)
            else:
                self.service.inbox.mark(identity, 'applied')
                self._discard_auto_waits(player)
                self.observations[player]['valid'] = len(data['tiles']) == expected + 1
                self._show(player, status=f"{len(data['tiles'])} 张 · 请指定和牌张" if len(data['tiles']) == expected + 1 else '张数不符，请核对')
            if player == self.console.selected:
                self.recalculate_win()
        elif draft['kind'] == 'waits':
            if data.get('tiles'):
                self.observations[player] = dict(tiles=sort_hand_tiles(data['tiles']), source=draft['source'],
                    confidence=None, context_id=draft['context_id'], valid=True, stale=False, pending=True)
            self.stage_waits(draft)
        else:
            self.win_suggestions[player] = draft
            if data.get('tiles'):
                self.observations[player] = dict(tiles=sort_hand_tiles(data['tiles']), source=draft['source'],
                    confidence=None, context_id=draft['context_id'], valid=True, stale=False,
                    status='和牌役种已计算', pending=True)
                self._show(player)
            if player == self.console.selected:
                self.fill_win(draft)

    def _manual_waits(self, player):
        return player in self.console.wait_dirty and (
            player not in self.wait_baselines or self.console.wait_drafts.get(player) != self.wait_baselines[player])

    def stage_waits(self, draft, force=False):
        player, data = draft['player'], draft['data']
        old = self.wait_ids.get(player)
        if old and old != draft['id']:
            self.service.inbox.mark(old, 'dismissed')
        self.wait_ids[player] = draft['id']
        names = '、'.join(tile_name(t) for t in data['waits'])
        observation = self.observations.get(player)
        current = self.model.state['players'][player]
        already_live = current.get('tenpai') and current.get('waits') == data['waits'] and player not in self.console.wait_dirty
        if observation and (self.confirmed.get(player) == tuple(sorted(observation['tiles'])) or already_live):
            self.service.inbox.mark(draft['id'], 'applied')
            self.wait_ids.pop(player, None)
            self._show(player, status='听 ' + names + ' · 已提交', pending=False)
            return
        if not force and self._manual_waits(player):
            self._show(player, status='听 ' + names + ' · 保留手动待牌', pending=True)
            return
        values = dict(waits=list(data['waits']), remaining=current.get('remaining'),
                      no_yaku=list(data.get('no_yaku', [])), furiten=bool(current.get('furiten', False)))
        if 'remaining' in data:
            values['remaining'] = data['remaining']
        if 'furiten' in data:
            values['furiten'] = bool(data['furiten'])
        self.console.wait_drafts[player] = values
        self.console.wait_dirty.add(player)
        self.wait_baselines[player] = copy.deepcopy(values)
        self._show(player, status='听 ' + names + ' · 待确认', pending=True)
        if player == self.console.selected:
            self.console.refresh_wait_editor()

    def _discard_auto_waits(self, player):
        identity = self.wait_ids.pop(player, None)
        if identity:
            self.service.inbox.mark(identity, 'dismissed')
        if not self._manual_waits(player) and player in self.wait_baselines:
            self.console.wait_dirty.discard(player)
            self.console.wait_drafts.pop(player, None)
            if player == self.console.selected:
                self.console.refresh_wait_editor()
        self.wait_baselines.pop(player, None)

    def confirm_waits(self, player, *, riichi=None):
        if self.console.recognized_hands[player].editing:
            raise DraftError('请先采用或取消手牌修正')
        identity = self.wait_ids.get(player)
        if not identity:
            return False
        source = self.service.inbox.get(identity, pending=True)
        values = copy.deepcopy(self.console.wait_drafts.get(player) or self.wait_baselines.get(player))
        if not values:
            values = dict(waits=source['data']['waits'], no_yaku=source['data'].get('no_yaku', []),
                          remaining=None, furiten=False)
        if riichi is None:
            riichi = bool(self.model.state['players'][player].get('riichi'))
        # Declaring riichi supplies a yaku for every structural wait, unless the
        # operator explicitly changed the per-wait yaku flags afterwards.
        if riichi and not self._manual_waits(player):
            values['no_yaku'] = []
        if values['waits'] != source['data']['waits']:
            source = self.service.submit('waits', player, dict(waits=values['waits'], no_yaku=values['no_yaku']),
                source='operator-correction', context_id=source['context_id'])
            self._remember(source['id'])
        self._confirming.add(player)
        dirty = player in self.console.wait_dirty
        self.console.wait_dirty.discard(player)
        try:
            self.service.confirm_waits(source['id'], riichi=riichi, no_yaku=values['no_yaku'],
                remaining=values.get('remaining'), furiten=values.get('furiten', False))
        except Exception:
            if dirty:
                self.console.wait_dirty.add(player)
            raise
        finally:
            self._confirming.discard(player)
        self.wait_ids.pop(player, None)
        self.wait_baselines.pop(player, None)
        self.service.inbox.mark(identity, 'applied')
        if player in self.observations:
            self.confirmed[player] = tuple(sorted(self.observations[player]['tiles']))
        self._show(player, status='听 ' + '、'.join(tile_name(t) for t in values['waits']) + ' · 已提交', pending=False)
        if player == self.console.selected:
            self.console.refresh_wait_editor()
        return True

    def correct_hand(self, player, tiles):
        self.manual_hands.add(player)
        self._fresh_hands.pop(player, None)
        self.confirmed.pop(player, None)
        self.receive_tiles(player, tiles, source='手动核对', operator=True)

    def _win_state(self):
        c = self.console
        return dict(yaku={name: control.isChecked() for name, control in c.yaku_buttons.items()},
                    bonuses=[control.value() for control in c.bonuses], fu=c.fu.currentData(),
                    han=c.han.value(), linked=c.linked.isChecked())

    def win_edited(self, name=None):
        if self._applying or not self.console.ready:
            return
        if name in _FLAG_ROLES:
            self.recalculate_win()
        elif name == 'clear':
            self.manual_win_players.add(self.console.selected)
        else:
            self.manual_win_players.add(self.console.selected)
        self.console.preview()

    def win_review_status(self):
        """Original submit button uses this guard; manual scoring stays available."""
        player = self.console.selected
        if self.console.recognized_hands[player].editing:
            return '正在修正手牌 · 请先采用或取消', False
        if player in self.manual_win_players:
            return '手动核对结果', True
        if self.win_owner != player:
            return '', True
        draft = self.win_suggestions.get(player)
        if not draft or draft['context_id'] != self.service.context()['context_id']:
            return '识别结果已失效 · 等待新画面或手动核对役种', False
        if player in self.win_errors:
            return self.win_errors[player], False
        observation = self.observations.get(player)
        if observation and (observation.get('stale') or not observation.get('valid')):
            return '手牌画面已过期 · 等待识别恢复或手动核对役种', False
        return '识别辅助填写 · 请核对和牌张、役种与收支', True

    def before_select(self):
        c = self.console
        self.win_inputs[c.selected] = (c.win_tile.text(), c.ura_indicators.text())
        if self.win_owner == c.selected or c.selected in self.manual_win_players:
            self.win_states[c.selected] = dict(self._win_state(), kind=c.kind,
                loser=c.loser.currentIndex(), yakuman=getattr(c, '_automation_yakuman', None),
                baseline=copy.deepcopy(self.win_baseline))

    def _restore_win_controls(self, state):
        c = self.console
        self._applying = True
        ready = c.ready
        c.ready = False
        try:
            for name, control in c.yaku_buttons.items():
                control.setChecked(state.get('yaku', {}).get(name, False))
            for control, value in zip(c.bonuses, state.get('bonuses', [0, 0, 0])):
                control.setValue(value)
            c.linked.setChecked(state.get('linked', False))
            c.han.setValue(max(1, state.get('han', 1)))
            c.fu.setCurrentIndex(c.fu.findData(state.get('fu', 30)))
            c.kind = state.get('kind', 'ron')
            c.ron.setChecked(c.kind == 'ron')
            c.tsumo.setChecked(c.kind == 'tsumo')
            c.loser.setCurrentIndex(state.get('loser', (c.selected + 1) % 4))
            c._automation_yakuman = state.get('yakuman')
        finally:
            c.ready = ready
            self._applying = False
        c.yaku_changed()

    def selected(self, previous=None):
        c = self.console
        self._sync_hand_target()
        if self._applying:
            return
        if previous != c.selected:
            c.win_assist_hint.clear()
            c.win_fill_button.setEnabled(False)
            values = self.win_inputs.get(c.selected, ('', ''))
            c.win_tile.setText(values[0])
            c.ura_indicators.setText(values[1])
            if c.selected in self.win_states:
                state = self.win_states[c.selected]
                self._restore_win_controls(state)
                self.win_owner = c.selected
                self.win_baseline = copy.deepcopy(state.get('baseline'))
            elif self.win_owner is not None and self.win_owner != c.selected:
                self._applying = True
                ready = c.ready
                c.ready = False
                try:
                    for control in c.yaku_buttons.values():
                        control.setChecked(False)
                    for control in c.bonuses:
                        control.setValue(0)
                    c.linked.setChecked(False)
                    c.han.setValue(1)
                    c.fu.setCurrentIndex(c.fu.findData(30))
                    c._automation_yakuman = None
                finally:
                    c.ready = ready
                    self._applying = False
                self.win_owner = None
                self.win_baseline = None
                c.yaku_changed()
        if c.selected in self.win_suggestions:
            self.fill_win(self.win_suggestions[c.selected])
        else:
            self.recalculate_win()

    def recalculate_win(self, force=False):
        c = self.console
        if self._closed or self._applying or not c.ready:
            return False

        player = c.selected
        observation = self.observations.get(player)
        if not observation or observation.get('stale') or not observation.get('valid') or self.model.state.get('phase') != 'playing':
            c.preview()
            return False
        try:
            winning = parse_tiles(c.win_tile.text())
            if not winning:
                if self.win_owner == player:
                    self.win_errors[player] = '请指定一张和牌张后再结算'
                return False
            if len(winning) != 1:
                raise DraftError('请指定一张和牌张')
            if not force and player in self.manual_win_players:
                c.win_assist_hint.setText('保留手动役种 · 可点“更新役种”重新计算')
                c.win_fill_button.setEnabled(True)
                return False
            flags = {name for name, control in c.yaku_buttons.items() if control.isChecked()}
            riichi = bool(self.model.state['players'][player].get('riichi') or {'立直', '两立直'} & flags)
            ippatsu = '一发' in flags
            expected = 13 - 3 * len(self.model.state['players'][player]['melds'])
            # A programmatic producer uses exactly the same boundary as YOLO.
            raw = self.service.submit('tiles', player, {'tiles': observation['tiles']}, source=observation['source'])
            self._remember(raw['id'])
            try:
                suggestion = self.service.calculate_win(raw['id'], winning[0], tsumo=c.kind == 'tsumo',
                    riichi=riichi, ippatsu=ippatsu, ura_indicators=parse_tiles(c.ura_indicators.text()),
                    loser=c.loser.currentIndex(), win_tile_included=len(observation['tiles']) == expected + 1,
                    special_flags={'double_riichi': '两立直' in flags})
            except Exception:
                self.service.inbox.mark(raw['id'], 'dismissed')
                raise
            self._remember(suggestion['id'])
            self.win_suggestions[player] = suggestion
            return self.fill_win(suggestion, force=force)
        except (ValueError, RuntimeError) as error:
            self.win_errors[player] = str(error)
            c.win_assist_hint.setText(str(error))
            return False
        finally:
            c.preview()

    def update_win(self):
        """Explicitly adopt a semantic provider suggestion even without raw tiles."""
        player = self.console.selected
        observation = self.observations.get(player)
        if observation and observation.get('valid') and self.console.win_tile.text().strip():
            return self.recalculate_win(force=True)
        draft = self.win_suggestions.get(player)
        if draft:
            return self.fill_win(draft, force=True)
        return self.recalculate_win(force=True)

    def fill_win(self, draft, force=False):
        c = self.console
        if draft['player'] != c.selected or draft['context_id'] != self.service.context()['context_id']:
            return False
        data = draft['data']
        if set(data['yaku']) - set(c.yaku_buttons):
            self.win_errors[c.selected] = '含未支持役种，请核对'
            c.win_assist_hint.setText('含未支持役种，请核对')
            c.preview()
            return False
        try:
            from mahjong_values import yaku_total
            opened = any(m['kind'] != 'ankan' for m in self.model.state['players'][draft['player']]['melds'])
            han, yakuman = yaku_total(data['yaku'], opened, sum(data['bonuses'].values()), manual=False)
            if (not data.get('yakuman') and han != data['han']) or (data.get('yakuman') and (not yakuman or yakuman > data['yakuman'])):
                raise DraftError('役种与番数不一致，请核对外部建议')
            self.model.preview(dict(type=data['type'], winner=draft['player'], loser=data['loser'],
                han=data['han'], fu=data['fu'] or 30, yakuman=data.get('yakuman', 0)))
        except (ValueError, RuntimeError) as error:
            self.win_errors[c.selected] = str(error)
            c.win_assist_hint.setText(str(error))
            c.preview()
            return False
        if not force and (c.selected in self.manual_win_players or (
                self.win_baseline is None and any(b.isChecked() for n,b in c.yaku_buttons.items() if n not in _FLAG_ROLES))):
            c.win_assist_hint.setText('已有手动役种 · 可点“更新役种”采用建议')
            c.win_fill_button.setEnabled(True)
            return False
        old_focus = QApplication.focusWidget()
        self._applying = True
        ready = c.ready
        c.ready = False
        try:
            for name, control in c.yaku_buttons.items():
                control.setChecked(name in data['yaku'])
            for control, key in zip(c.bonuses, ('dora', 'aka', 'ura')):
                control.setValue(data['bonuses'].get(key, 0))
            c.fu.setCurrentIndex(c.fu.findData(data['fu'] or 30))
            c.han.setValue(max(1, data['han']))
            c.linked.setChecked(True)
            c.kind = data['type']
            c.ron.setChecked(c.kind == 'ron')
            c.tsumo.setChecked(c.kind == 'tsumo')
            c.loser.setCurrentIndex(data['loser'])
            c._automation_yakuman = (frozenset(data['yaku']), data.get('yakuman', 0))
            if data.get('win_tile'):
                c.win_tile.setText(data['win_tile'])
            if data.get('ura_indicators') is not None:
                c.ura_indicators.setText(compact(data['ura_indicators']))
        finally:
            c.ready = ready
            self._applying = False
        self.win_owner = c.selected
        self.win_suggestions[c.selected] = draft
        self.win_errors.pop(c.selected, None)
        self.win_baseline = self._win_state()
        self.manual_win_players.discard(c.selected)
        c.yaku_changed()
        c.win_fill_button.setEnabled(True)
        c.win_assist_hint.setText('已辅助填写役种 · 核对后提交结算')
        if old_focus and old_focus.isVisible():
            old_focus.setFocus()
        self.service.inbox.mark(draft['id'], 'applied')
        return True

    @Slot()
    def configure(self):
        if self._closed:
            return
        config = normalize_recognition_config(self.model.settings.get('recognition_assist'))
        for player, source in enumerate(config['sources']):
            if player in self._preview_paused:
                continue
            signature = json.dumps([source, config['model'], config['confidence']], sort_keys=True)
            previous = self._source_signatures.get(player)
            if previous == signature:
                continue
            self._source_signatures[player] = signature
            if source['enabled']:
                self.start_source(player, source, model=config['model'], confidence=config['confidence'])
            elif previous is not None or player in self.workers:
                self.stop_source(player)
        self._config = config

    def start_source(self, player, source_config=None, *, model=None, confidence=None):
        if self._closed:
            return None
        config = normalize_recognition_config(self.model.settings.get('recognition_assist'))
        source = copy.deepcopy(source_config or config['sources'][player])
        self.stop_source(player)
        reason = recognition_runtime.recognition_unavailable_reason(source['kind'])
        if reason:
            self.source_state_changed.emit(player, reason)
            return None
        from recognition_worker import RecognitionWorker
        from tile_recognition import recognition_model_path
        self._discard_auto_waits(player)
        self.confirmed.pop(player, None)
        self.win_suggestions.pop(player, None)
        if player in self.observations:
            self.observations[player]['valid'] = False
            self._show(player, stale=True, status='等待新画面', pending=False)
        self.manual_hands.discard(player)
        revision = self._source_revisions[player]
        path = source if source['kind'] in ('camera', 'spout') else source['path']
        worker = RecognitionWorker(path, image=source['kind'] == 'image',
            model_path=recognition_model_path(model or config['model']),
            confidence=confidence if confidence is not None else config['confidence'],
            roi=source.get('roi', [0, 0, 1, 1]), parent=self)
        self.workers[player] = worker
        self.worker_info[worker] = dict(player=player, revision=revision,
            context_revision=0, image=source['kind'] == 'image')
        self.gates[player] = StableHandGate(stable_frames=1 if source['kind'] == 'image' else 3,
            minimum_confidence=worker.confidence)
        if hasattr(worker, 'frame_ready_tagged'):
            worker.frame_ready_tagged.connect(self._tagged_frame, Qt.ConnectionType.QueuedConnection)
        else:
            worker.frame_ready.connect(self._frame, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
        if hasattr(worker, 'status_changed'):
            worker.status_changed.connect(self._source_status, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self.source_state_changed.emit(player, '正在连接')
        self.console.recognized_hands[player].set_observation([], status='正在连接')
        self._set_hand_visible(player, True)
        worker.start()
        self._freshness_timer.start()
        return worker

    @Slot(object, object)
    def _frame(self, image, result):
        self._process_frame(self.sender(), image, result)

    @Slot(object, object, int)
    def _tagged_frame(self, image, result, context_revision):
        self._process_frame(self.sender(), image, result, context_revision)

    def _process_frame(self, worker, image, result, context_revision=None):
        info = self.worker_info.get(worker)
        if not info or self._closed:
            return
        player = info['player']
        if worker is not self.workers.get(player) or info['revision'] != self._source_revisions[player]:
            return
        if context_revision is not None and context_revision != info['context_revision']:
            return
        captured_at = getattr(result, 'captured_at', 0.)
        if not info['image'] and captured_at and time.monotonic() - captured_at > _STALE_SECONDS:
            self._invalidate_source(player, '识别画面已过期 · 等待最新画面')
            return
        self.preview_received.emit(player, image, result)
        self.latest_previews[player] = (image, result)
        self.source_state_changed.emit(player, f'{len(result.tiles)} 张 · {result.elapsed_ms:.0f} ms')
        if player in self.manual_hands or self.model.state.get('phase') != 'playing':
            return
        tiles = [d.tile for d in result.detections]
        expected = 13 - 3 * len(self.model.state['players'][player]['melds'])
        minimum = min((d.confidence for d in result.detections), default=0.)
        count = len(tiles)
        gate = self.gates[player]
        stable = gate.observe(result.detections, count if count == expected + 1 else expected,
            melds=self.model.state['players'][player]['melds'])
        if stable:
            try:
                self.receive_tiles(player, stable, source=result.provider or 'camera', confidence=minimum)
                self._fresh_hands[player] = captured_at or time.monotonic()
            except (ValueError, RuntimeError) as error:
                self.console.recognized_hands[player].set_observation(tiles, source=result.provider,
                    confidence=minimum, status='需核对：' + str(error), pending=False)
        elif (player in self.observations and sorted(tiles, key=str) == sorted(self.observations[player]['tiles'], key=str)
                and gate.valid_frame and result.unknown_count == 0 and minimum >= worker.confidence
                and len(tiles) in (expected, expected + 1)):
            self._fresh_hands[player] = captured_at or time.monotonic()
        else:
            self.console.recognized_hands[player].set_observation(tiles, source=result.provider,
                confidence=minimum, status=gate.reason or '核对中 · ' + str(len(tiles)) + ' 张',
                stale=bool(self.observations.get(player, {}).get('stale')), pending=False)

    def _invalidate_source(self, player, message):
        if player in self.manual_hands:
            return
        self._fresh_hands.pop(player, None)
        self._discard_auto_waits(player)
        self.confirmed.pop(player, None)
        self.win_suggestions.pop(player, None)
        if player in self.observations:
            self.observations[player]['valid'] = False
            self._show(player, stale=True, status=message, pending=False)
        if player in self.gates:
            self.gates[player].reset()
        if player == self.console.selected:
            self.console.win_fill_button.setEnabled(False)
            if self.win_owner == player:
                self.console.win_assist_hint.setText(message + ' · 可手动核对役种')
            self.console.preview()

    def _check_freshness(self):
        if self._closed:
            return
        if self.workers and not recognition_runtime.recognition_available():
            for player in list(self.workers):
                self.stop_source(player)
                self.source_state_changed.emit(player, '识别组件已卸载；手动导播可正常使用')
            return
        now = time.monotonic()
        for player, seen in list(self._fresh_hands.items()):
            worker = self.workers.get(player)
            info = self.worker_info.get(worker, {})
            if not info.get('image') and now - seen > _STALE_SECONDS:
                self._invalidate_source(player, '画面未稳定 · 上次识别已过期')

    @Slot(str)
    def _source_status(self, message):
        info = self.worker_info.get(self.sender())
        if info and not self._closed and info['revision'] == self._source_revisions[info['player']]:
            self.source_state_changed.emit(info['player'], message)

    @Slot(str)
    def _failed(self, message):
        info = self.worker_info.get(self.sender())
        if info and not self._closed and info['revision'] == self._source_revisions[info['player']]:
            player = info['player']
            self._invalidate_source(player, '来源中断 · ' + message)
            self.source_state_changed.emit(player, message)
            if player not in self.observations:
                self.console.recognized_hands[player].set_observation([], status=message)

    @Slot()
    def _finished(self):
        worker = self.sender()
        info = self.worker_info.pop(worker, None)
        if info is None:
            return
        if worker is self.workers.get(info['player']):
            self.workers.pop(info['player'], None)
            if not self.workers:
                self._freshness_timer.stop()
            if not self._closed:
                if not info['image']:
                    self._invalidate_source(info['player'], '来源已停止 · 上次识别已过期')
                self.source_state_changed.emit(info['player'], '已停止' if worker.isInterruptionRequested() else '来源已结束')
        # Completed and superseded readers are no longer useful. Keeping them
        # parented to this console would retain every reconnect until exit.
        worker.deleteLater()

    def stop_source(self, player):
        self._source_revisions[player] += 1
        worker = self.workers.pop(player, None)
        if worker:
            worker.requestInterruption()
        if not self.workers:
            self._freshness_timer.stop()
        self.manual_hands.discard(player)
        self._invalidate_source(player, '识别已停止 · 请手动核对役种')
        self.gates.pop(player, None)
        self.observations.pop(player, None)
        self.latest_previews.pop(player, None)
        self.win_inputs.pop(player, None)
        if player not in self.manual_win_players:
            self.win_states.pop(player, None)
        self.console.recognized_hands[player].clear()
        self._set_hand_visible(player, False)
        self.source_state_changed.emit(player, '已停止')
        return worker

    def pause_for_preview(self, player):
        # The preview subscribes to the same physical capture. Its temporary
        # crop and inference never suspend the four production readers.
        return None

    def resume_after_preview(self, player):
        return None

    def resume_recognition(self, player):
        self.manual_hands.discard(player)
        self._invalidate_source(player, '已恢复自动识别 · 等待稳定新画面')
        worker = self.workers.get(player)
        source = normalize_recognition_config(self.model.settings.get('recognition_assist'))['sources'][player]
        if source['enabled'] and (worker is None or not worker.isRunning()):
            self.start_source(player)

    def source_menu(self, player):
        widget = self.console.recognized_hands[player]
        menu = QMenu(widget)
        menu.addAction('识别来源与选区…', lambda: self.console.open_recognition_settings(player))
        menu.addAction('修正手牌', getattr(widget, 'begin_tile_edit', widget.begin_edit))
        menu.addAction('恢复自动识别', lambda: self.resume_recognition(player))
        worker = self.workers.get(player)
        if worker and worker.isRunning():
            menu.addAction('停止识别', lambda: self.stop_source(player))
        else:
            menu.addAction('连接已保存来源', lambda: self.start_source(player))
        if player in self.wait_ids:
            menu.addAction('采用自动待牌建议', lambda: self.stage_waits(self.service.inbox.get(self.wait_ids[player], pending=True), force=True))
        menu.exec(widget.source_button.mapToGlobal(widget.source_button.rect().bottomLeft()))

    def model_changed(self, before, after, kind):
        if self._closed:
            return
        hard = kind in _LIFECYCLE or any(before.get(k) != after.get(k) for k in
            ('roundIndex', 'dealer')) or any(
            a.get('id') != b.get('id') for a, b in zip(before['players'], after['players']))
        if hard or after.get('phase') != 'playing':
            invalidated_win_owner = self.win_owner if self.win_owner not in self.manual_win_players else None
            self.manual_hands.clear()
            for hand in self.console.recognized_hands:
                if hand.editing:
                    hand.cancel_edit()
            if kind in _LIFECYCLE:
                for player in self.wait_baselines:
                    self.console.wait_dirty.discard(player)
                    self.console.wait_drafts.pop(player, None)
            for player in list(self.observations):
                self._discard_auto_waits(player)
                if kind in _LIFECYCLE:
                    self.console.recognized_hands[player].clear()
                else:
                    self.observations[player]['valid'] = False
                    self._show(player, stale=True, status='上一帧 · 需重新识别', pending=False)
            if kind in _LIFECYCLE:
                self.observations.clear()
                self.manual_hands.clear()
                for player in range(4):
                    self._set_hand_visible(player, player in self.workers)
            self.wait_ids.clear()
            self.wait_baselines.clear()
            self.win_suggestions.clear()
            self.win_errors.clear()
            self._fresh_hands.clear()
            self.win_baseline = None
            # Keep ownership of an obsolete automatic score until it is
            # replaced or deliberately edited. Clearing ownership alone would
            # relabel the old visible yaku/fu as a valid manual result.
            self.win_owner = invalidated_win_owner
            self.win_inputs.clear()
            self.win_states.clear()
            self.confirmed.clear()
            self.manual_win_players.clear()
            self.console.win_tile.clear()
            self.console.ura_indicators.clear()
            self.console.win_assist_hint.clear()
            # Table positions own their readers. Changing dealer, hands or
            # the on-air view invalidates drafts rather than releasing cameras.
            # Reject frames whose inference began before this context change.
            for worker, info in self.worker_info.items():
                if worker is self.workers.get(info['player']):
                    info['context_revision'] += 1
                    worker.context_revision = info['context_revision']
            for gate in self.gates.values():
                gate.reset()
            # An imported event may have different saved source bindings.
            QTimer.singleShot(0, self.configure)
            self.console.preview()
            return
        # A call changes only its owner's concealed count. Indicators and rule
        # edits recalculate known hands without discarding the operator's inputs.
        for player, (old, new) in enumerate(zip(before['players'], after['players'])):
            if old.get('melds') != new.get('melds'):
                self.manual_hands.discard(player)
                self._invalidate_source(player, '副露已变更 · 等待新手牌')
                worker = self.workers.get(player)
                if worker in self.worker_info:
                    self.worker_info[worker]['context_revision'] += 1
                    worker.context_revision = self.worker_info[worker]['context_revision']
        # Scores, names, deposits and riichi flags do not change a concealed
        # hand. Recompute against the new context without restarting captures.
        observations = [(p, o) for p, o in self.observations.items()
                        if o.get('valid') and not o.get('stale') and p not in self._confirming]
        expected_context = self.service.context()['context_id']
        def refresh():
            if self._closed or self.model.state.get('phase') != 'playing' or self.service.context()['context_id'] != expected_context:
                return
            for player, observation in observations:
                if (self.observations.get(player) is not observation
                        or not observation.get('valid') or observation.get('stale')):
                    continue
                self.receive_tiles(player, observation['tiles'], source=observation['source'],
                    confidence=observation.get('confidence'), operator=player in self.manual_hands)
        QTimer.singleShot(0, refresh)

    def close(self):
        if self._closed:
            return
        from recognition_source_editor import drain_worker
        self._closed = True
        self._freshness_timer.stop()
        for player in range(4):
            self.stop_source(player)
        for worker in list(self.worker_info):
            drain_worker(worker)
