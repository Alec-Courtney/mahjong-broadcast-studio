"""Stable OBS identities and alpha frames, independent of live content visibility."""
import copy
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import Qt,QPoint,QPointF,QRect,QEvent,QObject
from PySide6.QtGui import QImage,QColor,QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QMessageBox
from console import Console
from model import Model
from overlays import OverlayManager
from settings_dialog import SettingsDialog
from obs_capture import scene_collection
from player_layout import restore_player_geometries
from i18n import set_language

APP=QApplication.instance() or QApplication([])


def alpha_pixels(window):
    image=window.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return sum(value>0 for value in bytes(image.constBits())[3::4])


class OBSCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='tmp-obs-capture-',dir=ROOT/'tests')
        self.model=Model(self.temp.name);self.model.settings['show_overlays']=False
        self.manager=OverlayManager(self.model)

    def tearDown(self):
        self.manager.close();APP.processEvents();self.temp.cleanup();set_language('zh_CN')

    def test_one_four_player_window_stays_visible_and_clears_when_disabled(self):
        group=self.manager.players_capture;handle=int(group.winId())
        self.assertTrue(group.isVisible())
        self.assertFalse(any(self.manager.windows[f'player{i}'].isVisible() for i in range(4)))
        self.assertEqual(alpha_pixels(group),0)
        self.model.setting('show_overlays',True)
        APP.processEvents();self.assertGreater(alpha_pixels(group),1000)
        self.manager.set_visible('players',False)
        APP.processEvents();self.assertEqual(alpha_pixels(group),0)
        self.assertTrue(group.isVisible());self.assertEqual(int(group.winId()),handle)
        self.manager.set_visible('players',True)
        APP.processEvents();self.assertGreater(alpha_pixels(group),1000)
        self.assertEqual(int(group.winId()),handle)

    def test_window_titles_and_handles_do_not_follow_language_or_scene(self):
        before={key:(window.windowTitle(),int(window.winId())) for key,window in self.manager.capture_windows.items()}
        self.manager.apply_scene('qualification')
        self.model.setting('language','ja_JP')
        self.manager.apply_scene('clean')
        self.assertEqual({key:(window.windowTitle(),int(window.winId())) for key,window in self.manager.capture_windows.items()},before)
        self.assertTrue(all(window.isVisible() for window in self.manager.capture_windows.values()))

    def test_settings_show_checkbox_previews_without_saving_and_close_restores_blank(self):
        main=Console(self.model,self.manager);dialog=SettingsDialog(main)
        try:
            dialog.show();dialog._go_to_page('播出窗口');APP.processEvents()
            show=next(row[1] for row in dialog.values if row[0]=='players')
            before=copy.deepcopy((self.model.state,self.model.settings))
            QTest.mouseClick(show,Qt.MouseButton.LeftButton,pos=QPoint(7,show.height()//2))
            self.assertTrue(self.manager.capture_suppressed('players'))
            QTest.mouseClick(show,Qt.MouseButton.LeftButton,pos=QPoint(7,show.height()//2))
            self.assertTrue(self.manager.capture_previewing('players'))
            self.assertGreater(alpha_pixels(self.manager.players_capture),1000)
            self.assertEqual((self.model.state,self.model.settings),before)
            self.assertEqual(dialog.dirty_sections(),[])
            with patch('settings_window.SettingsWindow.ask_save_changes',return_value=QMessageBox.StandardButton.No) as ask:
                dialog.close();ask.assert_not_called()
            self.assertEqual(alpha_pixels(self.manager.players_capture),0)
        finally:
            dialog._allow_close=True;dialog.close();dialog.deleteLater()
            main.workspace.dispose();main.shortcuts.dispose();main.language_controller.dispose();APP.removeEventFilter(main);main.hide();main.deleteLater();APP.processEvents()

    def test_export_has_one_player_source_and_correct_wgc_matching_settings(self):
        data=scene_collection(self.manager,identity=lambda w:w.windowTitle()+':QtWindow:python.exe')
        inputs=[source for source in data['sources'] if source['id']=='window_capture']
        self.assertEqual(len(inputs),len(self.manager.capture_windows))
        self.assertEqual([s['name'] for s in inputs].count('四家选手'),1)
        self.assertFalse(any(s['name'].startswith('选手 ') for s in inputs))
        self.assertEqual([s['name'] for s in inputs].count('全屏节目'),1)
        self.assertFalse({'赛前开场','赛后连播','立直特效'} & {s['name'] for s in inputs})
        self.assertFalse({'赛事排名','技术统计','赛后走势'} & {s['name'] for s in inputs})
        self.assertTrue({'四家选手','局况 · 宝牌','侧边展示栏','术语解释'} <= {s['name'] for s in inputs})
        self.assertTrue(all(s['settings']['method']==2 and s['settings']['priority']==1 for s in inputs))
        self.assertTrue(all(s['settings']['client_area'] and not s['settings']['cursor'] for s in inputs))
        self.assertEqual((data['base_res_cx'],data['base_res_cy']),(1920,1080))
        self.assertTrue(all(item['scale']==dict(x=.5,y=.5) if item['name']=='局况 · 宝牌' else item['scale']==dict(x=1.,y=1.) for item in data['sources'][-1]['settings']['items']))
        self.assertTrue(all(item['scale_filter']=='lanczos' for item in data['sources'][-1]['settings']['items']))

    def test_obs_export_places_sources_relative_to_their_own_monitor(self):
        def source(name, origin, offset, ratio):
            screen = SimpleNamespace(geometry=lambda: QRect(*origin, 1600, 900),
                                     devicePixelRatio=lambda: ratio)
            return SimpleNamespace(windowTitle=lambda: name, screen=lambda: screen,
                                   x=lambda: origin[0] + offset[0], y=lambda: origin[1] + offset[1])

        manager = SimpleNamespace(capture_windows={
            'primary': source('Primary', (0, 0), (80, 60), 1.),
            'second': source('Second', (-1920, -400), (100, 80), 1.5),
        })
        data = scene_collection(manager, identity=lambda window: window.windowTitle())
        items = data['sources'][-1]['settings']['items']
        self.assertEqual([item['pos'] for item in items], [dict(x=80, y=60), dict(x=150, y=120)])

    def test_player_drag_across_scaled_monitors_preserves_output_and_saves_position(self):
        def mouse_event(kind, x, y, button, buttons):
            return QMouseEvent(kind, QPointF(20, 20), QPointF(x, y), button, buttons,
                               Qt.KeyboardModifier.NoModifier)

        self.model.setting('show_overlays', True)
        capture = self.manager.players_capture
        size = self.manager.capture_output_size(capture)
        left, no_button = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
        capture.mousePressEvent(mouse_event(QEvent.Type.MouseButtonPress, 300, 720, left, left))
        with patch('capture_surface.screen_pixel_ratio', return_value=1.5), \
             patch('overlays.screen_pixel_ratio', return_value=1.5), \
             patch('overlays.QCursor.pos', return_value=QPoint(400, 730)):
            self.manager._capture_dpi_changed(capture, 1., 1.5)
            position = capture.pos()
            capture.mouseMoveEvent(mouse_event(QEvent.Type.MouseMove, 450, 750, no_button, left))
            capture.mouseReleaseEvent(mouse_event(QEvent.Type.MouseButtonRelease, 450, 750, left, no_button))
            self.assertEqual(capture.pos() - position, QPoint(50, 20))
            self.assertEqual(self.manager.capture_output_size(capture), size)
        seats = self.manager.player_group_snapshot()
        saved = Model(self.model.path).settings['windows']
        self.assertEqual({key: saved[key] for key in seats}, seats)

    def test_hud_capture_drag_updates_layout_without_saving_double_dimensions(self):
        capture=self.manager.capture_windows['round'];layout=self.manager.windows['round']
        old_size=layout.size();capture.move(80,90);APP.processEvents()
        self.assertEqual(layout.pos(),capture.pos());self.assertEqual(layout.size(),old_size)
        self.manager.save_geometry()
        self.assertEqual(self.model.settings['windows']['round'],[80,90,old_size.width(),old_size.height()])

    def test_hud_animation_ticks_repaint_the_actual_capture_surface(self):
        class PaintCounter(QObject):
            count=0
            def eventFilter(self,watched,event):
                if event.type()==QEvent.Type.Paint:self.count+=1
                return False
        self.model.setting('show_overlays',True);self.manager.set_visible('round',True)
        surface=self.manager.hud_capture;counter=PaintCounter();surface.installEventFilter(counter)
        APP.processEvents();counter.count=0
        self.manager.tick();APP.processEvents()
        self.assertGreater(counter.count,0)
        surface.removeEventFilter(counter)

    def test_broken_v1_row_is_repaired_and_saved_as_a_complete_group(self):
        self.model.settings['broadcast_canvas_version']=1
        self.model.settings['windows'].update({f'player{i}':geometry for i,geometry in enumerate([
            [350,883,438,340],[828,584,438,340],[1322,584,438,340],[1816,584,438,340]])})
        state,undo=copy.deepcopy((self.model.state,self.model.undo))
        self.manager.restore_geometry();self.manager.on_display()
        expected={f'player{i}':[334+494*i,584,438,348] for i in range(4)}
        self.assertEqual(self.manager.player_group_snapshot(),expected)
        self.assertEqual(self.manager.players_capture.height(),370)
        self.assertGreaterEqual(self.manager.players_capture.width(),1920)
        self.model.save()
        reopened=Model(self.model.path)
        self.assertEqual({key:reopened.settings['windows'][key] for key in expected},expected)
        self.assertEqual(reopened.settings['broadcast_canvas_version'],8)
        self.assertEqual((self.model.state,self.model.undo),(state,undo))

    def test_offscreen_player_group_moves_together(self):
        self.model.settings['broadcast_canvas_version']=2
        self.model.settings['windows'].update({f'player{i}':[-8000+430*i,-8000,400,310] for i in range(4)})
        self.manager.restore_geometry();self.manager.on_display()
        frames=self.manager.player_group_snapshot()
        self.assertEqual(len({frame[1] for frame in frames.values()}),1)
        self.assertEqual([frames[f'player{i}'][0]-frames['player0'][0] for i in range(4)],[0,430,860,1290])
        self.assertEqual(self.manager.players_capture.height(),338)
        self.assertGreaterEqual(self.manager.players_capture.width(),1690)

    def test_one_programme_capture_window_remains_available_when_not_playing(self):
        shared=self.manager.fullscreen_capture
        for player in (self.manager.opening,self.manager.postmatch):
            self.assertIs(player.window,shared)
            handle=int(player.window.winId())
            player.stop()
            self.manager.on_display();APP.processEvents()
            self.assertTrue(player.window.isVisible())
            self.assertEqual(alpha_pixels(player.window),0)
            self.assertEqual(int(player.window.winId()),handle)

    def test_hidden_content_is_transparent_even_with_chroma_background_enabled(self):
        self.model.setting('capture_bg','green');APP.processEvents()
        for key in ('players','round','fullscreen'):
            self.assertEqual(alpha_pixels(self.manager.capture_windows[key]),0,key)

    def test_opening_and_ending_replace_pixels_in_same_surface_without_changing_layout_or_scores(self):
        self.model.setting('show_overlays',True)
        # Removed single-window switches must not silently blank the shared output.
        self.model.settings['visible'].update(opening=False,postmatch=False)
        surface=self.manager.fullscreen_capture;handle=int(surface.winId())
        other_geometry={key:window.geometry() for key,window in self.manager.capture_windows.items() if key!='fullscreen'}
        state,undo=copy.deepcopy((self.model.state,self.model.undo))
        red=QImage(16,9,QImage.Format.Format_ARGB32_Premultiplied);red.fill(QColor('red'))
        blue=QImage(16,9,QImage.Format.Format_ARGB32_Premultiplied);blue.fill(QColor('blue'))
        with patch.object(self.manager.opening,'frame',return_value=red),patch.object(self.manager.postmatch,'frame',return_value=blue),patch.object(self.manager.postmatch,'opacity',return_value=1.):
            self.manager.opening.state='playing';self.manager.on_display()
            self.assertEqual(surface.grab().toImage().pixelColor(surface.rect().center()),QColor('red'))
            self.manager.opening.stop()
            self.assertEqual(alpha_pixels(surface),0)
            self.manager.postmatch.state='playing';self.manager.on_display()
            self.assertEqual(surface.grab().toImage().pixelColor(surface.rect().center()),QColor('blue'))
            self.model.setting('broadcast_scene','clean')
            self.assertEqual(surface.grab().toImage().pixelColor(surface.rect().center()),QColor('blue'))
            self.manager.set_visible('fullscreen',False)
            self.assertEqual(alpha_pixels(surface),0)
            self.manager.set_visible('fullscreen',True)
            self.assertEqual(surface.grab().toImage().pixelColor(surface.rect().center()),QColor('blue'))
            self.manager.postmatch.stop()
        self.assertEqual(alpha_pixels(surface),0)
        self.assertEqual(int(surface.winId()),handle)
        self.assertEqual({key:window.geometry() for key,window in self.manager.capture_windows.items() if key!='fullscreen'},other_geometry)
        self.assertEqual((self.model.state,self.model.undo),(state,undo))
        self.assertFalse(self.manager.windows['riichi_effect'].isVisible())

    def test_fullscreen_percent_position_is_shared_and_saved_without_moving_other_modules(self):
        main=Console(self.model,self.manager);dialog=SettingsDialog(main)
        try:
            before={key:window.geometry() for key,window in self.manager.capture_windows.items() if key!='fullscreen'}
            row=next(row for row in dialog.values if row[0]=='fullscreen')
            self.assertTrue(all(dialog.capture_rows[key][0].isHidden() for key in ('opening','postmatch','riichi_effect','chart','statistics')))
            row[2].setValue(50);row[3].setValue(60);row[4].setValue(50)
            self.assertTrue(dialog.apply(),dialog.error.text())
            expected=[50,60,960,540]
            self.assertEqual(self.model.settings['windows']['fullscreen'],expected)
            self.assertEqual(Model(self.model.path).settings['windows']['fullscreen'],expected)
            self.assertEqual(self.manager.opening.window.geometry(),self.manager.postmatch.window.geometry())
            self.assertEqual({key:window.geometry() for key,window in self.manager.capture_windows.items() if key!='fullscreen'},before)
            self.assertEqual(dialog.dirty_sections(),[])
        finally:
            dialog._allow_close=True;dialog.close();dialog.deleteLater()
            main.workspace.dispose();main.shortcuts.dispose();main.language_controller.dispose();APP.removeEventFilter(main);main.hide();main.deleteLater();APP.processEvents()

    def test_old_fullscreen_layout_migrates_without_moving_independent_modules(self):
        before=self.manager.windows['round'].geometry()
        self.manager.close()
        self.model.settings['windows'].pop('fullscreen',None)
        self.model.settings['windows']['opening']=[20,30,800,450]
        self.model.settings['windows']['postmatch']=[60,70,960,540]
        self.model.settings['windows']['qualification'] = [60, 204, 1800, 588]
        self.model.settings.update(qualification_geometry_mode='four',
                                   qualification_sidebar_base_height=650,
                                   qualification_geometries={'sidebar': [53, 89, 360, 585]})
        self.manager=OverlayManager(self.model)
        self.assertEqual(self.manager.fullscreen_capture.geometry().getRect(),(20,30,800,450))
        self.assertEqual(self.manager.windows['round'].geometry(),before)
        self.assertEqual(self.manager.windows['qualification'].geometry().getRect(), (53, 89, 360, 585))
        self.assertIs(self.manager.opening.window,self.manager.postmatch.window)

    def test_legacy_win_banner_keeps_its_saved_width_and_bottom_placement(self):
        self.model.settings['windows']['yaku'] = [31, 680, 1440, 220]
        self.model.settings['win_banner_canvas_version'] = 1
        self.manager.restore_geometry()
        banner = self.manager.windows['yaku']
        self.assertEqual((banner.x(), banner.width(), banner.y() + banner.height()), (31, 1440, 900))
        self.manager.save_geometry()
        self.assertEqual(Model(self.model.path).settings['windows']['yaku'], list(banner.geometry().getRect()))

    def test_each_event_restores_its_own_fullscreen_position_without_recreating_surface(self):
        from event_store import EventStore
        store=EventStore(self.model);first=self.model.event['id']
        surface=self.manager.fullscreen_capture;handle=int(surface.winId())
        sidebar=self.manager.windows['qualification'];sidebar_handle=int(sidebar.winId())
        sidebar.setGeometry(53,89,360,585)
        surface.setGeometry(20,30,800,450);surface.save_geometry()
        store.create('另一赛事','individual_league')
        sidebar.setGeometry(70,90,400,650);surface.setGeometry(70,80,960,540);surface.save_geometry()
        second=self.model.event['id']
        store.open(first)
        self.assertEqual(surface.geometry().getRect(),(20,30,800,450))
        self.assertEqual(sidebar.geometry().getRect(),(53,89,360,585))
        for mode in ('individual','qualification'):
            self.manager.set_sidebar_mode(mode)
            self.assertEqual(sidebar.geometry().getRect(),(53,89,360,585))
        store.open(second)
        self.assertEqual(surface.geometry().getRect(),(70,80,960,540))
        self.assertEqual(sidebar.geometry().getRect(),(70,90,400,650))
        self.assertEqual(int(sidebar.winId()),sidebar_handle)
        self.assertEqual(int(surface.winId()),handle)

    def test_standings_are_painted_inside_the_same_player_capture(self):
        players=[{**p,'team':f'队伍{i+1}','team_id':f'team{i}','base_points':(i-2)*20.5} for i,p in enumerate(self.model.state['players'])]
        self.model.configure_broadcast({'roster':players,'seats':[p['id'] for p in players]})
        self.model.setting('show_overlays',True)
        surface=self.manager.players_capture;handle=int(surface.winId());geometry=surface.geometry()
        scores=[p['score'] for p in self.model.state['players']]
        for mode in ('individual','team','players'):
            self.manager.set_bottom_mode(mode);self.manager.extension_transitions.clear();self.manager.on_display()
            self.assertGreater(alpha_pixels(surface),1000,mode)
            self.assertEqual(int(surface.winId()),handle);self.assertEqual(surface.geometry(),geometry)
            self.assertFalse(self.manager.windows['standings'].isVisible())
        self.assertEqual([p['score'] for p in self.model.state['players']],scores)
        self.assertNotIn('standings',self.manager.layout_windows)
        self.manager.set_bottom_mode('individual');self.manager.extension_transitions.clear()
        self.manager.set_visible('players',False);self.assertEqual(alpha_pixels(surface),0)
        self.manager.set_visible('players',True);self.assertGreater(alpha_pixels(surface),1000)

    def test_four_conditions_share_the_fullscreen_handle_without_resizing_sidebar(self):
        self.model.setting('show_overlays',True)
        self.manager.set_sidebar_mode('individual',True)
        sidebar=self.manager.windows['qualification'];sidebar.setGeometry(70,90,360,585)
        surface=self.manager.fullscreen_capture;surface.setGeometry(20,30,960,540)
        handle=int(surface.winId());sidebar_handle=int(sidebar.winId())
        before=copy.deepcopy((self.model.state,self.model.undo))
        for key in ('four_conditions','chart','statistics','four_conditions'):
            self.manager.set_extension_visible(key,True);self.manager.extension_transitions.clear();self.manager.on_display()
            self.assertEqual(self.manager.fullscreen_panel(),key)
            self.assertGreater(alpha_pixels(surface),1000)
            self.assertEqual(surface.geometry().getRect(),(20,30,960,540));self.assertEqual(int(surface.winId()),handle)
            self.assertEqual(sidebar.geometry().getRect(),(70,90,360,585));self.assertEqual(int(sidebar.winId()),sidebar_handle)
            self.assertFalse(self.manager.windows['four_conditions'].isVisible())
        self.manager.hide_conditions();self.manager.extension_transitions.clear();self.manager.on_display()
        self.assertEqual(alpha_pixels(surface),0)
        self.assertTrue(self.manager.enabled['qualification']);self.assertEqual(self.manager.sidebar_mode(),'individual')
        self.assertEqual((self.model.state,self.model.undo),before)

    def test_four_conditions_split_option_exports_one_optional_fullscreen_source(self):
        self.assertEqual(len(self.manager.capture_windows),8)
        self.assertNotIn('four_conditions',self.manager.capture_windows)
        self.model.setting('split_fullscreen_windows',True);self.model.setting('show_overlays',True)
        self.manager.show_conditions('four');self.manager.extension_transitions.clear();self.manager.on_display()
        window=self.manager.capture_windows['four_conditions']
        self.assertEqual(len(self.manager.capture_windows),13)
        self.assertEqual(window.geometry().getRect(),(0,0,1920,1080))
        self.assertGreater(alpha_pixels(window),1000)
        data=scene_collection(self.manager,identity=lambda w:w.windowTitle()+':QtWindow:python.exe')
        self.assertEqual(sum(source['name']=='四人并排条件' for source in data['sources']),1)
        self.assertNotIn('fullscreen',self.manager.capture_windows)

    def test_console_conditions_switch_routes_to_selected_surface(self):
        self.model.setting('show_overlays',True);self.manager.set_sidebar_mode('individual',True)
        main=Console(self.model,self.manager)
        try:
            main.quick_conditions.setChecked(True)
            self.assertTrue(self.manager.enabled['four_conditions']);self.assertEqual(self.manager.sidebar_mode(),'individual')
            main.quick_conditions.setChecked(False);self.assertFalse(self.manager.enabled['four_conditions'])
            self.assertTrue(self.manager.enabled['qualification'])
            self.model.configure_sidebar({'qualification_layout':'sidebar'})
            main.quick_conditions.setChecked(True)
            self.assertEqual(self.manager.sidebar_mode(),'qualification');self.assertTrue(self.manager.enabled['qualification'])
            self.assertFalse(self.manager.enabled['four_conditions'])
        finally:
            main.workspace.dispose();main.shortcuts.dispose();main.language_controller.dispose();APP.removeEventFilter(main);main.hide();main.deleteLater();APP.processEvents()

    def test_split_mode_is_saved_atomically_and_keeps_the_active_opening_clock(self):
        self.model.setting('show_overlays',True)
        surface=self.manager.fullscreen_capture;handle=int(surface.winId())
        self.manager.opening.state='playing';self.manager.opening._started=123.
        red=QImage(16,9,QImage.Format.Format_ARGB32_Premultiplied);red.fill(QColor('red'))
        main=Console(self.model,self.manager);dialog=SettingsDialog(main)
        try:
            with patch.object(self.manager.opening,'frame',return_value=red):
                dialog.split_fullscreen.setChecked(True)
                self.assertFalse(self.model.settings['split_fullscreen_windows'])
                with patch.object(self.model.repository,'save',side_effect=OSError('无法写入')):
                    self.assertFalse(dialog.apply())
                self.assertIs(self.manager.opening.window,surface)
                self.assertTrue(dialog.split_fullscreen.isChecked())
                self.assertTrue(dialog.apply(),dialog.error.text())
                self.assertTrue(Model(self.model.path).settings['split_fullscreen_windows'])
                self.assertNotIn('fullscreen',self.manager.capture_windows)
                self.assertTrue({'opening','postmatch','chart','statistics','riichi_effect'} <= self.manager.capture_windows.keys())
                individual=self.manager.opening.window
                self.assertIsNot(individual,surface);self.assertFalse(surface.isVisible())
                self.assertEqual(individual.grab().toImage().pixelColor(individual.rect().center()),QColor('red'))
                self.assertEqual(self.manager.opening._started,123.)
                dialog.split_fullscreen.setChecked(False)
                self.assertTrue(dialog.apply(),dialog.error.text())
                self.assertIs(self.manager.opening.window,surface)
                self.assertEqual(int(surface.winId()),handle)
                self.assertEqual(surface.grab().toImage().pixelColor(surface.rect().center()),QColor('red'))
                self.assertEqual(dialog.dirty_sections(),[])
        finally:
            self.manager.opening.stop()
            dialog._allow_close=True;dialog.close();dialog.deleteLater()
            main.workspace.dispose();main.shortcuts.dispose();main.language_controller.dispose();APP.removeEventFilter(main);main.hide();main.deleteLater();APP.processEvents()

    def test_split_panels_are_independent_while_rankings_stay_in_player_ui(self):
        self.model.setting('split_fullscreen_windows',True);self.model.setting('show_overlays',True)
        self.manager.set_extension_visible('chart',True);self.manager.set_extension_visible('statistics',True)
        self.assertTrue(self.manager.enabled['chart']);self.assertTrue(self.manager.enabled['statistics'])
        self.assertTrue(self.manager.windows['chart'].isVisible());self.assertTrue(self.manager.windows['statistics'].isVisible())
        self.assertFalse(self.manager.fullscreen_capture.isVisible())
        self.assertNotIn('standings',self.manager.capture_windows)
        data=scene_collection(self.manager,identity=lambda w:w.windowTitle()+':QtWindow:python.exe')
        names={s['name'] for s in data['sources'] if s['id']=='window_capture'}
        self.assertTrue({'赛前开场','赛后连播','赛后走势','技术统计','立直特效'} <= names)
        self.assertFalse({'全屏节目','赛事排名'} & names)


class PlayerGeometryMigrationTests(unittest.TestCase):
    def setUp(self):
        self.old={f'player{i}':[15+i*448,1009,438,340] for i in range(4)}
        self.new={f'player{i}':[i*494,710,438,340] for i in range(4)}

    def restore(self,saved,version):
        return restore_player_geometries(saved,self.old,self.new,version)

    def test_legacy_customization_preserves_implicit_neighbours(self):
        saved={'player0':[16,1009,438,340]}
        self.assertEqual(self.restore(saved,0),{**self.old,**saved})
        self.assertEqual(self.restore(self.old,0),self.new)

    def test_partial_v1_snapshot_joins_the_three_new_defaults(self):
        self.assertEqual(self.restore({'player0':[16,1009,438,340]},1),self.new)

    def test_intentional_layouts_are_preserved(self):
        grid={f'player{i}':[(i%2)*448,(i//2)*350,438,340] for i in range(4)}
        self.assertEqual(self.restore(grid,1),grid)
        custom={**self.new,'player0':[24,500,438,340]}
        self.assertEqual(self.restore(custom,2),custom)
        irregular={**self.new,'player0':[24,500,438,340],'player2':[1050,710,438,340]}
        self.assertEqual(self.restore(irregular,1),irregular)


if __name__=='__main__':unittest.main()
