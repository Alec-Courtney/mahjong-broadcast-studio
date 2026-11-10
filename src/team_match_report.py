"""Selected confirmed halves as a single team summary image, without baselines."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from event_penalties import net_results, penalty_totals
from event_teams import is_rgb_color
from ranking_order import match_seats, team_identity
from ranking_reports import confirmed_matches


def decimal(value):
    value = round(value, 4)
    return f'{value:.4f}'.rstrip('0').rstrip('.') if value else '0'


def date_label(match):
    try:
        day = datetime.fromisoformat(match['recorded_at']).astimezone(timezone(timedelta(hours=8)))
        return f"{day.month}/{day.day}（周{'一二三四五六日'[day.weekday()]}）"
    except (KeyError, TypeError, ValueError):
        return ''


def build_report(state, event, match_ids):
    if event.get('kind') != 'team_league':
        raise ValueError('队伍场次结果图用于团队赛事')
    selected = set(match_ids)
    if not selected:
        raise ValueError('请至少勾选一个半庄')
    matches = confirmed_matches(state)
    known = {match['id'] for match in matches}
    if selected - known:
        raise ValueError('所选半庄已删除或尚未确认，请重新选择')
    teams, halves = {}, []
    registered = state['broadcast'].get('teams', {})
    for number, match in enumerate(matches, 1):
        if match['id'] not in selected:
            continue
        entries = net_results(match)
        seats = {identity: i for i, identity in enumerate(match_seats(match))}
        entries = sorted(entries, key=lambda entry: seats.get(entry['id'], entry['rank']))
        cells = {}
        _, deductions = penalty_totals(match)
        for entry in entries:
            identity = team_identity(entry)
            if identity not in teams:
                current = registered.get(identity, {})
                name = current.get('name') or entry.get('team') or ('未分队 · ' + entry.get('name', entry['id']))
                color = current.get('color') or entry.get('color')
                teams[identity] = dict(id=identity, name=name,
                    color=color if is_rgb_color(color) else '#66757f', points=0.)
            cell = cells.setdefault(identity, dict(players=[], points=0., penalty=deductions.get(identity, 0.)))
            cell['players'].append(dict(id=entry['id'], name=entry.get('name') or entry['id'],
                                        score=entry.get('score'), points=entry['points']))
            cell['points'] += entry['points']
        for identity, cell in cells.items():
            cell['points'] = round(cell['points'] - cell['penalty'], 4)
            teams[identity]['points'] = round(teams[identity]['points'] + cell['points'], 4)
        halves.append(dict(id=match['id'], number=number, title=match.get('title') or f'第{number}半庄',
                           date=date_label(match), cells=cells))
    return dict(event_id=event['id'], title=event['name'] + ' · 队伍场次结果',
                teams=list(teams.values()), halves=halves)


def _layout(data):
    # Keep four-team reports close to the reference; larger lineups remain one image.
    label_width, column_width, row = 166, 242, 49
    width = max(820, label_width + column_width * len(data['teams']))
    column_width = (width - label_width) / max(1, len(data['teams']))
    blocks = []
    for half in data['halves']:
        players = max((len(cell['players']) for cell in half['cells'].values()), default=1)
        blocks.append(dict(half=half, players=players,
                           penalty=any(cell['penalty'] for cell in half['cells'].values())))
    height = 72 + row * (len(data['teams']) + 1) + sum(
        row * (1 + 3 * block['players'] + int(block['penalty'])) + 8 for block in blocks) + 8
    return width, height, label_width, column_width, row, blocks


def render_report(data):
    from PySide6.QtCore import Qt, QRectF
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
    from artwork import FONT
    from panel_typography import draw_single_line

    width, height, label_width, column_width, row, blocks = _layout(data)
    if width * height > 80000000:
        raise ValueError('所选场次图片过大，请减少勾选的半庄数量')
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    if image.isNull():
        raise OSError('无法创建队伍场次结果图')
    image.fill(QColor('#ffffff'))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    def text(value, rect, size=23, bold=False, color='#16202b'):
        font = QFont(FONT)
        font.setPixelSize(size)
        font.setBold(bold)
        painter.setPen(QColor(color))
        draw_single_line(painter, str(value), rect.adjusted(10, 0, -10, 0), font,
                         Qt.AlignmentFlag.AlignCenter, minimum=12)

    def ink(fill):
        color = QColor(fill)
        return '#ffffff' if color.redF() * .299 + color.greenF() * .587 + color.blueF() * .114 < .52 else '#15202b'

    def cell(value, x, y, w, h, fill='#f4f5f6', bold=False, size=23, color=None):
        rect = QRectF(x, y, w, h)
        painter.fillRect(rect, QColor(fill))
        painter.setPen(QPen(QColor('#ffffff'), 1))
        painter.drawRect(rect)
        text(value, rect, size, bold, color or ink(fill))

    try:
        text(data['title'], QRectF(0, 8, width, 55), 30, True)
        y = 72
        split = width * .5
        cell('出战队伍', 0, y, split, row, bold=True)
        cell('所选场次总积分', split, y, width - split, row, bold=True)
        y += row
        for team in data['teams']:
            cell(team['name'], 0, y, split, row, team['color'])
            cell(decimal(team['points']), split, y, width - split, row, team['color'])
            y += row
        for block in blocks:
            half = block['half']
            y += 8
            cell(half['date'] or '场次 / 队伍', 0, y, label_width, row, bold=True, size=20)
            for i, team in enumerate(data['teams']):
                cell(team['name'], label_width + i * column_width, y, column_width, row, team['color'], True)
            y += row
            for index in range(block['players']):
                for offset, label in enumerate((f"第{half['number']}半庄" if index == 0 else '同队选手', '最终点数', '积分（pt）')):
                    line_y = y + offset * row
                    cell(label, 0, line_y, label_width, row, size=21, bold=offset == 0)
                    for i, team in enumerate(data['teams']):
                        record = half['cells'].get(team['id'])
                        player = record['players'][index] if record and index < len(record['players']) else None
                        value = '—'
                        if player:
                            if offset == 0:
                                value = player['name']
                            elif offset == 1:
                                score = player['score']
                                value = f'{score:,.0f}' if isinstance(score, (int, float)) else '—'
                            else:
                                value = decimal(player['points'])
                        cell(value, label_width + i * column_width, line_y, column_width, row,
                             team['color'] if record else '#f1f2f4')
                y += 3 * row
            if block['penalty']:
                cell('队伍扣分（pt）', 0, y, label_width, row, size=19)
                for i, team in enumerate(data['teams']):
                    record = half['cells'].get(team['id'])
                    penalty = record['penalty'] if record else 0.
                    cell('−' + decimal(penalty) if penalty else '—', label_width + i * column_width,
                         y, column_width, row, team['color'] if record else '#f1f2f4')
                y += row
    finally:
        painter.end()
    return image


def export_png(image, path):
    from PySide6.QtCore import QBuffer, QIODevice
    from session_repository import atomic_bytes

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    try:
        if not image.save(buffer, 'PNG'):
            raise OSError('无法保存队伍场次结果图')
        atomic_bytes(Path(path), bytes(buffer.data()))
    finally:
        buffer.close()
