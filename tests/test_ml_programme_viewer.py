"""The final player remains offline, portable and faithful to its actual media."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import build_ml_programme_viewer as viewer


class ProgrammeViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tmp-ml-viewer-', dir=viewer.ROOT/'tests')
        self.folder = Path(self.temp.name)
        (self.folder / '关键帧').mkdir()
        (self.folder / '关键帧/title.png').write_bytes(b'image')
        (self.folder / 'film.mp4').write_bytes(b'verified-media')
        (self.folder / 'ML_演示数据.json').write_text('{}', encoding='utf8')
        self.chapters = dict(video='film.mp4', title='ML <开场>', duration_seconds=151.483333333,
            chapters=[dict(id='opening-title', start=0., title='<标题>', keyframe='关键帧/title.png'),
                      dict(id='live', start=18., title='直播', features=['宝牌']),
                      dict(id='post', start=112., title='赛后')])
        self.verification = dict(video_rendered=True, real_ml_identity_data=True,
            no_music_in_live_match=True, live_interval=[18.,112.], riichi_effect_starts=[{}]*5)
        self.media = dict(duration_seconds=151.483333333, fps=60., width=1920, height=1080, audio_present=True)
        self.write_inputs()

    def tearDown(self):
        self.temp.cleanup()

    def write_inputs(self):
        (self.folder/'chapters.json').write_text(json.dumps(self.chapters), encoding='utf8')
        (self.folder/'verification.json').write_text(json.dumps(self.verification), encoding='utf8')

    def test_offline_player_embeds_all_chapters_and_sound_gesture(self):
        with patch.object(viewer, 'probe_video', return_value=self.media):
            report = viewer.build(self.folder)
        page = (self.folder/viewer.PAGE).read_text(encoding='utf8')
        self.assertEqual(report['chapters'], 3)
        self.assertEqual(report['missing_links'], [])
        self.assertEqual(report['external_dependencies'], [])
        self.assertIn('autoplay muted', page)
        self.assertIn("film.muted=!film.muted", page)
        self.assertIn("film.play().catch", page)
        self.assertIn('18–112秒对局阶段没有背景音乐', page)
        self.assertIn('ML &lt;开场&gt;', page)
        self.assertNotIn('fetch(', page)
        self.assertNotIn(str(self.folder), page)
        self.assertIn('一次5→4', (self.folder/'README.md').read_text(encoding='utf8'))

    def test_silent_or_mismatched_media_cannot_be_presented_as_complete(self):
        for change in (dict(audio_present=False), dict(duration_seconds=150.), dict(fps=30.)):
            with self.subTest(change=change), patch.object(viewer, 'probe_video', return_value=self.media|change):
                with self.assertRaises(ValueError):
                    viewer.build(self.folder)
        self.assertFalse((self.folder/viewer.PAGE).exists())

    def test_chapter_media_cannot_escape_the_portable_directory(self):
        self.chapters['chapters'][0]['keyframe'] = '../outside.png'
        self.write_inputs()
        with patch.object(viewer, 'probe_video', return_value=self.media):
            with self.assertRaises(ValueError):
                viewer.build(self.folder)
        self.assertFalse((self.folder/viewer.PAGE).exists())


if __name__ == '__main__':
    unittest.main()
