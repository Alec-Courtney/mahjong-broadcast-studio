"""Startup music warming reuses a cache and never starts sound playback."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from music_preload import MusicPreloader


class MusicPreloadTests(unittest.TestCase):
    def test_unchanged_music_is_queued_once_despite_repeated_score_updates(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
            path=Path(folder)/'music.mp3';path.write_bytes(b'ID3test')
            config=dict(postmatch_music_path=str(path),postmatch_music_volume=.4,postmatch_music_enabled=True)
            model=SimpleNamespace(path=Path(folder),state={'broadcast':{'config':config}},changed=Mock(),display_changed=Mock())
            with patch.object(MusicPreloader,'_prepare',return_value=Path(folder)/'cache.wav') as prepare:
                cache=MusicPreloader(model)
                try:
                    cache.future.result()
                    for _ in range(20):cache.schedule()
                    prepare.assert_called_once_with(path,.4)
                    config['postmatch_music_volume']=.5;cache.schedule();cache.future.result()
                    self.assertEqual(prepare.call_count,2)
                    self.assertEqual(path.read_bytes(),b'ID3test')
                finally:cache.close()

    def test_preparation_uses_existing_cache_api_without_playing_or_repeating_effects(self):
        with patch('postmatch_audio.PostMatchAudio') as player:
            MusicPreloader._prepare(Path('music.mp3'),.5)
            player.return_value.prepare.assert_called_once_with(music_path=Path('music.mp3'),music_volume=.5,sfx_enabled=False)
            player.return_value.play.assert_not_called()

    def test_muted_or_unset_music_does_not_queue_work(self):
        model=SimpleNamespace(path=ROOT,state={'broadcast':{'config':{'postmatch_music_path':''}}},changed=Mock(),display_changed=Mock())
        with patch.object(MusicPreloader,'_prepare') as prepare:
            cache=MusicPreloader(model)
            try:self.assertIsNone(cache.future);prepare.assert_not_called()
            finally:cache.close()
