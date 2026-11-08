"""Visible-ink placement for single-line broadcast and report captions."""
from __future__ import annotations

from dataclasses import dataclass
import re
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QFontMetricsF, QPainterPath


@dataclass
class TextPlacement:
    value: str
    font: QFont
    baseline: QPointF
    ink_bounds: QRectF
    body_bounds: QRectF
    box: QRectF


def _glyph_bounds(value, font):
    path = QPainterPath();path.addText(0, 0, font, value)
    return path.boundingRect()


def _numeric(value):
    return bool(re.fullmatch(r'[\s()（）+−\-\d,.%％]+', value) and any(char.isdigit() for char in value))


def single_line_layout(value, box, font, align=Qt.AlignmentFlag.AlignLeft, minimum=8, elide=True):
    """Center ink, using the digit body rather than comma descenders for numbers.

    Font fitting uses visible glyphs. A consistent punctuation envelope prevents
    a comma alone from making one numeric cell smaller than its neighbours.
    """
    value = str(value);box = QRectF(box);font = QFont(font);numeric = _numeric(value)
    minimum = max(1, min(int(minimum), max(1, font.pixelSize())))
    while True:
        ink = _glyph_bounds(value, font)
        digits = _glyph_bounds('0123456789', font)
        body = QRectF(ink.left(), digits.top(), ink.width(), digits.height()) if numeric else QRectF(ink)
        safe = ink.united(_glyph_bounds('0123456789,.−+', font)) if numeric else ink
        required_height = 2*max(body.center().y()-safe.top(), safe.bottom()-body.center().y())
        if (ink.width() <= box.width() and required_height <= box.height()) or font.pixelSize() <= minimum:
            break
        font.setPixelSize(font.pixelSize()-1)
    if elide and ink.width() > box.width():
        value = QFontMetricsF(font).elidedText(value, Qt.TextElideMode.ElideRight, max(0, box.width()))
        ink = _glyph_bounds(value, font);numeric = _numeric(value)
        digits = _glyph_bounds('0123456789', font)
        body = QRectF(ink.left(), digits.top(), ink.width(), digits.height()) if numeric else QRectF(ink)
    if align & Qt.AlignmentFlag.AlignHCenter:
        x = box.center().x()-ink.center().x()
    elif align & Qt.AlignmentFlag.AlignRight:
        x = box.right()-ink.right()
    else:
        x = box.left()-ink.left()
    baseline = QPointF(x, box.center().y()-body.center().y())
    ink.translate(baseline);body.translate(baseline)
    return TextPlacement(value, font, baseline, ink, body, box)


def draw_single_line(painter, value, box, font, align=Qt.AlignmentFlag.AlignLeft, minimum=8, elide=True):
    placement = single_line_layout(value, box, font, align, minimum, elide)
    painter.save();painter.setClipRect(placement.box);painter.setFont(placement.font)
    painter.drawText(placement.baseline, placement.value)
    painter.restore()
    return placement
