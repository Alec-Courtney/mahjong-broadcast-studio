"""One confirmed-results source for on-screen tables, PNG and colored XLSX."""
from __future__ import annotations

import copy
import math
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from event_teams import is_rgb_color
from ranking_schema import (COLUMNS, PERSONAL, TEAM, ML_TECH, TECH, default_columns,
    preset_columns, allowed_columns, columns, validate_columns, validate_headers, validate_baseline)
from ranking_order import ScoreTimeline, team_identity as team_id

REPORT_PAGE_SIZE = 36


def confirmed_matches(state):
    # A correction replaces a match by id. Old files lacking completion metadata
    # keep their explicit historical confirmation; a current match must be final.
    matches = {}
    for item in state['broadcast']['matches']:
        if item.get('committed') and (item['id'] != state['id'] or state['phase'] == 'finished'):
            matches[item['id']] = item
    return list(matches.values())


def row_team_color(row):
    """Use each stable team's own colour; unassigned/invalid rows stay neutral."""
    identity = str(row.get('team_id') or row.get('id') or '')
    value = row.get('color')
    if identity.startswith('unassigned:') or ('team' in row and not row.get('team') and not row.get('team_id')):
        return '#66757f'
    return value.lower() if is_rgb_color(value) else '#66757f'


def team_row_fill(row, light=True):
    """Readable whole-row tint, shared by PNG, Excel and the workspace table."""
    value = row_team_color(row)[1:]
    channels = [int(value[index:index+2], 16) for index in (0, 2, 4)]
    # Pale table tints keep dark report text legible on white-background exports.
    channels = [round(channel*.18+255*.82) if light else round(channel*.48+12*.52)
                for channel in channels]
    return '#' + ''.join(f'{channel:02x}' for channel in channels)


def report_row_fill(data, row, index=0):
    if data.get('mode') in ('individual', 'team'):
        return '#ffffff'
    if 0 < row.get('rank', 0) <= data.get('event', {}).get('qualification_count', 0):
        return '#e8f3ec'
    return '#ffffff' if index % 2 == 0 else '#edf2ef'


def report_cell_fill(data, row, key, index=0):
    if data.get('mode') in ('individual', 'team') and key == 'team' and row.get('team'):
        return row_team_color(row)
    return report_row_fill(data, row, index)


def report_cell_color(data, row, key):
    if data.get('mode') in ('individual', 'team') and key == 'team' and row.get('team'):
        from PySide6.QtGui import QColor
        color = QColor(row_team_color(row))
        luminance = sum(channel * weight for channel, weight in
                        zip((color.redF(), color.greenF(), color.blueF()), (.2126, .7152, .0722)))
        return '#ffffff' if luminance < .53 else '#111111'
    value = row.get(key)
    if key in ('points', 'leader_gap', 'cutoff_gap') and isinstance(value, (int, float)) and value < 0:
        return '#b64444'
    return '#111111'


def style_table_item(cell, data, row, key, index=0):
    """Apply the exported report palette to one operator-table item."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    cell.setBackground(QColor(report_cell_fill(data, row, key, index)))
    cell.setForeground(QColor(report_cell_color(data, row, key)))
    if key in ('games', 'planned', 'remaining', 'progress'):
        planned = row.get('planned', 0)
        cell.setToolTip(f"已打 {row['played']} 场 / 计划 {planned} 场，剩余 {row['remaining']} 场"
                        if planned else f"已打 {row['played']} 场，未设置计划场次")


def apply_plan(row, event, mode):
    row['planned'] = event.get('team_plans' if mode == 'team' else 'player_plans', {}).get(
        row['id'], event.get('planned_games', 0))
    planned = row['planned']
    row['remaining'] = max(0, planned - row['played']) if planned else None
    row['progress'] = row['played'] / planned if planned else None
    row['games'] = f"{row['played']}/{planned or '—'}" if mode == 'team' else row['played']


def individual_rows(state, event):
    rows = {}
    teams = state['broadcast'].get('teams', {})
    for player in state['broadcast']['roster']:
        baseline = player.get('result_baseline', {})
        counts = baseline.get('counts', [0] * 4)[:]
        team = teams.get(team_id(player), {})
        rows[player['id']] = dict(id=player['id'], name=player['name'], team=team.get('name', player.get('team', '')),
            team_id=team_id(player), color=team.get('color', player.get('color', '#377e72')), hidden=player.get('hidden', False),
            points=float(player.get('base_points', 0)), played=player.get('games', 0),
            counts=counts, max_score=baseline.get('max_score'), status=player.get('status', 'active'))
    ledger = ScoreTimeline(rows.values())
    for match in confirmed_matches(state):
        ledger.record(match)
        for result in match['results']:
            if result['id'] not in rows:continue
            row = rows[result['id']]; row['played'] += 1
            row['counts'][result['rank'] - 1] += 1
            if isinstance(result.get('score'), (int, float)):
                row['max_score'] = result['score'] if row['max_score'] is None else max(row['max_score'], result['score'])
    technical=technical_rows(state)
    for row in rows.values():
        row.update(technical[row['id']])
        if not row['detail_hands']:
            for key in ('tsumo_count','ron_count','draw_count'):row[key]=None
        if not row['hands'] and row['played']:
            for key in ('hands','calls','riichi','wins','deal_ins','win_points','deal_in_points'):row[key]=None
        count = sum(row['counts']); row['known_games'] = count
        apply_plan(row, event, 'individual')
        row['average_rank'] = sum((i + 1) * n for i, n in enumerate(row['counts'])) / count if count else None
        row['top_rate'] = row['counts'][0] / count if count else None
        row['top_two_rate'] = sum(row['counts'][:2]) / count if count else None
        row['avoid_fourth_rate'] = 1 - row['counts'][3] / count if count else None
        for key, value in zip(('first', 'second', 'third', 'fourth'), row['counts']):
            row[key] = value if count or not row['played'] else None
    return ledger.ranked(rows.values())


def technical_rows(state):
    """Basic stats include reliable bases; joint stats require actual hand records."""
    from tournament import STAT_FIELDS
    rows={}
    for player in state['broadcast']['roster']:
        base=player.get('stats',player.get('baseline_stats',{}))
        rows[player['id']]={key:base.get(key,0) for key in STAT_FIELDS}
        rows[player['id']].update(detail_hands=0,detail_wins=0,detail_riichi=0,detail_calls=0,
            tsumo_count=0,ron_count=0,riichi_wins=0,riichi_deals=0,call_wins=0,call_deals=0,
            dama_wins=0,draw_count=0,draw_tenpai=0,riichi_win_points=0,call_win_points=0,
            technical_matches=0)
    for match in confirmed_matches(state):
        seen=set()
        for hand in match['hands']:
            result=hand.get('result') or {};tenpai=result.get('tenpai')
            for seat,player in enumerate(hand['players']):
                row=rows.get(player['id'])
                if row is None:continue
                seen.add(player['id'])
                for key in STAT_FIELDS:row[key]+=player.get(key,0)
                won=bool(player.get('wins'));dealt=bool(player.get('deal_ins'));riichi=bool(player.get('riichi'));called=bool(player.get('calls'))
                row['detail_hands']+=1;row['detail_wins']+=int(won);row['detail_riichi']+=int(riichi);row['detail_calls']+=int(called)
                row['tsumo_count']+=int(won and hand['kind']=='tsumo');row['ron_count']+=int(won and hand['kind']=='ron')
                row['riichi_wins']+=int(won and riichi);row['riichi_deals']+=int(dealt and riichi)
                row['call_wins']+=int(won and called);row['call_deals']+=int(dealt and called)
                row['dama_wins']+=int(won and not riichi and not called)
                if won and riichi:row['riichi_win_points']+=player.get('win_points',0)
                if won and called:row['call_win_points']+=player.get('win_points',0)
                if hand['kind']=='draw' and isinstance(tenpai,list):row['draw_count']+=1;row['draw_tenpai']+=int(seat in tenpai)
        for identity in seen:rows[identity]['technical_matches']+=1
    for row in rows.values():
        for key,count in [('call_rate','calls'),('riichi_rate','riichi'),('win_rate','wins'),('deal_in_rate','deal_ins')]:row[key]=row[count]/row['hands'] if row['hands'] else None
        row['average_win']=row['win_points']/row['wins'] if row['wins'] else None
        row['average_deal_in']=row['deal_in_points']/row['deal_ins'] if row['deal_ins'] else None
        for key,numerator,denominator in [('tsumo_rate','tsumo_count','detail_wins'),('dama_win_rate','dama_wins','detail_wins'),
            ('riichi_win_rate','riichi_wins','detail_riichi'),('riichi_deal_in_rate','riichi_deals','detail_riichi'),
            ('call_win_rate','call_wins','detail_calls'),('call_deal_in_rate','call_deals','detail_calls'),
            ('draw_tenpai_rate','draw_tenpai','draw_count'),('riichi_average_win','riichi_win_points','riichi_wins'),
            ('call_average_win','call_win_points','call_wins')]:row[key]=row[numerator]/row[denominator] if row[denominator] else None
    return rows


def team_rows(state, event):
    teams = {}
    registered = state['broadcast'].get('teams', {})
    roster = state['broadcast']['roster']
    hidden = {p['id'] for p in roster if p.get('hidden')}
    matches = confirmed_matches(state)
    def obtain(player):
        identity = team_id(player)
        if identity not in teams:
            registered_team = registered.get(identity, {})
            name = registered_team.get('name') or player.get('team') or player['name']
            teams[identity] = dict(id=identity, team=name,
                name=name, color=registered_team.get('color') or player.get('color', '#377e72'),
                points=0., played=0, counts=[0] * 4, known_games=0,
                hidden=identity.startswith('unassigned:') and player['id'] in hidden)
        return teams[identity]
    for player in roster:
        row = obtain(player); row['points'] += player.get('base_points', 0); row['played'] += player.get('games', 0)
        for i, value in enumerate(player.get('result_baseline', {}).get('counts', [0] * 4)):row['counts'][i] += value
    for match in matches:
        for result in match['results']:obtain(result)
    for identity, base in event.get('team_baselines', {}).items():
        if identity not in teams:obtain(dict(id='baseline:'+identity,team_id=identity,team=base.get('name',identity),name=base.get('name',identity),color=base.get('color','#377e72')))
        if 'points' in base:teams[identity]['points'] = base['points']
        if 'games' in base:teams[identity]['played'] = base['games']
    ledger = ScoreTimeline(teams.values())
    for match in matches:
        for identity in ledger.record(match, 'team'):
            teams[identity]['played'] += 1
        for result in match['results']:
            teams[team_id(result)]['counts'][result['rank'] - 1] += 1
    ranked = ledger.ranked(teams.values()); count = event.get('qualification_count', 0)
    cutoff = ranked[count - 1]['points'] if 0 < count < len(ranked) else None
    for row in ranked:
        apply_plan(row, event, 'team')
        row['leader_gap'] = round(row['points'] - ranked[0]['points'], 4)
        row['cutoff_gap'] = round(row['points'] - cutoff, 4) if cutoff is not None else None
        row['known_games'] = sum(row['counts'])
        for key, value in zip(('first', 'second', 'third', 'fourth'), row['counts']):
            row[key] = value if row['known_games'] or not row['played'] else None
    return ranked


def report(state, event, mode='individual'):
    if mode == 'team' and event.get('kind') != 'team_league':raise ValueError('个人赛事只有个人积分榜')
    rows = team_rows(state, event) if mode == 'team' else individual_rows(state, event)
    technical_notes=[]
    if mode!='team' and any(k in TECH for k in columns(event,mode)):
        technical_notes=['技术比率仅按已确认半庄的已知结算局及可靠历史基数；暗杠不计副露，平均打点含本场、不含供托。',
            '扩展交叉指标只按逐局明细统计；只补录最终持点的半庄没有逐局技术数据。']
        technical_notes += [f"技术统计覆盖：{r['name']}，已知 {r['hands'] or 0} 局；有逐局明细 {r['technical_matches']}/{r['played']} 场。" for r in rows if r['technical_matches']<r['played']]
    return dict(title=event['name'] + (' · 团队积分榜' if mode == 'team' else ' · 个人积分榜'),
        mode=mode, columns=columns(event, mode), rows=rows, event=copy.deepcopy(event),
        notes=['仅统计已确认的半庄，积分已计入该半庄的生效判罚。',
               'Top率=1位/已知顺位场次；连对率=(1位+2位)/已知顺位场次；避四率=1−4位/已知顺位场次。',
               '平均顺位和比率按已知顺位统计，历史缺失不补造；最高分为半庄最终持点。',
               '累计积分同分时，先达到者在前。同一半庄达到同分时按席位顺序。',
               ('团队场次每队每半庄计一次；顺位次数按成员出场计。距第一名/晋级线=本队积分−对应积分。' if mode == 'team' else
                '个人榜场次数仅显示已打场次；计划、剩余场次和完成进度可在显示列中开启。')]
              +technical_notes[:2]+ [f"统计覆盖：{r['team'] if mode == 'team' else r['name']}，已知顺位 {r['known_games']}，已打 {r['played']} 场。"
                 for r in rows if r['known_games'] < r['played']]+technical_notes[2:])


def header(key, mode, event=None):
    custom=(event or {}).get('report_headers',{}).get(mode,{}).get(key)
    return custom or ('团队积分' if key == 'points' and mode == 'team' else COLUMNS[key][0])


def display(key, value):
    if value is None:return '—'
    kind = COLUMNS[key][2]
    if kind == 'percent':return f'{value:.1%}'
    if kind == 'average':return f'{value:.2f}'
    if kind == 'score_average':return f'{value:,.1f}'
    if kind == 'decimal':return f'{value:.1f}' if value else '0.0'
    return str(value)


def report_layout(data, start=0, limit=40):
    """Size every page from the full field so columns stay aligned when paging."""
    from PySide6.QtCore import Qt, QRectF
    from PySide6.QtGui import QFont, QFontMetricsF
    import artwork

    keys = data['columns']
    rows = data['rows'][start:None if limit is None else start+limit]
    body = QFont(artwork.FONT); body.setPixelSize(24); body.setWeight(QFont.Weight.Normal)
    heading = QFont(artwork.FONT); heading.setPixelSize(22); heading.setWeight(QFont.Weight.Normal)
    # Small CJK glyphs can acquire uneven, heavy strokes with full grid fitting.
    body.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    heading.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    metrics, headings = QFontMetricsF(body), QFontMetricsF(heading)
    widths = []
    for key in keys:
        content = max((metrics.horizontalAdvance(display(key, row.get(key))) for row in data['rows']), default=0)
        label = headings.horizontalAdvance(header(key, data['mode'], data['event']))
        widths.append(math.ceil(max(COLUMNS[key][1], min(360, content+16), min(320, label+16))))
    margin = 10
    width = max(760, sum(widths)+2*margin)
    extra = width-2*margin-sum(widths)
    flexible = [index for index, key in enumerate(keys) if key in ('team', 'name')] or [len(keys)-1]
    for index in flexible:widths[index] += extra//len(flexible)
    widths[flexible[-1]] += extra % len(flexible)
    flags = int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWrapAnywhere)
    header_height = math.ceil(max(headings.boundingRect(QRectF(0, 0, w-16, 1000), flags,
        header(key, data['mode'], data['event'])).height() for key, w in zip(keys, widths)))+8
    heights = [math.ceil(max([metrics.height()] + [metrics.boundingRect(QRectF(0, 0, w-16, 10000), flags,
        str(row.get(key) or '')).height() for key, w in zip(keys, widths) if key in ('name', 'team')]))+10 for row in rows]
    top = 58
    footer = 26 if len(data['rows']) > len(rows) else 0
    return dict(width=width, height=top+header_height+sum(heights)+footer+10,
                widths=widths, header_height=header_height, heights=heights, top=top, margin=margin,
                body_font=body, header_font=heading)


def render_report(data, start=0, limit=40):
    from PySide6.QtCore import Qt, QRectF, QLineF
    from PySide6.QtGui import QImage, QPainter, QPainterPath, QColor, QFont, QFontMetricsF, QPen
    import artwork
    from panel_typography import draw_single_line, single_line_layout

    layout = report_layout(data, start, limit)
    number_font = QFont(layout['body_font'])
    number_font.setHintingPreference(QFont.HintingPreference.PreferDefaultHinting)
    width, height = layout['width'], layout['height']
    keys = data['columns']; rows = data['rows'][start:None if limit is None else start+limit]
    margin, top = layout['margin'], layout['top']
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    if image.isNull():raise OSError('无法创建榜单图片')
    image.fill(QColor('#ffffff'))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    def text(value, rect, font, color='#111111', wrap=False):
        box = rect.adjusted(8, 2, -8, -2)
        painter.setFont(font); painter.setPen(QColor(color))
        if wrap and QFontMetricsF(font).horizontalAdvance(str(value)) > box.width():
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWrapAnywhere, str(value))
        elif font.hintingPreference() == QFont.HintingPreference.PreferNoHinting:
            placement = single_line_layout(value, box, font, Qt.AlignmentFlag.AlignCenter, elide=False)
            path = QPainterPath()
            path.addText(placement.baseline, placement.font, str(value))
            painter.save()
            painter.setClipRect(box)
            painter.fillPath(path, QColor(color))
            painter.restore()
        else:
            draw_single_line(painter, value, box, font, Qt.AlignmentFlag.AlignCenter, elide=False)
    try:
        title_font = QFont(artwork.FONT); title_font.setPixelSize(34); title_font.setBold(True)
        text(data['title'], QRectF(margin, 6, width-2*margin, 46), title_font)
        x = margin
        for key, col_width in zip(keys, layout['widths']):
            rect = QRectF(x, top, col_width, layout['header_height'])
            painter.fillRect(rect, QColor('#e5e5e5'))
            text(header(key, data['mode'], data['event']), rect, layout['header_font'], wrap=True)
            x += col_width
        y = top+layout['header_height']
        for index, (row, row_height) in enumerate(zip(rows, layout['heights'])):
            x = margin
            for key, col_width in zip(keys, layout['widths']):
                rect = QRectF(x, y, col_width, row_height)
                painter.fillRect(rect, QColor(report_cell_fill(data, row, key, index)))
                text(display(key, row.get(key)), rect,
                     layout['body_font'] if COLUMNS[key][2] == 'text' else number_font,
                     report_cell_color(data, row, key),
                     wrap=key in ('team', 'name'))
                x += col_width
            y += row_height
            painter.setPen(QPen(QColor('#929292'), .7))
            painter.drawLine(QLineF(margin, y, width-margin, y))
        bottom = y
        x = margin
        solid_after = {'rank', 'team', 'name', 'points', 'games', 'average_rank', 'fourth', 'avoid_fourth_rate'}
        for index, (key, col_width) in enumerate(zip(keys, layout['widths'])):
            x += col_width
            if index == len(keys)-1:continue
            # Every column gets a line; finer dashed rules group related stats.
            painter.setPen(QPen(QColor('#555555'), .9,
                                Qt.PenStyle.SolidLine if key in solid_after else Qt.PenStyle.DashLine))
            painter.drawLine(QLineF(x, top, x, bottom))
        painter.setPen(QPen(QColor('#222222'), 1.4))
        painter.drawLine(QLineF(margin, top+layout['header_height'], width-margin, top+layout['header_height']))
        painter.drawRect(QRectF(margin, top, width-2*margin, bottom-top))
        if len(data['rows']) > len(rows):
            font = QFont(artwork.FONT); font.setPixelSize(18)
            text(f"{start+1 if rows else 0}–{start+len(rows)} / {len(data['rows'])}",
                 QRectF(width-210, bottom+3, 200, 24), font, '#555555')
    finally:painter.end()
    return image


def KINDS_LABEL(event):
    from event_store import KINDS
    return KINDS.get(event.get('kind'), '')


def export_png(data, filename):
    from PySide6.QtCore import QBuffer, QIODevice
    from session_repository import atomic_bytes

    target = Path(filename)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    try:
        if not render_report(data, limit=None).save(buffer, 'PNG'):
            raise OSError('无法写入榜单图片')
        atomic_bytes(target, bytes(buffer.data()))
    finally:
        buffer.close()
    return [target]


def _xml(value):
    return escape(re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value)))


def _col(index):
    value = ''
    while index:
        index, digit = divmod(index - 1, 26); value = chr(65 + digit) + value
    return value


def export_xlsx(state, event, filename):
    """Standard OOXML, no Excel installation or optional runtime dependency."""
    reports = [report(state, event)]
    if event['kind'] == 'team_league':reports.append(report(state, event, 'team'))
    if event['kind'] == 'individual_knockout' and event['bracket']['nodes']:reports.append(schedule_report(state,event))
    return _export_reports(reports,event,filename)


def export_schedule_xlsx(state,event,filename):
    return _export_reports([schedule_report(state,event)],event,filename)


def schedule_report(state,event):
    from event_schedule import resolve_sources,node_matches,signature,source_label
    extra={'node_round':('轮次',80,'int'),'node_name':('桌名',180,'text'),
        'node_status':('状态',150,'text'),'advancers':('已确认晋级',220,'text')}
    extra.update({f'seat{i}':(f'席位{i}',160,'text') for i in range(1,5)});COLUMNS.update(extra)
    roster={p['id']:p['name'] for p in state['broadcast']['roster']};rows=[]
    for node in event['bracket']['nodes']:
        row=dict(node_round=node['round'],node_name=node['name'],games=f"{len(node_matches(state,node['id']))}/{node['planned_games']}",
                 node_status={'pending':'待开赛','running':'进行中','complete':'已确认晋级'}[node['status']],
                 advancers=' / '.join(roster.get(p,p) for p in node.get('winners',[])))
        if node.get('result_signature') and node['result_signature']!=signature(state,node):row['node_status']='成绩更正 · 待核查'
        for index,(identity,source) in enumerate(zip(resolve_sources(event,node),node['sources']),1):
            row[f'seat{index}']=roster.get(identity) or source_label(event,source,roster)
        rows.append(row)
    return dict(title=event['name']+' · 淘汰赛程',sheet_name='淘汰赛程',event=event,mode='schedule',
        columns=['node_round','node_name','seat1','seat2','seat3','seat4','games','node_status','advancers'],rows=rows,
        notes=['仅已确认的半庄计入本桌场次；晋级名单须在工作台核查并确认。','席位来源、每桌半庄数和晋级人数保存在完整赛事JSON中。'])


def _export_reports(reports,event,filename):
    styles = []
    fills = ['FFFFFF','000000','1B604B','EDF3EF','E0F0E4','FFFFFF','F6E5AC','DFE8ED','EAD7C7','E5E5E5']
    def style(fill=0, font=0, fmt=0, align='center'):
        spec = (fill, font, fmt, align)
        if spec not in styles:styles.append(spec)
        return styles.index(spec)
    style()
    sheets = []
    for data in reports:
        keys = data['columns']; last_col = _col(len(keys)); body = []
        def units(value):return sum(2 if ord(c)>255 else 1 for c in str(value))
        col_widths=[max(11,COLUMNS[k][1]/7,min(42,units(header(k,data['mode'],event))+3),
            min(44,max((units(r.get(k,''))+3 for r in data['rows']),default=0)) if k in ('name','team','node_name','advancers') else 0) for k in keys]
        def cell(address, value, sid):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return f'<c r="{address}" s="{sid}"><v>{value}</v></c>'
            return f'<c r="{address}" s="{sid}" t="inlineStr"><is><t xml:space="preserve">{_xml("—" if value is None else value)}</t></is></c>'
        ranking=data['mode'] in ('individual','team')
        body.append(f'<row r="1" ht="36" customHeight="1">{cell("A1", data["title"], style(5,6) if ranking else style(2,1,0,"left"))}</row>')
        header_height=max(29,max(math.ceil(units(header(k,data['mode'],event))/max(1,w-2))*16+8 for k,w in zip(keys,col_widths)))
        body.append(f'<row r="4" ht="{header_height}" customHeight="1">' + ''.join(cell(f'{_col(i)}4', header(key,data['mode'],event),style(9,4) if ranking else style(2,1)) for i,key in enumerate(keys,1)) + '</row>')
        for row_index, entry in enumerate(data['rows'], 5):
            cells = []
            for col_index, key in enumerate(keys,1):
                color = report_cell_fill(data, entry, key, row_index-5)[1:].upper()
                if color not in fills[2:]:fills.append(color)
                fill = fills.index(color, 2)
                value = entry.get(key); kind = COLUMNS[key][2]
                fmt = {'decimal':164,'average':165,'percent':166,'int':3,'score_average':164}.get(kind,0)
                font = {'#b64444':3, '#111111':4, '#ffffff':5}.get(report_cell_color(data, entry, key), 0)
                cells.append(cell(f'{_col(col_index)}{row_index}', value, style(fill,font,fmt)))
            row_height=max(25,max((math.ceil(units(entry.get(k,''))/max(1,w-2))*16+8 for k,w in zip(keys,col_widths) if COLUMNS[k][2]=='text'),default=25))
            body.append(f'<row r="{row_index}" ht="{row_height}" customHeight="1">' + ''.join(cells) + '</row>')
        end = max(4, len(data['rows']) + 4)
        merges = [f'A1:{last_col}1']
        widths = ''.join(f'<col min="{i}" max="{i}" width="{width:.1f}" customWidth="1"/>' for i,width in enumerate(col_widths,1))
        sheets.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<dimension ref="A1:{last_col}{end}"/>'
            '<sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            f'<cols>{widths}</cols><sheetData>{"".join(body)}</sheetData><autoFilter ref="A4:{last_col}{end}"/>'
            f'<mergeCells count="{len(merges)}">' + ''.join(f'<mergeCell ref="{m}"/>' for m in merges) + '</mergeCells>'
            '<pageMargins left="0.25" right="0.25" top="0.4" bottom="0.4" header="0.2" footer="0.2"/>'
            '<pageSetup orientation="landscape" paperSize="9" fitToWidth="1" fitToHeight="0"/></worksheet>')
    style_xml = ('<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="3"><numFmt numFmtId="164" formatCode="0.0;[Red]-0.0;0.0"/><numFmt numFmtId="165" formatCode="0.00"/><numFmt numFmtId="166" formatCode="0.0%"/></numFmts>'
        '<fonts count="7">' + ''.join(f'<font><sz val="{size}"/><color rgb="FF{color}"/><name val="Microsoft YaHei"/>{"<b/>" if bold else ""}</font>' for size,color,bold in [(11,'203B34',False),(12,'FFFFFF',True),(10,'61746B',False),(11,'BC4141',False),(11,'111111',False),(11,'FFFFFF',False),(12,'111111',True)]) + '</fonts>'
        f'<fills count="{len(fills)}">' + ''.join('<fill><patternFill patternType="none"/></fill>' if i==0 else '<fill><patternFill patternType="gray125"/></fill>' if i==1 else f'<fill><patternFill patternType="solid"><fgColor rgb="FF{color}"/><bgColor indexed="64"/></patternFill></fill>' for i,color in enumerate(fills)) + '</fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(styles)}">' + ''.join(f'<xf numFmtId="{fmt}" fontId="{font}" fillId="{fill}" borderId="0" xfId="0" applyNumberFormat="1" applyFill="1" applyAlignment="1"><alignment horizontal="{align}" vertical="center" wrapText="1"/></xf>' for fill,font,fmt,align in styles) + '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    names = [data.get('sheet_name') or ('团队积分榜' if data['mode']=='team' else '个人积分榜') for data in reports]
    content = '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(sheets)+1)) + '</Types>'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
    workbook = '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView/></bookViews><sheets>' + ''.join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i,name in enumerate(names,1)) + '</sheets></workbook>'
    relationships = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="{rel}worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1)) + f'<Relationship Id="styles" Type="{rel}styles" Target="styles.xml"/></Relationships>'
    target = Path(filename); temporary = target.with_name(target.name + '.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', content)
        archive.writestr('_rels/.rels', f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{rel}officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', workbook); archive.writestr('xl/_rels/workbook.xml.rels', relationships)
        archive.writestr('xl/styles.xml', style_xml)
        for index, sheet in enumerate(sheets,1):archive.writestr(f'xl/worksheets/sheet{index}.xml',sheet)
    temporary.replace(target); return target
