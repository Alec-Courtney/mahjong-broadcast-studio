"""The programme uses one clock, restores live windows and never changes scores."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from PySide6.QtWidgets import QApplication, QWidget
from broadcast_console import BroadcastDialog
from event_store import EventStore
from model import Model
from overlays import OverlayManager
from postmatch_audio import ASSET_DIR

APP = QApplication.instance() or QApplication([])
TEST_MUSIC = ASSET_DIR / 'layout_sweep.wav'


class FakeAudio:
    def __init__(self, gate=None):
        self.error = ''
        self.calls = []
        self.gate = gate
        self.prepared = threading.Event()
        self.music_active = False

    def prepare(self, **kwargs):
        self.options = kwargs
        if self.gate:self.gate.wait(3)
        self.prepared.set()
        return None

    def play(self, position=0.):
        self.calls.append(('play', position));self.music_active=self.options.get('music_enabled',True)
    def pause(self):self.calls.append(('pause',))
    def resume(self):self.calls.append(('resume',))
    def seek(self, position):self.calls.append(('seek', position))
    def stop(self):self.calls.append(('stop',));self.music_active=False
    def close(self):self.calls.append(('close',));self.music_active=False
    def finish_effects(self):self.calls.append(('finish_effects',))
    def set_music_enabled(self, enabled):
        self.music_active=bool(enabled)
        if hasattr(self,'options'):self.options['music_enabled']=bool(enabled)


class PostmatchPlayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'tests')
        self.model = Model(self.tmp.name)
        self.model.configure_postmatch({'postmatch_music_path': str(TEST_MUSIC)})
        self.model.field('roundIndex', 7);self.model.field('dealer', 3)
        self.model.settle(dict(type='ron', winner=0, loser=1, han=3, fu=40), None)
        self.manager = OverlayManager(self.model)
        self.player = self.manager.postmatch
        self.music = self.manager.programme.music
        self.audio_patch = patch('postmatch_audio.PostMatchAudio', FakeAudio)
        self.audio_patch.start()

    def tearDown(self):
        self.manager.close();APP.processEvents();self.audio_patch.stop();self.tmp.cleanup()

    def play(self, fake=None):
        fake = fake or FakeAudio()
        with patch('postmatch_audio.PostMatchAudio', return_value=fake):self.player.start()
        self.assertTrue(fake.prepared.wait(3))
        for _ in range(100):
            APP.processEvents()
            if self.player.state != 'preparing' and not self.music.preparing:break
            time.sleep(.005)
        self.assertEqual(self.player.state, 'playing', self.player.message)
        return fake

    def test_stable_snapshot_and_last_page_hold_until_explicit_stop(self):
        expected = {key:self.manager.content_visible(key) for key in self.manager.windows}
        before = copy.deepcopy(self.model.state)
        fake = self.play()
        frozen = copy.deepcopy(self.player.snapshot.state)
        self.assertEqual(before, frozen)
        self.assertFalse(any(self.manager.content_visible(key) for key in self.manager.windows))
        self.model.set_score(0, self.model.state['players'][0]['score']+100)
        self.assertEqual(self.player.snapshot.state, frozen)
        self.player._offset = self.player.total_duration
        self.player._started = time.monotonic()
        self.player._tick()
        self.assertEqual(self.player.state, 'complete')
        self.assertFalse(any(self.manager.content_visible(key) for key in self.manager.windows))
        self.assertTrue(self.player.active)
        self.assertIn(('finish_effects',), fake.calls)
        self.assertTrue(self.music.enabled)
        self.assertTrue(self.player.can_pause)
        self.manager.programme.stop()
        self.assertEqual(expected, {key:self.manager.content_visible(key) for key in self.manager.windows})

    def test_pause_resume_and_next_table_share_audio_position(self):
        fake = self.play()
        self.player._started = 100.
        with patch('postmatch_player.time.monotonic', return_value=107.):
            self.player.pause_resume()
            self.assertEqual(self.player.state, 'paused');self.assertEqual(self.player.elapsed, 7.)
        with patch('postmatch_player.time.monotonic', return_value=120.):
            self.assertEqual(self.player.elapsed, 7.)
            self.player.next_page()
            self.assertAlmostEqual(self.player.elapsed, 20.967)
            self.assertEqual(fake.calls[-2:], [('seek', 20.967), ('pause',)])
            self.player.pause_resume()
        with patch('postmatch_player.time.monotonic', return_value=121.):self.assertAlmostEqual(self.player.elapsed, 21.967)
        self.assertEqual(fake.calls[-1], ('resume',))

    def test_audio_device_failure_stops_both_transports_and_reports_reason(self):
        fake = self.play();errors=[];self.player.failed.connect(errors.append)
        def fail():
            fake.error='声音设备已断开'
            return False
        fake.pause=fail
        self.player.pause_resume()
        self.assertFalse(self.player.active);self.assertFalse(self.player.timer.isActive())
        self.assertEqual(errors,['声音设备已断开'])
        self.assertIn(('close',),fake.calls)

    def test_music_continues_after_graphics_and_replay_without_reopening_device(self):
        fake=self.play()
        self.player.stop(completed=True)
        music=self.music.audio
        self.assertTrue(music.music_active);self.assertEqual(self.player.state,'complete')
        self.assertTrue(self.player.active)
        self.assertNotIn(('close',),fake.calls)
        self.manager.programme.pause_resume();self.assertEqual(self.player.state,'paused')
        self.manager.programme.pause_resume();self.assertEqual(self.player.state,'complete')
        self.player.start()
        for _ in range(100):
            APP.processEvents()
            if self.player.state!='preparing':break
            time.sleep(.005)
        self.assertEqual(self.player.state,'playing');self.assertIs(self.music.audio,music)
        self.assertNotIn(('close',),music.calls)
        self.manager.programme.stop();self.assertFalse(self.music.enabled);self.assertIn(('close',),music.calls)

    def test_manual_music_survives_graphics_hide_until_operator_stops_it(self):
        self.play();self.player.stop(completed=True)
        self.player.set_music_enabled(False)
        self.assertFalse(self.music.enabled)
        self.player.set_music_enabled(True)
        for _ in range(100):
            APP.processEvents()
            if not self.music.preparing:break
            time.sleep(.005)
        self.assertTrue(self.music.enabled)
        self.model.setting('show_overlays',False)
        self.assertFalse(self.player.active);self.assertTrue(self.music.enabled)
        self.manager.programme.stop();self.assertFalse(self.music.enabled)

    def test_visual_clock_begins_after_audio_device_opens(self):
        fake=FakeAudio();clock=[100.]
        def delayed_play(position=0.):
            clock[0]=100.2
            return True
        fake.play=delayed_play
        with patch('postmatch_player.time.monotonic',side_effect=lambda:clock[0]):
            self.play(fake)
            self.assertEqual(self.player.elapsed,0.)
            clock[0]=101.2
            self.assertAlmostEqual(self.player.elapsed,1.)

    def test_hide_and_new_hand_stop_audio_and_restore_capture_policy(self):
        fake = self.play()
        self.model.setting('show_overlays', False)
        self.assertFalse(self.player.active)
        self.assertIn(('stop',), fake.calls)
        self.assertFalse(any(self.manager.content_visible(key) for key in self.manager.windows))
        self.play()
        self.model.start_match()
        self.assertFalse(self.player.active)
        self.assertFalse(self.player.preparing)

    def test_starting_a_new_match_stops_music_that_continued_after_final_graphics(self):
        fake=self.play();self.player.stop(completed=True)
        self.assertTrue(self.music.enabled)
        self.model.start_match()
        self.assertFalse(self.music.enabled)
        self.assertFalse(self.player.active)
        self.assertIn(('close',),fake.calls)

    def test_cancel_preparation_does_not_open_a_stale_programme(self):
        gate = threading.Event();fake = FakeAudio(gate)
        with patch('postmatch_audio.PostMatchAudio', return_value=fake):self.player.start()
        self.assertTrue(self.player.preparing)
        self.player.stop();gate.set();self.assertTrue(fake.prepared.wait(3))
        for _ in range(10):APP.processEvents()
        self.assertEqual(self.player.state, 'idle')
        self.assertTrue(self.player.window.isVisible());self.assertFalse(self.player.active)
        self.assertNotIn(('play', 0.), fake.calls)

    def test_music_muted_during_preparation_cannot_be_reenabled_by_old_options(self):
        gate=threading.Event();fake=FakeAudio(gate)
        with patch('postmatch_audio.PostMatchAudio',return_value=fake):self.player.start()
        self.model.configure_postmatch({'postmatch_music_enabled':False})
        self.player.set_music_enabled(False)
        gate.set();self.assertTrue(fake.prepared.wait(3))
        for _ in range(20):
            APP.processEvents()
            if not self.player.preparing:break
        self.assertEqual(self.player.state,'playing')
        self.assertFalse(self.player.music_active)

    def test_line_flow_runs_during_graph_hold_but_paused_and_statistic_frames_are_cached(self):
        self.play();self.player.pause_resume()
        self.player.seek(7.);first = self.player.frame()
        self.assertIs(self.player.frame(),first)
        # Advance the existing clock without a seek invalidating the cache.
        self.player._offset=8.;second = self.player.frame()
        self.assertNotEqual(bytes(first.constBits()), bytes(second.constBits()))
        self.player.seek(26.);first=self.player.frame()
        self.player._offset=28.;self.assertIs(self.player.frame(),first)
        self.player.window.setGeometry(90, 80, 960, 540)
        self.player.window.save_geometry();self.manager.save_geometry()
        self.assertEqual(self.model.settings['windows']['fullscreen'], [90, 80, 960, 540])

    def test_play_controls_and_hold_defaults_match_current_finished_hand(self):
        parent = QWidget();parent.model = self.model;parent.manager = self.manager
        dialog = BroadcastDialog(parent)
        try:
            self.assertTrue(dialog.postmatch_play.isEnabled())
            self.assertEqual(dialog.postmatch_chart_hold.value(), 15.)
            self.assertEqual(dialog.postmatch_stats_hold.value(), 15.)
            self.assertTrue(dialog.postmatch_music.isChecked());self.assertTrue(dialog.postmatch_sfx.isChecked())
            dialog.postmatch_chart_hold.setValue(23.)
            dialog.save_postmatch()
            restarted = Model(self.model.path)
            self.assertEqual(restarted.state['broadcast']['config']['postmatch_chart_hold'], 23.)
        finally:
            dialog.deleteLater();parent.deleteLater();APP.processEvents()


class PostmatchEventSettingsTests(unittest.TestCase):
    def test_programme_preferences_follow_events_and_do_not_consume_score_undo(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            model = Model(tmp);store = EventStore(model);first = model.event['id']
            model.field('honba', 2)
            undo = len(model.undo)
            model.configure_postmatch({'postmatch_chart_hold': 24., 'postmatch_stats_hold': 18., 'postmatch_music_enabled': False})
            self.assertEqual(len(model.undo), undo)
            store.create('另一赛场', 'individual_league')
            self.assertEqual(model.state['broadcast']['config']['postmatch_chart_hold'], 15.)
            store.open(first)
            self.assertEqual(model.state['broadcast']['config']['postmatch_chart_hold'], 24.)
            self.assertEqual(model.state['broadcast']['config']['postmatch_stats_hold'], 18.)
            self.assertFalse(model.state['broadcast']['config']['postmatch_music_enabled'])

    def test_invalid_programme_options_leave_current_session_unchanged(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            model = Model(tmp)
            for values in ({'postmatch_chart_hold':0}, {'postmatch_stats_hold':float('nan')}, {'postmatch_music_volume':1.1}, {'postmatch_sfx_enabled':1}):
                before = copy.deepcopy(model.state)
                with self.assertRaises(ValueError):model.configure_postmatch(values)
                self.assertEqual(model.state, before)


if __name__ == '__main__':unittest.main()
