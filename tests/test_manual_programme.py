"""Manual terminal views, independent music, held endings and shared controls."""
import copy,os,sys,tempfile,time,threading,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage,QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from model import Model
from overlays import OverlayManager
from player_layout import PLAYER_LAYOUT
from tests.test_postmatch_player import FakeAudio, TEST_MUSIC

APP=QApplication.instance() or QApplication([])


class ManualProgrammeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='tmp-manual-programme-',dir=ROOT/'tests')
        self.audio_patch=patch('postmatch_audio.PostMatchAudio',FakeAudio);self.audio_patch.start()
        self.model=Model(self.temp.name);self.model.settings.update(show_overlays=True,theme='mleague')
        self.model.configure_postmatch({'postmatch_music_path': str(TEST_MUSIC)})
        self.manager=OverlayManager(self.model);self.controller=self.manager.programme

    def tearDown(self):
        self.manager.close()
        for _ in range(3):QTest.qWait(10);APP.processEvents()
        self.audio_patch.stop();self.temp.cleanup()

    def wait_ready(self,predicate):
        for _ in range(100):
            APP.processEvents()
            if predicate():return
            QTest.qWait(10)
        self.fail('background preparation did not finish')

    def finish_hand(self):
        self.model.field('roundIndex',7);self.model.field('dealer',3)
        self.model.settle(dict(type='ron',winner=0,loser=1,han=3,fu=40),dict(winner=0,name='选手一',han=3,fu=40,yaku=['立直']))
        self.assertEqual(self.model.state['phase'],'finished')

    def drawn_rank(self):
        image=QImage(*PLAYER_LAYOUT.canvas,QImage.Format.Format_ARGB32_Premultiplied);painter=QPainter(image)
        try:
            with patch('artwork.paint_ml_card') as draw:
                self.manager.art.player(painter,0,time.monotonic())
                return draw.call_args.args[5]
        finally:painter.end()

    def test_terminal_submission_keeps_live_cards_until_explicit_operator_choice(self):
        self.finish_hand();self.assertIsNone(self.drawn_rank())
        for stage in ('players_out','win','win_out','players_in','table'):
            self.manager.animation.finish();self.manager.pending_win=False;self.manager.pending_next=stage in ('win','win_out')
            self.manager.set_stage(stage,time.monotonic()-100);self.manager.tick()
            self.assertIsNone(self.drawn_rank());self.assertEqual(self.manager.bottom_mode(),'players')
            self.assertFalse(self.model.settings['final_cards_visible']);self.assertIsNone(self.manager.final_pt_deadline)
        before=copy.deepcopy((self.model.state,self.model.undo))
        self.manager.set_bottom_mode('final_scores');self.assertIsNotNone(self.drawn_rank())
        self.manager.set_bottom_mode('final_pt');self.assertEqual(self.manager.bottom_mode(),'final_pt')
        self.manager.set_bottom_mode('players');self.assertIsNone(self.drawn_rank())
        self.assertEqual((self.model.state,self.model.undo),before)

    def test_player_preview_can_be_skipped_and_yields_to_live_riichi(self):
        before = copy.deepcopy((self.model.state, self.model.undo))
        self.assertTrue(self.manager.replay_players('cycle'))
        self.manager.finish()
        self.assertEqual(self.manager.player_opacity(), 1.)
        self.assertEqual((self.model.state, self.model.undo), before)
        self.assertTrue(self.manager.replay_players())
        self.model.riichi(0)
        self.assertTrue(self.manager.riichi_queue.busy)
        state = copy.deepcopy(self.model.state)
        with self.assertRaises(ValueError):
            self.manager.replay_players()
        self.assertEqual(self.model.state, state)

    def test_result_choices_remain_available_after_showing_final_cards(self):
        from broadcast_console import refresh_bottom_selector
        from PySide6.QtWidgets import QComboBox
        self.finish_hand();self.manager.finish();self.model.commit_match()
        self.manager.set_bottom_mode('final_pt');selector=QComboBox()
        refresh_bottom_selector(selector,self.manager)
        self.assertGreaterEqual(selector.findData('match_result'),0)
        self.assertGreaterEqual(selector.findData('total_result'),0)
        self.manager.set_bottom_mode('total_result');self.assertEqual(self.manager.bottom_mode(),'total_result')

    def test_first_final_scores_do_not_replay_the_previous_match_pt_mode(self):
        self.model.settings['final_points_mode']='pt';self.finish_hand()
        self.manager.set_bottom_mode('final_scores');self.assertIsNone(self.manager._final_card_change)
        self.manager.set_bottom_mode('final_pt');self.assertIsNotNone(self.manager._final_card_change)

    def test_music_device_failure_is_reported_and_clears_pressed_state(self):
        self.controller.music.set_enabled(True);self.wait_ready(lambda:not self.controller.music.preparing)
        audio=self.controller.music.audio;errors=[];self.controller.music.failed.connect(errors.append)
        audio.error='test device disconnected';audio.pause=lambda:False
        self.controller.pause_resume()
        self.assertFalse(self.controller.music.enabled);self.assertIsNone(self.controller.music.audio)
        self.assertEqual(errors,['test device disconnected']);self.assertIn(('close',),audio.calls)

    def test_committing_final_result_never_cues_a_result_page(self):
        self.finish_hand();self.manager.finish();self.model.commit_match()
        self.assertNotIn(self.manager.stage,('settlement_hand','settlement_total','settlement_final'))
        self.assertFalse(self.model.settings['final_cards_visible'])
        self.manager.set_bottom_mode('match_result');self.assertEqual(self.manager.stage,'settlement_hand')
        self.manager.stage_started=time.monotonic()-1000;self.manager.tick()
        self.assertEqual(self.manager.stage,'settlement_hand')
        self.manager.set_bottom_mode('total_result');self.manager.stage_started=time.monotonic()-1000;self.manager.tick()
        self.assertEqual(self.manager.stage,'settlement_total')

    def test_ending_remains_visible_and_manual_music_mute_survives_completion(self):
        self.finish_hand();self.controller.select('ending')
        self.wait_ready(lambda:self.manager.postmatch.state=='playing' and not self.controller.music.preparing)
        self.assertTrue(self.controller.music.enabled)
        self.controller.music.set_enabled(False)
        player=self.manager.postmatch;player._offset=player.total_duration;player._started=time.monotonic();player._tick()
        self.assertEqual(player.state,'complete');self.assertTrue(player.active);self.assertEqual(player.opacity(),1.)
        self.assertFalse(self.controller.music.enabled);self.assertEqual(self.controller.current_key,'ending_statistics')
        frame=player.frame();self.assertIsNotNone(frame)
        first=bytes(frame.constBits());player._tick();self.assertEqual(bytes(player.frame().constBits()),first)
        self.controller.stop();self.assertFalse(player.active)

    def test_music_plays_without_a_page_and_shared_controls_pause_and_stop_it(self):
        self.controller.music.set_enabled(True)
        self.wait_ready(lambda:not self.controller.music.preparing)
        self.assertIsNone(self.controller.current_key);self.assertTrue(self.controller.music.audio.music_active)
        self.controller.pause_resume();self.assertTrue(self.controller.paused)
        self.assertIn(('pause',),self.controller.music.audio.calls)
        self.controller.pause_resume();self.assertFalse(self.controller.paused)
        self.controller.stop();self.assertFalse(self.controller.music.enabled)

    def test_ending_plays_without_preparing_music_when_no_track_is_selected(self):
        self.model.configure_postmatch({'postmatch_music_path': ''})
        self.finish_hand();self.controller.select('ending')
        self.wait_ready(lambda:self.manager.postmatch.state=='playing')
        self.assertTrue(self.manager.postmatch.audio.options['sfx_enabled'])
        self.assertFalse(self.controller.music.enabled)
        self.assertFalse(self.controller.music.preparing)
        self.assertIsNone(self.controller.music.audio)

    def test_standalone_music_restarts_unpaused_after_turning_off_a_paused_track(self):
        self.controller.music.set_enabled(True);self.wait_ready(lambda:not self.controller.music.preparing)
        self.controller.pause_resume();self.assertTrue(self.controller.paused)
        self.controller.music.set_enabled(False);self.assertFalse(self.controller.paused)
        self.controller.music.set_enabled(True);self.wait_ready(lambda:not self.controller.music.preparing)
        self.assertFalse(self.controller.paused);self.assertTrue(self.controller.music.audio.music_active)

    def test_title_page_pause_and_page_switch_keep_manual_music_override(self):
        self.controller.select('opening_title');self.wait_ready(lambda:not self.controller.music.preparing)
        self.assertEqual(self.controller.current_key,'opening_title');self.assertTrue(self.controller.music.enabled)
        self.controller.music.set_enabled(False);self.controller.pause_resume();elapsed=self.manager.opening.elapsed
        QTest.qWait(20);self.assertEqual(self.manager.opening.elapsed,elapsed)
        self.controller.pause_resume();self.assertFalse(self.controller.music.enabled)
        self.controller.select('four_conditions');self.assertEqual(self.controller.current_key,'four_conditions')
        self.assertFalse(self.manager.opening.active);self.assertFalse(self.controller.music.enabled)

    def test_cancelled_music_worker_cannot_start_a_late_track(self):
        gate=threading.Event();audio=FakeAudio(gate=gate);self.controller.music.audio_factory=lambda:audio
        self.controller.music.set_enabled(True);self.controller.music.set_enabled(False);gate.set()
        self.wait_ready(lambda:audio.prepared.is_set())
        for _ in range(3):QTest.qWait(10);APP.processEvents()
        self.assertFalse(self.controller.music.enabled);self.assertIsNone(self.controller.music.audio)
        self.assertFalse(any(call[0]=='play' for call in audio.calls));self.assertIn(('close',),audio.calls)

    def test_shared_pause_freezes_only_the_selected_panel(self):
        self.controller.select('chart');self.controller.pause_resume()
        first=self.controller.frozen_frame.copy();self.manager.chart_flow_started-=60;self.manager.tick()
        image=QImage(first.size(),first.format());image.fill(Qt.GlobalColor.transparent);p=QPainter(image)
        try:self.manager.paint_content(p,'chart')
        finally:p.end()
        self.assertEqual(bytes(first.constBits()),bytes(image.constBits()))
        self.controller.pause_resume();self.assertIsNone(self.controller.frozen_frame)

    def test_clicking_the_selected_page_hides_it_and_preserves_standalone_music(self):
        self.controller.toggle('opening_title');self.wait_ready(lambda:not self.controller.music.preparing)
        snapshot=self.manager.opening.snapshot
        self.controller.music.set_enabled(True)  # The operator now owns this music choice.
        self.controller.toggle('opening_title')
        self.assertIsNone(self.controller.current_key);self.assertFalse(self.manager.opening.active)
        self.assertIs(self.manager.opening.snapshot,snapshot);self.assertTrue(self.controller.music.enabled)
        self.controller.toggle('chart');self.assertEqual(self.controller.current_key,'chart')
        self.controller.toggle('chart');self.assertIsNone(self.controller.current_key)
        self.assertTrue(self.controller.music.enabled)

    def test_clicking_the_selected_ending_while_preparing_cancels_the_pending_page(self):
        self.finish_hand();gate=threading.Event();audio=FakeAudio(gate)
        with patch('postmatch_audio.PostMatchAudio',return_value=audio):self.controller.toggle('ending_chart')
        self.assertTrue(self.manager.postmatch.preparing)
        self.controller.toggle('ending_chart');gate.set();self.wait_ready(lambda:audio.prepared.is_set())
        QTest.qWait(20);APP.processEvents()
        self.assertIsNone(self.controller.current_key);self.assertFalse(self.manager.postmatch.active)
        self.assertFalse(self.manager.postmatch.preparing);self.assertFalse(self.controller.music.enabled)

    def test_programme_bar_tracks_actual_page_and_uses_one_transport(self):
        from console import Console
        from programme import PAGE_BUTTONS
        ui=Console(self.model,self.manager);ui.show();APP.processEvents()
        try:
            self.assertEqual(set(ui.programme_buttons),{key for key,_ in PAGE_BUTTONS})
            self.assertNotIn('riichi_effect',ui.programme_buttons)
            ui.programme_buttons['opening_title'].click()
            self.wait_ready(lambda:not self.controller.music.preparing)
            self.assertTrue(ui.programme_buttons['opening_title'].isChecked());self.assertTrue(ui.programme_music.isChecked())
            ui.programme_music.click();self.assertFalse(self.controller.music.enabled)
            ui.programme_pause.click();self.assertTrue(self.controller.paused);self.assertTrue(ui.programme_pause.isChecked())
            ui.programme_pause.click();self.assertFalse(self.controller.paused);self.assertFalse(ui.programme_music.isChecked())
            snapshot=self.manager.opening.snapshot;ui.programme_buttons['opening_title'].click()
            self.assertFalse(self.manager.opening.active);self.assertIsNone(self.controller.current_key)
            self.assertIs(self.manager.opening.snapshot,snapshot);self.assertFalse(ui.programme_buttons['opening_title'].isChecked())
            ui.programme_buttons['chart'].click()
            self.assertTrue(ui.programme_buttons['chart'].isChecked());self.assertFalse(ui.programme_buttons['opening_title'].isChecked())
            ui.programme_buttons['chart'].click();self.assertIsNone(self.controller.current_key)
            self.assertFalse(ui.programme_buttons['chart'].isChecked())
            ui.programme_stop.click();self.assertIsNone(self.controller.current_key)
            self.assertFalse(any(control.isChecked() for control in ui.programme_buttons.values()))
        finally:ui.close();ui.deleteLater();APP.processEvents()


if __name__=='__main__':unittest.main()
