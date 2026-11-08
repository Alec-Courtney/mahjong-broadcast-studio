"""Reference-measured glossary reveal, vertical rule and text-only exit."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math

ENTRY_SECONDS=40/30
EXIT_SECONDS=.7


@dataclass(frozen=True)
class CommentaryFrame:
    width: float=0.
    divider_height: float=0.
    divider_alpha: float=0.
    text_alpha: float=0.
    text_offset_y: float=0.


HIDDEN=CommentaryFrame()
SHOWN=CommentaryFrame(1.,1.,1.,1.,0.)


def _time(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('术语动画时间无效')
    return float(value)


def _curve(knots):
    """Monotone Hermite interpolation; measured screen-record holds are smoothed."""
    xs=tuple(point[0] for point in knots);ys=tuple(point[1] for point in knots)
    h=[b-a for a,b in zip(xs,xs[1:])]
    slope=[(b-a)/step for a,b,step in zip(ys,ys[1:],h)]
    tangents=[slope[0]]
    for i in range(1,len(xs)-1):
        left,right=slope[i-1],slope[i]
        if left*right<=0:tangents.append(0.)
        else:
            a=2*h[i]+h[i-1];b=h[i]+2*h[i-1]
            tangents.append((a+b)/(a/left+b/right))
    tangents.append(slope[-1])
    def sample(t):
        if t<=xs[0]:return ys[0]
        if t>=xs[-1]:return ys[-1]
        i=bisect_right(xs,t)-1;u=(t-xs[i])/h[i]
        value=(2*u**3-3*u*u+1)*ys[i]+(u**3-2*u*u+u)*h[i]*tangents[i]+(-2*u**3+3*u*u)*ys[i+1]+(u**3-u*u)*h[i]*tangents[i+1]
        return max(min(ys[i],ys[i+1]),min(max(ys[i],ys[i+1]),value))
    return sample


# Source cue: last blank background frame .500s. Widths are measured against
# the fixed source rectangle x255..1154; no text or background font is scaled.
_ENTRY_WIDTH=_curve(((0,0),(1/30,.073),(2/30,.517),(.1,.646),(.2,.755),(7/30,.838),
                     (8/30,.929),(.3,.951),(.4,.977),(16/30,1),(ENTRY_SECONDS,1)))
_ENTRY_DIVIDER=_curve(((0,0),(5/30,0),(.2,.02),(7/30,.55),(8/30,.78),(.3,.81),(11/30,.86),
                       (.4,.94),(13/30,.97),(17/30,1),(ENTRY_SECONDS,1)))
_ENTRY_DIVIDER_ALPHA=_curve(((0,0),(5/30,0),(.2,.2),(7/30,.6),(8/30,1),(ENTRY_SECONDS,1)))
_ENTRY_TEXT=_curve(((0,0),(20/30,0),(.7,.05),(.8,.09),(25/30,.134),(26/30,.314),
                    (28/30,.404),(29/30,.494),(1.,.588),(31/30,.683),(32/30,.852),
                    (1.1,.879),(34/30,.926),(1.2,.955),(37/30,.981),(1.3,1),(ENTRY_SECONDS,1)))
# Exit starts at source 8.133s: text rises about 20 source px (24 base px),
# then the background's right boundary returns left from 8.433..8.833s.
_EXIT_WIDTH=_curve(((0,1),(.3,1),(10/30,.996),(11/30,.979),(.4,.948),(13/30,.899),
                    (.5,.722),(16/30,.584),(17/30,.430),(.6,.284),(19/30,.178),(.7,0)))
_EXIT_DIVIDER=_curve(((0,1),(2/30,1),(.1,.98),(5/30,.79),(.2,.71),(8/30,.54),(.3,0),(.7,0)))
_EXIT_DIVIDER_ALPHA=_curve(((0,1),(2/30,1),(.1,.9),(5/30,.66),(.2,.64),(8/30,.56),(.3,0),(.7,0)))
_EXIT_TEXT=_curve(((0,1),(1/30,.98),(.1,.82),(4/30,.66),(5/30,.46),(.2,.34),(8/30,.22),(.3,0),(.7,0)))
_EXIT_TEXT_Y=_curve(((0,0),(1/30,-2.4),(.1,-6),(4/30,-8.4),(5/30,-12),(.2,-15.6),(8/30,-19.2),(.3,-24),(.7,-24)))


class CommentaryTransition:
    """One reversible cue. Content ownership stays with the caller's snapshot."""
    def __init__(self):
        self.target=False;self.started=None;self.initial=HIDDEN

    @property
    def duration(self):return ENTRY_SECONDS if self.target else EXIT_SECONDS

    def active(self,now):
        now=_time(now)
        return self.started is not None and now-self.started<self.duration-1e-9

    def frame(self,now):
        now=_time(now)
        if self.started is None:return SHOWN if self.target else HIDDEN
        age=max(0.,now-self.started);start=self.initial
        if age>=self.duration-1e-9:return SHOWN if self.target else HIDDEN
        if self.target:
            # Reversals continue from their exact visible state. Already visible
            # text returns immediately instead of waiting for a fresh reveal.
            partial_text=start.text_alpha>1e-9
            text_progress=(min(1.,age/.3) if partial_text else _ENTRY_TEXT(age))
            divider_progress=(min(1.,age/(11/30)) if start.divider_alpha>1e-9 else _ENTRY_DIVIDER(age))
            alpha_progress=(min(1.,age/(2/30)) if start.divider_alpha>1e-9 else _ENTRY_DIVIDER_ALPHA(age))
            return CommentaryFrame(start.width+(1-start.width)*_ENTRY_WIDTH(age),
                                   start.divider_height+(1-start.divider_height)*divider_progress,
                                   start.divider_alpha+(1-start.divider_alpha)*alpha_progress,
                                   start.text_alpha+(1-start.text_alpha)*text_progress,
                                   start.text_offset_y*(1-text_progress))
        return CommentaryFrame(start.width*_EXIT_WIDTH(age),start.divider_height*_EXIT_DIVIDER(age),
                               start.divider_alpha*_EXIT_DIVIDER_ALPHA(age),start.text_alpha*_EXIT_TEXT(age),
                               start.text_offset_y+(-24-start.text_offset_y)*(-_EXIT_TEXT_Y(age)/24))

    def cue(self,visible,now,restart=False):
        now=_time(now);visible=bool(visible)
        if visible==self.target and not restart:return False
        current=self.frame(now)
        if restart and visible and not self.active(now):current=HIDDEN
        if not visible and current.width<=1e-9:
            self.reset();return False
        self.initial=current;self.target=visible;self.started=now
        return True

    def reset(self,visible=False):
        self.target=bool(visible);self.started=None;self.initial=SHOWN if self.target else HIDDEN
