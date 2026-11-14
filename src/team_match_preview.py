"""One exported PNG and its exact, zoomable proof for selected half-games."""
from PySide6.QtCore import QEvent, Qt, QSignalBlocker
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QLabel, QLineEdit, QScrollArea, QVBoxLayout

from desk_theme import apply_theme
from ui_widgets import button, integer, row
import team_match_report as report


class TeamMatchReportDialog(QDialog):
    def __init__(self, history, match_ids):
        super().__init__(history)
        self.data = report.build_report(history.state, history.event_metadata, match_ids)
        self.image = None
        self.fit_mode = True
        self.setWindowTitle('队伍场次结果图 · 预览与导出')
        self.setMinimumSize(760, 520)
        screen = self.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry()
        self.resize(min(1240, area.width() - 60), min(850, area.height() - 70))
        layout = QVBoxLayout(self)
        self.title = QLineEdit(self.data['title'])
        self.title.setMaxLength(160)
        self.title.setProperty('i18n_literal', True)
        self.title.editingFinished.connect(self.update_image)
        layout.addLayout(row('图片标题', self.title))
        self.zoom = integer(10, 200, 10)
        self.zoom.setSuffix(' %')
        self.zoom.setMaximumWidth(150)
        self.zoom.setValue(100)
        self.zoom.valueChanged.connect(self.set_zoom)
        self.export_button = button('导出整张 PNG', self.export)
        self.export_button.setObjectName('primary')
        layout.addLayout(row('缩放', self.zoom, button('适应宽度', self.fit),
                             button('100%', self.actual_size), 1,
                             self.export_button, button('关闭', self.close)))
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.canvas = QLabel()
        self.canvas.setProperty('typographyPreview', True)
        self.scroll.setWidget(self.canvas)
        self.scroll.viewport().installEventFilter(self)
        layout.addWidget(self.scroll, 1)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setProperty('i18n_literal', True)
        layout.addWidget(self.status)
        workspace = history.parentWidget()
        self.export_directory = (workspace.store.directory(history.event_metadata['id']) / 'exports'
                                 if hasattr(workspace, 'store') else history.settings.get('last_export_dir', '.'))
        apply_theme(self, history.settings)
        self.update_image()

    def update_image(self):
        self.data['title'] = self.title.text().strip() or '队伍场次结果'
        self.image = report.render_report(self.data)
        if self.fit_mode:
            self.fit()
        else:
            self.render_scaled()

    def fit(self, *_):
        self.fit_mode = True
        if self.image is None:
            return
        value = round(max(1, self.scroll.viewport().width() - 16) / self.image.width() * 100)
        with QSignalBlocker(self.zoom):
            self.zoom.setValue(max(10, min(200, value)))
        self.render_scaled()

    def set_zoom(self, *_):
        self.fit_mode = False
        self.render_scaled()

    def actual_size(self):
        self.fit_mode = False
        self.zoom.setValue(100)
        self.render_scaled()

    def render_scaled(self):
        if self.image is None:
            return
        size = self.image.size() * (self.zoom.value() / 100)
        pixmap = QPixmap.fromImage(self.image).scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation)
        self.canvas.setPixmap(pixmap)
        self.canvas.resize(pixmap.size())

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport():
            if event.type() == QEvent.Type.Resize and self.fit_mode:
                self.fit()
            if event.type() == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.zoom.setValue(self.zoom.value() + (10 if event.angleDelta().y() > 0 else -10))
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def export(self):
        from pathlib import Path
        from event_workspace import safe_name

        # Render pending title edits too; file pixels always match this preview.
        try:
            self.update_image()
            directory = Path(self.export_directory)
            directory.mkdir(parents=True, exist_ok=True)
            path, _ = QFileDialog.getSaveFileName(self, '导出队伍场次结果图',
                str(directory / (safe_name(self.data['title']) + '.png')), 'PNG (*.png)')
            if not path:
                return
            if not Path(path).suffix:
                path += '.png'
            report.export_png(self.image, path)
            self.status.setText('已导出：' + path)
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
