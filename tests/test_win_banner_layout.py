"""Full-width win capture spacing, aspect and compatible geometry restoration."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QRectF,Qt
from PySide6.QtGui import QColor,QImage,QPainter
from PySide6.QtWidgets import QApplication
from artwork import Artwork,Animation,SIZES
from model import Model
from overlays import OverlayManager
from win_layout import restore_win_geometry

APP=QApplication.instance() or QApplication([])


class WinBannerLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='tmp-win-layout-',dir=ROOT/'tests')
        self.model=Model(self.temp.name);self.model.settings['show_overlays']=False
        self.model.settle(dict(type='ron',winner=0,loser=1,han=3,fu=40),dict(winner=0,name='选手一',han=3,fu=40,yakuman=0,yaku=['立直','断幺九','宝牌 1']))
        self.manager=OverlayManager(self.model)

    def tearDown(self):
        self.manager.close();APP.processEvents();self.temp.cleanup()

    def test_full_width_canvas_keeps_portrait_size_and_adds_transparent_sides(self):
        art=Artwork(self.model,Animation());portrait=[]
        image=QImage(*SIZES['yaku'],QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
        def paint(painter,model,profile,bounds,**kwargs):
            portrait.append(painter.worldTransform().mapRect(bounds));painter.fillRect(bounds,QColor('#ff00ff'))
        painter=QPainter(image)
        try:
            with patch('artwork.paint_identity_media',side_effect=paint):art.draw(painter,'yaku',now=art.created_at+30)
        finally:painter.end()
        self.assertEqual(image.size().toTuple(),(1920,220))
        self.assertEqual(portrait,[QRectF(50,0,134,200)])
        for x in range(50):self.assertTrue(all(image.pixelColor(x,y).alpha()==0 for y in range(220)))
        for x in range(1880,1920):self.assertTrue(all(image.pixelColor(x,y).alpha()==0 for y in range(220)))
        self.assertEqual(image.pixelColor(50,100),QColor('#ff00ff'))
        self.assertEqual(image.pixelColor(183,100),QColor('#ff00ff'))

    def test_default_window_fits_full_hd_and_old_default_migrates_to_it(self):
        self.model.settings['windows']['yaku']=[40,840,1840,220]
        self.model.settings.pop('win_banner_canvas_version',None)
        self.manager.restore_geometry()
        self.assertEqual(self.manager.windows['yaku'].geometry().getRect(),(0,840,1920,220))
        self.manager.save_geometry();reopened=Model(self.model.path)
        self.assertEqual(reopened.settings['windows']['yaku'],[0,840,1920,220])
        self.assertEqual(reopened.settings['win_banner_canvas_version'],2)

    def test_custom_width_and_bottom_edge_survive_one_time_aspect_migration(self):
        default=[0,840,1920,220];legacy=[40,840,1840,220]
        geometry=restore_win_geometry([31,1120,2480,297],default,legacy,0)
        self.assertEqual(geometry,[31,1133,2480,284])
        self.assertEqual(geometry[1]+geometry[3],1417)
        self.assertEqual(restore_win_geometry(geometry,default,legacy,2),geometry)

    def test_previous_full_width_banner_preserves_bottom_and_width_during_upgrade(self):
        default=[0,840,1920,220];legacy=[40,840,1840,220]
        for saved,expected in (([0,840,1920,220],[0,840,1920,220]),
                               ([30,700,1440,220],[30,755,1440,165])):
            self.assertEqual(restore_win_geometry(saved,default,legacy,1),expected)
            self.assertEqual(restore_win_geometry(expected,default,legacy,2),expected)

    def test_saving_reopening_and_replaying_does_not_change_scores_or_window(self):
        window=self.manager.windows['yaku'];window.setGeometry(30,700,1440,165)
        handle=int(window.winId());state,undo=copy.deepcopy((self.model.state,self.model.undo))
        self.manager.save_geometry();self.manager.restore_geometry();self.manager.on_display()
        self.assertEqual(window.geometry().getRect(),(30,700,1440,165))
        self.assertEqual(int(window.winId()),handle)
        self.assertEqual((self.model.state,self.model.undo),(state,undo))


if __name__=='__main__':unittest.main()
