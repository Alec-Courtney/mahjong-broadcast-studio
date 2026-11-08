"""Reference-measured colour drift and a shared score-to-canvas mapping."""
from __future__ import annotations

import math
from PySide6.QtGui import QColor, QLinearGradient


# The original recording has frozen sections. Fit only its moving frames:
# approximately nine seconds per colour cycle and 1.1 cycles per plot width.
FLOW_PERIOD_SECONDS = 9.
FLOW_CYCLES_ACROSS_PLOT = 1.10
FLOW_ROW_PHASE = .25
FLOW_INITIAL_PHASE = .3666
TOP_HEADROOM_FRACTION = .06


def chart_y(value, low, high, top, bottom):
    """Keep actual extrema/ticks while leaving space above the highest score."""
    height = max(0., bottom-top)
    if high <= low:
        return (top+bottom)/2
    return bottom-(value-low)/(high-low)*height*(1-TOP_HEADROOM_FRACTION)


def line_colour(accent, position, elapsed, index=0):
    """A broad, soft pale band moves right; geometry and opacity stay fixed.

    RGB sampling in the source shows all three channels rising together by
    roughly 60–90 levels, rather than the line becoming transparent or wider.
    """
    base = QColor(accent)
    if not base.isValid():
        base = QColor('#4da5b1')
    clock = round((float(elapsed)/FLOW_PERIOD_SECONDS) % 1., 10)
    phase = FLOW_CYCLES_ACROSS_PLOT*position-clock-index*FLOW_ROW_PHASE+FLOW_INITIAL_PHASE
    light = (.5+.5*math.cos(math.tau*phase))**2.5
    channels = [round(component*.74+72*light) for component in (base.red(), base.green(), base.blue())]
    return QColor(*(max(0, min(255, value)) for value in channels), base.alpha())


def line_gradient(accent, left, width, elapsed, index=0):
    """Project the moving colour field horizontally onto the thin polyline."""
    gradient = QLinearGradient(left, 0., left+max(1., width), 0.)
    # Dense stops preserve the smooth cosine profile without changing the path,
    # adding a glowing halo or creating a bright moving endpoint.
    gradient.setStops([(step/64, line_colour(accent, step/64, elapsed, index)) for step in range(65)])
    return gradient
