"""Use the broadcast meld painter for both saved groups and editable drafts."""
import copy
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget
from artwork import meld, meld_tiles, meld_width


class MeldPreviewWidget(QWidget):
    tile_clicked = Signal(int)
    tile_removed = Signal(int)

    def __init__(self, group=None, scale=1.25, parent=None):
        super().__init__(parent)
        self.group = None
        self.scale = scale
        self.indices = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.set_group(group, scale)

    def set_group(self, group, scale, indices=None):
        self.group = copy.deepcopy(group)
        self.scale = scale
        self.indices = indices
        self.setVisible(bool(group and group.get('tiles')))
        self.setFixedHeight(round((50 if group and group['kind'] == 'kakan' else 36)*scale)+8)
        self.setCursor(Qt.CursorShape.PointingHandCursor if indices is not None else Qt.CursorShape.ArrowCursor)
        if indices is not None:self.setToolTip('点击设为鸣入牌；右键移除')
        self.updateGeometry(); self.update()

    def sizeHint(self):
        return QSize(round(meld_width(self.group)*self.scale)+8 if self.group else 1, self.height())

    def minimumSizeHint(self):
        return QSize(1, self.height())

    def geometry_values(self):
        scale = min(self.scale, max(1, self.width()-8)/max(1, meld_width(self.group)))
        return scale, 4., 4.+(14*scale if self.group['kind'] == 'kakan' else 0.)

    def tile_rects(self):
        if not self.group:return []
        scale, x, y = self.geometry_values()
        indices=list(self.indices) if self.indices is not None else list(range(len(self.group['tiles'])))
        if self.group['kind']=='ankan':
            codes=list(self.group['tiles'])
            for index in (0,3):
                if index<len(codes) and len(codes)>1 and codes[index][0]=='0':
                    codes[1],codes[index]=codes[index],codes[1];indices[1],indices[index]=indices[index],indices[1]
        return [(indices[index],
                 QRectF(x+dx*scale, y+dy*scale, (36 if side else 25)*scale, (25 if side else 36)*scale))
                for index, _, dx, dy, side, _ in meld_tiles(self.group)]

    def paintEvent(self, event):
        if not self.group:return
        scale, x, y = self.geometry_values()
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        meld(painter, self.group, x, y, scale); painter.end()

    def mousePressEvent(self, event):
        if self.indices is not None:
            for index, area in reversed(self.tile_rects()):
                if area.contains(event.position()):
                    if event.button() == Qt.MouseButton.LeftButton:self.tile_clicked.emit(index)
                    elif event.button() == Qt.MouseButton.RightButton:self.tile_removed.emit(index)
                    else:break
                    event.accept(); return
        super().mousePressEvent(event)
