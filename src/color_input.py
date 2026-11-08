"""Color swatch picker with an editable hexadecimal value, shared by forms."""
import re

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap, QIcon
from PySide6.QtWidgets import (
    QColorDialog, QHBoxLayout, QLineEdit, QPushButton, QStyledItemDelegate,
    QStyleOptionViewItem, QWidget,
)
from i18n import tr


def choose_color(current, parent, alpha=True):
    options = QColorDialog.ColorDialogOption.DontUseNativeDialog
    if alpha:
        options |= QColorDialog.ColorDialogOption.ShowAlphaChannel
    return QColorDialog.getColor(current, parent, tr('选择颜色'), options)


class ColorInput(QWidget):
    changed = Signal()

    def __init__(self, value='#FFFFFFFF', parent=None, alpha=True):
        super().__init__(parent)
        self.alpha = alpha
        self.color = QColor(value)
        self.edit = QLineEdit()
        self.edit.setProperty('i18nLiteral', True)
        self.edit.setMaxLength(9 if alpha else 7)
        self.edit.setMinimumWidth(75)
        self.edit.setPlaceholderText('#AARRGGBB' if alpha else '#RRGGBB')
        self.swatch = QPushButton()
        self.swatch.setFixedWidth(30)
        self.swatch.setToolTip(tr('打开调色面板'))
        self.swatch.setAccessibleName(tr('打开调色面板'))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.swatch)
        self.edit.textEdited.connect(self._edited)
        self.swatch.clicked.connect(self.choose)
        self.set_color(value)

    def _valid(self):
        value = self.edit.text().strip()
        pattern = r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?' if self.alpha else r'#[0-9a-fA-F]{6}'
        return bool(re.fullmatch(pattern, value)) and QColor(value).isValid()

    def _paint_swatch(self):
        image = QPixmap(22, 18)
        painter = QPainter(image)
        for y in range(0, 18, 5):
            for x in range(0, 22, 5):
                painter.fillRect(x, y, 5, 5, QColor('#dddddd' if (x+y)//5 % 2 else '#ffffff'))
        painter.fillRect(image.rect(), self.color)
        painter.end()
        icon = QIcon(image)
        icon.addPixmap(image, QIcon.Mode.Disabled)
        self.swatch.setIcon(icon)
        self.swatch.setIconSize(QSize(22, 18))

    def _edited(self, *_):
        valid = self._valid()
        self.edit.setToolTip('' if valid else tr('请输入有效色号：#RRGGBB 或 #AARRGGBB'))
        if valid:
            self.color = QColor(self.edit.text().strip())
            self._paint_swatch()
            self.changed.emit()

    def set_color(self, value):
        color = QColor(value)
        if not color.isValid():
            raise ValueError(tr('请输入有效色号：#RRGGBB 或 #AARRGGBB'))
        self.color = color
        self.edit.setText(color.name(QColor.NameFormat.HexArgb if self.alpha else QColor.NameFormat.HexRgb).upper())
        self.edit.setToolTip('')
        self._paint_swatch()

    def choose(self):
        selected = choose_color(self.color, self, self.alpha)
        if selected.isValid():
            self.set_color(selected)
            self.changed.emit()

    def value(self):
        if not self._valid():
            raise ValueError(tr('请输入有效色号：#RRGGBB 或 #AARRGGBB'))
        return QColor(self.edit.text().strip()).name(QColor.NameFormat.HexArgb if self.alpha else QColor.NameFormat.HexRgb)

    def text(self):
        return self.value()

    def setText(self, value):
        self.set_color(value)


class ColorDelegate(QStyledItemDelegate):
    """Roster cells keep normal text editing and expose a clickable color chip."""
    @staticmethod
    def chip(rect):
        return QRect(rect.right()-25, rect.center().y()-8, 19, 16)

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width() + 27, size.height())

    def paint(self, painter, option, index):
        text_option = QStyleOptionViewItem(option)
        text_option.rect.adjust(0, 0, -27, 0)
        super().paint(painter, text_option, index)
        painter.save()
        painter.setPen(QColor('#89949c'))
        color = QColor(str(index.data() or ''))
        painter.setBrush(color if color.isValid() else QColor('#ffffff'))
        painter.drawRect(self.chip(option.rect))
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if (event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton
                and self.chip(option.rect).contains(event.position().toPoint())):
            selected = choose_color(QColor(str(index.data() or '#377e72')), option.widget, False)
            if selected.isValid():
                model.setData(index, selected.name(), Qt.ItemDataRole.EditRole)
            return True
        return super().editorEvent(event, model, option, index)
