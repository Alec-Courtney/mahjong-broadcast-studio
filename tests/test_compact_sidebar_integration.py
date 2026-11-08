"""Compact content stays inside one fixed OBS sidebar envelope."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
from PySide6.QtCore import QRect,Qt
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QApplication
from model import Model
from overlays import OverlayManager
from qualification_presentation import qualification_panel_size
from sidebar_paging import sidebar_page_data
def sidebar_model(directory, people=16, teams=4):
    model=Model(directory)
    roster=[dict(id=f'compact-person-{i}',name=f'选手{i+1:02}',team=f'队伍{i%teams+1:02}',
                 team_id=f'compact-team-{i%teams}',color=('#377e72','#316e9c','#aa3e4b','#6a5b99')[i%4],
                 base_points=150-i*4.25,games=0,seed=i+1) for i in range(people)]
    model.configure_broadcast(dict(roster=roster,seats=[row['id'] for row in roster[:4]],
        event_mode='team',sidebar_mode='team',sidebar_scope='all',sidebar_auto_page=False,
        sidebar_page_size=10,settlement_enabled=False))
    model.settings.update(overlay_font_family='Microsoft YaHei',show_overlays=True)
    return model

APP=QApplication.instance() or QApplication([])


class CompactSidebarIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now=1000.
        self.clock=patch('overlays.time.monotonic',side_effect=lambda:self.now);self.clock.start()
        self.tmp=tempfile.TemporaryDirectory(prefix='tmp-sidebar-native-',dir=ROOT/'tests')
        self.path=Path(self.tmp.name)
        self.model=sidebar_model(self.path,people=24,teams=8)
        self.model.configure_sidebar({'sidebar_mode':'individual','sidebar_scope':'current'})
        self.manager=OverlayManager(self.model);self.manager.apply_scene('qualification')
        self.advance(1.)

    def tearDown(self):
        if self.manager:self.manager.close()
        APP.processEvents();self.clock.stop();self.tmp.cleanup()

    def advance(self,seconds):
        self.now+=seconds;self.manager.tick()

    def image(self,static=False):
        image=QImage(*self.manager.base_size('qualification'),QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent);p=QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing);p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        try:
            if static:self.manager.draw_content(p,'qualification',static=True)
            else:self.manager.paint_content(p,'qualification')
        finally:p.end()
        return image

    def assert_native_bounds(self,height,geometry=None):
        window=self.manager.windows['qualification']
        self.assertEqual(qualification_panel_size(self.model),(400,height))
        self.assertEqual(self.manager.base_size('qualification'),(400,height))
        self.assertEqual(window.base,(400,height))
        if geometry is not None:self.assertEqual(window.geometry(),QRect(*geometry))
        return window

    def restart(self):
        self.manager.close();self.manager=None;APP.processEvents()
        self.model=Model(self.path);self.manager=OverlayManager(self.model);self.manager.on_display()

    def test_sidebar_scope_and_content_switches_keep_window_and_font_scale(self):
        window=self.assert_native_bounds(650);window.setGeometry(47,91,400,650)
        baseline=copy.deepcopy({key:self.model.state[key] for key in ('players','rules','pot')})
        history=copy.deepcopy((self.model.undo,self.model.redo))
        self.model.configure_sidebar({'sidebar_scope':'all','sidebar_page_size':4})
        self.assert_native_bounds(650,(47,91,400,650))
        self.assertEqual((window.width()/window.base[0],window.height()/window.base[1]),(1.,1.))
        self.model.configure_sidebar({'sidebar_page':5})
        self.assert_native_bounds(650,(47,91,400,650))
        self.model.configure_sidebar({'sidebar_scope':'current'})
        self.assert_native_bounds(650,(47,91,400,650))
        for mode in ('team','qualification','individual'):
            self.manager.set_sidebar_mode(mode)
            self.assert_native_bounds(650,(47,91,400,650))
        self.assertEqual((window.width()/window.base[0],window.height()/window.base[1]),(1.,1.))
        self.assertEqual({key:self.model.state[key] for key in baseline},baseline)
        self.assertEqual((len(self.model.undo),len(self.model.redo)),tuple(map(len,history)))

    def test_actual_auto_pagination_does_not_shrink_on_four_row_last_page(self):
        self.model.configure_sidebar({'sidebar_scope':'all','sidebar_page_size':10,
                                     'sidebar_auto_page':True,'sidebar_page_interval':5})
        window=self.assert_native_bounds(650);geometry=window.geometry()
        for page in (1,2):
            self.now=self.manager._sidebar_deadline;self.manager.tick();self.advance(.51)
            self.assertEqual(self.manager.current_sidebar_page(),page)
            self.assertEqual(window.geometry(),geometry);self.assert_native_bounds(650)
        self.assertEqual(len(sidebar_page_data(self.model,'individual',page=2)['rows']),4)
        self.assertEqual(self.image().height(),650)

    def test_svg_exports_keep_fixed_viewbox_for_current_and_paginated_content(self):
        for scope,expected in (('current','0 0 400 650'),('all','0 0 400 650')):
            with self.subTest(scope=scope):
                self.model.configure_sidebar({'sidebar_scope':scope,'sidebar_page_size':4})
                target=self.path/(scope+'.svg');self.manager.export_svg('qualification',target)
                self.assertEqual(ET.parse(target).getroot().get('viewBox'),expected)

    def test_completed_live_layers_and_static_frame_have_identical_pixels(self):
        for mode in ('individual','team'):
            with self.subTest(mode=mode):
                self.model.configure_sidebar({'sidebar_mode':mode,'sidebar_scope':'current'})
                self.advance(1.)
                live=self.image();static=self.image(static=True)
                self.assertEqual((live.width(),live.height()),(400,650))
                self.assertEqual(bytes(live.constBits()),bytes(static.constBits()))
                layers=self.manager.extension_cache[('qualification','sidebar_layers',0)]
                self.assertTrue(all((image.width(),image.height())==(400,650) for image in layers.values()))
                self.assertEqual(live.pixelColor(100,500).alpha(),0)
                self.assertEqual(bytes(layers['highlight'].constBits()),bytes(layers['highlight'].sizeInBytes()))

    def test_manual_fixed_geometry_is_preserved_after_two_restarts(self):
        self.manager.windows['qualification'].setGeometry(51,83,360,585)
        self.manager.save_geometry()
        for _ in range(2):
            self.restart();self.assert_native_bounds(650,(51,83,360,585))
            self.assertEqual(self.model.settings['qualification_sidebar_base_height'],650)

    def test_legacy_tall_sidebar_is_kept_at_its_saved_size(self):
        self.manager.close();self.manager=None;APP.processEvents()
        self.model.settings['windows']['qualification']=[53,89,400,650]
        self.model.settings['qualification_geometry_mode']='sidebar'
        self.model.settings['qualification_geometries']={'sidebar':[53,89,400,650]}
        self.model.settings.pop('qualification_sidebar_base_height',None);self.model.save()
        self.model=Model(self.path);self.manager=OverlayManager(self.model)
        self.assert_native_bounds(650,(53,89,400,650))
        self.assertEqual(self.model.settings['qualification_sidebar_base_height'],650)
        self.restart();self.assert_native_bounds(650,(53,89,400,650))

    def test_four_conditions_use_fullscreen_without_rearranging_sidebar(self):
        self.manager.windows['qualification'].setGeometry(59,87,400,650)
        self.manager.save_geometry()
        self.model.configure_broadcast({'sidebar_mode':'qualification','qualification_layout':'four'})
        self.manager.show_conditions()
        self.assertEqual(self.manager.fullscreen_panel(),'four_conditions')
        self.assert_native_bounds(650,(59,87,400,650))
        self.model.configure_sidebar({'sidebar_mode':'individual','sidebar_scope':'current'})
        self.assert_native_bounds(650,(59,87,400,650))
        self.model.configure_sidebar({'sidebar_scope':'all'})
        self.assert_native_bounds(650,(59,87,400,650))

    def test_legacy_compact_height_expands_once_without_changing_font_scale(self):
        self.manager.close();self.manager=None;APP.processEvents()
        self.model.settings['windows']['qualification']=[53,89,400,306]
        self.model.settings.update(qualification_geometry_mode='sidebar',qualification_sidebar_base_height=306)
        self.model.settings['qualification_geometries']={'sidebar':[53,89,400,306]}
        self.model.settings.pop('sidebar_fixed_canvas_version',None);self.model.save()
        self.model=Model(self.path);self.manager=OverlayManager(self.model)
        self.assert_native_bounds(650,(53,89,400,650))
        self.restart();self.assert_native_bounds(650,(53,89,400,650))

    def test_old_wide_condition_geometry_uses_the_saved_vertical_sidebar(self):
        self.manager.close();self.manager=None;APP.processEvents()
        self.model.settings['windows']['qualification']=[60,204,1800,588]
        self.model.settings.update(qualification_geometry_mode='four',qualification_sidebar_base_height=650)
        self.model.settings['qualification_geometries']={'four':[60,204,1800,588],'sidebar':[53,253,427,693]}
        self.model.settings.pop('sidebar_fixed_canvas_version',None);self.model.save()
        self.model=Model(self.path);self.manager=OverlayManager(self.model)
        self.assert_native_bounds(650,(53,253,427,693))
        self.restart();self.assert_native_bounds(650,(53,253,427,693))


if __name__=='__main__':unittest.main()
