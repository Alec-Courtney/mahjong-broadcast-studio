"""The default full-screen surface for programmes, review panels and effects."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication

from capture_surface import clear_capture, draw_locator,reference_canvas
from postmatch_player import PostMatchWindow


class FullscreenCaptureWindow(PostMatchWindow):
    capture_key = 'fullscreen'

    def __init__(self, manager):
        self.manager = manager
        super().__init__(manager.postmatch)
        self.setWindowTitle('麻将UI · 全屏节目')

    def restore_geometry(self):
        saved = self.manager.model.settings.setdefault('windows', {})
        # Import one previous full-screen placement; subsequent scenes share it.
        geometry = next((saved[key] for key in ('fullscreen', 'opening', 'postmatch', 'riichi_effect')
                         if key in saved and len(saved[key]) == 4 and
                         any(screen.availableGeometry().intersects(QRectF(*saved[key]).toRect())
                             for screen in QApplication.screens())), None)
        screen = QApplication.primaryScreen().availableGeometry()
        if geometry and self.manager.model.settings.get('fullscreen_canvas_version',0)<1:
            scale=min(screen.width()/1920,screen.height()/1080)
            old_width,old_height=round(1920*scale),round(1080*scale)
            old=[screen.x()+(screen.width()-old_width)//2,screen.y()+(screen.height()-old_height)//2,old_width,old_height]
            if all(abs(a-b)<=1 for a,b in zip(geometry,old)):geometry=None
        if geometry:
            x, y, width, _ = geometry
            width = max(320, round(width))
        else:
            canvas=reference_canvas();x,y,width,_=canvas.getRect()
        self.setGeometry(round(x), round(y), width, round(width*1080/1920))
        saved['fullscreen'] = [self.x(), self.y(), self.width(), self.height()]
        self.manager.model.settings['fullscreen_canvas_version']=1

    def save_geometry(self):
        self.manager.save_geometry()

    def active_playback(self):
        for key in ('opening', 'postmatch'):
            playback = getattr(self.manager, key)
            if playback.active:
                return playback
        return None

    def content_visible(self):
        settings = self.manager.model.settings
        return bool(not settings.get('split_fullscreen_windows',False)
                and self.manager.capture_enabled('fullscreen') and settings.get('show_overlays', True)
                and (self.active_playback() is not None or self.manager.fullscreen_panel()
                     or self.manager.content_visible('riichi_effect')))

    def sync_settings(self):
        # Full-screen effects remain click-through; explicit location preview can move.
        super().sync_settings(shown=self.content_visible() and (self.active_playback() is not None or self.manager.fullscreen_panel() is not None)
                              and not self.manager.capture_suppressed('fullscreen'),
                              preview=self.manager.capture_previewing('fullscreen'))

    def paintEvent(self, event):
        preview = self.manager.capture_previewing('fullscreen')
        shown = not self.manager.capture_suppressed('fullscreen') and (preview or self.content_visible())
        painter = QPainter(self)
        try:
            clear_capture(painter, QRectF(self.rect()), self.manager.model.settings if shown else {})
            if not shown:
                return
            if preview:
                draw_locator(painter, QRectF(self.rect()), '全屏节目 · 开场 / 赛后 / 走势 / 统计 / 四人条件 / 立直')
                return
            playback = self.active_playback()
            if playback is not None:
                frame = playback.frame()
                if frame is not None:
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    painter.setOpacity(playback.opacity())
                    painter.drawImage(QRectF(self.rect()), frame)
            else:
                panel=self.manager.fullscreen_panel()
                if panel:
                    width,height=self.manager.base_size(panel)
                    scale=min(self.width()/width,self.height()/height)
                    painter.save();painter.translate((self.width()-width*scale)/2,(self.height()-height*scale)/2)
                    painter.scale(scale,scale);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    self.manager.paint_content(painter,panel);painter.restore()
            if self.manager.content_visible('riichi_effect'):
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.scale(self.width()/1920, self.height()/1080)
                self.manager.paint_content(painter, 'riichi_effect')
                painter.restore()
        finally:
            painter.end()

    def closeEvent(self, event):
        if self.manager.closing:
            event.accept()
            return
        self.manager.programme.stop()
        event.ignore()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.manager.programme.stop()
        elif event.key() == Qt.Key.Key_Space:self.manager.programme.pause_resume()
        elif not self.manager.opening.active:super().keyPressEvent(event)
        else:
            event.ignore()
