"""Four seated players' confirmed points, independent of the live projection.

A two-half-game knockout table keeps its first result visible through game two.
Other tables show booked cumulative pt.  The active knockout node is the scope;
points from earlier rounds must never leak into a new round's small score strip.
"""
from __future__ import annotations

import math

SCORE_STRIP_SIZE = (820, 120)
SCORE_STRIP_BACKGROUND = '#da080a0c'


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else 0.
    except (TypeError, ValueError):
        return 0.


def format_points(value):
    """Unknown is a dash; a genuine zero remains a signed, one-decimal pt."""
    if value is None:
        return '—'
    value = round(_number(value), 1)
    return f'{0. if value == 0 else value:+,.1f}'.replace('-', '−')


def table_score_strip(state, event=None):
    """Return seat-ordered display data without mutating the supplied session.

    ``table_score_mode`` optionally overrides automatic recognition.  Event-wide
    planned_games describes league player/team totals, so only a scheduled
    node's planned_games can automatically identify a two-half-game round.
    """
    from ranking_reports import confirmed_matches

    event = event or {}
    broadcast = state.get('broadcast', {})
    config = broadcast.get('config', {})
    players = state.get('players', [])[:4]
    roster = {item['id']: item for item in broadcast.get('roster', [])}
    active_id = broadcast.get('active_node_id')
    node = next((item for item in event.get('bracket', {}).get('nodes', [])
                 if item.get('id') == active_id), None) if active_id else None
    mode = config.get('table_score_mode', 'auto')
    if mode not in ('first_game', 'cumulative'):
        mode = 'first_game' if node and node.get('planned_games') == 2 else 'cumulative'

    matches = confirmed_matches(state) if broadcast else []
    if active_id:
        matches = [item for item in matches if item.get('bracket_node_id') == active_id]
    elif mode == 'first_game':
        # Explicit first-game mode also works for a manually run four-person
        # table, without mistakenly choosing another table's first archive.
        seats = {item['id'] for item in players}
        matches = [item for item in matches
                   if {result['id'] for result in item.get('results', [])} == seats]

    rows = [dict(id=player['id'], name=player.get('name', ''), seat=seat,
                 points=None if mode == 'first_game' else 0., games=0)
            for seat, player in enumerate(players)]
    lookup = {row['id']: row for row in rows}
    if mode == 'first_game':
        first = matches[0] if matches else None
        if first:
            for result in first.get('results', []):
                if result['id'] in lookup:
                    lookup[result['id']].update(points=_number(result.get('points')), games=1)
        title = '第1半庄结果'
        detail = '已确认 · pt' if first else '等待第1半庄确认'
    else:
        # Historical entry baselines belong to an entire league, not a newly
        # started elimination round.  Preserve them only in event scope.
        if not active_id:
            for row in rows:
                profile = roster.get(row['id'], {})
                row.update(points=_number(profile.get('base_points')), games=profile.get('games', 0))
        for match in matches:
            for result in match.get('results', []):
                row = lookup.get(result['id'])
                if row is not None:
                    row['points'] += _number(result.get('points'))
                    row['games'] += 1
        for row in rows:
            row['points'] = round(row['points'], 4)
        title = '本轮累计 pt' if active_id else '累计 pt'
        detail = '仅已确认成绩'

    return dict(title=title, detail=detail, mode=mode,
                scope='round' if active_id else 'event', rows=rows,
                confirmed_games=len(matches), source_match_id=matches[0]['id'] if mode == 'first_game' and matches else None)


def draw_table_score_strip(painter, model):
    """Draw compact two-by-two or four-column white text on gray-black."""
    from sidebar_paging import is_team_event
    if is_team_event(model):
        return
    from PySide6.QtCore import Qt, QRectF
    from PySide6.QtGui import QColor, QPen
    from typography import overlay_font, overlay_scope
    from panel_typography import draw_single_line, single_line_layout
    from i18n import tr

    data = table_score_strip(model.state, getattr(model, 'event', None))
    painter.save()
    with overlay_scope(getattr(model, 'settings', {})):
        painter.setClipRect(QRectF(0, 0, *SCORE_STRIP_SIZE))
        painter.fillRect(QRectF(0, 0, *SCORE_STRIP_SIZE), QColor(SCORE_STRIP_BACKGROUND))

        def label(value, rect, size, bold=False, align=Qt.AlignmentFlag.AlignLeft, font=None):
            shared = font is not None
            font = font or overlay_font(size, bold)
            painter.setPen(QColor('#ffffff'))
            return draw_single_line(painter, value, rect, font, align,
                                    minimum=font.pixelSize() if shared else 10, elide=False)

        layout = model.state.get('broadcast', {}).get('config', {}).get('table_score_layout', 'compact')
        name_box = QRectF(0, 0, 177, 29) if layout == 'columns' else QRectF(0, 0, 145, 45)
        point_box = QRectF(0, 0, 177, 38) if layout == 'columns' else QRectF(0, 0, 111, 45)
        # Every player uses one common data font. Include a three-digit pt
        # envelope so one/two-digit results cannot grow independently.
        data_font = overlay_font(26 if layout == 'columns' else 35, True)
        samples = [(row['name'], name_box) for row in data['rows']]
        samples += [(format_points(row['points']), point_box) for row in data['rows']]
        samples += [(value, point_box) for value in ('+999.9', '−999.9')]
        for value, box in samples:
            data_font = single_line_layout(value, box.adjusted(0, 0, -2, 0),
                                           data_font, minimum=1, elide=False).font
        if layout == 'columns':
            label(tr(data['title']), QRectF(16, 4, 380, 30), 20, True)
            painter.setPen(QPen(QColor('#40ffffff'), 1))
            painter.drawLine(16, 37, 804, 37)
            for row in data['rows']:
                x = row['seat']*205
                if row['seat']:
                    painter.setPen(QPen(QColor('#28ffffff'), 1))
                    painter.drawLine(x, 48, x, 108)
                label(row['name'], QRectF(x+14, 42, 177, 29), 26, True,
                      align=Qt.AlignmentFlag.AlignHCenter, font=data_font)
                label(format_points(row['points']), QRectF(x+14, 73, 177, 38), 26, True,
                      align=Qt.AlignmentFlag.AlignHCenter, font=data_font)
        else:
            # The original tournament broadcast places the title on the left
            # and four short name/result pairs in two rows on its right.
            label(tr(data['title']), QRectF(12, 36, 226, 48), 35, True)
            painter.setPen(QPen(QColor('#bbffffff'), 2))
            painter.drawLine(248, 18, 248, 102)
            for row in data['rows']:
                x = 264+(row['seat'] % 2)*276
                y = 12+(row['seat']//2)*49
                label(row['name'], QRectF(x, y, 145, 45), 35, True, font=data_font)
                label(format_points(row['points']), QRectF(x+149, y, 111, 45), 35, True,
                      align=Qt.AlignmentFlag.AlignRight, font=data_font)
    painter.restore()
