"""Preserved highest-league player and standings cards (2026-10-27).

Recovered from deliverables/MahjongOverlayStudio-v04-Events-20261027.zip.
Only the rendering functions are retained. Shared score accounting, corrected
riichi-deposit rows and meld rendering continue to use the current application.
Select explicitly with model.setting("theme", "saikouisen"). ML is the default.
"""
import copy
import tournament
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainterPath, QPen
from identity_media import paint_identity_media


def starting_standings(state):
    """The START row is the booked field before this half-game, never live pt."""
    source = copy.copy(state)
    source['broadcast'] = copy.copy(state['broadcast'])
    source['broadcast']['matches'] = [match for match in state['broadcast']['matches']
                                       if match['id'] != state['id']]
    rows = tournament.standings(source, mode='individual', live=False)
    for row in rows:
        if not row.get('games') and not row.get('points'):
            row.update(points=None, rank=None)
    return {row['id']: row for row in rows}


def draw_start(p, model, player_id, x, y, width=260):
    from artwork import paint_card_label
    from card_style import get_card_style
    from broadcast_artwork import _point
    row = starting_standings(model.state).get(player_id, {})
    style = get_card_style(model.settings, 'saikouisen')
    rank_spec = style['text']['rank']
    point_spec = dict(rank_spec, size=style['text']['score']['size']*.62,
                      bold=style['text']['score']['bold'])
    start_spec = dict(rank_spec, size=12, bold=False)
    paint_card_label(p, 'START', start_spec, QRectF(x+10, y+1, 60, 17), model.settings)
    paint_card_label(p, _point(row.get('points'), True), point_spec,
                     QRectF(x+24, y+17, width-104, 34), model.settings, right=True)
    rank = '—' if row.get('rank') is None else str(row['rank'])+'位'
    paint_card_label(p, rank, rank_spec, QRectF(x+width-68, y+18, 55, 33), model.settings, right=True)


def paint_saikouisen_card(*args, **kwargs):
    """Expose the preserved style to the settings preview without model state."""
    from artwork import paint_saikouisen_card as paint
    return paint(*args, **kwargs)

def draw_player(self, p, i, now):
    from artwork import rect, text, color, fmt, tile, paint_status_badge
    from mahjong_values import display_score, normalize_waits, TEAM_COLORS
    s = self.model.state
    d = s['players'][i]
    top = 164
    left = 14
    photo_w = 120
    card_x = 142
    card_w = 260
    finished = s['phase'] == 'finished'
    final = next((row for row in tournament.match_results(s) if row['id'] == d['id']), None) if finished and self.model.settings.get('final_cards_visible',False) else None
    points = final is not None and self.model.settings.get('final_points_mode', 'scores') == 'pt' and (not self.animation.active)
    frame = d.get('color', '#ffffff')
    team = d.get('team_color', TEAM_COLORS[i])
    card_h = 104
    if not finished and i == s['dealer']:
        outline = QPainterPath();outline.addRoundedRect(QRectF(4, top-10, 406, 184), 8, 8)
        p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(color(frame), 3));p.drawPath(outline)
    paint_identity_media(p,self.model,d,QRectF(left,top,photo_w,165),radius=4)
    score = self.animation.value(i, now)
    score = display_score(s, i, self.model.settings.get('riichi_payment_mode')) if score is None else score
    value = f"{final['points']:+,.1f}".replace('-', '−') if points else fmt(score)
    paint_saikouisen_card(p, d['name'], d.get('team', ''), team, value,
                         final['rank'] if final else None, settings=self.model.settings)
    status_y = top + 112
    mode = '' if finished else 'riichi' if d['riichi'] else 'tenpai' if d.get('tenpai') else ''
    if final:
        if points:
            text(p, 'pt', card_x + 13, status_y, card_w - 26, 53, 23, align=Qt.AlignmentFlag.AlignRight, literal=True)
    elif not mode:
        draw_start(p, self.model, d['id'], card_x, status_y, card_w)
    if mode:
        c = '#c00d1a' if mode == 'riichi' else '#df9700'
        progress = self.transition(self.status_start.get(i), now, 0.46)
        p.save()
        p.setOpacity(p.opacity() * progress)
        p.translate(0, (1 - progress) * 8)
        rect(p, card_x, status_y, card_w, 53, c)
        if d.get('furiten'):
            rect(p, card_x, status_y, card_w, 53, '#486cba4b')
        waits = normalize_waits(d.get('waits', [])) if d.get('tenpai') else []
        remaining = d.get('remaining') if d.get('tenpai') else None
        count_width = 61
        available = card_w - 24 - count_width
        scale = min(1.26, available / max(1, len(waits) * 32))
        start = card_x + 10
        for j, code in enumerate(waits):
            p.save()
            if code in d.get('waits_no_yaku', []):
                p.setOpacity(p.opacity() * 0.32)
            tile(p, code, start + j * 32 * scale, status_y + (53 - 36 * scale) / 2, scale)
            p.restore()
        after_tiles = start+len(waits)*32*scale+4
        if waits and after_tiles+45 < card_x+card_w-count_width:
            text(p, '待牌', after_tiles, status_y+2, 44, 49, 17)
        if remaining is not None:
            text(p, '余', card_x + card_w - 59, status_y + 2, 19, 49, 16)
            self.remaining_number(p, i, remaining, card_x + card_w - 39, status_y, now)
        paint_status_badge(p, '立直' if mode == 'riichi' else '听牌', left, status_y, c, photo_w)
        p.restore()
    self.player_melds(p, d, i, now, finished)
    self.player_payments(p, i, now)

def draw_standing_photo(p, model, row, mode):
    import artwork
    from broadcast_artwork import _rect, _text, _accent, _profile, _num, _point, _draw_in_table_slots, WHITE, MUTED
    'Keep the highest-league square photo area; teams use their own name mark.'
    area = QRectF(10, 10, 120, 165)
    profile = _profile(model, row.get('id'), row.get('name', '')) if mode == 'individual' else {}
    paint_identity_media(p, model, {**profile, **row} if mode == 'individual' else row,
                         area, team=mode != 'individual')
    return profile

def draw_standing_card(p, model, row, mode):
    import artwork
    from broadcast_artwork import _rect, _text, _accent, _profile, _num, _point, _draw_in_table_slots, WHITE, MUTED
    "Only this seat's all-event rank and points replace its original contents."
    profile = draw_standing_photo(p, model, row, mode)
    label = row.get('team') or profile.get('team', '') if mode == 'individual' else '队伍积分'
    from i18n import tr
    paint_saikouisen_card(p, row.get('name', ''), tr(label, literal=mode == 'individual'),
                         _accent(row.get('color') or profile.get('team_color')),
                         _point(row.get('points'), True), row.get('rank', '—'), x=138, y=10,
                         settings=getattr(model, 'settings', {}))

def draw_settlement(p, model, summary, stage='hand', progress=1.0, layout=None):
    import artwork
    from broadcast_artwork import _rect, _text, _accent, _profile, _num, _point, _draw_in_table_slots, WHITE, MUTED
    'Native per-hand broadcast extension, independent of official broadcast claims.'
    total = stage == 'total'
    rows = summary.get('standings' if total else 'players', [])
    mode = 'team' if total and summary.get('mode', 'team') == 'team' else 'individual'
    amount = max(0.0, min(1.0, _num(progress, 1.0)))

    def draw(row):
        identity = dict(row)
        if not total:
            identity.update(id=row.get('player_id', row.get('id')), name=row.get('player_name', row.get('name', '')))
        profile = draw_standing_photo(p, model, identity, mode)
        before = _num(row.get('points_before', row.get('before_points')))
        after = _num(row.get('points_after', row.get('points')))
        delta = _num(row.get('points_delta', row.get('delta')), after - before)
        label = '队伍总积分' if mode == 'team' else '本场pt'
        from i18n import tr
        settings = getattr(model, 'settings', {})
        card = paint_saikouisen_card(p, identity.get('name', ''), tr(label),
                                    _accent(row.get('color') or profile.get('team_color')),
                                    _point(before + (after - before) * amount, True),
                                    x=138, y=10, settings=settings)
        delta_color = '#00d66f' if delta > 0 else '#ff3c51' if delta < 0 else MUTED
        old = row.get('rank_before', row.get('before_rank', '—'))
        new = row.get('rank_after', row.get('rank', '—'))
        rank_label = '—' if new is None else f'{new}位' if old == new else f'{old} → {new}位'
        artwork.paint_card_label(p, rank_label, card['style']['text']['rank'], QRectF(151, 124, 115, 49), settings)
        artwork.text(p, _point(delta, True) + ('' if total else ' pt'), 273, 124, 112, 49, 25, delta_color, Qt.AlignmentFlag.AlignRight)
    _draw_in_table_slots(p, rows, layout, draw)
