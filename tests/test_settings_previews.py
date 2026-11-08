"""The settings previews render locally and leave the live programme alone."""
import copy
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from broadcast_console import BroadcastDialog, POSTMATCH_AUDITION_SECONDS
from event_store import EventStore
from model import Model
from overlays import OverlayManager
from postmatch_audio import ASSET_DIR, PostMatchAudio
from riichi_effect import RIICHI_DURATION
from ui_widgets import selected

APP = QApplication.instance() or QApplication([])
TEST_MUSIC = ASSET_DIR / 'layout_sweep.wav'


class SettingsPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tmp-previews-', dir=ROOT / 'tests')
        self.root = Path(self.temp.name)
        self.model = Model(self.root / 'event-data')
        self.store = EventStore(self.model)
        self.model.settings['show_overlays'] = False
        self.manager = OverlayManager(self.model)
        self.owner = QWidget()
        self.owner.model, self.owner.manager = self.model, self.manager
        self.dialog = BroadcastDialog(self.owner, workspace_roster=True)
        self.dialog.show()
        APP.processEvents()

    def tearDown(self):
        self.dialog._allow_close = True
        self.dialog.close()
        self.dialog.deleteLater()
        self.owner.deleteLater()
        self.manager.close()
        APP.processEvents()
        self.temp.cleanup()

    def test_riichi_click_previews_once_and_leaving_or_switching_stops_it(self):
        panel = self.dialog
        preview = panel.riichi_preview
        self.assertIsNone(preview.snapshot)
        selected(panel.riichi_preview_seat, 2)
        self.assertFalse(preview.timer.isActive())
        before = copy.deepcopy(self.model.state)
        clock = SimpleNamespace(start=lambda: None, elapsed=lambda: 600)
        preview.clock = clock
        idle_image = preview.grab().toImage()
        panel.riichi_preview_button.click()
        self.assertEqual(preview.snapshot.player_id, self.model.state['players'][2]['id'])
        self.assertTrue(preview.timer.isActive())
        self.assertNotEqual(preview.grab().toImage(), idle_image)
        self.assertFalse(self.manager.riichi_queue.busy)
        self.assertEqual(self.model.state, before)
        self.assertEqual(panel.dirty_sections(), [])

        clock.elapsed = lambda: round(RIICHI_DURATION * 1000)
        preview.advance()
        self.assertFalse(preview.timer.isActive())
        self.assertIsNone(preview.snapshot)
        panel.riichi_preview_button.click()
        panel.select_category('qualification')
        self.assertIsNone(preview.snapshot)
        panel.select_category('presentation')
        self.assertIsNone(preview.snapshot)
        panel.riichi_preview_button.click()
        self.store.create('另一场赛事', 'team_league')
        self.assertFalse(preview.timer.isActive())
        self.assertIsNone(preview.snapshot)

    def test_qualification_preview_renders_pending_title_manual_content_and_layout(self):
        panel = self.dialog
        panel.select_category('qualification')
        before = copy.deepcopy(self.model.state)
        initial = panel.qualification_preview.grab().toImage()
        selected(panel.qualification_mode, 'manual')
        panel.qualification_title.setText('晋级预览标题')
        panel.manual_conditions.item(0, 1).setText('自摸 2000 / 4000')
        self.assertNotEqual(panel.qualification_preview.grab().toImage(), initial)
        selected(panel.qualification_layout, 'sidebar')
        self.assertEqual(panel.qualification_preview.design_size, (400, 650))
        selected(panel.qualification_focus, self.model.state['players'][0]['id'])
        with patch('broadcast_artwork.draw_extension') as draw:
            panel.draw_qualification_preview(None)
        _, kind, draft = draw.call_args.args
        self.assertEqual(kind, 'qualification')
        from qualification_presentation import qualification_data
        first = qualification_data(draft)['rows'][0]
        self.assertEqual(first['title'], '晋级预览标题')
        self.assertEqual(first['condition'], '自摸 2000 / 4000')
        self.assertEqual(self.model.state, before)

    def test_title_and_logo_previews_follow_imported_event_media_without_cueing(self):
        panel = self.dialog
        old_logo = panel.corner_preview.grab().toImage()
        source = self.root / 'test-card.png'
        image = QImage(160, 90, QImage.Format.Format_RGB32)
        color = QColor('#f03389')
        image.fill(color)
        self.assertTrue(image.save(str(source)))
        self.model.configure_opening({'opening_title': source})
        self.model.configure_corner_header({'corner_logo': str(source)})
        rendered = panel.opening_title_preview.grab().toImage()
        self.assertEqual(rendered.pixelColor(rendered.width() // 2, rendered.height() // 2), color)
        self.assertNotEqual(panel.corner_preview.grab().toImage(), old_logo)
        self.assertFalse(self.manager.opening.active)
        self.store.create('另一个没有素材的赛事', 'individual_league')
        fresh = panel.opening_title_preview.grab().toImage()
        self.assertNotEqual(fresh.pixelColor(fresh.width() // 2, fresh.height() // 2), color)

    def test_music_audition_uses_pending_selection_and_stops_on_tab_change(self):
        panel = self.dialog
        panel.set_postmatch_media('music', str(TEST_MUSIC))
        panel.postmatch_music_volume.setValue(52)
        before = copy.deepcopy(self.model.state)
        prepared = []
        audio = SimpleNamespace(error='', prepare=lambda **options: prepared.append(options))
        audio.set_music_enabled = lambda enabled: setattr(audio, 'playing', enabled)
        audio.close = lambda: setattr(audio, 'playing', False)
        audio.playing = False
        self.assertIsNone(panel._postmatch_audition)
        with patch('postmatch_audio.PostMatchAudio', return_value=audio):
            panel.postmatch_audition_button.click()
            for _ in range(100):
                if audio.playing:
                    break
                QTest.qWait(10)
        self.assertTrue(audio.playing)
        self.assertEqual(prepared[0]['music_volume'], .52)
        self.assertEqual(Path(prepared[0]['music_path']), TEST_MUSIC)
        self.assertEqual(prepared[0]['music_preview_seconds'], POSTMATCH_AUDITION_SECONDS)
        self.assertFalse(prepared[0]['sfx_enabled'])
        self.assertTrue(panel._audition_timer.isActive())
        self.assertFalse(self.manager.programme.music.enabled)
        self.assertEqual(self.model.state, before)
        panel.select_category('commentary')
        self.assertFalse(audio.playing)
        self.assertFalse(panel._audition_timer.isActive())

    def test_audio_preview_prepares_a_bounded_music_only_clip(self):
        player = PostMatchAudio(cache_dir=self.root / 'audio')
        target = player.prepare(music_path=TEST_MUSIC, sfx_enabled=False, music_preview_seconds=1.)
        self.assertIsNotNone(target, player.error)
        self.assertIsNone(player.path)
        with wave.open(str(target), 'rb') as stream:
            self.assertEqual(stream.getnframes(), stream.getframerate())

    def test_audition_without_a_selected_track_keeps_live_audio_idle(self):
        panel = self.dialog
        with patch('postmatch_audio.PostMatchAudio') as transport:
            panel.postmatch_audition_button.click()
            transport.assert_not_called()
        self.assertIsNone(panel._postmatch_audition)
        self.assertIn('选择本地音乐', panel.postmatch_audition_status.text())
        self.assertFalse(self.manager.programme.music.enabled)


if __name__ == '__main__':
    unittest.main()
