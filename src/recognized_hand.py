"""Compact, editable recognition feedback beneath an existing player row.

This view owns no game state and imports neither the recognizer nor the model.
It emits a correction only when the operator explicitly confirms the editor.
"""
from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
import re
import sys

from PySide6.QtCore import QByteArray, QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QLineEdit, QSizePolicy, QToolButton, QWidget
from tile_order import sort_hand_tiles


_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
_ASSETS = _ROOT / 'resources' / 'tiles'
_TILE_FILES = {
    **{f'{number}{suit}': f'{prefix}{number}.svg' for suit, prefix in
       (('m', 'Man'), ('p', 'Pin'), ('s', 'Sou')) for number in range(1, 10)},
    **{f'{index + 1}z': f'{name}.svg' for index, name in
       enumerate(('Ton', 'Nan', 'Shaa', 'Pei', 'Haku', 'Hatsu', 'Chun'))},
    **{f'0{suit}': f'{prefix}5-Dora.svg' for suit, prefix in
       (('m', 'Man'), ('p', 'Pin'), ('s', 'Sou'))},
}


def parse_hand_text(value: str) -> list[str]:
    """Parse ordinary compact tile notation; preserve the operator's tile order."""
    value = re.sub(r'[\s,，]+', '', value.lower())
    if not value or not re.fullmatch(r'(?:[0-9]+[mps]|[1-7]+z)+', value):
        raise ValueError('请使用牌谱格式，例如 123m405p678s11z')
    tiles = [number + suit for digits, suit in re.findall(r'([0-9]+)([mpsz])', value)
             for number in digits]
    if len(tiles) > 14:
        raise ValueError('暗手牌最多 14 张，副露由系统读取')
    return tiles


def compact_hand(tiles) -> str:
    """Group adjacent suits without reordering a camera observation."""
    result = []
    numbers = ''
    previous = None
    for code in tiles:
        if code not in _TILE_FILES:
            if numbers:
                result.append(numbers + previous)
                numbers = ''
                previous = None
            result.append('?')
        elif code[1] == previous:
            numbers += code[0]
        else:
            if numbers:
                result.append(numbers + previous)
            numbers, previous = code[0], code[1]
    if numbers:
        result.append(numbers + previous)
    return ''.join(result)


class RecognizedHandWidget(QWidget):
    hand_edited = Signal(int, object)
    source_requested = Signal(int)
    edit_requested = Signal(int)
    edit_finished = Signal(int, bool)
    selection_changed = Signal(int)

    def __init__(self, player: int, parent=None):
        super().__init__(parent)
        self.player = player
        self.tiles = []
        self.source = ''
        self.confidence = None
        self.status = '尚无识别'
        self.stale = False
        self.pending = False
        self._renderers = {}
        self._editing = False
        self._edit_mode = None
        self._draft_tiles = []
        self.selected_index = None
        self._edit_error = ''
        self._deferred_observation = None
        self._base_tooltip = ''
        self.setObjectName('recognizedHand')
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(130)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        self.source_button = QToolButton(self)
        self.source_button.setObjectName('recognitionSource')
        self.source_button.setText('...')
        self.source_button.setAccessibleName('识别来源')
        self.source_button.setToolTip('选择识别画面或暂停识别')
        self.source_button.setAutoRaise(True)
        self.source_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.source_button.setStyleSheet('QToolButton{padding:0;border:0;font-size:16px;}')
        self.source_button.clicked.connect(lambda: self.source_requested.emit(self.player))

        self.editor = QLineEdit(self)
        self.editor.setObjectName('recognizedHandEditor')
        self.editor.setAccessibleName('修正识别手牌')
        self.editor.setPlaceholderText('输入牌谱，Enter 确认')
        self.editor.setStyleSheet('QLineEdit{min-height:0;padding:2px 3px;font-size:11px;}')
        self.editor.hide()
        self.editor.returnPressed.connect(self.commit_edit)
        self.editor.installEventFilter(self)
        self.edit_buttons = {}
        for key, label, action in (
            ('delete', '删牌', self.delete_selected_tile),
            ('append', '加牌', self.append_mode),
            ('text', '牌谱', self._toggle_edit_mode),
            ('apply', '采用', self.commit_tile_edit),
            ('cancel', '取消', self.cancel_edit),
        ):
            control = QToolButton(self)
            control.setText(label)
            control.setObjectName('handEditApply' if key == 'apply' else 'handEditAction')
            control.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            control.setCursor(Qt.CursorShape.PointingHandCursor)
            control.setStyleSheet('QToolButton{padding:1px 4px;min-height:20px;}')
            control.clicked.connect(action)
            control.hide()
            self.edit_buttons[key] = control
        self._update_tooltip()
        self._position_children()

    @property
    def editing(self):
        return self._editing

    @property
    def is_tile_editing(self):
        return self._editing and self._edit_mode == 'tiles'

    @property
    def draft_tiles(self):
        return list(self._draft_tiles)

    def _visible_tiles(self):
        return self._draft_tiles if self.is_tile_editing else self.tiles

    def set_observation(self, tiles, source='', confidence=None, status='', stale=False, pending=False):
        value = dict(tiles=sort_hand_tiles([code if code in _TILE_FILES else None for code in tiles]),
                     source=str(source or ''), confidence=confidence,
                     status=str(status or ('识别需核对' if tiles else '尚无识别')),
                     stale=bool(stale), pending=bool(pending))
        if self._editing:
            self._deferred_observation = value
            return
        self._apply_observation(value)

    def _apply_observation(self, value):
        self.tiles = list(value['tiles'])
        self.source = value['source']
        self.confidence = value['confidence']
        self.status = value['status']
        self.stale = value['stale']
        self.pending = value['pending']
        self._update_tooltip()
        self._position_children()
        self.updateGeometry()
        self.update()

    def clear(self):
        was_editing = self._editing
        self._editing = False
        self._edit_mode = None
        self._draft_tiles = []
        self.selected_index = None
        self._deferred_observation = None
        self.editor.hide()
        for control in self.edit_buttons.values():
            control.hide()
        self._apply_observation(dict(tiles=[], source='', confidence=None,
                                     status='尚无识别', stale=False, pending=False))
        if was_editing:
            self.edit_finished.emit(self.player, False)

    def _update_tooltip(self):
        lines = [self.status]
        if self.tiles:
            lines.append(compact_hand(self.tiles))
        if self.source:
            lines.append('来源：' + self.source)
        if isinstance(self.confidence, (int, float)) and not isinstance(self.confidence, bool):
            lines.append(f'最低可信度：{100 * self.confidence:.0f}%')
        if self.stale:
            lines.append('结果已过期，请重新识别')
        lines.append('点错牌，再从选牌区替换；可加牌、删牌。采用后重算草稿，Esc 取消。')
        self._base_tooltip = '\n'.join(lines)
        self.setToolTip(self._base_tooltip)

    def _layout_tiles(self):
        count = len(self._visible_tiles())
        if self.is_tile_editing and self.selected_index is None and count < 14:
            count += 1
        available = max(1, self.width() - 25)
        columns = max(1, min(count or 1, int(available // 17)))
        rows = max(1, math.ceil(count / columns)) if count else 0
        width = min(22., available / max(1, columns))
        height = width * 36 / 25
        return columns, rows, width, height

    def tile_rects(self):
        """Actual face bounds, also useful for accessibility and capture checks."""
        columns, _, width, height = self._layout_tiles()
        return [QRectF((index % columns) * width, (index // columns) * height,
                       width, height) for index in range(len(self._visible_tiles()))]

    def _edit_toolbar_metrics(self):
        columns = min(5, max(1, self.width() // 45))
        rows = math.ceil(5 / columns)
        return columns, rows, min(58, self.width() // columns)

    def sizeHint(self):
        _, rows, _, height = self._layout_tiles()
        content_height = max(22, math.ceil(rows * height) + (15 if rows else 0))
        if self._editing:
            content_height = max(content_height, 42) + self._edit_toolbar_metrics()[1] * 25 + 3
        return QSize(280, content_height)

    def minimumSizeHint(self):
        return QSize(130, self.sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_children()
        if event.oldSize().width() != event.size().width():
            self.updateGeometry()

    def _position_children(self):
        self.source_button.setGeometry(max(0, self.width() - 23), 0, 22, 22)
        _, rows, _, height = self._layout_tiles()
        body_height = max(27, math.ceil(rows * height))
        self.editor.setGeometry(0, 0, max(1, self.width() - 25), body_height)
        if hasattr(self, 'edit_buttons'):
            columns, _, width = self._edit_toolbar_metrics()
            top = max(42, body_height + 15) + 3
            for index, control in enumerate(self.edit_buttons.values()):
                control.setGeometry(index % columns * width, top + index // columns * 25, width - 3, 23)

    def _renderer(self, name):
        if name not in self._renderers:
            path = _ASSETS / name
            if name in ('Ton.svg', 'Nan.svg', 'Shaa.svg', 'Pei.svg'):
                data = path.read_bytes().replace(b'#142896', b'#141414').replace(b'#19328c', b'#141414')
                renderer = QSvgRenderer(QByteArray(data))
            else:
                renderer = QSvgRenderer(str(path))
            self._renderers[name] = renderer
        return self._renderers[name]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rects = self.tile_rects()
        if self.stale and not self._editing:
            painter.setOpacity(.45)
        for code, bounds in zip(self._visible_tiles(), rects):
            self._renderer('Front.svg').render(painter, bounds)
            if code:
                face = QRectF(bounds.x() + bounds.width() * .09, bounds.y() + bounds.height() / 12,
                              bounds.width() * .82, bounds.height() * 5 / 6)
                self._renderer(_TILE_FILES[code]).render(painter, face)
            else:
                painter.setPen(QColor('#785e47'))
                font = QFont(self.font())
                font.setPixelSize(max(11, round(bounds.height() * .6)))
                painter.setFont(font)
                painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, '?')
        painter.setOpacity(1.)
        colors = self.window().palette()
        if self.is_tile_editing:
            painter.setPen(QPen(colors.color(QPalette.ColorRole.Link), 2))
            if self.selected_index is not None and self.selected_index < len(rects):
                painter.drawRoundedRect(rects[self.selected_index].adjusted(1, 1, -1, -1), 2, 2)
            elif len(self._draft_tiles) < 14:
                columns, _, width, height = self._layout_tiles()
                index = len(self._draft_tiles)
                bounds = QRectF(index % columns * width, index // columns * height, width, height)
                painter.drawRoundedRect(bounds.adjusted(1, 1, -1, -1), 2, 2)
                painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, '+')
        _, rows, _, height = self._layout_tiles()
        label_top = math.ceil(rows * height) if rows else 2
        if self._edit_mode == 'text':
            label_top = max(27, label_top)
        label_rect = QRectF(0, label_top, max(0, self.width() - 25), 15)
        if self._editing:
            text = self._edit_error or ('输入牌谱后采用' if self._edit_mode == 'text' else
                ('已有 14 张 · 点牌可替换或删除' if len(self._draft_tiles) >= 14 else '从选牌区加一张牌') if self.selected_index is None else
                f'第 {self.selected_index + 1} 张 · 从选牌区替换')
        else:
            text = '已过期 · ' + self.status if self.stale else self.status
            if self.source in ('手动修正', '手动核对') and not text.startswith('手动'):
                text = '手动已锁定 · ' + text
        role = (QPalette.ColorRole.Link if self._editing else
                QPalette.ColorRole.PlaceholderText if self.stale else
                QPalette.ColorRole.Link if self.pending else QPalette.ColorRole.WindowText)
        painter.setPen(colors.color(role))
        font = QFont(self.font())
        font.setPixelSize(10)
        painter.setFont(font)
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, round(label_rect.width())))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            index = next((i for i, rect in enumerate(self.tile_rects()) if rect.contains(event.position())), None)
            if index is None and self.is_tile_editing and self.selected_index is None:
                event.accept()
                return
            self.begin_tile_edit(index)
            if index is None and not self._visible_tiles():
                self.append_mode()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.mousePressEvent(event)

    def begin_tile_edit(self, index=None):
        first = not self._editing
        if first:
            self._draft_tiles = list(self.tiles)
            self._deferred_observation = None
        elif self._edit_mode == 'text':
            try:
                self._draft_tiles = parse_hand_text(self.editor.text())
            except ValueError:
                # Preserve the visual draft if optional text entry is unfinished.
                pass
        self._editing = True
        self._edit_mode = 'tiles'
        self._edit_error = ''
        self.selected_index = max(0, min(int(index or 0), len(self._draft_tiles) - 1)) if self._draft_tiles else None
        self.editor.hide()
        self._refresh_edit_controls()
        if first:
            self.edit_requested.emit(self.player)
        self.selection_changed.emit(self.player)
        self.setFocus()

    def replace_selected_tile(self, tile):
        if not self.is_tile_editing or tile not in _TILE_FILES:
            return False
        if self.selected_index is None:
            if len(self._draft_tiles) >= 14:
                return False
            self._draft_tiles.append(tile)
            # Keep append mode for quickly entering several missing tiles.
        else:
            self._draft_tiles[self.selected_index] = tile
        self._edit_error = ''
        self._refresh_edit_controls()
        self.selection_changed.emit(self.player)
        return True

    def delete_selected_tile(self):
        if not self.is_tile_editing or self.selected_index is None:
            return False
        self._draft_tiles.pop(self.selected_index)
        self.selected_index = min(self.selected_index, len(self._draft_tiles) - 1) if self._draft_tiles else None
        self._edit_error = ''
        self._refresh_edit_controls()
        self.selection_changed.emit(self.player)
        return True

    def append_mode(self):
        if not self._editing:
            self.begin_tile_edit()
        if self._edit_mode == 'text':
            self.begin_tile_edit()
        if len(self._draft_tiles) >= 14:
            self._edit_error = '已有 14 张；可先删除多识别的牌'
            self.update()
            return False
        self.selected_index = None
        self._edit_error = ''
        self._refresh_edit_controls()
        self.selection_changed.emit(self.player)
        return True

    @staticmethod
    def _validate_draft(tiles):
        if not 1 <= len(tiles) <= 14:
            raise ValueError('暗手牌须为 1–14 张，副露由系统读取')
        if any(tile not in _TILE_FILES for tile in tiles):
            raise ValueError('请先替换带问号的牌')
        counts = Counter('5' + tile[1] if tile[0] == '0' else tile for tile in tiles)
        if max(counts.values()) > 4:
            raise ValueError('同一种牌最多四张，红五也计入五')

    def _refresh_edit_controls(self):
        for control in self.edit_buttons.values():
            control.setVisible(self._editing)
        visual = self.is_tile_editing
        self.edit_buttons['delete'].setEnabled(visual and self.selected_index is not None)
        self.edit_buttons['append'].setEnabled(visual and len(self._draft_tiles) < 14)
        self.edit_buttons['text'].setText('选牌' if self._edit_mode == 'text' else '牌谱')
        self._position_children()
        self.updateGeometry()
        self.update()

    def commit_tile_edit(self):
        if self._edit_mode == 'text':
            return self.commit_edit()
        if not self.is_tile_editing:
            return False
        try:
            self._validate_draft(self._draft_tiles)
        except ValueError as error:
            self._edit_error = str(error)
            self.update()
            return False
        self._adopt_correction(self._draft_tiles)
        return True

    def begin_edit(self):
        if self._edit_mode == 'text':
            self.editor.setFocus()
            self.editor.selectAll()
            return
        first = not self._editing
        if first:
            self._draft_tiles = list(self.tiles)
            self._deferred_observation = None
        self._editing = True
        self._edit_mode = 'text'
        self._edit_error = ''
        self.editor.setText(compact_hand(self._draft_tiles))
        self.editor.setToolTip('Enter 确认修正，Esc 取消')
        self.editor.setStyleSheet('QLineEdit{min-height:0;padding:2px 3px;font-size:11px;}')
        self.editor.show()
        self._refresh_edit_controls()
        if first:
            self.edit_requested.emit(self.player)
        self.editor.setFocus()
        self.editor.selectAll()

    def _toggle_edit_mode(self):
        if self._edit_mode == 'text':
            self.begin_tile_edit(self.selected_index)
        else:
            self.begin_edit()

    def commit_edit(self):
        if not self._editing or self._edit_mode != 'text':
            return
        try:
            tiles = parse_hand_text(self.editor.text())
            self._validate_draft(tiles)
        except ValueError as error:
            self.editor.setToolTip(str(error))
            self.editor.setStyleSheet('QLineEdit{min-height:0;padding:2px 3px;font-size:11px;border-color:#d4716c;}')
            self.editor.setFocus()
            return
        self._adopt_correction(tiles)

    def _adopt_correction(self, tiles):
        tiles = sort_hand_tiles(tiles)
        self._editing = False
        self._edit_mode = None
        self.selected_index = None
        self._deferred_observation = None
        self.editor.hide()
        self._refresh_edit_controls()
        self._apply_observation(dict(tiles=tiles, source='手动修正', confidence=None,
            status='手动修正 · 已锁定', stale=False, pending=True))
        self.hand_edited.emit(self.player, list(tiles))
        self.edit_finished.emit(self.player, True)

    def cancel_edit(self):
        if not self._editing:
            return
        self._editing = False
        self._edit_mode = None
        self.selected_index = None
        self.editor.hide()
        self._refresh_edit_controls()
        if self._deferred_observation:
            value = self._deferred_observation
            self._deferred_observation = None
            self._apply_observation(value)
        self.edit_finished.emit(self.player, False)

    def keyPressEvent(self, event):
        if self._editing:
            if event.key() == Qt.Key.Key_Escape:
                self.cancel_edit()
            elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.commit_tile_edit()
            elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.is_tile_editing:
                self.delete_selected_tile()
            else:
                super().keyPressEvent(event)
                return
            event.accept()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.editor and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.cancel_edit()
            return True
        return super().eventFilter(watched, event)
