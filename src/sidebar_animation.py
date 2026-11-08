"""Measured ranking-sidebar entry/exit tracks, including reversible cues."""
from __future__ import annotations

from dataclasses import dataclass

ENTRY_SECONDS = .9
EXIT_SECONDS = .2


def _clamp(value):
    if value >= 1.-1e-12:return 1.
    if value <= 1e-12:return 0.
    return max(0., min(1., value))


@dataclass(frozen=True)
class SidebarFrame:
    width: float = 0.
    opacity: float = 0.
    foreground: float = 0.
    highlight: float = 0.


HIDDEN = SidebarFrame()
SHOWN = SidebarFrame(1., 1., 1., 1.)


class SidebarTransition:
    """One absolute clock; a reversed cue begins from its exact visible state."""
    def __init__(self):
        self.target = False
        self.started = None
        self.initial = HIDDEN

    def active(self, now):
        return self.started is not None and now-self.started < (ENTRY_SECONDS if self.target else EXIT_SECONDS)-1e-9

    def frame(self, now):
        if self.started is None:
            return SHOWN if self.target else HIDDEN
        age = max(0., now-self.started)
        start = self.initial
        if self.target:
            if age >= ENTRY_SECONDS-1e-9:return SHOWN
            # Backgrounds remain anchored on their left edge. Source-frame
            # widths show a smooth ease-out with no bounce or font scaling.
            growth = 1-(1-_clamp(age/.5))**2
            foreground = _clamp((age-.467)/.4)
            highlight = _clamp((age-.5)/.367)
            opacity = 1. if start.width <= 1e-9 else start.opacity+(1-start.opacity)*_clamp(age/.2)
            return SidebarFrame(start.width+(1-start.width)*growth, opacity,
                                start.foreground+(1-start.foreground)*foreground,
                                start.highlight+(1-start.highlight)*highlight)
        if age >= EXIT_SECONDS-1e-9:return HIDDEN
        u = _clamp(age/EXIT_SECONDS)
        return SidebarFrame(start.width*(1-u**3), start.opacity*(1-u), start.foreground, start.highlight)

    def cue(self, visible, now, restart=False):
        visible = bool(visible)
        if visible == self.target and not restart:return False
        current = self.frame(now)
        if restart and visible and not self.active(now):current = HIDDEN
        if not visible and current.width <= 1e-9:
            self.target = False;self.started = None;self.initial = HIDDEN
            return False
        self.initial = current
        self.target = visible
        self.started = float(now)
        return True
