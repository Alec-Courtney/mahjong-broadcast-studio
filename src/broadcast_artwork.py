"""Native broadcast panels drawn from the P01/P10/P14/P15 reference frames.

Optional supplied portraits are embedded in SVG; other elements are vectors.
No fictional history is drawn.
"""
from __future__ import annotations

import math
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QLinearGradient,
                          QPainter, QPainterPath, QPen, QTextLayout, QTextOption)
import artwork
from typography import overlay_font,overlay_scope
from panel_typography import draw_single_line
from i18n import tr
from qualification_presentation import qualification_panel_size, FOUR_PANEL_SIZE, MT_CARD_WIDTH, MT_CARD_GAP
from table_score_strip import SCORE_STRIP_SIZE, draw_table_score_strip
from identity_media import paint_identity_media
from chart_effects import chart_y, line_gradient
from corner_header import CORNER_SIZE, draw_corner_header
from commentary_style import commentary_style_options
from player_layout import PLAYER_LAYOUT

EXTRA_SIZES = {
    'qualification': (400, 650), 'four_conditions': (1920, 1080), 'commentary': (1100, 132),
    'standings': (1840, 160), 'statistics': (1600, 800), 'chart': (1600, 900),
    'table_scores': SCORE_STRIP_SIZE,
    'corner_header': CORNER_SIZE,
}
WHITE = '#f6f8f8'
MUTED = '#bac5cb'
GREEN = '#00542f'
GOLD = '#efd48e'


def _config(model):
    return model.state.get('broadcast', {}).get('config', {})


def _num(value, default=0.):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _point(value, plus=False, decimals=1):
    if value is None:
        return '—'
    v = _num(value)
    return (f'{v:+,.{decimals}f}' if plus else f'{v:,.{decimals}f}').replace('-', '−')


def _font(size, bold=False):
    return overlay_font(size,bold,artwork.FONT)


def _text(p, value, x, y, w, h, size=26, fill=WHITE, align=Qt.AlignmentFlag.AlignLeft,
          bold=False, wrap=False, minimum=14, literal=False):
    """Bounded text that handles Chinese and unusually long team names."""
    value = tr(str(value), literal=literal)
    flags = align | Qt.AlignmentFlag.AlignVCenter
    if wrap:
        flags |= Qt.TextFlag.TextWordWrap
    f = _font(size, bold)
    if not wrap:
        p.save();p.setPen(QColor(fill))
        placement = draw_single_line(p, value, QRectF(x, y, w, h), f, align, minimum)
        p.restore()
        return placement
    while f.pixelSize() > minimum:
        fm = QFontMetricsF(f)
        bounds = fm.boundingRect(QRectF(0, 0, w, 10000), int(flags), value)
        if bounds.height() <= h and bounds.width() <= w and (wrap or fm.horizontalAdvance(value) <= w):
            break
        f.setPixelSize(f.pixelSize() - 1)
    fm = QFontMetricsF(f)
    if not wrap:
        value = fm.elidedText(value, Qt.TextElideMode.ElideRight, max(0, w))
    else:
        bounds = fm.boundingRect(QRectF(0, 0, w, 10000), int(flags), value)
        if bounds.height() > h or bounds.width() > w:
            low, high = 0, len(value)
            while low < high:
                mid = (low+high+1)//2
                trial = fm.boundingRect(QRectF(0, 0, w, 10000), int(flags), value[:mid]+'…')
                if trial.height() <= h and trial.width() <= w:
                    low = mid
                else:
                    high = mid-1
            value = value[:low]+'…'
    p.save()
    p.setClipRect(QRectF(x, y, w, h))
    p.setFont(f)
    p.setPen(QColor(fill))
    p.drawText(QRectF(x, y, w, h), int(flags), value)
    p.restore()


def _rect(p, x, y, w, h, fill):
    p.fillRect(QRectF(x, y, w, h), QColor(fill))


def _line(p, x1, y1, x2, y2, fill, width=1):
    p.setPen(QPen(QColor(fill), width))
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _accent(value):
    c = QColor(value or '#4da5b1')
    if not c.isValid():
        c = QColor('#4da5b1')
    if c.lightnessF() < .42:
        c = c.lighter(145)
    return c.name()


def _profile(model, player_id, name=''):
    for profile in model.state.get('players', []):
        if (player_id is not None and profile.get('id') == player_id) or (player_id is None and name and profile.get('name') == name):
            return profile
    roster = model.state.get('broadcast', {}).get('roster', [])
    if isinstance(roster, dict):
        roster = roster.values()
    for profile in roster:
        if isinstance(profile, dict) and profile.get('id') == player_id:
            return profile
    return {}


def _identity(p, model, row, x, y, w, h, show_photo=True, footer=False):
    """Team-colour card with a portrait on the right, as in P01/P14."""
    accent = QColor(_accent(row.get('color')))
    gradient = QLinearGradient(x, y, x + w, y)
    gradient.setColorAt(0, accent)
    gradient.setColorAt(.68, accent.darker(108))
    gradient.setColorAt(1, QColor('#5210171b'))
    p.fillRect(QRectF(x, y, w, h), gradient)
    profile = _profile(model, row.get('id'), row.get('name', ''))
    photo_w = (min(w*.47, h*1.38) if w >= 400 else min(w*.38, h*.78)) if show_photo else 0
    if show_photo:
        dest = QRectF(x+w-photo_w, y, photo_w, h)
        paint_identity_media(p, model, {**profile, **row}, dest, padding=0)
    text_w = w-photo_w-40
    _text(p, row.get('name', ''), x+20, y+h*.17, text_w, h*.36,
          38 if h >= 140 else 26, bold=True, literal=True)
    team = row.get('team') or profile.get('team', '')
    if team:
        multiline = QFontMetricsF(_font(23)).horizontalAdvance(team) > text_w
        _text(p, team, x+20, y+h*.53, text_w, h*.33, 23, wrap=multiline, minimum=14, literal=True)


def _header(p, model, width, title, detail=''):
    _rect(p, 0, 0, width, 98, '#ed003c23')
    _text(p, title, 30, 0, width*.40, 98, 39, bold=True)
    _text(p, detail, width*.40, 0, width*.60-30, 98, 34,
          align=Qt.AlignmentFlag.AlignRight)


def _empty(p, w, h, message='暂无已记录对局'):
    _text(p, message, 40, h*.38, w-80, 80, 32, MUTED,
          Qt.AlignmentFlag.AlignCenter)


def _commentary_text_layout(value, font, width):
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout = QTextLayout(value, font)
    layout.setTextOption(option)
    layout.beginLayout()
    height = 0.
    while True:
        line = layout.createLine()
        if not line.isValid():break
        line.setLineWidth(max(1., width))
        line.setPosition(QPointF(0, height))
        height += line.height()
    layout.endLayout()
    return layout, height


def _commentary_font(style, role):
    size = style[f'commentary_{role}_font_size']
    font = _font(size, role == 'title')
    family = style[f'commentary_{role}_font_family'].strip()
    if family:
        font.setFamily(family)
    spacing = style[f'commentary_{role}_letter_spacing']
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing * font.pixelSize() / size)
    return font


def commentary_layout(model, width=1100):
    """Reflow the complete definition at its native broadcast font size."""
    width = max(1, round(width))
    c = _config(model)
    builtin = c.get('commentary_builtin', False)
    title = tr(str(c.get('commentary_title', '')).strip(), literal=not builtin)
    body = tr(str(c.get('commentary_body', '')).strip(), literal=not builtin)
    title, body = ' '.join(title.split()), ' '.join(body.split())
    style = commentary_style_options(c)
    title_font = _commentary_font(style, 'title')
    body_font = _commentary_font(style, 'body')
    title_width = min(144., max(56., QFontMetricsF(title_font).horizontalAdvance(title)+2), max(1., width*.3))
    divider = 18+title_width+16
    top = 24 if c.get('show_match_title', True) else 0
    body_width = max(1., width-divider-36)
    title_layout, title_height = _commentary_text_layout(title, title_font, title_width)
    body_layout, body_height = _commentary_text_layout(body, body_font, body_width)
    text_height = max(68., title_height, body_height)
    title_rect = QRectF(18, top+20, title_width, text_height)
    body_rect = QRectF(divider+18, top+20, body_width, text_height)
    return dict(title=title, body=body, top=top, divider=divider, width=width,
                height=math.ceil(top+40+text_height), panel_height=40+text_height,
                title_rect=title_rect, body_rect=body_rect,
                title_font=title_font, body_font=body_font,
                title_layout=title_layout, title_height=title_height,
                body_layout=body_layout, body_height=body_height, body_scale=1.,
                body_lines=body_layout.lineCount(), wrapped=body_layout.lineCount() > 1)


def draw_commentary(p, model, frame=None, layer='all', width=1100, *, layout=None):
    """Keep static pixels; animate only measured background/rule/text tracks."""
    if layer not in ('all','background','divider','text'):
        raise ValueError('未知的术语绘制图层')
    if layout is None:
        layout = commentary_layout(model, width)
    if not layout['title'] and not layout['body']:
        return
    top = layout['top']
    if frame is not None:
        if frame.width<=0:return
        p.save()
        if frame.width<1 or frame.text_offset_y<0:
            p.setClipRect(QRectF(0,top,layout['width']*frame.width,layout['panel_height']),Qt.ClipOperation.IntersectClip)
    if layer in ('all','background'):
        _rect(p, 0, top, layout['width'], layout['panel_height'], '#da080a0c')
    if layer in ('all','divider') and (frame is None or (frame.divider_height>0 and frame.divider_alpha>0)):
        if frame is None:
            _line(p, layout['divider'], top+20, layout['divider'], layout['height']-20, '#ecffffff', 2)
        else:
            p.save();p.setOpacity(p.opacity()*frame.divider_alpha)
            half=layout['body_rect'].height()/2*frame.divider_height
            center=top+layout['panel_height']/2
            _line(p,layout['divider'],center-half,layout['divider'],center+half,'#ecffffff',2)
            p.restore()
    if layer not in ('all','text') or (frame is not None and frame.text_alpha<=0):
        if frame is not None:p.restore()
        return
    p.save()
    if frame is not None:
        p.setOpacity(p.opacity()*frame.text_alpha)
        p.translate(0,frame.text_offset_y)
    p.setPen(QColor(WHITE))
    title_rect = layout['title_rect']
    layout['title_layout'].draw(p, QPointF(title_rect.x(), title_rect.y()+(title_rect.height()-layout['title_height'])/2))
    body_rect = layout['body_rect']
    p.translate(body_rect.x(), body_rect.y()+(body_rect.height()-layout['body_height'])/2)
    p.scale(layout['body_scale'], layout['body_scale'])
    layout['body_layout'].draw(p, QPointF(0, 0))
    p.restore()
    if frame is not None:p.restore()


def _arrow(p, previous, current, x, y, width=22):
    if not previous or not current or previous == current:
        _text(p, '−', x, y-13, width, 28, 24, '#8a979e', Qt.AlignmentFlag.AlignCenter)
        return
    up = int(previous) > int(current)
    col = '#48e15b' if up else '#ed4556'
    points = [(0, 6), (width/2, -4), (width, 6)] if up else [(0, -4), (width/2, 6), (width, -4)]
    p.setPen(QPen(QColor(col), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(x+points[0][0], y+points[0][1]), QPointF(x+points[1][0], y+points[1][1]))
    p.drawLine(QPointF(x+points[1][0], y+points[1][1]), QPointF(x+points[2][0], y+points[2][1]))


def _standing_label(p, value, x, y, w, h, size=26, bold=False, literal=False, align=Qt.AlignmentFlag.AlignLeft):
    # White live-broadcast lettering has a small dark shadow, not a title panel.
    _text(p, value, x+1, y+2, w, h, size, '#c0000000', align, bold=bold, literal=literal)
    _text(p, value, x, y, w, h, size, WHITE, align, bold=bold, literal=literal)


def _standings_default_layout():
    # Original card envelopes are 406 x 184, with 64 px between adjacent cards.
    factor = 1840/(4*406+3*64)
    return [QRectF(i*470*factor, 0, 406*factor, 160) for i in range(4)]


def _standing_photo(p, model, row, mode):
    """Shared photo, team crest or wordmark in the fixed identity envelope."""
    profile = _profile(model, row.get('id'), row.get('name', '')) if mode == 'individual' else {}
    area = QRectF(10, 10, 132, 196)
    paint_identity_media(p, model, {**profile, **row} if mode == 'individual' else row,
                         area, team=mode != 'individual', radius=15)
    return profile


def _standing_card(p, model, row, mode):
    """Only this seat's all-event rank and points replace its original contents."""
    if getattr(model, 'settings', {}).get('theme') == 'saikouisen':
        from legacy_player_artwork import draw_standing_card
        return draw_standing_card(p, model, row, mode)
    profile = _profile(model, row.get('id'), row.get('name', '')) if mode == 'individual' else {}
    label = (row.get('team') or profile.get('team', '')) if mode == 'individual' else '队伍积分'
    artwork.paint_ml_card(p, row.get('name', ''), label,
                          _accent(row.get('color') or profile.get('team_color')),
                          _point(row.get('points'), True), row.get('rank', '—'),
                          x=PLAYER_LAYOUT.card_x-PLAYER_LAYOUT.envelope().x(),y=10,
                          width=PLAYER_LAYOUT.card_width,settings=getattr(model,'settings',{}))
    _standing_photo(p, model, row, mode)



def _draw_in_table_slots(p, rows, layout, draw, height=184, width=406):
    slots = [QRectF(rect) for rect in layout] if layout else _standings_default_layout()
    for index, row in enumerate(rows[:4]):
        seat = int(_num(row.get('seat'), index))
        if seat < 0 or seat >= len(slots):
            continue
        slot = slots[seat]
        p.save()
        p.translate(slot.x(), slot.y())
        p.scale(slot.width()/width, slot.height()/height)
        draw(row)
        p.restore()


def draw_standings(p, model, layout=None):
    """The fixed table seats show only the confirmed event standings."""
    from tournament import table_standings
    c = _config(model)
    mode = c.get('standings_mode', 'individual')
    rows = table_standings(model.state, mode=mode, live=False)
    if not rows:
        _standing_label(p, '尚未设置赛事名册', 20, 25, 1800, 110, 27,
                        align=Qt.AlignmentFlag.AlignCenter)
        return
    height = 184 if getattr(model, 'settings', {}).get('theme') == 'saikouisen' else 215
    width = 406 if getattr(model, 'settings', {}).get('theme') == 'saikouisen' else PLAYER_LAYOUT.envelope().width()
    _draw_in_table_slots(p, rows, layout, lambda row:_standing_card(p, model, row, mode), height, width)


def draw_settlement(p, model, summary, stage='hand', progress=1., layout=None):
    """Native per-hand broadcast extension, independent of official broadcast claims."""
    if getattr(model, 'settings', {}).get('theme') == 'saikouisen':
        from legacy_player_artwork import draw_settlement as legacy_settlement
        return legacy_settlement(p, model, summary, stage, progress, layout)
    total = stage == 'total'
    rows = summary.get('standings' if total else 'players', [])
    mode = 'team' if total and summary.get('mode', 'team') == 'team' else 'individual'
    amount = max(0., min(1., _num(progress, 1.)))
    def draw(row):
        identity = dict(row)
        if not total:
            identity.update(id=row.get('player_id', row.get('id')), name=row.get('player_name', row.get('name', '')))
        profile = _profile(model, identity.get('id'), identity.get('name', '')) if mode == 'individual' else {}
        before = _num(row.get('points_before', row.get('before_points')))
        after = _num(row.get('points_after', row.get('points')))
        value = _point(before+(after-before)*amount, True)
        rank = row.get('rank_after', row.get('rank', '—'))
        team = identity.get('team') or profile.get('team', '') if mode == 'individual' else '队伍总积分'
        artwork.paint_ml_card(p, identity.get('name', ''), team,
                              _accent(identity.get('color') or profile.get('team_color')),
                              value, rank,x=PLAYER_LAYOUT.card_x-PLAYER_LAYOUT.envelope().x(),y=10,
                              width=PLAYER_LAYOUT.card_width,settings=getattr(model,'settings',{}))
        _standing_photo(p, model, identity, mode)
    _draw_in_table_slots(p, rows, layout, draw, 215,PLAYER_LAYOUT.envelope().width())



def _condition_translation(value, model):
    # Preserve entered player names inside the model's generated condition text.
    names = sorted({str(q.get('name', '')) for q in model.state.get('players', [])
                    if q.get('name')}, key=len, reverse=True)
    markers = {}
    for i, name in enumerate(names):
        marker = '{participant' + str(i) + '}'
        if name in value:
            value = value.replace(name, marker)
            markers[marker] = name
    value = tr(value)
    for marker, name in markers.items():
        value = value.replace(marker, name)
    return value


def _compact_scenarios(scenarios):
    """Display the minimum successful legal payment with MT's threshold suffix."""
    accepted = [q for q in scenarios if q.get('qualifies', True)]
    if not accepted:
        return tr('无满足档位')
    minimum = min(accepted, key=lambda q:(q.get('total', q.get('base', 0)), q.get('text', '')))
    from qualification_presentation import condition_label
    return condition_label(minimum.get('text', ''))


def _focused_qualification(p, model, row):
    _rect(p, 12, 109, 376, 80, '#de17242a')
    _rect(p, 12, 109, 5, 80, _accent(row.get('color')))
    _text(p, row.get('name', ''), 27, 114, 343, 39, 31, bold=True, literal=True)
    if row.get('manual'):
        _text(p, row.get('condition', ''), 23, 210, 354, 352, 30, wrap=True, minimum=19, literal=True)
        return
    scenarios = row.get('scenarios', [])
    if model.state.get('phase') != 'playing' and not scenarios:
        _text(p, row.get('condition') or '本局已结算', 23, 210, 354, 352,
              30, wrap=True, minimum=19)
        return
    groups = [('自摸', [q for q in scenarios if q.get('type') == 'tsumo'], None)]
    for other in row.get('ron', []):
        groups.append(('荣和', [q for q in scenarios if q.get('type') == 'ron' and q.get('from') == other.get('from')], other.get('name', '')))
    for i, (kind, entries, name) in enumerate(groups[:4]):
        y = 201+i*94
        _rect(p, 12, y, 376, 88, '#bd192228')
        label = tr(kind) + (' / '+name if name is not None else '')
        _text(p, label, 24, y+3, 349, 27, 21, GOLD, literal=True)
        _text(p, _compact_scenarios(entries), 24, y+32, 349, 50, 21, wrap=True, minimum=16, literal=True)


def _current_team_ids(model):
    """Use roster IDs, matching tournament._team_standings grouping semantics."""
    roster = {r.get('id'):r for r in model.state.get('broadcast', {}).get('roster', [])}
    ids = set()
    for player in model.state.get('players', []):
        row = roster.get(player.get('id'), player)
        ids.add(row.get('team_id') or row.get('team') or 'unassigned:'+str(row.get('id', player.get('id', ''))))
    return ids


def draw_team_sidebar(p, model, mode='team', rows=None, highlight_ids=None, layer='all'):
    """Static layout or independent background/highlight/foreground layers.

    Layer separation changes only the transition. A table-only field has no
    red rows and uses the same compact bounds in static and animated views.
    """
    from sidebar_paging import sidebar_page_data
    c = _config(model)
    data = sidebar_page_data(model, mode=mode, rows=rows, highlight_ids=highlight_ids)
    participants = data['highlight_ids']
    visible, pages, page = data['rows'], data['pages'], data['page']
    row_height, first_y = 52, 86
    background = layer in ('all', 'background')
    foreground = layer in ('all', 'foreground')
    if background:
        _rect(p, 0, 0, *data['panel_size'], '#b8081713' if mode == 'team' else '#f30b1115')
        if mode == 'team':_rect(p, 0, 0, 400, 86, '#df004c30')
    if foreground:
        if mode == 'team':
            event = getattr(model, 'event', None) or {}
            _text(p, event.get('name') or c.get('title', ''), 12, 0, 376, 25, 17, literal=True)
            _text(p, '队伍排名', 12, 25, 300, 24, 24, bold=True)
        else:
            _text(p, '个人排名', 17, 7, 292, 40, 27, bold=True)
        if pages>1:
            _text(p, f'{page+1}/{pages}', 315, 15, 67, 27, 16, MUTED, Qt.AlignmentFlag.AlignRight)
        _line(p, 16, 51, 384, 51, '#98a7af', 1)
        _text(p, '排名', 12, 56, 58, 23, 16, MUTED, Qt.AlignmentFlag.AlignCenter)
        _text(p, '队名' if mode == 'team' else '姓名', 68, 56, 178, 23, 16, MUTED)
        _text(p, '积分', 260, 56, 122, 23, 16, MUTED, Qt.AlignmentFlag.AlignRight)
    if not visible:
        if foreground:
            _text(p, '尚未设置赛事名册', 20, 107, 360, 70, 24, MUTED,
                  Qt.AlignmentFlag.AlignCenter, wrap=True)
        return
    for index, row in enumerate(visible):
        y = first_y+index*row_height
        playing = row.get('id') in participants
        if layer == 'all' or (layer == 'background' and not playing) or (layer == 'highlight' and playing):
            if mode == 'team':
                _rect(p, 0, y, 400, row_height, '#b7a6242a' if playing else '#bc101c19')
            else:
                _rect(p, 8, y, 384, row_height-3, '#b7a6242a' if playing else '#a8121b20')
        if not foreground:continue
        tone = WHITE if mode == 'team' or playing else '#bbc5ca'
        cell_height = row_height if mode == 'team' else row_height-3
        _text(p, row.get('rank', '—'), 16, y, 38, cell_height, 28, tone, Qt.AlignmentFlag.AlignCenter)
        names = ((getattr(model, 'event', None) or {}).get('team_names', {}).get(row.get('id')) if mode == 'team' else None)
        subtitle, display_name = names or ('', '')
        display_name = display_name or row.get('name', '')
        if subtitle:
            _text(p, subtitle, 68, y+1, 185, 18, 12, tone, minimum=1, literal=True)
            _text(p, display_name, 68, y+18, 185, 32, 22, tone, bold=True, minimum=1, literal=True)
        else:
            _text(p, display_name, 68, y, 178, cell_height, 23, tone, bold=playing, minimum=1, literal=True)
        _text(p, _point(row.get('points'), mode != 'team'), 256, y, 126, cell_height, 28, tone,
              Qt.AlignmentFlag.AlignRight, bold=playing)
    if mode == 'team' and foreground:
        _line(p, 57, first_y, 57, first_y+len(visible)*row_height, '#88bdd4c5', 1)


def _event_standings_rows(rows, highlighted, limit=20):
    """Show all small fields; large fields retain real ranks and explicit omitted runs."""
    if len(rows) <= limit:
        return list(rows)
    current = [index for index, row in enumerate(rows) if row.get('id') in highlighted]
    selected = {0, 1, len(rows)-1}
    for index in current:
        selected.update(range(max(0,index-1),min(len(rows),index+2)))
    # Four seated entrants plus their neighbours and endpoints use at most 20
    # rows including separators. Keep every entrant even for unusual inputs.
    def assemble(indices):
        result, previous = [], -1
        for index in sorted(indices):
            if previous >= 0 and index > previous+1:
                result.append(None)
            result.append(rows[index]);previous=index
        return result
    result = assemble(selected)
    if len(result) > limit:
        selected = {0, len(rows)-1, *current}
        result = assemble(selected)
    for index in range(len(rows)):
        candidate = assemble(selected | {index})
        if len(candidate) <= limit:
            selected.add(index);result=candidate
        if len(result) == limit:
            break
    return result


def draw_individual_settlement_standings(p, model, summary):
    from tournament import standings
    rows = summary.get('all_standings')
    if rows is None:
        rows = standings(model.state, mode='individual', live=False)
    highlighted = set(summary.get('highlight_ids') or [player.get('id') for player in model.state.get('players', [])])
    visible = _event_standings_rows(rows, highlighted)
    _rect(p, 0, 0, 400, 650, '#f30b1115')
    _text(p, '个人排名', 17, 0, 366, 49, 26, bold=True)
    _line(p, 16, 49, 384, 49, '#98a7af', 1)
    _text(p, '排名', 12, 53, 58, 23, 16, MUTED, Qt.AlignmentFlag.AlignCenter)
    _text(p, '姓名', 68, 53, 178, 23, 16, MUTED)
    _text(p, '积分', 260, 53, 122, 23, 16, MUTED, Qt.AlignmentFlag.AlignRight)
    if not visible:
        _empty(p, 400, 650, '尚未设置赛事名册')
        return
    row_height = min(52., 532/max(1,len(visible)))
    name_size = min(23, max(16, round(row_height*.60)))
    point_size = min(28, max(18, round(row_height*.67)))
    for index, row in enumerate(visible):
        y = 81+index*row_height
        if row is None:
            _text(p, '…', 15, y, 370, row_height-2, point_size, MUTED, Qt.AlignmentFlag.AlignCenter)
            continue
        active = row.get('id') in highlighted
        _rect(p, 8, y, 384, row_height-2, '#b7a6242a' if active else '#a8121b20')
        tone = WHITE if active else '#bbc5ca'
        _text(p, row.get('rank', '—'), 16, y, 38, row_height-2, point_size, tone, Qt.AlignmentFlag.AlignCenter)
        _text(p, row.get('name', ''), 68, y, 178, row_height-2, name_size, tone, bold=active, minimum=1, literal=True)
        _text(p, _point(row.get('points'), True), 256, y, 126, row_height-2, point_size, tone,
              Qt.AlignmentFlag.AlignRight, bold=active)


def _mt_role_font(size, width, height, values):
    """Use one common font for every complete caption in a four-card role."""
    from panel_typography import single_line_layout
    font = _font(size)
    for value in ([values] if isinstance(values, str) else values):
        font = single_line_layout(value, QRectF(0, 0, width-2, height),
                                  font, minimum=1, elide=False).font
    return font


def _mt_condition_lines(row):
    scenarios = row.get('scenarios', [])
    lines = [_compact_scenarios([entry for entry in scenarios if entry.get('type') == 'tsumo'])]
    for other in row.get('ron', [])[:3]:
        entries = [entry for entry in scenarios if entry.get('type') == 'ron'
                   and entry.get('from') == other.get('from')]
        lines.append(_condition_source_line(other.get('name', ''), _compact_scenarios(entries)))
    return lines


def _mt_text(p, value, x, y, width, height, font, *, literal=False, wrap=False):
    """Draw the complete text at the shared role size without elision."""
    value = tr(str(value), literal=literal)
    p.save();p.setPen(QColor(WHITE))
    if wrap:
        p.setFont(font);p.setClipRect(QRectF(x, y, width, height))
        p.drawText(QRectF(x, y, width, height),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap), value)
    else:
        draw_single_line(p, value, QRectF(x, y, width, height), font,
                         minimum=font.pixelSize(), elide=False)
    p.restore()


def _condition_source_line(name, caption):
    return name+'　'+caption


def _four_qualification_card(p, model, row, x, width, fonts):
    """MT's vertical hierarchy: title, name, tsumo group and three ron rows."""
    _rect(p, x, 0, width, FOUR_PANEL_SIZE[1], '#b405080a')
    config = _config(model)
    custom_title = bool(config.get('qualification_title', '').strip() or
                        config.get('qualification_titles', {}).get(row.get('id'), '').strip())
    _mt_text(p, row.get('title', '晋级条件'), x+21, 24, width-42, 45,
             fonts['title'], literal=custom_title)
    _line(p, x+21, 87, x+width-3, 87, row.get('color') or '#4da5b1', 3)
    _mt_text(p, row.get('name', ''), x+21, 106, width-42, 55,
             fonts['name'], literal=True)
    scenarios = row.get('scenarios', [])
    if row.get('manual') or (model.state.get('phase') != 'playing' and not scenarios):
        _mt_text(p, row.get('condition') or '暂无可用条件', x+21, 191, width-42, 350,
                 fonts['condition'], wrap=True, literal=True)
        return
    _mt_text(p, '自摸条件', x+48, 186, width-69, 38, fonts['section'])
    lines = _mt_condition_lines(row)
    _mt_text(p, lines[0], x+21, 237, width-42, 56,
             fonts['condition'], literal=True)
    _mt_text(p, '荣和条件', x+48, 318, width-69, 38, fonts['section'])
    for index, value in enumerate(lines[1:]):
        _mt_text(p, value, x+21, 370+index*60, width-42, 55,
                 fonts['condition'], literal=True)


def draw_four_qualification(p, model):
    from qualification_presentation import qualification_data
    data = qualification_data(model)
    rows = data.get('rows', [])
    if not rows:
        _rect(p, 0, 0, *FOUR_PANEL_SIZE, '#da080a0c')
        _empty(p, *FOUR_PANEL_SIZE, '请先设置晋级规则')
        return
    width, gap = MT_CARD_WIDTH, MT_CARD_GAP
    rows = rows[:4]
    config = _config(model)
    titles = [tr(row.get('title', '晋级条件'), literal=bool(
        config.get('qualification_title', '').strip() or
        config.get('qualification_titles', {}).get(row.get('id'), '').strip())) for row in rows]
    lines = [value for row in rows if not row.get('manual') for value in _mt_condition_lines(row)]
    fonts = dict(title=_mt_role_font(34, width-42, 45, titles),
                 name=_mt_role_font(39, width-42, 55, [row.get('name', '') for row in rows]),
                 section=_mt_role_font(26, width-69, 38, [tr('自摸条件'), tr('荣和条件')]),
                 condition=_mt_role_font(32, width-42, 55, lines))
    for index, row in enumerate(rows[:4]):
        _four_qualification_card(p, model, row, index*(width+gap), width, fonts)


def draw_fullscreen_conditions(p, model):
    p.save()
    try:
        p.translate(60, 204)
        draw_four_qualification(p, model)
    finally:
        p.restore()


def draw_qualification(p, model):
    sidebar_mode = _config(model).get('sidebar_mode', 'qualification')
    if sidebar_mode in ('team', 'individual'):
        return draw_team_sidebar(p, model, mode=sidebar_mode)
    from qualification_presentation import qualification_data
    data = qualification_data(model)
    rows = data.get('rows', [])[:4]
    focus = _config(model).get('qualification_focus', '')
    players=model.state.get('players',[])
    dealer=players[model.state.get('dealer',0)].get('id') if players else None
    fallback=next((r for r in rows if r.get('id')==dealer),rows[0] if rows else None)
    focused = next((r for r in rows if focus and r.get('id') == focus), fallback)
    _rect(p, 0, 0, 400, 650, '#f10b1115')
    _rect(p, 0, 0, 400, 60, '#ef004b2b')
    from qualification_presentation import qualification_titles, automatic_qualification_title
    titles = qualification_titles(model)
    title = titles.get(focused.get('id')) if focused is not None else (
        _config(model).get('qualification_title', '').strip() or automatic_qualification_title(model))
    _text(p, title, 20, 6, 360, 48, 32, bold=True, literal=True)
    if focused is not None:
        _focused_qualification(p, model, focused)
        return
    _empty(p, 400, 650, '请先设置晋级规则')


def _rate(value):
    return '—' if value is None else f'{_num(value)*100:.1f}%'


def draw_statistics(p, model):
    from tournament import statistics, progression, match_results
    c = _config(model)
    scope = c.get('statistics_scope', 'match')
    requested_result = c.get('statistics_view', 'rates') == 'result'
    result = requested_result and model.state.get('phase') == 'finished'
    if requested_result:
        scope = 'match'
    rows = statistics(model.state, scope=scope)
    progress = progression(model.state)
    match_rows = {r['id']: r for r in match_results(model.state)} if result else {}
    profiles = {q.get('id'): q for q in model.state.get('players', [])}
    if result:
        # Table results sort by this match's scores, not season totals.
        current_ids = set(profiles)
        rows = [r for r in rows if r.get('id') in current_ids]
        rows.sort(key=lambda r: match_rows.get(r['id'], {}).get('rank', 99))
    pages = max(1, math.ceil(len(rows)/4))
    page = max(0, int(_num(c.get('statistics_page', 0)))) % pages
    rows = rows[page*4:(page+1)*4]
    _rect(p, 0, 0, 1600, 800, '#9403080b')
    title = '本场结果' if result else ('本场技术统计' if scope == 'match' else '赛事累计统计')
    detail = f"总局数 {progress.get('hands', 0)}  ·  流局 {progress.get('draws', 0)}"
    _header(p, model, 1600, title, detail if scope == 'match' or result else f'{page+1}/{pages}')
    if result:
        columns = [('排名', 504, 120), ('最终点数', 624, 280), ('立直', 904, 224), ('和了', 1128, 224), ('放铳', 1352, 224)]
    else:
        columns = [('局数', 345, 105), ('副露率', 450, 180), ('立直率', 630, 180),
                   ('和了率', 810, 180), ('放铳率', 990, 180), ('平均和了', 1170, 190), ('平均铳点', 1360, 210)]
    for name, x, width in columns:
        _text(p, name, x, 108, width, 34, 23, WHITE, Qt.AlignmentFlag.AlignCenter)
    if not rows:
        _empty(p, 1600, 800)
        return
    for j, row in enumerate(rows):
        y = 150+j*160 if result else 158+j*140
        row_height = 150 if result else 132
        _rect(p, 24, y, 1552, row_height, '#9f03090d')
        _identity(p, model, row, 24, y, 320 if not result else 480, row_height)
        if result:
            profile = profiles.get(row.get('id'), {})
            match_row = match_rows.get(row.get('id'), {})
            values = [str(match_row.get('rank', j+1+page*4))+' 位', _point(match_row.get('score', profile.get('score')), decimals=0),
                      f"{row.get('riichi', 0)} 回", f"{row.get('wins', 0)} 回", f"{row.get('deal_ins', 0)} 回"]
            score_placement = None
            for k, ((_, x, width), val) in enumerate(zip(columns, values)):
                placement = _text(p, val, x+8, y+10, width-16, row_height-20, 49 if k == 1 else 44, align=Qt.AlignmentFlag.AlignCenter)
                if k == 1:score_placement = placement
            current = match_rows.get(row.get('id'), {})
            pt = current.get('points')
            if pt is not None:
                top = score_placement.ink_bounds.bottom()+8
                _text(p, '('+_point(pt, True)+')', 624, top, 280, max(1, y+row_height-6-top), 24,
                      align=Qt.AlignmentFlag.AlignCenter, minimum=10)
        else:
            cells = [(str(row.get('hands', 0)), None),
                     (_rate(row.get('call_rate')), f"{row.get('calls', 0)} 回"),
                     (_rate(row.get('riichi_rate')), f"{row.get('riichi', 0)} 回"),
                     (_rate(row.get('win_rate')), f"{row.get('wins', 0)} 回"),
                     (_rate(row.get('deal_in_rate')), f"{row.get('deal_ins', 0)} 回"),
                     (_point(row.get('average_win'), decimals=0), '点'),
                     (_point(row.get('average_deal_in'), decimals=0), '点')]
            for (_, x, width), (value, label) in zip(columns, cells):
                placement = _text(p, value, x, y+8, width, row_height-16, 34, align=Qt.AlignmentFlag.AlignCenter)
                if label:
                    top = placement.ink_bounds.bottom()+7
                    _text(p, label, x, top, width, max(1, y+row_height-7-top), 19, MUTED,
                          Qt.AlignmentFlag.AlignCenter, minimum=10)


def _chart_axis_labels(data, state=None):
    """Name the first node of each round; leave renchan node spacing intact.

    Hand metadata is authoritative. For older data without it, parse the stored
    round label. A recording that starts during honba must not imply a new round.
    """
    import re
    raw = data.get('labels', [])
    hands = (state or {}).get('broadcast', {}).get('hands', [])
    use_metadata = len(hands)+1 == len(raw)
    result, previous_round, seen_round = [], None, False
    pattern = re.compile(r'^\s*([东東南])\s*([1-4])\s*局(?:\s*(\d+)\s*本[场場])?\s*$')
    for index, original in enumerate(raw):
        round_index, honba = None, 0
        if use_metadata and index:
            record = hands[index-1]
            value = record.get('round_index')
            if isinstance(value, int) and not isinstance(value, bool) and 0<=value<8:
                round_index, honba = value, int(_num(record.get('honba'), 0))
        if round_index is None:
            found = pattern.fullmatch(str(original))
            if found:
                round_index = (0 if found[1] in ('东', '東') else 4)+int(found[2])-1
                honba = int(found[3] or 0)
        if round_index is None:
            result.append('')
            continue
        repeat = round_index == previous_round
        started_in_honba = not seen_round and honba>0
        result.append('' if repeat or started_in_honba else
                      ('东' if round_index<4 else '南')+str(round_index%4+1)+'局')
        previous_round, seen_round = round_index, True
    return result


def draw_chart(p, model, progress=1., flow_time=0.):
    from tournament import progression
    c = _config(model)
    data = progression(model.state)
    series = data.get('series', [])[:4]
    labels = data.get('labels', [])
    width, height = EXTRA_SIZES['chart']
    _rect(p, 0, 0, width, height, '#9403080b')
    title = '本场点数走势'
    _header(p, model, width, title,
            f"总局数 / {data.get('hands', 0)} 局   流局数 / {data.get('draws', 0)} 局")
    if not series or not labels:
        _empty(p, width, height)
        return
    plot = QRectF(124, 212, 948, 610)
    key = 'scores'
    values = [_num(value) for line in series for value in line.get(key, []) if value is not None]
    if not values:
        _empty(p, width, height)
        return
    raw_low, raw_high = min(values), max(values)
    if raw_high == raw_low:
        raw_low -= 5000
        raw_high += 5000
    # P14 labels the actual extrema and common opening score, rather than a
    # spreadsheet-like rounded scale with a horizontal line through every tick.
    low, high = raw_low, raw_high
    opening = [line.get(key, [None])[0] for line in series if line.get(key)]
    ticks = {low, high}
    if opening and all(value is not None and value == opening[0] for value in opening):
        ticks.add(_num(opening[0]))
    to_y = lambda value: chart_y(_num(value), low, high, plot.top(), plot.bottom())
    n = len(labels)
    to_x = lambda i: plot.left()+i*plot.width()/max(1, n-1)
    for value in sorted(ticks):
        y = to_y(value)
        label = _point(value, decimals=0)
        _text(p, label, 3, y-22, 110, 44, 27, WHITE, Qt.AlignmentFlag.AlignRight)
    axis_labels = _chart_axis_labels(data, model.state)
    for i, label in enumerate(axis_labels):
        x = to_x(i)
        _line(p, x, plot.top(), x, plot.bottom(), '#70878c90', 1.3)
        if label:
            _text(p, str(label), x-43, plot.bottom()+10, 86, 45, 19, WHITE,
                  Qt.AlignmentFlag.AlignCenter, wrap=True, minimum=13)
    _line(p, plot.left(), plot.top(), plot.left(), plot.bottom(), '#e5ecef', 3)
    _line(p, plot.left(), plot.bottom(), plot.right(), plot.bottom(), '#e5ecef', 3)
    reveal = max(0., min(1., _num(progress, 1.)))
    reveal_right = plot.left()+reveal*plot.width()
    legend = sorted(series, key=lambda q: -_num((q.get('scores') or [0])[-1]))
    phases = {id(line): index for index, line in enumerate(legend)}
    for line in series:
        values = line.get(key, [])
        path = QPainterPath()
        started = False
        last_point = None
        for i, value in enumerate(values[:n]):
            if value is None:
                started = False
                continue
            point = QPointF(to_x(i), to_y(value))
            if started:
                path.lineTo(point)
            else:
                path.moveTo(point)
            started = True
            last_point = point
        p.save()
        p.setClipRect(QRectF(plot.left()-5, plot.top()-5, max(0., reveal_right-plot.left())+(10 if reveal >= 1. else 5), plot.height()+10))
        accent = _accent(line.get('color'))
        p.setPen(QPen(line_gradient(accent, plot.left(), plot.width(), flow_time, phases[id(line)]), 4., Qt.PenStyle.SolidLine,
                     Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.restore()
    for i, line in enumerate(legend):
        y = 156+i*173
        _identity(p, model, line, 1116, y, 460, 164, footer=True)


_DRAWERS = {
    'qualification': draw_qualification, 'commentary': draw_commentary,
    'four_conditions': draw_fullscreen_conditions,
    'standings': draw_standings, 'statistics': draw_statistics, 'chart': draw_chart,
    'table_scores': draw_table_score_strip,
    'corner_header': draw_corner_header,
}


def draw_extension(painter, kind, model, layout=None, progress=1., settlement=None, settlement_stage=None, flow_time=0., canvas_size=None):
    with overlay_scope(getattr(model,'settings',{})):
        return _draw_extension(painter,kind,model,layout,progress,settlement,settlement_stage,flow_time,canvas_size)


def _draw_extension(painter, kind, model, layout=None, progress=1., settlement=None, settlement_stage=None, flow_time=0., canvas_size=None):
    if kind not in _DRAWERS:
        raise ValueError('未知转播模块：'+str(kind))
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    size = (400, 650) if kind == 'qualification' and settlement is not None and settlement_stage == 'total' else (
        qualification_panel_size(model) if kind == 'qualification' else EXTRA_SIZES[kind])
    if kind=='commentary':size=canvas_size or (size[0],commentary_layout(model,size[0])['height'])
    painter.setClipRect(QRectF(0, 0, *size))
    try:
        if kind == 'standings':
            if settlement is not None:
                draw_settlement(painter, model, settlement, settlement_stage or 'hand', progress, layout)
            else:
                draw_standings(painter, model, layout=layout)
        elif kind == 'chart':
            draw_chart(painter, model, progress=progress, flow_time=flow_time)
        elif kind == 'commentary':
            draw_commentary(painter, model, width=size[0])
        elif kind == 'qualification' and settlement is not None and settlement_stage == 'total':
            draw_individual_settlement_standings(painter, model, settlement)
        else:
            _DRAWERS[kind](painter, model)
    finally:
        painter.restore()

