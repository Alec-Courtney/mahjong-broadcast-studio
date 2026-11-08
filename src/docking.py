"""Native operator panels and their layout; independent of match state.

Only a versioned presentation preference is persisted. Moving, closing or
restoring a panel never changes the table, tournament archive or broadcast.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager

from PySide6.QtCore import QByteArray, QEvent, QObject, QRect, QTimer, Qt,QPoint
from PySide6.QtGui import QColor,QCursor,QPainter,QPen
from PySide6.QtWidgets import QApplication, QDockWidget, QMainWindow, QScrollArea,QWidget,QLabel,QHBoxLayout,QToolButton,QToolBar,QStyle

from i18n import tr


class PanelCollection:
    """Small construction adapter: builders describe panels, not window layout."""

    def __init__(self):
        self.widgets = []

    def addWidget(self, widget, *_):
        self.widgets.append(widget)

    def insertWidget(self, index, widget, *_):
        self.widgets.insert(index, widget)


class NavigationResizeHandle(QWidget):
    """Resize the fixed navigation toolbar from inside the main window."""

    DEFAULT_WIDTH = 142
    MIN_WIDTH = 116
    MAX_WIDTH = 360

    def __init__(self, workspace, scroll, toolbar, parent):
        super().__init__(parent)
        self.workspace, self.scroll, self.toolbar = workspace, scroll, toolbar
        self.preferred_width = self.DEFAULT_WIDTH
        self._drag_start = None
        self.setObjectName('navigationResizeHandle')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedWidth(7)
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setToolTip('拖动调整左侧栏宽度')

    def restore_width(self, width=None):
        if type(width) is not int:
            width = self.DEFAULT_WIDTH
        self.preferred_width = min(self.MAX_WIDTH, max(self.MIN_WIDTH, width))
        self.fit_width()

    def fit_width(self):
        if self._drag_start is None:
            self._show_width(self.preferred_width)

    def _show_width(self, requested_width):
        window = self.workspace.window
        current_width = self.scroll.minimumWidth()
        scrollbar_width = self.scroll.verticalScrollBar().sizeHint().width()
        minimum = max(self.MIN_WIDTH, self.scroll.widget().minimumSizeHint().width() + scrollbar_width)
        available = current_width + window.width() - window.minimumSizeHint().width()
        width = max(minimum, min(self.MAX_WIDTH, available, requested_width))
        if width == current_width:
            return
        self.scroll.setFixedWidth(width)
        self.parentWidget().layout().invalidate()
        self.toolbar.layout().invalidate()
        window.layout().invalidate()
        window.layout().activate()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = (event.globalPosition().x(), self.scroll.width())
            event.accept()
        else:
            event.ignore()

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            start_x, start_width = self._drag_start
            self._show_width(start_width + round(event.globalPosition().x() - start_x))
            event.accept()
        else:
            event.ignore()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            _, start_width = self._drag_start
            self._drag_start = None
            if self.scroll.width() != start_width:
                self.preferred_width = self.scroll.width()
                self.workspace.schedule_save()
            event.accept()
        else:
            event.ignore()


def visible_geometry(rect, screens):
    """Keep a usable title bar on an attached screen after monitor changes."""
    screens = list(screens)
    if not screens:
        return rect
    target = max(screens, key=lambda screen: rect.intersected(screen).width()
                 * rect.intersected(screen).height())
    width = min(max(240, rect.width()), target.width())
    height = min(max(120, rect.height()), target.height())
    x = min(max(rect.x(), target.left()), target.right() - width + 1)
    y = min(max(rect.y(), target.top()), target.bottom() - height + 1)
    return QRect(x, y, width, height)


class PanelDropOverlay(QWidget):
    def __init__(self,workspace,key,dock):
        # A floating native dock covers ordinary child widgets while dragging.
        # This visual-only tool stays above it without taking its mouse grab.
        super().__init__(dock,Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|
                         Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.WindowTransparentForInput|
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.workspace=workspace;self.key=key;self.edge=None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet('background: transparent;');self.hide()

    def zones(self):
        x,y=self.width()//2,self.height()//2;size=36;gap=5
        return {'left':QRect(x-size*3//2-gap,y-size//2,size,size),'right':QRect(x+size//2+gap,y-size//2,size,size),
                'above':QRect(x-size//2,y-size*3//2-gap,size,size),'below':QRect(x-size//2,y+size//2+gap,size,size)}

    def edge_at(self,point):
        for edge,rect in self.zones().items():
            if rect.contains(point):return edge
        return None

    def paintEvent(self,event):
        p=QPainter(self)
        try:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source);p.fillRect(self.rect(),Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            if self.edge:
                rect=self.rect().adjusted(2,2,-2,-2)
                if self.edge=='left':rect.setWidth(rect.width()//2)
                elif self.edge=='right':rect.setLeft(rect.center().x())
                elif self.edge=='above':rect.setHeight(rect.height()//2)
                else:rect.setTop(rect.center().y())
                p.fillRect(rect,QColor(42,139,213,100));p.setPen(QPen(QColor('#46b8ff'),3));p.drawRect(rect)
            for edge,rect in self.zones().items():
                p.fillRect(rect,QColor('#2387c8') if edge==self.edge else QColor('#1c3449'))
                p.setPen(QColor('white'));p.drawText(rect,int(Qt.AlignmentFlag.AlignCenter),{'left':'←','right':'→','above':'↑','below':'↓'}[edge])
        finally:p.end()


class PanelTitleBar(QWidget):
    def __init__(self,workspace,key,dock):
        super().__init__(dock);self.workspace=workspace;self.key=key;self.dock=dock
        layout=QHBoxLayout(self);layout.setContentsMargins(7,2,3,2);layout.setSpacing(3)
        self.title=QLabel(dock.windowTitle(),self);self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents);layout.addWidget(self.title,1)
        self.controls=[]
        for icon,callback in ((QStyle.StandardPixmap.SP_TitleBarNormalButton,lambda:dock.setFloating(not dock.isFloating())),(QStyle.StandardPixmap.SP_TitleBarCloseButton,dock.close)):
            control=QToolButton(self);control.setIcon(self.style().standardIcon(icon));control.setAutoRaise(True);control.clicked.connect(callback);layout.addWidget(control)
            self.controls.append(control)

    def mousePressEvent(self,event):
        event.ignore()

    def mouseMoveEvent(self,event):
        event.ignore()

    def mouseReleaseEvent(self,event):event.ignore()
    def mouseDoubleClickEvent(self,event):
        event.ignore()


class OperatorDock(QDockWidget):
    """Qt owns dragging; an explicit arrow drop adds a local split afterward."""
    def __init__(self,title,window,workspace,key):
        self.workspace=workspace;self.key=key
        super().__init__(title,window)

    def event(self,event):
        workspace=self.workspace;kind=event.type()
        moving=(kind==QEvent.Type.MouseMove and workspace._drag_native and workspace._drag_source==self.key)
        guided=moving and self.isFloating()
        if guided:
            workspace._drag_started=True;workspace._automatic_rows=False
            # Read the panel under the cursor before Qt previews a new layout.
            workspace._update_drag_target(event.globalPosition().toPoint(),event.modifiers())
        releasing=(kind==QEvent.Type.MouseButtonRelease and event.button()==Qt.MouseButton.LeftButton
                   and workspace._drag_native and workspace._drag_source==self.key)
        destination=None
        if releasing and not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            destination=workspace.arrow_destination(event.globalPosition().toPoint())
            if destination:workspace.window.setUpdatesEnabled(False)
        # Calling the real handler preserves floating, gap previews, edges,
        # nested docking and all native drops outside the four arrow buttons.
        result=super().event(event)
        if releasing:workspace.finish_native_drag(destination)
        elif kind==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
            title=self.titleBarWidget()
            if title and title.rect().contains(title.mapFromGlobal(event.globalPosition().toPoint())):
                workspace.begin_drag(self.key,native=True)
        elif moving:
            if self.isFloating():
                workspace._drag_started=True;workspace._automatic_rows=False
                if not guided:workspace._update_drag_target(event.globalPosition().toPoint(),event.modifiers())
                if workspace._drag_target is not None:workspace.drop_overlays[workspace._drag_target].raise_()
        elif kind==QEvent.Type.MouseButtonDblClick or (kind==QEvent.Type.KeyPress and event.key()==Qt.Key.Key_Escape):
            workspace.end_drag()
        return result


class DockWorkspace(QObject):
    VERSION = 4

    def __init__(self, window, model):
        super().__init__(window)
        self.window, self.model = window, model
        self.docks = {}
        self.scrolls = {}
        self._suspend = 0
        self._ready = False
        self._locked = False
        self._first_show = True
        self.drop_overlays={};self.panel_buttons={};self._automatic_rows=True
        self._drag_source=None;self._drag_target=None
        self._drag_native=False;self._drag_started=False
        self._drag_timer=QTimer(self);self._drag_timer.setInterval(16)
        self._drag_timer.timeout.connect(self._update_drag_target)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self.save)
        window.setDockOptions(QMainWindow.DockOption.AllowNestedDocks)
        self.refresh_animations()
        window.installEventFilter(self)
        model.changed.connect(self._model_changed)
        model.display_changed.connect(self.refresh_animations)

    def refresh_animations(self):
        enabled = self.model.settings.get('console_dock_animations', False)
        if self.window.isAnimated() != enabled:
            self.window.setAnimated(enabled)

    @contextmanager
    def paused(self):
        self._suspend += 1
        try:
            yield
        finally:
            self._suspend -= 1

    def add_panel(self, identity, title, widget, minimum_width=220):
        dock = OperatorDock(title,self.window,self,identity)
        dock.setObjectName('operator.' + identity)
        dock.setTitleBarWidget(PanelTitleBar(self,identity,dock))
        self.drop_overlays[identity]=PanelDropOverlay(self,identity,dock)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setToolTip(tr('拖动标题栏调整位置；双击标题栏切换独立窗口'))
        scroll = QScrollArea(dock)
        scroll.setWidgetResizable(True)
        scroll.setMinimumSize(330 if identity == 'yaku' else minimum_width, 40)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        if hasattr(widget, 'setTitle'):
            widget.setTitle('')
        if widget.layout():
            widget.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
            widget.layout().setContentsMargins(5, 5, 5, 5)
            widget.layout().setSpacing(5)
            if identity in ('quick', 'tiles'):
                widget.layout().setContentsMargins(5, 3, 5, 3)
            if identity == 'tiles':
                widget.layout().setAlignment(Qt.AlignmentFlag(0))
        widget.setProperty('panelBody', True)
        if identity == 'players':
            widget.setStyleSheet('QGroupBox[panelBody="true"]{padding:2px;}')
            widget.layout().setContentsMargins(3, 3, 3, 3)
        scroll.setWidget(widget)
        dock.setWidget(scroll)
        if identity == 'tiles':
            dock.setMinimumHeight(138)
        elif identity == 'quick':
            scroll.setMaximumHeight(widget.layout().itemAt(0).sizeHint().height() + 6)
        self.docks[identity], self.scrolls[identity] = dock, scroll
        self.window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        for signal in (dock.topLevelChanged, dock.dockLocationChanged,
                       dock.visibilityChanged):
            signal.connect(self.schedule_save)
        dock.installEventFilter(self)
        return dock

    def attach_menu(self, menu):
        self.menu = menu
        self.panel_toolbar=QToolBar('面板切换',self.window);self.panel_toolbar.setObjectName('operatorPanelSwitches')
        self.panel_toolbar.setMovable(False);self.panel_toolbar.setFloatable(False)
        self.window.addToolBar(Qt.ToolBarArea.TopToolBarArea,self.panel_toolbar)
        names={'players':'选手','round':'局况','dora':'宝牌','waits':'听牌','score':'计分','meld':'副露','yaku':'役种','tiles':'牌面','quick':'节目','commentary':'术语'}
        for key,dock in self.docks.items():
            control=QToolButton(self.panel_toolbar);control.setText(names.get(key,dock.windowTitle()));control.setCheckable(True)
            control.clicked.connect(lambda shown,target=key:self.show_panel(target) if shown else self.docks[target].hide())
            dock.visibilityChanged.connect(lambda shown,target=key:self._sync_panel_button(target))
            self.panel_toolbar.addWidget(control);self.panel_buttons[key]=control
        for dock in self.docks.values():
            menu.addAction(dock.toggleViewAction())
        menu.addSeparator()
        self.lock_action = menu.addAction(tr('锁定导播布局'))
        self.lock_action.setCheckable(True)
        self.lock_action.toggled.connect(self.set_locked)
        menu.addAction(tr('恢复默认导播布局'), self.reset_layout)
        menu.addAction(tr('找回屏幕外的窗口'), self.recover_windows)

    def initialize(self):
        self.reset_layout(save=False)
        self.restore()
        self._ready = True
        for key in self.panel_buttons:self._sync_panel_button(key)

    def reset_layout(self, checked=False, save=True):
        """Keep every live panel exposed; only optional commentary starts hidden."""
        del checked
        self.end_drag()
        with self.paused():
            self.window.navigation_handle.restore_width()
            self._automatic_rows=True
            for dock in self.docks.values():
                dock.setFloating(False)
                self.window.removeDockWidget(dock)
            left = Qt.DockWidgetArea.LeftDockWidgetArea
            top = Qt.DockWidgetArea.TopDockWidgetArea
            horizontal, vertical = Qt.Orientation.Horizontal, Qt.Orientation.Vertical
            d = self.docks
            for key in ('players', 'score', 'yaku'):
                self.window.addDockWidget(left, d[key])
            self.window.splitDockWidget(d['players'], d['score'], horizontal)
            self.window.splitDockWidget(d['score'], d['yaku'], horizontal)
            self.window.splitDockWidget(d['players'], d['round'], vertical)
            self.window.splitDockWidget(d['round'], d['dora'], vertical)
            self.window.splitDockWidget(d['dora'], d['meld'], vertical)
            self.window.splitDockWidget(d['score'], d['waits'], vertical)
            self.window.splitDockWidget(d['yaku'], d['tiles'], vertical)
            self.window.addDockWidget(top, d['quick'])
            self.window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, d['commentary'])
            for dock in d.values():
                dock.show()
            d['commentary'].hide()
            if hasattr(self.window, 'set_yakuman_expanded'):
                self.window.set_yakuman_expanded(False)
            available = max(780, self.window.width() - 170)
            palette_width = max(480, round(available * .39))
            left_width = round((available - palette_width) * .53)
            self.window.resizeDocks([d['players'], d['score'], d['yaku']],
                                    [left_width, available - palette_width - left_width, palette_width], horizontal)
            self._size_default_rows()
            self.set_locked(False, save=False)
        if save:
            self.save()

    def set_locked(self, locked, save=True):
        self._locked = bool(locked)
        if self._locked:self.end_drag()
        features = QDockWidget.DockWidgetFeature.DockWidgetClosable
        if not self._locked:
            features |= (QDockWidget.DockWidgetFeature.DockWidgetMovable |
                         QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        for dock in self.docks.values():
            dock.setFeatures(features)
            if isinstance(dock.titleBarWidget(),PanelTitleBar):dock.titleBarWidget().controls[0].setEnabled(not self._locked)
        if hasattr(self, 'lock_action') and self.lock_action.isChecked() != self._locked:
            self.lock_action.blockSignals(True)
            self.lock_action.setChecked(self._locked)
            self.lock_action.blockSignals(False)
        if save:
            self.schedule_save()

    def show_panel(self, identity):
        dock = self.docks[identity]
        dock.show()
        dock.raise_()
        if dock.isFloating():
            dock.activateWindow()
        return dock

    def _sync_panel_button(self,key):
        if not self._ready:return
        control=self.panel_buttons.get(key)
        if control:
            control.blockSignals(True);control.setChecked(not self.docks[key].isHidden());control.blockSignals(False)

    def begin_drag(self,source,native=False):
        if self._locked:return
        self.end_drag();self._drag_source=source;self._drag_native=native;self._drag_started=not native
        if native:self._save_timer.stop()
        self._drag_timer.start()

    def _update_drag_target(self,position=None,modifiers=None):
        if self._drag_source is None or not self._drag_started:return
        position=QCursor.pos() if position is None else position
        modifiers=QApplication.keyboardModifiers() if modifiers is None else modifiers
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            self._drag_target=None
            for overlay in self.drop_overlays.values():overlay.hide();overlay.edge=None
            return
        target=None
        if self._drag_target is not None:
            dock=self.docks[self._drag_target]
            if dock.isVisible() and not dock.isFloating() and self.drop_overlays[self._drag_target].geometry().contains(position):
                target=self._drag_target
        if target is None:
            # Activate a local split only when the pointer enters its centre.
            # Once revealed, keep its arrows reachable up to that panel's edge.
            for key,dock in self.docks.items():
                if key==self._drag_source or not dock.isVisible() or dock.isFloating():continue
                centre=QRect(round(dock.width()*.3),round(dock.height()*.3),round(dock.width()*.4),round(dock.height()*.4))
                if centre.contains(dock.mapFromGlobal(position)):target=key;break
        if target!=self._drag_target:
            self._drag_target=target
            for overlay in self.drop_overlays.values():overlay.hide();overlay.edge=None
            if target is not None:
                dock=self.docks[target];self.drop_overlays[target].setGeometry(QRect(dock.mapToGlobal(QPoint(0,0)),dock.size()))
        if target is not None:
            overlay=self.drop_overlays[target];overlay.edge=overlay.edge_at(overlay.mapFromGlobal(position))
            overlay.show();overlay.raise_();overlay.update()

    def arrow_destination(self,position):
        self._update_drag_target(position)
        if self._drag_target is None:return None
        overlay=self.drop_overlays[self._drag_target];edge=overlay.edge_at(overlay.mapFromGlobal(position))
        return (self._drag_source,self._drag_target,edge) if edge else None

    def finish_native_drag(self,destination):
        self.end_drag()
        if destination:
            def apply():
                try:
                    if self._ready and not self._locked:self.move_panel(*destination)
                finally:self.window.setUpdatesEnabled(True)
            # Qt has finished its release handler before we refine the landing.
            QTimer.singleShot(0,self.window,apply)

    def end_drag(self):
        native=self._drag_native
        self._drag_timer.stop();self._drag_source=self._drag_target=None
        self._drag_native=self._drag_started=False
        for overlay in self.drop_overlays.values():overlay.hide();overlay.edge=None
        if native:self.schedule_save()

    def move_panel(self,source,target,edge):
        if self._locked or source==target:return False
        if edge not in ('left','right','above','below'):raise ValueError('停靠位置无效')
        moving,anchor=self.docks[source],self.docks[target]
        if anchor.isFloating():return False
        orientation=Qt.Orientation.Horizontal if edge in ('left','right') else Qt.Orientation.Vertical
        self.window.setUpdatesEnabled(False)
        try:
            with self.paused():
                moving.setFloating(False);self.window.removeDockWidget(moving);anchor.show()
                self.window.splitDockWidget(anchor,moving,orientation)
                if edge in ('left','above'):self.window.splitDockWidget(moving,anchor,orientation)
                moving.show();self._automatic_rows=False
        finally:self.window.setUpdatesEnabled(True);self.end_drag()
        self.schedule_save();return True

    def _remove_tabs(self):
        for anchor in self.docks.values():
            peers=self.window.tabifiedDockWidgets(anchor)
            if not peers:continue
            area=self.window.dockWidgetArea(anchor)
            visible={dock:not dock.isHidden() for dock in peers}
            for dock in peers:self.window.removeDockWidget(dock)
            # A restored one-member tab container can retain Qt's internal
            # tabbed flag. Reinsert its anchor before creating real neighbours.
            self.window.removeDockWidget(anchor);self.window.addDockWidget(area,anchor)
            previous=anchor
            for dock in peers:
                self.window.splitDockWidget(previous,dock,Qt.Orientation.Vertical);dock.setVisible(visible[dock]);previous=dock

    def ensure_visible(self, widget):
        for identity, scroll in self.scrolls.items():
            if scroll.widget() is widget or scroll.widget().isAncestorOf(widget):
                self.show_panel(identity)
                scroll.ensureWidgetVisible(widget)
                return

    def schedule_save(self, *_):
        if self._ready and not self._suspend and not self._drag_native:
            self._save_timer.start()

    def layout_value(self):
        """Collect the current layout without publishing or writing settings."""
        encode = lambda value: bytes(value.toBase64()).decode('ascii')
        return {
            'version': self.VERSION,
            'state': encode(self.window.saveState(self.VERSION)),
            'geometry': encode(self.window.saveGeometry()),
            'locked': self._locked,
            'navigation_width': self.window.navigation_handle.preferred_width,
            'yakuman_expanded': bool(getattr(self.window, 'yakuman_toggle', None)
                                     and self.window.yakuman_toggle.isChecked()),
        }

    def save(self):
        if not self._ready or self._suspend:
            return True
        self._save_timer.stop()
        try:
            settings = dict(self.model.settings, console_layout=self.layout_value())
            self.model.save_preferences(settings)
            return True
        except (OSError, ValueError) as error:
            if hasattr(self.window, 'error'):
                self.window.error(str(error))
            return False

    def restore(self):
        self._save_timer.stop()
        data = self.model.settings.get('console_layout')
        with self.paused():
            if isinstance(data, dict) and data.get('version') in (1, 2, 3,self.VERSION):
                try:
                    def decode(key):
                        value = data.get(key, '')
                        if not isinstance(value, str) or len(value) > 262144:
                            raise ValueError('Invalid operator layout')
                        return QByteArray(base64.b64decode(value, validate=True))
                    geometry, state = decode('geometry'), decode('state')
                    if geometry:
                        self.window.restoreGeometry(geometry)
                    # Earlier layouts hid live controls behind tabs or reserved
                    # a mostly empty full-width row for the tile palette.
                    if data.get('version') in (3,self.VERSION) and state and not self.window.restoreState(state, data['version']):
                        self.reset_layout(save=False)
                    self._remove_tabs();self._automatic_rows=False
                    self.set_locked(data.get('locked', False), save=False)
                    self.window.navigation_handle.restore_width(data.get('navigation_width'))
                    if hasattr(self.window, 'set_yakuman_expanded'):
                        self.window.set_yakuman_expanded(data.get('yakuman_expanded', False)
                                                        if data.get('version') in (3,self.VERSION) else False)
                    self._migrating_legacy = data.get('version') not in (3,self.VERSION)
                except (ValueError, TypeError):
                    self.reset_layout(save=False)
            self.recover_windows(save=False)

    def recover_windows(self, checked=False, save=True):
        del checked
        screens = [screen.availableGeometry() for screen in QApplication.screens()]
        with self.paused():
            if not self.window.isMaximized():
                self.window.setGeometry(visible_geometry(self.window.geometry(), screens))
            for dock in self.docks.values():
                if dock.isFloating():
                    dock.setGeometry(visible_geometry(dock.geometry(), screens))
        if save:
            self.schedule_save()

    def _model_changed(self, before, after, kind):
        if kind in ('import', 'event_settings'):
            self._save_timer.stop()
            with self.paused():
                self.reset_layout(save=False)
                self.restore()

    def eventFilter(self, obj, event):
        if obj is self.window and event.type() == QEvent.Type.Resize and 'tiles' in self.docks:
            QTimer.singleShot(0, self._reclaim_unused_space)
        if obj is self.window and event.type() == QEvent.Type.Show and self._first_show:
            self._first_show = False
            QTimer.singleShot(0, self._settle_initial_sizes)
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize,
                            QEvent.Type.Show, QEvent.Type.Hide):
            self.schedule_save()
        return False

    def _reclaim_unused_space(self):
        # Qt can retain an old top-area allocation after the main window grows,
        # even though the compact quick strip itself has reached its maximum.
        # Repair that gap without touching detached or rearranged workspaces.
        if not self._ready or not self._automatic_rows or 'quick' not in self.docks:
            return
        keys = ('players', 'round', 'dora', 'meld', 'score', 'waits', 'yaku', 'tiles')
        if any(self.docks[key].isFloating() or not self.docks[key].isVisible() for key in keys):
            return
        quick = self.docks['quick']
        if quick.isFloating() or not quick.isVisible() or self.window.dockWidgetArea(quick) != Qt.DockWidgetArea.TopDockWidgetArea:
            return
        heads = [self.docks[key] for key in ('players', 'score', 'yaku')]
        if max(dock.y() for dock in heads) != min(dock.y() for dock in heads):
            return
        columns = (('players', 'round', 'dora', 'meld'), ('score', 'waits'), ('yaku', 'tiles'))
        if any(len({self.docks[key].x() for key in column}) != 1 for column in columns):
            return
        if heads[0].y() - quick.geometry().bottom() > 12 or self.window.height() != getattr(self, '_sized_window_height', None):
            with self.paused():
                self._size_default_rows()

    def _settle_initial_sizes(self):
        if not self._ready:
            return
        with self.paused():
            if not self.model.settings.get('console_layout') or getattr(self, '_migrating_legacy', False):
                self._size_default_rows()

    def _size_default_rows(self):
        # The tile palette now shares the right column with yaku, rather than
        # reserving a full-width blank strip below every other panel.
        header = self.window.menuWidget().height() if self.window.menuWidget() else 0
        self._sized_window_height = self.window.height()
        available = self.window.height() - header - self.window.statusBar().height()
        if hasattr(self,'panel_toolbar'):available-=self.panel_toolbar.sizeHint().height()
        top = max(64,self.docks['quick'].minimumSizeHint().height())
        middle = max(350, available - top - 10)
        tiles = 366 if self.window.height() >= 900 else min(240, round(middle * .40))
        player_content = self.scrolls['players'].widget().minimumSizeHint().height() + 40
        players = min(390, player_content, max(140, round(middle * .56)))
        round_height = min(158, max(75, round(middle * .18)))
        if self.window.height() >= 900:
            round_height = max(round_height, self.scrolls['round'].widget().minimumSizeHint().height() + 34)
        dora = 86 if middle >= 650 else 65
        meld = max(70, middle - players - round_height - dora - 15)
        score = min(520, max(210, round(middle * .64)))
        waits = middle - score - 5
        keys = ('quick', 'players', 'round', 'dora', 'score', 'meld', 'waits', 'yaku', 'tiles')
        heights = (top, players, round_height, dora, score, meld, waits, middle - tiles - 5, tiles)
        self.window.resizeDocks([self.docks[key] for key in keys], heights, Qt.Orientation.Vertical)

    def dispose(self):
        self.end_drag()
        self._save_timer.stop()
        self._ready = False
