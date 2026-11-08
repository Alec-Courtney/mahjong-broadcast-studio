"""Four fixed positions consume a continuous source independently of Program.

Capture is entirely fake. It represents an OBS output fixed to a source/scene;
it cannot certify a real OBS configuration or an actual camera driver.
"""
from __future__ import annotations

from collections import Counter
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

from PySide6.QtWidgets import QApplication
from shiboken6 import isValid
from camera_devices import CameraDevice
from console import Console
from model import Model, parse_tiles
from overlays import OverlayManager
from recognition_config import default_recognition_config
from recognition_runtime import recognition_available
from settings_dialog import SettingsDialog
from tile_recognition import RecognitionFrame, TileDetection

if recognition_available():
    import numpy as np

APP = QApplication.instance() or QApplication([])
QUADS = ((0., 0., .5, .5), (.5, 0., .5, .5), (0., .5, .5, .5), (.5, .5, .5, .5))
HANDS = tuple(parse_tiles(value) for value in
              ('123m123p123s1112z', '123m456p12789s11z',
               '123456m234p678s1z', '999m333p5s666777z'))
WAITS = (['2z'], ['3s'], ['1z'], ['5s'])
DEVICE = CameraDevice(0, 'OBS fixed recognition output (test)', 'obs-fixed-four-seat-test')


def pump_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(.01)
    APP.processEvents()
    return predicate()


class PatternCapture:
    """One producer; Program choice is deliberately outside its fixed feed."""
    def __init__(self):
        self.released = False
        self.release_count = 0
        self.read_count = 0
        self.program_scene = 'program-camera-1'
        self.lock = threading.Lock()

    def isOpened(self):
        return not self.released

    def get(self, key):
        return {5: 30., 3: 400., 4: 240.}.get(key, 0.)

    def set(self, *_):
        return True

    def read(self):
        time.sleep(.015)
        with self.lock:
            if self.released:
                return False, None
            self.read_count += 1
            sequence = self.read_count
        frame = np.zeros((240, 400, 3), dtype=np.uint8)
        for seat, (x, y, width, height) in enumerate(QUADS):
            left, top = round(x * 400), round(y * 240)
            right, bottom = round((x + width) * 400), round((y + height) * 240)
            frame[top:bottom, left:right, 0] = (seat + 1) * 40
            frame[top:bottom, left:right, 1] = sequence % 255
        return True, frame

    def release(self):
        with self.lock:
            if not self.released:
                self.release_count += 1
                self.released = True


class PatternRecognizer:
    provider_name = 'fixed-source-test-provider'
    def __init__(self, *_, confidence=.55, **__):
        self.confidence = confidence

    def recognize(self, frame_bgr, *, roi=None):
        height, width = frame_bgr.shape[:2]
        x, y, rw, rh = roi or (0, 0, width, height)
        marker = int(np.median(frame_bgr[y:y + rh, x:x + rw, 0]))
        seat = max(0, min(3, marker // 40 - 1))
        tiles = HANDS[seat]
        step = max(2, rw / len(tiles))
        detections = tuple(TileDetection(tile, .96,
            (x + index * step, y + 10, x + (index + .8) * step, y + min(rh - 1, 70)), tile)
            for index, tile in enumerate(tiles))
        return RecognitionFrame(detections, width, height, 'fixed-recognition-source', 1., self.provider_name)


@unittest.skipUnless(recognition_available(), 'optional vision runtime not installed')
class FourSeatCaptureTests(unittest.TestCase):
    def setUp(self):
        from shared_camera import CameraCapturePool
        self.temporary = tempfile.TemporaryDirectory(prefix='four-seat-', dir=ROOT / 'tests')
        self.capture = PatternCapture()
        self.opened = []
        def resolve(source):
            if isinstance(source, dict) and source.get('identifier') != DEVICE.identifier:
                raise AssertionError('No physical camera may be opened in this test')
            return DEVICE
        def open_capture(device, *, cv2_module=None):
            self.opened.append(device.identifier)
            return self.capture, device
        self.pool = CameraCapturePool(resolver=resolve, opener=open_capture)
        self.pool_patch = patch('shared_camera.DEFAULT_POOL', self.pool)
        self.provider_patch = patch('tile_recognition.OnnxTileRecognizer', PatternRecognizer)
        self.pool_patch.start()
        self.provider_patch.start()
        self.addCleanup(self._cleanup)
        self.model = Model(self.temporary.name)
        config = default_recognition_config()
        config['sources'] = [dict(enabled=True, kind='camera', path='0', identifier=DEVICE.identifier,
            name=DEVICE.name, index=0, backend='dshow', roi=list(roi)) for roi in QUADS]
        self.model.settings.update(show_overlays=False, topmost=False, recognition_assist=config)
        self.manager = OverlayManager(self.model)
        self.ui = Console(self.model, self.manager)
        self.assist = self.ui.inline_automation
        self.updates = Counter()
        self.assist.preview_received.connect(lambda seat, image, result: self.updates.update([seat]))
        self.ui.show()
        self.assertTrue(pump_until(lambda: all(seat in self.assist.observations for seat in range(4))),
                        [widget.status for widget in self.ui.recognized_hands])
        self.workers = dict(self.assist.workers)

    def _cleanup(self):
        workers = getattr(self, 'workers', dict(getattr(getattr(self, 'assist', None), 'workers', {})))
        if getattr(self, 'ui', None):
            self.ui.close()
        pump_until(lambda: not any(isValid(worker) and worker.isRunning() for worker in workers.values()), 3)
        closer = getattr(self.pool, 'close', None) or getattr(self.pool, 'close_all', None)
        if closer:
            closer()
        self.capture.release()
        self.provider_patch.stop()
        self.pool_patch.stop()
        APP.processEvents()
        self.temporary.cleanup()

    def assert_fixed_positions(self):
        for seat in range(4):
            self.assertEqual(Counter(self.ui.recognized_hands[seat].tiles), Counter(HANDS[seat]))
            self.assertEqual(self.ui.wait_drafts[seat]['waits'], WAITS[seat])
            self.assertIs(self.assist.workers[seat], self.workers[seat])
            self.assertTrue(self.workers[seat].isRunning())

    def test_one_capture_continuously_feeds_all_fixed_positions_when_program_changes(self):
        self.assertEqual(self.opened, [DEVICE.identifier])
        before_state = copy.deepcopy(self.model.state)
        for program in ('program-camera-4', 'title-card', 'program-camera-2'):
            before = self.updates.copy()
            self.capture.program_scene = program
            self.ui.select((self.ui.selected + 1) % 4)
            self.model.setting('broadcast_scene', 'clean' if program == 'title-card' else 'table')
            self.assertTrue(pump_until(lambda: all(self.updates[seat] > before[seat] for seat in range(4))))
            self.assert_fixed_positions()
        self.assertEqual(self.opened, [DEVICE.identifier])
        self.assertEqual(self.model.state, before_state)
        self.assertEqual(self.model.undo, [])

    def test_dealer_and_round_changes_do_not_rebind_or_restart_the_capture_positions(self):
        self.model.field('dealer', 1)
        self.model.field('roundIndex', 1)
        self.assertTrue(pump_until(lambda: all(seat in self.assist.observations and self.assist.observations[seat].get('valid') for seat in range(4))))
        self.assert_fixed_positions()
        self.assertEqual(self.opened, [DEVICE.identifier])

    def test_live_settings_preview_is_an_extra_reader_while_all_four_continue(self):
        self.assertTrue(self.assist.supports_parallel_preview)
        dialog = SettingsDialog(self.ui)
        try:
            dialog.focus_recognition(0)
            before = self.updates.copy()
            dialog._start_recognition_preview(0)
            self.assertTrue(pump_until(lambda: dialog.recognition_preview.preview.image is not None))
            self.assertTrue(pump_until(lambda: all(self.updates[seat] > before[seat] for seat in range(4))))
            self.assert_fixed_positions()
            self.assertEqual(self.opened, [DEVICE.identifier])
            dialog._stop_recognition_preview(0)
            before = self.updates.copy()
            self.assertTrue(pump_until(lambda: all(self.updates[seat] > before[seat] for seat in range(4))))
            self.assert_fixed_positions()
            self.assertEqual(self.capture.release_count, 0)
        finally:
            dialog.close()
            APP.processEvents()

    def test_stopping_one_position_does_not_release_the_shared_camera_for_other_three(self):
        stopped = self.assist.stop_source(0)
        self.assertTrue(pump_until(lambda: not stopped.isRunning()))
        before = self.updates.copy()
        self.assertTrue(pump_until(lambda: all(self.updates[seat] > before[seat] for seat in (1, 2, 3))))
        self.assertEqual(self.capture.release_count, 0)
        self.assertEqual(self.opened, [DEVICE.identifier])


if __name__ == '__main__':
    unittest.main()
