"""The glossary reflows between broadcast neighbours without scaling its text."""
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QRect,Qt
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QApplication
from broadcast_artwork import commentary_layout
from model import Model
from overlays import OverlayManager

APP=QApplication.instance() or QApplication([])
BODY='曾经舍弃过自己正在等待的牌，或者错过了可以荣和的机会，此时不能荣和他人的舍牌，但仍然可以通过自摸和牌。'


class CommentaryReflowTests(unittest.TestCase):
    def setUp(self):
        self.now=1000.;self.clock=patch('overlays.time.monotonic',side_effect=lambda:self.now);self.clock.start()
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.model=Model(self.tmp.name)
        self.model.configure_commentary(dict(commentary_title='振听',commentary_body=BODY,commentary_builtin=False,commentary_duration=0))
        self.manager=OverlayManager(self.model)
        self.manager.set_extension_visible('table_scores',False)
        self.manager.windows['round'].setGeometry(40,8,619,121)
        self.manager.windows['corner_header'].setGeometry(1350,24,540,220)
        self.manager.set_extension_visible('corner_header',True)
        self.manager.center_commentary(QRect(0,0,1920,1080))

    def tearDown(self):
        self.manager.close();APP.processEvents();self.tmp.cleanup();self.clock.stop()

    def test_actual_gap_is_667_pixels_and_all_copy_wraps_at_24_pixels(self):
        window=self.manager.windows['commentary'];geometry=window.geometry()
        self.assertEqual(geometry.width(),667)
        self.assertFalse(geometry.intersects(self.manager.windows['round'].geometry()))
        self.assertFalse(geometry.intersects(self.manager.windows['corner_header'].geometry()))
        self.assertEqual(geometry.left()-659,1350-(geometry.x()+geometry.width()))
        self.assertEqual(window.base,(window.width(),window.height()))
        layout=commentary_layout(self.model,window.base[0]);wide=commentary_layout(self.model,1100)
        self.assertGreater(layout['body_lines'],wide['body_lines'])
        self.assertEqual(layout['body_font'].pixelSize(),24)
        self.assertEqual(layout['body_scale'],1.)
        self.assertEqual(sum(layout['body_layout'].lineAt(i).textLength() for i in range(layout['body_lines'])),len(BODY))
        self.assertGreaterEqual(window.height(),layout['height'])
        image=QImage(*window.base,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
        painter=QPainter(image)
        try:self.manager.draw_content(painter,'commentary',static=True)
        finally:painter.end()
        self.assertGreater(image.pixelColor(image.width()-2,image.height()-2).alpha(),0)

    def test_long_copy_adds_rows_and_height_without_reducing_font(self):
        short=commentary_layout(self.model,667)
        self.model.configure_commentary({'commentary_body':BODY*4})
        layout=commentary_layout(self.model,667)
        self.assertGreater(layout['body_lines'],2)
        self.assertGreater(layout['height'],short['height'])
        self.assertEqual(layout['body_font'].pixelSize(),short['body_font'].pixelSize())
        self.assertEqual(self.manager.windows['commentary'].height(),layout['height'])

    def test_custom_position_and_width_survive_early_reflow_and_restart(self):
        self.model.settings.update(commentary_layout_version=5,commentary_auto=False)
        self.model.settings['windows']['commentary']=[210,82,440,80]
        self.manager.restore_geometry()
        window=self.manager.windows['commentary']
        self.assertEqual((window.x(),window.y(),window.width()),(210,82,440))
        self.assertGreaterEqual(window.height(),commentary_layout(self.model,440)['height'])
        self.assertEqual(window.base,(window.width(),window.height()))
        expected=window.geometry();self.manager.save_geometry();self.manager.restore_geometry()
        self.assertEqual(window.geometry(),expected)
        self.assertFalse(self.model.settings['commentary_auto'])
        self.assertEqual(self.model.settings['commentary_layout_version'],6)

    def test_high_dpi_uses_physical_width_and_font_without_double_scaling(self):
        window=self.manager.windows['commentary'];window.setGeometry(210,82,444,30)
        with patch('capture_surface.screen_pixel_ratio',return_value=1.5):
            self.manager._sync_commentary_geometry()
            self.assertEqual(window.base[0],666)
            self.assertEqual(window.base[1],round(window.height()*1.5))
            self.assertEqual(window.height(),math.ceil(commentary_layout(self.model,666)['height']/1.5))
            self.assertEqual(commentary_layout(self.model,window.base[0])['body_font'].pixelSize(),24)

    def test_resize_freezes_outgoing_width_then_animates_reflowed_copy(self):
        self.manager.set_extension_visible('commentary',True)
        self.now+=2.;self.manager.tick()
        old=self.manager._commentary_data
        self.assertEqual(old['size'][0],667)
        window=self.manager.windows['commentary'];window.resize(440,window.height())
        self.manager.on_display()
        self.assertIs(self.manager._commentary_data,old)
        self.assertEqual(self.manager._commentary_pending['size'][0],440)
        self.now+=.71;self.manager.tick()
        self.assertEqual(self.manager._commentary_data['size'][0],440)
        self.assertTrue(self.manager._commentary_motion.target)
        self.assertEqual(window.base[0],440)

    def test_failed_auto_placement_restores_native_canvas_and_previous_geometry(self):
        window=self.manager.windows['commentary'];geometry=window.geometry();base=window.base
        with patch.object(self.model.repository,'save',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):self.manager.center_commentary(QRect(0,0,1200,675))
        self.assertEqual(window.geometry(),geometry)
        self.assertEqual(window.base,base)
        self.assertEqual(window.base,(window.width(),window.height()))


if __name__=='__main__':unittest.main()
