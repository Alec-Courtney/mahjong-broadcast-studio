"""ML-style orbiting portrait colors with a real stationary interval.

The red/yellow riichi palette follows broadcast references. The three-second
orbit and five-second hold are adjustable local timing, not official timings.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QConicalGradient, QPen


ORBIT_SECONDS = 3.0
HOLD_SECONDS = 5.0
FRAME_WIDTH = 4.0

PALETTES = {
    'riichi': ('#e62332', '#fa5621', '#ffe96a', '#ff9d21', '#e62332'),
}


def status_mode(player):
    # The supplied broadcast reference keeps tenpai-only portraits unframed.
    return 'riichi' if player.get('riichi') else None


def frame_phase(started, now, speed=1.):
    """Return clockwise degrees, whether moving, and seconds to the next orbit."""
    speed = max(.1, speed)
    phase = max(0., (now-started)*speed) % (ORBIT_SECONDS+HOLD_SECONDS)
    if phase >= ORBIT_SECONDS:
        return 0., False, (ORBIT_SECONDS+HOLD_SECONDS-phase)/speed
    t = phase/ORBIT_SECONDS
    # A complete orbit returns to the same phase before stopping. Ease both
    # ends so rotation neither snaps to a new color nor starts at full speed.
    t = t*t*t*(t*(t*6-15)+10)
    return -360*t, True, 0.


def paint_status_frame(painter, area, mode, started, now, speed=1.):
    angle, _, _ = frame_phase(started, now, speed)
    # At rest the red/darker end is above the portrait and the yellow/light
    # end below it, as in the supplied H03 broadcast frame.
    gradient = QConicalGradient(area.center(), 90+angle)
    for stop, value in zip((0., .25, .5, .72, 1.), PALETTES[mode]):
        gradient.setColorAt(stop, QColor(value))
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QBrush(gradient), FRAME_WIDTH))
    painter.drawRoundedRect(area.adjusted(1, 1, -1, -1), 14, 14)
    painter.restore()
