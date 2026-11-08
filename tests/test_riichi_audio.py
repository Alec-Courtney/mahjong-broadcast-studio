"""One cue per FIFO start, reusable transport, no music or device in tests."""
from array import array
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from riichi_audio import RIICHI_SOUND_DURATION,RIICHI_SOUND_PATH,RiichiAudio,prepare_riichi_sound


class FakePlayer:
    def __init__(self):self.error='';self.calls=[];self.music_path='not configured'
    def play(self,**kwargs):self.calls.append(('play',kwargs));return True
    def finish_effects(self):self.calls.append(('stop',));return True


def pcm(path):
    with wave.open(str(path),'rb') as source:
        result=array('h');result.frombytes(source.readframes(source.getnframes()));return result


class RiichiAudioTests(unittest.TestCase):
    def test_synthesized_asset_is_bounded_stereo_pcm_with_original_cue_timing(self):
        metadata=json.loads((RIICHI_SOUND_PATH.parent/'sources.json').read_text(encoding='utf8'))
        self.assertEqual(metadata['kind'],'original_synthesis')
        self.assertEqual(sha256(RIICHI_SOUND_PATH.read_bytes()).hexdigest(),metadata['packaged_sha256'])
        start, end = metadata['active_window_seconds']
        with wave.open(str(RIICHI_SOUND_PATH),'rb') as source:
            self.assertEqual((source.getnchannels(),source.getsampwidth(),source.getframerate()),(2,2,48000))
            self.assertEqual(source.getnframes()/48000,RIICHI_SOUND_DURATION)
        samples=pcm(RIICHI_SOUND_PATH)
        self.assertEqual(max(abs(value) for value in samples[:round(start*48000)*2]),0)
        active = samples[round(start*48000)*2:round(end*48000)*2]
        self.assertGreater(max(abs(value) for value in active[::2]),1000)
        self.assertGreater(max(abs(value) for value in active[1::2]),1000)
        self.assertEqual(max(abs(value) for value in samples[round(end*48000)*2:]),0)
        self.assertLess(max(abs(value) for value in samples),32767)

    def test_prepared_volume_is_exact_and_cache_is_reused_without_device_access(self):
        with tempfile.TemporaryDirectory(prefix='tmp-riichi-audio-',dir=ROOT/'tests') as temporary:
            path=prepare_riichi_sound(.5,temporary);samples=pcm(path);source=pcm(RIICHI_SOUND_PATH)
            self.assertEqual(samples,array('h',(round(value*.5) for value in source)))
            stamp=path.stat().st_mtime_ns;self.assertEqual(prepare_riichi_sound(.5,temporary),path)
            self.assertEqual(path.stat().st_mtime_ns,stamp)
            for value in (-1,2,True,float('nan')):
                with self.assertRaises(ValueError):prepare_riichi_sound(value,temporary)

    def test_repeated_ticks_or_reused_sequence_ids_never_replay(self):
        with tempfile.TemporaryDirectory(prefix='tmp-riichi-audio-',dir=ROOT/'tests') as temporary:
            fake=FakePlayer();cue=RiichiAudio(cache_dir=temporary,player_factory=lambda:fake,enabled=True)
            for _ in range(10):self.assertTrue(cue.play(1))
            self.assertEqual(fake.calls,[('play',dict(position=0.))]);self.assertIsNone(fake.music_path)
            self.assertTrue(cue.play(2));self.assertTrue(cue.play(1));self.assertEqual(len(fake.calls),2)
            cue.stop();self.assertEqual(fake.calls[-1],('stop',));self.assertTrue(cue.play(2))
            self.assertEqual(fake.calls[-1],('stop',))
            self.assertTrue(cue.play(3));self.assertEqual(fake.calls[-1][0],'play')
            cue.close();self.assertFalse(cue.play(4))

    def test_muting_or_invalid_sequences_do_not_open_any_transport(self):
        with patch('riichi_audio.PostMatchAudio') as factory:
            cue=RiichiAudio(volume=0);self.assertTrue(cue.play(1));factory.assert_not_called()
            cue=RiichiAudio();cue.enabled=False;self.assertTrue(cue.play(1));factory.assert_not_called()
            for value in (None,True,0,-1,'1'):
                self.assertFalse(cue.play(value));factory.assert_not_called()
            with patch.dict(os.environ,{'QT_QPA_PLATFORM':'offscreen'}):
                cue=RiichiAudio();self.assertFalse(cue.enabled);self.assertTrue(cue.play(1))
                self.assertEqual(cue.last_sequence_id,1);factory.assert_not_called()

    def test_playback_errors_do_not_escape_into_the_gameplay_command(self):
        with tempfile.TemporaryDirectory(prefix='tmp-riichi-audio-',dir=ROOT/'tests') as temporary:
            fake=FakePlayer();fake.error='device unavailable';fake.play=lambda **kwargs:False
            cue=RiichiAudio(cache_dir=temporary,player_factory=lambda:fake,enabled=True)
            self.assertFalse(cue.play(1));self.assertIn('device unavailable',cue.error)
            self.assertTrue(cue.play(1))


if __name__=='__main__':unittest.main()
