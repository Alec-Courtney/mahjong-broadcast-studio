"""One event-data snapshot and clock for the complete team opening."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from postmatch_player import PostMatchWindow
from capture_surface import reference_canvas


class MatchOpeningWindow(PostMatchWindow):
    capture_key = 'opening'
    """Share the programme window's drag, scale, lock and topmost behavior."""
    def __init__(self, playback):
        super().__init__(playback)
        self.setWindowTitle('麻将UI · 赛前开场')

    def restore_geometry(self):
        screen = QApplication.primaryScreen().availableGeometry()
        geometry = self.playback.model.settings.get('windows', {}).get('opening')
        if (geometry and len(geometry) == 4
                and any(s.availableGeometry().intersects(QRectF(*geometry).toRect())
                        for s in QApplication.screens())):
            self.setGeometry(*map(round, geometry))
        else:
            self.setGeometry(reference_canvas())

    def save_geometry(self):
        manager = getattr(self, '_geometry_manager', None)
        if manager is not None:return manager.save_geometry(geometry_overrides={self.capture_key:[self.x(), self.y(), self.width(), self.height()]})
        import copy
        candidate = copy.deepcopy(self.playback.model.settings)
        candidate.setdefault('windows', {})['opening'] = [self.x(), self.y(), self.width(), self.height()]
        self.playback.model.save_preferences(candidate)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.playback.stop()
        else:
            event.ignore()


class MatchOpeningPlayback(QObject):
    changed = Signal()
    finished = Signal()

    def __init__(self, model, guard=None):
        super().__init__()
        self.model = model
        self.guard = guard
        self.window = None
        self._owns_capture_window = True
        self.snapshot = None
        self.state = 'idle'
        self._started = 0.
        self._offset = 0.
        self.page = None
        self._reported_phase = None
        self._resume_state = 'playing'
        self._closed = False
        self._frame = None
        self._frame_number = None
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)

    @property
    def active(self):
        return self.state in ('playing','paused','holding')

    @property
    def paused(self):return self.state=='paused'

    @property
    def current_page(self):
        if self.page is not None:return self.page
        from match_opening import opening_timeline
        return {'title':'title','roster':'roster','focus':'focus','exit':'focus','complete':'focus'}[opening_timeline(self.elapsed,self.duration).phase]

    @property
    def elapsed(self):
        return min(self.duration,max(0.,self._offset+(time.monotonic()-self._started if self.state=='playing' else 0.))) if self.active else 0.

    @property
    def duration(self):
        return 2. if self.page is not None else self.snapshot.duration if self.snapshot is not None else 18.

    def start(self,page=None):
        if self._closed:
            raise ValueError('赛前开场窗口已关闭')
        if self.active:
            raise ValueError('赛前开场正在播放')
        if page not in (None,'title','roster','focus'):raise ValueError('开场页面无效')
        if self.guard and page is None:
            self.guard()
        from match_opening import prepare_opening
        snapshot = prepare_opening(self.model,require_distinct_teams=page not in ('title','focus'))
        if self.window is None:
            self.window = MatchOpeningWindow(self)
        self.snapshot = snapshot
        self.page=page;self._offset=0.;self._reported_phase=None
        self._started = time.monotonic()
        self._frame = self._frame_number = None
        self.state = 'playing'
        self.changed.emit()
        self.sync_display()
        self.timer.start()
        return True

    def pause_resume(self):
        if self.state in ('playing','holding'):
            self._offset=self.elapsed;self._resume_state=self.state;self.state='paused';self.timer.stop()
        elif self.state=='paused':
            self._started=time.monotonic();self.state=self._resume_state
            if self.state=='playing':self.timer.start()
        self.changed.emit()

    def stop(self, completed=False):
        was_active = self.active
        self.state = 'complete' if completed else 'idle'
        self.timer.stop()
        if self.window:
            self.window.update()
        if completed and was_active:
            self.finished.emit()
        if was_active:
            self.changed.emit()

    def opacity(self):
        # The shared renderer already includes the opening/closing fades.
        return 1.

    def frame(self):
        if not self.active or self.snapshot is None:
            return None
        number = int(min(self.elapsed, self.duration)*60)
        if number != self._frame_number:
            from match_opening import DESIGN_SIZE, draw_opening
            image = QImage(*DESIGN_SIZE, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            try:
                draw_opening(painter, self.snapshot, number/60,page=self.page)
            finally:
                painter.end()
            self._frame, self._frame_number = image, number
        return self._frame

    def ensure_capture_window(self):
        if self.window is None:self.window = MatchOpeningWindow(self)
        return self.window

    def bind_capture_window(self, window):
        self.window = window
        self._owns_capture_window = False

    def sync_display(self):
        if self.active and (not self.model.settings.get('show_overlays', True)
                            or self.model.settings.get('broadcast_scene') == 'clean'):
            self.stop()
        if self.window and not self._closed:
            self.window.sync_settings()
            if self.active:
                self.window.show()
                self.window.raise_()
                self.window.update()
            else:
                if not self.window.isVisible():self.window.show()
                self.window.update()

    def on_model_changed(self, before, after, kind):
        if kind not in ('opening', 'sidebar', 'corner_header', 'postmatch', 'commentary', 'riichi_effect'):
            self.stop()

    def _tick(self):
        if not self.active:
            return
        if self.elapsed >= self.duration:
            if self.page is None:self.stop(completed=True)
            else:
                self._offset=self.duration;self.state='holding';self.timer.stop();self.changed.emit()
        elif self.window:
            self.window.update()
            if self.current_page!=self._reported_phase:self._reported_phase=self.current_page;self.changed.emit()

    def status_text(self):
        if self.active:
            if self.page is not None:return ('已暂停 · ' if self.paused else '')+{'title':'赛季标题','roster':'队伍介绍','focus':'本场选手'}[self.page]+(' · 保持显示' if self.state=='holding' else '')
            return '正在播放：赛季标题 → 四队名单 → 本场出战 → 四席入场'
        if self.state == 'complete':
            return '开场已结束，已衔接四席选手入场。'
        return '读取当前四席所属队伍的全员名单、队徽、队色及出战人选。'

    def close(self):
        self.stop()
        self._closed = True
        if self.window and self._owns_capture_window:
            self.window.hide()
            self.window.deleteLater()
            self.window = None
