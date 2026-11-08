"""Small shared Qt controls, independent of either desk or event records."""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QHeaderView,
    QLabel, QListWidget, QPushButton, QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QWidget,
)


def quantity_control(widget):
    """Keep native editing with large minus/plus targets on the right."""
    widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return widget


def button(label, callback):
    widget = QPushButton(label)
    widget.clicked.connect(callback)
    return widget


def row(*widgets):
    layout = QHBoxLayout()
    for widget in widgets:
        if isinstance(widget, str): layout.addWidget(QLabel(widget))
        elif isinstance(widget, int): layout.addStretch(widget)
        else: layout.addWidget(widget)
    return layout


def choice(items):
    widget = QComboBox()
    for label, value in items: widget.addItem(label, value)
    return widget


def table(headers, editable=False):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.verticalHeader().hide()
    widget.setAlternatingRowColors(True)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    widget.horizontalHeader().setStretchLastSection(True)
    if not editable:
        widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    return widget


def item(value, identity=None, editable=True):
    result = QTableWidgetItem(str(value))
    if identity is not None:
        result.setData(Qt.ItemDataRole.UserRole, identity)
    if not editable:
        result.setFlags(result.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return result


def integer(low=0, high=1000000, step=1):
    widget = quantity_control(QSpinBox())
    widget.setRange(low, high)
    widget.setSingleStep(step)
    widget.setKeyboardTracking(False)
    return widget


def decimal(low=-10000000, high=10000000, digits=1):
    widget = quantity_control(QDoubleSpinBox())
    widget.setDecimals(digits)
    widget.setRange(low, high)
    widget.setKeyboardTracking(False)
    return widget


class WindowScaleSpinBox(QDoubleSpinBox):
    """Edit a window's scale while keeping its stored geometry in pixels."""
    def __init__(self, pixels, base_width, low_width=120, high_width=4000):
        super().__init__()
        quantity_control(self)
        self.low_width, self.high_width = low_width, high_width
        self.setDecimals(2)
        self.setSingleStep(1)
        self.setSuffix(' %')
        self.setKeyboardTracking(False)
        self.setToolTip('100% 为窗口原始设计尺寸，宽高按相同比例调整')
        self.set_pixel_width(pixels, base_width)

    def set_pixel_width(self, pixels, base_width=None):
        if base_width is not None:self.base_width = base_width
        self._minimum_pixels = min(self.low_width, pixels)
        self._maximum_pixels = max(self.high_width, pixels)
        self.setRange(self._minimum_pixels * 100 / self.base_width, self._maximum_pixels * 100 / self.base_width)
        self.setValue(pixels * 100 / self.base_width)
        self._reference_pixels = pixels
        self._reference_percent = self.value()

    def pixel_width(self):
        if self.value() == self._reference_percent:return self._reference_pixels
        return max(self._minimum_pixels, min(self._maximum_pixels, round(self.base_width * self.value() / 100)))


def selected(widget, value):
    widget.setCurrentIndex(max(0, widget.findData(value)))


def note(text):
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setObjectName('muted')
    return widget


class SidebarPages(QWidget):
    """Left navigation with the small tab API used by existing integrations."""
    currentChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.nav = QListWidget()
        self.nav.setFixedWidth(162)
        self.stack = QStackedWidget()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.nav)
        layout.addWidget(self.stack, 1)
        self.nav.currentRowChanged.connect(self._select)

    def _select(self, index):
        if index < 0: return
        self.stack.setCurrentIndex(index)
        self.currentChanged.emit(index)

    def addTab(self, widget, title):
        index = self.stack.addWidget(widget)
        self.nav.addItem(title)
        if self.nav.currentRow() < 0: self.nav.setCurrentRow(0)
        return index

    def currentIndex(self): return self.stack.currentIndex()
    def setCurrentIndex(self, index): self.nav.setCurrentRow(index)
    def count(self): return self.stack.count()
    def widget(self, index): return self.stack.widget(index)
    def tabText(self, index): return self.nav.item(index).text()
    def setTabText(self, index, value): self.nav.item(index).setText(value)
