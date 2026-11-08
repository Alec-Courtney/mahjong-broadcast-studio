"""Recognition sources live in existing settings drafts and never in match undo."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from console import Console
from model import Model
from overlays import OverlayManager
from recognition_config import default_recognition_config, normalize_recognition_config
from recognition_settings import _OFFLINE_SAMPLES
from settings_dialog import SettingsDialog

APP = QApplication.instance() or QApplication([])


class RecognitionConfigTests(unittest.TestCase):
    def test_defaults_are_isolated_and_invalid_saved_sources_stay_disabled(self):
        first = default_recognition_config()
        first['sources'][0]['roi'][0] = .5
        self.assertEqual(default_recognition_config()['sources'][0]['roi'], [0, 0, 1, 1])
        result = normalize_recognition_config({'sources': [None] * 4, 'model': 'invalid', 'confidence': float('nan')})
        self.assertEqual(result, default_recognition_config())

    def test_strict_mode_rejects_invalid_enabled_source_and_outside_roi(self):
        for change in ({'enabled': True}, {'roi': [.5, 0, .8, 1]}, {'kind': 'camera', 'enabled': True, 'path': '-1'}, {'roi': [False, 0, 1, 1]}):
            value = default_recognition_config()
            value['sources'][1].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                normalize_recognition_config(value, strict=True)
        camera = default_recognition_config()
        camera['sources'][0].update(enabled=True, kind='camera')
        self.assertEqual(normalize_recognition_config(camera, strict=True)['sources'][0]['path'], '0')

    def test_saved_sample_path_is_relocated_if_the_local_file_exists(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as directory:
            old = Path(directory) / 'old.png'
            moved = Path(directory) / 'moved.png'
            value = default_recognition_config()
            value['sources'][0].update(enabled=True, kind='image', path=str(old))
            with patch.dict('recognition_config._MOVED_SAMPLE_PATHS', {old: moved}, clear=True):
                self.assertEqual(normalize_recognition_config(value)['sources'][0]['path'], str(old))
                moved.touch()
                self.assertEqual(normalize_recognition_config(value)['sources'][0]['path'], str(moved))
                old.touch()
                self.assertEqual(normalize_recognition_config(value)['sources'][0]['path'], str(old))


class RecognitionSettingsTests(unittest.TestCase):
    def setUp(self):
        self.close_answer = patch('settings_window.SettingsWindow.ask_save_changes', return_value=QMessageBox.StandardButton.No)
        self.close_answer.start();self.addCleanup(self.close_answer.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix='recognition-settings-', dir=ROOT / 'tests')
        self.model = Model(self.tmp.name)
        self.model.settings['show_overlays'] = False
        self.manager = OverlayManager(self.model)
        self.main = Console(self.model, self.manager)
        missing_samples = {
            kind: (label, Path(self.tmp.name) / path.name, roi)
            for kind, (label, path, roi) in _OFFLINE_SAMPLES.items()
        }
        with patch.dict(_OFFLINE_SAMPLES, missing_samples):
            self.dialog = SettingsDialog(self.main)

    def local_image_sample(self, seat):
        label, _, roi = _OFFLINE_SAMPLES['image']
        path = Path(self.tmp.name) / 'test-frame.png'
        image = QImage(32, 32, QImage.Format.Format_RGB32)
        image.fill(QColor('#203040'))
        self.assertTrue(image.save(str(path)))
        with patch.dict(_OFFLINE_SAMPLES, {'image': (label, path, roi)}):
            self.dialog._recognition_sample(seat, 'image')
        return str(path)

    def tearDown(self):
        self.dialog.close()
        self.main.close()
        APP.processEvents()
        self.tmp.cleanup()

    def test_sources_are_drafts_until_save_and_cancel_leaves_settings_unchanged(self):
        before = copy.deepcopy((self.model.state, self.model.settings, self.model.undo))
        controls = self.dialog.recognition_sources[2]
        self.assertTrue(controls['samples'].isHidden())
        path = self.local_image_sample(2)
        self.assertTrue(controls['enabled'].isChecked())
        self.assertEqual(controls['kind'].currentData(), 'image')
        self.assertEqual(controls['path'].text(), path)
        self.dialog.reject()
        self.assertEqual((self.model.state, self.model.settings, self.model.undo), before)

    def test_save_persists_per_seat_sources_without_match_or_undo_changes(self):
        before = copy.deepcopy((self.model.state, self.model.undo, self.model.redo))
        self.local_image_sample(1)
        self.dialog.recognition_model.setCurrentIndex(self.dialog.recognition_model.findData('small'))
        self.dialog.recognition_confidence.setValue(.7)
        self.dialog.apply()
        self.assertEqual(self.dialog.error.text(), '')
        value = self.model.settings['recognition_assist']
        self.assertEqual(value['model'], 'small')
        self.assertEqual(value['confidence'], .7)
        self.assertTrue(value['sources'][1]['enabled'])
        self.assertFalse(value['sources'][0]['enabled'])
        self.assertAlmostEqual(value['sources'][1]['roi'][0], .3453)
        self.assertEqual((self.model.state, self.model.undo, self.model.redo), before)
        self.assertEqual(Model(self.tmp.name).settings['recognition_assist'], value)

    def test_invalid_crop_blocks_the_entire_settings_save(self):
        before = copy.deepcopy((self.model.state, self.model.settings))
        self.dialog.theme.setCurrentIndex(1)
        controls = self.dialog.recognition_sources[0]
        controls['roi'][0].setValue(80)
        controls['roi'][2].setValue(50)
        self.dialog.apply()
        self.assertIn('区域', self.dialog.error.text())
        self.assertEqual((self.model.state, self.model.settings), before)

    def test_reload_and_focus_use_existing_settings_page(self):
        value = default_recognition_config()
        value['sources'][3].update(enabled=False, kind='camera', path='2', roi=[.1, .2, .7, .6])
        self.model.setting('recognition_assist', value)
        self.dialog.reload_drafts()
        self.dialog.focus_recognition(3)
        self.assertIs(self.dialog.nav.currentItem(), self.dialog.recognition_entry)
        self.assertIs(self.dialog.pages.currentWidget(), self.dialog.recognition_scroll)
        self.assertEqual(self.dialog.recognition_sources[3]['path'].text(), '2')
        self.assertFalse(self.dialog.recognition_sources[3]['browse'].isEnabled())
        self.assertEqual([field.value() for field in self.dialog.recognition_sources[3]['roi']], [10, 20, 70, 60])

    def test_camera_sources_are_bound_to_fixed_positions_not_dealer_winds(self):
        value = default_recognition_config()
        for index, source in enumerate(value['sources']):
            source.update(kind='camera', path=str(index), identifier=f'camera-position-{index}',
                          name=f'Camera {index + 1}', index=index, backend='dshow')
        self.model.setting('recognition_assist', value)
        self.model.field('dealer', 2)
        self.main.select(3)
        self.dialog.reload_drafts()
        result = self.dialog._recognition_value()
        for index, controls in enumerate(self.dialog.recognition_sources):
            self.assertTrue(controls['group'].title().startswith(f'固定位置 {index + 1} · '))
            self.assertEqual(controls['group'].property('fixedSeatIndex'), index)
            self.assertEqual(result['sources'][index]['identifier'], f'camera-position-{index}')
            self.assertEqual(self.dialog.recognition_preview.player.itemData(index), index)

    def test_sources_follow_the_event_and_new_event_starts_disabled(self):
        from event_store import EventStore
        store = EventStore(self.model)
        first = self.model.event['id']
        value = default_recognition_config()
        value['model'] = 'small'
        value['sources'][0].update(kind='camera', path='3')
        self.model.setting('recognition_assist', value)
        store.create('另一个测试赛事', 'individual_league')
        self.assertEqual(normalize_recognition_config(self.model.settings.get('recognition_assist')), default_recognition_config())
        store.open(first)
        self.dialog.reload_drafts()
        self.assertEqual(self.dialog.recognition_model.currentData(), 'small')
        self.assertEqual(self.dialog.recognition_sources[0]['path'].text(), '3')
        self.assertEqual(self.model.settings['recognition_assist'], value)

    def test_spout_discovery_preserves_fixed_sender_and_keeps_controls_compact(self):
        before = copy.deepcopy((self.model.state, self.model.settings, self.model.undo))
        source = self.dialog.recognition_sources[2]
        source['sender'].setEditText('Table Position 3')
        self.dialog._recognition_senders_ready(['Table Position 1', 'Table Position 2'])
        self.assertEqual(source['path'].text(), 'Table Position 3')
        self.assertIn('暂未发送', source['sender'].toolTip())
        self.assertEqual(self.dialog.recognition_sources[0]['path'].text(), '')
        self.dialog._select_recognition_source(2)
        self.assertEqual(self.dialog.recognition_source_stack.currentIndex(), 2)
        self.assertEqual(self.dialog.recognition_roi_stack.currentIndex(), 2)
        self.assertTrue(self.dialog.recognition_advanced.isHidden())
        self.assertEqual((self.model.state, self.model.settings, self.model.undo), before)
        source['enabled'].setChecked(True)
        value = self.dialog._recognition_value()['sources'][2]
        self.assertEqual((value['kind'], value['path']), ('spout', 'Table Position 3'))
        self.assertNotIn('commit', self.dialog.entry_buttons)
        self.assertNotIn('new_match', self.dialog.entry_buttons)


if __name__ == '__main__':
    unittest.main()
