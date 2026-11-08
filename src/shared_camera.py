"""One native camera capture, independently consumed by seats and previews.

Only the latest frame is retained. Each subscriber receives its own copy and
sequence cursor, so a slower recognizer does not delay the other three seats.
No Model, Qt or detector dependency is required by this module.
"""
from __future__ import annotations

import copy
import threading
import time

from camera_devices import CameraDevice, enumerate_cameras, resolve_camera


class SharedCameraError(RuntimeError):
    pass


class SharedCameraTimeout(SharedCameraError):
    pass


def _copy_frame(frame):
    clone = getattr(frame, 'copy', None)
    return clone() if callable(clone) else copy.deepcopy(frame)


def _resolve(source):
    """Canonicalize numeric compatibility inputs to the same stable identity."""
    if isinstance(source, bool):
        raise ValueError('无效的摄像头编号')
    if isinstance(source, CameraDevice):
        return resolve_camera(source)
    if isinstance(source, dict):
        value = dict(source)
        if not value.get('identifier') and not value.get('device_id') and not value.get('name') and not value.get('device_name'):
            index = value.get('index', value.get('path', '0'))
            if isinstance(index, str) and index.isdecimal():
                index = int(index)
            if type(index) is not int:
                raise ValueError('无效的摄像头编号')
            return resolve_camera(index, enumerate_cameras())
        return resolve_camera(value)
    if isinstance(source, str) and source.isdecimal():
        source = int(source)
    if type(source) is int:
        return resolve_camera(source, enumerate_cameras())
    return resolve_camera(source)


def _open(device, *, cv2_module=None):
    from camera_devices import open_camera
    # Resolution already matched the persisted device identity to this ordinal.
    return open_camera(device, cv2_module=cv2_module, devices=[device])


class _Capture:
    def __init__(self, pool, key, device, cv2_module):
        self.pool, self.key, self.device = pool, key, device
        self.cv2_module = cv2_module
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.finished = threading.Event()
        self.ready = False
        self.opened = False
        self.error = None
        self.terminal = False
        self.closing = False
        self.references = 0
        self.sequence = 0
        self.frame = None
        self.properties = {}
        self.thread = threading.Thread(target=self.run, name='shared-camera-capture', daemon=True)

    def run(self):
        capture = None
        try:
            capture, resolved = self.pool.opener(self.device, cv2_module=self.cv2_module)
            if not capture.isOpened():
                raise SharedCameraError('摄像头未返回可用视频')
            properties = {}
            for key in (5, 3, 4):  # OpenCV FPS, FRAME_WIDTH, FRAME_HEIGHT identifiers.
                try:
                    properties[key] = capture.get(key)
                except Exception:
                    properties[key] = 0
            with self.condition:
                self.device = resolved
                self.properties = properties
                self.ready = self.opened = True
                self.condition.notify_all()
            while not self.stop.is_set():
                okay, frame = capture.read()
                if not okay:
                    break
                if frame is None:
                    raise SharedCameraError('摄像头返回空画面')
                owned = _copy_frame(frame)
                with self.condition:
                    if self.stop.is_set():
                        break
                    self.frame = owned
                    self.sequence += 1
                    self.condition.notify_all()
        except Exception as error:
            with self.condition:
                self.error = error if isinstance(error, SharedCameraError) else SharedCameraError(str(error))
        finally:
            # Release on the capture thread. A native read may still be active;
            # another consumer must never release its handle from a second thread.
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass
            with self.condition:
                self.ready = True
                self.terminal = self.closing = True
                self.condition.notify_all()
            self.finished.set()
            self.pool._finished(self)

    def wait_ready(self, timeout):
        deadline = time.monotonic() + timeout
        with self.condition:
            while not self.ready:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SharedCameraTimeout('等待摄像头连接超时，请检查设备或 OBS 虚拟摄像头')
                self.condition.wait(remaining)
            if self.error is not None and not self.opened:
                raise self.error
            if not self.opened:
                raise SharedCameraError('摄像头连接已停止')


class CameraSubscription:
    """Independent latest-frame reader; closing it affects no other reader."""
    def __init__(self, pool, entry):
        self._pool, self._entry = pool, entry
        self._closed = False
        self._sequence = 0

    @property
    def device(self):
        return self._entry.device

    @property
    def sequence(self):
        return self._sequence

    def isOpened(self):
        return not self._closed and self._entry.opened

    def get(self, key):
        return self._entry.properties.get(key, 0)

    def read(self, timeout=2.0):
        if timeout is None:
            timeout = 2.0
        if timeout < 0:
            raise ValueError('读取等待时间无效')
        deadline = time.monotonic() + timeout
        entry = self._entry
        with entry.condition:
            while True:
                if self._closed:
                    return False, None
                if entry.sequence > self._sequence and entry.frame is not None:
                    self._sequence = entry.sequence
                    frame = entry.frame
                    break
                if entry.error is not None:
                    raise entry.error
                if entry.terminal or entry.stop.is_set():
                    return False, None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SharedCameraTimeout('摄像头暂未返回新画面，请检查连接或 OBS 虚拟摄像头')
                entry.condition.wait(remaining)
        return True, _copy_frame(frame)

    def close(self):
        with self._entry.condition:
            if self._closed:
                return
            self._closed = True
            self._entry.condition.notify_all()
        self._pool._release(self._entry)

    release = close

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class CameraCapturePool:
    def __init__(self, *, resolver=None, opener=None):
        self.resolver = resolver or _resolve
        self.opener = opener or _open
        self._condition = threading.Condition()
        self._entries = {}

    @staticmethod
    def key(device):
        identity = ('id', device.identifier.casefold()) if device.identifier else ('index', device.index)
        return (device.backend.casefold(),) + identity

    @property
    def active_captures(self):
        with self._condition:
            return len(self._entries)

    def subscribe(self, source, *, cv2_module=None, timeout=5.0):
        device = self.resolver(source)
        key = self.key(device)
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                entry = self._entries.get(key)
                if entry is None:
                    entry = _Capture(self, key, device, cv2_module)
                    self._entries[key] = entry
                    entry.references = 1
                    entry.thread.start()
                    break
                if not entry.closing and not entry.stop.is_set():
                    entry.references += 1
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SharedCameraTimeout('摄像头正在释放，请稍后重试')
                self._condition.wait(remaining)
        reader = CameraSubscription(self, entry)
        try:
            entry.wait_ready(max(.001, deadline - time.monotonic()))
        except Exception:
            reader.close()
            raise
        return reader

    def _release(self, entry):
        with self._condition:
            entry.references = max(0, entry.references - 1)
            if entry.references == 0:
                entry.closing = True
                entry.stop.set()
                with entry.condition:
                    entry.condition.notify_all()
            self._condition.notify_all()

    def _finished(self, entry):
        with self._condition:
            if self._entries.get(entry.key) is entry:
                self._entries.pop(entry.key)
            self._condition.notify_all()

    def close(self, timeout=2.0):
        with self._condition:
            entries = list(self._entries.values())
            for entry in entries:
                entry.closing = True
                entry.stop.set()
                with entry.condition:
                    entry.condition.notify_all()
        deadline = time.monotonic() + timeout
        for entry in entries:
            entry.thread.join(max(0, deadline - time.monotonic()))


DEFAULT_POOL = CameraCapturePool()


def subscribe_camera(source, *, cv2_module=None, pool=None, timeout=5.0):
    return (DEFAULT_POOL if pool is None else pool).subscribe(source, cv2_module=cv2_module, timeout=timeout)
