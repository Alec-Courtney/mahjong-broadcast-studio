"""Reference timing, capture integration and interrupted ML wait transitions."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import Qt,QRectF
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QApplication,QWidget
from artwork import Artwork,Animation,SIZES
from corner_header import CORNER_SIZE,corner_canvas_size
from corner_editor import CornerHeaderDialog,CornerPreview
from live_badge import STING_SECONDS,WHITE_WIPE,WHITE_HOLD,RED_WIPE,LiveFrame,draw_live_badge,live_frame
from model import Model
from overlays import OverlayManager
from tenpai_animation import WaitFrame

APP=QApplication.instance() or QApplication([])


def render(size,draw):
    image=QImage(*size,QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent);p=QPainter(image)
    try:draw(p)
    finally:p.end()
    return image


class LiveTenpaiAnimationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='tmp-live-tenpai-',dir=ROOT/'tests')
        self.model=Model(self.temp.name);self.model.settings.update(show_overlays=False,theme='mleague')
        self.clock=patch('time.monotonic',return_value=100.);self.now=self.clock.start()
        self.manager=None

    def tearDown(self):
        if self.manager:self.manager.close()
        APP.processEvents();self.clock.stop();self.temp.cleanup()

    def waits(self,enabled,**extra):
        before=copy.deepcopy(self.model.state)
        self.model.state['players'][0].update(tenpai=enabled,waits=['9p'] if enabled else [],remaining=1 if enabled else None,**extra)
        return before,self.model.state

    def test_live_rests_seven_seconds_after_each_completed_sting(self):
        for cycle in (0,1,5):
            start=cycle*(7+STING_SECONDS)
            self.assertEqual(live_frame(start+.001).phase,'red')
            self.assertFalse(live_frame(start+6.999).moving)
            self.assertEqual(live_frame(start+7.01).phase,'white_wipe')
        white=live_frame(7+WHITE_WIPE+.02)
        self.assertEqual(white.phase,'white');self.assertFalse(white.moving)
        red=live_frame(7+WHITE_WIPE+WHITE_HOLD+.1)
        self.assertEqual(red.phase,'red_wipe');self.assertTrue(red.moving)
        flash=7+WHITE_WIPE+WHITE_HOLD+RED_WIPE
        self.assertEqual(live_frame(flash+.01).phase,'flash')

    def test_actual_capture_bypasses_static_cache_and_sleeps_through_holds(self):
        self.manager=OverlayManager(self.model);manager=self.manager
        self.model.settings['show_overlays']=True;manager.enabled['corner_header']=True
        manager._extension_picture('corner_header')
        red=render(CORNER_SIZE,lambda p:manager.draw_content(p,'corner_header'))
        self.now.return_value=108.05
        white=render(CORNER_SIZE,lambda p:manager.draw_content(p,'corner_header'))
        self.assertNotEqual(red,white)
        self.assertGreater(red.pixelColor(358,255).red(),200)
        self.assertLess(red.pixelColor(358,255).green(),20)
        self.assertGreater(white.pixelColor(358,255).green(),240)
        manager.timer.stop()
        self.now.return_value=107.4
        window=manager.windows['corner_header']
        with patch.object(window,'update') as update,patch.object(manager.players_capture,'update') as players:
            manager._wake_live_badge()
            damage=update.call_args.args[0]
            self.assertLess(damage.width()*damage.height(),window.width()*window.height()/10)
            players.assert_not_called()
        self.assertTrue(manager.live_timer.isActive());self.assertFalse(manager.timer.isActive())
        self.assertEqual(manager.live_timer.interval(),16)
        self.now.return_value=109.3;manager._wake_live_badge()
        self.assertGreater(manager.live_timer.interval(),6000)
        manager.enabled['corner_header']=False;manager._wake_live_badge()
        self.assertFalse(manager.live_timer.isActive())

    def test_old_title_geometry_extends_at_the_existing_scale_once(self):
        self.model.settings['windows']['corner_header']=[100,40,270,110]
        self.manager=OverlayManager(self.model)
        self.assertEqual(self.manager.windows['corner_header'].geometry().getRect(),(100,40,270,151))
        self.manager.restore_geometry()
        self.assertEqual(self.manager.windows['corner_header'].geometry().getRect(),(100,40,270,151))

    def test_live_damage_paints_only_badge_without_replaying_title(self):
        self.manager=OverlayManager(self.model);manager=self.manager
        self.model.settings['show_overlays']=True
        manager.enabled['corner_header']=True;self.model.settings['visible']['corner_header']=True
        manager.on_display();APP.processEvents();APP.processEvents()
        manager.timer.stop()
        self.now.return_value=107.3
        with patch.object(manager,'paint_content',wraps=manager.paint_content) as paint:
            manager._wake_live_badge();APP.processEvents()
            self.assertFalse(any(call.args[1]=='corner_header' for call in paint.call_args_list))
        manager.close(persist=False);self.assertFalse(manager.live_timer.isActive())

    def test_wipes_follow_measured_reference_edges_in_both_directions(self):
        # Leading edge in source pixels, at five pixels below the badge top.
        # These retain the source's initial acceleration and slower finish.
        for phase,frames,duration in (
            ('red_wipe',[(4,40),(5,58),(6,83),(7,128),(8,186),(9,233),(10,265),(11,290),(12,309)],24),
            ('white_wipe',[(1,14),(2,36),(3,72),(4,162),(6,200),(8,256),(12,319)],29)):
            for frame,expected in frames:
                with self.subTest(phase=phase,frame=frame):
                    image=render((352,118),lambda p:draw_live_badge(p,LiveFrame(phase,frame/duration),QRectF(4,4,344,110)))
                    def new_colour(x):
                        c=image.pixelColor(x+4,9)
                        return c.red()>180 and c.green()<70 if phase=='red_wipe' else min(c.red(),c.green(),c.blue())>180
                    edge=max((x for x in range(344) if new_colour(x)),default=-1)+1
                    self.assertAlmostEqual(edge,expected,delta=2)

    def test_moving_live_down_keeps_title_preview_and_capture_pixels_unchanged(self):
        self.model.settings['windows']['corner_header']=[100,40,540,302]
        self.manager=OverlayManager(self.model)
        preview=CornerPreview(self.model);preview.resize(540,302)
        try:
            initial=preview.grab().toImage().copy(0,0,540,220)
            preview.values['corner_live_y']=420;preview.sync_canvas()
            self.assertEqual(preview.grab().toImage().copy(0,0,540,220),initial)
            before=render((540,220),lambda p:self.manager.draw_content(p,'corner_header'))
            self.model.configure_corner_header({'corner_live_y':420})
            after=render((540,220),lambda p:self.manager.draw_content(p,'corner_header'))
            self.assertEqual(before,after)
            self.assertEqual(self.manager.windows['corner_header'].size().toTuple(),(540,482))
        finally:preview.deleteLater();APP.processEvents()

    def test_incoming_red_plate_keeps_the_same_padding_after_e_while_sliding(self):
        gaps=[]
        for frame in (12,14,16,18):
            image=render((352,118),lambda p:draw_live_badge(p,LiveFrame('red_wipe',frame/24),QRectF(4,4,344,110)))
            def red(x):
                c=image.pixelColor(x+4,94)
                return c.red()>180 and c.green()<70
            boundary=max(x for x in range(344) if red(x))
            def white(x):
                c=image.pixelColor(x+4,94)
                return min(c.red(),c.green(),c.blue())>180
            ink=max(x for x in range(boundary-3) if white(x))
            gaps.append(boundary-ink)
        self.assertLessEqual(max(gaps)-min(gaps),1)
        self.assertGreaterEqual(min(gaps),60)

    def test_saved_capture_height_does_not_stretch_or_squeeze_original_title(self):
        self.model.settings['windows']['corner_header']=[100,40,540,308]
        self.manager=OverlayManager(self.model)
        self.assertEqual(self.manager.windows['corner_header'].size().toTuple(),(540,302))

    def test_live_hold_editor_persists_without_touching_match_scores(self):
        self.manager=OverlayManager(self.model)
        owner=QWidget();owner.model=self.model;owner.manager=self.manager
        editor=CornerHeaderDialog(owner)
        try:
            self.assertEqual(editor.live_hold.value(),7.)
            players=copy.deepcopy(self.model.state['players']);editor.live_hold.setValue(9.5);editor.apply()
            self.assertEqual(self.model.state['broadcast']['config']['corner_live_hold'],9.5)
            self.assertEqual(self.model.state['players'],players)
            editor.reload();self.assertEqual(editor.live_hold.value(),9.5)
        finally:editor.deleteLater();owner.deleteLater();APP.processEvents()

    def test_live_layout_editor_expands_capture_and_preserves_title_scale_after_reload(self):
        self.model.settings['windows']['corner_header']=[100,40,270,151]
        self.manager=OverlayManager(self.model)
        owner=QWidget();owner.model=self.model;owner.manager=self.manager
        editor=CornerHeaderDialog(owner)
        try:
            editor.live_width.setValue(240);editor.live_height.setValue(72)
            editor.live_x.setValue(410);editor.live_gap.setValue(90)
            self.assertEqual(editor.preview.values['corner_live_x'],410)
            self.assertEqual(corner_canvas_size(editor.preview.values),(658,390))
            editor.apply();config=self.model.state['broadcast']['config']
            self.assertEqual((config['corner_live_width'],config['corner_live_height']),(240,72))
            self.assertEqual(self.manager.windows['corner_header'].base,(658,390))
            expected=(100,40,329,195)
            self.assertEqual(self.manager.windows['corner_header'].geometry().getRect(),expected)
            self.assertEqual(tuple(self.model.settings['windows']['corner_header']),expected)
            reloaded=Model(self.temp.name)
            self.assertEqual(tuple(reloaded.settings['windows']['corner_header']),expected)
            self.assertEqual(reloaded.state['broadcast']['config']['corner_live_y'],310)
            editor.reload();self.assertEqual(editor.live_width.value(),240)
            self.assertEqual(editor.live_gap.value(),90)
        finally:editor.deleteLater();owner.deleteLater();APP.processEvents()

    def test_withdrawal_retains_last_tiles_and_count_after_model_clears_them(self):
        self.waits(True);art=Artwork(self.model,Animation())
        initial=render(SIZES['player'],lambda p:art.player_waits(p,self.model.state['players'][0],0,100.))
        before,after=self.waits(False);art.update(before,after)
        self.assertFalse(self.model.state['players'][0]['tenpai'])
        first=render(SIZES['player'],lambda p:art.player_waits(p,self.model.state['players'][0],0,100.))
        self.assertEqual(initial,first)
        middle=render(SIZES['player'],lambda p:art.player_waits(p,self.model.state['players'][0],0,100.16))
        last=render(SIZES['player'],lambda p:art.player_waits(p,self.model.state['players'][0],0,100.51))
        self.assertNotEqual(initial,middle)
        empty=render(SIZES['player'],lambda p:None);self.assertEqual(last,empty)
        motion=art.wait_motion[0];frame=motion.frame(100.25)
        self.assertGreater(frame.tiles_alpha,0.);self.assertLess(frame.tiles_x,0.)
        self.assertEqual(frame.count_alpha,0.);self.assertGreater(frame.line_fraction,0.)

    def test_entry_and_rapid_reversal_continue_from_the_visible_frame(self):
        art=Artwork(self.model,Animation());before,after=self.waits(True);art.update(before,after)
        motion=art.wait_motion[0]
        self.assertEqual(motion.frame(100.),WaitFrame(0.,-24.,0.,0.))
        self.assertEqual(motion.frame(101.),WaitFrame())
        self.now.return_value=100.15;shown=motion.frame(100.15)
        before,after=self.waits(False);art.update(before,after)
        self.assertEqual(art.wait_motion[0].frame(100.15),shown)
        self.now.return_value=100.25;shown=art.wait_motion[0].frame(100.25)
        before,after=self.waits(True);art.update(before,after)
        self.assertEqual(art.wait_motion[0].frame(100.25),shown)
        self.assertEqual(art.wait_motion[0].frame(101.),WaitFrame())

    def test_new_hand_clears_withdrawal_without_ghosting_the_next_player(self):
        self.manager=OverlayManager(self.model)
        self.model.update(lambda state:state['players'][0].update(tenpai=True,waits=['9p'],remaining=1))
        self.now.return_value=101.
        self.model.update(lambda state:state['players'][0].update(tenpai=False,waits=[],remaining=None))
        self.assertIn(0,self.manager.art.wait_motion)
        self.model.start_match();self.assertEqual(self.manager.art.wait_motion,{})


if __name__=='__main__':unittest.main()
