"""Management navigation and coexistence with immediate child-window edits."""
import copy
import gc
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from PySide6.QtCore import QTimer, QEvent
from PySide6.QtWidgets import QApplication, QDialog
from console import Console, PlayerLayoutDialog, ProfilesDialog
from event_store import EventStore
from i18n import set_language
from model import Model
from overlays import OverlayManager
from settings_dialog import SettingsDialog
from event_workspace import ColumnDialog
from term_buttons import TermButtonDialog
from shiboken6 import isValid

APP = QApplication.instance() or QApplication([])


class SettingsNavigationTests(unittest.TestCase):
    def setUp(self):
        self.close_answer = patch('settings_window.SettingsWindow.ask_save_changes', return_value=QMessageBox.StandardButton.No)
        self.close_answer.start();self.addCleanup(self.close_answer.stop)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'tests')
        self.model = Model(self.tmp.name)
        self.model.settings['show_overlays'] = False
        self.manager = OverlayManager(self.model)
        self.main = Console(self.model, self.manager)
        self.dialog = SettingsDialog(self.main)

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.main.workspace.dispose()
        self.main.shortcuts.dispose()
        self.main.language_controller.dispose()
        APP.removeEventFilter(self.main)
        for name in ('broadcast_dialog', 'event_workspace', 'sidebar_presets_dialog'):
            child = getattr(self.main, name, None)
            if child is not None:
                child.close()
        self.main.hide()
        self.main.deleteLater()
        self.manager.close()
        APP.processEvents()
        set_language('zh_CN')
        self.tmp.cleanup()

    def test_management_buttons_use_existing_pages_and_operations(self):
        self.assertFalse({'events','event_settings','roster','reports','schedule','backup','profiles','demo'} & self.dialog.entry_buttons.keys())
        self.assertFalse(self.main.navigation_buttons['reports'].isHidden())
        for key, index in dict(broadcast=0, qualification=2, glossary=3, data=4).items():
            with self.subTest(entry=key), patch.object(self.dialog, '_open_broadcast_page') as open_page:
                self.dialog.entry_buttons[key].click()
                open_page.assert_called_once_with(index)
        for key, method in dict(player_layout='open_player_layout',
                                sidebar_presets='open_sidebar_presets').items():
            with self.subTest(entry=key), patch.object(self.main, method) as opener:
                self.dialog.entry_buttons[key].click()
                opener.assert_called_once_with()
        with patch.object(self.main.shortcuts, 'open_dialog') as opener:
            self.dialog.entry_buttons['shortcuts'].click()
            opener.assert_called_once_with()

    def test_repeated_main_settings_close_releases_windows_signals_and_previews(self):
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents();gc.collect()
        baseline=sum(isinstance(widget,SettingsDialog) for widget in APP.topLevelWidgets())
        receivers=self.model.receivers('2display_changed()')
        for _ in range(6):
            opened=[]
            def close_settings():
                dialog=self.main.settings_dialog
                opened.append(dialog)
                self.manager.preview_capture(dialog,'players')
                dialog.close()
            QTimer.singleShot(0,close_settings)
            self.main._open_settings_dialog(output=True)
            self.assertIsNone(self.main.settings_dialog)
            self.assertTrue(opened[0]._recognition_preview_closed)
            self.assertFalse(self.manager.capture_previewing('players'))
            APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents();gc.collect()
            self.assertFalse(isValid(opened[0]))
            self.assertEqual(sum(isinstance(widget,SettingsDialog) for widget in APP.topLevelWidgets()),baseline)
            self.assertEqual(self.model.receivers('2display_changed()'),receivers)

    def test_main_and_recognition_settings_release_resources_if_exec_fails(self):
        for opener in (lambda:self.main._open_settings_dialog(output=True),lambda:self.main.open_recognition_settings(2)):
            opened=[]
            def fail(dialog):
                opened.append(dialog)
                self.manager.preview_capture(dialog,'players')
                raise RuntimeError('dialog failed')
            with patch.object(SettingsDialog,'exec',fail),patch.object(SettingsDialog,'focus_recognition'):
                with self.assertRaisesRegex(RuntimeError,'dialog failed'):opener()
            self.assertIsNone(self.main.settings_dialog)
            self.assertTrue(opened[0]._recognition_preview_closed)
            self.assertFalse(self.manager.capture_previewing('players'))
            APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents()
            self.assertFalse(isValid(opened[0]))

    def test_other_one_time_settings_are_destroyed_after_close_or_exec_failure(self):
        self.main.open_event_workspace(3)
        workspace=self.main.event_workspace
        panel=self.main.broadcast_panel()
        cases=((ProfilesDialog,self.main.open_profiles),(PlayerLayoutDialog,self.main.open_player_layout),
               (TermButtonDialog,panel.edit_term_buttons),(ColumnDialog,workspace.edit_columns))
        for dialog_type,opener in cases:
            for failed in (False,True):
                with self.subTest(dialog=dialog_type.__name__,exec_failure=failed):
                    opened=[]
                    receivers=self.model.receivers('2display_changed()')
                    def close_dialog():
                        dialog=APP.activeModalWidget()
                        opened.append(dialog)
                        dialog.close()
                    def fail(dialog):
                        opened.append(dialog)
                        raise RuntimeError('dialog failed')
                    if failed:
                        with patch.object(dialog_type,'exec',fail),self.assertRaisesRegex(RuntimeError,'dialog failed'):
                            opener()
                    else:
                        QTimer.singleShot(0,close_dialog)
                        opener()
                    self.assertIsInstance(opened[0],dialog_type)
                    APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents()
                    self.assertFalse(isValid(opened[0]))
                    self.assertEqual(self.model.receivers('2display_changed()'),receivers)

    def test_nested_management_dialog_returns_to_the_same_modal_draft(self):
        errors, visited = [], []
        self.dialog.theme.setCurrentIndex(1)

        def close_child():
            child = APP.activeModalWidget()
            try:
                self.assertIs(child, self.main.broadcast_panel())
                self.assertEqual(child.tabs.currentIndex(), 3)
                self.assertTrue(child.isEnabled())
                self.assertTrue(self.dialog.isVisible())
                visited.append('glossary')
                self.model.setting('show_melds', False)
            except BaseException as error:
                errors.append(error)
            finally:
                if child is not self.dialog:
                    child.reject()

        def open_child():
            try:
                QTimer.singleShot(0, close_child)
                self.dialog.entry_buttons['glossary'].click()
                self.assertIs(APP.activeModalWidget(), self.dialog)
                self.assertTrue(self.dialog.isVisible())
                self.assertEqual(self.dialog.theme.currentData(), 'light')
                self.dialog.apply()
                self.assertTrue(self.dialog.isVisible())
                self.dialog.reject()
            except BaseException as error:
                errors.append(error)
                self.dialog.reject()

        QTimer.singleShot(0, open_child)
        self.assertEqual(self.dialog.exec(), QDialog.DialogCode.Rejected)
        if errors:
            raise errors[0]
        self.assertEqual(visited, ['glossary'])
        self.assertEqual(self.model.settings['console_theme'], 'light')
        self.assertFalse(self.model.settings['show_melds'])

    def test_unedited_fields_keep_immediate_child_changes(self):
        self.dialog.theme.setCurrentIndex(1)
        self.model.setting('show_melds', False)
        self.model.setting('speed', 1.25)
        window = self.manager.players_capture
        window.move(window.x() + 110, window.y() + 37)
        geometry = window.geometry()
        self.manager.save_geometry()
        undo = copy.deepcopy(self.model.undo)
        self.dialog.apply()
        self.assertEqual(self.dialog.error.text(), '')
        self.assertEqual(self.model.settings['console_theme'], 'light')
        self.assertFalse(self.model.settings['show_melds'])
        self.assertEqual(self.model.settings['speed'], 1.25)
        self.assertEqual(window.geometry(), geometry)
        self.assertEqual(self.model.undo, undo)

    def test_cancel_discards_local_draft_but_keeps_child_saves(self):
        self.dialog.theme.setCurrentIndex(1)
        self.model.setting('show_melds', False)
        self.dialog.reject()
        self.assertEqual(self.model.settings['console_theme'], 'dark')
        self.assertFalse(self.model.settings['show_melds'])
        self.assertFalse(Model(self.tmp.name).settings['show_melds'])

    def test_component_menu_reuses_the_original_actions(self):
        original = tuple(self.main.panel_menu.actions())
        errors, visited = [], []

        def close_menu():
            popup = APP.activePopupWidget()
            try:
                self.assertIsNotNone(popup)
                self.assertEqual(tuple(popup.actions()), original)
                visited.append(True)
            except BaseException as error:
                errors.append(error)
            finally:
                if popup is not None:
                    popup.close()

        QTimer.singleShot(0, close_menu)
        self.dialog.entry_buttons['panels'].click()
        if errors:
            raise errors[0]
        self.assertEqual(visited, [True])
        self.assertEqual(tuple(self.main.panel_menu.actions()), original)

    def test_card_leaves_and_window_coordinates_merge_individually(self):
        self.dialog.card_editor.font_size.setValue(23)
        style = copy.deepcopy(self.model.settings['player_card_style'])
        style['team_name_gap'] = 12.
        self.model.setting('player_card_style', style)
        controls = next(values for values in self.dialog.values if values[0] == 'players')
        controls[2].setValue(controls[2].value() + 80)
        target_x = controls[2].value()
        window = self.manager.players_capture
        target_y = window.y() + 60
        from PySide6.QtCore import QPoint
        self.manager.translate_player_group(self.manager.player_group_snapshot(), QPoint(0, 60))
        self.manager.save_geometry()
        self.dialog.apply()
        self.assertEqual(self.dialog.error.text(), '')
        self.assertEqual(self.model.settings['player_card_style']['text']['team']['size'], 23)
        self.assertEqual(self.model.settings['player_card_style']['team_name_gap'], 12.)
        self.assertEqual((window.x(), window.y()), (target_x, target_y))

    def test_conflicting_field_blocks_all_writes_until_reload(self):
        self.dialog.speed.setValue(1.25)
        self.dialog.theme.setCurrentIndex(1)
        self.model.setting('speed', .75)
        before = copy.deepcopy((self.model.state, self.model.settings, self.model.undo))
        self.dialog.apply()
        self.assertIn('其他窗口', self.dialog.error.text())
        self.assertEqual((self.model.state, self.model.settings, self.model.undo), before)
        self.dialog.reload_button.click()
        self.assertEqual(self.dialog.speed.value(), .75)
        self.assertEqual(self.dialog.theme.currentData(), 'dark')
        self.dialog.theme.setCurrentIndex(1)
        self.dialog.apply()
        self.assertEqual(self.model.settings['speed'], .75)
        self.assertEqual(self.model.settings['console_theme'], 'light')

    def test_scoring_rules_have_one_editing_home_and_child_saves_are_preserved(self):
        self.assertFalse(hasattr(self.dialog, 'start_points'))
        self.model.update(lambda state: state['rules'].update(kiriageMangan=True), 'broadcast')
        self.dialog.theme.setCurrentIndex(1)
        self.assertTrue(self.dialog.apply())
        self.assertTrue(self.model.state['rules']['kiriageMangan'])

    def test_coordinate_conflict_blocks_save(self):
        controls = next(values for values in self.dialog.values if values[0] == 'players')
        original_x = controls[2].value()
        controls[2].setValue(original_x + 50)
        window = self.manager.players_capture
        from PySide6.QtCore import QPoint
        self.manager.translate_player_group(self.manager.player_group_snapshot(), QPoint(100, 0))
        self.manager.save_geometry()
        self.dialog.apply()
        self.assertIn('其他窗口', self.dialog.error.text())
        self.assertEqual(window.x(), original_x + 100)

    def test_event_switch_invalidates_draft_and_reload_adopts_current_event(self):
        self.dialog.theme.setCurrentIndex(1)
        store = EventStore(self.model, Path(self.tmp.name) / 'library')
        # Initial library creation has no change signal. The save guard must
        # still detect it before writing even one stale draft value.
        self.dialog.apply()
        self.assertFalse(self.dialog.save_button.isEnabled())
        self.assertIn('赛事已切换', self.dialog.error.text())
        self.assertEqual(self.model.settings['console_theme'], 'dark')
        self.dialog.reload_button.click()
        self.assertTrue(self.dialog.save_button.isEnabled())
        self.assertEqual(self.dialog.theme.currentData(), 'dark')
        self.assertEqual(self.dialog.event_id, self.model.event['id'])
        current = self.model.event['id']
        store.create('另一赛事', 'individual_league')
        store.open(current)
        self.assertFalse(self.dialog.save_button.isEnabled())
        self.assertIn('赛事已切换', self.dialog.error.text())

    def test_import_in_same_event_also_invalidates_old_drafts(self):
        self.dialog.theme.setCurrentIndex(1)
        self.model.changed.emit(copy.deepcopy(self.model.state), self.model.state, 'import')
        self.assertFalse(self.dialog.save_button.isEnabled())
        self.dialog.apply()
        self.assertEqual(self.model.settings['console_theme'], 'dark')

    def test_speed_hold_and_language_are_local_drafts_until_save(self):
        before = copy.deepcopy((self.model.state, self.model.settings, self.model.undo))
        self.dialog.speed.setValue(.75)
        self.dialog.final_scores_hold.setValue(15.)
        self.dialog.language.setCurrentIndex(1)
        self.assertEqual((self.model.state, self.model.settings, self.model.undo), before)
        self.dialog.apply()
        self.assertEqual(self.dialog.error.text(), '')
        self.assertEqual(self.model.settings['speed'], .75)
        self.assertEqual(self.model.settings['final_scores_hold'], 15.)
        self.assertEqual(self.model.settings['language'], 'ja_JP')
        self.assertEqual(self.model.state, before[0])
        self.assertEqual(self.model.undo, before[2])


if __name__ == '__main__':
    unittest.main()
