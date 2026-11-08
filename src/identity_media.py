"""One audience-facing identity fallback: portrait, own-team logo, wordmark."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath


@dataclass(frozen=True)
class IdentityMedia:
    kind: str
    image: QImage | None
    label: str
    accent: str
    reference: str = ''


_IMAGES = {}


def load_identity_image(model, reference):
    """A bad or missing path is a display fallback, never a broken picture."""
    if not isinstance(reference, str) or not reference:
        return None
    try:
        from media_store import resolve_media_path
        source = resolve_media_path(model.path,reference)
        stat = source.stat()
        if not source.is_file() or not 0 < stat.st_size <= 8*1024*1024:return None
        key = (str(source), stat.st_mtime_ns, stat.st_size)
        if key not in _IMAGES:
            image = QImage(str(source))
            if image.isNull() or image.width()>10000 or image.height()>10000 or image.width()*image.height()>24000000:
                image = None
            if len(_IMAGES)>96:_IMAGES.clear()
            _IMAGES[key] = image
        return _IMAGES[key]
    except (OSError, ValueError, RuntimeError):
        return None


def _team_key(profile):
    return profile.get('team_id') or profile.get('team') or ''


def resolve_identity_media(model, profile, team=False):
    """Resolve data without changing the player, roster or historical snapshot.

    Explicit snapshot fields win over the current roster. A team card never
    inherits one member's portrait. Team logos may be shared by any teammate.
    """
    state = model.state
    roster = state.get('broadcast', {}).get('roster', [])
    if isinstance(roster, dict):roster = list(roster.values())
    records = [*state.get('players', []), *roster]
    identity = profile.get('player_id') or profile.get('id')
    entity = {}
    if not team:
        match = next((record for record in records if identity and record.get('id') == identity), None)
        if match:entity.update(match)
    entity.update(profile)
    team_name = str(entity.get('team') or (entity.get('name', '') if team else ''))
    team_id = _team_key(entity)
    unassigned = str(team_id or entity.get('id') or '').startswith('unassigned:')
    if unassigned:
        team_id = ''
        if not entity.get('team'):team_name = ''
    if team and not team_id:
        team_id = str(entity.get('id') or '')
        if team_id.startswith('unassigned:'):team_id = ''
    has_team = bool(team_name or team_id)
    accent = entity.get('team_color') or entity.get('player_color') or entity.get('color') or '#61717a'
    if not isinstance(accent, str) or not QColor(accent).isValid():accent = '#61717a'
    if not team:
        photo = entity.get('photo', '')
        image = load_identity_image(model, photo)
        if image is not None:return IdentityMedia('photo', image, entity.get('name', ''), accent, photo)
    if has_team:
        references = [entity.get('team_logo', '')]
        if team:
            registered = state.get('broadcast', {}).get('teams', {}).get(team_id, {})
            references.append(registered.get('team_logo', ''))
        references += [record.get('team_logo', '') for record in records
                       if _team_key(record) and _team_key(record) == (team_id or team_name)]
        for reference in dict.fromkeys(value for value in references if isinstance(value,str)):
            image = load_identity_image(model, reference)
            if image is not None:return IdentityMedia('team_logo', image, team_name or team_id, accent, reference)
        return IdentityMedia('team', None, team_name or team_id, accent)
    return IdentityMedia('neutral', None, '—', '#61717a')


def contain_rect(image, bounds, padding=None):
    """Keep every edge of a wide or tall transparent logo inside its padding."""
    bounds = QRectF(bounds)
    pad = max(4., min(bounds.width(), bounds.height())*.10) if padding is None else max(0., float(padding))
    pad = min(pad, max(0., min(bounds.width(), bounds.height())/2-.5))
    inner = bounds.adjusted(pad, pad, -pad, -pad)
    factor = min(inner.width()/max(1,image.width()), inner.height()/max(1,image.height()))
    width, height = image.width()*factor, image.height()*factor
    return QRectF(inner.center().x()-width/2, inner.center().y()-height/2, width, height)


def paint_identity_media(painter, model, profile, bounds, team=False, radius=0., padding=None):
    """Paint transparent contained logos; portrait photographs retain cover crop."""
    media = resolve_identity_media(model, profile, team=team)
    bounds = QRectF(bounds)
    painter.save()
    clip = QPainterPath()
    clip.addRoundedRect(bounds, radius, radius) if radius else clip.addRect(bounds)
    painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if media.kind != 'photo':painter.fillRect(bounds, QColor('#b305080a'))
    if media.image is not None:
        image = media.image
        if media.kind == 'team_logo':
            painter.drawImage(contain_rect(image, bounds, padding), image)
        else:
            ratio = bounds.width()/max(1.,bounds.height())
            iw, ih = image.width(), image.height()
            sw, sh = (ih*ratio, ih) if iw/ih>ratio else (iw, iw/ratio)
            painter.drawImage(bounds, image, QRectF((iw-sw)/2, max(0.,(ih-sh)/3), sw, sh))
    else:
        from typography import overlay_font
        pad = max(5., min(bounds.width(), bounds.height())*.10)
        box = bounds.adjusted(pad, pad, -pad, -pad)
        if media.kind == 'team':
            painter.fillRect(QRectF(box.left(), box.top(), box.width(), max(2.,bounds.height()*.02)), QColor(media.accent))
            box.adjust(0, 5, 0, 0)
        painter.setPen(QColor('#f6f8f8') if media.kind == 'team' else QColor('#b9c4c8'))
        flags = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter | (Qt.TextFlag.TextSingleLine if len(media.label)<=4 else Qt.TextFlag.TextWrapAnywhere)
        font = overlay_font(min(30,max(12,int(bounds.width()/4))),True)
        while font.pixelSize() > 1:
            measured = QFontMetricsF(font).boundingRect(QRectF(0,0,box.width(),10000),int(flags),media.label)
            if measured.width()<=box.width() and measured.height()<=box.height():break
            font.setPixelSize(font.pixelSize()-1)
        painter.setFont(font)
        painter.drawText(box,int(flags),media.label)
    painter.restore()
    return media


def identity_preview_image(model, profile, width=80, height=100, team=False):
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:paint_identity_media(painter, model, profile, QRectF(0,0,width,height), team=team)
    finally:painter.end()
    return image
