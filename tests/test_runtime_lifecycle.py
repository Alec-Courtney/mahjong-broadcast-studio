"""Finished recognition and failed media startup release their owned resources."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests')]
from PySide6.QtCore import QEvent, QThread, Signal
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid
from console import Console
from model import Model
from overlays import OverlayManager
from postmatch_audio import ASSET_DIR
from programme import ProgrammeMusic
from tile_recognition import VideoSource

APP = QApplication.instance() or QApplication([])


def until(predicate, timeout=2):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if predicate():return True
        time.sleep(.002)
    return predicate()


class ShortWorker(QThread):
    frame_ready_tagged = Signal(object, object, int)
    failed = Signal(str)
    status_changed = Signal(str)

    def __init__(self, source, *, confidence=.55, parent=None, **_):
        super().__init__(parent)
        self.confidence = confidence
        self.context_revision = 0
        self.release = threading.Event()
        self.started_work = threading.Event()

    def run(self):
        self.started_work.set()
        self.release.wait(2)


class RecognitionLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'tests')
        self.model = Model(self.temp.name)
        self.model.settings.update(show_overlays=False)
        self.manager = OverlayManager(self.model)
        self.console = Console(self.model, self.manager)
        self.assist = self.console.inline_automation
        APP.processEvents()
        self.workers = []
        self.patches = [patch('recognition_runtime.recognition_available', return_value=True),
                        patch('recognition_worker.RecognitionWorker', ShortWorker)]
        for active in self.patches:active.start()

    def tearDown(self):
        for worker in self.workers:
            if isValid(worker):worker.release.set();worker.wait(3000)
        until(lambda: not self.assist.worker_info, timeout=.2)
        self.console.close()
        APP.processEvents()
        for active in reversed(self.patches):active.stop()
        self.temp.cleanup()

    def start(self):
        worker = self.assist.start_source(0, dict(enabled=True, kind='image',
            path='unused-image.png', roi=[0,0,1,1]))
        self.workers.append(worker)
        self.assertTrue(worker.started_work.wait(1))
        return worker

    def test_finished_recognizers_are_removed_and_native_objects_are_deleted(self):
        for _ in range(6):
            worker = self.start()
            worker.release.set()
            self.assertTrue(until(lambda: not isValid(worker) or not worker.isRunning()))
            self.assertTrue(until(lambda: worker not in self.assist.worker_info))
            self.assertNotIn(0, self.assist.workers)
            self.assertTrue(until(lambda: not isValid(worker)))
        self.assertEqual(self.assist.findChildren(QThread), [])

    def test_old_worker_finish_cannot_remove_or_invalidate_replacement(self):
        old = self.start()
        current = self.start()
        old.release.set()
        self.assertTrue(until(lambda: old not in self.assist.worker_info))
        self.assertIs(self.assist.workers[0], current)
        self.assertIn(current, self.assist.worker_info)
        self.assertTrue(current.isRunning())

    def test_application_exit_waits_for_detached_worker_with_a_deadline(self):
        from recognition_source_editor import wait_for_drained_workers
        worker = self.start()
        self.assertTrue(self.console.close())
        self.assertTrue(worker.isRunning())
        self.assertFalse(wait_for_drained_workers(timeout_ms=5))
        worker.release.set()
        self.assertTrue(wait_for_drained_workers(timeout_ms=1000))
        self.assertFalse(worker.isRunning())


class ConsoleShutdownTests(unittest.TestCase):
    def test_destroyed_console_cancels_pending_hand_editor_reveal(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
            model = Model(directory)
            model.settings.update(show_overlays=False, topmost=False)
            manager = OverlayManager(model)
            console = Console(model, manager)
            try:
                console.show()
                APP.processEvents()
                with patch.object(console, 'reveal_hand_editor') as reveal:
                    console.edit_recognized_hand(0)
                    self.assertTrue(console.close())
                    console.deleteLater()
                    APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    self.assertFalse(isValid(console))
                    APP.processEvents()
                    reveal.assert_not_called()
            finally:
                if isValid(console): console.close()
                manager.close(persist=False)


class FailedMediaStartupTests(unittest.TestCase):
    def test_overlay_teardown_after_preflight_does_not_write_again_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
            model = Model(directory)
            baseline = model.receivers('2changed(PyObject,PyObject,QString)')
            manager = OverlayManager(model)
            with patch.object(model, 'save', side_effect=OSError('write unavailable')) as save:
                manager.close(persist=False)
                manager.close(persist=False)
                save.assert_not_called()
            self.assertTrue(manager.closing)
            self.assertFalse(manager.timer.isActive())
            self.assertFalse(manager.sidebar_timer.isActive())
            self.assertEqual(model.receivers('2changed(PyObject,PyObject,QString)'), baseline)

    def test_failed_visibility_cues_do_not_change_the_live_picture_or_preferences(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
            model = Model(directory)
            manager = OverlayManager(model)
            try:
                actions = [lambda: manager.set_visible('round', not manager.enabled.get('round', True)),
                           lambda: manager.set_visible('players', False),
                           lambda: manager.set_extension_visible('commentary', True),
                           lambda: manager.set_extension_visible('chart', True)]
                for action in actions:
                    before = copy.deepcopy((model.settings, manager.enabled, manager.extension_transitions,
                                            manager.stage, manager.commentary_deadline, manager.chart_started))
                    with patch.object(model.repository, 'save', side_effect=OSError('write unavailable')):
                        with self.assertRaisesRegex(OSError, 'write unavailable'):action()
                    self.assertEqual((model.settings, manager.enabled, manager.extension_transitions,
                                      manager.stage, manager.commentary_deadline, manager.chart_started), before)
            finally:manager.close(persist=False)

    def test_failed_scene_save_does_not_stop_a_preparing_programme(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
            model = Model(directory)
            manager = OverlayManager(model)
            try:
                manager.postmatch.state = 'preparing'
                before = copy.deepcopy((model.settings, manager.enabled, manager.extension_transitions))
                with patch.object(model.repository, 'save', side_effect=OSError('write unavailable')), \
                     patch.object(manager.postmatch, 'stop') as stop:
                    with self.assertRaisesRegex(OSError, 'write unavailable'):manager.apply_scene('clean')
                    stop.assert_not_called()
                self.assertEqual((model.settings, manager.enabled, manager.extension_transitions), before)
                self.assertTrue(manager.postmatch.preparing)
            finally:manager.close(persist=False)

    def test_failed_final_mode_save_does_not_cancel_animation_or_change_the_mode(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
            model = Model(directory)
            model.field('roundIndex', 7); model.field('dealer', 3)
            manager = OverlayManager(model)
            try:
                model.settle(dict(type='ron', winner=0, loser=1, han=3, fu=40), None)
                before = copy.deepcopy((model.settings, manager.stage, manager.pending_win,
                                        manager.pending_next, manager.animation.active))
                with patch.object(model.repository, 'save', side_effect=OSError('write unavailable')):
                    with self.assertRaisesRegex(OSError, 'write unavailable'):manager.set_final_points_mode('pt')
                self.assertEqual((model.settings, manager.stage, manager.pending_win,
                                  manager.pending_next, manager.animation.active), before)
            finally:manager.close(persist=False)

    def test_successful_display_cue_publishes_only_durable_settings(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
            model = Model(directory)
            manager = OverlayManager(model)
            snapshots = []
            model.display_changed.connect(lambda: snapshots.append(json.loads(
                (model.path/'session.json').read_text(encoding='utf-8'))['settings']))
            try:
                manager.set_visible('round', False)
                self.assertFalse(manager.enabled['round'])
                self.assertTrue(snapshots)
                self.assertTrue(all(not state['visible']['round'] for state in snapshots))
                self.assertFalse(Model(directory).settings['visible']['round'])
            finally:manager.close(persist=False)

    def test_video_metadata_failure_releases_the_already_open_capture(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
            video = Path(directory)/'input.avi'
            video.touch()
            capture = Mock()
            capture.isOpened.return_value = True
            capture.get.side_effect = RuntimeError('metadata unavailable')
            cv2 = Mock()
            cv2.VideoCapture.return_value = capture
            with patch('tile_recognition._runtime', return_value=(cv2,None,None)):
                with self.assertRaisesRegex(RuntimeError, 'metadata unavailable'):
                    VideoSource(video)
            capture.release.assert_called_once_with()

    def test_music_constructor_failure_leaves_preparing_and_reports_the_error(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
            model = Model(directory)
            model.configure_postmatch({'postmatch_music_path': str(ASSET_DIR / 'layout_sweep.wav')})
            music = ProgrammeMusic(model, audio_factory=Mock(side_effect=OSError('audio unavailable')))
            errors = []
            music.failed.connect(errors.append)
            try:
                music.set_enabled(True)
                self.assertTrue(until(lambda: bool(errors)))
                self.assertEqual(errors, ['audio unavailable'])
                self.assertFalse(music.preparing)
                self.assertFalse(music.enabled)
                self.assertIsNone(music.audio)
            finally:music.close()


if __name__ == '__main__':unittest.main()
