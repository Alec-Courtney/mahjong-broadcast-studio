"""Settings-only live source preview and drag selection, independent of game state."""
from __future__ import annotations

import copy
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QRect, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget, QLayout
from shiboken6 import isValid

_DRAINING = []


class SourcePreviewCanvas(QWidget):
    roi_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self.roi = (0., 0., 1., 1.)
        self.anchor = None
        self.drag = None
        self.setMinimumSize(280, 170)
        self.setToolTip('在画面上拖动，只框选该固定位置的暗手牌；可在预览进行时调整')

    def image_rect(self):
        if self.image is None:
            return QRect()
        size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRect((self.width() - size.width()) // 2, (self.height() - size.height()) // 2, size.width(), size.height())

    def set_image(self, image):
        self.image = image
        self.update()

    def set_roi(self, roi):
        self.roi = tuple(roi)
        self.update()

    def normalized(self, point):
        area = self.image_rect()
        return (max(0., min(1., (point.x() - area.x()) / max(1, area.width()))),
                max(0., min(1., (point.y() - area.y()) / max(1, area.height()))))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#10161d'))
        if self.image is None:
            painter.setPen(QColor('#a1afbf'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '选择设备后连接预览\n在画面上拖选手牌区域')
            return
        area = self.image_rect()
        painter.drawImage(area, self.image)
        if self.drag is not None:
            start, end = self.anchor, self.drag
            roi = (min(start[0], end[0]), min(start[1], end[1]), abs(start[0] - end[0]), abs(start[1] - end[1]))
        else:
            roi = self.roi
        x, y, width, height = roi
        painter.setPen(QPen(QColor('#65e0ba'), 2))
        painter.drawRect(QRect(round(area.x() + x * area.width()), round(area.y() + y * area.height()),
                              round(width * area.width()), round(height * area.height())))

    def mousePressEvent(self, event):
        if self.image is not None and event.button() == Qt.MouseButton.LeftButton and self.image_rect().contains(event.position().toPoint()):
            self.anchor = self.normalized(event.position())
            self.drag = self.anchor
            self.update()

    def mouseMoveEvent(self, event):
        if self.anchor is not None:
            self.drag = self.normalized(event.position())
            self.update()

    def mouseReleaseEvent(self, event):
        if self.anchor is None:
            return
        start, end = self.anchor, self.normalized(event.position())
        self.anchor = self.drag = None
        width, height = abs(start[0] - end[0]), abs(start[1] - end[1])
        if width >= .01 and height >= .01:
            self.roi = (min(start[0], end[0]), min(start[1], end[1]), width, height)
            self.roi_changed.emit(self.roi)
        self.update()


class RecognitionSourceEditor(QWidget):
    """One selected preview with the latest hand from each configured seat."""
    start_requested = Signal(int)
    stop_requested = Signal(int)
    roi_changed = Signal(int, object)
    selected_player_changed = Signal(int)
    refresh_devices_requested = Signal()

    def __init__(self, names, parent=None):
        super().__init__(parent)
        from recognized_hand import RecognizedHandWidget
        self.player = QComboBox()
        self.player.setProperty('i18nLiteral', True)
        for index, name in enumerate(names):
            self.player.addItem(f'固定位置 {index + 1} · {name}', index)
        self.start_button = QPushButton('连接预览')
        self.stop_button = QPushButton('停止预览')
        self.stop_button.setEnabled(False)
        self.refresh_button = QPushButton('刷新来源')
        header = QHBoxLayout()
        header.addWidget(QLabel('画面'))
        header.addWidget(self.player, 1)
        for control in (self.start_button, self.stop_button, self.refresh_button):
            header.addWidget(control)
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header)
        self.preview = SourcePreviewCanvas()
        layout.addWidget(self.preview, 1)
        self.hand = RecognizedHandWidget(0)
        self.hand.source_button.hide()
        self.hand.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.hand)
        self.status = QLabel('拖框选择暗手牌；保存后用于导播台辅助填写。')
        self.status.setWordWrap(True)
        self.status.setObjectName('muted')
        layout.addWidget(self.status)
        self.start_button.clicked.connect(lambda: self.start_requested.emit(self.player.currentIndex()))
        self.stop_button.clicked.connect(lambda: self.stop_requested.emit(self.player.currentIndex()))
        self.refresh_button.clicked.connect(self.refresh_devices_requested)
        self.player.currentIndexChanged.connect(self._select)
        self.preview.roi_changed.connect(lambda roi: self.roi_changed.emit(self.player.currentIndex(), roi))
        self._frames = {}

    def _select(self, player):
        frame = self._frames.get(player)
        if frame:
            self.show_frame(player, *frame)
        else:
            self.preview.set_image(None)
            self.hand.clear()
        self.selected_player_changed.emit(player)

    def show_frame(self, player, image, result):
        self._frames[player] = (image, result)
        if player != self.player.currentIndex():
            return
        self.preview.set_image(image)
        confidence = min((d.confidence for d in result.detections), default=None)
        self.hand.set_observation([d.tile for d in result.detections], source=result.provider or result.source,
            confidence=confidence, status=f'{len(result.tiles)} 张 · {result.elapsed_ms:.0f} ms / 帧')
        self.status.setText(f'正在预览固定位置 {player + 1}；拖选区域后会更新识别范围。此处结果不会提交到对局。')

    def set_running(self, player, running, message=''):
        if player == self.player.currentIndex():
            self.start_button.setEnabled(not running)
            self.stop_button.setEnabled(running)
            if message:
                self.status.setText(message)

    def clear(self):
        self._frames.clear()
        self.preview.set_image(None)
        self.hand.clear()

    def invalidate(self, player, message='来源已修改，请重新连接预览'):
        self._frames.pop(player, None)
        if player == self.player.currentIndex():
            self.preview.set_image(None)
            self.hand.clear()
            self.status.setText(message)

    def mark_stale(self, player, message):
        self._frames.pop(player, None)
        if player == self.player.currentIndex():
            self.preview.set_image(None)
            self.hand.set_observation(self.hand.tiles, status=message, stale=True)
            self.status.setText(message)


class RecognitionPreviewSession(QObject):
    """Fallback diagnostics runner. Never imports a Model or submits a draft."""
    frame_received = Signal(int, object, object)
    state_changed = Signal(int, str)

    def __init__(self, parent=None, worker_factory=None):
        super().__init__(parent)
        self.worker = None
        self._player = None
        self._pending = None
        self._source = None
        self._model = None
        self._confidence = .55
        self._closed = False
        self.running = False
        self._failed_message = ''
        self.worker_factory = worker_factory

    def start(self, player, source, *, model='medium', confidence=.55):
        if self._closed:
            return
        self._pending = (player, copy.deepcopy(source), model, confidence)
        if self.worker and self.worker.isRunning():
            self.worker._preview_cancelled = True
            self.worker.requestInterruption()
        else:
            self._launch_pending()

    def _launch_pending(self):
        if not self._pending or self._closed:
            return
        if self.worker:
            self.worker.deleteLater()
            self.worker = None
        player, source, model, confidence = self._pending
        self._pending = None
        try:
            from recognition_worker import RecognitionWorker
            from tile_recognition import recognition_model_path
            path = source.get('path', '').strip()
            if source['kind'] == 'camera':
                capture_source = dict(source) if source.get('identifier') else int(path or '0')
            elif source['kind'] == 'spout':
                if not path:
                    raise ValueError('请选择 OBS 来源名称')
                capture_source = dict(source)
            else:
                if not Path(path).is_file():
                    raise ValueError('请选择可读取的本地图片或视频')
                capture_source = path
            factory = self.worker_factory or RecognitionWorker
            worker = factory(capture_source, image=source['kind'] == 'image',
                model_path=recognition_model_path(model),
                confidence=confidence, roi=tuple(source.get('roi', [0, 0, 1, 1])), parent=self)
            worker._preview_cancelled = False
            self.worker, self._player = worker, player
            self._source, self._model, self._confidence = source, model, confidence
            self.running = True
            self._failed_message = ''
            if hasattr(worker, 'frame_ready_tagged'):
                worker.frame_ready_tagged.connect(self._receive_tagged_frame, Qt.ConnectionType.QueuedConnection)
            else:
                worker.frame_ready.connect(self._receive_frame, Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
            if hasattr(worker, 'status_changed'):
                worker.status_changed.connect(self._status, Qt.ConnectionType.QueuedConnection)
            worker.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
            self.state_changed.emit(player, '正在连接预览…')
            worker.start()
        except Exception as error:
            self.running = False
            self.state_changed.emit(player, str(error))

    @Slot(object, object)
    def _receive_frame(self, image, result):
        sender = self.sender()
        if not self._closed and sender is self.worker and not getattr(sender, '_preview_cancelled', False):
            self.frame_received.emit(self._player, image, result)

    @Slot(object, object, int)
    def _receive_tagged_frame(self, image, result, revision):
        sender = self.sender()
        if sender is self.worker and revision == getattr(sender, 'context_revision', 0):
            self._receive_frame(image, result)

    @Slot(str)
    def _status(self, message):
        if not self._closed and self.sender() is self.worker and not getattr(self.worker, '_preview_cancelled', False):
            self.state_changed.emit(self._player, message)

    @Slot(str)
    def _failed(self, message):
        if not self._closed and self.sender() is self.worker and not getattr(self.worker, '_preview_cancelled', False):
            self.running = False
            self._failed_message = message
            self.state_changed.emit(self._player, message)

    @Slot()
    def _finished(self):
        if self._closed or self.sender() is not self.worker:
            return
        self.running = False
        if not self._failed_message:
            self.state_changed.emit(self._player, '预览已停止')
        self._launch_pending()

    def set_roi(self, player, roi):
        if self._player != player or not self._source:
            return
        self._source['roi'] = list(roi)
        if self._source['kind'] == 'image':
            # A still may already be in inference. Queue a fresh pass for the
            # new crop, rather than accepting the old crop's final frame.
            self.start(player, self._source, model=self._model, confidence=self._confidence)
        elif self.worker and self.worker.isRunning():
            self.worker.context_revision = getattr(self.worker, 'context_revision', 0) + 1
            self.worker.roi = tuple(roi)

    def stop(self):
        self._pending = None
        if self.worker:
            self.worker._preview_cancelled = True
            self.worker.requestInterruption()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.stop()
        drain_worker(self.worker, timeout=300)


class CameraScanWorker(QThread):
    devices_ready = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            from camera_devices import enumerate_cameras
            self.devices_ready.emit(enumerate_cameras())
        except Exception as error:
            self.failed.emit(str(error))


class SpoutScanWorker(QThread):
    devices_ready = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            from spout_capture import enumerate_senders
            self.devices_ready.emit(enumerate_senders())
        except Exception as error:
            self.failed.emit(str(error))


def drain_worker(worker, timeout=100):
    """Keep a native scan/capture alive until it finishes after the form closes."""
    if worker is None or not worker.isRunning():
        return
    worker.requestInterruption()
    if not worker.wait(timeout):
        worker.setParent(None)
        retain_worker(worker)
        # Completion may race with attaching the finished callback.
        if worker.isFinished():
            _release_worker(worker)


def _release_worker(worker):
    if worker in _DRAINING:
        _DRAINING.remove(worker)
        if isValid(worker):
            worker.deleteLater()


def retain_worker(worker):
    if worker in _DRAINING:
        return
    _DRAINING.append(worker)
    worker.finished.connect(lambda: _release_worker(worker))


def wait_for_drained_workers(timeout_ms=6000):
    """Give stopping native workers a shared deadline before Qt is destroyed."""
    deadline = time.monotonic() + timeout_ms / 1000
    for worker in tuple(_DRAINING):
        if not isValid(worker) or not worker.isRunning():
            continue
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        if not worker.wait(remaining_ms):
            return False
    return True
