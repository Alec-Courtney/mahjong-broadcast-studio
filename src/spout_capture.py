"""Named, thread-owned Spout input; independent from Qt and broadcast state.

The SDK creates an invisible OpenGL context on the capture thread. Each reader
owns its receiver, so a settings preview can coexist with all four live seats.
No active-sender selection, sender renaming or OBS configuration is performed.
"""
from __future__ import annotations

import sys
import threading
import time


def _runtime():
    if sys.platform != 'win32':
        raise RuntimeError('OBS Spout 输入仅支持 Windows；其他平台可使用摄像头或视频文件。')
    try:
        import SpoutGL
        import numpy
    except (ImportError, OSError) as error:
        raise RuntimeError('OBS Spout 接收组件未安装，请运行 INSTALL-Automation.cmd。') from error
    return SpoutGL, numpy


def enumerate_senders() -> list[str]:
    """Read shared sender names without opening a camera or a receiving stream."""
    sdk, _ = _runtime()
    receiver = sdk.SpoutReceiver()
    try:
        return sorted(set(str(name) for name in receiver.getSenderList() if name))
    finally:
        receiver.releaseReceiver()


class SpoutCapture:
    """OpenCV-like reader. A false read is transient; retry the same sender.

    Construct/read/release on one worker thread. Reads wait at most 250 ms by
    default and only return fresh frames, never a cached image after a freeze.
    ``status`` is suitable for the settings preview and live operator hint.
    """
    recoverable = True
    is_live = True

    def __init__(self, source, *, runtime=None, clock=time.monotonic, sleep=time.sleep):
        name = source.get('path', '') if isinstance(source, dict) else source
        if not isinstance(name, str) or not name.strip() or '\x00' in name:
            raise ValueError('请选择固定桌位对应的 OBS Spout 来源。')
        if len(name.encode('utf-8')) > 255:
            raise ValueError('Spout 来源名称过长，请在 OBS 中缩短名称。')
        self.name = name
        self.source = {'kind': 'spout', 'path': name}
        self._owner = threading.get_ident()
        self._clock, self._sleep = clock, sleep
        self._sdk, self._np = runtime or _runtime()
        self._receiver = self._sdk.SpoutReceiver()
        self._closed = False
        self._buffer = None
        self._last_sender_frame = None
        self._warmup = 0
        self._connection_started = self._clock()
        self.last_frame_at = None
        self.sender_frame = None
        self.width = self.height = 0
        self.fps = 0.0
        self.status = f'等待 OBS 来源：{self.name}'
        try:
            if not self._receiver.createOpenGL():
                raise RuntimeError('无法创建 Spout 接收环境；请确认显卡驱动可用，并让本程序与 OBS 使用同一显卡。')
            self._receiver.setReceiverName(self.name)
        except Exception:
            self.release()
            raise

    def _check_thread(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError('Spout 接收器必须在创建它的采集线程中读取和关闭。')

    def _disconnect(self):
        if self._buffer is not None or self._receiver.isConnected():
            self._receiver.releaseReceiver()
        self._buffer = None
        self._last_sender_frame = None
        self.width = self.height = 0
        self._receiver.setReceiverName(self.name)

    def read(self, timeout=.25):
        self._check_thread()
        if self._closed:
            return False, None
        deadline = self._clock() + max(0.0, min(float(timeout), .5))
        while True:
            # A named receiver is pinned even if another application changes
            # Spout's active sender. Never connect while this exact name is absent.
            if self.name not in self._receiver.getSenderList():
                self._disconnect()
                self.status = f'等待 OBS 来源：{self.name}（恢复后自动连接）'
            else:
                okay = self._receiver.receiveImage(self._buffer, self._sdk.enums.GL_RGBA, False, 0)
                updated = self._receiver.isUpdated()  # SDK requires clearing this on every cycle.
                if updated:
                    self.width = int(self._receiver.getSenderWidth())
                    self.height = int(self._receiver.getSenderHeight())
                    if not (0 < self.width <= 8192 and 0 < self.height <= 8192
                            and self.width * self.height <= 33554432):
                        raise RuntimeError('Spout 来源画面尺寸无效或过大，请检查 OBS 来源。')
                    self._buffer = self._np.zeros((self.height, self.width, 4), dtype=self._np.uint8)
                    self._last_sender_frame = None
                    # Initial GPU pixel buffers may not have completed their
                    # first transfer. Warm up without treating these as hands.
                    self._warmup = 2
                    self._connection_started = self._clock()
                    self.status = f'正在连接 OBS 来源：{self.name}'
                elif okay and self._buffer is not None and self._receiver.isFrameNew():
                    if not self._receiver.isFrameCountEnabled():
                        self.status = 'Spout 新帧计数未启用，暂停识别；请运行 INSTALL-Automation.cmd 后重启 OBS 和导播台。'
                    else:
                        sequence = int(self._receiver.getSenderFrame())
                        if sequence <= 0 and self._clock() - self._connection_started >= 2:
                            self.status = 'OBS 来源尚未提供新帧计数；安装组件后请重启 OBS，并开启持续发送。'
                        if sequence != self._last_sender_frame:
                            self._last_sender_frame = sequence
                            if self._warmup:
                                self._warmup -= 1
                            else:
                                self.sender_frame = sequence
                                self.last_frame_at = self._clock()
                                self.status = f'已连接 OBS 来源：{self.name}'
                                # RGBA is portable across SDK backends. Return a
                                # detached BGR copy for OpenCV/ONNX ownership.
                                return True, self._buffer[:, :, 2::-1].copy()
                elif not okay:
                    self.status = 'OBS 来源未返回画面；请检查 Spout 滤镜、持续发送，并让 OBS 与本程序使用同一显卡。'
                elif self.last_frame_at is not None and self._clock() - self.last_frame_at >= 2:
                    self.status = 'OBS 来源画面已暂停；请开启 Continuous filter broadcast，恢复新帧后自动识别。'
            remaining = deadline - self._clock()
            if remaining <= 0:
                return False, None
            self._sleep(min(.02, remaining))

    def isOpened(self):
        return not self._closed

    def get(self, key):
        # OpenCV property values; importing OpenCV is unnecessary for this adapter.
        return {3: self.width, 4: self.height, 5: self.fps}.get(key, 0)

    def release(self):
        self._check_thread()
        if self._closed:
            return
        self._closed = True
        try:
            self._receiver.releaseReceiver()
        finally:
            self._receiver.closeOpenGL()
            self._buffer = None

    close = release

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()
