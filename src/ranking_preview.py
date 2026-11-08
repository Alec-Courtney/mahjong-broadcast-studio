"""Large, read-only colour proof of the same paged report exported as PNG."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QVBoxLayout

import ranking_reports as reports
from i18n import tr
from settings_window import SettingsWindow
from ui_widgets import button, choice, integer, row, selected


PAGE_SIZE = reports.REPORT_PAGE_SIZE


class RankingPreviewWindow(SettingsWindow):
    mode_requested = Signal(str)
    page_requested = Signal(int)
    refresh_requested = Signal()
    export_requested = Signal()

    def __init__(self, workspace):
        super().__init__(workspace)
        self.model = workspace.model
        self.data = None
        self.image = None
        self.scaled_size = None
        self.fit_mode = None
        self._settings_model = self.model
        self.model.display_changed.connect(self.refresh_settings_theme)
        self.setWindowTitle('榜单 · 彩色预览')
        self.setMinimumSize(760, 540)
        screen = workspace.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry()
        self.resize(min(1460, available.width()-60), min(920, available.height()-70))

        layout = QVBoxLayout(self)
        self.mode = choice([('个人积分榜', 'individual')])
        self.mode.activated.connect(lambda _: self.mode_requested.emit(self.mode.currentData()))
        self.summary = QLabel()
        self.summary.setProperty('i18n_literal', True)
        layout.addLayout(row(self.mode, button('刷新榜单', self.refresh_requested.emit),
                             self.summary, 1, button('导出图片', self.export_requested.emit),
                             button('关闭', self.close)))

        self.rendered_page = 1
        self.previous = button('上一页', lambda: self.page_requested.emit(self.rendered_page-1))
        self.next = button('下一页', lambda: self.page_requested.emit(self.rendered_page+1))
        self.page_label = QLabel()
        self.page_label.setProperty('i18n_literal', True)
        self.zoom = integer(10, 300, 10)
        self.zoom.setSuffix(' %')
        self.zoom.setMaximumWidth(150)
        self.zoom.setValue(100)
        self.zoom.valueChanged.connect(self.change_zoom)
        layout.addLayout(row(self.previous, self.page_label, self.next, 1,
                             '缩放', self.zoom, button('适应宽度', lambda: self.fit('width')),
                             button('整页', lambda: self.fit('page')),
                             button('100%', lambda: self.set_zoom(100))))

        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidgetResizable(False)
        self.scroll.setStyleSheet('QScrollArea{background:#cbd5d0;border:1px solid #9eafa5}')
        self.canvas = QLabel()
        self.canvas.setProperty('typographyPreview', True)
        self.scroll.setWidget(self.canvas)
        self.scroll.viewport().installEventFilter(self)
        layout.addWidget(self.scroll, 1)
        hint = QLabel('配色、表头与导出图片一致。滚轮滚动查看，Ctrl＋滚轮缩放；宽榜单可横向滚动。')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.refresh_settings_theme()

    def set_report(self, data, page=1):
        pages = max(1, (len(data['rows'])+PAGE_SIZE-1)//PAGE_SIZE)
        page = min(pages, max(1, page))
        if self.data is data and self.rendered_page == page:
            return
        self.data = data
        self.rendered_page = page
        blocker = QSignalBlocker(self.mode)
        self.mode.clear()
        self.mode.addItem('个人积分榜', 'individual')
        if data.get('event', {}).get('kind') == 'team_league':
            self.mode.addItem('团队积分榜', 'team')
        selected(self.mode, data['mode'])
        del blocker
        self.page_label.setText(tr('第 {page} / {pages} 页').format(page=page, pages=pages))
        self.previous.setEnabled(page > 1)
        self.next.setEnabled(page < pages)
        unit = '队' if data['mode'] == 'team' else '人'
        self.summary.setText(f"共 {len(data['rows'])} {unit} · 每页 {PAGE_SIZE} 行")
        self.setWindowTitle(data['title']+' · 彩色预览')
        self.image = reports.render_report(data, (page-1)*PAGE_SIZE, PAGE_SIZE)
        self.scaled_size = None
        if self.fit_mode:
            self.fit(self.fit_mode)
        else:
            self.render_scaled()

    def change_zoom(self, *_):
        self.fit_mode = None
        self.render_scaled()

    def set_zoom(self, value):
        self.fit_mode = None
        if self.zoom.value() == value:
            self.render_scaled()
        else:
            self.zoom.setValue(value)

    def fit(self, mode):
        self.fit_mode = mode
        if self.image is None:
            return
        viewport = self.scroll.viewport()
        ratio = max(1, viewport.width()-16) / self.image.width()
        if mode == 'page':
            ratio = min(ratio, max(1, viewport.height()-16)/self.image.height())
        blocker = QSignalBlocker(self.zoom)
        self.zoom.setValue(max(self.zoom.minimum(), min(self.zoom.maximum(), round(ratio*100))))
        del blocker
        self.render_scaled()

    def render_scaled(self):
        if self.image is None:
            return
        size = self.image.size()* (self.zoom.value()/100)
        if size == self.scaled_size:
            return
        self.scaled_size = size
        pixmap = QPixmap.fromImage(self.image)
        if size != self.image.size():
            pixmap = pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.canvas.setPixmap(pixmap)
        self.canvas.resize(size)

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport():
            if event.type() == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                direction = 10 if event.angleDelta().y() > 0 else -10
                self.set_zoom(max(self.zoom.minimum(), min(self.zoom.maximum(), self.zoom.value()+direction)))
                event.accept()
                return True
            if event.type() == QEvent.Type.Resize and self.fit_mode:
                self.fit(self.fit_mode)
        return super().eventFilter(watched, event)
