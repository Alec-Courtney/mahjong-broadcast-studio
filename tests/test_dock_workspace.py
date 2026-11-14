"""Critical native workspace behavior, using disposable sessions only."""
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
from PySide6.QtCore import QEvent,QPoint,QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDockWidget,QMainWindow,QRubberBand
from console import Console
from glossary import get_term
from docking import visible_geometry
from model import Model, YAKU
from overlays import OverlayManager
from term_buttons import TermButtonDialog

APP = QApplication.instance() or QApplication([])


class DockWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tests')
        self.model = Model(self.temp.name)
        self.model.settings['show_overlays'] = False
        self.ui = Console(self.model, OverlayManager(self.model))
        self.ui.resize(1460, 1000)
        self.ui.show()
        APP.processEvents()
        APP.processEvents()

    def tearDown(self):
        self.ui.close()
        self.ui.deleteLater()
        APP.processEvents()
        self.temp.cleanup()

    def native_move(self,dock,point,modifiers=Qt.KeyboardModifier.NoModifier):
        event=QMouseEvent(QEvent.Type.MouseMove,QPointF(dock.mapFromGlobal(point)),QPointF(point),
                          Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,modifiers)
        APP.sendEvent(dock,event)

    def native_drag(self,key,modifiers=Qt.KeyboardModifier.NoModifier):
        dock=self.ui.workspace.docks[key];title=dock.titleBarWidget();point=QPoint(20,title.height()//2)
        origin=title.mapToGlobal(point)
        QTest.mousePress(title,Qt.MouseButton.LeftButton,modifiers,pos=point)
        self.ui.workspace._drag_timer.stop()
        self.assertEqual(self.ui.workspace._drag_source,key)
        self.native_move(dock,origin+QPoint(QApplication.startDragDistance()+12,0),modifiers)
        self.assertTrue(dock.isFloating())
        return dock

    def native_release(self,dock,point,modifiers=Qt.KeyboardModifier.NoModifier):
        QTest.mouseRelease(dock,Qt.MouseButton.LeftButton,modifiers,pos=dock.mapFromGlobal(point))
        APP.processEvents()

    def test_closed_floating_history_reopens_from_the_top_toolbar(self):
        workspace = self.ui.workspace
        dock = workspace.docks['history']
        control = workspace.panel_buttons['history']
        before = copy.deepcopy(self.model.state)
        self.assertEqual(control.text(), '历史局收支')
        dock.setFloating(True)
        APP.processEvents()
        self.assertTrue(control.isChecked())
        dock.close()
        APP.processEvents()
        self.assertFalse(control.isChecked())
        self.assertTrue(dock.isHidden())
        control.click()
        APP.processEvents()
        self.assertTrue(dock.isVisible())
        self.assertTrue(dock.isFloating())
        self.assertTrue(control.isChecked())
        self.assertEqual(self.model.state, before)

    def test_native_edge_preview_and_docking_work_without_using_an_arrow(self):
        workspace=self.ui.workspace;before=copy.deepcopy(self.model.state)
        original=workspace.docks['dora'].geometry()
        with patch.object(workspace,'move_panel',wraps=workspace.move_panel) as assisted:
            dock=self.native_drag('dora')
            point=self.ui.mapToGlobal(QPoint(self.ui.width()//2,self.ui.statusBar().y()-4))
            self.native_move(dock,point)
            preview=self.ui.findChild(QRubberBand,'qt_rubberband')
            self.assertIsNotNone(preview);self.assertTrue(preview.isVisible())
            self.assertNotEqual(preview.geometry(),original)
            self.assertFalse(any(overlay.isVisible() for overlay in workspace.drop_overlays.values()))
            self.native_release(dock,point)
            self.assertFalse(dock.isFloating());assisted.assert_not_called()
        self.assertEqual(self.model.state,before)

    def test_native_drag_can_still_float_outside_the_console(self):
        workspace=self.ui.workspace
        with patch.object(workspace,'move_panel',wraps=workspace.move_panel) as assisted:
            dock=self.native_drag('dora');point=self.ui.mapToGlobal(QPoint(self.ui.width()+100,self.ui.height()//2))
            self.native_move(dock,point);self.native_release(dock,point)
            self.assertTrue(dock.isFloating());assisted.assert_not_called()
        self.assertIsNone(workspace._drag_source);self.assertFalse(workspace._drag_timer.isActive())

    def test_control_drag_keeps_native_floating_and_suppresses_arrows(self):
        workspace=self.ui.workspace;tiles=workspace.docks['tiles']
        try:
            with patch.object(workspace,'move_panel',wraps=workspace.move_panel) as assisted:
                dock=self.native_drag('dora',Qt.KeyboardModifier.ControlModifier)
                point=tiles.mapToGlobal(tiles.rect().center())
                self.native_move(dock,point,Qt.KeyboardModifier.ControlModifier)
                self.assertFalse(any(overlay.isVisible() for overlay in workspace.drop_overlays.values()))
                self.native_release(dock,point,Qt.KeyboardModifier.ControlModifier)
                self.assertTrue(dock.isFloating());assisted.assert_not_called()
        finally:QTest.keyRelease(self.ui,Qt.Key.Key_Control)

    def test_layout_round_trip_hidden_floating_and_lock_do_not_change_match(self):
        workspace = self.ui.workspace
        before = copy.deepcopy(self.model.state)
        self.assertTrue({'players', 'round', 'dora', 'waits', 'score', 'meld',
                         'yaku', 'quick', 'tiles', 'commentary'} <= workspace.docks.keys())
        self.assertNotIn('yakuman', workspace.docks)
        dock = workspace.docks['meld']
        dock.setFloating(True)
        dock.resize(420, 310)
        workspace.docks['dora'].close()
        self.ui.set_yakuman_expanded(True)
        workspace.set_locked(True)
        workspace.save()
        saved = copy.deepcopy(self.model.settings['console_layout'])
        workspace.reset_layout(save=False)
        self.model.settings['console_layout'] = saved
        workspace.restore()
        self.assertTrue(dock.isFloating())
        self.assertTrue(workspace.docks['dora'].isHidden())
        self.assertTrue(self.ui.yakuman_toggle.isChecked())
        self.assertFalse(dock.features() & QDockWidget.DockWidgetFeature.DockWidgetMovable)
        workspace.show_panel('dora')
        self.assertFalse(workspace.docks['dora'].isHidden())
        self.assertEqual(self.model.state, before)
        screen = QRect(0, 0, 1920, 1040)
        self.assertTrue(screen.contains(visible_geometry(QRect(9000, -9000, 450, 300), [screen])))

    def test_visual_drop_places_wide_yaku_only_below_the_tile_panel(self):
        workspace=self.ui.workspace;tiles=workspace.docks['tiles'];yaku=workspace.docks['yaku']
        # Leave the bottom area free for this deliberately wide drag source.
        workspace.docks['history'].hide()
        self.ui.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea,yaku);APP.processEvents()
        self.assertGreater(yaku.width(),tiles.width())
        before=copy.deepcopy(self.model.state);self.native_drag('yaku')
        self.native_move(yaku,tiles.mapToGlobal(tiles.rect().center()))
        overlay=workspace.drop_overlays['tiles'];self.assertTrue(overlay.isVisible())
        self.assertEqual(overlay.grab().toImage().pixelColor(3,3).alpha(),0)
        point=overlay.mapToGlobal(overlay.zones()['below'].center());self.native_move(yaku,point)
        self.assertEqual(overlay.edge,'below')
        self.assertTrue(overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.native_release(yaku,point)
        self.assertEqual(yaku.x(),tiles.x());self.assertEqual(yaku.width(),tiles.width())
        self.assertGreater(yaku.y(),tiles.geometry().bottom())
        self.assertFalse(self.ui.dockOptions() & QMainWindow.DockOption.AllowTabbedDocks)
        self.assertTrue(all(not self.ui.tabifiedDockWidgets(dock) for dock in workspace.docks.values()))
        self.assertEqual(self.model.state,before)

    def test_left_drop_creates_an_adjacent_panel_and_cancel_preserves_layout(self):
        workspace=self.ui.workspace;before={key:dock.geometry() for key,dock in workspace.docks.items()}
        workspace.begin_drag('dora');workspace._drag_timer.stop()
        self.assertFalse(any(overlay.isVisible() for overlay in workspace.drop_overlays.values()))
        workspace._update_drag_target(workspace.docks['tiles'].mapToGlobal(workspace.docks['tiles'].rect().center()))
        self.assertTrue(workspace.drop_overlays['tiles'].isVisible())
        workspace.end_drag();self.assertEqual({key:dock.geometry() for key,dock in workspace.docks.items()},before)
        self.assertTrue(workspace.move_panel('dora','tiles','left'));APP.processEvents()
        self.assertLess(workspace.docks['dora'].geometry().right(),workspace.docks['tiles'].x())
        self.assertEqual(workspace.docks['dora'].y(),workspace.docks['tiles'].y())
        self.assertGreater(workspace.docks['yaku'].width(),workspace.docks['tiles'].width())
        workspace.set_locked(True);geometry=workspace.docks['dora'].geometry()
        self.assertFalse(workspace.move_panel('dora','tiles','below'))
        self.assertEqual(workspace.docks['dora'].geometry(),geometry)

    def test_drag_guides_only_show_for_the_target_entered_at_its_centre(self):
        workspace=self.ui.workspace;tiles=workspace.docks['tiles'];score=workspace.docks['score']
        before={key:dock.geometry() for key,dock in workspace.docks.items()}
        shown=lambda:{key for key,overlay in workspace.drop_overlays.items() if overlay.isVisible()}
        workspace.begin_drag('yaku');workspace._drag_timer.stop();self.assertEqual(shown(),set())
        workspace._update_drag_target(tiles.mapToGlobal(QPoint(3,tiles.height()//2)))
        self.assertEqual(shown(),set())
        workspace._update_drag_target(tiles.mapToGlobal(tiles.rect().center()))
        self.assertEqual(shown(),{'tiles'})
        workspace._update_drag_target(tiles.mapToGlobal(QPoint(tiles.width()//2,tiles.height()-4)))
        self.assertEqual(shown(),{'tiles'})
        workspace._update_drag_target(score.mapToGlobal(score.rect().center()))
        self.assertEqual(shown(),{'score'})
        workspace._update_drag_target(self.ui.mapToGlobal(QPoint(-20,-20)))
        self.assertEqual(shown(),set());workspace.end_drag()
        self.assertEqual({key:dock.geometry() for key,dock in workspace.docks.items()},before)

    def test_toolbar_switches_and_saved_v3_tabs_are_restored_without_merging(self):
        workspace=self.ui.workspace;button=workspace.panel_buttons['dora']
        button.click();self.assertTrue(workspace.docks['dora'].isHidden())
        button.click();self.assertFalse(workspace.docks['dora'].isHidden())
        self.ui.tabifyDockWidget(workspace.docks['round'],workspace.docks['dora'])
        self.model.settings['console_layout']=dict(version=3,state=bytes(self.ui.saveState(3).toBase64()).decode('ascii'),
            geometry=bytes(self.ui.saveGeometry().toBase64()).decode('ascii'))
        workspace.reset_layout(save=False);workspace.restore();APP.processEvents()
        self.assertTrue(all(not self.ui.tabifiedDockWidgets(dock) for dock in workspace.docks.values()))
        self.assertEqual(set(workspace.panel_buttons),set(workspace.docks))

    def test_yakuman_fold_retains_choices_and_does_not_hide_ordinary_yaku(self):
        ordinary = self.ui.yaku_buttons[YAKU[0][0]]
        yakuman = self.ui.yaku_buttons[YAKU[30][0]]
        before = copy.deepcopy(self.model.state)
        self.assertFalse(self.ui.yakuman_toggle.isChecked())
        ordinary.click()
        self.ui.yakuman_toggle.click()
        yakuman.click()
        self.ui.yakuman_toggle.click()
        self.assertTrue(self.ui.yakuman_body.isHidden())
        self.assertTrue(ordinary.isVisible())
        self.assertTrue(ordinary.isChecked())
        self.assertTrue(yakuman.isChecked())
        self.ui.yakuman_toggle.click()
        self.assertTrue(yakuman.isVisible())
        self.assertEqual(self.model.state, before)
        self.assertEqual(self.model.undo, [])

    def test_legacy_tab_layout_migrates_without_hiding_live_controls(self):
        workspace = self.ui.workspace
        before = copy.deepcopy(self.model.state)
        self.ui.tabifyDockWidget(workspace.docks['round'], workspace.docks['dora'])
        self.ui.tabifyDockWidget(workspace.docks['score'], workspace.docks['meld'])
        self.model.settings['console_layout'] = dict(version=1, locked=True,
            state=bytes(self.ui.saveState(1).toBase64()).decode('ascii'),
            geometry=bytes(self.ui.saveGeometry().toBase64()).decode('ascii'))
        workspace.reset_layout(save=False)
        workspace.restore()
        for key in ('players', 'round', 'dora', 'waits', 'score', 'meld', 'yaku'):
            self.assertFalse(workspace.docks[key].isHidden(), key)
            self.assertEqual(self.ui.tabifiedDockWidgets(workspace.docks[key]), [], key)
        self.assertTrue(workspace._locked)
        workspace.save()
        self.assertEqual(self.model.settings['console_layout']['version'], workspace.VERSION)
        self.assertEqual(self.model.state, before)

    def test_navigation_controls_wrap_and_remain_inside_every_viewport(self):
        from sidebar_presets import sidebar_presets
        presets = copy.deepcopy(sidebar_presets(self.model))
        presets[0]['title'] = 'W' * 80
        self.ui.manager.save_sidebar_presets(presets)
        self.ui.quick_postmatch_status.setText('Playback preparation: ' + 'review_audio_' * 6 + '.wav')
        nav = self.ui.navigation_scroll
        controls = [self.ui.navigation_buttons['settings'], self.ui.quick_scene,
                    self.ui.quick_bottom, self.ui.quick_table_scores,
                    self.ui.quick_conditions, self.ui.quick_corner, self.ui.show_melds,
                    self.ui.font_smaller_button,
                    self.ui.font_larger_button, self.ui.player_animation_button,
                    self.ui.corner_bottom_text,
                    self.ui.corner_apply_button, self.ui.corner_logo_button,
                    *self.ui.sidebar_preset_buttons.values()]
        self.assertTrue(self.ui.show_yaku.isHidden())
        self.assertFalse(self.ui.presentation_group.isAncestorOf(self.ui.show_yaku))
        for width, height in ((960, 620), (1460, 1000)):
            self.ui.resize(width, height)
            QTest.qWait(20)
            APP.processEvents()
            self.assertEqual(nav.width(), 142)
            self.assertEqual(nav.horizontalScrollBar().maximum(), 0)
            self.assertLessEqual(nav.widget().width(), nav.viewport().width())
            for group in (self.ui.presentation_group, self.ui.corner_group, self.ui.sidebar_presets_group):
                point = group.mapTo(nav.viewport(), group.rect().topLeft())
                self.assertGreaterEqual(point.x(), 0)
                self.assertLessEqual(point.x() + group.width(), nav.viewport().width())
            for control in controls:
                with self.subTest(size=(width, height), control=control.objectName() or type(control).__name__):
                    nav.ensureWidgetVisible(control, 0, 4)
                    APP.processEvents()
                    # Editors expose an internal focus rectangle to Qt.  Also
                    # scroll their full frame into view, as the operator can.
                    for _ in range(2):
                        top=control.mapTo(nav.viewport(),QPoint(0,0)).y()
                        bottom=top+control.height();bar=nav.verticalScrollBar()
                        if top<0:bar.setValue(bar.value()+top)
                        elif bottom>nav.viewport().height():bar.setValue(bar.value()+bottom-nav.viewport().height())
                        APP.processEvents()
                    original = control.rect()
                    remaining = QRect(original)
                    child = control
                    while child is not self.ui:
                        parent = child.parentWidget()
                        self.assertIsNotNone(parent)
                        remaining.translate(child.pos())
                        remaining = remaining.intersected(parent.rect())
                        child = parent
                    self.assertTrue(control.isVisible())
                    self.assertEqual(remaining.size(), original.size())
                    if control.hasHeightForWidth():
                        self.assertGreaterEqual(control.height(), control.heightForWidth(control.width()))

    def test_navigation_inner_handle_resizes_without_resizing_the_window(self):
        nav = self.ui.navigation_scroll
        handle = self.ui.navigation_handle
        workspace = self.ui.workspace
        before = copy.deepcopy(self.model.state)
        outer = QRect(self.ui.frameGeometry())
        sidebar_left = nav.mapTo(self.ui, QPoint()).x()
        old_right = handle.mapTo(self.ui, QPoint()).x()
        self.assertEqual(handle.cursor().shape(), Qt.CursorShape.SplitHCursor)
        self.assertEqual(sidebar_left + nav.width(), old_right)

        self.drag_navigation(80)

        self.assertEqual(nav.width(), 222)
        self.assertEqual(nav.mapTo(self.ui, QPoint()).x(), sidebar_left)
        self.assertEqual(handle.mapTo(self.ui, QPoint()).x(), old_right + 80)
        self.assertEqual(self.ui.frameGeometry(), outer)
        self.assertEqual(nav.horizontalScrollBar().maximum(), 0)
        self.assertTrue(workspace.save())
        self.assertEqual(Model(self.model.path).settings['console_layout']['navigation_width'], 222)
        self.assertEqual(self.model.state, before)
        self.assertEqual(self.model.undo, [])

        workspace.reset_layout(save=False)
        self.assertEqual(nav.width(), 142)
        workspace.restore()
        self.ui.resize(1460, 1000)
        APP.processEvents()
        self.assertEqual(nav.width(), 222)

    def drag_navigation(self, distance):
        handle = self.ui.navigation_handle
        start = handle.rect().center()
        destination = handle.mapToGlobal(start) + QPoint(distance, 0)
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
        move = QMouseEvent(QEvent.Type.MouseMove, QPointF(handle.mapFromGlobal(destination)),
                           QPointF(destination), Qt.MouseButton.NoButton,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        APP.sendEvent(handle, move)
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.mapFromGlobal(destination))
        APP.processEvents()

    def test_floating_panel_shortcuts_work_and_editor_keeps_native_input(self):
        dock = self.ui.workspace.docks['players']
        dock.setFloating(True)
        dock.show()
        dock.activateWindow()
        QApplication.setActiveWindow(dock)
        self.ui.player_widgets[0][1].setFocus()
        APP.processEvents()
        self.ui.shortcuts.sync_enabled()
        self.assertTrue(self.ui.shortcuts.can_trigger())
        QTest.keyClick(self.ui.player_widgets[0][1], Qt.Key.Key_2, Qt.KeyboardModifier.AltModifier)
        APP.processEvents()
        self.assertEqual(self.ui.selected, 1)
        score = self.ui.player_widgets[1][2]
        score.setFocus()
        APP.processEvents()
        self.assertFalse(self.ui.shortcuts.can_trigger())
        QTest.keyClick(score, Qt.Key.Key_R)
        self.assertFalse(self.model.state['players'][1]['riichi'])

    def test_tile_tray_reflows_when_detached_and_target_reveals_closed_panel(self):
        dock = self.ui.workspace.docks['tiles']
        dock.setFloating(True)
        dock.resize(420, 350)
        APP.processEvents()
        APP.processEvents()
        self.assertLess(self.ui._picker_columns, len(self.ui.picker_order))
        self.ui.workspace.docks['meld'].close()
        self.ui.focus_tile_target('meld')
        self.assertFalse(self.ui.workspace.docks['meld'].isHidden())
        self.assertTrue(self.ui.show_melds.isVisible())

    def test_term_slots_save_with_gaps_and_survive_table_undo(self):
        dock = self.ui.workspace.docks['commentary']
        dock.setFloating(True)
        self.assertTrue(dock.isFloating())
        self.assertFalse(self.ui.quick_bar.isAncestorOf(self.ui.quick_terms))
        self.model.field('honba', 1)
        panel = self.ui.broadcast_panel()
        current = panel.config().get('commentary_title')
        undo_count = len(self.model.undo)
        dialog = TermButtonDialog(panel)
        dialog.label.setText('快捷说明')
        dialog.title.setText('本场测试术语')
        dialog.content.setPlainText('按钮对应的独立词库正文。')
        dialog.destination.setCurrentIndex(8)
        dialog.move_slot()
        dialog.columns.setValue(2)
        dialog.apply()
        self.assertEqual(dialog.error.text(), '')
        buttons = panel.config()['term_buttons']
        custom = next(entry for entry in buttons if entry['slot'] == 8)
        self.assertEqual(custom['label'], '快捷说明')
        self.assertFalse(any(entry['slot'] == 0 for entry in buttons))
        self.assertEqual(panel.config().get('commentary_title'), current)
        self.assertEqual(len(self.model.undo), undo_count)
        restored = Model(self.temp.name)
        self.assertEqual(restored.state['broadcast']['config']['term_buttons'], buttons)
        self.model.history('undo')
        self.assertEqual(self.model.state['honba'], 0)
        self.assertEqual(panel.config()['term_buttons'], buttons)
        self.assertEqual(self.ui.quick_terms.buttons[custom['ref']].text(), '快捷说明')
        before = copy.deepcopy(self.model.state)
        cancelled = TermButtonDialog(panel)
        cancelled.clear_slot()
        cancelled.reject()
        self.assertEqual(self.model.state, before)
        dialog.deleteLater()
        cancelled.deleteLater()

    def test_existing_builtin_override_keeps_one_favorite_identity(self):
        panel = self.ui.broadcast_panel()
        title = get_term('one_chance')['zh_title']
        self.model.configure_commentary(dict(custom_terms=[[title, '自定义解释']],
             term_favorites=['custom:' + title], term_buttons=[dict(slot=4, ref='custom:' + title, label='')]))
        self.assertEqual(panel.favorite_refs(), ['one_chance'])
        self.assertEqual(panel.favorite_buttons()[0]['ref'], 'one_chance')
        panel.remove_favorite('one_chance')
        self.assertEqual(panel.config()['term_buttons'], [])
        self.assertEqual(panel.config()['term_favorites'], [])


if __name__ == '__main__':
    unittest.main()
