"""Live glossary cues preserve reference motion and freeze outgoing words."""
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
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QApplication
from model import Model
from overlays import OverlayManager
from broadcast_artwork import draw_extension,commentary_layout
from commentary_motion import SHOWN

APP=QApplication.instance() or QApplication([])


def pixels(image):return bytes(image.constBits())


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

    def image(self,static=False):
        image=QImage(*self.manager.base_size('commentary'),QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
        p=QPainter(image);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            if static:self.manager.draw_content(p,'commentary',static=True)
            else:self.manager.paint_content(p,'commentary')
        finally:p.end()
        return image

    def show(self):self.manager.set_extension_visible('commentary',True)

    def test_fixed_background_precedes_text_and_final_pixels_match_static(self):
        before=copy.deepcopy(self.model.state);self.show();self.at(.1)
        image=self.image()
        self.assertEqual(image.pixelColor(10,24).alpha(),218)
        self.assertEqual(image.pixelColor(image.width()-10,45).alpha(),0)
        self.assertEqual(self.manager._commentary_motion.frame(self.now).text_alpha,0.)
        self.at(1.34)
        self.assertEqual(self.manager._commentary_motion.frame(self.now),SHOWN)
        self.assertEqual(pixels(self.image()),pixels(self.image(True)))
        self.assertEqual(self.model.state,before)

    def test_exit_moves_only_text_then_closes_background_and_native_window(self):
        self.show();self.at(2.);self.manager.set_extension_visible('commentary',False)
        self.at(2.15);frame=self.manager._commentary_motion.frame(self.now)
        self.assertEqual(frame.width,1.);self.assertLess(frame.text_offset_y,-8)
        self.assertEqual(self.image().pixelColor(10,24).alpha(),218)
        self.assertTrue(self.manager.content_visible('commentary'))
        self.at(2.5);self.assertLess(self.manager._commentary_motion.frame(self.now).width,.8)
        self.at(2.71);self.assertFalse(self.manager.content_visible('commentary'))
        self.assertFalse(self.manager.content_visible('commentary'))

    def test_visible_rule_grows_and_contracts_around_the_same_center_in_every_frame(self):
        self.show();layout=commentary_layout(self.model,self.manager.base_size('commentary')[0]);center=layout['top']+54
        x=round(layout['divider']);entry_lengths=[];exit_lengths=[]
        def bounds():
            image=self.image();ys=[]
            for y in range(layout['top']+8,layout['top']+100):
                background=image.pixelColor(x+6,y).red()
                if max(image.pixelColor(column,y).red() for column in range(x-2,x+3))-background>5:ys.append(y)
            self.assertTrue(ys)
            self.assertAlmostEqual((min(ys)+max(ys)+1)/2,center,delta=.5)
            return max(ys)-min(ys)+1
        for frame in range(6,18):
            self.at(frame/30);entry_lengths.append(bounds())
        self.assertEqual(entry_lengths,sorted(entry_lengths))
        self.assertLessEqual(entry_lengths[0],4)  # A center dot precedes the line.
        self.assertLess(entry_lengths[0],entry_lengths[-1])
        self.at(2.);self.manager.set_extension_visible('commentary',False)
        for frame in range(1,9):
            self.at(2.+frame/30);exit_lengths.append(bounds())
        self.assertEqual(exit_lengths,sorted(exit_lengths,reverse=True))
        self.assertGreater(exit_lengths[0],exit_lengths[-1])

    def test_word_replacement_finishes_old_exit_and_uses_latest_requested_word(self):
        self.show();self.at(2.);previous=pixels(self.image())
        self.model.configure_commentary(dict(commentary_title='本场',commentary_body='连庄时累积的本场数。'))
        self.assertEqual(pixels(self.image()),previous)
        self.assertEqual(self.manager._commentary_data['model'].state['broadcast']['config']['commentary_title'],'场供')
        self.at(2.2)
        self.model.configure_commentary(dict(commentary_title='听牌',commentary_body='再得到一张有效牌即可完成和牌牌形。'))
        self.assertEqual(self.manager._commentary_data['model'].state['broadcast']['config']['commentary_title'],'场供')
        self.at(2.71)
        self.assertTrue(self.manager._commentary_motion.target)
        self.assertEqual(self.manager._commentary_data['model'].state['broadcast']['config']['commentary_title'],'听牌')
        self.at(4.05);self.assertEqual(pixels(self.image()),pixels(self.image(True)))

    def test_style_edit_keeps_outgoing_type_and_reflows_new_caption(self):
        self.show()
        self.at(2.)
        outgoing = self.manager._commentary_data
        self.model.configure_commentary({
            'commentary_title_font_family': 'Arial', 'commentary_title_font_size': 38,
            'commentary_title_letter_spacing': 2., 'commentary_body_font_size': 48,
            'commentary_body_letter_spacing': 4.,
        })
        self.assertIs(self.manager._commentary_data, outgoing)
        self.assertEqual(outgoing['layout']['title_font'].pixelSize(), 26)
        self.assertEqual(outgoing['layout']['body_font'].pixelSize(), 24)
        pending = self.manager._commentary_pending
        self.assertGreater(pending['height'], outgoing['height'])
        self.at(2.71)
        self.assertIs(self.manager._commentary_data, pending)
        title = pending['layout']['title_font']
        body = pending['layout']['body_font']
        self.assertEqual((title.family(), title.pixelSize(), title.letterSpacing()), ('Arial', 38, 2.))
        self.assertEqual((body.pixelSize(), body.letterSpacing()), (48, 4.))
        self.at(4.05)
        self.assertEqual(pixels(self.image()), pixels(self.image(True)))

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

    def test_reverse_and_global_hide_continue_from_current_visible_frame(self):
        self.show();self.at(.95);entering=pixels(self.image())
        self.manager.set_extension_visible('commentary',False)
        self.assertEqual(pixels(self.image()),entering)
        self.at(1.02);exiting=pixels(self.image());self.show()
        self.assertEqual(pixels(self.image()),exiting)
        self.at(2.4);self.manager.set_overlays_visible(False)
        self.assertTrue(self.manager.content_visible('commentary'))
        self.at(3.11);self.assertFalse(self.manager.content_visible('commentary'))
        self.manager.set_overlays_visible(True);self.at(4.46)
        self.assertEqual(self.manager._commentary_motion.frame(self.now),SHOWN)


if __name__=='__main__':unittest.main()
