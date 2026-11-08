"""Four-seat knockout graph with explicit, auditable advancement."""
from __future__ import annotations
import copy
import hashlib
import json
import re
import uuid

from ranking_order import ScoreTimeline, match_seats
from event_penalties import net_results


def table_name(number):
    """One-based spreadsheet letters, shared across all rounds of a bracket."""
    if isinstance(number,bool) or not isinstance(number,int) or number<1:
        raise ValueError('桌号须为正整数')
    letters=''
    while number:
        number,digit=divmod(number-1,26);letters=chr(65+digit)+letters
    return letters+'桌'


def next_table_name(nodes):
    maximum=len(nodes)
    for node in nodes:
        match=re.fullmatch(r'([A-Z]+)桌',node.get('name',''))
        if match:
            number=0
            for letter in match[1]:number=number*26+ord(letter)-64
            maximum=max(maximum,number)
    return table_name(maximum+1)


def source_label(event,source,roster=None):
    """Compact upstream identity, without a generic pending-advancement suffix."""
    if 'player' in source:
        player=(roster or {}).get(source['player'],source['player'])
        return player.get('name',source['player']) if isinstance(player,dict) else player
    parent=next((node for node in event['bracket']['nodes'] if node['id']==source.get('node')),None)
    return (parent['name'] if parent else '来源桌')+f'{source.get("place",1)}位'


def validate_bracket(bracket, state=None):
    from tournament import _integer
    if not isinstance(bracket, dict) or not isinstance(bracket.get('nodes'), list) or len(bracket['nodes']) > 200:
        raise ValueError('淘汰赛程格式无效')
    nodes = bracket['nodes']; ids = set()
    roster = {p['id'] for p in state['broadcast']['roster']} if state else None
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get('id'), str) or not node['id'] or node['id'] in ids:
            raise ValueError('淘汰桌编号无效或重复')
        ids.add(node['id'])
        if not isinstance(node.get('name'), str) or not node['name'] or len(node['name']) > 80:
            raise ValueError('淘汰桌名称无效')
        _integer(node.get('round'), '淘汰轮次', 1, 32)
        _integer(node.get('planned_games'), '本桌半庄数', 1, 1000)
        _integer(node.get('advance_count'), '本桌晋级人数', 1, 3)
        if node.get('status') not in ('pending', 'running', 'complete'):raise ValueError('淘汰桌状态无效')
        sources = node.get('sources')
        if not isinstance(sources, list) or len(sources) != 4:raise ValueError('淘汰桌须有四个席位来源')
        for source in sources:
            if not isinstance(source, dict):raise ValueError('淘汰席位来源无效')
            if 'player' in source:
                if not isinstance(source['player'], str) or (roster is not None and source['player'] not in roster):
                    raise ValueError('淘汰选手不在名册中')
            elif 'node' in source:
                if not isinstance(source['node'], str):raise ValueError('上游淘汰桌无效')
                _integer(source.get('place'), '上游晋级名次', 1, 3)
            else:raise ValueError('请选择选手或上游晋级席位')
        if len({json.dumps(s,sort_keys=True) for s in sources}) != 4:raise ValueError('同桌四个来源不能重复')
        overrides=node.get('source_overrides',{})
        if (not isinstance(overrides,dict) or any(key not in ('0','1','2','3') or not isinstance(value,str)
            or not value or (roster is not None and value not in roster) for key,value in overrides.items())):
            raise ValueError('手动指定选手须对应有效席位和赛事名册')
        winners = node.get('winners', [])
        if not isinstance(winners, list) or len(set(winners)) != len(winners) or any(not isinstance(v,str) or (roster is not None and v not in roster) for v in winners):
            raise ValueError('晋级选手无效')
        if node['status'] == 'complete' and len(winners) != node['advance_count']:
            raise ValueError('晋级名单与人数不一致')
    lookup = {node['id']: node for node in nodes}; used_slots = set()
    round_players = set()
    for node in nodes:
        for source in node['sources']:
            if 'node' in source:
                parent = lookup.get(source['node'])
                if not parent or parent['round'] >= node['round'] or source['place'] > parent['advance_count']:
                    raise ValueError('晋级来源须来自较早轮次的有效名次')
                slot = (source['node'],source['place'])
                if slot in used_slots:raise ValueError('同一晋级席位不能重复进入多桌')
                used_slots.add(slot)
        resolved=resolve_sources({'bracket':bracket},node)
        known=[identity for identity in resolved if identity is not None]
        if len(known)!=len(set(known)):raise ValueError('同桌四个席位不能指向同一选手')
        for identity in known:
            key=(node['round'],identity)
            if key in round_players:raise ValueError('同一选手不能在同轮重复出场')
            round_players.add(key)
    active = bracket.get('active_node', '')
    if not isinstance(active,str) or (active and active not in lookup):raise ValueError('当前淘汰桌无效')
    return bracket


def generate(roster, planned_games=1, advance_count=2):
    roster = [player for player in roster if not player.get('hidden')]
    count = len(roster)
    if count < 4 or count % 4:raise ValueError('快捷排程需要4的倍数名选手；其他规模可手动编排四人桌')
    sources = [{'player':p['id']} for p in roster]
    nodes = []; round_number = 1
    while sources:
        if len(sources) % 4:raise ValueError('当前人数与晋级数无法组成完整四人桌，请选择其他晋级数或手动编排')
        next_sources = []; tables = len(sources) // 4
        for index in range(tables):
            node = dict(id=uuid.uuid4().hex, name=table_name(len(nodes)+1),
                round=round_number, sources=sources[index*4:index*4+4], planned_games=planned_games,
                advance_count=advance_count, status='pending', winners=[])
            nodes.append(node)
            next_sources += [{'node':node['id'],'place':n} for n in range(1,advance_count+1)]
        if tables == 1:break
        if len(next_sources) >= len(sources):raise ValueError('晋级人数必须少于参赛人数')
        sources = next_sources; round_number += 1
    return {'nodes':nodes,'active_node':''}


def resolve_sources(event, node):
    lookup = {n['id']:n for n in event['bracket']['nodes']}; players = []
    for index,source in enumerate(node['sources']):
        manual=node.get('source_overrides',{}).get(str(index))
        if manual:players.append(manual)
        elif 'player' in source:players.append(source['player'])
        else:
            parent = lookup[source['node']]
            winners = parent.get('winners', []) if parent['status']=='complete' else []
            players.append(winners[source['place']-1] if len(winners)>=source['place'] else None)
    return players


def node_matches(state, node_id):
    from ranking_reports import confirmed_matches
    return [m for m in confirmed_matches(state) if m.get('bracket_node_id') == node_id]


def node_results(state, event, node):
    roster = {p['id']:p for p in state['broadcast']['roster']}
    ids = resolve_sources(event,node)
    rows = {identity:dict(id=identity, name=roster[identity]['name'], points=0.) for identity in ids if identity in roster}
    ledger = ScoreTimeline(rows.values())
    matches = node_matches(state,node['id'])
    for match in matches:
        if {r['id'] for r in match['results']} != set(ids):raise ValueError('本桌历史参赛人与赛程来源不一致')
        ledger.record(match if match_seats(match) else dict(match, seats=ids))
    return ledger.ranked(rows.values())


def signature(state, node):
    matches = node_matches(state, node['id'])
    values = [(m['id'], [(r['id'], r['points']) for r in net_results(m)]) for m in matches]
    if node.get('ranking_rule') == 'arrival':
        values = [(value, match_seats(match)) for value, match in zip(values, matches)]
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()


def validate_sources(state,event,node,checked=None):
    """All entry paths must reject obsolete upstream winners, including re-entry."""
    checked=set() if checked is None else checked
    lookup={n['id']:n for n in event['bracket']['nodes']}
    for index,source in enumerate(node['sources']):
        if str(index) in node.get('source_overrides',{}):continue
        if 'node' not in source or source['node'] in checked:continue
        parent=lookup[source['node']];validate_sources(state,event,parent,checked)
        if parent['status']!='complete':raise ValueError('请先确认上游晋级')
        if len(node_matches(state,parent['id']))<parent['planned_games'] or parent.get('result_signature')!=signature(state,parent):
            raise ValueError('上游成绩有更正，请重新核查并确认晋级')
        rows = node_results(state, event, parent)
        top = rows[:parent['advance_count']]
        winners = parent.get('winners', [])
        if parent.get('ranking_rule') == 'arrival':
            valid = winners == [row['id'] for row in top]
        else:
            # Honor a legacy decision between tied entrants once confirmed.
            points = {row['id']: row['points'] for row in rows}
            valid = [points.get(identity) for identity in winners] == [row['points'] for row in top]
        if not valid:raise ValueError('上游同分顺序或晋级名单有变更，请重新确认')
        checked.add(parent['id'])


def save_node(store,node,additional_players=()):
    """Publish names, manual entrants and a schedule together after persistence."""
    from event_metadata import validate_event,now
    import tournament
    model=store.model;before=copy.deepcopy(model.state);state=copy.deepcopy(before)
    event=copy.deepcopy(model.event);node=copy.deepcopy(node)
    if event['kind']!='individual_knockout':raise ValueError('请在个人淘汰赛事中编排晋级桌')
    old=next((value for value in event['bracket']['nodes'] if value['id']==node['id']),None)
    if old and (old['status']!='pending' or node_matches(before,old['id'])):
        if {key:value for key,value in old.items() if key!='name'}!={key:value for key,value in node.items() if key!='name'}:
            raise ValueError('已开赛桌仅可修改桌名，参赛者和赛程结构保留')
    profiles=copy.deepcopy(state['broadcast']['roster']);known={profile['id']:profile for profile in profiles}
    for profile in additional_players:
        if profile['id'] in known:
            if known[profile['id']]!=profile:raise ValueError('新增选手编号与已有名册冲突')
        else:profiles.append(copy.deepcopy(profile));known[profile['id']]=profile
    tournament.configure(state,{'roster':profiles})
    if old:event['bracket']['nodes']=[node if value['id']==node['id'] else value for value in event['bracket']['nodes']]
    else:event['bracket']['nodes'].append(node)
    event['updated_at']=now();validate_event(event,state);model.engine.validate(state)
    from event_roster import require_visible_seats
    existing = set(resolve_sources(model.event, old)) if old else set()
    require_visible_seats(state, set(resolve_sources(event, node)) - existing)
    model._save_snapshot(state,model.settings,model.last_info,event)
    model.state,model.event=state,event;model.undo.clear();model.redo.clear()
    model.changed.emit(before,state,'event_setup');model.display_changed.emit()
    return node['id']


def preview_advancement(state, event, node_id):
    node = next(n for n in event['bracket']['nodes'] if n['id']==node_id)
    validate_sources(state,event,node)
    if len(node_matches(state,node_id)) < node['planned_games']:raise ValueError('请先完成并确认本桌计划的全部半庄')
    rows = node_results(state,event,node)
    if len(rows)!=4:raise ValueError('四个席位尚未确定')
    return rows[:node['advance_count']]


def confirm_advancement(store, node_id):
    model = store.model; event = copy.deepcopy(model.event); bracket = event['bracket']
    node = next(n for n in bracket['nodes'] if n['id']==node_id)
    winners = [r['id'] for r in preview_advancement(model.state,event,node_id)]
    if node.get('winners') and winners != node['winners']:
        children = [n for n in bracket['nodes'] if any(s.get('node')==node_id and str(index) not in n.get('source_overrides',{}) for index,s in enumerate(n['sources']))]
        if any(n['status']!='pending' or node_matches(model.state,n['id']) for n in children):
            raise ValueError('下游桌已经开赛，不能更改上游晋级名单；请先核查改判记录')
    node.update(status='complete', winners=winners, ranking_rule='arrival')
    node['result_signature'] = signature(model.state, node)
    store.update(bracket=bracket)
    return winners


def start_node(store, node_id):
    model = store.model; event = model.event
    node = next(n for n in event['bracket']['nodes'] if n['id']==node_id)
    if node['status']=='complete':raise ValueError('本桌已经确认晋级')
    if model.state['broadcast'].get('active_node_id')==node_id and model.state['phase']!='finished':return
    if len(node_matches(model.state,node_id)) >= node['planned_games']:raise ValueError('本桌半庄已打完，请预览并确认晋级')
    ids = resolve_sources(event,node)
    if None in ids or len(set(ids)) != 4:raise ValueError('需先确认上游晋级，凑齐四名不同选手')
    validate_sources(model.state,event,node)
    from match_service import require_completed_match
    require_completed_match(model.state)
    event = copy.deepcopy(event)
    bracket = event['bracket']; bracket['active_node'] = node_id
    next(n for n in bracket['nodes'] if n['id']==node_id)['status']='running'
    from event_metadata import now
    event['updated_at'] = now()
    model.matches.start_scheduled(ids, node_id, node['advance_count'], event)


def render_bracket(state, event):
    """Compatibility facade; scheduling itself has no drawing dependency."""
    from bracket_artwork import render_bracket as render
    return render(state, event)
