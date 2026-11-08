"""Verify audio duration, cue placement, headroom and silent/error transport."""
from array import array
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
import postmatch_audio as audio


class FakeMusicLoop:
    def __init__(self):
        self.active = False
        self.paused = False
        self.calls = []

    def play(self, path):
        self.calls.append(('play', path));self.active = True;self.paused = False

    def pause(self):
        if self.active:self.calls.append(('pause',));self.paused = True

    def resume(self):
        if self.active:self.calls.append(('resume',));self.paused = False

    def close(self):
        self.calls.append(('close',));self.active = False;self.paused = False


class PostMatchAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(dir=ROOT/'tests')
        folder = Path(cls.directory.name)
        tone = array('h', (round(12000 * math.sin(math.tau * index / 120)) for index in range(120)))
        stereo_tone = array('h', (sample for sample in tone for _ in range(audio.CHANNELS)))
        cls.music = audio._write_pcm(folder/'test-music.wav', stereo_tone * 1600)
        cls.track = audio.mix_soundtrack(folder/'all.wav', music_path=cls.music, music_volume=1., sfx_volume=1.)
        with wave.open(str(cls.track), 'rb') as stream:
            cls.parameters = stream.getparams()
            cls.samples = array('h')
            cls.samples.frombytes(stream.readframes(stream.getnframes()))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_complete_holds_have_the_requested_length_and_extra_fade(self):
        self.assertEqual(self.parameters[:3], (2, 2, 48000))
        self.assertAlmostEqual(self.parameters.nframes/self.parameters.framerate, 39.467, places=5)
        self.assertAlmostEqual(audio.soundtrack_duration(23., 7.), 39.467, places=5)

    def test_exit_effects_move_with_chart_hold_without_moving_entry_effects(self):
        normal, longer = audio.sound_cues(15), audio.sound_cues(27)
        self.assertEqual(normal[:3], longer[:3])
        for a, b in zip(normal[3:], longer[3:]):
            self.assertAlmostEqual(b.time-a.time, 12.)
        self.assertAlmostEqual(normal[3].time, 21.087)
        self.assertAlmostEqual(normal[4].time, 23.187)

    def test_full_volume_mix_has_headroom_and_clean_endpoints(self):
        self.assertGreater(max(abs(n) for n in self.samples), 1000)
        self.assertLessEqual(max(abs(n) for n in self.samples), 31129)
        self.assertEqual(list(self.samples[:2]), [0, 0])
        self.assertEqual(list(self.samples[-2:]), [0, 0])

    def test_music_continues_during_both_holds_and_across_the_page_change(self):
        for start in (8., 15., 20.8, 21.2, 27., 36.):
            first = round(start*48000)*2
            window = self.samples[first:first+4800*2]
            with self.subTest(time=start):
                self.assertGreater(sum(value*value for value in window)/len(window), 100.)

    def test_unset_music_leaves_hold_silent_and_keeps_data_sweep_at_new_time(self):
        player = audio.PostMatchAudio(cache_dir=Path(self.directory.name)/'default')
        target = player.prepare(chart_hold=2., stats_hold=0.)
        self.assertIsInstance(target, Path, player.error)
        self.assertIsNone(player.music_path)
        with wave.open(str(target), 'rb') as stream:
            samples = array('h')
            samples.frombytes(stream.readframes(stream.getnframes()))
        # Entry effects end at 5.517s; graph remains complete until 7.967s.
        first, last = round(6*48000)*2, round(7*48000)*2
        self.assertTrue(all(value == 0 for value in samples[first:last]))
        cue = audio.sound_cues(2.)[-1].time
        first, last = round((cue+.1)*48000)*2, round((cue+.4)*48000)*2
        self.assertGreater(max(abs(value) for value in samples[first:last]), 100)

    def test_graph_sound_is_audible_at_selected_level_and_occurs_once(self):
        target = audio.mix_soundtrack(Path(self.directory.name)/'graph-level.wav',
                                     music_enabled=False, sfx_volume=.7)
        with wave.open(str(target), 'rb') as stream:
            mixed = array('h');mixed.frombytes(stream.readframes(stream.getnframes()))
        with wave.open(str(audio.ASSET_DIR/'graph_reveal.wav'), 'rb') as stream:
            original = array('h');original.frombytes(stream.readframes(stream.getnframes()))
        first = round(2.*48000)*2
        actual = mixed[first:first+len(original)]
        # No other cue overlaps the 2.0–4.667s graph event. Keep the operator's
        # selected 70% level instead of burying it behind another -10dB cut.
        energy_ratio = (sum(value*value for value in actual)/sum(value*value for value in original))**.5
        self.assertAlmostEqual(energy_ratio, .7, places=3)
        for left, right in ((6.,20.9),(25.,38.9)):
            self.assertTrue(all(value == 0 for value in mixed[round(left*48000)*2:round(right*48000)*2]))

    def test_standard_wav_music_needs_no_ffmpeg_and_mixes_without_sfx(self):
        target = Path(self.directory.name)/'custom.wav'
        with patch.object(audio.shutil, 'which', return_value=None):
            audio.mix_soundtrack(target, chart_hold=0., stats_hold=0., music_path=self.track, sfx_enabled=False)
        self.assertTrue(target.is_file())

    def test_muted_prepare_and_transport_do_not_touch_a_sound_device(self):
        player = audio.PostMatchAudio()
        with patch.object(player, '_command', side_effect=AssertionError('Device touched')):
            self.assertIsNone(player.prepare(music_enabled=False, sfx_enabled=False))
            self.assertEqual(player.error, '')
            self.assertTrue(player.play())
            self.assertTrue(player.pause())
            self.assertTrue(player.seek(10.))
            self.assertTrue(player.resume())
            self.assertTrue(player.stop())
            player.close()

    def test_missing_music_reports_a_preparation_error(self):
        player = audio.PostMatchAudio()
        self.assertIsNone(player.prepare(music_path=Path(self.directory.name)/'missing.mp3', sfx_enabled=False))
        self.assertIn('声音准备失败', player.error)

    def test_preparation_is_safe_for_a_background_worker(self):
        player = audio.PostMatchAudio(cache_dir=Path(self.directory.name)/'cache')
        with patch.object(player, '_command', side_effect=AssertionError('Device touched')):
            result = player.prepare(chart_hold=0., stats_hold=0., music_path=self.track, sfx_enabled=False)
        self.assertIsInstance(result, Path)
        self.assertEqual(player.error, '')

    def test_pause_seek_and_resume_use_one_continuous_track(self):
        player = audio.PostMatchAudio()
        player.path = self.track
        player.duration = 39.467
        commands = []
        def command(value):
            commands.append(value)
            return '1200' if value.startswith('status ') else ''
        with patch.object(player, '_command', side_effect=command):
            self.assertTrue(player.play())
            self.assertTrue(player.pause())
            self.assertTrue(player.seek(23.967))
            self.assertTrue(player.resume())
            self.assertTrue(player.stop())
            player.close()
        self.assertEqual(sum(value.startswith('open ') for value in commands), 1)
        self.assertTrue(any(value.endswith('from 23967') for value in commands))

    def test_selected_music_has_a_separate_loop_and_effects_file_is_silent_during_holds(self):
        player = audio.PostMatchAudio(cache_dir=Path(self.directory.name)/'independent')
        self.assertIsInstance(player.prepare(music_path=self.music), Path, player.error)
        self.assertNotEqual(player.path, player.music_path)
        with wave.open(str(player.music_path), 'rb') as stream:
            self.assertAlmostEqual(stream.getnframes()/stream.getframerate(), 3.6, places=4)
        with wave.open(str(player.path), 'rb') as stream:
            samples = array('h');samples.frombytes(stream.readframes(stream.getnframes()))
        for left, right in ((6.,20.9),(25.,38.9)):
            self.assertTrue(all(value == 0 for value in samples[round(left*48000)*2:round(right*48000)*2]))

    def test_full_volume_independent_tracks_keep_combined_peak_headroom(self):
        player = audio.PostMatchAudio(cache_dir=Path(self.directory.name)/'headroom')
        self.assertIsInstance(player.prepare(music_path=self.music, music_volume=1., sfx_volume=1.), Path, player.error)
        peaks = []
        for path in (player.music_path, player.path):
            with wave.open(str(path), 'rb') as stream:
                samples = array('h');samples.frombytes(stream.readframes(stream.getnframes()))
            peaks.append(max(abs(value) for value in samples)/32768.)
        self.assertGreater(peaks[0], .1)
        self.assertGreater(peaks[1], .1)
        self.assertLessEqual(sum(peaks), .95)

    def test_next_page_and_natural_completion_do_not_repeat_or_restart_music(self):
        player = audio.PostMatchAudio(cache_dir=Path(self.directory.name)/'reuse')
        player._music = FakeMusicLoop()
        self.assertIsInstance(player.prepare(chart_hold=0., stats_hold=0., music_path=self.music), Path, player.error)
        commands = []
        def command(value):
            commands.append(value)
            return '500' if value.startswith('status ') else ''
        with patch.object(player, '_command', side_effect=command):
            self.assertTrue(player.play())
            music_path = player.music_path
            self.assertTrue(player.seek(5.967))
            self.assertEqual(sum(call[0]=='play' for call in player._music.calls), 1)
            self.assertTrue(player.finish_effects())
            self.assertTrue(player.music_active)
            effect_play_count = sum(value.startswith('play ') for value in commands)
            self.assertTrue(player.pause());self.assertTrue(player.music_paused)
            self.assertTrue(player.resume());self.assertFalse(player.music_paused)
            self.assertEqual(sum(value.startswith('play ') for value in commands), effect_play_count)
            # A repeat with different page holds reuses the same live music.
            self.assertIsInstance(player.prepare(chart_hold=2., stats_hold=3., music_path=self.music), Path, player.error)
            self.assertEqual(player.music_path, music_path)
            self.assertTrue(player.play())
            self.assertEqual(sum(call[0]=='play' for call in player._music.calls), 1)
            self.assertTrue(player.set_music_enabled(False))
            self.assertFalse(player.music_active)
            player.close()


if __name__ == '__main__':
    unittest.main()
