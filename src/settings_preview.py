"""Paint broadcast artwork inside a settings widget without a capture window."""
from PySide6.QtCore import QElapsedTimer, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget


class ArtworkPreview(QWidget):
    def __init__(self, design_size, draw, *, height=200, parent=None, cached=True):
        super().__init__(parent)
        self.design_size = design_size
        self.draw = draw
        self.cached = cached
        self._frame = None
        self._frame_size = None
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        width, height = self.design_size
        return QSize(min(640, round(self.height() * width / height)), self.height())

    def set_design_size(self, size):
        if size != self.design_size:
            self.design_size = size
            self.updateGeometry()
            self.invalidate()

    def invalidate(self):
        self._frame = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            if not self.cached:
                self._paint_preview(painter)
                return
            ratio = self.devicePixelRatioF()
            frame_size = (self.width(), self.height(), ratio)
            if self._frame is None or frame_size != self._frame_size:
                frame = QImage(round(self.width() * ratio), round(self.height() * ratio),
                               QImage.Format.Format_RGB32)
                frame.setDevicePixelRatio(ratio)
                renderer = QPainter(frame)
                try:
                    self._paint_preview(renderer)
                finally:
                    renderer.end()
                self._frame, self._frame_size = frame, frame_size
            painter.drawImage(0, 0, self._frame)
        finally:
            painter.end()

    def _paint_preview(self, painter):
        painter.fillRect(self.rect(), QColor('#18232e'))
        if self.width() <= 12 or self.height() <= 12:
            return
        width, height = self.design_size
        scale = min((self.width() - 12) / width, (self.height() - 12) / height)
        painter.translate((self.width() - width * scale) / 2, (self.height() - height * scale) / 2)
        painter.scale(scale, scale)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setClipRect(QRectF(0, 0, width, height))
        self.draw(painter)


class RiichiAnimationPreview(ArtworkPreview):
    def __init__(self, model, parent=None):
        from riichi_effect import DESIGN_SIZE

        super().__init__(DESIGN_SIZE, self.draw_frame, height=200, parent=parent, cached=False)
        self.model = model
        self.snapshot = None
        self.clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.advance)

    def play(self, seat):
        from riichi_effect import snapshot_riichi

        self.snapshot = snapshot_riichi(self.model, seat)
        self.clock.start()
        self.timer.start()
        self.update()

    def stop(self):
        if self.snapshot is None:
            return
        self.timer.stop()
        self.snapshot = None
        self.update()

    def advance(self):
        from riichi_effect import RIICHI_DURATION

        if self.clock.elapsed() >= RIICHI_DURATION * 1000:
            self.stop()
        else:
            self.update()

    def draw_frame(self, painter):
        from i18n import tr
        from riichi_effect import draw_riichi

        if self.snapshot is not None:
            draw_riichi(painter, self.snapshot, self.clock.elapsed() / 1000)
        else:
            font = QFont()
            font.setPixelSize(62)
            painter.setFont(font)
            painter.setPen(QColor('#c5d3dd'))
            painter.drawText(QRectF(0, 0, *self.design_size), Qt.AlignmentFlag.AlignCenter, tr('点击播放立直预览'))

    def hideEvent(self, event):
        self.stop()
        super().hideEvent(event)
