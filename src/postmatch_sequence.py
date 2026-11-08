"""Measured ML post-match choreography on one monotonic, seekable clock.

The two supplied clips provide the entry and statistics transition separately.
Only their movement is combined; match data belongs to the caller. Times are
seconds and all coordinates use a fixed 1920 x 1080 design canvas.
"""
from __future__ import annotations

import math

DESIGN_SIZE = (1920, 1080)
ENTRY_DURATION = 5.967
TRANSITION_DURATION = 3.0
CARD_RIGHT = 1293.
CARD_LEFT = 123.
CARD_WIDTH = 510.
CARD_HEIGHT = 194.
CARD_ROWS = (218., 425., 632., 840.)
HEADER = (122., 48., 1687., 113.)
CHART = (252., 281., 998., 660.)
DATA_LEFT = 633.
DATA_WIDTH = 1176.
COLUMN_CENTRES = (702., 963., 1230., 1461., 1695.)
# Measured from the actual supplied 02 original-speed clip: simultaneous X/Y
# axis endpoint positions, and the green strip's right edge (A source seconds
# minus 3.933). Its very brisk first frames are missed by generic cubic ease.
AXES_ENTRY_SAMPLES = ((.800, 0.), (.834, .300), (.867, .647),
                      (.967, .865), (1.067, .953), (1.167, .989), (1.267, 1.))
HEADER_ENTRY_SAMPLES = ((.867, 0.), (.900, .395), (.934, .630),
                        (.967, .760), (1.067, .925), (1.167, .977), (1.267, 1.))


def _seconds(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f'{name} must be a finite, non-negative number')
    return float(value)


def duration(chart_hold=15., stats_hold=15.):
    """Full entry + both complete-page holds + measured table transition."""
    return ENTRY_DURATION + _seconds(chart_hold, 'chart_hold') + TRANSITION_DURATION + _seconds(stats_hold, 'stats_hold')


def _clamp(value):
    # Decimal reference timings must settle exactly despite binary float error.
    if value >= 1.-1e-12:return 1.
    if value <= 1e-12:return 0.
    return value


def _bezier(progress):
    # Solve CSS cubic-bezier(.5, 0, .42, 1), rather than treating its X as time.
    if progress <= 0 or progress >= 1:
        return _clamp(progress)
    lo, hi = 0., 1.
    for _ in range(24):
        t = (lo + hi) / 2
        x = 3*(1-t)**2*t*.5 + 3*(1-t)*t*t*.42 + t**3
        if x < progress:lo = t
        else:hi = t
    t = (lo + hi) / 2
    return 3*(1-t)*t*t + t**3


def _observed(t, samples):
    """Monotonic Hermite interpolation through measured source endpoints."""
    if t <= samples[0][0]:return samples[0][1]
    if t >= samples[-1][0]:return samples[-1][1]
    widths = [right[0]-left[0] for left, right in zip(samples, samples[1:])]
    slopes = [(right[1]-left[1])/width for left, right, width in zip(samples, samples[1:], widths)]
    tangents = [slopes[0]]
    for i in range(1, len(samples)-1):
        a, b = slopes[i-1], slopes[i]
        w1, w2 = 2*widths[i]+widths[i-1], widths[i]+2*widths[i-1]
        tangents.append(0. if a*b <= 0 else (w1+w2)/(w1/a+w2/b))
    tangents.append(0.)
    for i, (left, right) in enumerate(zip(samples, samples[1:])):
        if t <= right[0]:
            length = right[0]-left[0];u = (t-left[0])/length
            return _clamp((2*u**3-3*u*u+1)*left[1]+(u**3-2*u*u+u)*length*tangents[i]
                          +(-2*u**3+3*u*u)*right[1]+(u**3-u*u)*length*tangents[i+1])
    return 1.


def _track(t, at, length, row=0, stagger=0., curve='linear'):
    value = _clamp((t-at-row*stagger)/length)
    if curve == 'out':return 1-(1-value)**3
    if curve == 'inout':return 4*value**3 if value < .5 else 1-(-2*value+2)**3/2
    if curve == 'card':return _bezier(value)
    return value


def timeline(elapsed, chart_hold=15., stats_hold=15.):
    """Return independent render tracks; seeking never emits audio or mutates data.

    ``phase`` is entry/chart_hold/transition/stats_hold/complete. ``time`` and
    ``duration`` describe the whole sequence. The remaining values are bounded
    opacity/progress or design-pixel coordinates: header_bar/header_text,
    background_opacity, axes_progress/axes_opacity/grid_opacity, four line
    masks ``{from,to}``, four cards, table headers_opacity and four data rows.
    Card background, names and portraits retain separate entry tracks. The
    rows expose values_opacity and two independently moving diagonal sweeps.
    Complete-page holds keep geometry and data fixed. The painter uses ``time``
    separately so the line colours continue to drift throughout the graph hold.
    """
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed):
        raise ValueError('elapsed must be finite')
    total = duration(chart_hold, stats_hold)
    stats_start = ENTRY_DURATION + chart_hold
    stats_complete = stats_start + TRANSITION_DURATION
    t = max(0., min(total, float(elapsed)))
    if t < ENTRY_DURATION:phase = 'entry'
    elif t < stats_start:phase = 'chart_hold'
    elif t < stats_complete:phase = 'transition'
    elif t < total:phase = 'stats_hold'
    else:phase = 'complete'
    entry = t < stats_start
    local = min(t, ENTRY_DURATION) if entry else min(TRANSITION_DURATION, t-stats_start)
    axes = _observed(local, AXES_ENTRY_SAMPLES) if entry else 1-_track(local, .040, .380, curve='inout')
    grid = _track(local, 1.334, .433) if entry else 1-_track(local, .120, .540)
    erase = 0. if entry else _track(local, 0., .500)
    cards, rows = [], []
    for row, y in enumerate(CARD_ROWS):
        move = 0. if entry else _track(local, 1.020, .700, row, .050, 'card')
        background = _track(local, 4.500, .367, row, .100, 'out') if entry else 1.
        cards.append(dict(x=CARD_RIGHT+(CARD_LEFT-CARD_RIGHT)*move, y=y,
                          width=CARD_WIDTH, height=CARD_HEIGHT,
                          background_opacity=background,
                          background_offset=-40*(1-background) if entry else 0.,
                          name_opacity=_track(local, 4.834, .466, row, .100) if entry else 1.,
                          portrait_opacity=_track(local, 4.867, .800, row, .100) if entry else 1.))
        sweep = 0. if entry else _track(local, 2.020, .300, row, .100)
        active = not entry and 2.020+row*.100 <= local < 2.320+row*.100
        fade = max(0., min(1., sweep/.08, (1-sweep)/.08)) if active else 0.
        rows.append(dict(values_opacity=0. if entry else _track(local, 2.270, .420, row, .100),
                         sweep_progress=sweep, sweep_opacity=fade,
                         sweep_accent_progress=_clamp(sweep+.08*math.sin(2*math.pi*sweep))))
    return dict(phase=phase, time=t, duration=total, stats_start=stats_start,
                stats_complete=stats_complete, complete=t >= total,
                background_opacity=_track(local, 0., .400) if entry else 1.,
                header_bar=_observed(local, HEADER_ENTRY_SAMPLES) if entry else 1.,
                header_text=_track(local, 1.334, .433) if entry else 1.,
                axes_progress=axes, axes_opacity=1. if entry else axes,
                grid_opacity=grid,
                lines=[dict(**{'from': 0. if entry else erase}, to=_track(local, 2., 2.333, row, .100) if entry else 1.) for row in range(4)],
                cards=cards, headers_opacity=0. if entry else _track(local, 2.040, .230), rows=rows)
