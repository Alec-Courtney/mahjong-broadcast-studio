"""Small regression checks for settings drafts and the two operator palettes."""
import copy
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
from PySide6.QtWidgets import QApplication, QComboBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget
from PySide6.QtGui import QPalette, QWheelEvent
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from console import Console, LayoutDialog
from i18n import localize_widgets, set_language
from model import Model
from overlays import OverlayManager
from settings_window import SettingsWindow

APP = QApplication.instance() or QApplication([])


class SettingsInputTests(unittest.TestCase):
    def test_wheel_scrolls_settings_without_editing_and_still_adjusts_live_inputs(self):
        def wheel(widget, delta):
            local = widget.rect().center()
            event = QWheelEvent(QPointF(local), QPointF(widget.mapToGlobal(local)),
                                QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            APP.sendEvent(widget, event)

        settings = SettingsWindow()
        desk = QWidget()
        desk.setProperty('liveDeskInputs', True)
        try:
            settings.resize(360, 240)
            body = QWidget()
            body.setMinimumHeight(600)
            fields = QVBoxLayout(body)
            spin = QSpinBox()
            spin.setValue(5)
            combo = QComboBox()
            combo.addItems(['甲', '乙'])
            fields.addWidget(spin)
            fields.addWidget(combo)
            fields.addStretch()
            scroll = QScrollArea()
            scroll.setWidget(body)
            QVBoxLayout(settings).addWidget(scroll)
            settings.show()
            APP.processEvents()
            spin.setFocus()
            wheel(spin, 120)
            wheel(combo, -120)
            self.assertEqual((spin.value(), combo.currentIndex()), (5, 0))
            scroll.verticalScrollBar().setValue(0)
            wheel(scroll.viewport(), -120)
            self.assertGreater(scroll.verticalScrollBar().value(), 0)
            live_spin = QSpinBox(desk)
            live_spin.setValue(5)
            wheel(live_spin, 120)
            self.assertEqual(live_spin.value(), 6)
        finally:
            settings.deleteLater()
            desk.deleteLater()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.close_answer = patch('settings_window.SettingsWindow.ask_save_changes', return_value=QMessageBox.StandardButton.No)
        self.close_answer.start();self.addCleanup(self.close_answer.stop)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'tests')
        self.model = Model(self.tmp.name)
        self.model.settings['show_overlays'] = False
        self.manager = OverlayManager(self.model)
        self.ui = Console(self.model, self.manager)
        self.dialog = LayoutDialog(self.ui)

    def tearDown(self):
        self.dialog.deleteLater()
        self.ui.workspace.dispose()
        self.ui.shortcuts.dispose()
        self.ui.language_controller.dispose()
        APP.removeEventFilter(self.ui)
        self.ui.hide()
        self.ui.deleteLater()
        self.manager.close()
        APP.processEvents()
        set_language('zh_CN')
        self.tmp.cleanup()

    def test_cancel_preserves_theme_geometry_and_match(self):
        before = copy.deepcopy((self.model.state, self.model.settings))
        self.dialog.theme.setCurrentIndex(1)
        self.dialog.show_melds.setChecked(False)
        self.dialog.reset_windows.setChecked(True)
        self.dialog.reject()
        self.assertEqual(before, (self.model.state, self.model.settings))

    def test_theme_and_meld_save_do_not_enter_score_undo(self):
        state = copy.deepcopy(self.model.state)
        undo = copy.deepcopy(self.model.undo)
        self.dialog.theme.setCurrentIndex(1)
        self.dialog.show_melds.setChecked(False)
        self.dialog.apply()
        self.assertEqual(self.model.state, state)
        self.assertEqual(self.model.undo, undo)
        self.assertEqual(self.model.settings['console_theme'], 'light')
        self.assertFalse(self.model.settings['show_melds'])
        for surface in (self.ui, self.ui.broadcast_panel()):
            self.assertGreater(surface.palette().color(QPalette.ColorRole.Window).lightness(), 220)
            self.assertLess(surface.palette().color(QPalette.ColorRole.WindowText).lightness(), 90)
        self.assertEqual(Model(self.tmp.name).settings['console_theme'], 'light')

    def test_search_navigates_and_handles_no_matches(self):
        self.dialog.search.setText('字体')
        self.assertEqual(self.dialog.nav.currentItem().text(0), '字体与字号')
        self.dialog.search.setText('not-a-setting')
        self.assertIs(self.dialog.pages.currentWidget(), self.dialog.empty_page)
        self.dialog.search.clear()
        self.assertIsNot(self.dialog.pages.currentWidget(), self.dialog.empty_page)

    def test_constructing_settings_never_shows_temporary_top_level_controls(self):
        class WindowShows(QObject):
            def __init__(self):
                super().__init__()
                self.widgets = []

            def eventFilter(self, widget, event):
                if event.type() == QEvent.Type.Show and callable(getattr(widget, 'isWindow', None)) and widget.isWindow():
                    self.widgets.append(widget)
                return False

        shows = WindowShows()
        APP.installEventFilter(shows)
        editor = None
        try:
            sample = Path(self.tmp.name) / 'recognition-sample.png'
            sample.touch()
            with patch.dict('recognition_settings._OFFLINE_SAMPLES',
                            {'image': ('图片样例', sample, (0, 0, 1, 1))}, clear=True):
                editor = LayoutDialog(self.ui)
            self.assertEqual(shows.widgets, [])
            editor.show()
            APP.processEvents()
            self.assertEqual(shows.widgets, [editor])
            self.assertTrue(all(widget.window() is editor for widgets in editor.capture_rows.values() for widget in widgets))
        finally:
            APP.removeEventFilter(shows)
            if editor is not None:
                editor._allow_close = True
                editor.close()
                editor.deleteLater()
            APP.processEvents()

    def test_sidebar_labels_translate_without_translating_profiles(self):
        set_language('ja_JP')
        localize_widgets(self.dialog)
        localize_widgets(self.ui.broadcast_panel())
        self.assertEqual(self.dialog.groups[0].text(0), '基本設定')
        self.assertEqual(self.dialog.theme.itemText(1), 'ライト')
        self.assertEqual(self.ui.broadcast_panel().tabs.tabText(0), '放送設定')
        set_language('zh_CN')
        localize_widgets(self.dialog)
        self.assertEqual(self.dialog.groups[0].text(0), '常用设置')

    def test_card_style_edits_and_reset_are_drafts_until_save(self):
        before = copy.deepcopy((self.model.state, self.model.settings, self.model.undo))
        editor = self.dialog.card_editor
        editor.font_size.setValue(21)
        editor.spacing.setValue(2)
        editor.text_paint.mode.setCurrentIndex(1)
        editor.text_paint.start.set_color('#80ff8800')
        editor.text_paint.start.changed.emit()
        editor.gap.setValue(10)
        editor.background_paint.start_alpha.setValue(35)
        self.assertEqual(editor.value()['backgrounds']['score']['start'][:3], '#59')
        self.assertTrue(editor.background_paint.start_alpha.isEnabled())
        ml = editor.value('mleague')
        editor.editing_style.setCurrentIndex(1)
        editor.font_size.setValue(18)
        editor.reset_defaults()
        self.assertEqual(editor.value('mleague'), ml)
        self.assertEqual(editor.value('saikouisen')['text']['team']['size'], 14)
        self.dialog.reject()
        self.assertEqual(before, (self.model.state, self.model.settings, self.model.undo))

    def test_both_card_styles_save_independently_and_selected_style_applies_on_save(self):
        editor = self.dialog.card_editor
        editor.font_size.setValue(21)
        editor.gap.setValue(9)
        editor.editing_style.setCurrentIndex(1)
        editor.font_size.setValue(17)
        editor.gap.setValue(4)
        self.assertEqual(self.dialog.player_style.currentData(), 'saikouisen')
        self.assertEqual(self.model.settings['theme'], 'mleague')
        state, undo = copy.deepcopy(self.model.state), copy.deepcopy(self.model.undo)
        self.dialog.apply()
        self.assertEqual(self.dialog.error.text(), '')
        self.assertEqual(self.model.settings['theme'], 'saikouisen')
        self.assertEqual(self.model.settings['player_card_style']['text']['team']['size'], 21)
        self.assertEqual(self.model.settings['saikouisen_card_style']['text']['team']['size'], 17)
        self.assertEqual(self.model.state, state)
        self.assertEqual(self.model.undo, undo)
        restored = Model(self.tmp.name).settings
        self.assertEqual(restored['theme'], 'saikouisen')
        self.assertEqual(restored['player_card_style'], editor.value('mleague'))
        self.assertEqual(restored['saikouisen_card_style'], editor.value('saikouisen'))

    def test_style_only_selection_is_dirty_persistent_and_renders_on_live_capture(self):
        from PySide6.QtGui import QImage, QPainter
        import time
        editor = self.dialog.card_editor
        editor.editing_style.setCurrentIndex(1)
        self.assertIn('settings', self.dialog.dirty_sections())
        self.assertEqual(self.model.settings['theme'], 'mleague')
        self.assertTrue(self.dialog.apply(), self.dialog.error.text())
        self.assertEqual(self.model.settings['theme'], 'saikouisen')
        self.assertEqual(self.model.settings['player_style_version'], 1)
        self.assertEqual(self.dialog.dirty_sections(), [])
        image = QImage(500, 500, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        try:
            with patch('legacy_player_artwork.draw_player') as legacy:
                self.manager.art.player(painter, 0, time.monotonic())
                self.assertTrue(legacy.called)
        finally:painter.end()
        self.assertEqual(Model(self.tmp.name).settings['theme'], 'saikouisen')
        self.dialog.reload_drafts()
        self.assertEqual(editor.editing_style.currentData(), 'saikouisen')
        self.dialog.player_style.setCurrentIndex(0)
        self.assertEqual(editor.editing_style.currentData(), 'mleague')
        self.assertTrue(self.dialog.apply())
        self.assertEqual(Model(self.tmp.name).settings['theme'], 'mleague')

    def test_incomplete_color_input_stays_editable_and_blocks_save(self):
        editor = self.dialog.card_editor
        before = copy.deepcopy(self.model.settings)
        editor.text_paint.start.edit.setText('#12')
        editor.font_size.setValue(24)
        self.dialog.apply()
        self.assertIn('色号', self.dialog.error.text())
        self.assertEqual(editor.text_paint.start.edit.text(), '#12')
        self.assertEqual(self.model.settings, before)


if __name__ == '__main__':
    unittest.main()
