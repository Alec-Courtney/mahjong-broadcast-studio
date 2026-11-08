"""A frozen, code-native title -> complete team rosters -> active players opening.

This module reads a Model once and never writes it or creates a window. All
media pixels are frozen in the snapshot; the painter never reopens live paths.
The supplied title image is contained intact. The 18-second motion is a new
ML-inspired design, not a timing measurement from a reference broadcast.
"""
from __future__ import annotations

from dataclasses import dataclass,replace
from functools import lru_cache
import math
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainterPath, QPen

from identity_media import contain_rect, load_identity_image, resolve_identity_media

DESIGN_SIZE = (1920,1080)
OPENING_DURATION = 18.0


@dataclass(frozen=True)
class OpeningMedia:
    kind: str
    png: bytes
    width: int
    height: int
    label: str = ''
    reference: str = ''


@dataclass(frozen=True)
class OpeningMember:
    player_id: str
    name: str
    selected: bool
    seat: int | None = None
    seat_label: str = ''


@dataclass(frozen=True)
class OpeningPlayer:
    player_id: str
    name: str
    team_id: str
    team_name: str
    team_color: str
    seat: int
    seat_label: str
    media: OpeningMedia


@dataclass(frozen=True)
class OpeningTeam:
    team_id: str
    name: str
    color: str
    logo: OpeningMedia
    members: tuple[OpeningMember,...]


@dataclass(frozen=True)
class OpeningSnapshot:
    title: str
    title_reference: str
    title_image: OpeningMedia
    teams: tuple[OpeningTeam,...]
    players: tuple[OpeningPlayer,...]
    font_family: str = 'Microsoft YaHei'
    font_scale: float = 1.0
    duration: float = OPENING_DURATION


@dataclass(frozen=True)
class OpeningFrame:
    elapsed: float
    duration: float
    phase: str
    opacity: float
    title_alpha: float
    roster_alpha: float
    focus_alpha: float
    team_progress: tuple[float,...]
    player_progress: tuple[float,...]
    complete: bool


def _ease(value):
    t=max(0.,min(1.,value))
    return t*t*(3-2*t)


def _team_key(profile):
    return str(profile.get('team_id') or profile.get('team') or '')


def _color(value):
    color=QColor(value) if isinstance(value,str) else QColor()
    return color.name() if color.isValid() else '#61717a'


def _freeze(image,kind,label='',reference=''):
    if image is None or image.isNull():
        return OpeningMedia(kind,b'',0,0,str(label),str(reference))
    # More resolution than the largest native panel needs; preserve every edge.
    if max(image.width(),image.height())>2048:
        image=image.scaled(2048,2048,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
    raw=QByteArray();buffer=QBuffer(raw);buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    try:
        if not image.save(buffer,'PNG'):raise ValueError('无法冻结开场图片')
    finally:buffer.close()
    return OpeningMedia(kind,bytes(raw),image.width(),image.height(),str(label),str(reference))


def _title_presentation(model):
    state = model.state
    config = state.get('broadcast', {}).get('config', {})
    settings = getattr(model, 'settings', {})
    try:
        scale = max(.6, min(1.6, float(settings.get('overlay_font_scale', 1.))))
    except (TypeError, ValueError):
        scale = 1.
    if not math.isfinite(scale):
        scale = 1.
    title = str(config.get('title') or state.get('title') or
                (getattr(model, 'event', None) or {}).get('name') or '本场对局')
    return title, str(settings.get('overlay_font_family') or 'Microsoft YaHei'), scale


def prepare_opening(model,title_reference=None,require_distinct_teams=True):
    """Freeze the actual four teams and their complete event rosters.

    ``None`` selects the event image or the default card in the project root.
    An explicit empty string selects the generic title.
    A title/portrait that cannot be read uses the event title/own-team identity.
    """
    from opening_config import opening_title_reference

    state=model.state;broadcast=state.get('broadcast',{})
    active=list(state.get('players',[]))
    if len(active)!=4:
        raise ValueError('四队开场需要当前四席均已安排选手')
    keys=[_team_key(profile) for profile in active]
    if require_distinct_teams and (any(not key for key in keys) or len(set(keys))!=4):
        raise ValueError('四队开场需要当前四席分属四支不同团队，请先完成队伍与出战选手安排')
    ids=[str(p.get('id') or '') for p in active]
    if any(not identity for identity in ids) or len(set(ids))!=4:
        raise ValueError('四队开场需要四位不同的出战选手')
    roster=broadcast.get('roster',[])
    if isinstance(roster,dict):roster=list(roster.values())
    # Preserve event roster order. Current table facts win for selected profiles.
    records={str(p.get('id')):dict(p) for p in roster if isinstance(p,dict) and p.get('id')}
    for profile in active:
        records[str(profile['id'])]={**records.get(str(profile['id']),{}),**profile}
    dealer=int(state.get('dealer',0))%4
    selected={identity:seat for seat,identity in enumerate(ids)}
    seat_label=lambda seat:('东家','南家','西家','北家')[(seat-dealer)%4]
    teams=[];players=[]
    for seat,profile in enumerate(active):
        key=keys[seat];name=str(profile.get('team') or key)
        color=_color(profile.get('team_color') or profile.get('color'))
        logo=resolve_identity_media(model,profile,team=True)
        frozen_logo=_freeze(logo.image,logo.kind,logo.label,logo.reference)
        members=[]
        for identity,record in records.items():
            if _team_key(record)!=key or (record.get('hidden') and identity not in selected):continue
            member_seat=selected.get(identity)
            members.append(OpeningMember(identity,str(record.get('name') or identity),member_seat is not None,
                                         member_seat,seat_label(member_seat) if member_seat is not None else ''))
        teams.append(OpeningTeam(key,name,color,frozen_logo,tuple(members)))
        media=resolve_identity_media(model,profile)
        players.append(OpeningPlayer(ids[seat],str(profile.get('name') or ids[seat]),key,name,color,seat,seat_label(seat),
                                     _freeze(media.image,media.kind,media.label,media.reference)))
    reference=opening_title_reference(model) if title_reference is None else str(title_reference or '')
    title_image=_freeze(load_identity_image(model,reference),'title',reference=reference)
    title,font_family,font_scale=_title_presentation(model)
    return OpeningSnapshot(title,reference,title_image,tuple(teams),tuple(players),font_family,font_scale)


def opening_timeline(elapsed,duration=OPENING_DURATION):
    if isinstance(elapsed,bool) or not isinstance(elapsed,(int,float)) or not math.isfinite(elapsed):
        raise ValueError('开场播放时间必须是有限秒数')
    if isinstance(duration,bool) or not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<=0:
        raise ValueError('开场总时长必须大于零')
    actual=max(0.,min(float(elapsed),float(duration)));t=actual*OPENING_DURATION/duration
    phase='title' if t<4 else 'roster' if t<11 else 'focus' if t<17.2 else 'exit' if t<18 else 'complete'
    opacity=_ease(t/.55)*(1-_ease((t-17.2)/.8))
    focus=_ease((t-10.8)/.65)
    return OpeningFrame(actual,float(duration),phase,opacity,1-_ease((t-3.8)/.55),
                        _ease((t-3.8)/.6)*(1-focus),focus,
                        tuple(_ease((t-4.05-seat*.22)/.85) for seat in range(4)),
                        tuple(_ease((t-11.05-seat*.15)/.9) for seat in range(4)),t>=18)


@lru_cache(maxsize=24)
def _image(raw):
    return QImage.fromData(raw) if raw else QImage()


def title_bounds(snapshot):
    image=snapshot.title_image
    if not image.png:return QRectF(0,0,*DESIGN_SIZE)
    return _title_rect(image.width,image.height)


def _title_rect(width,height):
    ratio=min(DESIGN_SIZE[0]/width,DESIGN_SIZE[1]/height)
    width,height=width*ratio,height*ratio
    return QRectF((DESIGN_SIZE[0]-width)/2,(DESIGN_SIZE[1]-height)/2,width,height)


def _text(p,snapshot,value,box,size,color='#f1f4f5',bold=False,align=Qt.AlignmentFlag.AlignLeft):
    _styled_text(p,snapshot.font_family,snapshot.font_scale,value,box,size,color,bold,align)


def _styled_text(p,family,scale,value,box,size,color='#f1f4f5',bold=False,align=Qt.AlignmentFlag.AlignLeft):
    """Keep long or multi-script identities inside their measured text boxes."""
    box=QRectF(box);value=str(value);font=QFont(family)
    font.setPixelSize(max(8,round(size*scale)));font.setBold(bold)
    while font.pixelSize()>8 and QFontMetricsF(font).horizontalAdvance(value)>box.width():
        font.setPixelSize(font.pixelSize()-1)
    metrics=QFontMetricsF(font);value=metrics.elidedText(value,Qt.TextElideMode.ElideRight,max(1,round(box.width())))
    p.save();p.setClipRect(box,Qt.ClipOperation.IntersectClip);p.setFont(font);p.setPen(QColor(color))
    p.drawText(box,int(align|Qt.AlignmentFlag.AlignVCenter|Qt.TextFlag.TextSingleLine),value);p.restore()


def _polygon(box):
    x,y,w,h=box.x(),box.y(),box.width(),box.height();cut=24.
    path=QPainterPath();path.moveTo(x+cut,y);path.lineTo(x+w,y);path.lineTo(x+w,y+h-cut)
    path.lineTo(x+w-cut,y+h);path.lineTo(x,y+h);path.lineTo(x,y+cut);path.closeSubpath()
    return path


def _wipe(box,progress):
    x,y,w,h=box.x(),box.y(),box.width(),box.height();end=x+(w+50)*progress
    path=QPainterPath();path.moveTo(x-1,y-1);path.lineTo(end,y-1)
    path.lineTo(end-50,y+h+1);path.lineTo(x-1,y+h+1);path.closeSubpath()
    return path


def _background(p):
    gradient=QLinearGradient(0,0,1920,1080);gradient.setColorAt(0,QColor('#16232c'));gradient.setColorAt(1,QColor('#060d13'))
    p.fillRect(QRectF(0,0,*DESIGN_SIZE),gradient)
    p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#06ffffff'))
    for x in (-460,640,1720):
        path=QPainterPath();path.moveTo(x,0);path.lineTo(x+220,0);path.lineTo(x-430,1080);path.lineTo(x-650,1080);path.closeSubpath();p.drawPath(path)


def _media(p,snapshot,media,box,accent):
    p.save();p.setClipRect(box,Qt.ClipOperation.IntersectClip)
    gradient=QLinearGradient(box.topLeft(),box.bottomRight());color=QColor(accent);color.setAlpha(35)
    gradient.setColorAt(0,color);gradient.setColorAt(1,QColor('#10080e12'));p.fillRect(box,gradient)
    image=_image(media.png)
    if not image.isNull():
        if media.kind=='photo':
            ratio=box.width()/box.height();w,h=image.width(),image.height()
            sw,sh=(h*ratio,h) if w/h>ratio else (w,w/ratio)
            p.drawImage(box,image,QRectF((w-sw)/2,max(0.,(h-sh)/3),sw,sh))
        else:p.drawImage(contain_rect(image,box),image)
    else:
        _text(p,snapshot,media.label or '—',box.adjusted(16,8,-16,-8),34,bold=True,align=Qt.AlignmentFlag.AlignHCenter)
    p.restore()


def _card(p,box,color,progress):
    p.setClipPath(_wipe(box,progress),Qt.ClipOperation.IntersectClip)
    gradient=QLinearGradient(box.topLeft(),box.bottomRight());gradient.setColorAt(0,QColor('#24323c'));gradient.setColorAt(1,QColor('#101a22'))
    p.fillPath(_polygon(box),gradient);edge=QColor(color);edge.setAlpha(110);p.setPen(QPen(edge,1.25));p.drawPath(_polygon(box))
    p.fillRect(QRectF(box.left()+24,box.top(),box.width()-24,5),QColor(color))


def _header(p,snapshot,label):
    _text(p,snapshot,label,QRectF(96,65,1120,76),44,bold=True)
    _text(p,snapshot,snapshot.title,QRectF(1240,82,584,45),22,'#aab8c2',align=Qt.AlignmentFlag.AlignRight)
    p.fillRect(QRectF(96,151,1728,1),QColor('#34434e'))


def _roster(p,snapshot,frame):
    _header(p,snapshot,'本场阵容')
    for index,team in enumerate(snapshot.teams):
        progress=frame.team_progress[index]
        if progress<=0:continue
        box=QRectF(96+438*index,194+30*(1-progress),414,760)
        p.save();_card(p,box,team.color,progress)
        _media(p,snapshot,team.logo,QRectF(box.x()+90,box.y()+42,234,160),team.color)
        _text(p,snapshot,team.name,QRectF(box.x()+24,box.y()+214,366,58),34,bold=True,align=Qt.AlignmentFlag.AlignHCenter)
        p.fillRect(QRectF(box.x()+24,box.y()+290,366,1),QColor('#52616c'))
        _text(p,snapshot,'完整名单',QRectF(box.x()+24,box.y()+302,366,36),19,'#aebbc5')
        count=len(team.members);columns=1 if count<=8 else 2 if count<=20 else 3
        rows=max(1,math.ceil(count/columns));height=min(69.,350/rows);width=366/columns
        for number,member in enumerate(team.members):
            column,row=divmod(number,rows);area=QRectF(box.x()+24+column*width,box.y()+350+row*height,width-4,height-6)
            if member.selected:
                highlight=QColor(team.color);highlight.setAlpha(78);p.fillRect(area,highlight)
                p.fillRect(QRectF(area.left(),area.top(),3,area.height()),QColor(team.color))
            badge=86 if member.selected and columns==1 else 0
            _text(p,snapshot,member.name,area.adjusted(12,0,-badge-8,0),min(29,height*.5),bold=member.selected)
            if badge:
                _text(p,snapshot,'本场出战',QRectF(area.right()-badge-6,area.top(),badge,area.height()),17,bold=True,align=Qt.AlignmentFlag.AlignHCenter)
        active=next(member for member in team.members if member.selected)
        _text(p,snapshot,active.seat_label+'  ·  本场出战',QRectF(box.x()+24,box.y()+704,366,32),19,'#b9c7d0',align=Qt.AlignmentFlag.AlignHCenter)
        p.restore()


def _focus(p,snapshot,frame):
    _header(p,snapshot,'本场出战')
    for index,player in enumerate(snapshot.players):
        progress=frame.player_progress[index]
        if progress<=0:continue
        box=QRectF(96+438*index,194+30*(1-progress),414,760)
        p.save();_card(p,box,player.team_color,progress)
        _text(p,snapshot,'本场出战',QRectF(box.x()+28,box.y()+22,358,40),20,'#afbec8')
        _media(p,snapshot,player.media,QRectF(box.x()+28,box.y()+78,358,418),player.team_color)
        _text(p,snapshot,player.name,QRectF(box.x()+28,box.y()+518,358,70),44,bold=True,align=Qt.AlignmentFlag.AlignHCenter)
        _text(p,snapshot,player.team_name,QRectF(box.x()+28,box.y()+592,358,50),26,'#c3d0d8',align=Qt.AlignmentFlag.AlignHCenter)
        p.fillRect(QRectF(box.x()+157,box.y()+672,100,43),QColor(player.team_color))
        badge_ink='#0b151c' if QColor(player.team_color).lightnessF()>.52 else '#f1f4f5'
        _text(p,snapshot,player.seat_label,QRectF(box.x()+157,box.y()+672,100,43),23,badge_ink,bold=True,align=Qt.AlignmentFlag.AlignHCenter)
        p.restore()


def _draw_title(p,image,title,family,scale):
    if image is not None and not image.isNull():
        p.fillRect(QRectF(0,0,*DESIGN_SIZE),QColor('#e9eaec'))
        p.drawImage(_title_rect(image.width(),image.height()),image)
    else:
        _styled_text(p,family,scale,title,QRectF(180,390,1560,150),76,bold=True,align=Qt.AlignmentFlag.AlignHCenter)
        _styled_text(p,family,scale,'四队同桌  ·  本场对局',QRectF(180,564,1560,70),32,'#b4c2ca',align=Qt.AlignmentFlag.AlignHCenter)


def draw_opening_title_preview(p,model):
    """Use the programme's title artwork without encoding a playback snapshot."""
    from opening_config import opening_title_reference

    image=load_identity_image(model,opening_title_reference(model))
    _background(p)
    _draw_title(p,image,*_title_presentation(model))


def draw_opening(p,snapshot,elapsed,page=None):
    """Paint the 1920x1080 opening on the caller's painter; return its frame."""
    frame=opening_timeline(elapsed,snapshot.duration)
    if page is not None:
        offset={'title':0.,'roster':4.,'focus':11.}[page]
        frame=opening_timeline(offset+min(elapsed,2.),snapshot.duration)
        frame=replace(frame,opacity=_ease(elapsed/.55),title_alpha=1. if page=='title' else 0.,
                      roster_alpha=1. if page=='roster' else 0.,focus_alpha=1. if page=='focus' else 0.)
    if frame.opacity<=0:return frame
    p.save();p.setRenderHint(p.RenderHint.Antialiasing);p.setRenderHint(p.RenderHint.SmoothPixmapTransform)
    p.setClipRect(QRectF(0,0,*DESIGN_SIZE),Qt.ClipOperation.IntersectClip);p.setOpacity(p.opacity()*frame.opacity)
    _background(p)
    if frame.title_alpha>0:
        p.save();p.setOpacity(p.opacity()*frame.title_alpha)
        _draw_title(p,_image(snapshot.title_image.png),snapshot.title,snapshot.font_family,snapshot.font_scale)
        p.restore()
    if frame.roster_alpha>0:
        p.save();p.setOpacity(p.opacity()*frame.roster_alpha);_roster(p,snapshot,frame);p.restore()
    if frame.focus_alpha>0:
        p.save();p.setOpacity(p.opacity()*frame.focus_alpha);_focus(p,snapshot,frame);p.restore()
    p.restore()
    return frame
