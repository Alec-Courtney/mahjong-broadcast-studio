"""Independent tile, count and separator tracks from the ML withdrawal clip."""
from __future__ import annotations

from dataclasses import dataclass, field


def _progress(age, duration, delay=0.):
    return max(0., min(1., (age-delay)/duration))


@dataclass(frozen=True)
class WaitFrame:
    tiles_alpha: float = 1.
    tiles_x: float = 0.
    count_alpha: float = 1.
    line_fraction: float = 1.


@dataclass
class WaitMotion:
    started: float
    entering: bool
    player: dict
    remaining: object = None
    origin: WaitFrame = field(default_factory=WaitFrame)

    def active(self, now, speed=1.):
        return (now-self.started)*max(.1, speed) < .5

    def frame(self, now, speed=1.):
        age = max(0., now-self.started)*max(.1, speed)
        origin = self.origin
        if self.entering:
            t = _progress(age, .46);t = t*t*(3-2*t)
            return WaitFrame(origin.tiles_alpha+(1-origin.tiles_alpha)*t,
                             origin.tiles_x*(1-t),
                             origin.count_alpha+(1-origin.count_alpha)*_progress(age, .38),
                             origin.line_fraction+(1-origin.line_fraction)*_progress(age, .3))
        # Frames 20–29: tile shifts left and fades, count fades sooner.
        # The stationary divider then retracts toward its bottom endpoint.
        t = _progress(age, 10/30)
        return WaitFrame(origin.tiles_alpha*(1-t),
                         origin.tiles_x+(-24-origin.tiles_x)*t,
                         origin.count_alpha*(1-_progress(age, 7/30)),
                         origin.line_fraction*(1-_progress(age, 9/30, 6/30)))
