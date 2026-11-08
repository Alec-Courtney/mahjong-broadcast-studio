"""Transparent captures and physical-pixel geometry shared by all outputs."""
import math
from PySide6.QtCore import Qt,QRect,QObject,QEvent,QTimer
from PySide6.QtGui import QColor, QPen, QPainter
from PySide6.QtWidgets import QApplication


def capture_screen(target=None):
    """Resolve a window or screen without assuming it belongs to the primary."""
    if target is not None:
        screen_method=getattr(target,'screen',None)
        screen=screen_method() if callable(screen_method) else target
        if screen is not None:return screen
    return QApplication.primaryScreen()


def screen_pixel_ratio(target=None):
    screen=capture_screen(target)
    ratio=float(screen.devicePixelRatio()) if screen is not None else 1.
    return ratio if math.isfinite(ratio) and ratio>0 else 1.


def reference_canvas(target=None):
    """Desktop coordinates for a 1920x1080 physical-pixel broadcast canvas."""
    screen=capture_screen(target)
    origin=screen.geometry().topLeft() if screen is not None else QRect().topLeft()
    ratio=screen_pixel_ratio(screen)
    return QRect(origin.x(),origin.y(),round(1920/ratio),round(1080/ratio))


def reference_width(window):
    """100% always means the design's physical width on the owning monitor."""
    return max(1,round(window.base[0]/screen_pixel_ratio(window)))


def dpi_scaled_geometry(geometry,old_ratio,new_ratio):
    """Keep physical dimensions after a monitor change; retain the dragged origin."""
    factor=old_ratio/new_ratio
    return QRect(geometry.x(),geometry.y(),max(1,round(geometry.width()*factor)),max(1,round(geometry.height()*factor)))


def capture_framebuffer_size(window,native_ratio=None):
    """Qt capture dimensions round to the nearest native pixel, with .5 up."""
    if native_ratio is None:
        native=getattr(window,'devicePixelRatioF',None)
        native_ratio=float(native()) if callable(native) else screen_pixel_ratio(window)
    return max(1,math.floor(window.width()*native_ratio+.5)),max(1,math.floor(window.height()*native_ratio+.5))


def _pixel_round(value):
    return math.floor(value+.5) if value>=0 else math.ceil(value-.5)


def _output_physical_size(window):
    value=getattr(window,'output_physical_size',None)
    if callable(value):value=value()
    if value is None:return None
    try:
        if callable(getattr(value,'width',None)):width,height=value.width(),value.height()
        else:width,height=value
        width,height=float(width),float(height)
        if not all(math.isfinite(number) and number>0 for number in (width,height)):raise ValueError
    except (TypeError,ValueError):raise ValueError('捕获窗口的播出像素尺寸无效') from None
    return width,height


def capture_transform(window):
    """OBS placement in physical 1920x1080 pixels, local to each capture's screen.

    Qt uses monitor-local logical distances on mixed-DPI desktops. Scaling a
    distance from the primary origin gives incorrect positions on other screens.
    Capture density (the HUD's 2x surface) affects source size, never placement.
    output_physical_size, when supplied as a pair/QSize or zero-argument callable,
    is the final output size: HUD callers supply 619x121, not its 2x dimensions.
    """
    screen=capture_screen(window)
    origin=screen.geometry().topLeft() if screen is not None else QRect().topLeft()
    ratio=screen_pixel_ratio(screen)
    native=getattr(window,'devicePixelRatioF',None)
    native=float(native()) if callable(native) else ratio
    if not math.isfinite(native) or native<=0:native=ratio
    density=float(getattr(window,'capture_pixel_ratio',1))
    if not math.isfinite(density) or density<=0:raise ValueError('捕获窗口的像素倍率无效')
    # Usually native == ratio. Correct a pending native DPR update as well so
    # exporting immediately after a move cannot multiply the intended footprint.
    output_size=_output_physical_size(window)
    if output_size is not None:
        width,height=capture_framebuffer_size(window,native)
        scale=dict(x=output_size[0]/width,y=output_size[1]/height)
    else:
        factor=ratio/native/density;scale=dict(x=factor,y=factor)
    return dict(pos=dict(x=_pixel_round((window.x()-origin.x())*ratio),y=_pixel_round((window.y()-origin.y())*ratio)),
                scale=scale)


class CaptureDpiMonitor(QObject):
    """Notify the layout owner once when its capture moves between DPI scales.

    The manager owns grouped/derived geometry, so callback(window, old, new)
    updates that layout rather than resizing a 2x HUD or four-seat surface alone.
    Call rebase() after restoring a saved layout and dispose() before shutdown.
    """
    def __init__(self,window,callback):
        super().__init__(window)
        self.window=window;self.callback=callback;self._screen=None;self._handle=None
        self._disposed=False;self._ratio=screen_pixel_ratio(window)
        self._timer=QTimer(self);self._timer.setSingleShot(True);self._timer.timeout.connect(self._check)
        window.installEventFilter(self);self._bind()

    def _bind(self):
        handle=self.window.windowHandle()
        if handle is not self._handle:
            if self._handle is not None:
                try:self._handle.screenChanged.disconnect(self._schedule)
                except (RuntimeError,TypeError):pass
            self._handle=handle
            if handle is not None:handle.screenChanged.connect(self._schedule)
        screen=capture_screen(self.window)
        if screen is not self._screen:
            if self._screen is not None:
                for signal in (self._screen.logicalDotsPerInchChanged,self._screen.physicalDotsPerInchChanged):
                    try:signal.disconnect(self._schedule)
                    except (RuntimeError,TypeError):pass
            self._screen=screen
            if screen is not None:
                screen.logicalDotsPerInchChanged.connect(self._schedule);screen.physicalDotsPerInchChanged.connect(self._schedule)

    def _schedule(self,*_):
        if not self._disposed:self._timer.start(0)

    def _check(self):
        if self._disposed:return
        self._bind();ratio=screen_pixel_ratio(self.window);previous=self._ratio
        self._ratio=ratio
        if not math.isclose(previous,ratio,rel_tol=1e-6):self.callback(self.window,previous,ratio)

    def rebase(self):
        if self._disposed:return
        self._timer.stop();self._bind();self._ratio=screen_pixel_ratio(self.window)

    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.Show,QEvent.Type.WinIdChange,QEvent.Type.Move,QEvent.Type.DevicePixelRatioChange):self._schedule()
        return False

    def dispose(self):
        if self._disposed:return
        self._disposed=True;self._timer.stop();self.window.removeEventFilter(self)
        if self._handle is not None:
            try:self._handle.screenChanged.disconnect(self._schedule)
            except (RuntimeError,TypeError):pass
        if self._screen is not None:
            for signal in (self._screen.logicalDotsPerInchChanged,self._screen.physicalDotsPerInchChanged):
                try:signal.disconnect(self._schedule)
                except (RuntimeError,TypeError):pass
        self.callback=None


def clear_capture(painter, rect, settings):
    background = settings.get('capture_bg', 'transparent')
    color = QColor('#00ff00' if background == 'green' else '#0000ff') if background != 'transparent' else QColor(0, 0, 0, 0)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    painter.fillRect(rect, color)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)


def draw_locator(painter, rect, title):
    painter.save()
    painter.setPen(QPen(QColor('#62b8e8'), 2, Qt.PenStyle.DashLine))
    painter.drawRect(rect.adjusted(2, 2, -2, -2))
    painter.fillRect(4, 4, min(rect.width()-8, 420), 30, QColor('#17354f'))
    painter.setPen(QColor('white'))
    painter.drawText(rect.adjusted(12, 6, -6, -6), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, title+' · 定位预览')
    painter.restore()
