"""One native capture continues for all four independent seat readers."""
from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from camera_devices import CameraDevice
from shared_camera import CameraCapturePool, SharedCameraError, SharedCameraTimeout, subscribe_camera

try:
    import numpy as np
except ImportError:
    np = None


def until(predicate, timeout=1):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.002)
    return predicate()


class Capture:
    def __init__(self, gate=None):
        self.gate = gate
        self.released = threading.Event()
        self.release_count = 0
        self.frames = 0
        self.error = None
        self.eof = False
        self.image = np.zeros((20, 20, 3), dtype=np.uint8)
        self.image[:10, 10:] = 1
        self.image[10:, :10] = 2
        self.image[10:, 10:] = 3

    def isOpened(self):
        return True

    def get(self, key):
        return {5: 30., 3: 20, 4: 20}.get(key, 0)

    def read(self):
        if self.gate:
            self.gate.wait(5)
        time.sleep(.005)
        if self.error:
            raise self.error
        if self.eof:
            return False, None
        self.frames += 1
        return True, self.image

    def release(self):
        self.release_count += 1
        self.released.set()


@unittest.skipIf(np is None, 'optional vision runtime not installed')
class SharedCameraTests(unittest.TestCase):
    def setUp(self):
        self.device = CameraDevice(2, 'OBS Virtual Camera', 'obs-stable')
        self.captures = []
        self.readers = []
        self.pools = []

    def tearDown(self):
        for capture in self.captures:
            if capture.gate:
                capture.gate.set()
        for reader in self.readers:
            reader.close()
        for pool in self.pools:
            pool.close()

    def pool(self, *, gate=None, opener=None, resolver=None):
        def connect(device, *, cv2_module=None):
            capture = Capture(gate)
            self.captures.append(capture)
            return capture, device
        result = CameraCapturePool(resolver=resolver or (lambda source: self.device), opener=opener or connect)
        self.pools.append(result)
        return result

    def subscribe(self, pool, source=None, **kwargs):
        reader = pool.subscribe(self.device.to_dict() if source is None else source, **kwargs)
        self.readers.append(reader)
        return reader

    def test_four_seat_readers_open_once_and_receive_independent_roi_copies(self):
        pool = self.pool()
        readers = [self.subscribe(pool) for _ in range(4)]
        self.assertEqual(len(self.captures), 1)
        self.assertEqual(pool.active_captures, 1)
        frames = [reader.read()[1] for reader in readers]
        rois = [(0, 0), (10, 0), (0, 10), (10, 10)]
        for index, (frame, (x, y)) in enumerate(zip(frames, rois)):
            self.assertTrue(np.all(frame[y:y + 10, x:x + 10] == index))
        frames[0][:] = 255
        self.assertEqual(int(frames[1][0, 0, 0]), 0)
        self.assertEqual(int(self.captures[0].image[0, 0, 0]), 0)

    def test_preview_fifth_reader_and_stop_one_seat_do_not_stop_others(self):
        pool = self.pool()
        readers = [self.subscribe(pool) for _ in range(5)]
        readers[4].close()
        readers[1].close()
        self.assertFalse(self.captures[0].released.is_set())
        for index in (0, 2, 3):
            self.assertTrue(readers[index].read()[0])
        self.assertEqual(len(self.captures), 1)

    def test_last_close_releases_once_and_is_idempotent(self):
        pool = self.pool()
        readers = [self.subscribe(pool) for _ in range(4)]
        for reader in readers[:-1]:
            reader.close()
        self.assertFalse(self.captures[0].released.is_set())
        readers[-1].close()
        readers[-1].close()
        self.assertTrue(until(self.captures[0].released.is_set))
        self.assertEqual(self.captures[0].release_count, 1)
        self.assertTrue(until(lambda: pool.active_captures == 0))

    def test_concurrent_subscriptions_cannot_open_duplicate_native_handles(self):
        lock = threading.Lock()
        def connect(device, *, cv2_module=None):
            time.sleep(.03)
            capture = Capture()
            with lock:
                self.captures.append(capture)
            return capture, device
        pool = self.pool(opener=connect)
        barrier = threading.Barrier(4)
        readers = []
        errors = []
        def create():
            try:
                barrier.wait(1)
                reader = pool.subscribe(self.device)
                with lock:
                    readers.append(reader)
            except Exception as error:
                errors.append(error)
        workers = [threading.Thread(target=create) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(2)
        self.readers.extend(readers)
        self.assertEqual(errors, [])
        self.assertEqual(len(readers), 4)
        self.assertEqual(len(self.captures), 1)

    def test_legacy_ordinal_and_stable_identifier_share_the_same_capture(self):
        inventory = [CameraDevice(0, 'OBS Virtual Camera', 'obs-stable')]
        pool = self.pool(resolver=None)
        from shared_camera import _resolve
        pool.resolver = _resolve
        with patch('shared_camera.enumerate_cameras', return_value=inventory), patch('camera_devices.enumerate_cameras', return_value=inventory):
            readers = [self.subscribe(pool, 0),
                       self.subscribe(pool, {'kind': 'camera', 'path': '0'}),
                       self.subscribe(pool, {'identifier': 'obs-stable', 'index': 9, 'name': 'OBS Virtual Camera'})]
        self.assertEqual([reader.device.index for reader in readers], [0, 0, 0])
        self.assertEqual(len(self.captures), 1)

    def test_disconnect_propagates_to_every_reader_and_reconnects_after_release(self):
        gate = threading.Event()
        pool = self.pool(gate=gate)
        readers = [self.subscribe(pool) for _ in range(4)]
        self.captures[0].error = RuntimeError('device disconnected')
        gate.set()
        self.assertTrue(until(self.captures[0].released.is_set))
        for reader in readers:
            with self.assertRaisesRegex(SharedCameraError, 'disconnected'):
                reader.read()
        fresh = self.subscribe(pool)
        self.assertTrue(fresh.read()[0])
        for reader in readers:
            reader.close()
        self.assertTrue(fresh.isOpened())
        self.assertEqual(len(self.captures), 2)
        self.assertEqual(self.captures[0].release_count, 1)

    def test_eof_is_visible_to_every_consumer_without_camera_open_fanout(self):
        gate = threading.Event()
        pool = self.pool(gate=gate)
        readers = [self.subscribe(pool) for _ in range(4)]
        self.captures[0].eof = True
        gate.set()
        self.assertTrue(until(self.captures[0].released.is_set))
        for reader in readers:
            self.assertEqual(reader.read(), (False, None))
        self.assertEqual(len(self.captures), 1)

    def test_wait_timeout_does_not_release_other_readers(self):
        gate = threading.Event()
        pool = self.pool(gate=gate)
        first, second = self.subscribe(pool), self.subscribe(pool)
        with self.assertRaises(SharedCameraTimeout):
            first.read(timeout=.02)
        first.close()
        self.assertFalse(self.captures[0].released.is_set())
        gate.set()
        self.assertTrue(second.read()[0])

    def test_closing_one_reader_wakes_its_wait_without_closing_native_capture(self):
        gate = threading.Event()
        pool = self.pool(gate=gate)
        first, second = self.subscribe(pool), self.subscribe(pool)
        values = []
        entered = threading.Event()
        def read():
            entered.set()
            values.append(first.read(timeout=2))
        worker = threading.Thread(target=read)
        worker.start()
        self.assertTrue(entered.wait(1))
        first.close()
        worker.join(.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(values, [(False, None)])
        self.assertFalse(self.captures[0].released.is_set())
        gate.set()
        self.assertTrue(second.read()[0])

    def test_reopen_waits_for_last_native_read_and_release_to_finish(self):
        gate = threading.Event()
        def connect(device, *, cv2_module=None):
            if self.captures:
                self.assertTrue(self.captures[0].released.is_set())
            capture = Capture(gate if not self.captures else None)
            self.captures.append(capture)
            return capture, device
        pool = self.pool(opener=connect)
        first = self.subscribe(pool)
        first.close()
        values = []
        worker = threading.Thread(target=lambda: values.append(pool.subscribe(self.device, timeout=1)))
        worker.start()
        time.sleep(.03)
        self.assertEqual(len(self.captures), 1)
        self.assertTrue(worker.is_alive())
        gate.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.readers.extend(values)
        self.assertEqual(len(self.captures), 2)
        self.assertTrue(values[0].read()[0])

    def test_latest_frame_memory_is_bounded_and_slow_reader_skips_backlog(self):
        pool = self.pool()
        reader = self.subscribe(pool)
        self.assertTrue(reader.read()[0])
        initial = reader.sequence
        time.sleep(.055)
        self.assertTrue(reader.read()[0])
        self.assertGreater(reader.sequence, initial + 2)
        self.assertTrue(np.array_equal(reader._entry.frame, self.captures[0].image))
        self.assertFalse(hasattr(reader._entry, 'frames'))

    def test_global_pool_is_resolved_when_function_is_called(self):
        pool = self.pool()
        with patch('shared_camera.DEFAULT_POOL', pool):
            reader = subscribe_camera(self.device)
        self.readers.append(reader)
        self.assertTrue(reader.read()[0])


if __name__ == '__main__':
    unittest.main()
