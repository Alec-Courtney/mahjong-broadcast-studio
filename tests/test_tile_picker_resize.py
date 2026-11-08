"""The fixed four suit rows fit the picker board on both resize axes."""
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QPoint,QRect,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from console import Console,picker_tile_icon
from model import Model
from overlays import OverlayManager
try:
    from .ui_test_support import settle
except ImportError:
    from ui_test_support import settle

APP=QApplication.instance() or QApplication([])


class TilePickerResizeTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.cache').mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='tile-picker-resize-',dir=ROOT/'.cache')
        self.model=Model(self.tmp.name);self.model.settings.update(show_overlays=False,topmost=False)
        self.manager=OverlayManager(self.model);self.ui=Console(self.model,self.manager)
        self.ui.resize(1460,1000);self.ui.show();settle(APP)
        self.dock=self.ui.workspace.docks['tiles'];self.dock.setFloating(True);self.dock.show()
        self.scroll=self.ui.workspace.scrolls['tiles']

    def tearDown(self):
        self.ui.close();self.ui.deleteLater();APP.processEvents();self.tmp.cleanup()

    def resize_picker(self,width,height):
        self.dock.resize(width,height);settle(APP)
        return self.ui.tile_buttons['1m'].size()

    def assert_all_faces_fit(self):
        board=self.ui.picker_board;viewport=self.scroll.viewport()
        self.assertEqual(self.scroll.horizontalScrollBar().maximum(),0)
        self.assertEqual(self.scroll.verticalScrollBar().maximum(),0)
        self.assertEqual(len(self.ui.tile_buttons),37)
        for key in self.ui.picker_target_keys:
            control=self.ui.target_buttons[key]
            if not control.isVisible():continue
            self.assertGreaterEqual(control.width(),control.minimumSizeHint().width())
            self.assertTrue(viewport.rect().contains(QRect(control.mapTo(viewport,QPoint()),control.size())))
        first=self.ui.tile_buttons['1m'];scale=min(board.width()/460,board.height()/264)
        self.assertAlmostEqual(first.width(),46*scale,delta=1)
        self.assertAlmostEqual(first.height(),66*scale,delta=1)
        for row,codes in enumerate(self.ui.picker_rows):
            for column,code in enumerate(codes):
                control=self.ui.tile_buttons[code]
                with self.subTest(tile=code):
                    self.assertEqual(self.ui.picker_grid.getItemPosition(self.ui.picker_grid.indexOf(control))[:2],(row,column))
                    self.assertEqual(control.size(),first.size())
                    self.assertEqual(control.iconSize(),control.size())
                    self.assertTrue(board.rect().contains(control.geometry()))
                    self.assertTrue(viewport.rect().contains(QRect(control.mapTo(viewport,QPoint()),control.size())))
                    self.assertTrue(control.isVisible());self.assertTrue(control.toolTip())

    def test_width_and_height_each_constrain_all_four_suit_rows(self):
        for font in (14,24):
            self.model.setting('console_font_size',font);settle(APP)
            large=self.resize_picker(600,500);self.assert_all_faces_fit()
            narrow=self.resize_picker(260,500);self.assert_all_faces_fit()
            short=self.resize_picker(600,220);self.assert_all_faces_fit()
            self.assertLess(narrow.width(),large.width())
            self.assertLess(short.height(),large.height())
            self.assertEqual(self.dock.height(),220)
            self.resize_picker(260,220);self.assert_all_faces_fit()

    def test_docked_palette_keeps_every_face_visible_at_small_and_large_desks(self):
        self.dock.setFloating(False)
        for font in (14,24):
            self.model.setting('console_font_size',font)
            for size in ((960,620),(1460,1000)):
                self.ui.resize(*size);settle(APP);self.assert_all_faces_fit()

    def test_tile_targets_still_receive_real_clicks_after_shrinking(self):
        self.resize_picker(300,250)
        self.ui.focus_tile_target('dora')
        QTest.mouseClick(self.ui.tile_buttons['4m'],Qt.MouseButton.LeftButton)
        self.assertEqual(self.model.dora_tiles[-1],'4m')
        self.ui.focus_tile_target('waits')
        QTest.mouseClick(self.ui.tile_buttons['0p'],Qt.MouseButton.LeftButton)
        self.assertEqual(self.ui.wait_draft()['waits'],['5p'])
        self.ui.focus_tile_target('win_tile')
        QTest.mouseClick(self.ui.tile_buttons['0p'],Qt.MouseButton.LeftButton)
        self.assertEqual(self.ui.win_tile.text(),'0p')
        self.ui.focus_tile_target('ura')
        QTest.mouseClick(self.ui.tile_buttons['1z'],Qt.MouseButton.LeftButton)
        self.assertEqual(self.ui.ura_indicators.text(),'1z')
        self.ui.focus_tile_target('meld')
        QTest.mouseClick(self.ui.tile_buttons['5m'],Qt.MouseButton.LeftButton)
        self.assertIn('5m',self.ui.draft)

    def test_large_faces_use_cached_svg_resolution_instead_of_upscaled_small_icons(self):
        self.resize_picker(1500,1000);self.assert_all_faces_fit()
        first=self.ui.tile_buttons['1m'];native=first.devicePixelRatioF()
        self.assertGreater(first.width()*native,100)
        icon_size=first.icon().availableSizes()[0]
        self.assertGreaterEqual(icon_size.width(),math.ceil(first.width()*native))
        self.assertGreaterEqual(icon_size.height(),math.ceil(first.height()*native))
        self.resize_picker(600,500);before=picker_tile_icon.cache_info().misses
        self.resize_picker(620,500)
        self.assertEqual(picker_tile_icon.cache_info().misses,before)


if __name__=='__main__':unittest.main()
