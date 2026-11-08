"""Named-device settings, live diagnostics, drag ROI and camera handoff lifecycle."""
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
sys.path.insert(0, str(ROOT / 'src'))

from PySide6.QtCore import QObject, QPoint, QThread, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from app_paths import LOCAL_RECOGNITION_SAMPLES
from camera_devices import CameraDevice
from console import Console
from model import Model
from overlays import OverlayManager
from recognition_config import default_recognition_config, normalize_recognition_config
from recognition_runtime import recognition_available
from recognition_source_editor import RecognitionPreviewSession, SourcePreviewCanvas
from settings_dialog import SettingsDialog
from tile_recognition import MEDIUM_MODEL, RecognitionFrame, TileDetection

APP = QApplication.instance() or QApplication([])
SAMPLE = LOCAL_RECOGNITION_SAMPLES / 'mleague-hand-hd.jpg'


def pump_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(.01)
    APP.processEvents()
    return predicate()


class CaptureStub(QThread):
    frame_ready = Signal(object, object)
    failed = Signal(str)
    status_changed = Signal(str)

    def __init__(self, source, *, image=False, model_path=None, confidence=.55, roi=None, parent=None):
        super().__init__(parent)
        self.source, self.image, self.roi = source, image, roi
        self.entered = threading.Event()
        self.release = threading.Event()

    def run(self):
        self.entered.set()
        # The test owner controls the native-read release. A wall-clock
        # timeout could finish before the UI reaches its stop assertion under
        # a busy full-suite event loop, which is a valid earlier completion.
        self.release.wait()
        image = QImage(400, 200, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.gray)
        result = RecognitionFrame((TileDetection('3m', .9, (1, 1, 25, 40)),), 400, 200, provider='camera-stub')
        self.frame_ready.emit(image, result)


class FakeCoordinator(QObject):
    preview_received = Signal(int, object, object)
    source_state_changed = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.workers, self.latest_previews = {}, {}
        self.paused, self.resumed = [], []

    def pause_for_preview(self, player):
        self.paused.append(player)

    def resume_after_preview(self, player):
        self.resumed.append(player)


class PreviewGeometryTests(unittest.TestCase):
    def test_drag_selection_uses_image_coordinates_with_letterboxing(self):
        canvas = SourcePreviewCanvas()
        canvas.resize(400, 300)
        image = QImage(400, 200, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.black)
        canvas.set_image(image)
        canvas.show()
        APP.processEvents()
        changes = []
        canvas.roi_changed.connect(changes.append)
        try:
            QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(40, 70))
            QTest.mouseMove(canvas, QPoint(240, 170))
            QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(240, 170))
            self.assertEqual(len(changes), 1)
            for actual, expected in zip(changes[0], (.1, .1, .5, .5)):
                self.assertAlmostEqual(actual, expected)
        finally:
            canvas.close()

    def test_optional_named_identifier_survives_normalization(self):
        value = default_recognition_config()
        descriptor = CameraDevice(2, 'OBS Virtual Camera', 'stable-obs-id').to_dict()
        value['sources'][0].update(kind='camera', enabled=True, path='2', **descriptor)
        normalized = normalize_recognition_config(value, strict=True)
        for key in ('identifier', 'name', 'index', 'backend'):
            self.assertEqual(normalized['sources'][0][key], descriptor[key])


class CameraSettingsPreviewTests(unittest.TestCase):
    def setUp(self):
        self.close_answer = patch('settings_window.SettingsWindow.ask_save_changes', return_value=QMessageBox.StandardButton.No)
        self.close_answer.start();self.addCleanup(self.close_answer.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix='source-editor-', dir=ROOT / 'tests')
        self.model = Model(self.tmp.name)
        self.model.settings['show_overlays'] = False
        self.manager = OverlayManager(self.model)
        self.main = Console(self.model, self.manager)
        self.dialog = SettingsDialog(self.main)

    def tearDown(self):
        session = self.dialog.recognition_preview_session
        if session.worker and isinstance(session.worker, CaptureStub):
            session.worker.release.set()
            session.worker.wait(3000)
        self.dialog.close()
        self.main.close()
        APP.processEvents()
        self.tmp.cleanup()

    def test_named_devices_prefer_obs_without_enabling_or_overwriting_file_paths(self):
        file_path = str(Path(self.tmp.name) / 'user-selected.png')
        with patch('recognition_settings.QFileDialog.getOpenFileName', return_value=(file_path, '')):
            self.dialog._choose_recognition_source(0)
        self.assertEqual(self.dialog.recognition_sources[0]['kind'].currentData(), 'image')
        before = copy.deepcopy((self.model.state, self.model.settings))
        self.dialog._recognition_devices_ready([
            CameraDevice(0, 'USB Camera', 'usb-id'), CameraDevice(2, 'OBS Virtual Camera', 'obs-id')])
        self.assertEqual(self.dialog.recognition_sources[0]['path'].text(), file_path)
        camera = self.dialog.recognition_sources[1]
        camera['kind'].setCurrentIndex(camera['kind'].findData('camera'))
        self.assertEqual(camera['device'].currentText(), 'OBS Virtual Camera')
        self.assertEqual(camera['device_data']['identifier'], 'obs-id')
        self.assertEqual(camera['path'].text(), '2')
        self.assertFalse(camera['enabled'].isChecked())
        self.assertEqual((self.model.state, self.model.settings), before)

    def test_spout_preview_uses_exact_sender_and_marks_paused_feed_stale(self):
        before = copy.deepcopy((self.model.state, self.model.settings, self.model.undo))
        self.dialog.recognition_preview_session.worker_factory = CaptureStub
        source = self.dialog.recognition_sources[0]
        source['sender'].setEditText('Fixed Seat A')
        self.dialog._start_recognition_preview(0)
        worker = self.dialog.recognition_preview_session.worker
        self.assertIsNotNone(worker)
        self.assertTrue(pump_until(worker.entered.is_set))
        self.assertEqual(worker.source['kind'], 'spout')
        self.assertEqual(worker.source['path'], 'Fixed Seat A')
        image = QImage(400, 200, QImage.Format.Format_RGB32)
        result = RecognitionFrame((TileDetection('7z', .91, (0, 0, 25, 40)),), 400, 200, provider='spout-stub')
        self.dialog._recognition_diagnostic_frame(0, image, result)
        self.dialog._recognition_saved_state(0, 'previous source disconnected')
        self.assertFalse(source['result'].stale)
        self.assertIsNotNone(self.dialog.recognition_preview.preview.image)
        worker.status_changed.emit('来源暂未发送，等待重连')
        self.assertTrue(pump_until(lambda: source['result'].stale))
        self.assertIsNone(self.dialog.recognition_preview.preview.image)
        self.assertTrue(self.dialog.recognition_preview_session.running)
        self.assertEqual((self.model.state, self.model.settings, self.model.undo), before)

    def test_missing_saved_device_is_visible_and_never_replaced_by_same_ordinal(self):
        value = default_recognition_config()
        value['sources'][0].update(kind='camera', path='0', identifier='missing-id', name='Old Camera', index=0, backend='dshow')
        self.model.setting('recognition_assist', value)
        self.dialog.reload_drafts()
        self.dialog._recognition_devices_ready([CameraDevice(0, 'New Camera', 'new-id')])
        source = self.dialog.recognition_sources[0]
        self.assertIn('未连接', source['device'].currentText())
        self.assertEqual(self.dialog._recognition_value()['sources'][0]['identifier'], 'missing-id')

    def test_dragging_roi_changes_only_the_unsaved_selected_source(self):
        before = copy.deepcopy((self.model.state, self.model.settings, self.model.undo))
        self.dialog._drag_recognition_roi(2, (.2, .1, .6, .7))
        self.assertEqual([field.value() for field in self.dialog.recognition_sources[2]['roi']], [20, 10, 60, 70])
        self.assertEqual([field.value() for field in self.dialog.recognition_sources[0]['roi']], [0, 0, 100, 100])
        self.assertEqual((self.model.state, self.model.settings, self.model.undo), before)

    def test_saved_stream_updates_the_corresponding_camera_tile_result(self):
        image = QImage(400, 200, QImage.Format.Format_RGB32)
        result = RecognitionFrame((TileDetection('7z', .91, (0, 0, 25, 40)),), 400, 200, provider='saved-cam')
        self.dialog._recognition_saved_frame(3, image, result)
        self.dialog._recognition_saved_state(3, '1 张 · 12 ms')
        self.assertEqual(self.dialog.recognition_sources[3]['result'].tiles, ['7z'])
        self.assertFalse(self.dialog.recognition_sources[3]['result'].stale)
        self.assertEqual(self.dialog.recognition_sources[0]['result'].tiles, [])

    def test_preview_stop_waits_for_camera_release_before_resuming_saved_stream(self):
        original = self.main.inline_automation
        coordinator = FakeCoordinator()
        self.main.inline_automation = coordinator
        self.dialog.recognition_preview_session.worker_factory = CaptureStub
        source = self.dialog.recognition_sources[0]
        source['kind'].setCurrentIndex(source['kind'].findData('camera'))
        source['path'].setText('0')
        worker = None
        try:
            self.dialog._start_recognition_preview(0)
            worker = self.dialog.recognition_preview_session.worker
            self.assertIsNotNone(worker)
            self.assertTrue(pump_until(worker.entered.is_set))
            self.assertEqual(coordinator.paused, [0])
            self.assertTrue(worker.isRunning())
            self.dialog._stop_recognition_preview(0)
            self.assertEqual(coordinator.resumed, [])
            worker.release.set()
            self.assertTrue(pump_until(lambda: not worker.isRunning()))
            self.assertTrue(pump_until(lambda: coordinator.resumed == [0]))
            self.assertEqual(self.dialog.recognition_sources[0]['result'].tiles, [])
        finally:
            if worker is not None:
                worker.release.set()
                worker.wait(3000)
            self.main.inline_automation = original

    def test_shared_capture_preview_does_not_pause_or_wait_for_production_workers(self):
        original = self.main.inline_automation
        coordinator = FakeCoordinator()
        coordinator.supports_parallel_preview = True
        self.main.inline_automation = coordinator
        self.dialog.recognition_preview_session.worker_factory = CaptureStub
        production = CaptureStub(0, parent=self.dialog)
        coordinator.workers[0] = production
        controls = self.dialog.recognition_sources[0]
        controls['kind'].setCurrentIndex(controls['kind'].findData('camera'))
        controls['path'].setText('0')
        diagnostic = None
        production.start()
        try:
            self.assertTrue(pump_until(production.entered.is_set))
            self.dialog._start_recognition_preview(0)
            diagnostic = self.dialog.recognition_preview_session.worker
            self.assertIsNotNone(diagnostic)
            self.assertIsNot(diagnostic, production)
            self.assertTrue(pump_until(diagnostic.entered.is_set))
            self.assertTrue(production.isRunning())
            self.assertEqual(coordinator.paused, [])
            self.dialog._stop_recognition_preview(0)
            diagnostic.release.set()
            self.assertTrue(pump_until(lambda: not diagnostic.isRunning()))
            self.assertTrue(production.isRunning())
            self.assertEqual(coordinator.resumed, [])
        finally:
            if diagnostic is not None:
                diagnostic.release.set()
                diagnostic.wait(3000)
            production.release.set()
            production.wait(3000)
            self.main.inline_automation = original

    def test_changing_source_cancels_preview_and_ignores_the_old_camera_frame(self):
        original = self.main.inline_automation
        coordinator = FakeCoordinator()
        self.main.inline_automation = coordinator
        self.dialog.recognition_preview_session.worker_factory = CaptureStub
        source = self.dialog.recognition_sources[0]
        source['kind'].setCurrentIndex(source['kind'].findData('camera'))
        source['path'].setText('0')
        worker = None
        try:
            self.dialog._start_recognition_preview(0)
            worker = self.dialog.recognition_preview_session.worker
            self.assertTrue(pump_until(worker.entered.is_set))
            source['kind'].setCurrentIndex(source['kind'].findData('video'))
            source['path'].setText('changed-source.avi')
            worker.failed.emit('obsolete camera disconnect')
            worker.release.set()
            self.assertTrue(pump_until(lambda: not worker.isRunning()))
            self.assertEqual(self.dialog.recognition_sources[0]['result'].tiles, [])
            self.assertIsNone(self.dialog.recognition_preview.preview.image)
            self.assertNotIn('obsolete camera', self.dialog._recognition_preview_error)
            self.assertTrue(pump_until(lambda: coordinator.resumed == [0]))
        finally:
            if worker is not None:
                worker.release.set()
                worker.wait(3000)
            self.main.inline_automation = original

    def test_cancel_during_saved_camera_handoff_waits_until_old_capture_releases(self):
        original = self.main.inline_automation
        coordinator = FakeCoordinator()
        self.main.inline_automation = coordinator
        self.dialog.recognition_preview_session.worker_factory = CaptureStub
        old = CaptureStub(0, parent=self.dialog)
        coordinator.workers[0] = old
        source = self.dialog.recognition_sources[0]
        source['kind'].setCurrentIndex(source['kind'].findData('camera'))
        source['path'].setText('0')
        old.start()
        try:
            self.assertTrue(pump_until(old.entered.is_set))
            self.dialog._start_recognition_preview(0)
            self.assertIsNone(self.dialog.recognition_preview_session.worker)
            self.dialog._stop_recognition_preview(0)
            self.assertEqual(coordinator.resumed, [])
            old.release.set()
            self.assertTrue(pump_until(lambda: coordinator.resumed == [0]))
            self.assertIsNone(self.dialog.recognition_preview_session.worker)
        finally:
            old.release.set()
            old.wait(3000)
            self.main.inline_automation = original

    @unittest.skipUnless(recognition_available() and SAMPLE.exists() and MEDIUM_MODEL.exists(),
                         'optional vision runtime, model or source not installed')
    def test_actual_image_preview_produces_tiles_without_any_game_or_settings_write(self):
        self.dialog._recognition_sample(0, 'image')
        before = copy.deepcopy((self.model.state, self.model.settings, self.model.undo))
        self.dialog._start_recognition_preview(0)
        session = self.dialog.recognition_preview_session
        self.assertTrue(pump_until(lambda: session.worker is not None and not session.worker.isRunning(), 30))
        self.assertIsNotNone(self.dialog.recognition_preview.preview.image)
        self.assertEqual(len(self.dialog.recognition_sources[0]['result'].tiles), 13)
        self.assertEqual((self.model.state, self.model.settings, self.model.undo), before)


if __name__ == '__main__':
    unittest.main()
