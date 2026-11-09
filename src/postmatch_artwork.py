"""Layered 1080p post-match graphics based on the supplied ML video analysis."""
from __future__ import annotations

import copy
import math
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QImage, QLinearGradient,
                          QPainter, QPainterPath, QPen, QRadialGradient)

from broadcast_artwork import _accent, _chart_axis_labels, _num, _point, _profile, _text
from identity_media import contain_rect, identity_preview_image, paint_identity_media, resolve_identity_media
from postmatch_sequence import (CARD_HEIGHT, CARD_ROWS, CARD_WIDTH, CHART,
                                COLUMN_CENTRES, DATA_LEFT, DATA_WIDTH, HEADER, timeline)
from typography import overlay_scope
from chart_effects import chart_y, line_gradient
import tournament

SIZE = (1920, 1080)
WHITE = '#f6f8f8'
MUTED = '#c7cbd0'
_FROSTED_BACKGROUND = None


def prepare_postmatch(model):
    """Read the current four only; preserve real match counts and score history.

    The caller can keep this result on a frozen model as ``postmatch_data`` to
    avoid recomputing it each frame. No page is populated from reference clips.
    """
    state = model.state
    progress = copy.deepcopy(tournament.progression(state))
    profiles = {row['id']: row for row in state.get('players', [])}
    results = {row['id']: row for row in tournament.match_results(state)}
    counts = {row['id']: row for row in tournament.statistics(state, scope='match')}
    series = {row['id']: row for row in progress.get('series', [])}
    rows = []
    for seat, profile in enumerate(state.get('players', [])[:4]):
        identity = profile['id']
        result, stats = results.get(identity, {}), counts.get(identity, {})
        line = series.get(identity, {})
        row = {**copy.deepcopy(_profile(model, identity)), **copy.deepcopy(profile)}
        row.update(id=identity, name=profile.get('name', ''),
                   color=profile.get('team_color') or row.get('color') or line.get('color'),
                   score=result.get('score', profile.get('score')),
                   rank=result.get('rank', seat+1),
                   points=result.get('points') if state.get('phase') == 'finished' else None,
                   scores=copy.deepcopy(line.get('scores', [])),
                   riichi=stats.get('riichi'), wins=stats.get('wins'), deal_ins=stats.get('deal_ins'))
        rows.append(row)
    rows.sort(key=lambda row: (-_num(row.get('score')), _num(row.get('rank'), 99)))
    values = [_num(value) for row in rows for value in row['scores'] if value is not None]
    if not values:values = [_num(row.get('score')) for row in rows] or [0.]
    low, high = min(values), max(values)
    if low == high:low, high = low-5000., high+5000.
    ticks = {low, high}
    opening = [row['scores'][0] for row in rows if row['scores']]
    if len(opening) == len(rows) and opening and opening[0] is not None and all(value == opening[0] for value in opening):
        ticks.add(_num(opening[0]))
    return dict(rows=rows, labels=list(progress.get('labels', [])),
                axis_labels=_chart_axis_labels(progress, state),
                low=low, high=high, ticks=sorted(ticks),
                hands=progress.get('hands', 0), draws=progress.get('draws', 0),
                title=state.get('broadcast', {}).get('config', {}).get('title') or state.get('title') or '本场比赛')


def _image_layer(width, height, draw):
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:draw(painter)
    finally:painter.end()
    return image


def prepare_postmatch_data(model):
    """Freeze real data and prepare reusable text/media layers before playback."""
    data = prepare_postmatch(model)
    with overlay_scope(getattr(model, 'settings', {})):
        for row in data['rows']:
            row['_background_image'] = _card_background_image(model, row)
            row['_portrait_image'] = identity_preview_image(model, row, width=239, height=194)
            row['_names_image'] = _image_layer(int(CARD_WIDTH), int(CARD_HEIGHT),
                                               lambda painter: _card_names(painter, row, 0., 0.))
    return data


def prepare_postmatch_model(model):
    """Prepare a caller-owned frozen snapshot and retain its render cache."""
    model._postmatch_data = prepare_postmatch_data(model)
    return model


def _alpha(p, opacity):
    p.setOpacity(p.opacity()*max(0., min(1., opacity)))


def _studio_background():
    """An original, defocused studio/table impression without captured content.

    Draw at low resolution and repeatedly smooth down/up so broad neutral wall,
    blue seating and a grey table remain recognizable only as low frequencies.
    The programme foreground stays dominant, as in the supplied blurred scene.
    """
    global _FROSTED_BACKGROUND
    if _FROSTED_BACKGROUND is not None:return _FROSTED_BACKGROUND
    image = QImage(192, 108, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor('#060b12'))
    p = QPainter(image);p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    # The far neutral set is interrupted by wide, very soft vertical shadows.
    p.fillRect(QRectF(60, 12, 124, 34), QColor('#686763'))
    p.fillRect(QRectF(68, 18, 96, 13), QColor('#8a8981'))
    for x, w in ((51, 14), (87, 9), (122, 8), (168, 11)):
        p.fillRect(QRectF(x, 2, w, 65), QColor('#070c13'))
    p.fillRect(QRectF(42, 31, 83, 18), QColor('#193a58'))
    p.fillRect(QRectF(137, 32, 52, 23), QColor('#233e52'))
    p.fillRect(QRectF(70, 37, 54, 11), QColor('#0b1c2b'))
    # Table surface and its dark apron recede through the middle of the frame.
    path = QPainterPath();path.moveTo(51, 57);path.lineTo(143, 51)
    path.lineTo(190, 74);path.lineTo(77, 87);path.closeSubpath()
    p.fillPath(path, QColor('#68716f'))
    path = QPainterPath();path.moveTo(77, 87);path.lineTo(190, 74)
    path.lineTo(188, 91);path.lineTo(87, 101);path.closeSubpath()
    p.fillPath(path, QColor('#172531'))
    p.fillRect(QRectF(99, 64, 44, 11), QColor('#454d4e'))
    # Foreground chair/rail forms frame the scene without sharp silhouettes.
    p.setBrush(QColor('#04090f'));p.drawEllipse(QRectF(-36, 23, 104, 110))
    p.fillRect(QRectF(5, 81, 100, 16), QColor('#0c1d2c'))
    p.fillRect(QRectF(87, 94, 35, 16), QColor('#656664'))
    p.end()
    for width, height in ((72, 41), (144, 82), (48, 27), (192, 108)):
        image = image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    result = image.scaled(*SIZE, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    p = QPainter(result)
    p.fillRect(QRectF(0, 0, *SIZE), QColor('#5e01050a'))
    vignette = QRadialGradient(QPointF(1060, 530), 1160)
    vignette.setColorAt(0, QColor('#00000000'));vignette.setColorAt(.6, QColor('#10000000'));vignette.setColorAt(1, QColor('#b8000205'))
    p.fillRect(QRectF(0, 0, *SIZE), vignette);p.end()
    _FROSTED_BACKGROUND = result
    return result


def _background(p, opacity, background):
    p.save();_alpha(p, opacity)
    if isinstance(background, QImage) and not background.isNull():
        ratio = 16/9
        width, height = background.width(), background.height()
        sw, sh = (height*ratio, height) if width/height > ratio else (width, width/ratio)
        p.drawImage(QRectF(0, 0, *SIZE), background, QRectF((width-sw)/2, (height-sh)/2, sw, sh))
        p.fillRect(QRectF(0, 0, *SIZE), QColor('#6002070a'))
    else:
        p.drawImage(QPointF(0, 0), _studio_background())
    p.restore()


def _header(p, data, frame):
    x, y, width, height = HEADER
    p.save();p.setClipRect(QRectF(x, y, width*frame['header_bar'], height), Qt.ClipOperation.IntersectClip)
    gradient = QLinearGradient(x, y, x+width, y)
    gradient.setColorAt(0, QColor('#f3003c23'));gradient.setColorAt(1, QColor('#de003a22'))
    p.fillRect(QRectF(x, y, width, height), gradient);p.restore()
    p.save();_alpha(p, frame['header_text'])
    _text(p, data['title'], x+25, y+8, 850, height-16, 43, bold=True, literal=True)
    _text(p, f"总局数 / {data['hands']} 局    流局数 / {data['draws']} 局", x+900, y+8, width-925, height-16, 36,
          align=Qt.AlignmentFlag.AlignRight)
    p.restore()


def _chart(p, model, data, frame):
    if frame['grid_opacity'] <= 0 and frame['axes_opacity'] <= 0 and all(mask['to'] <= mask['from'] for mask in frame['lines']):
        return
    x, y, width, height = CHART
    bottom = y+height
    labels = data['labels']
    n = max(1, len(labels)-1)
    to_x = lambda index: x+index*width/n
    to_y = lambda score: chart_y(_num(score), data['low'], data['high'], y, bottom)
    p.save();_alpha(p, frame['grid_opacity'])
    p.setPen(QPen(QColor('#6e9aa0a5'), 1.7))
    for index in range(len(labels)):
        xx = to_x(index);p.drawLine(QPointF(xx, y), QPointF(xx, bottom))
    for score in data['ticks']:
        _text(p, _point(score, decimals=0), 58, to_y(score)-26, 177, 52, 32, WHITE, Qt.AlignmentFlag.AlignRight)
    for index, label in enumerate(data['axis_labels']):
        if label:_text(p, label, to_x(index)-48, bottom+12, 96, 50, 23, WHITE, Qt.AlignmentFlag.AlignCenter, minimum=13)
    p.restore()
    p.save();_alpha(p, frame['axes_opacity'])
    axes = frame['axes_progress'];p.setPen(QPen(QColor('#f2f4f3'), 3.2))
    p.drawLine(QPointF(x, bottom), QPointF(x+width*axes, bottom))
    p.drawLine(QPointF(x, bottom), QPointF(x, bottom-height*axes));p.restore()
    for index, row in enumerate(data['rows']):
        mask = frame['lines'][index]
        if mask['to'] <= mask['from']:continue
        path, started = QPainterPath(), False
        for j, score in enumerate(row['scores'][:len(labels)]):
            if score is None:started = False;continue
            point = QPointF(to_x(j), to_y(score))
            if started:path.lineTo(point)
            else:path.moveTo(point)
            started = True
        p.save()
        p.setClipRect(QRectF(x+width*mask['from']-3, y-6, width*(mask['to']-mask['from'])+6, height+12), Qt.ClipOperation.IntersectClip)
        p.setPen(QPen(line_gradient(_accent(row.get('color')), x, width, frame['time'], index), 4.6,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush);p.drawPath(path);p.restore()


def _logo_stamp(logo, ink):
    """Tint logo detail while retaining transparency and lifting a flat canvas."""
    stamp = logo.scaled(440, 268, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = stamp.bits()
    right = (stamp.width()-1)*4
    bottom = (stamp.height()-1)*stamp.bytesPerLine()
    corners = [pixels[offset:offset+4].tolist() for offset in (0, right, bottom, bottom+right)]
    background = corners[0][:3]
    flat_canvas = all(corner[3] >= 250 and max(abs(a-b) for a,b in zip(corner[:3], background)) <= 16
                      for corner in corners)
    ink_rgb = bytes((ink.red(), ink.green(), ink.blue()))
    for offset in range(0, len(pixels), 4):
        red, green, blue, alpha = pixels[offset:offset+4]
        if not alpha:
            continue
        if flat_canvas:
            distance = max(abs(red-background[0]), abs(green-background[1]), abs(blue-background[2]))
            alpha = alpha * min(255, max(0, (distance-14)*3)) // 255
        luminance = (77*red + 150*green + 29*blue) >> 8
        pixels[offset:offset+3] = ink_rgb
        pixels[offset+3] = alpha * (165 + 90*luminance//255) // 255
    return stamp


def _card_background_image(model, row):
    width, height = int(CARD_WIDTH), int(CARD_HEIGHT)
    bounds = QRectF(0, 0, width, height)
    hue, saturation, value, _ = QColor(_accent(row.get('color'))).getHsvF()
    mid_hue, light_hue = (hue+.033) % 1., (hue+.065) % 1.
    value = min(.72, max(.34, value))
    gradient = QLinearGradient(0, 0, width, 0)
    gradient.setColorAt(0, QColor.fromHsvF(hue, saturation*.95, value*.70))
    gradient.setColorAt(.54, QColor.fromHsvF(mid_hue, saturation*.94, value*.86))
    gradient.setColorAt(.83, QColor.fromHsvF(light_hue, saturation*.90, value*1.10))

    logo = resolve_identity_media(model, row, team=True).image

    def paint(p):
        p.fillRect(bounds, gradient)
        if logo is not None:
            ink = QColor.fromHsvF(mid_hue, saturation*.42, 1.)
            stamp = _logo_stamp(logo, ink)
            area = QRectF(width*.06, -height*.19, width*.86, height*1.38)
            p.setOpacity(.19)
            p.drawImage(contain_rect(stamp, area, padding=0), stamp)
            p.setOpacity(1.)
        fade = QLinearGradient(0, 0, width, 0)
        fade.setColorAt(.81, Qt.GlobalColor.white)
        fade.setColorAt(1, Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        p.fillRect(bounds, fade)

    return _image_layer(width, height, paint)


def _card_names(p, row, x, y):
    _text(p, row.get('name', ''), x+22, y+29, 248, 68, 43, bold=True, minimum=20, literal=True)
    if row.get('team'):
        _text(p, row['team'], x+23, y+105, 234, 66, 25, wrap=True, minimum=15, literal=True)


def _card(p, model, row, frame):
    x, y, width, height = frame['x'], frame['y'], frame['width'], frame['height']
    p.save();_alpha(p, frame['background_opacity'])
    background = row.get('_background_image')
    if background is None:
        background = _card_background_image(model, row)
    p.drawImage(QPointF(x+frame['background_offset'], y), background);p.restore()
    p.save();_alpha(p, frame['portrait_opacity'])
    if '_portrait_image' in row:p.drawImage(QPointF(x+width-239, y), row['_portrait_image'])
    else:paint_identity_media(p, model, row, QRectF(x+width-239, y, 239, height))
    p.restore()
    p.save();_alpha(p, frame['name_opacity'])
    if '_names_image' in row:p.drawImage(QPointF(x, y), row['_names_image'])
    else:_card_names(p, row, x, y)
    p.restore()


def _parallelogram(p, x, y, width, height, color):
    skew = math.tan(math.radians(14))*height
    path = QPainterPath();path.moveTo(x+skew, y);path.lineTo(x+width+skew, y)
    path.lineTo(x+width, y+height);path.lineTo(x, y+height);path.closeSubpath()
    p.fillPath(path, color)


def _table(p, data, frame):
    if frame['headers_opacity'] <= 0 and all(row['values_opacity'] <= 0 and row['sweep_opacity'] <= 0 for row in frame['rows']):
        return
    headers = ('名次', '最终点数', '立直次数', '和了次数', '放铳次数')
    p.save();_alpha(p, frame['headers_opacity'])
    for center, name in zip(COLUMN_CENTRES, headers):
        _text(p, name, center-115, 172, 230, 37, 27, align=Qt.AlignmentFlag.AlignCenter)
    p.restore()
    widths = (130., 300., 220., 220., 220.)
    for index, row in enumerate(data['rows']):
        y, track = CARD_ROWS[index], frame['rows'][index]
        p.save();p.setClipRect(QRectF(DATA_LEFT, y, DATA_WIDTH, CARD_HEIGHT), Qt.ClipOperation.IntersectClip)
        p.save();_alpha(p, track['values_opacity'])
        p.fillRect(QRectF(DATA_LEFT, y, DATA_WIDTH, CARD_HEIGHT), QColor('#70020709'));p.restore()
        if track['sweep_opacity'] > 0:
            accent = QColor(_accent(row.get('color')))
            p.save();_alpha(p, track['sweep_opacity']*.35)
            broad_width = DATA_WIDTH*.70
            _parallelogram(p, DATA_LEFT-broad_width+(DATA_WIDTH+broad_width)*track['sweep_progress'], y, broad_width, CARD_HEIGHT, accent)
            p.restore();p.save();_alpha(p, track['sweep_opacity']*.85)
            thin_width = DATA_WIDTH*.025
            _parallelogram(p, DATA_LEFT-thin_width+(DATA_WIDTH+thin_width)*track['sweep_accent_progress'], y, thin_width, CARD_HEIGHT, accent)
            p.restore()
        p.save();_alpha(p, track['values_opacity'])
        values = [str(row['rank'])+' 位', _point(row.get('score'), decimals=0)]
        values += [('—' if row.get(key) is None else str(row[key])+' 回') for key in ('riichi', 'wins', 'deal_ins')]
        score_placement = None
        for column, (center, width, value) in enumerate(zip(COLUMN_CENTRES, widths, values)):
            placement = _text(p, value, center-width/2, y+10, width, CARD_HEIGHT-20, 65 if column == 1 else 59, align=Qt.AlignmentFlag.AlignCenter, minimum=27)
            if column == 1:score_placement = placement
        if row.get('points') is not None and score_placement is not None:
            # The main numeral stays on the portrait/row midline. Its pt caption
            # occupies only the remaining space underneath and cannot move it.
            top = score_placement.ink_bounds.bottom()+8
            _text(p, '('+_point(row['points'], True)+')', COLUMN_CENTRES[1]-150, top, 300,
                  max(1., y+CARD_HEIGHT-6-top), 36, align=Qt.AlignmentFlag.AlignCenter, minimum=10)
        p.restore();p.restore()


def draw_postmatch(p, model, elapsed, chart_hold=15., stats_hold=15., background=None):
    """Draw on the caller's painter; data and media roots stay on its snapshot.

    ``background`` may be a prepared, dim/defocused QImage. No window, audio,
    wall clock, animation timers or changes to business state are created here.
    """
    data = getattr(model, '_postmatch_data', None) or getattr(model, 'postmatch_data', None) or prepare_postmatch_data(model)
    frame = timeline(elapsed, chart_hold, stats_hold)
    p.save()
    try:
        with overlay_scope(getattr(model, 'settings', {})):
            _background(p, frame['background_opacity'], background)
            _header(p, data, frame)
            _chart(p, model, data, frame)
            _table(p, data, frame)
            for row, track in zip(data['rows'], frame['cards']):_card(p, model, row, track)
    finally:p.restore()
    return frame
