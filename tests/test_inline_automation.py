"""Recognition assists the original desk without stealing seats or publishing drafts."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app_paths import LOCAL_RECOGNITION_SAMPLES
from console import Console
from model import Model, parse_tiles
from overlays import OverlayManager
from automation_api import DraftError
from recognition_runtime import recognition_available
from tile_recognition import MEDIUM_MODEL

APP = QApplication.instance() or QApplication([])
NO_YAKU_HAND = '123456m234p678s1z'
HANDS = ('123m123p123s1112z', '123m456p12789s11z', NO_YAKU_HAND, '999m333p5s666777z')
WAITS = (['2z'], ['3s'], ['1z'], ['5s'])
REPLAY = LOCAL_RECOGNITION_SAMPLES / 'samples' / 'constructed-tenpai-replay.avi'


def pump(seconds=.04):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(.005)
    APP.processEvents()


def spin_until(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        pump(.015)
    return predicate()


class InlineAutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='inline-automation-', dir=ROOT / 'tests')
        self.model = Model(self.temp.name)
        self.model.settings.update(show_overlays=False, topmost=False)
        self.manager = OverlayManager(self.model)
        self.ui = Console(self.model, self.manager)
        self.ui.resize(1460, 1000)
        self.ui.show()
        pump()
        self.assist = self.ui.inline_automation

    def tearDown(self):
        self.ui.close()
        APP.processEvents()
        self.temp.cleanup()

    def receive(self, player, hand=None, **kwargs):
        self.assist.receive_tiles(player, parse_tiles(hand or HANDS[player]),
                                  source='replaceable-test-provider', confidence=.95, **kwargs)
        pump()

    def test_four_player_results_fill_original_wait_editors_without_focus_or_model_changes(self):
        self.assertTrue(all(hand.isHidden() for hand in self.ui.recognized_hands))
        self.assertTrue(self.ui.target_buttons['hand'].isHidden())
        self.ui.select(2)
        self.ui.player_widgets[2][2].setFocus()
        pump()
        focused = APP.focusWidget()
        before = copy.deepcopy(self.model.state)
        for player in range(4):
            self.receive(player)
        self.assertEqual(self.ui.selected, 2)
        self.assertIs(APP.focusWidget(), focused)
        self.assertEqual(self.model.state, before)
        self.assertEqual(self.model.undo, [])
        for player, waits in enumerate(WAITS):
            self.assertFalse(self.ui.recognized_hands[player].isHidden())
            self.assertEqual(self.ui.recognized_hands[player].tiles, parse_tiles(HANDS[player]))
            self.assertEqual(self.ui.wait_drafts[player]['waits'], waits)
        self.assertEqual(self.ui.wait_draft()['waits'], ['1z'])
        self.assertIsNone(getattr(self.ui, 'automation_dialog', None))
        entry = getattr(self.ui, 'automation_button', None)
        self.assertTrue(entry is None or entry.isHidden())

    def test_unloading_recognition_hides_hands_and_preserves_manual_broadcast(self):
        for player in range(4):
            self.receive(player)
        self.ui.select(0)
        self.ui.submit_wait_button.click()
        published = copy.deepcopy(self.model.state)
        self.ui.recognized_hands[1].begin_tile_edit(0)
        # Uninstall while the desk is open: existing local readers must stop,
        # edits and unpublished suggestions must not survive as invisible state.
        workers = {player: MagicMock() for player in range(4)}
        self.assist.workers.update(workers)
        with patch('recognition_runtime.recognition_available', return_value=False):
            self.assist._check_freshness()
            for player in range(4):
                self.assertIsNone(self.assist.start_source(player, dict(
                    enabled=True, kind='image', path='unavailable.jpg', roi=[0, 0, 1, 1])))
            self.assertTrue(all(hand.isHidden() and not hand.tiles and not hand.editing
                                for hand in self.ui.recognized_hands))
            self.assertFalse(self.assist.observations)
            self.assertFalse(self.assist.workers)
            self.assertFalse(self.assist.wait_ids)
            self.assertTrue(self.ui.target_buttons['hand'].isHidden())
            self.assertNotEqual(self.ui.tile_target, 'hand')
            self.assertEqual(self.model.state, published)
            for worker in workers.values():
                worker.requestInterruption.assert_called_once()
            # Replacement providers remain usable without this local YOLO.
            self.receive(2)
            self.assertFalse(self.ui.recognized_hands[2].isHidden())
        self.assist.close()
        self.assertTrue(all(hand.isHidden() for hand in self.ui.recognized_hands))
        self.ui.select(3)
        self.ui.add_wait_draft('5s')
        self.ui.submit_waits()
        self.assertEqual(self.model.state['players'][3]['waits'], ['5s'])
        self.assertTrue(self.model.state['players'][0]['tenpai'])

    def test_original_submit_publishes_ordinary_waits_and_manual_flags_once(self):
        self.receive(0, NO_YAKU_HAND)
        self.ui.furiten.setChecked(True)
        self.ui.remaining.setValue(0)
        before = copy.deepcopy(self.model.state)
        self.ui.submit_wait_button.click()
        player = self.model.state['players'][0]
        self.assertTrue(player['tenpai'])
        self.assertFalse(player['riichi'])
        self.assertEqual(player['waits'], ['1z'])
        self.assertEqual(player['waits_no_yaku'], ['1z'])
        self.assertTrue(player['furiten'])
        self.assertEqual(player['remaining'], 0)
        self.assertEqual(player['score'], before['players'][0]['score'])
        self.assertEqual(len(self.model.undo), 1)
        self.model.history('undo')
        self.assertEqual(self.model.state, before)

    def test_published_recognized_wait_count_updates_without_new_submission(self):
        self.receive(0);self.ui.remaining.setValue(4);self.ui.submit_wait_button.click()
        self.ui.remaining.setValue(2);pump()
        self.assertEqual(self.model.state['players'][0]['remaining'], 2)
        self.assertEqual(self.ui.wait_draft()['remaining'], 2)
        self.assertNotIn(0, self.ui.wait_dirty)
        self.receive(0)
        self.assertEqual(self.model.state['players'][0]['remaining'], 2)
        self.assertEqual(self.ui.remaining.value(), 2)
        self.assertNotIn(0, self.assist.wait_ids)

    def test_wait_buttons_override_recognized_flags_without_auto_submission(self):
        self.receive(0)
        self.ui.wait_no_yaku.click();self.ui.wait_row.itemAt(0).widget().click();self.ui.furiten.click()
        self.assertEqual(self.ui.wait_draft()['no_yaku'], ['2z'])
        self.assertTrue(self.ui.wait_draft()['furiten'])
        self.assertEqual(self.model.state['players'][0]['waits'], [])
        self.ui.submit_wait_button.click()
        self.assertEqual(self.model.state['players'][0]['waits_no_yaku'], ['2z'])
        self.assertTrue(self.model.state['players'][0]['furiten'])
        self.ui.wait_row.itemAt(0).widget().click();self.ui.furiten.click()
        self.ui.submit_wait_button.click()
        self.assertEqual(self.model.state['players'][0]['waits'], ['2z'])
        self.assertEqual(self.model.state['players'][0]['waits_no_yaku'], [])
        self.assertFalse(self.model.state['players'][0]['furiten'])

    def test_existing_player_riichi_button_commits_deposit_and_waits_atomically(self):
        self.receive(1)
        before = copy.deepcopy(self.model.state)
        self.ui.player_widgets[1][3].click()
        self.assertTrue(self.model.state['players'][1]['riichi'])
        self.assertEqual(self.model.state['players'][1]['waits'], ['3s'])
        self.assertEqual(self.model.state['players'][1].get('waits_no_yaku'), [])
        self.assertEqual(self.model.state['players'][1]['score'], before['players'][1]['score'] - 1000)
        self.assertEqual(self.model.state['pot'], before['pot'] + 1000)
        self.assertEqual(len(self.model.undo), 1)
        self.model.history('undo')
        self.assertEqual(self.model.state, before)

    def test_original_quick_declaration_uses_the_selected_player_observation(self):
        self.receive(0, NO_YAKU_HAND)
        self.ui.quick_actions['riichi'].click()
        self.assertEqual(self.model.state['players'][0]['waits'], ['1z'])
        self.assertTrue(self.model.state['players'][0]['riichi'])
        self.assertEqual(self.model.state['pot'], 1000)
        self.assertEqual(len(self.model.undo), 1)

    def test_new_observation_does_not_overwrite_manually_corrected_waits(self):
        self.receive(0, NO_YAKU_HAND)
        self.ui.add_wait_draft('3m')
        self.ui.furiten.setChecked(True)
        edited = copy.deepcopy(self.ui.wait_draft())
        self.receive(0, '123456m234p678s2z')
        self.assertEqual(self.ui.wait_draft(), edited)
        self.assertEqual(self.ui.recognized_hands[0].tiles, parse_tiles('123456m234p678s2z'))
        self.assertEqual(self.model.state['players'][0]['waits'], [])

    def test_editing_the_inline_hand_recalculates_its_wait_suggestion_only(self):
        self.receive(0, NO_YAKU_HAND)
        before = copy.deepcopy(self.model.state)
        corrected = parse_tiles('123456m234p678s2z')
        hand = self.ui.recognized_hands[0]
        QTest.mouseClick(hand, Qt.MouseButton.LeftButton, pos=hand.tile_rects()[-1].center().toPoint())
        self.assertEqual(self.ui.tile_target, 'hand')
        self.assertFalse(self.ui.submit_wait_button.isEnabled())
        self.assertFalse(self.ui.settle_button.isEnabled())
        self.ui.tile_buttons['2z'].click()
        self.assertEqual(hand.tiles, parse_tiles(NO_YAKU_HAND))
        hand.edit_buttons['apply'].click()
        pump()
        self.assertEqual(self.ui.recognized_hands[0].tiles, corrected)
        self.assertEqual(self.ui.wait_draft()['waits'], ['2z'])
        self.assertEqual(self.model.state, before)
        self.assertEqual(self.model.undo, [])
        # Operator corrections remain authoritative across every provider
        # draft kind; an external yaku-only result must not bypass the lock.
        baseline_waits = copy.deepcopy(self.ui.wait_draft())
        baseline_roles = self.ui.win_event()[1]
        for kind, data in (
            ('tiles', {'tiles': parse_tiles(HANDS[0])}),
            ('waits', {'waits': ['9m'], 'tiles': parse_tiles(HANDS[0])}),
            ('win', dict(type='ron', loser=1, han=1, fu=40, yakuman=0,
                         yaku=['立直'], bonuses={'dora': 0, 'aka': 0, 'ura': 0})),
        ):
            with self.subTest(external_draft=kind):
                draft = (self.assist.receive_tiles(0, data['tiles'], source='late-external-provider', confidence=.01)
                         if kind == 'tiles' else
                         self.ui.automation.submit(kind, 0, data, source='late-external-provider'))
                pump()
                self.assertEqual(self.ui.automation.inbox.get(draft['id'])['status'], 'dismissed')
                self.assertEqual(self.assist.observations[0]['tiles'], corrected)
                self.assertIsNone(self.assist.observations[0]['confidence'])
                self.assertEqual(self.ui.recognized_hands[0].tiles, corrected)
                self.assertEqual(self.ui.wait_draft(), baseline_waits)
                self.assertEqual(self.ui.win_event()[1], baseline_roles)
        self.ui.submit_wait_button.click()
        self.assertEqual(self.model.state['players'][0]['waits'], ['2z'])
        self.ui.yaku_buttons['立直'].click()
        self.ui.win_tile.setText('2z')
        self.assertTrue(self.assist.recalculate_win())
        context = self.ui.automation.context()['context_id']
        self.model.dora('4m')
        pump()
        self.assertNotEqual(self.ui.automation.context()['context_id'], context)
        self.assertEqual(self.assist.observations[0]['context_id'], self.ui.automation.context()['context_id'])
        self.assertIn(0, self.assist.manual_hands)
        self.assertEqual(self.assist.observations[0]['tiles'], corrected)
        self.assertEqual(self.model.state['players'][0]['waits'], ['2z'])
        self.assertNotIn(0, self.ui.wait_dirty)
        self.assertEqual(self.ui.bonuses[0].value(), 1)
        self.assertTrue(self.assist.win_review_status()[1])

    def test_original_tenpai_button_confirms_the_recognized_waits_for_its_own_seat(self):
        self.receive(3)
        self.ui.player_widgets[3][4].click()
        self.assertTrue(self.model.state['players'][3]['tenpai'])
        self.assertEqual(self.model.state['players'][3]['waits'], ['5s'])
        self.assertFalse(self.model.state['players'][3]['riichi'])
        self.assertEqual(self.model.state['players'][0]['waits'], [])

    def test_win_inputs_assist_existing_highlighted_roles_and_bonuses_without_settlement(self):
        self.model.dora('4m')
        self.receive(0, NO_YAKU_HAND)
        before = copy.deepcopy(self.model.state)
        self.ui.yaku_buttons['立直'].click()
        self.ui.yaku_buttons['一发'].click()
        self.ui.win_tile.setText('1z')
        self.ui.ura_indicators.setText('1p')
        self.ui.win_tile.editingFinished.emit()
        self.ui.ura_indicators.editingFinished.emit()
        pump()
        event, roles = self.ui.win_event()
        self.assertEqual(self.model.state, before)
        self.assertEqual(self.model.state['phase'], 'playing')
        self.assertEqual(event['winner'], 0)
        self.assertEqual(event['han'], 4)
        self.assertEqual(event['fu'], 40)
        self.assertIn('立直', roles)
        self.assertIn('一发', roles)
        self.assertEqual([spin.value() for spin in self.ui.bonuses], [1, 0, 1])
        self.assertTrue(self.ui.yaku_buttons['立直'].isChecked())
        self.assertFalse(self.model.state['players'][0]['riichi'])

    def test_manual_yaku_changes_survive_background_recalculation(self):
        self.receive(0, NO_YAKU_HAND)
        self.ui.yaku_buttons['立直'].click()
        self.ui.win_tile.setText('1z')
        self.ui.win_tile.editingFinished.emit()
        pump()
        self.ui.yaku_buttons['抢杠'].click()
        self.assist.recalculate_win(force=False)
        pump()
        self.assertTrue(self.ui.yaku_buttons['抢杠'].isChecked())
        self.assertEqual(self.model.state['phase'], 'playing')

    def test_switching_seats_restores_each_players_manual_yaku_and_win_input(self):
        self.receive(0, NO_YAKU_HAND)
        self.ui.yaku_buttons['立直'].click()
        self.ui.win_tile.setText('1z')
        self.ui.win_tile.editingFinished.emit()
        self.ui.yaku_buttons['抢杠'].click()
        self.ui.select(1)
        self.assertFalse(self.ui.yaku_buttons['抢杠'].isChecked())
        self.assertEqual(self.ui.win_tile.text(), '')
        self.ui.select(0)
        self.assertTrue(self.ui.yaku_buttons['抢杠'].isChecked())
        self.assertEqual(self.ui.win_tile.text(), '1z')
        self.assertEqual(self.model.state['phase'], 'playing')

    def test_external_win_suggestion_for_another_seat_does_not_switch_the_operator(self):
        self.ui.select(0)
        before = copy.deepcopy(self.model.state)
        self.ui.automation.submit('win', 2, dict(type='ron', loser=3, han=1, fu=40,
            yakuman=0, yaku=['立直'], bonuses={'dora': 0, 'aka': 0, 'ura': 0}), source='independent-calculator')
        pump()
        self.assertEqual(self.ui.selected, 0)
        self.assertEqual(self.model.state, before)
        self.ui.select(2)
        pump()
        self.assertTrue(self.ui.yaku_buttons['立直'].isChecked())
        self.assertEqual(self.ui.loser.currentIndex(), 3)
        self.assertEqual(self.model.state, before)
        # Rejected replacement calculations must not leave the old auto-filled
        # amount looking ready for settlement.
        self.ui.automation.submit('win', 2, dict(type='ron', loser=3, han=4, fu=40,
            yakuman=0, yaku=['立直'], bonuses={'dora': 0, 'aka': 0, 'ura': 0}), source='bad-calculator')
        pump()
        self.assertFalse(self.assist.win_review_status()[1])
        self.assertFalse(self.ui.settle_button.isEnabled())
        self.assertEqual(self.model.state, before)

    def test_external_yaku_only_suggestion_can_be_explicitly_adopted_after_manual_edit(self):
        self.ui.yaku_buttons['平和'].click()
        before = copy.deepcopy(self.model.state)
        self.ui.automation.submit('win', 0, dict(type='ron', loser=1, han=1, fu=40,
            yakuman=0, yaku=['立直'], bonuses={'dora': 0, 'aka': 0, 'ura': 0}), source='semantic-provider')
        pump()
        self.assertTrue(self.ui.yaku_buttons['平和'].isChecked())
        self.ui.win_fill_button.click()
        self.assertFalse(self.ui.yaku_buttons['平和'].isChecked())
        self.assertTrue(self.ui.yaku_buttons['立直'].isChecked())
        self.assertEqual(self.ui.fu.currentData(), 40)
        self.assertEqual(self.model.state, before)

    def test_fu_input_accepts_automation_and_restores_fixed_and_manual_values(self):
        before=copy.deepcopy(self.model.state)
        self.receive(0,'123456m234p67s22p')
        self.ui.win_tile.setText('8s');self.ui.tsumo.click();pump()
        self.assertEqual(self.ui.fu.currentData(),20);self.assertTrue(self.ui.fu.isReadOnly())
        self.assertNotIn(0,self.assist.manual_win_players);self.assertTrue(self.assist.win_review_status()[1])
        self.ui.automation.submit('win',2,dict(type='ron',loser=3,han=1,fu=70,
            yakuman=0,yaku=['立直'],bonuses={'dora':0,'aka':0,'ura':0}),source='external-fu-provider')
        pump();self.ui.select(2);pump()
        self.assertEqual(self.ui.fu.currentData(),70);self.assertFalse(self.ui.fu.isReadOnly())
        self.assertNotIn(2,self.assist.manual_win_players);self.assertTrue(self.assist.win_review_status()[1])
        self.ui.select(0);pump()
        self.assertEqual((self.ui.kind,self.ui.fu.currentData()),('tsumo',20))
        self.assertTrue(self.ui.fu.isReadOnly());self.assertTrue(self.assist.win_review_status()[1])
        self.assertEqual(self.model.state,before)

    def test_lifecycle_invalidates_old_observations_and_rejects_late_context(self):
        self.receive(0, NO_YAKU_HAND)
        self.ui.yaku_buttons['立直'].click()
        self.ui.win_tile.setText('1z')
        self.assertTrue(self.assist.recalculate_win())
        self.assist.manual_hands.add(0)
        context = self.ui.automation.context()['context_id']
        self.model.field('roundIndex', 1)
        pump()
        self.assertFalse(self.assist.win_review_status()[1])
        self.assertFalse(self.ui.settle_button.isEnabled())
        self.assertNotIn(0, self.assist.manual_hands)
        view = self.ui.recognized_hands[0]
        self.assertTrue(view.stale or not view.tiles)
        self.assertEqual(self.ui.wait_draft()['waits'], [])
        with self.assertRaises(DraftError):
            self.ui.automation.submit('tiles', 0, {'tiles': parse_tiles(NO_YAKU_HAND)},
                                      source='late-provider', context_id=context)
        try:
            self.receive(0, NO_YAKU_HAND, context_id=context)
        except DraftError:
            pass
        self.assertEqual(self.model.state['players'][0]['waits'], [])
        self.assertTrue(view.stale or not view.tiles)

    def test_same_hand_riichi_keeps_other_three_recognized_hands_available(self):
        for player in range(4):
            self.receive(player)
        snapshots = [list(widget.tiles) for widget in self.ui.recognized_hands]
        self.ui.player_widgets[0][3].click()
        pump()
        self.assertEqual([list(widget.tiles) for widget in self.ui.recognized_hands], snapshots)
        for widget in self.ui.recognized_hands[1:]:
            self.assertFalse(widget.stale)

    def test_lost_source_expires_only_automatic_drafts_and_manual_scoring_remains_available(self):
        self.receive(0, NO_YAKU_HAND)
        self.ui.yaku_buttons['立直'].click()
        self.ui.win_tile.setText('1z')
        self.assist.recalculate_win()
        self.assertTrue(self.ui.settle_button.isEnabled())
        before = copy.deepcopy(self.model.state)
        # A queued context recomputation must not revive a source invalidated
        # after it was scheduled but before Qt delivers the callback.
        self.model.dora('4m')
        before = copy.deepcopy(self.model.state)
        self.assist._invalidate_source(0, 'OBS 来源没有新画面')
        pump()
        self.assertTrue(self.ui.recognized_hands[0].stale)
        self.assertNotIn(0, self.assist.wait_ids)
        self.assertEqual(self.ui.wait_draft()['waits'], [])
        self.assertFalse(self.ui.settle_button.isEnabled())
        self.assertFalse(self.assist.win_review_status()[1])
        self.ui.settle()
        self.assertEqual(self.model.state, before)
        self.ui.han.setValue(3)
        self.assertTrue(self.assist.win_review_status()[1])
        self.assertTrue(self.ui.settle_button.isEnabled())
        self.assertEqual(self.model.state, before)

    def test_invalid_new_winning_tile_blocks_previous_automatic_result_and_valid_tile_recovers(self):
        self.receive(0, NO_YAKU_HAND)
        self.ui.yaku_buttons['立直'].click()
        self.ui.win_tile.setText('1z')
        self.assertTrue(self.assist.recalculate_win())
        self.ui.win_tile.setText('9z')
        self.assertFalse(self.assist.recalculate_win())
        self.assertFalse(self.ui.settle_button.isEnabled())
        self.ui.win_tile.setText('1z')
        self.assertTrue(self.assist.recalculate_win())
        self.assertTrue(self.ui.settle_button.isEnabled())

    def test_dora_update_recalculates_without_discarding_win_inputs_or_other_hands(self):
        for player in range(4):
            self.receive(player, NO_YAKU_HAND)
        self.ui.yaku_buttons['立直'].click()
        self.ui.win_tile.setText('1z')
        self.ui.ura_indicators.setText('1p')
        self.assist.recalculate_win()
        self.model.dora('4m')
        pump()
        self.assertEqual(self.ui.win_tile.text(), '1z')
        self.assertEqual(self.ui.ura_indicators.text(), '1p')
        self.assertEqual([control.value() for control in self.ui.bonuses], [1, 0, 1])
        self.assertTrue(all(not widget.stale for widget in self.ui.recognized_hands))
        self.model.meld(1, 'pon', ['5z'] * 3, 0)
        pump()
        self.assertTrue(self.ui.recognized_hands[1].stale)
        self.assertTrue(all(not self.ui.recognized_hands[p].stale for p in (0, 2, 3)))

    @unittest.skipUnless(recognition_available() and REPLAY.exists() and MEDIUM_MODEL.exists(),
                         'optional vision runtime, model or constructed replay missing')
    def test_actual_video_source_populates_the_original_hand_and_wait_controls(self):
        before = copy.deepcopy(self.model.state)
        self.assist.start_source(0, dict(enabled=True, kind='video', path=str(REPLAY), roi=[0, 0, 1, 1]))
        self.assertTrue(spin_until(lambda: self.ui.wait_drafts.get(0, {}).get('waits') == ['5s']),
                        self.ui.status.text())
        self.assertCountEqual(self.ui.recognized_hands[0].tiles, parse_tiles(HANDS[3]))
        self.assertEqual(self.ui.wait_draft()['waits'], ['5s'])
        self.assertEqual(self.model.state, before)
        self.assertEqual(self.model.undo, [])
        self.ui.submit_wait_button.click()
        self.assertEqual(self.model.state['players'][0]['waits'], ['5s'])
        self.assertTrue(self.model.state['players'][0]['tenpai'])
        self.assist.stop_source(0)
        pump()
        # Stopping recognition withdraws suggestions, never already broadcast data.
        self.assertEqual(self.model.state['players'][0]['waits'], ['5s'])
        self.assertTrue(self.ui.recognized_hands[0].isHidden())
        self.assertEqual(self.ui.recognized_hands[0].tiles, [])


if __name__ == '__main__':
    unittest.main()
