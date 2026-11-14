"""Per-seat live wait counts and resizing dora preserve operator drafts."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QFocusEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSpinBox
from console import Console
from model import Model
from overlays import OverlayManager
try:
    from .ui_test_support import settle, reveal, visibility
except ImportError:
    from ui_test_support import settle, reveal, visibility

APP = QApplication.instance() or QApplication([])


class PlayerWaitControlTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.cache').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='player-waits-', dir=ROOT / '.cache')
        self.model = Model(self.temp.name)
        self.model.settings.update(show_overlays=False, topmost=False)
        self.ui = Console(self.model, OverlayManager(self.model))
        self.ui.resize(1460, 1000); self.ui.show(); settle(APP)

    def tearDown(self):
        self.ui.close(); self.ui.deleteLater(); APP.processEvents(); self.temp.cleanup()

    def test_title_keeps_its_original_behavior_and_hand_actions_live_at_score_top(self):
        self.assertTrue(self.ui.round_box.isAncestorOf(self.ui.match_title))
        self.ui.match_title.setText('原样保留的比赛标题'); self.ui.match_title.editingFinished.emit()
        self.assertEqual(self.model.state['title'], '原样保留的比赛标题')
        for key in ('draw', 'draw_declaration', 'next_hand'):
            control = self.ui.quick_actions[key]
            self.assertTrue(self.ui.score_box.isAncestorOf(control))
            self.assertFalse(self.ui.round_box.isAncestorOf(control))
            self.assertLess(control.mapTo(self.ui.score_box, QPoint()).y(), self.ui.winner.y())

    def test_all_seats_show_published_waits_and_rows_follow_visible_hands(self):
        for index in range(4):
            waits = [f'{index+1}m', f'{index+2}p']
            self.model.submit_waits(index, waits, index, [waits[0]], index == 2)
        hand = self.ui.recognized_hands[2]
        hand.set_observation(['1m'] * 13); hand.show(); settle(APP)
        for index, (tiles, count) in enumerate(self.ui.player_wait_widgets):
            self.assertEqual(tiles.waits, self.model.state['players'][index]['waits'])
            self.assertEqual(count.value(), index)
            self.assertEqual(tiles.no_yaku, [f'{index+1}m'])
            self.assertTrue(tiles.tenpai)
            reveal(tiles, APP); self.assertTrue(visibility(tiles, self.ui)['visible'])
        tiles = self.ui.player_wait_widgets[2][0]
        self.assertGreaterEqual(tiles.y(), hand.geometry().bottom())
        QTest.mouseClick(tiles, Qt.MouseButton.LeftButton)
        self.assertEqual(self.ui.selected, 2)
        self.assertEqual(self.ui.tile_target, 'waits')

    def test_unselected_seat_count_updates_live_without_submitting_any_wait_drafts(self):
        self.model.submit_waits(2, ['3m'], 4)
        self.ui.select(2); self.ui.add_wait_draft('6m'); self.ui.furiten.click()
        self.ui.select(0); self.ui.add_wait_draft('9p')
        other_players = copy.deepcopy(self.model.state['players'])
        self.ui.player_wait_widgets[2][1].setValue(0)
        self.assertEqual(self.ui.selected, 0)
        self.assertEqual(self.model.state['players'][2]['remaining'], 0)
        self.assertEqual(self.model.state['players'][2]['waits'], ['3m'])
        self.assertFalse(self.model.state['players'][2].get('furiten'))
        self.assertEqual(self.ui.wait_drafts[2]['waits'], ['3m', '6m'])
        self.assertTrue(self.ui.wait_drafts[2]['furiten'])
        self.assertEqual(self.ui.wait_drafts[2]['remaining'], 0)
        self.assertEqual(self.ui.wait_draft()['waits'], ['9p'])
        self.assertEqual(self.ui.wait_dirty, {0, 2})
        for index in (0, 1, 3):
            self.assertEqual(self.model.state['players'][index], other_players[index])
        self.assertEqual(Model(self.temp.name).state['players'][2]['remaining'], 0)
        self.ui.select(2); self.ui.submit_waits()
        self.assertEqual(self.model.state['players'][2]['waits'], ['3m', '6m'])
        self.assertEqual(self.model.state['players'][2]['remaining'], 0)

    def test_count_supports_typing_step_buttons_wheel_unknown_and_undo(self):
        self.model.submit_waits(1, ['4s'], 4)
        count = self.ui.player_wait_widgets[1][1]; reveal(count, APP)
        count.lineEdit().setText('12 张'); count.interpretText()
        self.assertEqual(self.model.state['players'][1]['remaining'], 12)
        for subcontrol, expected in ((QStyle.SubControl.SC_SpinBoxUp, 13), (QStyle.SubControl.SC_SpinBoxDown, 12)):
            option = QStyleOptionSpinBox(); count.initStyleOption(option)
            rect = count.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, subcontrol, count)
            QTest.mouseClick(count, Qt.MouseButton.LeftButton, pos=rect.center())
            self.assertEqual(self.model.state['players'][1]['remaining'], expected)
        event = QWheelEvent(QPointF(count.rect().center()), QPointF(count.mapToGlobal(count.rect().center())),
                            QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        APP.sendEvent(count, event)
        self.assertEqual(self.model.state['players'][1]['remaining'], 13)
        count.setValue(0); self.assertEqual(self.model.state['players'][1]['remaining'], 0)
        count.lineEdit().setText(''); count.interpretText()
        self.assertIsNone(self.model.state['players'][1]['remaining'])
        self.model.history('undo'); self.assertEqual(count.value(), 0)
        count.setValue(500); self.assertEqual(self.model.state['players'][1]['remaining'], 136)

    def test_failed_count_save_restores_controls_and_pending_draft(self):
        self.model.submit_waits(0, ['3m'], 4)
        self.ui.add_wait_draft('6m')
        with patch.object(self.model, 'set_remaining', side_effect=OSError('写入失败')):
            self.ui.player_wait_widgets[0][1].setValue(2)
        self.assertEqual(self.model.state['players'][0]['remaining'], 4)
        self.assertEqual(self.ui.remaining.value(), 4)
        self.assertEqual(self.ui.player_wait_widgets[0][1].value(), 4)
        self.assertEqual(self.ui.wait_draft()['waits'], ['3m', '6m'])
        self.assertEqual(self.ui.wait_draft()['remaining'], 4)
        self.assertIn('写入失败', self.ui.status.text())

    def test_next_hand_and_undo_refresh_all_inline_status(self):
        self.model.submit_waits(3, ['7z'], 0)
        self.ui.quick_actions['draw'].click(); self.ui.quick_actions['next_hand'].click()
        tiles, count = self.ui.player_wait_widgets[3]
        self.assertFalse(tiles.tenpai); self.assertEqual(tiles.waits, []); self.assertEqual(count.value(), -1)
        self.model.history('undo'); self.model.history('undo')
        self.assertTrue(tiles.tenpai); self.assertEqual(tiles.waits, ['7z']); self.assertEqual(count.value(), 0)

    def test_dora_faces_fit_both_axes_and_removal_keeps_its_index(self):
        for code in ('1m', '4p', '7s', '1z', '7z'): self.model.dora(code)
        dock = self.ui.workspace.docks['dora']; dock.setFloating(True)
        sizes = []
        for width, height in ((240, 120), (600, 300), (190, 250)):
            dock.resize(width, height); settle(APP)
            buttons = [self.ui.dora_row.itemAt(index).widget() for index in range(5)]
            sizes.append(buttons[0].size())
            for control in buttons:
                self.assertTrue(self.ui.dora_board.rect().contains(control.geometry()))
                self.assertEqual(control.iconSize(), control.size())
                self.assertAlmostEqual(control.height()/control.width(), 36/25, delta=.15)
        self.assertGreater(sizes[1].height(), sizes[0].height())
        self.assertLess(sizes[2].width(), sizes[1].width())
        before = list(self.model.dora_tiles)
        self.ui.dora_row.itemAt(2).widget().click()
        self.assertEqual(self.model.dora_tiles, before[:2]+before[3:])

    def test_window_focus_restoration_cannot_redirect_dora_tiles_to_waits(self):
        self.model.submit_waits(0, ['3m'], 4)
        self.ui.focus_tile_target('dora')
        for reason in (Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.OtherFocusReason):
            APP.sendEvent(self.ui.remaining, QFocusEvent(QFocusEvent.Type.FocusIn, reason))
            self.assertEqual(self.ui.tile_target, 'dora')
        before = copy.deepcopy(self.model.state['players'])
        self.ui.tile_buttons['7p'].click()
        self.assertEqual(self.model.dora_tiles, ['7p'])
        self.assertEqual(self.model.state['players'], before)
        self.assertEqual(self.ui.wait_draft()['waits'], ['3m'])
        APP.sendEvent(self.ui.remaining, QFocusEvent(QFocusEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason))
        self.assertEqual(self.ui.tile_target, 'waits')

    def test_undo_restores_published_waits_in_editor_despite_an_empty_pending_draft(self):
        self.model.submit_waits(2, ['3m'], 4)
        self.model.submit_waits(2, ['6m'], 2)
        self.ui.select(2); self.ui.clear_wait_draft()
        self.assertEqual(self.ui.wait_draft()['waits'], [])
        self.ui.undo.click()
        self.assertEqual(self.model.state['players'][2]['waits'], ['3m'])
        self.assertEqual(self.ui.wait_draft()['waits'], ['3m'])
        self.assertEqual(self.ui.wait_row.count(), 1)
        self.assertEqual(self.ui.remaining.value(), 4)
        self.assertNotIn(2, self.ui.wait_dirty)
        self.ui.select(0); self.ui.select(2)
        self.assertEqual(self.ui.wait_draft()['waits'], ['3m'])

    def test_failed_wait_submission_keeps_the_unsubmitted_draft_when_switching_seats(self):
        self.model.submit_waits(0, ['3m'], 4)
        self.ui.add_wait_draft('6m')
        before = copy.deepcopy(self.model.state)
        with patch.object(self.model, '_save_snapshot', side_effect=OSError('写入失败')):
            self.ui.submit_wait_button.click()
        self.assertEqual(self.model.state, before)
        self.assertIn(0, self.ui.wait_dirty)
        self.ui.select(1); self.ui.select(0)
        self.assertEqual(self.ui.wait_draft()['waits'], ['3m', '6m'])


if __name__ == '__main__': unittest.main()
