"""Saving keeps editors open; closing never silently loses a settings draft."""
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
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from console import Console, PlayerLayoutDialog, ProfilesDialog
from event_store import EventStore
from event_workspace import ColumnDialog
from hotkeys import ShortcutDialog
from model import Model
from overlays import OverlayManager
from settings_dialog import SettingsDialog
from term_buttons import TermButtonDialog

APP = QApplication.instance() or QApplication([])


class SettingsSaveFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tmp-settings-flow-', dir=ROOT / 'tests')
        self.model = Model(self.temp.name)
        self.store = EventStore(self.model)
        self.model.settings['show_overlays'] = False
        self.manager = OverlayManager(self.model)
        self.main = Console(self.model, self.manager)
        self.dialog = SettingsDialog(self.main)
        self.close_answer = patch('settings_window.SettingsWindow.ask_save_changes', return_value=QMessageBox.StandardButton.No)
        self.question = self.close_answer.start()
        self.addCleanup(self.close_answer.stop)

    def tearDown(self):
        for dialog in [self.dialog, getattr(self.main, 'event_workspace', None), getattr(self.main, 'broadcast_dialog', None)]:
            if dialog is not None:
                dialog._allow_close = True
                dialog.close()
        self.main.workspace.dispose(); self.main.shortcuts.dispose(); self.main.language_controller.dispose()
        APP.removeEventFilter(self.main)
        self.main.hide(); self.main.deleteLater(); self.manager.close(); APP.processEvents()
        self.temp.cleanup()

    def test_repeated_saves_stay_open_skip_noop_and_write_once(self):
        self.dialog.show(); APP.processEvents()
        undo = copy.deepcopy(self.model.undo)
        with patch.object(self.model.repository, 'save', wraps=self.model.repository.save) as save:
            self.dialog.final_scores_hold.setValue(8)
            self.assertTrue(self.dialog.apply())
            self.assertTrue(self.dialog.isVisible())
            self.assertEqual(save.call_count, 1)
            self.assertEqual(self.dialog.dirty_sections(), [])
            self.assertTrue(self.dialog.apply())
            self.assertEqual(save.call_count, 1)
            self.dialog.final_scores_hold.setValue(9)
            self.assertTrue(self.dialog.apply())
            self.assertEqual(save.call_count, 2)
        self.assertEqual(self.model.undo, undo)
        self.assertEqual(Model(self.model.path).settings['final_scores_hold'], 9)
        self.dialog.close(); self.question.assert_not_called()

    def test_close_yes_saves_and_close_no_keeps_previous_save(self):
        self.dialog.show(); self.dialog.final_scores_hold.setValue(8)
        with patch('settings_window.SettingsWindow.ask_save_changes', return_value=QMessageBox.StandardButton.Yes) as question:
            self.assertTrue(self.dialog.close())
            self.assertEqual(question.call_count, 1)
        self.assertEqual(Model(self.model.path).settings['final_scores_hold'], 8)
        self.dialog.show(); self.dialog.final_scores_hold.setValue(15)
        self.assertTrue(self.dialog.close())
        self.assertEqual(Model(self.model.path).settings['final_scores_hold'], 8)
        self.question.assert_called_once()

    def test_failed_save_keeps_draft_open_and_rolls_back_geometry_and_state(self):
        self.dialog.show(); self.dialog.final_scores_hold.setValue(15)
        control = next(row for row in self.dialog.values if row[0] == 'players')
        geometry = self.manager.windows['player0'].geometry()
        control[2].setValue(control[2].value() + 30)
        before = copy.deepcopy((self.model.settings, self.model.state, self.model.undo))
        with patch('settings_window.SettingsWindow.ask_save_changes', return_value=QMessageBox.StandardButton.Yes), \
             patch.object(self.model.repository, 'save', side_effect=OSError('无法写入')):
            self.assertFalse(self.dialog.close())
        self.assertTrue(self.dialog.isVisible())
        self.assertIn('无法写入', self.dialog.error.text())
        self.assertEqual((self.model.settings, self.model.state, self.model.undo), before)
        self.assertEqual(self.manager.windows['player0'].geometry(), geometry)
        self.assertEqual(self.dialog.final_scores_hold.value(), 15)

    def test_settings_font_is_independent_persistent_and_updates_other_editors(self):
        self.main.open_event_workspace(1); workspace = self.main.event_workspace
        panel = self.main.broadcast_panel()
        self.dialog.settings_font_size.setValue(18)
        self.assertEqual(self.model.settings.get('console_font_size'), 14)
        for window in (self.dialog, workspace, panel):
            self.assertIn('font-size:18px', window.styleSheet())
        self.assertIn('font-size:14px', self.main.styleSheet())
        self.assertEqual(Model(self.model.path).settings['settings_font_size'], 18)
        self.assertEqual(self.dialog.dirty_sections(), [])

    def test_output_style_and_other_drafts_apply_and_persist_per_event(self):
        self.main.open_event_workspace(1)
        workspace = self.main.event_workspace
        workspace.ensure_event_settings()
        workspace.select_settings_category('output')
        editor = workspace.output_editor
        editor.card_editor.editing_style.setCurrentIndex(1)
        editor.show_melds.setChecked(False)
        editor.bg.setCurrentIndex(editor.bg.findData('blue'))
        editor.overlay_font_scale.setValue(125)
        editor.split_fullscreen.setChecked(True)
        first_id = self.model.event['id']
        self.assertTrue(editor.save_changes(), editor.error.text())
        expected = dict(theme='saikouisen', show_melds=False, capture_bg='blue',
                        overlay_font_scale=1.25, split_fullscreen_windows=True)
        for key,value in expected.items():
            self.assertEqual(self.model.settings[key], value)
            self.assertEqual(Model(self.model.path).settings[key], value)
        self.store.create('独立样式赛事', 'individual_league')
        self.assertEqual(self.model.settings['theme'], 'mleague')
        self.store.open(first_id)
        for key,value in expected.items():self.assertEqual(self.model.settings[key], value)

    def test_live_font_save_does_not_save_other_drafts_or_add_score_undo(self):
        undo = copy.deepcopy(self.model.undo)
        hold = self.model.settings['final_scores_hold']
        self.dialog.final_scores_hold.setValue(19)
        self.dialog.theme.setCurrentIndex(1)
        self.dialog.console_font_size.setValue(16)
        self.assertIn('font-size:16px', self.main.styleSheet())
        self.assertEqual(self.model.settings['console_font_size'], 16)
        restored = Model(self.model.path)
        self.assertEqual(restored.settings['console_font_size'], 16)
        self.assertEqual(restored.settings['final_scores_hold'], hold)
        self.assertEqual(restored.settings['console_theme'], 'dark')
        self.assertEqual(self.model.undo, undo)
        self.assertEqual(self.dialog.dirty_sections(), ['settings'])
        self.assertTrue(self.dialog.apply(), self.dialog.error.text())
        self.assertEqual(self.model.settings['final_scores_hold'], 19)
        self.assertEqual(self.dialog.dirty_sections(), [])

    def test_live_font_failure_rolls_back_control_and_model(self):
        before = copy.deepcopy(self.model.settings)
        with patch.object(self.model.repository, 'save', side_effect=OSError('无法写入')):
            self.dialog.settings_font_size.setValue(19)
        self.assertEqual(self.dialog.settings_font_size.value(), 14)
        self.assertEqual(self.model.settings, before)
        self.assertIn('无法写入', self.dialog.error.text())
        self.assertEqual(self.dialog.dirty_sections(), [])

    def test_demo_overwrite_controls_are_gone_and_legacy_restore_still_works(self):
        workspace = self.main.open_event_settings('statistics')
        panel = workspace.broadcast_settings
        self.assertNotIn('demo', self.dialog.entry_buttons)
        self.assertFalse(hasattr(panel, 'roster'))
        self.assertIs(panel.tabs.currentWidget(), panel.categories['statistics'])
        self.assertTrue(panel.demo_restore.isHidden())

        original = copy.deepcopy(self.model.state)
        self.model.export_data(panel.demo_session.backup_path)
        self.model.set_score(0, 34600)
        self.model.setting('demo_active', True)
        self.assertFalse(panel.demo_restore.isHidden())
        self.assertTrue(panel.demo_restore.isEnabled())
        self.assertEqual(panel.demo_badge.text(), '演示数据')

        panel.demo_restore.click()
        self.assertEqual(self.model.state, original)
        self.assertTrue(panel.demo_restore.isHidden())
        self.assertEqual(panel.demo_badge.text(), '')

    def test_translating_builtin_term_does_not_block_save_and_create(self):
        self.main.open_event_workspace(0)
        workspace = self.main.event_workspace
        panel = self.main.broadcast_panel()
        original = self.model.event['id']
        custom_terms = [[panel.commentary_title.text(), '原来保存的自定义解释']]
        self.model.configure_commentary({'custom_terms': custom_terms})
        for language in ('ja_JP', 'zh_CN'):
            self.main.language.setCurrentIndex(self.main.language.findData(language))
            self.assertNotIn('term', panel.dirty_sections())

        workspace.event_name.setText('切换前保存的名称')
        workspace.new_name.setText('从保存提示继续新建')
        prompts = []

        def save_and_continue():
            dialog = APP.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                prompts.append(dialog.text())
                QTest.mouseClick(dialog.button(QMessageBox.StandardButton.Yes), Qt.MouseButton.LeftButton)

        QTimer.singleShot(0, workspace, save_and_continue)
        workspace.create_event()

        self.assertNotEqual(self.model.event['id'], original, workspace.status.text())
        self.assertEqual(self.model.event['name'], '从保存提示继续新建')
        self.assertEqual(len(prompts), 1)
        self.assertNotIn(panel.windowTitle(), prompts[0])
        saved = Model(self.store.directory(original))
        self.assertEqual(saved.event['name'], '切换前保存的名称')
        self.assertEqual(saved.state['broadcast']['config']['custom_terms'], custom_terms)

    def test_commentary_style_preview_uses_draft_without_saving_words_or_going_live(self):
        workspace = self.main.open_event_settings('commentary')
        panel = workspace.broadcast_settings
        editor = panel.commentary_style
        APP.processEvents()
        self.assertTrue(editor.preview.isVisible())
        before_image = editor.preview.grab().toImage()
        before = copy.deepcopy(self.model.state)
        panel.commentary_title.setText('预览测试')
        panel.commentary_body.setPlainText('临时输入的术语解释')
        editor.controls['commentary_title_font_family'].setText('Arial')
        editor.controls['commentary_body_font_size'].setValue(37)
        editor.controls['commentary_body_letter_spacing'].setValue(3.5)
        self.assertNotEqual(editor.preview.grab().toImage(), before_image)
        self.assertEqual(editor._preview_layout['title_font'].family(), 'Arial')
        self.assertEqual(editor._preview_layout['body_font'].pixelSize(), 37)
        self.assertEqual(editor._preview_layout['body_font'].letterSpacing(), 3.5)
        self.assertEqual(self.model.state, before)
        self.assertFalse(self.manager.enabled.get('commentary', False))
        self.assertIn('commentary_style', panel.dirty_sections())

        panel.save_commentary_style()
        self.assertFalse(panel._save_failed, workspace.status.text())
        self.assertNotIn('commentary_style', panel.dirty_sections())
        self.assertIn('term', panel.dirty_sections())
        self.assertFalse(self.manager.enabled.get('commentary', False))
        expected = dict(before['broadcast']['config'], commentary_title_font_family='Arial',
                        commentary_body_font_size=37, commentary_body_letter_spacing=3.5)
        self.assertEqual(Model(self.model.path).state['broadcast']['config'], expected)

    def test_event_settings_and_roster_both_survive_save_all(self):
        self.main.open_event_workspace(1); workspace = self.main.event_workspace
        workspace.event_name.setText('修改后的赛事')
        workspace.roster.item(0, 0).setText('修改后的选手')
        self.assertCountEqual(workspace.dirty_sections(), ['general', 'roster'])
        self.assertTrue(workspace.save_changes())
        self.assertTrue(workspace.isVisible())
        self.assertEqual(self.model.event['name'], '修改后的赛事')
        self.assertEqual(self.model.state['broadcast']['roster'][0]['name'], '修改后的选手')
        self.assertEqual(workspace.dirty_sections(), [])

    def test_create_event_save_prompt_saves_each_editor_to_the_original_event(self):
        self.main.open_event_workspace(0)
        workspace = self.main.event_workspace
        panel = self.main.broadcast_panel()
        original = self.model.event['id']
        workspace.event_name.setText('保存的原赛事名称')
        panel.postmatch_chart_hold.setValue(19)
        workspace.new_name.setText('新赛事')

        def confirm_save():
            dialog = APP.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                dialog.button(QMessageBox.StandardButton.Yes).click()

        timer = QTimer(workspace)
        timer.setSingleShot(True)
        timer.timeout.connect(confirm_save)
        timer.start(0)
        workspace.create_event()
        timer.stop()

        self.assertNotEqual(self.model.event['id'], original, workspace.status.text())
        self.assertEqual(self.model.event['name'], '新赛事')
        saved = Model(self.store.directory(original))
        self.assertEqual(saved.event['name'], '保存的原赛事名称')
        self.assertEqual(saved.state['broadcast']['config']['postmatch_chart_hold'], 19)

    def test_save_failure_in_another_editor_is_visible_in_create_flow(self):
        self.main.open_event_workspace(0)
        workspace = self.main.event_workspace
        panel = self.main.broadcast_panel()
        original = self.model.event['id']
        panel.title.setText('待保存的播出标题')
        workspace.new_name.setText('保留待创建赛事')
        with patch.object(workspace, 'ask_event_change', return_value=QMessageBox.StandardButton.Yes), \
             patch.object(self.model.repository, 'save', side_effect=OSError('文件不可写')):
            workspace.create_event()
        self.assertEqual(self.model.event['id'], original)
        self.assertEqual(workspace.new_name.text(), '保留待创建赛事')
        self.assertIn(panel.windowTitle(), workspace.status.text())
        self.assertIn('文件不可写', workspace.status.text())
        self.assertIn('presentation', panel.dirty_sections())

    def test_broadcast_save_all_retains_drafts_across_roster_save(self):
        workspace = self.main.open_event_settings('presentation')
        panel = workspace.broadcast_settings
        panel.title.setText('新的播出标题')
        panel.postmatch_chart_hold.setValue(19)
        workspace.roster.item(0, 0).setText('新的播出选手')
        panel.qualification_title.setText('新的晋级标题')
        self.assertTrue(workspace.save_changes(), workspace.status.text())
        config = self.model.state['broadcast']['config']
        self.assertEqual(config['title'], '新的播出标题')
        self.assertEqual(config['postmatch_chart_hold'], 19)
        self.assertEqual(config['qualification_title'], '新的晋级标题')
        self.assertEqual(self.model.state['broadcast']['roster'][0]['name'], '新的播出选手')
        self.assertEqual(panel.dirty_sections(), [])

    def test_event_settings_open_as_categories_in_the_same_modal_window(self):
        self.main.open_event_workspace(1); workspace = self.main.event_workspace
        workspace.hide(); errors = []
        def open_panel():
            try:
                workspace.select_settings_category('presentation')
                panel = self.main.broadcast_panel()
                self.assertIs(APP.activeModalWidget(), workspace)
                self.assertIs(panel.window(), workspace)
                self.assertFalse(hasattr(panel, 'roster'))
                self.assertEqual(panel.tabs.tabText(panel.tabs.currentIndex()), '播出与动画')
                self.assertEqual(set(panel.categories), {'rules','presentation','qualification','commentary','statistics','output'})
                workspace.select_settings_category('output')
                self.assertIs(workspace.output_editor.window(), workspace)
                self.assertFalse(hasattr(workspace.output_editor,'theme'))
            except BaseException as error:
                errors.append(error)
            finally:
                workspace.reject()
        QTimer.singleShot(0, open_panel)
        workspace.exec()
        if errors:raise errors[0]

    def test_system_button_and_live_animation_settings_have_separate_scope(self):
        from system_settings import SystemSettingsDialog

        opened = []
        def edit_system(dialog):
            opened.append(dialog.windowTitle())
            self.assertFalse(hasattr(dialog, 'speed'))
            dialog.dock_animations.setChecked(True)
            self.assertTrue(dialog.apply())
            return QDialog.DialogCode.Rejected

        self.assertEqual(self.main.navigation_buttons['settings'].text(), '系统设置')
        with patch.object(SystemSettingsDialog, 'exec', edit_system):
            self.main.navigation_buttons['settings'].click()
        self.assertEqual(opened, ['系统设置'])
        self.assertTrue(self.main.isAnimated())
        workspace = self.main.open_event_settings('presentation')
        panel = workspace.broadcast_settings
        panel.live_speed.setValue(1.75)
        panel.live_riichi.setChecked(False)
        panel.postmatch_chart_hold.setValue(18)
        self.assertTrue(workspace.save_changes(), workspace.status.text())
        first = self.model.event['id']
        self.store.create('另一赛事', 'team_league')
        self.assertEqual(panel.live_speed.value(), 1.)
        self.assertTrue(panel.live_riichi.isChecked())
        self.assertTrue(self.main.isAnimated())
        self.store.open(first)
        self.assertEqual(panel.live_speed.value(), 1.75)
        self.assertFalse(panel.live_riichi.isChecked())
        self.assertEqual(panel.postmatch_chart_hold.value(), 18)
        self.assertTrue(self.main.isAnimated())

    def test_embedded_statistics_can_save_with_an_unrelated_roster_edit(self):
        workspace = self.main.open_event_settings('statistics')
        panel = workspace.broadcast_settings
        panel.baselines.item(0, 1).setText('12')
        workspace.roster.item(0, 0).setText('新的选手昵称')
        self.assertTrue(workspace.save_changes(), workspace.status.text())
        first = self.model.state['broadcast']['roster'][0]
        self.assertEqual((first['name'], first['stats']['hands']), ('新的选手昵称', 12))
        self.assertEqual(workspace.dirty_sections(), [])
    def test_shortcuts_profiles_and_layout_save_in_place(self):
        shortcuts = ShortcutDialog(self.main.shortcuts); shortcuts.show()
        shortcuts.edits['riichi'].setKeySequence(QKeySequence('Ctrl+R'))
        self.assertTrue(shortcuts.save()); self.assertTrue(shortcuts.isVisible())
        self.assertEqual(shortcuts.dirty_sections(), [])
        shortcuts.close(); shortcuts.deleteLater()
        profiles = ProfilesDialog(self.main); profiles.show()
        profiles.fields[0]['name'].setText('保存后的姓名')
        self.assertTrue(profiles.apply()); self.assertTrue(profiles.isVisible())
        profiles.close(); profiles.deleteLater()
        layout = PlayerLayoutDialog(self.main); layout.show()
        original = self.manager.player_group_geometry()
        layout.x.setValue(layout.x.value()+30)
        self.assertEqual(self.manager.player_group_geometry(), original)
        layout.apply(); self.assertTrue(layout.isVisible()); self.assertEqual(layout.dirty_sections(), [])
        layout.close(); layout.deleteLater()

    def test_column_settings_save_without_closing(self):
        self.main.open_event_workspace(3); workspace = self.main.event_workspace
        columns = ColumnDialog(workspace, self.model.event, 'individual'); columns.show()
        columns.rename_label('当前名次')
        self.assertTrue(columns.save(), columns.error.text())
        self.assertTrue(columns.isVisible())
        self.assertEqual(self.model.event['report_headers']['individual']['rank'], '当前名次')
        self.assertEqual(columns.dirty_sections(), [])
        columns.close(); columns.deleteLater()

    def test_card_edits_save_and_close_after_changing_preview_target(self):
        self.dialog.show(); self.dialog._go_to_page('选手卡样式')
        editor = self.dialog.card_editor
        editor.text_target.setCurrentIndex(2)
        editor.font_size.setValue(56)
        self.assertEqual(self.dialog.dirty_sections(), ['settings'])
        QTest.mouseClick(self.dialog.save_button, Qt.MouseButton.LeftButton)
        APP.processEvents()
        editor.text_target.setCurrentIndex(0)
        self.assertEqual(self.dialog.dirty_sections(), [])
        self.assertEqual(Model(self.model.path).settings['player_card_style']['text']['score']['size'], 56)
        self.assertTrue(self.dialog.close())
        self.question.assert_not_called()

    def test_actual_yes_button_saves_card_edit_and_closes_once(self):
        self.dialog.show(); self.dialog._go_to_page('选手卡样式')
        self.dialog.card_editor.text_target.setCurrentIndex(2)
        self.dialog.card_editor.font_size.setValue(56)
        answers, errors = [], []
        def choose_yes():
            question = APP.activeModalWidget()
            try:
                self.assertIsInstance(question, QMessageBox)
                answers.append(question.button(QMessageBox.StandardButton.Yes).text())
                QTest.mouseClick(question.button(QMessageBox.StandardButton.Yes), Qt.MouseButton.LeftButton)
            except BaseException as error:
                errors.append(error)
                if isinstance(question, QMessageBox):question.done(QMessageBox.StandardButton.No)
        self.close_answer.stop()
        try:
            QTimer.singleShot(0, choose_yes)
            self.assertTrue(self.dialog.close())
        finally:self.close_answer.start()
        if errors:raise errors[0]
        self.assertEqual(answers, ['是'])
        self.assertFalse(self.dialog.isVisible())
        self.assertEqual(Model(self.model.path).settings['player_card_style']['text']['score']['size'], 56)

    def test_unconfirmed_numeric_typing_is_detected_and_can_be_saved_on_close(self):
        self.dialog.show()
        self.dialog.final_scores_hold.lineEdit().setText('16.0 秒')
        self.assertEqual(self.dialog.dirty_sections(), ['settings'])
        with patch('settings_window.SettingsWindow.ask_save_changes', return_value=QMessageBox.StandardButton.Yes):
            self.assertTrue(self.dialog.close())
        self.assertEqual(Model(self.model.path).settings['final_scores_hold'], 16)

    def test_window_sizes_show_percent_and_keep_geometry_when_saving_other_settings(self):
        before = {key: window.geometry() for key, window in self.manager.layout_windows.items()}
        for key, show, x, y, scale in self.dialog.values:
            self.assertEqual(scale.suffix(), ' %')
            self.assertEqual(scale.pixel_width(), before[key].width())
        self.dialog.final_scores_hold.setValue(8)
        self.assertTrue(self.dialog.apply(), self.dialog.error.text())
        self.assertEqual({key: window.geometry() for key, window in self.manager.layout_windows.items()}, before)
        self.assertEqual(self.dialog.dirty_sections(), [])

    def test_window_scale_saves_as_compatible_pixel_geometry(self):
        self.dialog.show()
        scale = next(row[4] for row in self.dialog.values if row[0] == 'players')
        expected_width = round(scale.base_width * 1.25)
        scale.setValue(125)
        self.assertTrue(self.dialog.apply(), self.dialog.error.text())
        window = self.manager.players_capture
        self.assertLessEqual(abs(window.width()-expected_width), 2)
        saved = Model(self.model.path).settings['windows']
        for index in range(4):
            self.assertTrue(all(type(value) is int for value in saved[f'player{index}']))
        self.dialog.reload_drafts()
        self.assertEqual(scale.value(), round(window.width() * 100 / window.base[0], 2))
        self.assertEqual(self.dialog.dirty_sections(), [])
        self.assertTrue(self.dialog.close())
        self.question.assert_not_called()

    def test_program_exit_stops_if_a_settings_save_fails(self):
        self.main.settings_dialog = self.dialog
        self.main.show(); self.dialog.show()
        self.dialog.final_scores_hold.setValue(15)
        with patch('settings_window.SettingsWindow.ask_save_changes', return_value=QMessageBox.StandardButton.Yes), \
             patch.object(self.model.repository, 'save', side_effect=OSError('无法写入')):
            self.assertFalse(self.main.close())
        self.assertTrue(self.main.isVisible())
        self.assertFalse(self.manager.closing)

    def test_glossary_selection_is_clean_and_button_layout_saves_in_place(self):
        panel = self.main.broadcast_panel()
        if panel.terms.count() > 1:panel.terms.setCurrentIndex(1)
        self.assertNotIn('term', panel.dirty_sections())
        buttons = TermButtonDialog(panel); buttons.show()
        buttons.columns.setValue(4)
        self.assertTrue(buttons.apply(), buttons.error.text())
        self.assertTrue(buttons.isVisible())
        self.assertEqual(buttons.dirty_sections(), [])
        self.assertEqual(panel.config()['term_button_columns'], 4)
        buttons.close(); buttons.deleteLater()

    def test_sidebar_close_yes_saves_edited_preset(self):
        self.main.open_sidebar_presets(); presets = self.main.sidebar_presets_dialog
        identity = presets.entries[presets.list.currentRow()]['id']
        presets.title.setText('新侧栏名称')
        with patch('settings_window.SettingsWindow.ask_save_changes', return_value=QMessageBox.StandardButton.Yes):
            self.assertTrue(presets.close())
        stored = self.model.state['broadcast']['config']['sidebar_presets']
        self.assertEqual(next(p['title'] for p in stored if p['id'] == identity), '新侧栏名称')


if __name__ == '__main__':unittest.main()
