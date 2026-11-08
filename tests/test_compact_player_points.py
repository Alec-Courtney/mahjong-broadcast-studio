"""Compact ML cards keep pt swaps and payment merges on their visible score ink."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QPointF,Qt
from PySide6.QtGui import QImage,QPainter,QFontDatabase
from PySide6.QtWidgets import QApplication
import artwork
from artwork import Animation,Artwork,fmt,ml_card_geometry
from final_points_motion import FinalPointsChange
from mahjong_values import display_score
from model import Model
from player_layout import PLAYER_LAYOUT

APP=QApplication.instance() or QApplication([])


def image(draw):
    result=QImage(*PLAYER_LAYOUT.canvas,QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(Qt.GlobalColor.transparent);painter=QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:draw(painter)
    finally:painter.end()
    return result


class CompactPlayerPointsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.font_ids=[]
        for name in ('msyh.ttc','msyhbd.ttc'):
            path=Path('C:/Windows/Fonts')/name
            if path.exists():cls.font_ids.append(QFontDatabase.addApplicationFont(str(path)))

    @classmethod
    def tearDownClass(cls):
        for identity in cls.font_ids:
            if identity>=0:QFontDatabase.removeApplicationFont(identity)

    def setUp(self):
        (ROOT/'.cache').mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='compact-player-points-',dir=ROOT/'.cache')
        self.model=Model(self.tmp.name)
        self.model.settings.update(theme='mleague',show_overlays=False,final_cards_visible=True)
        self.model.field('roundIndex',7);self.model.field('dealer',3)
        self.before=copy.deepcopy(self.model.state)
        self.model.settle(dict(type='ron',winner=0,loser=1,han=3,fu=40),
            dict(winner=0,name=self.model.state['players'][0]['name'],han=3,fu=40,yaku=['立直']))
        self.assertEqual(self.model.state['phase'],'finished')
        self.model.settings['final_cards_visible']=True
        self.art=Artwork(self.model,Animation())

    def tearDown(self):self.tmp.cleanup()

    def assert_same_visible_frame(self,actual,expected):
        self.assertEqual(actual.size().toTuple(),PLAYER_LAYOUT.canvas)
        difference=max(abs(a-b) for a,b in zip(bytes(actual.constBits()),bytes(expected.constBits())))
        self.assertLessEqual(difference,2,'Pt labels and the compact card did not render to the same pixels')

    def test_completed_pt_swap_matches_the_actual_compact_card_for_every_seat(self):
        self.model.settings['final_points_mode']='pt'
        change=FinalPointsChange(self.art,'scores','pt',1000.)
        for seat in range(4):
            with self.subTest(seat=seat):
                self.assert_same_visible_frame(image(lambda p:change.paint(p,seat,1001.)),
                    image(lambda p:self.art.player(p,seat,1001.)))

    def test_reversing_a_running_pt_swap_keeps_transformed_labels_and_clip(self):
        self.model.settings['final_points_mode']='pt'
        previous=FinalPointsChange(self.art,'scores','pt',1000.)
        self.model.settings['final_points_mode']='scores'
        reverse=FinalPointsChange(self.art,'pt','scores',1000.4,previous=previous)
        for seat in range(4):
            with self.subTest(seat=seat):
                previous_frame=previous.score_image(seat,1000.4)
                self.assertEqual(bytes(reverse.frames[seat]['old'].constBits()),bytes(previous_frame.constBits()))
                self.assert_same_visible_frame(image(lambda p:reverse.paint(p,seat,1001.4)),
                    image(lambda p:self.art.player(p,seat,1001.4)))

    def test_payment_merge_targets_the_visible_compact_score_right_edge_and_center(self):
        self.model.settings.update(final_cards_visible=False,riichi_payment_mode='immediate')
        with patch('artwork.time.monotonic',return_value=1000.):
            self.art.animation.begin(self.before,self.model.state,theme='mleague')
        moment=1000.+(self.art.animation.timing.merge_at_ms+self.art.animation.timing.merge_duration_ms-10)/1000
        original=artwork.text
        for seat in (0,1):
            with self.subTest(seat=seat):
                ordinary,deposit=self.art.animation.payment_display_lines(seat)
                self.assertNotEqual(ordinary,0)
                value=('+' if ordinary>0 else '−')+fmt(abs(ordinary));targets=[]
                def record(painter,label,*args,**kwargs):
                    if label==value and 'ink' in kwargs:
                        ink=kwargs['ink']
                        targets.append(painter.worldTransform().map(QPointF(ink.boundingRect().right(),0)))
                    return original(painter,label,*args,**kwargs)
                with patch('artwork.text',side_effect=record):
                    image(lambda p:self.art.player(p,seat,moment))
                self.assertEqual(len(targets),1)
                score=ml_card_geometry('','',fmt(display_score(self.model.state,seat,'immediate')),
                                      settings=self.model.settings)['labels']['score']
                expected=PLAYER_LAYOUT.body_transform().map(QPointF(score['ink'].boundingRect().right(),score['box'].center().y()))
                self.assertAlmostEqual(targets[0].x(),expected.x(),delta=.02)
                self.assertAlmostEqual(targets[0].y(),expected.y(),delta=.02)


if __name__=='__main__':unittest.main()
