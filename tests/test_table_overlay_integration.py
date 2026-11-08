"""Native-window layout, title drafts and pure condition broadcast acceptance."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget
from broadcast_console import BroadcastDialog
from event_metadata import new_event
from event_workspace import ColumnDialog, NodeDialog
from model import Model
from overlays import OverlayManager

APP = QApplication.instance() or QApplication([])


class TableOverlayIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'tests')
        self.model = Model(self.tmp.name)
        self.model.settings['show_overlays'] = False
        self.manager = OverlayManager(self.model)

    def tearDown(self):
        self.manager.close()
        APP.processEvents()
        self.tmp.cleanup()

    def test_two_condition_layouts_preserve_dragged_geometry_across_reopen(self):
        four = QRect(71, 188, 960, 540)
        side = QRect(112, 222, 300, 488)
        self.manager.fullscreen_capture.setGeometry(four)
        self.manager.save_geometry()
        self.model.configure_broadcast({'qualification_layout': 'sidebar'})
        self.manager.windows['qualification'].setGeometry(side)
        self.manager.save_geometry()
        self.model.configure_broadcast({'qualification_layout': 'four'})
        self.assertEqual(self.manager.windows['qualification'].geometry(), side)
        self.assertEqual(self.manager.fullscreen_capture.geometry(), four)
        self.assertEqual(self.manager.windows['qualification'].base, (400,650))
        self.manager.save_geometry()
        self.manager.restore_geometry()
        self.assertEqual(self.manager.fullscreen_capture.geometry(), four)
        self.assertEqual(self.manager.windows['qualification'].geometry(), side)
        self.model.configure_broadcast({'sidebar_mode': 'individual'})
        self.assertEqual(self.manager.windows['qualification'].geometry(), side)
        self.assertEqual(self.manager.windows['qualification'].base, (400, 650))

    def test_old_sidebar_position_does_not_make_four_cards_a_tall_narrow_strip(self):
        settings = self.model.settings
        settings['windows']['qualification'] = [75, 90, 300, 488]
        settings.pop('qualification_geometry_mode', None)
        settings.pop('qualification_geometries', None)
        self.manager.restore_geometry()
        window = self.manager.windows['qualification']
        self.assertEqual(window.geometry(),QRect(75,90,300,488))
        self.manager.show_conditions('four')
        self.assertEqual(self.manager.fullscreen_panel(),None)  # global output is disabled in this fixture
        self.assertTrue(self.manager.enabled['four_conditions'])
        self.assertEqual(self.manager.fullscreen_capture.width()*9,self.manager.fullscreen_capture.height()*16)
        self.model.configure_broadcast({'qualification_layout': 'sidebar'})
        self.assertEqual(window.geometry(), QRect(75, 90, 300, 488))

    def test_score_strip_drag_save_reopen_and_scene_visibility(self):
        self.manager.apply_scene('qualification')
        self.assertTrue(self.manager.enabled['table_scores'])
        geometry = QRect(230, 142, 574, 84)
        self.manager.windows['table_scores'].setGeometry(geometry)
        self.manager.save_geometry()
        self.manager.restore_geometry()
        self.assertEqual(self.manager.windows['table_scores'].geometry(), geometry)
        self.manager.apply_scene('clean')
        self.assertFalse(self.manager.enabled['table_scores'])
        self.manager.apply_scene('table')
        self.assertTrue(self.manager.enabled['table_scores'])
        self.assertEqual(self.manager.windows['table_scores'].geometry(), geometry)

    def test_auto_caption_avoids_score_strip_and_keeps_manual_caption(self):
        self.manager.enabled['table_scores'] = True
        self.manager.windows['round'].setGeometry(40, 8, 619, 121)
        self.manager.windows['table_scores'].setGeometry(671, 8, 640, 94)
        caption = self.manager.center_commentary(QRect(0, 0, 1920, 1080))
        self.assertFalse(caption.intersects(self.manager.windows['table_scores'].geometry()))
        self.assertFalse(caption.intersects(self.manager.windows['round'].geometry()))
        self.manager.windows['commentary'].setGeometry(44, 275, 800, 96)
        self.manager.save_geometry()
        self.manager.on_display()
        self.assertEqual(self.manager.windows['commentary'].geometry(), QRect(44, 275, 800, 132))

    def test_title_controls_are_drafts_until_apply_and_all_four_persist(self):
        parent = QWidget();parent.model = self.model;parent.manager = self.manager
        panel = BroadcastDialog(parent)
        before = copy.deepcopy(self.model.state)
        try:
            panel.qualification_title.setText('统一晋级条件')
            panel.manual_conditions.item(0, 2).setText('冠军条件')
            panel.manual_conditions.item(1, 2).setText('半决赛晋级条件')
            self.assertEqual(self.model.state, before)
            panel.save_qualification()
            loaded = Model(self.tmp.name)
            config = loaded.state['broadcast']['config']
            self.assertEqual(config['qualification_title'], '统一晋级条件')
            self.assertEqual(config['qualification_titles']['p1'], '冠军条件')
            self.assertEqual(config['qualification_titles']['p2'], '半决赛晋级条件')
        finally:
            panel.hide();panel.deleteLater();parent.deleteLater();APP.processEvents()

    def test_pure_condition_cards_do_not_draw_rank_pt_or_explanatory_notes(self):
        import broadcast_artwork
        drawn = []
        original = QPainter.drawText
        def record(painter, *args):
            drawn.extend(value for value in args if isinstance(value,str))
            return original(painter, *args)
        image = QImage(1920,1080, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            with patch.object(QPainter, 'drawText', new=record):
                broadcast_artwork.draw_extension(painter, 'four_conditions', self.model)
        finally:painter.end()
        self.assertFalse(any(any(token in text for token in ('pt', '当前试算', '目标前', '最低档', '本局结束试算')) for text in drawn))
        self.assertEqual(sum(value == '自摸条件' for value in drawn), 4)
        self.assertEqual(sum(value == '荣和条件' for value in drawn), 4)
        names = [player['name']+'　' for player in self.model.state['players']]
        self.assertEqual(sum(any(value.startswith(name) for name in names) for value in drawn), 12)

    def test_report_columns_and_knockout_editor_dispatch_native_window_events(self):
        event = new_event('编辑器综合验收', 'individual_knockout')
        columns = ColumnDialog(None, event, 'individual')
        node = NodeDialog(None, self.model.state, event)
        try:
            columns.show();node.show();APP.processEvents()
            columns.reset()
            self.assertTrue(callable(columns.event))
            self.assertTrue(callable(node.event))
            self.assertIn('rank', columns.values())
            self.assertIn('name', columns.values())
        finally:
            columns.close();node.close();columns.deleteLater();node.deleteLater();APP.processEvents()


if __name__ == '__main__':unittest.main()
