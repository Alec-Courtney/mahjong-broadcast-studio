"""History corrections match fresh play, conserve deposits, and persist atomically."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from model import Model
from console import Console
from overlays import OverlayManager
from hand_history import hand_deltas
from hand_history_panel import HandCorrectionDialog
import tournament
from ui_test_support import settle, visibility

APP = QApplication.instance() or QApplication([])


class HandHistoryTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.cache').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / '.cache', prefix='hand-ledger-')
        self.model = Model(Path(self.tmp.name) / 'actual')
        self.control = Model(Path(self.tmp.name) / 'expected')

    def tearDown(self):
        self.tmp.cleanup()

    def win(self, model=None, winner=1, loser=2, han=3, fu=40, kind='ron'):
        model = model or self.model
        event = dict(type=kind, winner=winner, loser=loser, han=han, fu=fu)
        model.settle(event, dict(yaku=['立直', '宝牌 2'], winner=winner, han=han, fu=fu))
        return model.state['broadcast']['hands'][-1]['id']

    def scores(self, model=None):
        return [p['score'] for p in (model or self.model).state['players']]

    def assert_table_matches(self):
        self.assertEqual(self.scores(), self.scores(self.control))
        for key in ('pot', 'phase', 'roundIndex', 'dealer', 'honba', 'finalRanking'):
            self.assertEqual(self.model.state.get(key), self.control.state.get(key), key)
        self.assertEqual(tournament.statistics(self.model.state), tournament.statistics(self.control.state))

    def test_capture_all_payment_context_and_riichi_net_in_both_display_modes(self):
        for mode in ('immediate', 'deferred'):
            with self.subTest(mode=mode):
                self.model.start_match()
                self.model.setting('riichi_payment_mode', mode)
                self.model.field('honba', 2)
                self.model.field('pot', 1000)
                self.model.riichi(1)
                self.win()
                hand = self.model.state['broadcast']['hands'][-1]
                self.assertEqual(hand['context']['pot'], 2000)
                self.assertEqual(hand['context']['scores'], [30000, 29000, 30000, 30000])
                self.assertEqual(hand['context']['riichi_paid'], [False, True, False, False])
                self.assertEqual(hand['win_info']['yaku'], ['立直', '宝牌 2'])
                self.assertEqual(hand_deltas(hand), [0, 6800, -5800, 0])

    def test_later_display_and_round_edits_keep_the_historical_snapshot(self):
        self.model.riichi(1)
        self.win()
        hand = copy.deepcopy(self.model.state['broadcast']['hands'][0])
        self.model.field('honba', 4)
        self.model.field('roundIndex', 3)
        self.model.riichi(1)
        saved = self.model.state['broadcast']['hands'][0]
        self.assertEqual(saved['context'], hand['context'])
        self.assertEqual(saved['label'], hand['label'])
        self.assertEqual(saved['players'][1]['riichi'], 1)

    def test_correct_historical_winner_loser_and_fu_replays_later_hands(self):
        identity = self.win(winner=0, loser=1, han=1, fu=30)
        self.win(self.control, winner=2, loser=3, han=4, fu=40)
        for model in (self.model, self.control):
            model.next_hand()
            model.riichi(3)
            model.draw([1, 3])
            model.next_hand()
            self.win(model, winner=1, kind='tsumo')
        self.model.correct_hand(identity, dict(event=dict(type='ron', winner=2, loser=3, han=4, fu=40),
                                               win_info=dict(yaku=['断幺九', '宝牌 3'])))
        self.assert_table_matches()
        self.assertEqual(len(self.model.state['broadcast']['hands']), 3)
        self.assertEqual(self.model.state['broadcast']['hands'][0]['win_info']['yaku'], ['断幺九', '宝牌 3'])
        self.assertEqual(self.model.last_info['winner'], 1)

    def test_adding_a_draw_deposit_changes_later_pot_award(self):
        self.model.draw([0, 1])
        self.control.riichi(0)
        self.control.draw([0, 1])
        identity = self.model.state['broadcast']['hands'][0]['id']
        for model in (self.model, self.control):
            model.next_hand()
            self.win(model)
            model.next_hand()
            model.riichi(2)
        self.model.correct_hand(identity, dict(context=dict(riichi_paid=[True, False, False, False])))
        self.assert_table_matches()
        first, second = self.model.state['broadcast']['hands']
        self.assertEqual(first['result']['potAfter'], 1000)
        self.assertEqual(second['result']['potBefore'], 1000)
        self.assertEqual(second['result']['potAwards'], [dict(player=1, amount=1000)])
        self.assertTrue(self.model.state['players'][2]['riichi'])

    def test_change_draw_to_win_preserves_current_hand_and_deposit(self):
        for model in (self.model, self.control):
            model.riichi(2)
        self.model.draw([2])
        self.win(self.control, winner=2, loser=0)
        identity = self.model.state['broadcast']['hands'][0]['id']
        for model in (self.model, self.control):
            model.next_hand()
            model.riichi(3)
        current = self.model.state['broadcast']['current_hand']
        self.model.correct_hand(identity, dict(event=dict(type='ron', winner=2, loser=0, han=3, fu=40)))
        self.assert_table_matches()
        self.assertEqual(self.model.state['broadcast']['current_hand'], current)

    def test_manual_gaps_and_manual_round_overrides_survive_replay(self):
        identity = self.win(han=1, fu=30)
        self.win(self.control, winner=0, loser=3, han=4)
        for model in (self.model, self.control):
            model.next_hand()
            model.set_score(0, model.state['players'][0]['score'] + 700)
            model.field('roundIndex', 4)
            model.field('dealer', 2)
            model.field('honba', 5)
            self.win(model, winner=3, loser=1)
            model.set_score(3, model.state['players'][3]['score'] + 900)
        self.model.correct_hand(identity, dict(event=dict(type='ron', winner=0, loser=3, han=4, fu=40)))
        self.assert_table_matches()
        last = self.model.state['broadcast']['hands'][-1]
        self.assertEqual(last['context']['round_index'], 4)
        self.assertEqual(last['context']['honba'], 5)

    def test_final_draw_recalculates_terminal_sticks_and_tied_ranks(self):
        for model in (self.model, self.control):
            model.update(lambda state: state['rules'].update(terminalPot='top'))
            model.field('roundIndex', 7)
            model.field('dealer', 3)
            model.riichi(0)
        self.model.draw([])
        self.control.draw([0, 1])
        identity = self.model.state['broadcast']['hands'][-1]['id']
        self.model.commit_match()
        self.model.correct_hand(identity, dict(event=dict(type='draw', tenpai=[0, 1])))
        self.assert_table_matches()
        archive = self.model.state['broadcast']['matches'][0]
        archived_scores = {r['id']: r['score'] for r in archive['results']}
        self.assertEqual([archived_scores[p['id']] for p in self.model.state['players']], self.scores())
        self.assertEqual(archive['hands'], self.model.state['broadcast']['hands'])

    def test_noop_save_is_idempotent_and_undo_redo_restart_export_work(self):
        identity = self.win()
        self.model.next_hand()
        self.model.draw([0])
        before = copy.deepcopy(self.model.state)
        correction = dict(event=dict(type='tsumo', winner=3, han=2, fu=30), win_info=dict(yaku=['门前清自摸和', '宝牌 1']))
        self.model.correct_hand(identity, correction)
        corrected = copy.deepcopy(self.model.state)
        self.model.correct_hand(identity, correction)
        self.assertEqual(self.model.state, corrected)
        self.model.history('undo')
        self.model.history('undo')
        self.assertEqual(self.model.state, before)
        self.model.history('redo')
        self.assertEqual(self.model.state, corrected)
        restored = Model(self.model.path)
        self.assertEqual(restored.state, corrected)
        target = Path(self.tmp.name) / 'export.json'
        self.model.export_data(target)
        self.control.import_data(target)
        self.assertEqual(self.control.state, corrected)

    def test_legacy_hand_without_context_can_be_corrected(self):
        identity = self.win()
        def legacy(state):
            hand = state['broadcast']['hands'][0]
            for key in ('context', 'event', 'win_info'):
                hand.pop(key)
        self.model.update(legacy, 'hand_correction')
        self.model.correct_hand(identity, dict(event=dict(type='ron', winner=3, loser=0, han=2, fu=30),
                                               win_info=dict(yaku=['立直', '一发'])))
        self.win(self.control, winner=3, loser=0, han=2, fu=30)
        self.assert_table_matches()
        self.assertIn('context', self.model.state['broadcast']['hands'][0])

    def test_replacing_a_legacy_result_without_any_history_keeps_recording_origin(self):
        self.win()
        def without_history(state):
            state['broadcast']['hands'] = []
        self.model.update(without_history, 'hand_correction')
        before = self.model.state['lastResult']['beforeScores'][:]
        self.win(winner=3, loser=0)
        self.assertTrue(self.model.state['broadcast']['partial'])
        self.assertEqual(self.model.state['broadcast']['initial_scores'], before)

    def test_failed_save_or_invalid_or_stale_correction_changes_nothing(self):
        identity = self.win()
        before = copy.deepcopy((self.model.state, self.model.undo, self.model.last_info))
        with patch.object(self.model.repository, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.model.correct_hand(identity, dict(event=dict(type='draw', tenpai=[])))
        self.assertEqual((self.model.state, self.model.undo, self.model.last_info), before)
        with self.assertRaises(ValueError):
            self.model.correct_hand(identity, dict(event=dict(type='ron', winner=1, loser=1, han=3, fu=30)))
        with self.assertRaises(ValueError):
            self.model.correct_hand(identity, dict(context=dict(scores=[0, 0, 0, 1])))
        self.assertEqual((self.model.state, self.model.undo, self.model.last_info), before)
        stale = self.model.state
        self.model.field('honba', 2)
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.model.correct_hand(identity, {}, stale)

    def test_manual_payments_are_independent_and_flow_into_live_scores(self):
        identity = self.win()
        self.model.next_hand()
        self.model.draw([])
        self.model.next_hand()
        self.model.riichi(2)
        self.model.correct_hand(identity, dict(manual_deltas=[0, 7000, -7000, 0]))
        self.assertEqual(self.scores(), [30000, 37000, 22000, 30000])
        self.assertEqual(hand_deltas(self.model.state['broadcast']['hands'][0]), [0, 7000, -7000, 0])
        self.model.correct_hand(identity, dict(event=dict(type='ron', winner=0, loser=3, han=13, fu=30),
                                               win_info=dict(yaku=['清一色'])))
        # Changing descriptive yaku, winner, and fu must not overwrite a manual payment.
        self.assertEqual(self.scores(), [30000, 37000, 22000, 30000])
        hand = self.model.state['broadcast']['hands'][0]
        self.assertEqual(hand['result']['winner'], 0)
        self.assertEqual(hand['win_info']['yaku'], ['清一色'])
        self.assertEqual(hand_deltas(hand), [0, 7000, -7000, 0])
        self.assertEqual(Model(self.model.path).state, self.model.state)
        self.model.correct_hand(identity, dict(manual_deltas=None))
        self.assertNotEqual(self.scores(), [30000, 37000, 22000, 30000])

    def test_manual_income_handles_terminal_and_unbalanced_point_corrections(self):
        self.model.update(lambda state: state['rules'].update(allowNegative=False))
        identity = self.win()
        self.model.correct_hand(identity, dict(manual_deltas=[-40000, 41000, 0, 0]))
        self.assertEqual(self.scores(), [-10000, 71000, 30000, 30000])
        self.model.engine.validate(self.model.state)
        self.assertEqual(self.model.state['accountingTotal'], 121000)
        self.assertEqual(self.model.state['phase'], 'finished')
        self.assertEqual(hand_deltas(self.model.state['broadcast']['hands'][0]), [-40000, 41000, 0, 0])
        before = copy.deepcopy(self.model.state)
        self.model.correct_hand(identity, dict(win_info=dict(yaku=['一发'])))
        self.assertEqual(self.scores(), [p['score'] for p in before['players']])

    def test_manual_replacement_resubmission_and_non_hundred_validation(self):
        self.model.riichi(1)
        identity = self.win()
        self.model.correct_hand(identity, dict(manual_deltas=[0, 8000, -8000, 0]))
        self.assertEqual(self.scores(), [30000, 38000, 22000, 30000])
        # Replacing a direct settlement must reverse the actual prior payment.
        self.win(winner=3, loser=2)
        hand = self.model.state['broadcast']['hands'][0]
        self.assertNotIn('manual_deltas', hand)
        self.assertEqual(self.scores(), [30000, 29000, 24800, 36200])
        with self.assertRaises(ValueError):
            self.model.correct_hand(identity, dict(manual_deltas=[1, -1, 0, 0]))

    def test_earlier_pot_correction_keeps_later_manual_income_and_outgoing_pot(self):
        self.model.riichi(1)
        self.model.draw([1])
        first = self.model.state['broadcast']['hands'][0]['id']
        self.model.next_hand()
        later = self.win()
        self.model.correct_hand(later, dict(manual_deltas=[0, 9000, -8000, 0], manual_pot_after=0))
        self.model.correct_hand(first, dict(event=dict(type='ron', winner=3, loser=0, han=1, fu=30)))
        hand = self.model.state['broadcast']['hands'][-1]
        self.assertEqual(hand_deltas(hand), [0, 9000, -8000, 0])
        self.assertEqual(hand['result']['potBefore'], 0)
        self.assertEqual(hand['result']['potAfter'], 0)
        self.model.engine.validate(self.model.state)


class HandHistoryUITests(unittest.TestCase):
    win = HandHistoryTests.win
    scores = HandHistoryTests.scores

    def setUp(self):
        HandHistoryTests.setUp(self)
        self.model.settings.update(show_overlays=False, topmost=False, console_layout=None)
        self.manager = OverlayManager(self.model)
        self.ui = Console(self.model, self.manager)
        self.ui.show()
        settle(APP)

    def tearDown(self):
        self.ui.close()
        self.ui.deleteLater()
        APP.processEvents()
        HandHistoryTests.tearDown(self)

    def test_visible_list_and_click_edit_updates_live_score_controls(self):
        identity = self.win()
        self.model.next_hand()
        self.model.riichi(0)
        self.model.draw([0])
        panel = self.ui.hand_history
        self.assertEqual(panel.history.rowCount(), 1)
        self.assertIn('不听罚符', panel.latest_title.text())
        self.assertIn('交立直棒：选手一', panel.latest_detail.text())
        for size in ((1520, 1050), (1280, 800), (960, 620)):
            self.ui.resize(*size)
            settle(APP)
            self.assertTrue(self.ui.workspace.docks['history'].isVisible())
            self.assertTrue(visibility(panel.edit_latest, self.ui)['visible'], size)
            self.assertTrue(visibility(panel.history.horizontalHeader(), self.ui)['visible'], size)
        def edit(dialog):
            self.assertIsInstance(dialog, HandCorrectionDialog)
            self.assertEqual(dialog.hand_id, identity)
            dialog.winner.setCurrentIndex(3)
            dialog.han.setValue(4)
            self.assertTrue(dialog.save_button.isEnabled(), dialog.error.text())
            QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
            return dialog.result()
        with patch.object(QDialog, 'exec', edit):
            panel.open_row(0, 1)
        self.assertEqual(self.model.state['broadcast']['hands'][0]['result']['winner'], 3)
        self.assertEqual([widgets[2].value() for widgets in self.ui.player_widgets], self.scores())
        self.assertEqual(self.manager.stage, 'table')
        self.assertFalse(self.manager.pending_next)
        self.assertFalse(self.model.last_transition)

    def test_dialog_preview_cancel_and_stale_save_never_pay(self):
        identity = self.win()
        before = copy.deepcopy(self.model.state)
        dialog = HandCorrectionDialog(self.ui, identity)
        dialog.winner.setCurrentIndex(2)
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.loser.setCurrentIndex(0)
        dialog.paid[2].click()
        self.assertEqual(dialog.scores[2].value(), 29000)
        self.assertEqual(dialog.pot.value(), 1)
        self.assertEqual(self.model.state, before)
        dialog.reject()
        dialog.deleteLater()
        dialog = HandCorrectionDialog(self.ui, identity)
        self.model.field('honba', 3)
        dialog.save()
        self.assertIn('已变化', dialog.error.text())
        self.assertEqual(self.model.state['honba'], 3)
        dialog.deleteLater()

    def test_yaku_hidden_from_summary_and_direct_income_survives_role_edits(self):
        identity = self.win()
        self.assertNotIn('立直', self.ui.hand_history.latest_title.text())
        self.assertNotIn('宝牌', self.ui.hand_history.latest_title.text())
        dialog = HandCorrectionDialog(self.ui, identity)
        dialog.deltas[1].setValue(9000)
        dialog.deltas[2].setValue(-9000)
        self.assertTrue(dialog.manual.isChecked())
        dialog.yaku['七对子'].click()
        dialog.fu.setCurrentIndex(dialog.fu.findData(25))
        dialog.han.setValue(2)
        self.assertEqual([control.value() for control in dialog.deltas], [0, 9000, -9000, 0])
        self.assertTrue(dialog.save_button.isEnabled(), dialog.error.text())
        dialog.save()
        self.assertEqual(self.scores(), [30000, 39000, 21000, 30000])
        self.assertIn('七对子', self.model.state['broadcast']['hands'][0]['win_info']['yaku'])
        dialog.deleteLater()

    def test_detail_preserves_the_highest_supported_fu(self):
        identity = self.win(fu=140, han=1)
        dialog = HandCorrectionDialog(self.ui, identity)
        self.assertEqual(dialog.fu.currentData(), 140)
        dialog.save()
        self.assertEqual(self.model.state['broadcast']['hands'][0]['result']['fu'], 140)
        dialog.deleteLater()


if __name__ == '__main__':
    unittest.main()
