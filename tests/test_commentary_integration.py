"""Show, replace and dismiss glossary captions in the live capture."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from model import Model
from overlays import OverlayManager

APP=QApplication.instance() or QApplication([])


def has_visible_caption(window):
    image = window.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return any(bytes(image.constBits())[3::4])


class CommentaryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now=1000.;self.clock=patch('overlays.time.monotonic',side_effect=lambda:self.now);self.clock.start()
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.model=Model(self.tmp.name)
        self.model.configure_commentary(dict(commentary_title='场供',commentary_body='立直时支付的点棒，保留到有人和牌。',commentary_builtin=False,commentary_duration=0))
        self.manager=OverlayManager(self.model);self.manager.on_display()

    def tearDown(self):
        self.manager.close();APP.processEvents();self.tmp.cleanup();self.clock.stop()

    def at(self,seconds):
        self.now=1000.+seconds;self.manager.tick()

    def show(self):self.manager.set_extension_visible('commentary',True)

    def test_show_and_hide_reuse_the_live_capture_without_editing_the_match(self):
        before = copy.deepcopy((self.model.state, self.model.undo))
        window = self.manager.capture_windows['commentary']
        handle = int(window.winId())
        self.show()
        self.at(2.)
        self.assertTrue(has_visible_caption(window))
        self.manager.set_overlays_visible(False)
        self.at(3.)
        self.assertFalse(has_visible_caption(window))
        self.manager.set_overlays_visible(True)
        self.at(5.)
        self.assertTrue(has_visible_caption(window))
        self.assertEqual(int(window.winId()), handle)
        self.assertEqual((self.model.state, self.model.undo), before)

    def test_word_replacement_finishes_old_exit_and_uses_latest_requested_word(self):
        self.show();self.at(2.)
        self.model.configure_commentary(dict(commentary_title='本场',commentary_body='连庄时累积的本场数。'))
        self.assertEqual(self.manager._commentary_data['model'].state['broadcast']['config']['commentary_title'],'场供')
        self.at(2.2)
        self.model.configure_commentary(dict(commentary_title='听牌',commentary_body='再得到一张有效牌即可完成和牌牌形。'))
        self.assertEqual(self.manager._commentary_data['model'].state['broadcast']['config']['commentary_title'],'场供')
        self.at(2.71)
        self.assertTrue(self.manager._commentary_motion.target)
        self.assertEqual(self.manager._commentary_data['model'].state['broadcast']['config']['commentary_title'],'听牌')
        self.at(4.05)
        self.assertTrue(has_visible_caption(self.manager.capture_windows['commentary']))

    def test_long_caption_keeps_the_operators_saved_position_after_reopen(self):
        self.model.settings.update(commentary_layout_version=5, commentary_auto=False)
        self.model.settings['windows']['commentary'] = [210, 82, 440, 80]
        self.manager.restore_geometry()
        window = self.manager.windows['commentary']
        body = '立直时支付的点棒，保留到有人和牌。' * 8
        self.model.configure_commentary({'commentary_body': body})
        self.assertEqual((window.x(), window.y(), window.width()), (210, 82, 440))
        self.manager.save_geometry()
        restored = Model(self.model.path)
        self.assertEqual(restored.settings['windows']['commentary'], list(window.geometry().getRect()))
        self.assertEqual(restored.state['broadcast']['config']['commentary_body'], body)

    def test_complete_hold_counts_after_entry_and_manual_hide_discards_pending(self):
        self.model.configure_commentary({'commentary_duration':2});self.show()
        self.assertIsNone(self.manager.commentary_deadline)
        self.at(1.34);self.assertAlmostEqual(self.manager.commentary_deadline,1003.34)
        self.at(3.33);self.assertTrue(self.manager._commentary_motion.target)
        self.at(3.35);self.assertFalse(self.manager._commentary_motion.target)
        self.at(4.06);self.assertFalse(self.manager.content_visible('commentary'))
        self.model.configure_commentary({'commentary_duration':0});self.show();self.at(5.5)
        self.model.configure_commentary({'commentary_title':'不应自动上屏的新词'})
        self.manager.set_extension_visible('commentary',False);self.at(6.21)
        self.assertIsNone(self.manager._commentary_pending)
        self.assertFalse(self.manager.content_visible('commentary'))



if __name__=='__main__':unittest.main()
