"""Provider contract tests; no camera and no external network needed."""
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from tile_recognition import (OnnxTileRecognizer, RecognitionFrame, TileDetection,
                              TileRecognizer, _nms, normalize_class_name, read_image,
                              recognition_model_path, write_image, VideoSource)
from tile_recognition import recognition_available

try:
    import numpy as np
    import cv2
except ImportError:
    np = cv2 = None


class ClassMappingTests(unittest.TestCase):
    def test_optional_model_prefers_requested_size_and_uses_local_fallback(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            folder = Path(directory)
            names = ('MEDIUM_MODEL', 'SMALL_MODEL', 'NANO_MODEL', 'LEGACY_SMALL_MODEL', 'LEGACY_NANO_MODEL')
            paths = {name: folder / f'{name}.onnx' for name in names}
            with patch.multiple('tile_recognition', **paths):
                with self.assertRaisesRegex(FileNotFoundError, 'INSTALL-Automation.cmd'):
                    OnnxTileRecognizer()
                paths['LEGACY_SMALL_MODEL'].touch()
                paths['LEGACY_NANO_MODEL'].touch()
                self.assertEqual(recognition_model_path(), paths['LEGACY_SMALL_MODEL'])
                self.assertEqual(recognition_model_path('nano'), paths['LEGACY_NANO_MODEL'])
                paths['MEDIUM_MODEL'].touch()
                self.assertEqual(recognition_model_path(), paths['MEDIUM_MODEL'])
                self.assertEqual(recognition_model_path('small'), paths['LEGACY_SMALL_MODEL'])

    def test_only_legal_face_codes_are_accepted(self):
        for tile in ['1m', '9p', '0s', '1z', '7z']:
            self.assertEqual(normalize_class_name(tile.upper()), tile)
        for other in ['UNKNOWN', 'back', 'front', '', '8z', '0z', '10m', '1x']:
            self.assertIsNone(normalize_class_name(other))
        with patch('importlib.util.find_spec', return_value=None):
            self.assertFalse(recognition_available())
        with patch('importlib.util.find_spec', return_value=SimpleNamespace(origin=__file__)):
            self.assertTrue(recognition_available())
        with patch('importlib.util.find_spec', return_value=SimpleNamespace(origin=__file__ + '.uninstalled')):
            self.assertFalse(recognition_available())

    def test_named_camera_descriptor_uses_stable_resolution_and_releases_capture(self):
        from camera_devices import CameraDevice
        descriptor = {'kind': 'camera', 'path': '0', 'identifier': 'obs-stable', 'name': 'OBS Virtual Camera'}
        device = CameraDevice(2, 'OBS Virtual Camera', 'obs-stable')
        capture = MagicMock()
        capture.device = device
        capture.isOpened.return_value = True
        capture.get.side_effect = lambda key: {5: 30, 3: 1280, 4: 720}[key]
        capture.read.return_value = (True, 'frame')
        cv2_runtime = SimpleNamespace(CAP_PROP_FPS=5, CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4)
        with patch('tile_recognition._runtime', return_value=(cv2_runtime, None, None)), patch('shared_camera.subscribe_camera', return_value=capture) as connect:
            with VideoSource(descriptor) as stream:
                self.assertEqual(stream.source['identifier'], 'obs-stable')
                self.assertEqual(stream.source['index'], 2)
                self.assertEqual((stream.width, stream.height, stream.fps), (1280, 720, 30))
                self.assertEqual(stream.read(), (True, 'frame'))
        connect.assert_called_once_with(descriptor, cv2_module=cv2_runtime)
        capture.release.assert_called_once()

    def test_unknown_is_retained_and_tiles_are_spatially_sorted(self):
        frame = RecognitionFrame((TileDetection('2m', .9, (50, 10, 70, 40)),
                                  TileDetection(None, .8, (0, 10, 20, 40), 'UNKNOWN'),
                                  TileDetection('0p', .95, (25, 10, 45, 40))), 100, 50)
        self.assertEqual(frame.tiles, ['0p', '2m'])
        self.assertEqual(frame.unknown_count, 1)
        self.assertEqual(frame.to_dict()['detections'][1]['tile'], None)


@unittest.skipUnless(np is not None and recognition_available(), 'optional vision runtime not installed')
class InferenceContractTests(unittest.TestCase):
    def provider(self, prediction):
        """Controlled output verifies geometry without an untrained camera."""
        class Session:
            def run(inner, _, inputs):
                self.last_tensor = inputs['images']
                return [prediction]
        provider = OnnxTileRecognizer.__new__(OnnxTileRecognizer)
        provider.session = Session()
        provider.input_name = 'images'
        provider.input_width = provider.input_height = 640
        provider.class_names = ('1m', 'UNKNOWN')
        provider.confidence = .5
        provider.iou = .45
        provider.model_path = Path('test.onnx')
        return provider

    def test_nms_removes_cross_class_duplicates_only_when_boxes_overlap(self):
        boxes = np.array([[10, 10, 40, 60], [11, 11, 41, 61], [40, 10, 70, 60]], dtype=float)
        self.assertEqual(_nms(boxes, np.array([.8, .9, .7]), .45), [1, 2])

    def test_roi_letterbox_maps_boxes_back_to_full_image(self):
        # 200x100 crop -> 640x320 with 160px vertical padding.
        prediction = np.array([[[320], [320], [320], [160], [.95], [.1]]], dtype=np.float32)
        provider = self.provider(prediction)
        frame = np.zeros((300, 400, 3), dtype=np.uint8)
        frame[:, :, 0] = 255
        result = provider.recognize(frame, roi=(100, 50, 200, 100))
        self.assertEqual(result.tiles, ['1m'])
        self.assertEqual(result.detections[0].bbox, (150., 75., 250., 125.))
        self.assertEqual((result.width, result.height), (400, 300))
        self.assertEqual(self.last_tensor.shape, (1, 3, 640, 640))
        self.assertAlmostEqual(float(self.last_tensor[0, 2, 320, 320]), 1)
        self.assertAlmostEqual(float(self.last_tensor[0, 0, 320, 320]), 0)
        self.assertIsInstance(provider, TileRecognizer)

    def test_unknown_output_is_exposed_for_rejection(self):
        prediction = np.array([[[320], [320], [100], [200], [.1], [.9]]], dtype=np.float32)
        result = self.provider(prediction).recognize(np.zeros((640, 640, 3), dtype=np.uint8))
        self.assertEqual(result.unknown_count, 1)
        self.assertEqual(result.tiles, [])

    def test_bad_roi_input_shape_and_output_fail_explicitly(self):
        provider = self.provider(np.zeros((1, 6, 0), dtype=np.float32))
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        for roi in [(0, 0, 0, 10), (-1, 0, 30, 30), (180, 0, 30, 30), (0, 0, 10.5, 10)]:
            with self.assertRaises(ValueError):
                provider.recognize(frame, roi=roi)
        with self.assertRaises(ValueError):
            provider.recognize(frame.astype(np.float32))
        with self.assertRaises(ValueError):
            provider.recognize(frame[:, :, 0])
        provider = self.provider(np.zeros((1, 9, 3), dtype=np.float32))
        with self.assertRaises(ValueError):
            provider.recognize(frame)

    def test_low_confidence_is_removed(self):
        provider = self.provider(np.array([[[320], [320], [100], [200], [.49], [.1]]], dtype=np.float32))
        self.assertEqual(provider.recognize(np.zeros((640, 640, 3), dtype=np.uint8)).tiles, [])

    def test_chinese_image_path_and_video_roundtrip(self):
        temporary_root = Path(__file__).resolve().parents[1] / 'tests'
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            path = Path(directory) / '识别_手牌.png'
            frame = np.zeros((64, 96, 3), dtype=np.uint8)
            frame[:, :, 1] = 120
            write_image(path, frame)
            self.assertTrue(np.array_equal(frame, read_image(path)))
            video = Path(directory) / '测试回放.avi'
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 5, (96, 64))
            self.assertTrue(writer.isOpened())
            for _ in range(3):
                writer.write(frame)
            writer.release()
            with VideoSource(video) as source:
                self.assertEqual((source.width, source.height), (96, 64))
                read = [source.read()[0] for _ in range(4)]
            self.assertEqual(read, [True, True, True, False])
            with self.assertRaises(FileNotFoundError):
                VideoSource(Path(directory) / 'missing.avi')


if __name__ == '__main__':
    unittest.main()
