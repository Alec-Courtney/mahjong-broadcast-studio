"""Live hand progression and score-strip controls work from the visible desk."""
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
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QDialogButtonBox, QPushButton
from console import Console
from hotkeys import ShortcutDialog
from event_metadata import new_event
from model import Model
from overlays import OverlayManager
try:
    from .ui_test_support import settle, visibility, reveal
except ImportError:
    from ui_test_support import settle, visibility, reveal

APP = QApplication.instance() or QApplication([])


class RoundActionVisibilityTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.cache').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='round-actions-', dir=ROOT / '.cache')
        self.model = Model(self.tmp.name)
        self.model.settings.update(show_overlays=False, topmost=False)
        self.manager = OverlayManager(self.model)
        self.ui = Console(self.model, self.manager)
        self.ui.show()
        settle(APP)

    def tearDown(self):
        self.ui.close()
        self.ui.deleteLater()
        APP.processEvents()
        self.tmp.cleanup()

    def test_header_shortcuts_entry_opens_and_saves_bindings_without_changing_the_table(self):
        self.ui.resize(960, 620); settle(APP)
        self.assertTrue(visibility(self.ui.shortcut_button, self.ui)['visible'])
        shortcut_x = self.ui.shortcut_button.mapTo(self.ui, QPoint()).x()
        workspace_x = self.ui.workspace_menu_button.mapTo(self.ui, QPoint()).x()
        self.assertLess(shortcut_x, workspace_x)
        before = copy.deepcopy((self.model.state, self.model.undo))

        def save(dialog):
            dialog.edits['riichi'].setKeySequence('Ctrl+R')
            self.assertTrue(dialog.save())
            return QDialog.DialogCode.Rejected

        with patch.object(ShortcutDialog, 'exec', save):
            QTest.mouseClick(self.ui.shortcut_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.ui.shortcuts.bindings['riichi'], 'Ctrl+R')
        self.assertEqual(Model(self.tmp.name).settings['hotkeys']['riichi'], 'Ctrl+R')
        self.assertEqual((self.model.state, self.model.undo), before)

    def test_display_buttons_toggle_saved_preferences_without_entering_table_undo(self):
        initial_score = self.model.state['players'][0]['score']
        self.model.set_score(0, 31000)
        before = copy.deepcopy((self.model.state, self.model.undo))
        controls = (
            (self.ui.overlays, lambda: self.model.settings['show_overlays']),
            (self.ui.quick_table_scores, lambda: self.manager.enabled['table_scores']),
            (self.ui.quick_conditions, self.manager.conditions_visible),
            (self.ui.show_melds, lambda: self.model.settings['show_melds']),
        )
        self.assertFalse(self.ui.presentation_group.findChildren(QCheckBox))
        self.assertTrue(self.ui.show_yaku.isHidden())
        self.assertFalse(self.ui.presentation_group.isAncestorOf(self.ui.show_yaku))
        for control, value in controls:
            self.assertIsInstance(control, QPushButton)
            label = control.text(); previous = value()
            for expected in (not previous, previous):
                reveal(control, APP)
                QTest.mouseClick(control, Qt.MouseButton.LeftButton)
                self.assertEqual(value(), expected)
                self.assertEqual(control.isChecked(), expected)
                self.assertEqual(control.text(), label)
        self.assertEqual((self.model.state, self.model.undo), before)
        self.ui.undo.click()
        self.assertEqual(self.model.state['players'][0]['score'], initial_score)

    def test_history_arrows_show_current_shortcuts_and_restore_the_undone_score(self):
        self.assertFalse(self.ui.undo.text());self.assertFalse(self.ui.redo.text())
        self.assertFalse(self.ui.undo.icon().isNull());self.assertFalse(self.ui.redo.icon().isNull())
        self.assertIn('Ctrl+Z', self.ui.undo.toolTip())
        self.assertIn('Ctrl+Shift+Z', self.ui.redo.toolTip())
        self.ui.shortcuts.rebind({'undo': 'Alt+Z', 'redo': 'Alt+Shift+Z'})
        self.assertIn('Alt+Z', self.ui.undo.toolTip())
        self.assertNotIn('Ctrl+Z', self.ui.undo.toolTip())
        self.assertIn('Alt+Shift+Z', self.ui.redo.toolTip())
        initial_score = self.model.state['players'][0]['score']
        self.model.set_score(0, 31000)
        QTest.mouseClick(self.ui.undo, Qt.MouseButton.LeftButton)
        self.assertEqual(self.model.state['players'][0]['score'], initial_score)
        QTest.mouseClick(self.ui.redo, Qt.MouseButton.LeftButton)
        self.assertEqual(self.model.state['players'][0]['score'], 31000)
        self.ui.shortcuts.rebind({'redo': ''})
        self.assertIn('未设置快捷键', self.ui.redo.toolTip())
        self.assertNotIn('Alt+Shift+Z', self.ui.redo.toolTip())

    def test_draw_uses_four_seat_tenpai_status_and_next_hand_is_clickable(self):
        self.ui.resize(960, 620)
        before = [row['score'] for row in self.model.state['players']]
        self.model.tenpai(0)
        QTest.mouseClick(self.ui.quick_actions['draw'], Qt.MouseButton.LeftButton)
        self.assertEqual(self.model.state['phase'], 'result')
        self.assertEqual(self.model.state['lastResult']['tenpai'], [0])
        self.assertEqual([row['score'] for row in self.model.state['players']], [score + delta for score, delta in zip(before, (3000, -1000, -1000, -1000))])
        self.assertFalse(self.ui.quick_actions['draw'].isEnabled())
        self.assertFalse(self.ui.quick_actions['draw_declaration'].isEnabled())
        self.assertTrue(self.ui.quick_actions['next_hand'].isEnabled())
        QTest.mouseClick(self.ui.quick_actions['next_hand'], Qt.MouseButton.LeftButton)
        self.assertEqual(self.model.state['phase'], 'playing')

    def test_draw_declaration_uses_revealed_hands_and_preserves_live_waits(self):
        self.model.field('dealer', 2)
        self.model.submit_waits(2, ['2s', '5s'], 3)
        self.model.riichi(1)
        self.model.set_tenpai(1, False)
        before = self.model.state

        def confirm(dialog):
            dealer, south, west, north = dialog.findChildren(QCheckBox)
            self.assertTrue(dealer.isChecked())
            self.assertFalse(south.isChecked())
            self.assertFalse(west.isChecked())
            self.assertTrue(north.isChecked())
            self.assertFalse(north.isEnabled())
            dealer.click()
            south.click()
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
            return dialog.result()

        with patch.object(QDialog, 'exec', confirm):
            self.ui.quick_actions['draw_declaration'].click()
        result = self.model.state['lastResult']
        self.assertEqual(result['tenpai'], [1, 3])
        self.assertEqual(result['deltas'], [-1500, 1500, -1500, 1500])
        self.assertEqual(result['next']['reason'], 'rotation')
        self.assertEqual(result['next']['dealer'], 3)
        self.assertEqual(result['potAfter'], 1000)
        self.assertEqual(self.model.state['players'][2]['waits'], ['2s', '5s'])
        self.assertTrue(self.model.state['players'][2]['tenpai'])
        self.assertFalse(self.model.state['players'][3]['tenpai'])
        loaded = Model(self.tmp.name)
        self.assertEqual(loaded.state['broadcast']['hands'][-1]['result']['tenpai'], [1, 3])
        self.model.history('undo')
        self.assertEqual(self.model.state, before)

    def test_draw_declaration_cancel_does_not_submit_or_change_tenpai(self):
        self.model.tenpai(0)
        before = self.model.state
        undo = len(self.model.undo)

        def cancel(dialog):
            dialog.findChildren(QCheckBox)[0].click()
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel).click()
            return dialog.result()

        with patch.object(QDialog, 'exec', cancel):
            self.ui.quick_actions['draw_declaration'].click()
        self.assertIs(self.model.state, before)
        self.assertEqual(len(self.model.undo), undo)
        self.ui.quick_actions['draw'].click()
        self.assertEqual(self.model.state['lastResult']['tenpai'], [0])

    def test_score_strip_clicks_persist_when_global_output_is_on_or_off(self):
        for global_output in (False, True):
            self.manager.set_overlays_visible(global_output)
            self.manager.apply_scene('clean')
            self.manager.apply_scene('table')
            reveal(self.ui.quick_table_scores, APP)
            for expected in (False, True, False):
                QTest.mouseClick(self.ui.quick_table_scores, Qt.MouseButton.LeftButton)
                QTest.qWait(360)
                APP.processEvents()
                with self.subTest(global_output=global_output, expected=expected):
                    self.assertEqual(self.ui.quick_table_scores.isChecked(), expected)
                    self.assertEqual(self.manager.enabled['table_scores'], expected)
                    self.assertEqual(self.model.settings['visible']['table_scores'], expected)
                    self.assertEqual(self.manager.content_visible('table_scores'), expected and global_output)
                    loaded = Model(self.tmp.name)
                    self.assertEqual(loaded.settings['visible']['table_scores'], expected)
                    if not expected:
                        # The stable capture window stays open; its old artwork
                        # must have cleared completely after the exit finishes.
                        image = self.manager.windows['table_scores'].grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
                        self.assertEqual(max(bytes(image.bits())[3::4]), 0)

    def test_team_event_can_clear_an_inherited_score_strip_selection(self):
        self.model.event = new_event('团队赛', 'team_league')
        self.ui.refresh_quick_actions()
        self.assertTrue(self.ui.quick_table_scores.isChecked())
        self.assertTrue(self.ui.quick_table_scores.isEnabled())
        self.ui.quick_table_scores.click()
        self.assertFalse(self.manager.enabled['table_scores'])
        self.assertFalse(self.ui.quick_table_scores.isChecked())
        self.assertFalse(self.ui.quick_table_scores.isEnabled())


if __name__ == '__main__':
    unittest.main()
