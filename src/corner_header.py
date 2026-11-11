"""Editable, event-scoped upper-right league mark on a transparent canvas."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPen

from identity_media import load_identity_image
from panel_typography import draw_single_line
from live_badge import draw_live_badge, live_rect

CORNER_TITLE_SIZE = (540, 220)
CORNER_SIZE = (540, 302)
CORNER_DEFAULTS = {
    'corner_logo': '',
    'corner_season': '26–27',
    'corner_date': 'Oct. 29',
    'corner_match': '1',
    'corner_colour': '#ffffff',
    'corner_logo_scale': 1.,
    'corner_top_text': None,
    'corner_bottom_text': None,
    'corner_top_size': 96,
    'corner_bottom_size': 44,
    'corner_top_tracking': 2.,
    'corner_bottom_tracking': 1.2,
    'corner_logo_x': 0.,
    'corner_logo_y': 0.,
    'corner_live_hold': 7.,
    'corner_live_x': 350,
    'corner_live_y': 240,
    'corner_live_width': 168,
    'corner_live_height': 54,
}
CORNER_SETTING_KEYS = frozenset(CORNER_DEFAULTS)


def validate_corner_config(config):
    for key, limit in (('corner_logo', 4096), ('corner_season', 40),
                       ('corner_date', 40), ('corner_match', 12)):
        value = config.get(key, CORNER_DEFAULTS[key])
        if not isinstance(value, str) or len(value)>limit or any(c in value for c in '\r\n\0'):
            raise ValueError('右上角标识文字或图片路径格式无效')
    for key in ('corner_top_text','corner_bottom_text'):
        value=config.get(key)
        if value is not None and (not isinstance(value,str) or len(value)>80 or any(char in value for char in '\r\n\0')):
            raise ValueError('赛事标识上下段文字须为80字以内的单行文字')
    colour = config.get('corner_colour', '#ffffff')
    if not isinstance(colour, str) or not QColor(colour).isValid():
        raise ValueError('右上角标识颜色无效')
    scale = config.get('corner_logo_scale', 1.)
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or not .5<=scale<=1.25:
        raise ValueError('右上角 Logo 比例须为 50–125%')
    for key,low,high in (('corner_top_size',12,128),('corner_bottom_size',12,72),
                         ('corner_top_tracking',-4,20),('corner_bottom_tracking',-4,20),
                         ('corner_logo_x',-80,80),('corner_logo_y',-80,80),
                         ('corner_live_x',2,1080),('corner_live_y',3,1080),
                         ('corner_live_width',40,540),('corner_live_height',14,180)):
        value=config.get(key,CORNER_DEFAULTS[key])
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
            raise ValueError('赛事标识字号、字间距、Logo或LIVE版式超出范围')
    hold=config.get('corner_live_hold',7.)
    if isinstance(hold,bool) or not isinstance(hold,(int,float)) or not math.isfinite(hold) or not .5<=hold<=120:
        raise ValueError('LIVE 红底停留时间须为 0.5–120 秒')


def corner_options(config):
    result = {key:config.get(key, value) for key,value in CORNER_DEFAULTS.items()}
    validate_corner_config(result)
    return result


def corner_canvas_size(config):
    area=live_rect(config)
    return max(540,math.ceil(area.right()+8)),max(220,math.ceil(area.bottom()+8))


def corner_texts(config):
    """Old season/date/match documents remain readable until explicitly edited."""
    values=corner_options(config)
    top=values['corner_top_text']
    bottom=values['corner_bottom_text']
    if top is None:top=values['corner_season']
    if bottom is None:bottom=values['corner_date']
    return top,bottom


@lru_cache(maxsize=12)
def _visible_logo(image_key, raw):
    image = QImage.fromData(raw)
    if image.isNull():return None
    # Ignore transparent margins supplied by the image author. Keep the mark's
    # own proportions; a tall SL logo is contained rather than stretched.
    alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
    payload = bytes(alpha.constBits())
    stride = alpha.bytesPerLine()
    left, top, right, bottom = image.width(), image.height(), -1, -1
    for y in range(image.height()):
        row = payload[y*stride:y*stride+image.width()]
        hits = [x for x,value in enumerate(row) if value>8]
        if hits:
            left=min(left,hits[0]);right=max(right,hits[-1]);top=min(top,y);bottom=y
    return image.copy(left,top,right-left+1,bottom-top+1) if right>=left else None


def corner_logo(model, reference=''):
    if not reference:return None
    from media_store import resolve_media_path
    source = resolve_media_path(model.path,reference)
    image = load_identity_image(model, str(source))
    if image is None:return None
    try:
        stat=source.stat()
        return _visible_logo((str(source),stat.st_mtime_ns,stat.st_size),source.read_bytes())
    except OSError:
        return None


def _font(size, tracking=0.):
    font=QFont('Arial');font.setPixelSize(size);font.setWeight(QFont.Weight.Bold)
    font.setItalic(False);font.setStretch(100);font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing,tracking)
    return font


def draw_corner_header(painter, model, *, live=None, include_live=True):
    values=corner_options(model.state.get('broadcast',{}).get('config',{}))
    colour=QColor(values['corner_colour'])
    painter.save()
    try:
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform)
        painter.setPen(colour)
        image=corner_logo(model,values['corner_logo'])
        if image is not None:
            box=QRectF(12,10,176,200)
            factor=min(box.width()/image.width(),box.height()/image.height())*values['corner_logo_scale']
            # Larger user scales stay within the transparent window and leave
            # space before the season text, including very tall/wide marks.
            factor=min(factor,184/image.width(),216/image.height())
            width,height=image.width()*factor,image.height()*factor
            cx=min(192-width/2,max(4+width/2,box.center().x()+values['corner_logo_x']))
            cy=min(218-height/2,max(2+height/2,box.center().y()+values['corner_logo_y']))
            painter.drawImage(QRectF(cx-width/2,cy-height/2,width,height),image)
        # All text is fitted within its own box: a long season/date never
        # touches the logo or pushes the match number out of the window.
        top,bottom=corner_texts(values)
        season=QRectF(196,32,330,93)
        draw_single_line(painter,top,season,_font(round(values['corner_top_size']),values['corner_top_tracking']),Qt.AlignmentFlag.AlignLeft,16)
        painter.setPen(QPen(colour,4.0,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap));painter.drawLine(196,136,518,136)
        painter.setPen(colour)
        date_box=QRectF(204,151,214 if values['corner_match'] else 322,43)
        draw_single_line(painter,bottom,date_box,_font(round(values['corner_bottom_size']),values['corner_bottom_tracking']),Qt.AlignmentFlag.AlignHCenter,12)
        painter.setPen(QPen(colour,1.8));painter.drawLine(206,202,418,202)
        painter.setPen(colour)
        if values['corner_match']:
            draw_single_line(painter,'#',QRectF(428,144,26,30),_font(31),Qt.AlignmentFlag.AlignHCenter,12)
            draw_single_line(painter,values['corner_match'],QRectF(455,140,72,68),_font(78),Qt.AlignmentFlag.AlignLeft,12)
        if include_live:draw_live_badge(painter,live,live_rect(values))
    finally:
        painter.restore()
