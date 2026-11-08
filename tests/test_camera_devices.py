"""Device inventory is read-only; saved device identity survives ordinal changes."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import camera_devices as devices
from camera_devices import CameraDevice, CameraDiscoveryError


class CameraDeviceTests(unittest.TestCase):
    def setUp(self):
        self.inventory = [CameraDevice(0, 'USB Capture', 'device-path-a'),
                          CameraDevice(1, 'OBS Virtual Camera', 'obs-moniker')]

    def test_native_empty_inventory_does_not_probe_or_run_ffmpeg(self):
        with patch.object(devices.sys, 'platform', 'win32'), \
             patch.object(devices, '_enumerate_directshow', return_value=[]), \
             patch.object(devices, '_enumerate_ffmpeg') as fallback:
            self.assertEqual(devices.enumerate_cameras(), [])
            fallback.assert_not_called()

    def test_native_named_inventory_is_returned_without_a_capture_import(self):
        with patch.object(devices.sys, 'platform', 'win32'), \
             patch.object(devices, '_enumerate_directshow', return_value=self.inventory), \
             patch.object(devices, '_enumerate_ffmpeg') as fallback:
            result = devices.enumerate_cameras()
        self.assertEqual(result, self.inventory)
        fallback.assert_not_called()
        self.assertTrue(result[1].is_virtual)
        self.assertFalse(result[0].is_virtual)
        self.assertEqual(result[1].to_dict()['name'], 'OBS Virtual Camera')

    def test_native_failure_uses_registered_device_fallback(self):
        with patch.object(devices.sys, 'platform', 'win32'), \
             patch.object(devices, '_enumerate_directshow', side_effect=CameraDiscoveryError('native failed')), \
             patch.object(devices, '_enumerate_ffmpeg', return_value=self.inventory):
            self.assertEqual(devices.enumerate_cameras(), self.inventory)

    def test_ffmpeg_parser_preserves_video_ordinals_and_skips_audio(self):
        output = '''[dshow] "USB Capture" (none)
[dshow] Alternative name "@device_pnp_usb"
[dshow] "OBS Virtual Camera" (video)
[dshow] Alternative name "@device_sw_obs"
[dshow] "Microphone" (audio)
[dshow] Alternative name "@device_audio_mic"
'''
        result = devices._parse_ffmpeg_devices(output)
        self.assertEqual([(d.index, d.name, d.identifier) for d in result],
                         [(0, 'USB Capture', '@device_pnp_usb'), (1, 'OBS Virtual Camera', '@device_sw_obs')])

    def test_saved_identity_follows_the_same_camera_after_ordinal_reordering(self):
        saved = self.inventory[1].to_dict()
        reordered = [CameraDevice(0, 'OBS Virtual Camera', 'obs-moniker'),
                     CameraDevice(1, 'USB Capture', 'device-path-a')]
        self.assertEqual(devices.resolve_camera(saved, reordered).index, 0)
        self.assertEqual(devices.resolve_camera('OBS Virtual Camera', reordered).identifier, 'obs-moniker')
        self.assertEqual(devices.resolve_camera('obs-moniker', reordered).index, 0)

    def test_missing_saved_identifier_never_opens_an_unrelated_old_index(self):
        with self.assertRaises(CameraDiscoveryError):
            devices.resolve_camera(self.inventory[1].to_dict(), [self.inventory[0]])
        changed_identity = [CameraDevice(1, 'OBS Virtual Camera', 'new-obs-moniker')]
        with self.assertRaises(CameraDiscoveryError):
            devices.resolve_camera(self.inventory[1].to_dict(), changed_identity)

    def test_same_friendly_name_is_ambiguous_but_stable_paths_are_distinct(self):
        duplicated = [CameraDevice(0, 'USB Capture', 'one'), CameraDevice(1, 'USB Capture', 'two')]
        with self.assertRaises(CameraDiscoveryError):
            devices.resolve_camera({'name': 'USB Capture'}, duplicated)
        self.assertEqual(devices.resolve_camera({'identifier': 'two', 'name': 'USB Capture'}, duplicated).index, 1)

    def test_legacy_integer_and_alias_fields_remain_usable(self):
        self.assertEqual(devices.resolve_camera(1, self.inventory), self.inventory[1])
        self.assertEqual(devices.resolve_camera({'device_id': 'obs-moniker', 'device_name': 'OBS'}, self.inventory), self.inventory[1])
        self.assertEqual(devices.resolve_camera({'index': 3}, []).index, 3)
        for value in (True, -1, None, {'identifier': 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                devices.resolve_camera(value, [])

    def test_opening_explicit_selection_uses_dshow_and_current_ordinal(self):
        capture = Mock()
        capture.isOpened.return_value = True
        cv2 = SimpleNamespace(CAP_DSHOW=700, CAP_ANY=0, CAP_PROP_BUFFERSIZE=38,
                              VideoCapture=Mock(return_value=capture))
        result, selected = devices.open_camera(self.inventory[1].to_dict(),
                                              cv2_module=cv2, devices=self.inventory)
        self.assertIs(result, capture)
        self.assertEqual(selected.name, 'OBS Virtual Camera')
        cv2.VideoCapture.assert_called_once_with(1, 700)
        capture.set.assert_called_once_with(38, 1)

    def test_failed_open_releases_capture_and_explains_obs_start_and_exclusive_use(self):
        capture = Mock()
        capture.isOpened.return_value = False
        cv2 = SimpleNamespace(CAP_DSHOW=700, CAP_ANY=0, VideoCapture=Mock(return_value=capture))
        with self.assertRaises(RuntimeError) as error:
            devices.open_camera(self.inventory[1], cv2_module=cv2, devices=self.inventory)
        self.assertIn('OBS', str(error.exception))
        self.assertIn('独占', str(error.exception))
        self.assertIn('启动', str(error.exception))
        capture.release.assert_called_once()

    def test_optional_buffer_setting_cannot_break_a_successfully_opened_stream(self):
        capture = Mock()
        capture.isOpened.return_value = True
        capture.set.side_effect = ValueError('backend does not support this property')
        cv2 = SimpleNamespace(CAP_DSHOW=700, CAP_ANY=0, VideoCapture=Mock(return_value=capture))
        result, _ = devices.open_camera(self.inventory[0], cv2_module=cv2, devices=self.inventory)
        self.assertIs(result, capture)
        capture.release.assert_not_called()


if __name__ == '__main__':
    unittest.main()
