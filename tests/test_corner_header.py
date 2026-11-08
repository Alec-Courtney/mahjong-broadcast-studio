"""Portable event branding, score-history isolation and real transparency."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage,QPainter,QColor
from PySide6.QtWidgets import QApplication,QWidget
from model import Model
from overlays import OverlayManager
from broadcast_console import BroadcastDialog
from corner_header import CORNER_SIZE,corner_logo,draw_corner_header
from corner_editor import CornerHeaderDialog

APP=QApplication.instance() or QApplication([])


class CornerHeaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.directory=Path(self.tmp.name)
        self.model=Model(self.directory/'first');self.manager=None

    def tearDown(self):
        if self.manager:self.manager.close()
        APP.processEvents();self.tmp.cleanup()

    def logo(self):
        image=QImage(160,100,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
        p=QPainter(image);p.fillRect(20,10,120,80,QColor('#16c2ac'));p.end()
        path=self.directory/'own-logo.png';image.save(str(path));return path

    def test_custom_logo_survives_export_import_without_original_path(self):
        path=self.logo();self.model.configure_corner_header({'corner_logo':str(path),'corner_season':'27–28','corner_date':'Nov. 03','corner_match':'12'})
        config=self.model.state['broadcast']['config'];self.assertTrue(config['corner_logo'].startswith('assets/imported/logos/'))
        transfer=self.directory/'transfer.json';self.model.export_data(transfer);path.unlink()
        target=Model(self.directory/'second');target.import_data(transfer)
        self.assertEqual(target.state['broadcast']['config']['corner_season'],'27–28')
        image=corner_logo(target,target.state['broadcast']['config']['corner_logo'])
        self.assertEqual((image.width(),image.height()),(120,80));self.assertEqual(image.pixelColor(10,10).name(),'#16c2ac')

    def test_branding_does_not_consume_undo_and_failed_save_is_atomic(self):
        self.model.set_score(0,26000);self.model.history('undo')
        lengths=(len(self.model.undo),len(self.model.redo));last=copy.deepcopy(self.model.last_transition)
        self.model.configure_corner_header({'corner_season':'SEASON 27','corner_match':'2'})
        self.assertEqual((len(self.model.undo),len(self.model.redo)),lengths);self.assertEqual(self.model.last_transition,last)
        self.model.history('redo');self.assertEqual(self.model.state['broadcast']['config']['corner_season'],'SEASON 27')
        before=copy.deepcopy((self.model.state,self.model.undo,self.model.redo))
        with patch.object(self.model.repository,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.model.configure_corner_header({'corner_season':'failed'})
        self.assertEqual((self.model.state,self.model.undo,self.model.redo),before)

    def test_selected_test_logo_and_canvas_keep_transparent_background(self):
        self.assertIsNone(corner_logo(self.model))
        self.model.configure_corner_header({'corner_logo':str(self.logo())})
        self.assertIsNotNone(corner_logo(self.model,self.model.state['broadcast']['config']['corner_logo']))
        image=QImage(*CORNER_SIZE,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
        p=QPainter(image);draw_corner_header(p,self.model);p.end()
        self.assertEqual(image.pixelColor(0,0).alpha(),0);self.assertEqual(image.pixelColor(539,219).alpha(),0)
        opaque=sum(image.pixelColor(x,y).alpha()>0 for x in range(0,540,3) for y in range(0,220,3))
        self.assertGreater(opaque,800);self.assertLess(opaque,6000)

    def test_operator_edits_apply_to_transparent_window(self):
        self.model.settings['show_overlays']=False;self.manager=OverlayManager(self.model)
        parent=QWidget();parent.model=self.model;parent.manager=self.manager;dialog=CornerHeaderDialog(parent)
        try:
            dialog.top_text.setText('28–29');dialog.bottom_text.setText('Jan. 02');dialog.match.setText('3')
            dialog.apply(show=True)
            self.assertEqual(self.model.state['broadcast']['config']['corner_top_text'],'28–29')
            self.assertEqual(self.model.state['broadcast']['config']['corner_match'],'3')
            self.assertTrue(self.manager.enabled['corner_header'])
            self.assertEqual(self.manager.windows['corner_header'].base,CORNER_SIZE)
            self.assertTrue(self.manager.windows['corner_header'].testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        finally:
            dialog.hide();dialog.deleteLater();parent.deleteLater();APP.processEvents()


if __name__=='__main__':unittest.main()
