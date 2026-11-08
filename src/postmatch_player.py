"""One capture window and one clock for the complete post-match programme."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from postmatch_config import postmatch_options
from capture_surface import clear_capture,draw_locator,reference_canvas


class PostMatchWindow(QWidget):
    capture_key = 'postmatch'
    def __init__(self, playback):
        flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        if playback.model.settings.get('topmost', False):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.playback = playback
        self._drag = None
        self._locked = None
        self._topmost = playback.model.settings.get('topmost', False)
        self.setWindowTitle('麻将UI · 赛后连播')
        self.setProperty('i18nLiteral',True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.base = (1920,1080)
        self.setMinimumSize(320, 180)
        self.restore_geometry()

    def restore_geometry(self):
        screen = QApplication.primaryScreen().availableGeometry()
        geometry = self.playback.model.settings.get('windows', {}).get('postmatch')
        if geometry and len(geometry) == 4 and any(s.availableGeometry().intersects(QRectF(*geometry).toRect()) for s in QApplication.screens()):
            self.setGeometry(*map(round, geometry))
        else:
            self.setGeometry(reference_canvas())

    def save_geometry(self):
        manager = getattr(self, '_geometry_manager', None)
        if manager is not None:return manager.save_geometry(geometry_overrides={self.capture_key:[self.x(), self.y(), self.width(), self.height()]})
        candidate = copy.deepcopy(self.playback.model.settings)
        candidate.setdefault('windows', {})['postmatch'] = [self.x(), self.y(), self.width(), self.height()]
        self.playback.model.save_preferences(candidate)

    def sync_settings(self, shown=None, preview=None):
        settings = self.playback.model.settings
        if shown is None:
            shown = self.playback.active and settings.get('show_overlays',True) and settings.get('visible',{}).get(self.capture_key,True) and not getattr(self.playback,'capture_suppressed',False)
        if preview is None:preview = getattr(self.playback,'capture_preview',False)
        locked = bool(settings.get('locked', False) or not shown) and not preview
        topmost = bool(settings.get('topmost', False))
        if locked != self._locked:
            self._locked = locked
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, locked)
            if sys.platform == 'win32':
                import ctypes
                lib = ctypes.windll.user32
                get, put = lib.GetWindowLongPtrW, lib.SetWindowLongPtrW
                get.restype = put.restype = ctypes.c_ssize_t
                get.argtypes = [ctypes.c_void_p, ctypes.c_int]
                put.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
                handle = int(self.winId())
                style = get(handle, -20)
                put(handle, -20, (style | 0x20) if locked else (style & ~0x20))
        if topmost != self._topmost:
            self._topmost = topmost
            if sys.platform == 'win32':
                import ctypes
                fn = ctypes.windll.user32.SetWindowPos
                fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
                fn(int(self.winId()), ctypes.c_void_p(-1 if topmost else -2), 0, 0, 0, 0, 0x1 | 0x2 | 0x10)
            else:
                self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, topmost)

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            preview=getattr(self.playback,'capture_preview',False)
            shown=preview or (self.playback.active and self.playback.model.settings.get('show_overlays',True) and self.playback.model.settings.get('visible',{}).get(self.capture_key,True) and not getattr(self.playback,'capture_suppressed',False))
            clear_capture(p,QRectF(self.rect()),self.playback.model.settings if shown else {})
            if not shown:return
            if preview:
                draw_locator(p,QRectF(self.rect()),self.windowTitle());return
            frame = self.playback.frame()
            if frame is None:return
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.setOpacity(self.playback.opacity())
            p.drawImage(QRectF(0, 0, self.width(), self.height()), frame)
        finally:p.end()

    def mousePressEvent(self, event):
        if not self._locked and event.button() == Qt.MouseButton.LeftButton:
            self._drag = (event.globalPosition().toPoint(), self.pos())

    def mouseMoveEvent(self, event):
        if self._drag:
            self.move(self._drag[1] + event.globalPosition().toPoint() - self._drag[0])

    def mouseReleaseEvent(self, event):
        if self._drag:
            self._drag = None
            self._save_geometry_from_input()

    def _save_geometry_from_input(self):
        manager = getattr(self, '_geometry_manager', None) or getattr(self, 'manager', None)
        if manager is not None:return manager.display_configuration.run_native(self.save_geometry)
        try:return self.save_geometry()
        except (OSError, ValueError) as error:self.playback.failed.emit(str(error))

    def wheelEvent(self, event):
        if self._locked or not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.ignore()
            return
        factor = 1.06 if event.angleDelta().y() > 0 else 1/1.06
        width = max(320, min(3840, round(self.width()*factor)))
        self.resize(width, round(width*1080/1920))
        self._save_geometry_from_input()

    def closeEvent(self, event):
        self.playback.stop()
        if self.playback._closed:event.accept()
        else:event.ignore()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.playback.stop()
        elif event.key() == Qt.Key.Key_Space:
            self.playback.pause_resume()
        elif event.key() == Qt.Key.Key_Right:
            self.playback.next_page()
        else:
            super().keyPressEvent(event)


class PostMatchPlayback(QObject):
    changed = Signal()
    failed = Signal(str)
    _prepared = Signal(int, object, str)
    FADE_OUT = .5

    def __init__(self, model):
        super().__init__()
        self.model = model
        self.window = None
        self._owns_capture_window = True
        self.audio = None
        self.snapshot = None
        self.background = None
        self.options = postmatch_options(model.state.get('broadcast', {}).get('config', {}))
        self.state = 'idle'
        self.message = ''
        self._offset = 0.
        self._started = 0.
        self._generation = 0
        self._frame_key = None
        self._frame = None
        self._reported_second = -1
        self._closed = False
        self.page = None
        self.music_bus = None
        self._resume_state = 'playing'
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self._prepared.connect(self._on_prepared)

    @property
    def active(self):
        return self.state in ('playing', 'paused','complete')

    @property
    def preparing(self):
        return self.state == 'preparing'

    @property
    def music_active(self):
        return bool(self.audio and getattr(self.audio, 'music_active', False))

    @property
    def can_pause(self):
        return self.active or self.music_active or self.state == 'music_paused'

    @property
    def paused(self):
        return self.state in ('paused', 'music_paused')

    @property
    def chart_hold(self):
        return float(self.options['postmatch_chart_hold'])

    @property
    def stats_hold(self):
        return float(self.options['postmatch_stats_hold'])

    @property
    def body_duration(self):
        from postmatch_sequence import ENTRY_DURATION,TRANSITION_DURATION
        if self.page=='chart':return ENTRY_DURATION
        if self.page=='statistics':return ENTRY_DURATION+self.chart_hold+TRANSITION_DURATION
        from postmatch_sequence import duration
        return duration(self.chart_hold, self.stats_hold)

    @property
    def total_duration(self):
        return self.body_duration

    @property
    def elapsed(self):
        value = self._offset + (time.monotonic()-self._started if self.state == 'playing' else 0.)
        return max(0., min(value, self.total_duration))

    def status_text(self):
        if self.state=='complete':return '片尾已完成 · 保持最后一页，点击停止收起'
        if self.preparing:
            return '正在准备画面与声音…'
        if self.active:
            from postmatch_sequence import timeline
            phase = timeline(self.elapsed, self.chart_hold, self.stats_hold)['phase']
            label = {'entry': '图表入场', 'chart_hold': '折线图完整展示', 'transition': '转入简易统计', 'stats_hold': '简易统计完整展示', 'complete': '结束淡出'}[phase]
            return ('已暂停 · ' if self.state == 'paused' else '') + f'{label}  {self.elapsed:.0f} / {self.total_duration:.0f} 秒' + (' · ' + self.message if self.message else '')
        if self.state == 'music_paused':
            return '动画已结束 · 背景音乐已暂停'
        if self.music_active:
            return '动画已结束 · 背景音乐持续播放'
        return self.message or '折线图 → 简易统计 · 完整画面各停留 15 秒'

    def start(self,page=None):
        if page not in (None,'chart','statistics'):raise ValueError('片尾页面无效')
        if self._closed:
            return
        if self.model.state.get('phase') != 'finished':
            raise ValueError('请在当前半庄结束后播放赛后连播')
        if self.preparing:
            return
        self.page=page
        self.timer.stop()
        if self.audio:
            self.audio.finish_effects()
            if not self.music_active:
                self.audio.close()
        if not self.model.settings.get('show_overlays', True):
            self.model.setting('show_overlays', True)
        self.options = postmatch_options(self.model.state['broadcast']['config'])
        self.snapshot = SimpleNamespace(state=copy.deepcopy(self.model.state), settings=copy.deepcopy(self.model.settings),
                                        path=self.model.path, event=copy.deepcopy(getattr(self.model, 'event', None)),
                                        last_info=copy.deepcopy(getattr(self.model, 'last_info', None)))
        from postmatch_artwork import prepare_postmatch_model
        prepare_postmatch_model(self.snapshot)
        self.background = None
        background_path = self.options['postmatch_background_path']
        if background_path:
            image = QImage(str(self._resolve_path(background_path)))
            if image.isNull():
                raise ValueError('无法读取赛后背景图片，请重新选择图片')
            # Two smooth resamplings provide a pre-defocused background before the dissolve.
            self.background = image.scaled(64, 36, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation).scaled(1920, 1080, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        if self.window is None:
            self.window = PostMatchWindow(self)
        self.window.restore_geometry()
        self._frame_key = None
        self._frame = None
        self._offset = 0.
        self.message = ''
        self._generation += 1
        token = self._generation
        self.state = 'preparing'
        self.changed.emit()
        from postmatch_audio import PostMatchAudio
        audio = self.audio if self.music_active else PostMatchAudio()
        self.audio = audio
        options = dict(self.options)
        music_path = str(self._resolve_path(options['postmatch_music_path'])) if options['postmatch_music_path'] else ''

        def prepare_audio():
            error = ''
            try:
                audio.prepare(chart_hold=options['postmatch_chart_hold'], stats_hold=options['postmatch_stats_hold'],
                              music_path=music_path,
                              music_volume=options['postmatch_music_volume'], sfx_volume=options['postmatch_sfx_volume'],
                              music_enabled=options['postmatch_music_enabled'] and self.music_bus is None, sfx_enabled=options['postmatch_sfx_enabled'])
                error = audio.error or ''
            except Exception as exc:
                error = str(exc)
            self._prepared.emit(token, audio, error)

        threading.Thread(target=prepare_audio, name='PostMatchAudioPrepare', daemon=True).start()

    def _resolve_path(self, reference):
        from media_store import resolve_media_path
        return resolve_media_path(self.model.path,reference)

    def _on_prepared(self, token, audio, error):
        if self._closed or token != self._generation or self.state != 'preparing':
            audio.close()
            return
        if error:
            audio.close()
            self.state = 'idle'
            self.message = error if error.startswith('声音') else '声音准备失败：' + error
            self.failed.emit(self.message)
            self.changed.emit()
            return
        if not self.model.settings.get('show_overlays', True):
            self.model.setting('show_overlays', True)
        if not self.model.state['broadcast']['config'].get('postmatch_music_enabled', True):
            # A mute made while the worker was preparing must win over its
            # captured options, including when a previous music loop was live.
            audio.set_music_enabled(False)
            self.options['postmatch_music_enabled'] = False
        from postmatch_sequence import ENTRY_DURATION
        self._offset = ENTRY_DURATION+self.chart_hold if self.page=='statistics' else 0.
        if not self._audio_action('play', self._offset):
            return
        self._started = time.monotonic()
        self.state = 'playing'
        self.window.sync_settings()
        self.window.show()
        self.window.raise_()
        self.timer.start()
        self.changed.emit()

    def pause_resume(self):
        if self.state in ('playing','complete'):
            self._resume_state=self.state
            position = self.elapsed
            if not self._audio_action('pause') or not self._audio_action('seek', position):
                return
            self._offset = position
            self.state = 'paused'
            self.timer.stop()
        elif self.state == 'paused':
            if not self._audio_action('resume'):
                return
            self._started = time.monotonic()
            self.state = self._resume_state
            if self.state=='playing':self.timer.start()
        elif self.state == 'music_paused':
            if not self._audio_action('resume'):
                return
            self.state = 'complete'
        elif self.music_active:
            if not self._audio_action('pause'):
                return
            self.state = 'music_paused'
        self.changed.emit()

    def _audio_action(self, method, *args):
        if self.audio is None:
            return True
        error = ''
        try:
            success = getattr(self.audio, method)(*args)
            if success is False or self.audio.error:
                error = self.audio.error or '声音设备未完成播放操作'
        except Exception as exc:
            error = '声音操作失败：' + str(exc)
        if error:
            self.stop()
            self.message = error
            self.failed.emit(error)
            self.changed.emit()
            return False
        return True

    def seek(self, position):
        if not self.active:
            return
        position = max(0., min(float(position), self.body_duration))
        if not self._audio_action('seek', position):
            return
        if self.state == 'paused' and not self._audio_action('pause'):
            return
        self._offset = position
        self._started = time.monotonic()
        self._frame_key = None
        self.window.update()
        self.changed.emit()

    def next_page(self):
        self.seek(5.967 + self.chart_hold)

    def stop(self, completed=False):
        self._generation += 1
        self.timer.stop()
        if self.audio:
            if completed:
                self.audio.finish_effects()
            else:
                self.audio.stop()
                self.audio.close()
        self.state = 'complete' if completed else 'idle'
        self._offset = self.total_duration if completed else 0.
        self.message = '赛后连播已完成' if completed else ''
        if self.window:
            self.window.update()
        self.changed.emit()

    def opacity(self):
        return 1.

    def frame(self):
        if self.snapshot is None:
            return None
        from postmatch_sequence import timeline
        from postmatch_artwork import draw_postmatch
        elapsed = self.elapsed
        phase = timeline(elapsed, self.chart_hold, self.stats_hold)['phase']
        key = phase if phase in ('stats_hold', 'complete') else round(elapsed*60)
        if key != self._frame_key:
            frame = QImage(1920, 1080, QImage.Format.Format_ARGB32_Premultiplied)
            frame.fill(Qt.GlobalColor.transparent)
            painter = QPainter(frame)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            try:
                draw_postmatch(painter, self.snapshot, elapsed, self.chart_hold, self.stats_hold, self.background)
            finally:
                painter.end()
            self._frame, self._frame_key = frame, key
        return self._frame

    def _tick(self):
        if self.elapsed >= self.total_duration:
            self.stop(completed=True)
            return
        self.window.update()
        second = int(self.elapsed)
        if second != self._reported_second:
            self._reported_second = second
            self.changed.emit()

    def ensure_capture_window(self):
        if self.window is None:self.window = PostMatchWindow(self)
        return self.window

    def bind_capture_window(self, window):
        self.window = window
        self._owns_capture_window = False

    def sync_display(self):
        if not self.model.settings.get('show_overlays', True) and (self.active or self.preparing or self.music_active):
            self.stop()
        if self.window and not self._closed:
            self.window.sync_settings()
            if not self.window.isVisible():self.window.show()
            self.window.update()

    def on_model_changed(self, before, after, kind):
        # Display settings may change during a frozen programme; changing the hand ends it.
        if kind in ('import', 'clear', 'restore', 'start_match', 'new_match', 'next_hand'):
            if self.active or self.preparing or self.music_active or self.state == 'music_paused':
                self.stop()

    def set_music_enabled(self, enabled):
        if self.music_bus is not None:
            self.music_bus.set_enabled(enabled);return
        if self.audio:
            try:
                result = self.audio.set_music_enabled(bool(enabled))
                if result is False or self.audio.error:
                    self.message = self.audio.error or '背景音乐开关未生效'
                    self.failed.emit(self.message)
            except Exception as exc:
                self.message = '背景音乐操作失败：'+str(exc)
                self.failed.emit(self.message)
        if not enabled and self.state == 'music_paused':
            self.state = 'complete'
        self.changed.emit()

    def close(self):
        self._closed = True
        self.stop()
        if self.audio:
            self.audio.close()
        if self.window and self._owns_capture_window:
            self.window.close()
