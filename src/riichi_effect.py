"""Transparent team riichi declaration and a snapshot-based FIFO queue.

This module never changes the hand, player cards, payments or visibility policy.
The caller supplies a 1920x1080 capture surface and advances one queue clock.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from functools import lru_cache
import math
import os
from pathlib import Path
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QImage,
                          QLinearGradient, QPainterPath, QPen, QTransform)

from identity_media import contain_rect, resolve_identity_media


DESIGN_SIZE = (1920, 1080)
RIICHI_DURATION = 2.5
LOGO_RECT = (502.5, 363.75, 918.75, 288.75)
STICK_CENTRE = (960., 787.5)
STICK_WIDTH = 660.
STICK_HEIGHT = 78.75


@dataclass(frozen=True)
class RiichiSnapshot:
    player_id: str
    seat: int
    player_name: str
    team_name: str
    team_id: str
    team_color: str
    logo: QImage | None = None
    logo_reference: str = ''
    sequence_id: int = 0

    def copy(self, **changes):
        """Copy pixel storage too; later edits to a cache/file cannot change it."""
        changes.setdefault('logo', self.logo.copy() if self.logo is not None else None)
        return replace(self, **changes)


def _trim_logo(image):
    """Remove only empty alpha margins before containing the supplied mark."""
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width,height = rgba.width(),rgba.height()
    alpha = bytes(rgba.constBits())[3::4]
    visible = alpha.translate(bytes(1 if value >= 12 else 0 for value in range(256)))
    first,last = visible.find(b'\x01'),visible.rfind(b'\x01')
    if first < 0:
        return None
    top,bottom = first//width,last//width
    left,right = width,0
    for y in range(top,bottom+1):
        row=visible[y*width:(y+1)*width]
        first,last=row.find(b'\x01'),row.rfind(b'\x01')
        if first >= 0:left,right=min(left,first),max(right,last)
    return image.copy(left,top,right-left+1,bottom-top+1)


def snapshot_riichi(model, seat):
    """Freeze the actual declaring player's own-team media, never a portrait."""
    players = model.state.get('players', [])
    if isinstance(seat, bool) or not isinstance(seat, int) or not 0 <= seat < len(players):
        raise ValueError('立直选手座位无效')
    player = players[seat]
    media = resolve_identity_media(model, player, team=True)
    color = player.get('team_color') or media.accent
    if not isinstance(color, str) or not QColor(color).isValid():
        color = '#61717a'
    return RiichiSnapshot(
        player_id=str(player.get('id') or player.get('player_id') or seat), seat=seat,
        player_name=str(player.get('name') or ''),
        team_name=str(player.get('team') or (media.label if media.kind == 'team' else '') or player.get('name') or '—'),
        team_id=str(player.get('team_id') or player.get('team') or ''),
        team_color=QColor(color).name(),
        logo=_trim_logo(media.image) if media.kind == 'team_logo' and media.image is not None else None,
        logo_reference=media.reference if media.kind == 'team_logo' else '',
    )


class RiichiQueue:
    """Exactly one active declaration; later clicks wait for its full duration.

    enqueue returns its frozen item with a unique sequence_id. advance returns
    whether the active item changed; only one completed item advances per call.
    A late timer starts the next effect at the current time, never halfway in.
    All times are monotonic seconds, independent of other overlay speed settings.
    """
    def __init__(self, duration=RIICHI_DURATION, clock=None):
        if isinstance(duration, bool) or not isinstance(duration, (int,float)) or not math.isfinite(duration) or duration <= 0:
            raise ValueError('立直特效时长无效')
        self.duration = float(duration)
        self.clock = clock or time.monotonic
        self.active = None
        self.last_completed = None
        self.completed_count = 0
        self._pending = deque()
        self._started = 0.
        self._sequence = 0

    def _now(self, now):
        value = self.clock() if now is None else now
        if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value):
            raise ValueError('立直特效时钟无效')
        return float(value)

    @property
    def pending(self):
        return tuple(self._pending)

    @property
    def pending_count(self):
        return len(self._pending)

    @property
    def busy(self):
        return self.active is not None

    def elapsed(self, now=None):
        now = self._now(now)
        return min(self.duration, max(0., now-self._started)) if self.active is not None else 0.

    def enqueue(self, snapshot, now=None):
        if not isinstance(snapshot, RiichiSnapshot):
            raise TypeError('立直队列需要冻结的选手信息')
        now = self._now(now)
        self.advance(now)
        self._sequence += 1
        frozen = snapshot.copy(sequence_id=self._sequence)
        if self.active is None:
            self.active, self._started = frozen, now
        else:
            self._pending.append(frozen)
        return frozen

    def advance(self, now=None):
        now = self._now(now)
        previous = self.active
        if self.active is not None and now-self._started >= self.duration-1e-12:
            self.last_completed = self.active
            self.completed_count += 1
            self.active = None
        if self.active is None and self._pending:
            self.active, self._started = self._pending.popleft(), now
        return self.active is not previous

    def cancel(self, player_id=None, now=None):
        """Cancel matching active/pending items, or clear everything with None."""
        now = self._now(now)
        removed = 0
        if self.active is not None and (player_id is None or self.active.player_id == player_id):
            self.active = None
            removed += 1
        retained = deque()
        for item in self._pending:
            if player_id is None or item.player_id == player_id:
                removed += 1
            else:
                retained.append(item)
        self._pending = retained
        if self.active is None and self._pending:
            self.active, self._started = self._pending.popleft(), now
        return removed

    def clear(self):
        return self.cancel()


def _track(t, start, length):
    return max(0., min(1., (t-start)/length))


@lru_cache(maxsize=32)
def _curve_slopes(knots):
    """Monotone Hermite tangents avoid stops at every measured video frame."""
    spans = [b[0]-a[0] for a,b in zip(knots,knots[1:])]
    slopes = [(b[1]-a[1])/span for a,b,span in zip(knots,knots[1:],spans)]
    tangents = [slopes[0]]
    for index in range(1,len(knots)-1):
        before,after = slopes[index-1:index+1]
        if before*after <= 0:
            tangents.append(0.)
        else:
            first,second = 2*spans[index]+spans[index-1],spans[index]+2*spans[index-1]
            tangents.append((first+second)/(first/before+second/after))
    tangents.append(0.)
    return tuple(tangents)


def _curve(t, knots):
    if t <= knots[0][0]:
        return knots[0][1]
    if t >= knots[-1][0]:
        return knots[-1][1]
    tangents = _curve_slopes(knots)
    for index,(left,right) in enumerate(zip(knots,knots[1:])):
        if t <= right[0]:
            span = right[0]-left[0]
            u = (t-left[0])/span
            return ((2*u**3-3*u*u+1)*left[1] + (u**3-2*u*u+u)*span*tangents[index]
                    + (-2*u**3+3*u*u)*right[1] + (u**3-u*u)*span*tangents[index+1])
    return knots[-1][1]


def riichi_timeline(elapsed):
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int,float)) or not math.isfinite(elapsed):
        raise ValueError('立直特效时间无效')
    t = max(0., float(elapsed))
    end = 1-_track(t, 2.4333333333, .0666666667)
    visible = 0. <= t < RIICHI_DURATION
    # Source: the 30fps programme's effective 1024x576 picture, F5-F73.
    # Measurements describe silhouettes/spacing, not recovered author keyframes.
    # The repeated F10/F11 and F17/F18 recording frames are interpolated smoothly.
    logo = _curve(t, ((1/6,0.),(.2,.087),(7/30,.235),(8/30,.398),(.3,.551),
                      (1/3,.695),(.4,1.)))
    logo_scale = _curve(t, ((1/6,1.52),(.2,1.32),(7/30,1.165),(8/30,1.09),
                            (.3,1.05),(1/3,1.025),(.4,1.)))
    stick_alpha = _curve(t, ((1/6,0.),(.2,.572),(7/30,.725),(8/30,.874),
                             (.3,.98),(1/3,1.)))
    stick_width = _curve(t, ((.4,660.),(.5,649.),(16/30,649.),(17/30,590.),
                             (19/30,550.),(20/30,473.),(.7,363.),(22/30,75.)))
    stick_exit = 1-_curve(t, ((.7,0.),(21.5/30,.16),(22/30,1.)))
    glyph_scale = _curve(t, ((23/30,.16),(.8,.3),(25/30,.4),(26/30,.5),
                             (.9,.78),(28/30,.92),(31/30,1.)))
    # After the glyphs reach full width, only the centres continue separating.
    # Source F34 -> F73: outer word 245 -> 279px, individual R remains ~45px.
    tracking = _curve(t, ((23/30,4.),(.8,20.5),(25/30,29.5),(26/30,34.),
                          (.9,38.),(28/30,41.),(1.,45.),(34/30,50.),
                          (40/30,54.2),(50/30,58.1),(73/30,60.))) * 1.875
    echo = _curve(t, ((.7,0.),(23/30,.58),(.8,.65),(26/30,.52),(1.,.36),
                      (34/30,.26),(40/30,.13),(48/30,0.)))
    core = _curve(t, ((20/30,0.),(.7,.46),(22/30,1.),(23/30,1.),(.8,.45),(25/30,0.)))
    return dict(time=t, complete=t >= RIICHI_DURATION, opacity=end if visible else 0.,
                logo_opacity=logo, logo_scale=logo_scale,
                stick_opacity=stick_alpha*stick_exit, stick_width=stick_width,
                stick_height=STICK_HEIGHT*(1-.07*_track(t,19/30,1/30)),
                fold=_track(t,16/30,6/30), flash_opacity=core, core_opacity=core,
                reach_opacity=_curve(t, ((23/30,0.),(.8,.7),(25/30,1.))),
                reach_scale_x=glyph_scale, reach_tracking=tracking,
                reveal_overlap=_curve(t, ((23/30,0.),(.8,1.),(26/30,.7),(28/30,.35),(32/30,0.))),
                echo_opacity=echo, line_opacity=echo*_track(t,23/30,1/30), glitch=echo,
                sweep=_track(t,23/30,.8))


def _color(value, alpha=1.):
    color = QColor(value)
    color.setAlphaF(max(0.,min(1.,alpha)))
    return color


def _tint(value, white=.35, alpha=1.):
    color = QColor(value)
    return QColor.fromRgbF(*(channel*(1-white)+white for channel in
                             (color.redF(),color.greenF(),color.blueF())),alpha)


@lru_cache(maxsize=1)
def _reach_glyphs():
    # Offscreen Qt does not always discover installed font faces. Register the
    # actual Bold file once, so reviews and the live renderer use the same ink.
    font_file = Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/arialbd.ttf'
    if font_file.is_file():
        QFontDatabase.addApplicationFont(str(font_file))
    font = QFont('Arial')
    font.setPixelSize(135)
    font.setWeight(QFont.Weight.Bold)
    font.setStyleName('Bold')
    glyphs = []
    # Settled source ink sizes in source pixels. Font outlines stay code-native;
    # the small optical correction also avoids platform-dependent face metrics.
    for letter,width,height in zip('REACH',(45,39,49,44,40),(51,51,51,52,51)):
        path = QPainterPath()
        path.addText(0.,0.,font,letter)
        ink = path.boundingRect()
        path.translate(-ink.center().x(),-ink.center().y())
        transform = QTransform()
        transform.scale(width*1.875/max(ink.width(),1.),height*1.875/max(ink.height(),1.))
        glyphs.append(transform.map(path))
    return tuple(glyphs)


def _reach_word(glyph_scale,tracking):
    word = QPainterPath()
    word.setFillRule(Qt.FillRule.WindingFill)
    # A is the optical anchor; C/H have slightly narrower centre advances.
    for glyph,position in zip(_reach_glyphs(),(-2.,-1.,0.,.975,1.9416666667)):
        transform = QTransform()
        transform.translate(position*tracking,0.)
        transform.scale(glyph_scale,1.)
        word.addPath(transform.map(glyph))
    return word


def _stick(painter,tracks,accent):
    cx,cy = STICK_CENTRE
    w,h = tracks['stick_width'],tracks['stick_height']
    radius = min(12.,w*.1)
    painter.save()
    painter.setOpacity(painter.opacity()*tracks['stick_opacity'])
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_color('#09120e',.25))
    painter.drawRoundedRect(QRectF(cx-w/2+1.5,cy-h/2+4.,w,h),radius,radius)
    face = QLinearGradient(cx,cy-h/2,cx,cy+h/2)
    for position,value in ((0.,'#f3f5f3'),(.09,'#ffffff'),(.75,'#ffffff'),(1.,'#e2e5e2')):
        face.setColorAt(position,_color(value))
    painter.setPen(QPen(_color('#3b4940',.5),2.5))
    painter.setBrush(face)
    painter.drawRoundedRect(QRectF(cx-w/2,cy-h/2,w,h),radius,radius)
    painter.setPen(QPen(_color('#ffffff',.8),1.5))
    painter.drawLine(QPointF(cx-w/2+radius,cy-h/2+2.),QPointF(cx+w/2-radius,cy-h/2+2.))
    # A narrow shaded end face appears as the white body folds inward.
    if tracks['fold'] > 0:
        inset = 2.+tracks['fold']*4.
        painter.setPen(QPen(_color('#9daba5',.22),inset))
        painter.drawLine(QPointF(cx+w/2-inset/2,cy-h*.34),QPointF(cx+w/2-inset/2,cy+h*.34))
    dot = QLinearGradient(cx-7.,cy-14.,cx+5.,cy+14.)
    dot.setColorAt(0.,_color('#d05069'))
    dot.setColorAt(.45,_color('#ac2343'))
    dot.setColorAt(1.,_color('#8d1b35'))
    painter.setPen(QPen(_color('#7f3445',.36),1.8))
    painter.setBrush(dot)
    painter.setOpacity(painter.opacity()*(1-.65*_track(tracks['time'],20/30,2/30)))
    painter.drawEllipse(QPointF(cx,cy),14.,14.)
    painter.setPen(QPen(_color('#ffffff',.28),1.1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(cx-11.,cy-11.,22.,22.),35*16,100*16)
    painter.restore()


def _folded_core(painter,tracks,accent):
    if tracks['core_opacity'] <= 0:
        return
    cx,cy = STICK_CENTRE
    painter.save()
    painter.translate(cx,cy)
    painter.setOpacity(painter.opacity()*tracks['core_opacity'])
    fold_finish = _track(tracks['time'],.7,1/30)
    painter.scale(1.+.35*(1-fold_finish),.8+.2*fold_finish)
    # In F21-F24 the squeezed word and its enlarged echoes form the pink
    # central face. Retain glyph counters/vertical seams instead of a flat box.
    panel = QLinearGradient(-41.,0.,41.,0.)
    for position,alpha in ((0.,0.),(.12,.4),(.32,.55),(.54,.38),(.85,.5),(1.,0.)):
        panel.setColorAt(position,_tint(accent,.43,alpha))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(panel)
    painter.drawRect(QRectF(-41.,-57.,82.,114.))
    for scale,height,opacity,glyph_width,tracking in ((1.08,1.16,.22,.36,10.5),
                                                    (.98,1.1,.44,.28,14.),
                                                    (.9,1.04,.62,.5,10.)):
        painter.save()
        painter.scale(scale,height)
        painter.setPen(QPen(_tint(accent,.58,opacity),1.6))
        painter.setBrush(_tint(accent,.66,opacity))
        painter.drawPath(_reach_word(glyph_width,tracking))
        painter.restore()
    painter.setPen(QPen(_tint(accent,.75,.56),1.7))
    for x,top,bottom in ((-36.,-51.,48.),(-28.,-48.,50.),(29.,-49.,52.),(37.,-47.,47.)):
        painter.drawLine(QPointF(x,top),QPointF(x,bottom))
    painter.restore()


def _reach(painter,tracks,accent):
    if tracks['reach_opacity'] <= 0:
        return
    cx,cy = STICK_CENTRE
    word = _reach_word(tracks['reach_scale_x'],tracks['reach_tracking'])
    echo = tracks['echo_opacity']
    painter.save()
    painter.translate(cx+10.3125,cy+.9375)
    painter.setOpacity(painter.opacity()*tracks['reach_opacity'])
    if echo > 0:
        # The original contains whole-word enlarged echoes outside the white
        # face, plus a few hard horizontal cuts; these outlast the fast reveal.
        expansion = _curve(tracks['time'], ((23/30,.95),(.8,1.12),(26/30,1.2),
                                           (1.,1.3),(34/30,1.35),(1.6,1.45)))
        for xscale,yscale,xshift,alpha in ((expansion,1.3,-12.,.62),
                                         (1.+(expansion-1.)*.42,1.15,19.,.35),(.91,1.04,-8.,.25)):
            painter.save()
            painter.translate(xshift*echo,0.)
            painter.scale(xscale,yscale)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_tint(accent,.35,echo*alpha))
            painter.drawPath(word)
            painter.restore()
        painter.setPen(QPen(_tint(accent,.46,echo*.15),9.))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(word)
    overlap = tracks['reveal_overlap']
    if overlap > 0:
        # F24-F28 retain a wider, tightly spaced pale word while the narrow
        # white letters pull apart. Their overlapping stems make the brief
        # solid-looking central flash; this is separate from the late echoes.
        compressed = _reach_word(min(1.,tracks['reach_scale_x']+.42),tracks['reach_tracking']*.8)
        for shift,height,alpha in ((-18.,1.04,.60),(14.,.98,.24)):
            painter.save()
            painter.translate(shift*overlap,0.)
            painter.scale(1.,height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_tint(accent,.78,overlap*alpha))
            painter.drawPath(compressed)
            painter.restore()
    face = QLinearGradient(-260.,0.,260.,0.)
    edge_white = 1.-.2*overlap
    centre_white = 1.-.5*echo-.15*overlap
    face.setColorAt(0.,_tint(accent,edge_white))
    face.setColorAt(.3,_tint(accent,edge_white))
    face.setColorAt(.52,_tint(accent,centre_white))
    face.setColorAt(.72,_tint(accent,edge_white))
    face.setColorAt(1.,_tint(accent,edge_white))
    painter.setPen(QPen(_color('#17221e',.22),1.2))
    painter.setBrush(face)
    painter.drawPath(word)
    if echo > 0:
        frame = math.floor(tracks['time']*30+1e-8)
        for row,(y,height) in enumerate(((-43.,9.),(-6.,5.),(26.,8.))):
            painter.save()
            painter.setClipRect(QRectF(-650.,y,1300.,height))
            painter.translate(((-1.)**(frame+row))*(9.+row*7.)*echo,0.)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_tint(accent,.55,echo*.48))
            painter.drawPath(word)
            painter.restore()
    painter.restore()


def _streaks(painter,tracks,accent):
    strength = tracks['line_opacity']
    if strength <= 0:
        return
    cx,cy = STICK_CENTRE
    travel = tracks['sweep']*190.
    painter.save()
    for left,right,y,width,alpha in ((-850.,-235.,25.,2.5,.72),(-440.,-35.,-41.,2.,.64),
                                    (135.,900.,-29.,2.5,.64),(220.,670.,12.,1.5,.65),
                                    (-650.,-330.,54.,2.2,.76),(290.,810.,48.,2.,.5)):
        direction = -1. if left < 0 else 1.
        left += travel*direction
        right += travel*direction
        gradient = QLinearGradient(cx+left,cy+y,cx+right,cy+y)
        # The inner highlight stays white; the outer run carries actual team
        # colour instead of washing the whole line with the same pale tint.
        # Reverse the ramp on the left so both tails point away from REACH.
        for position,white,opacity in ((0.,1.,0.),(.16,1.,1.),(.30,1.,1.),
                                       (.44,.35,1.),(.54,0.,1.),(.84,0.,1.),
                                       (1.,0.,0.)):
            if direction < 0:position=1.-position
            gradient.setColorAt(position,_tint(accent,white,strength*alpha*opacity))
        painter.setPen(QPen(gradient,width))
        painter.drawLine(QPointF(cx+left,cy+y),QPointF(cx+right,cy+y))
    painter.restore()


def _logo(painter, snapshot, bounds):
    if snapshot.logo is not None and not snapshot.logo.isNull():
        painter.drawImage(contain_rect(snapshot.logo, bounds, 0.), snapshot.logo)
        return
    # A missing logo uses the same real team's name, never another team's mark.
    label = snapshot.team_name or snapshot.player_name or '—'
    font = QFont('Microsoft YaHei')
    font.setPixelSize(118)
    font.setWeight(QFont.Weight.Bold)
    while font.pixelSize() > 20 and QFontMetricsF(font).horizontalAdvance(label) > bounds.width():
        font.setPixelSize(font.pixelSize()-1)
    path = QPainterPath()
    path.addText(0,0,font,label)
    ink = path.boundingRect()
    path.translate(bounds.center().x()-ink.center().x(),bounds.center().y()-ink.center().y())
    painter.setPen(QPen(_color('#071017',.88),9,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(_color('#ffffff'))
    painter.drawPath(path)


def draw_riichi(painter, snapshot, elapsed, design_size=DESIGN_SIZE):
    """Paint the source's transparent logo, folding stick and tracking word."""
    tracks = riichi_timeline(elapsed)
    if tracks['opacity'] <= 0 or snapshot is None:
        return tracks
    painter.save()
    painter.scale(design_size[0]/DESIGN_SIZE[0],design_size[1]/DESIGN_SIZE[1])
    painter.setRenderHint(painter.RenderHint.Antialiasing)
    painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform)
    painter.setOpacity(painter.opacity()*tracks['opacity'])
    accent = snapshot.team_color
    painter.save()
    painter.setOpacity(painter.opacity()*tracks['logo_opacity'])
    logo_bounds = QRectF(*LOGO_RECT)
    scale = tracks['logo_scale']
    painter.translate(logo_bounds.center())
    painter.scale(scale,scale)
    painter.translate(-logo_bounds.center())
    _logo(painter,snapshot,logo_bounds)
    painter.restore()

    if tracks['stick_opacity'] > 0:
        _stick(painter,tracks,accent)
    _folded_core(painter,tracks,accent)
    _streaks(painter,tracks,accent)
    _reach(painter,tracks,accent)
    painter.restore()
    return tracks


# Short aliases for renderers that already use timeline()/duration().
timeline = riichi_timeline
