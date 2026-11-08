"""A held four-seat drag keeps its physical size after a monitor DPI change."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QEvent,QPoint,QPointF,Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
from model import Model
from overlays import OverlayManager

APP=QApplication.instance() or QApplication([])


def mouse_event(kind,position,button,buttons):
    return QMouseEvent(kind,QPointF(20,20),QPointF(position),button,buttons,Qt.KeyboardModifier.NoModifier)


class CaptureDragDpiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.model=Model(self.tmp.name)
        self.manager=OverlayManager(self.model)

    def tearDown(self):
        self.manager.close();APP.processEvents();self.tmp.cleanup()

    def test_held_drag_rebases_after_100_to_150_percent_and_persists_new_scale(self):
        capture=self.manager.players_capture
        for index in range(4):self.assertEqual(self.manager.windows[f'player{index}'].width(),438)
        capture.mousePressEvent(mouse_event(QEvent.Type.MouseButtonPress,QPoint(300,720),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton))
        self.assertIsNotNone(capture.drag)
        old_snapshot=capture.drag[1]
        with patch('capture_surface.screen_pixel_ratio',return_value=1.5),patch('overlays.screen_pixel_ratio',return_value=1.5),patch('overlays.QCursor.pos',return_value=QPoint(400,730)):
            self.manager._capture_dpi_changed(capture,1.,1.5)
            resized=self.manager.player_group_snapshot()
            self.assertEqual(capture.drag[0],QPoint(400,730))
            self.assertIsNot(capture.drag[1],old_snapshot)
            for geometry in resized.values():self.assertEqual(geometry[2],292)
            capture.mouseMoveEvent(mouse_event(QEvent.Type.MouseMove,QPoint(450,750),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton))
            continued=self.manager.player_group_snapshot()
            for key,(x,y,width,height) in resized.items():
                self.assertEqual(continued[key],[x+50,y+20,width,height])
                self.assertEqual(continued[key][2]*1.5,438)
            capture.mouseReleaseEvent(mouse_event(QEvent.Type.MouseButtonRelease,QPoint(450,750),Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton))
            self.assertIsNone(capture.drag)
            self.manager.close()
            loaded=Model(self.tmp.name);self.manager=OverlayManager(loaded)
            self.assertEqual(self.manager.player_group_snapshot(),continued)
            self.assertEqual(self.manager.capture_output_size(self.manager.players_capture),(1920,370))


if __name__=='__main__':unittest.main()
