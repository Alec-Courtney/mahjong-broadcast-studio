"""Qt runner for a replaceable recognizer; all camera and inference work is off the UI thread."""
from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage


class RecognitionWorker(QThread):
    frame_ready = Signal(object, object)
    frame_ready_tagged = Signal(object, object, int)
    status_changed = Signal(str)
    failed = Signal(str)

    def __init__(self, source, *, image=False, model_path=None, confidence=.55, roi=None, parent=None, recognizer_factory=None):
        super().__init__(parent)
        self.source = source
        self.image = image
        self.model_path = model_path
        self.confidence = confidence
        self.roi = roi
        self.recognizer_factory = recognizer_factory
        self.context_revision = 0

    def run(self):
        from tile_recognition import OnnxTileRecognizer, VideoSource, read_image, annotate
        stream = None
        last_status = None
        try:
            factory = self.recognizer_factory or OnnxTileRecognizer
            recognizer = factory(self.model_path, confidence=self.confidence) if self.model_path else factory(confidence=self.confidence)
            if self.image:
                still = read_image(self.source)
            else:
                stream = VideoSource(self.source)
            while not self.isInterruptionRequested():
                started = time.monotonic()
                context_revision = self.context_revision
                if self.image:
                    frame = still
                else:
                    okay, frame = stream.read()
                    if not okay:
                        if getattr(stream, 'recoverable', False):
                            message = getattr(stream, 'status', '') or '等待 OBS 来源画面'
                            if message != last_status:
                                self.status_changed.emit(message)
                                last_status = message
                            self.msleep(80)
                            continue
                        if isinstance(self.source, (int, dict)):
                            raise RuntimeError('摄像头未返回画面；请检查连接、隐私权限或设备是否被其他软件占用。使用 OBS 虚拟摄像头时请先在 OBS 启动虚拟摄像头。')
                        break
                if last_status:
                    self.status_changed.emit('已连接 · 正在识别')
                    last_status = None
                captured_at = time.monotonic()
                height, width = frame.shape[:2]
                roi = None
                if self.roi:
                    x, y, w, h = self.roi
                    left = max(0, min(width - 1, round(x * width)))
                    top = max(0, min(height - 1, round(y * height)))
                    right = max(left + 1, min(width, round((x + w) * width)))
                    bottom = max(top + 1, min(height, round((y + h) * height)))
                    roi = (left, top, right - left, bottom - top)
                result = replace(recognizer.recognize(frame, roi=roi), captured_at=captured_at)
                painted = annotate(frame, result)
                # BGR888 avoids importing a second image conversion pipeline.
                preview = QImage(painted.data, painted.shape[1], painted.shape[0], painted.strides[0], QImage.Format.Format_BGR888).copy()
                self.frame_ready.emit(preview, result)
                self.frame_ready_tagged.emit(preview, result, context_revision)
                if self.image:
                    break
                # Sample rather than queue every source frame. Live capture is
                # throttled, and file input is paced by the same preview budget.
                delay = max(0, .25 - (time.monotonic() - started))
                deadline = time.monotonic() + delay
                while not self.isInterruptionRequested() and time.monotonic() < deadline:
                    self.msleep(20)
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            if stream:
                stream.close()
