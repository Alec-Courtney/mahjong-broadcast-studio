"""Vector recreation of the supplied 30 fps LIVE sting."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainterPath, QPen, QTransform


LIVE_RECT = QRectF(350, 240, 168, 54)
WHITE_WIPE = 29 / 30
WHITE_HOLD = 9 / 30
RED_WIPE = 24 / 30
FLASH = 6 / 30
STING_SECONDS = WHITE_WIPE + WHITE_HOLD + RED_WIPE + FLASH
RED = '#ef0000'
WHITE = '#ffffff'

# Leading-edge positions measured at y=382/482 in the supplied 1280x576
# reference. Each knot is one source frame: white frames 58–87, red 96–120.
# The clip repeats a few frames; retaining those plateaus avoids inventing
# motion. Interpolation provides intermediate positions on faster displays.
WHITE_EDGE = (0., .0415, .0965, .1864, .4113, .4113, .5062, .5787,
              .6462, .6937, .7361, .7711, .8036, .8311, .8511, .8686,
              .8911, .9060, .9210, .9310, .9435, .9510, .9610, .9660,
              .9810, .9810, .9885, .9885, .9940, 1.)
RED_EDGE = (0., .0490, .0490, .0740, .1064, .1514, .2139, .3263,
            .4713, .5887, .6687, .7311, .7786, .8161, .8436, .8636,
            .8861, .9060, .9210, .9360, .9460, .9585, .9685, .9735, 1.)
WHITE_LETTER_SHIFT = tuple(x/344 for x in (0,0,0,0,0,0,1,3,6,10))
RED_LETTER_SHIFT = tuple(x/344 for x in
    (10,22,22,31,44,63,90,133,192,238,272,296,314,329,341,351,
     360,367,373,379,384,388,392,395,410))


def _track(samples, progress):
    at = max(0., min(1., progress))*(len(samples)-1)
    left = min(len(samples)-2, int(at));fraction = at-left
    return samples[left]+(samples[left+1]-samples[left])*fraction


def wipe_progress(phase, progress):
    return _track(WHITE_EDGE if phase=='white_wipe' else RED_EDGE,progress)


def live_rect(config=None):
    config = config or {}
    return QRectF(config.get('corner_live_x',350), config.get('corner_live_y',240),
                  config.get('corner_live_width',168), config.get('corner_live_height',54))


@dataclass(frozen=True)
class LiveFrame:
    phase: str
    progress: float = 0.
    moving: bool = False
    delay: float = 0.


def live_frame(elapsed, hold=7.):
    """The seven-second rest begins after the final red frame, in real time."""
    phase = max(0., elapsed) % (hold + STING_SECONDS)
    if phase < hold:
        return LiveFrame('red', delay=hold-phase)
    phase -= hold
    if phase < WHITE_WIPE:
        return LiveFrame('white_wipe', phase/WHITE_WIPE, True)
    phase -= WHITE_WIPE
    if phase < WHITE_HOLD:
        # The old lettering begins drifting before the red edge enters.
        still = 5/30
        return LiveFrame('white',phase/WHITE_HOLD,phase>=still,max(0.,still-phase))
    phase -= WHITE_HOLD
    if phase < RED_WIPE:
        return LiveFrame('red_wipe', phase/RED_WIPE, True)
    return LiveFrame('flash', (phase-RED_WIPE)/FLASH, True)


@lru_cache(maxsize=1)
def _lettering():
    font = QFont('Arial');font.setPixelSize(120);font.setBold(True)
    path = QPainterPath();path.addText(0, 0, font, 'LIVE')
    ink = path.boundingRect()
    transform = QTransform()
    # Leave a little extra room below the capitals so their antialiasing never
    # meets the lower edge, including short, operator-resized badges.
    transform.translate(.15, .10)
    transform.scale(.70/ink.width(), .76/ink.height())
    transform.translate(-ink.x(), -ink.y())
    return transform.map(path)


def _label(painter, background, foreground, offset=0., letters=True):
    painter.fillRect(QRectF(0, 0, 1, 1), QColor(background))
    if letters:
        painter.save();painter.translate(offset, 0)
        painter.fillPath(_lettering(), QColor(foreground));painter.restore()


def _wipe(painter, progress, area):
    slant = .52*area.height()/area.width()
    edge = progress*(1+slant)
    path = QPainterPath();path.moveTo(0, 0);path.lineTo(edge, 0)
    path.lineTo(edge-slant, 1);path.lineTo(0, 1);path.closeSubpath()
    painter.setClipPath(path, Qt.ClipOperation.IntersectClip)


def _slide_red_panel(painter, progress, area):
    """Translate one rigid red plate, including its letters and diagonal end."""
    slant = .52*area.height()/area.width()
    offset = (progress-1)*(1+slant)
    plate = QPainterPath();plate.moveTo(0,0);plate.lineTo(1+slant,0)
    plate.lineTo(1,1);plate.lineTo(-slant,1);plate.closeSubpath()
    painter.save()
    painter.translate(offset,0)
    painter.fillPath(plate,QColor(RED))
    painter.fillPath(_lettering(),QColor(WHITE))
    painter.restore()


def draw_live_badge(painter, frame=None, area=None):
    frame = frame or LiveFrame('red')
    area = area if area is not None else LIVE_RECT
    painter.save()
    try:
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.fillRect(area.adjusted(-1, 1, 1, 2), QColor('#65000000'))
        painter.setPen(QPen(QColor('#65de0000'), .8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(area.adjusted(-1, -1.6, 1, 1))
        painter.translate(area.x(), area.y())
        painter.scale(area.width(), area.height())
        painter.setClipRect(QRectF(0, 0, 1, 1), Qt.ClipOperation.IntersectClip)
        if frame.phase == 'white':
            _label(painter, WHITE, RED, _track(WHITE_LETTER_SHIFT,frame.progress))
        elif frame.phase == 'white_wipe':
            _label(painter, RED, WHITE)
            painter.save();_wipe(painter, wipe_progress(frame.phase,frame.progress), area)
            _label(painter, WHITE, RED);painter.restore()
        elif frame.phase == 'red_wipe':
            # The outgoing white plate and incoming red plate travel right.
            # The incoming letters share the red plate's transform, so its
            # diagonal never wipes through them or changes the padding by E.
            offset = _track(RED_LETTER_SHIFT,frame.progress)
            _label(painter, WHITE, RED, offset)
            _slide_red_panel(painter,wipe_progress(frame.phase,frame.progress),area)
        elif frame.phase == 'flash':
            _label(painter, RED, WHITE, letters=int(frame.progress*6) not in (0, 1, 4, 5))
        else:
            _label(painter, RED, WHITE)
    finally:
        painter.restore()
