"""Spout ownership/freshness contracts without graphics, devices or registry writes."""
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    import numpy as np
except ImportError:
    np = None
from spout_capture import SpoutCapture, enumerate_senders


class Receiver:
    def __init__(self):
        self.names = ['fixed-seat', 'other-seat']
        self.chosen = None
        self.frame = np.full((4, 8, 4), (10, 20, 30, 255), dtype=np.uint8)
        self.size = None
        self.sequence = 0
        self.previous = None
        self.new = False
        self.updated = False
        self.connected = False
        self.count_enabled = True
        self.releases = self.gl_closed = 0

    def createOpenGL(self): return True
    def closeOpenGL(self): self.gl_closed += 1
    def setReceiverName(self, name): self.chosen = name
    def getSenderList(self): return list(self.names)
    def isConnected(self): return self.connected
    def isFrameNew(self): return self.new
    def isFrameCountEnabled(self): return self.count_enabled
    def getSenderFrame(self): return self.sequence
    def getSenderWidth(self): return self.frame.shape[1]
    def getSenderHeight(self): return self.frame.shape[0]

    def releaseReceiver(self):
        self.releases += 1
        self.connected = False
        self.size = self.previous = None

    def isUpdated(self):
        result, self.updated = self.updated, False
        return result

    def receiveImage(self, buffer, *_):
        assert self.chosen == 'fixed-seat'
        if self.chosen not in self.names:
            return False
        self.connected = True
        if self.size != self.frame.shape:
            self.size = self.frame.shape
            self.updated = True
            return True
        self.new = self.sequence != self.previous
        self.previous = self.sequence
        if buffer is not None:
            buffer[:] = self.frame
        return True


def runtime(receiver):
    return SimpleNamespace(SpoutReceiver=lambda: receiver, enums=SimpleNamespace(GL_RGBA=6408)), np


@unittest.skipIf(np is None, 'optional NumPy runtime not installed')
class SpoutContracts(unittest.TestCase):
    def test_fresh_frames_resize_disconnect_reconnect_stay_on_named_source(self):
        receiver = Receiver()
        with SpoutCapture({'kind': 'spout', 'path': 'fixed-seat'}, runtime=runtime(receiver)) as source:
            def fresh(expected_shape):
                for _ in range(4):
                    receiver.sequence += 1
                    okay, frame = source.read(timeout=0)
                self.assertTrue(okay)
                self.assertEqual(frame.shape, expected_shape)
                self.assertEqual(frame[0, 0].tolist(), [30, 20, 10])
                frame[:] = 0
                self.assertTrue(receiver.frame.any(), 'consumer changed source buffer')
            fresh((4, 8, 3))
            self.assertEqual(source.read(timeout=0), (False, None), 'same frame counted twice')
            receiver.frame = np.full((6, 12, 4), (10, 20, 30, 255), dtype=np.uint8)
            fresh((6, 12, 3))
            receiver.names = ['other-seat']
            self.assertEqual(source.read(timeout=0), (False, None))
            self.assertIn('等待', source.status)
            self.assertEqual(receiver.chosen, 'fixed-seat')
            receiver.names.append('fixed-seat')
            fresh((6, 12, 3))
        self.assertEqual(receiver.gl_closed, 1)

    def test_missing_counter_or_source_never_claims_a_fresh_frame(self):
        receiver = Receiver()
        receiver.count_enabled = False
        with SpoutCapture('fixed-seat', runtime=runtime(receiver)) as source:
            for _ in range(6):
                receiver.sequence += 1
                self.assertEqual(source.read(timeout=0), (False, None))
            self.assertIn('INSTALL-Automation.cmd', source.status)
        with patch('spout_capture._runtime', return_value=runtime(receiver)):
            self.assertEqual(enumerate_senders(), ['fixed-seat', 'other-seat'])
        for name in ['', ' ', '\x00', 'x' * 256]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                SpoutCapture(name, runtime=runtime(receiver))

    def test_read_and_native_cleanup_must_stay_on_owner_thread(self):
        receiver = Receiver()
        source = SpoutCapture('fixed-seat', runtime=runtime(receiver))
        errors = []
        def misuse():
            for operation in (source.read, source.release):
                try:
                    operation()
                except RuntimeError as error:
                    errors.append(error)
        thread = threading.Thread(target=misuse)
        thread.start()
        thread.join(1)
        self.assertEqual(len(errors), 2)
        source.release()
        source.release()
        self.assertEqual(receiver.gl_closed, 1)
        self.assertEqual(source.read(), (False, None))

    def test_video_adapter_routes_spout_without_camera_ownership(self):
        from tile_recognition import VideoSource
        receiver = Receiver()
        descriptor = {'kind': 'spout', 'path': 'fixed-seat'}
        with patch('spout_capture._runtime', return_value=runtime(receiver)), patch('shared_camera.subscribe_camera') as camera:
            with VideoSource(descriptor) as source:
                self.assertTrue(source.recoverable)
                self.assertTrue(source.is_live)
                self.assertEqual(source.source, descriptor)
                for _ in range(4):
                    receiver.sequence += 1
                    okay, frame = source.read()
                self.assertTrue(okay)
                self.assertEqual((source.width, source.height), (8, 4))
                self.assertIn('已连接', source.status)
            camera.assert_not_called()


if __name__ == '__main__':
    unittest.main()
