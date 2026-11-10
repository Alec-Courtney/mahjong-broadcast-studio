"""Auditable tournament data, pure Python. Rates use settled recorded hands.
Payment averages include honba but exclude deposits; ankan is not an open call.
"""
from __future__ import annotations
import copy
import json
import math
import uuid
from datetime import datetime
from functools import lru_cache

from event_penalties import net_results, penalty_totals, validate_penalties
from event_teams import player_color
from ranking_order import ScoreTimeline, match_seats, team_identity

STAT_FIELDS = ('hands', 'calls', 'riichi', 'wins', 'deal_ins', 'win_points', 'deal_in_points')
STAT_DESCRIPTION = '按已记录的结算局统计；副露每局最多一次，暗杠不计；打点和铳点含本场、不含供托。'


def _finite(v, name, lo=-1000000000, hi=1000000000):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(name + '必须是有效范围内的数字')
    return v


def _integer(v, name, lo=0, hi=1000000000):
    _finite(v, name, lo, hi)
    if int(v) != v:
        raise ValueError(name + '必须是整数')
    return int(v)


def _label(s):
    return ('东' if s['roundIndex'] < 4 else '南') + str(s['roundIndex'] % 4 + 1) + '局' + (f" {s['honba']}本场" if s['honba'] else '')


def _profile(p):
    return dict(id=p['id'], name=p['name'], team=p.get('team', ''), team_id=p.get('team_id', p.get('team', '')),
                color=p.get('team_color', '#377e72'), photo=p.get('photo',''), team_logo=p.get('team_logo',''), base_points=0., games=0, status='active', stats={})


def ensure_broadcast(s):
    """Migrate without inventing hands preceding the recording start."""
    from sidebar_presets import default_sidebar_presets
    b = s.setdefault('broadcast', {})
    b.setdefault('version', 1)
    c = b.setdefault('config', {})
    defaults = dict(title=s.get('title', '赛事'), standings_mode='individual', event_mode='individual', settlement_enabled=True, settlement_hold=3., settlement_total_hold=3., sidebar_mode='qualification', sidebar_page=0, sidebar_page_size=10, qualification_slots=2,
                    qualification_final=False, qualification_mode='individual', qualification_calculation='auto', start_points=s['rules']['startPoints'], return_points=s['rules']['returnPoints'],
                    uma=s['rules']['uma'][:], oka_enabled=s['rules']['okaEnabled'], tie_break=s['rules']['tieBreak'],
                    qualification_tie_break='arrival', manual_conditions={},
                    qualification_layout='four', qualification_title='', qualification_titles={}, table_score_mode='auto', table_score_layout='compact',
                    sidebar_scope='all', sidebar_auto_page=True, sidebar_page_interval=10, sidebar_duration=15, sidebar_presets=default_sidebar_presets(),
                    term_favorites=['one_chance','nidouke','no_chance','matagi_suji','push_pull','kanzen_ishanten'],
                    term_button_columns=3, term_buttons=None, riichi_effect_enabled=True)
    for k, v in defaults.items():
        c.setdefault(k, copy.deepcopy(v))
    if c['qualification_tie_break'] == 'seed':
        c['qualification_tie_break'] = 'arrival'
    from postmatch_config import POSTMATCH_DEFAULTS
    for k, v in POSTMATCH_DEFAULTS.items():
        c.setdefault(k, copy.deepcopy(v))
    from corner_header import CORNER_DEFAULTS
    for k, v in CORNER_DEFAULTS.items():
        c.setdefault(k, copy.deepcopy(v))
    from opening_config import OPENING_DEFAULTS
    for k, v in OPENING_DEFAULTS.items():
        c.setdefault(k, copy.deepcopy(v))
    roster = b.setdefault('roster', [])
    ids = {p['id'] for p in roster}
    for p in s['players']:
        if p['id'] not in ids:
            roster.append(_profile(p))
    b.setdefault('hands', [])
    b.setdefault('matches', [])
    b.setdefault('current_hand', uuid.uuid4().hex)
    b.setdefault('initial_scores', [p['score'] for p in s['players']])
    b.setdefault('initial_label', '记录起点')
    b.setdefault('partial', bool(s.get('handSequence', 0) or s.get('roundIndex', 0) or s.get('lastResult')))
    b.setdefault('initial_players', copy.deepcopy(s['players']))
    return b


def validate_broadcast(s):
    b = ensure_broadcast(s)
    if b['version'] != 1:
        raise ValueError('不支持的赛事数据版本')
    c = b['config']
    from commentary_style import validate_commentary_style
    validate_commentary_style(c)
    from postmatch_config import validate_postmatch_config
    validate_postmatch_config(c)
    from corner_header import validate_corner_config
    validate_corner_config(c)
    from opening_config import validate_opening_config
    validate_opening_config(c)
    if not isinstance(c['riichi_effect_enabled'],bool):raise ValueError('立直特效开关格式无效')
    baselines=c.get('team_baselines',{})
    if not isinstance(baselines,dict):raise ValueError('队伍历史基数格式无效')
    for identity,baseline in baselines.items():
        if not isinstance(identity,str) or not isinstance(baseline,dict):raise ValueError('队伍历史基数格式无效')
        if 'points' in baseline:_finite(baseline['points'],'队伍历史积分')
        if 'games' in baseline:_integer(baseline['games'],'队伍历史场次',0,1000000)
        for key in ('name','color'):
            if key in baseline and (not isinstance(baseline[key],str) or len(baseline[key])>200):raise ValueError('队伍历史资料格式无效')
    for k in ('standings_mode', 'qualification_mode', 'event_mode'):
        if c[k] not in ('individual', 'team'):
            raise ValueError('排名类型无效')
    if c['sidebar_mode'] not in ('qualification','team','individual'):
        raise ValueError('左侧内容类型无效')
    if not isinstance(c['settlement_enabled'],bool):raise ValueError('结算展示开关格式无效')
    for key in ('settlement_hold','settlement_total_hold'):_finite(c[key],'结算展示时长',1,30)
    _integer(c['sidebar_page'],'侧栏页码',0,100000)
    _integer(c['sidebar_page_size'],'侧栏每页数量',2,10)
    if c['sidebar_scope'] not in ('all','current'):raise ValueError('侧栏范围无效')
    if not isinstance(c['sidebar_auto_page'],bool):raise ValueError('侧栏自动换页开关格式无效')
    _integer(c['sidebar_page_interval'],'侧栏换页间隔',5,60)
    _integer(c['sidebar_duration'],'侧栏展示时长',0,3600)
    from sidebar_presets import validate_sidebar_presets
    validate_sidebar_presets(c['sidebar_presets'])
    if c.get('qualification_calculation','auto') not in ('auto','manual'):
        raise ValueError('条件计算模式无效')
    if c['qualification_layout'] not in ('four','sidebar'):
        raise ValueError('条件版式无效')
    if c['table_score_mode'] not in ('auto','first_game','cumulative'):
        raise ValueError('上方积分口径无效')
    if c['table_score_layout'] not in ('compact','columns'):
        raise ValueError('上方积分版式无效')
    if not isinstance(c['qualification_title'],str) or len(c['qualification_title'])>80 or any(char in c['qualification_title'] for char in '\r\n'):
        raise ValueError('条件标题须为80字以内的单行文字')
    titles=c['qualification_titles']
    if not isinstance(titles,dict) or len(titles)>200 or any(not isinstance(key,str) or not isinstance(value,str) or len(value)>80 or any(char in value for char in '\r\n') for key,value in titles.items()):
        raise ValueError('选手条件标题格式无效')
    for key in ('title','commentary_title','commentary_body'):
        if key in c and (not isinstance(c[key],str) or len(c[key])>10000):raise ValueError('显示文字格式无效')
    for key,allowed in [('chart_mode',('scores','rank','ranks')),('statistics_scope',('match','all')),('statistics_view',('rates','result'))]:
        if key in c and c[key] not in allowed:raise ValueError('显示选项无效')
    for key in ('standings_page','statistics_page','commentary_duration'):
        if key in c:_integer(c[key],key,0,100000)
    if 'standings_page_size' in c:_integer(c['standings_page_size'],'每页名次',1,200)
    if 'hud_opacity' in c:_finite(c['hud_opacity'],'透明度',0,1)
    for key in ('standings_live','show_match_title','commentary_builtin'):
        if key in c and not isinstance(c[key],bool):raise ValueError('显示开关格式无效')
    if 'custom_terms' in c and (not isinstance(c['custom_terms'],list) or any(not isinstance(t,list) or len(t)!=2 or any(not isinstance(v,str) for v in t) for t in c['custom_terms'])):raise ValueError('自定义术语格式无效')
    favorites=c.get('term_favorites',[])
    if (not isinstance(favorites,list) or len(favorites)>12
            or any(not isinstance(value,str) or not value.strip() or len(value)>160 for value in favorites)
            or len(set(favorites))!=len(favorites)):
        raise ValueError('常用术语格式无效')
    _integer(c['term_button_columns'], '术语按钮列数', 1, 6)
    buttons=c['term_buttons']
    if buttons is not None:
        from glossary import get_term
        if not isinstance(buttons,list) or len(buttons)>12:raise ValueError('术语按钮布局无效')
        custom={'custom:'+entry[0] for entry in c.get('custom_terms',[])}
        slots,refs=set(),set()
        for item in buttons:
            if not isinstance(item,dict):raise ValueError('术语按钮布局无效')
            slot=_integer(item.get('slot'), '术语按钮位置', 0, 11)
            ref=item.get('ref');label=item.get('label','')
            if not isinstance(ref,str) or len(ref)>160 or not (get_term(ref) or ref in custom):
                raise ValueError('术语按钮对应词条不存在')
            if not isinstance(label,str) or len(label)>80 or any(char in label for char in '\r\n'):
                raise ValueError('术语按钮名称无效')
            if slot in slots or ref in refs:raise ValueError('术语按钮位置或词条重复')
            slots.add(slot);refs.add(ref)
    _integer(c['qualification_slots'], '晋级名额', 1, 200)
    _integer(c['start_points'], '新半庄起点',100,1000000000)
    if c['start_points']%100:raise ValueError('新半庄起点须为百点整数')
    _integer(c['return_points'], '精算原点')
    if c['return_points'] % 100:
        raise ValueError('精算原点须为百点整数')
    if not isinstance(c['uma'], list) or len(c['uma']) != 4:
        raise ValueError('顺位分须为四个数字')
    for v in c['uma']:
        _finite(v, '顺位分', -100000, 100000)
        if abs(v * 10 - round(v * 10)) > 1e-7:
            raise ValueError('顺位分最多一位小数')
    if c['tie_break'] not in ('initial_seat', 'share') or c['qualification_tie_break'] not in ('arrival', 'strict'):
        raise ValueError('同分规则无效')
    for k in ('oka_enabled', 'qualification_final'):
        if not isinstance(c[k], bool):
            raise ValueError('赛事开关须为布尔值')
    if c['oka_enabled'] and abs(sum(c['uma'])) > 1e-7:
        raise ValueError('起点奖励与不归零的顺位分不能同时启用')
    if not isinstance(c['manual_conditions'], dict) or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 2000 for k, v in c['manual_conditions'].items()):
        raise ValueError('手动条件格式无效')
    if not isinstance(b['roster'], list) or not 4 <= len(b['roster']) <= 200:
        raise ValueError('赛事名册须为4至200人')
    if 'teams' in b:
        from event_teams import validate_teams
        validate_teams(b['teams'])
    ids = set()
    for p in b['roster']:
        if not isinstance(p, dict) or not isinstance(p.get('id'), str) or not p['id'] or p['id'] in ids:
            raise ValueError('名册选手ID必须非空且唯一')
        ids.add(p['id'])
        if not isinstance(p.get('name'), str) or not p['name'].strip() or len(p['name']) > 80:
            raise ValueError('选手姓名无效')
        for k in ('team', 'team_id', 'color', 'photo', 'team_logo'):
            if k in p and (not isinstance(p[k], str) or len(p[k]) > 200):
                raise ValueError('名册字段格式无效')
        _finite(p.get('base_points', 0), '基准积分')
        _integer(p.get('games', 0), '场次', 0, 1000000)
        if not isinstance(p.get('hidden', False), bool):
            raise ValueError('选手屏蔽状态无效')
        if not isinstance(p.get('follow_team_color', False), bool):
            raise ValueError('选手颜色来源无效')
        if p.get('status', 'active') not in ('active', 'qualified', 'eliminated', 'inactive'):
            raise ValueError('选手状态无效')
        stats = p.get('stats', p.get('baseline_stats', {}))
        if not isinstance(stats, dict):
            raise ValueError('统计基数格式无效')
        for k in STAT_FIELDS:
            _integer(stats.get(k, 0), '统计基数')
        if any(stats.get(k, 0) > stats.get('hands', 0) for k in ('calls', 'riichi', 'wins', 'deal_ins')):
            raise ValueError('副露、立直、和牌或放铳局数不能超过总局数')
    if 'qualification_focus' in c and (not isinstance(c['qualification_focus'],str) or (c['qualification_focus'] and c['qualification_focus'] not in ids)):
        raise ValueError('条件焦点选手无效')
    if not set(p['id'] for p in s['players']).issubset(ids):
        raise ValueError('当前座位不在赛事名册中')
    if not isinstance(b.get('initial_players'),list) or len(b['initial_players'])!=4 or any(not isinstance(p,dict) or not isinstance(p.get('id'),str) or not isinstance(p.get('name'),str) for p in b['initial_players']):
        raise ValueError('记录起点选手格式无效')
    if not isinstance(b.get('initial_label'),str) or not isinstance(b.get('partial'),bool):
        raise ValueError('记录起点格式无效')
    if not isinstance(b['initial_scores'], list) or len(b['initial_scores']) != 4:
        raise ValueError('赛事起始点数格式无效')
    for v in b['initial_scores']:
        _integer(v, '起始点数', -1000000000)
    if not isinstance(b['current_hand'], str) or not b['current_hand']:
        raise ValueError('本局标识无效')
    if not isinstance(b['hands'], list) or not isinstance(b['matches'], list) or len(b['hands']) > 10000 or len(b['matches']) > 10000:
        raise ValueError('赛事记录格式无效')
    if not isinstance(b.get('result_discarded',False),bool):
        raise ValueError('半庄成绩删除状态无效')
    mids = set()
    for m in b['matches']:
        if not isinstance(m, dict) or not isinstance(m.get('id'), str) or m['id'] in mids:
            raise ValueError('半庄记录ID重复或无效')
        mids.add(m['id'])
        if 'recorded_at' in m:
            try:
                value = m['recorded_at']
                if not isinstance(value, str) or len(value) > 64 or datetime.fromisoformat(value).utcoffset() is None:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError('半庄记录时间无效') from None
        references=getattr(m,'_validated_archive_references',None)
        if references is not None:
            if not references.issubset(ids):raise ValueError('半庄选手不在名册中')
            continue
        if not isinstance(m.get('committed'), bool) or not isinstance(m.get('hands'), list):
            raise ValueError('半庄归档格式无效')
        if not isinstance(m.get('results'), list) or len(m['results']) != 4:
            raise ValueError('半庄积分记录格式无效')
        for r in m['results']:
            if not isinstance(r, dict) or r.get('id') not in ids:
                raise ValueError('半庄选手不在名册中')
            _finite(r.get('points'), '半庄积分')
            _integer(r.get('rank'), '半庄名次', 1, 4)
        validate_penalties(m)
        _validate_hands(m['hands'],ids)
        from state_snapshots import FrozenDict
        if isinstance(m,FrozenDict):
            m._validated_archive_references=frozenset(
                [r['id'] for r in m['results']]+[p['id'] for h in m['hands'] for p in h['players']])
    _validate_hands(b['hands'],ids)
    return b


def _validate_hands(hands,ids):
    """Validate live hands, or an immutable archive once before caching it."""
    seen = set()
    for h in hands:
        if not isinstance(h, dict) or not isinstance(h.get('id'), str) or h['id'] in seen or h.get('kind') not in ('ron', 'tsumo', 'draw'):
            raise ValueError('逐局记录格式无效')
        seen.add(h['id'])
        if not isinstance(h.get('players'), list) or len(h['players']) != 4:
            raise ValueError('逐局选手记录格式无效')
        if len({p.get('id') for p in h['players']}) != 4:
            raise ValueError('逐局选手ID重复')
        for p in h['players']:
            if p.get('id') not in ids:
                raise ValueError('逐局选手不在名册中')
            for k in STAT_FIELDS:
                _integer(p.get(k, 0), '逐局统计')
        for k in ('before_scores', 'after_scores'):
            if not isinstance(h.get(k), list) or len(h[k]) != 4:
                raise ValueError('逐局点数格式无效')
            for score in h[k]:
                _integer(score, '逐局点数', -1000000000)
        if 'context' in h:
            from hand_history import validate_context
            validate_context(h['context'])
        if 'event' in h and (not isinstance(h['event'],dict) or h['event'].get('type') != h['kind']):
            raise ValueError('逐局结算事件格式无效')
        info=h.get('win_info')
        if info is not None and (not isinstance(info,dict) or not isinstance(info.get('yaku',[]),list)
                or any(not isinstance(name,str) for name in info.get('yaku',[]))):
            raise ValueError('逐局役种格式无效')
        if 'manual_deltas' in h:
            values=h['manual_deltas']
            if not isinstance(values,list) or len(values)!=4 or any(type(value) is not int or abs(value)>1000000000 or value%100 for value in values):
                raise ValueError('逐局手动收支格式无效')
            _integer(h.get('manual_pot_after'), '局后供托', 0)
            if h['manual_pot_after']%1000:raise ValueError('局后供托须为千点整数')


def sync_profiles(s):
    b = ensure_broadcast(s)
    roster = {p['id']: p for p in b['roster']}
    teams = b.get('teams', {})
    for p in s['players']:
        r = roster[p['id']]
        old_team = r.get('team', '')
        r.update(name=p['name'], team=p.get('team', ''), photo=p.get('photo',''))
        if 'team_logo' in p:r['team_logo']=p['team_logo']
        if p.get('team_id'):
            r['team_id'] = p['team_id']
        elif r.get('team') != old_team or not r.get('team_id'):
            r['team_id'] = r.get('team', '')
        if r.get('follow_team_color'):
            p['team_color'] = r['color'] = player_color(r, teams)
        else:
            r['color'] = p.get('team_color', r.get('color', '#377e72'))


def record_hand(s, *, context=None, event=None, win_info=None):
    b = ensure_broadcast(s)
    r = s.get('lastResult')
    if not r:
        return
    players = []
    for i, p in enumerate(s['players']):
        won, lost = r.get('winner') == i, r.get('kind') == 'ron' and r.get('loser') == i
        players.append(dict(id=p['id'], name=p['name'], team=p.get('team',''), team_id=p.get('team_id',''), photo=p.get('photo',''), team_logo=p.get('team_logo',''), color=p.get('team_color', '#377e72'), hands=1,
                            calls=int(any(m['kind'] != 'ankan' for m in p['melds'])), riichi=int(p['riichi']), wins=int(won), deal_ins=int(lost),
                            win_points=sum(x['amount'] for x in r.get('payments', []) if x['to'] == i) if won else 0,
                            deal_in_points=sum(x['amount'] for x in r.get('payments', []) if x['from'] == i) if lost else 0))
    record = dict(id=b['current_hand'], label=_label(s), round_index=s['roundIndex'], honba=s['honba'], kind=r['kind'],
                  before_scores=r['beforeScores'][:], after_scores=[p['score'] for p in s['players']], players=players, result=copy.deepcopy(r))
    index = next((i for i, h in enumerate(b['hands']) if h['id'] == record['id']), None)
    if index is None:
        b['hands'].append(record)
    else:
        for key in ('context','event','win_info','manual_deltas','manual_pot_after'):
            if key in b['hands'][index]:record[key]=copy.deepcopy(b['hands'][index][key])
        old_profiles={p['id']:p for p in b['hands'][index]['players']}
        for player in record['players']:
            old=old_profiles.get(player['id'],{})
            for key in ('name','team','team_id','photo','team_logo','color'):
                if key in old:player[key]=old[key]
                elif key=='team_logo':player.pop(key,None)
        b['hands'][index] = record
    if context is not None:
        record.update(context=copy.deepcopy(context),event=copy.deepcopy(event),win_info=copy.deepcopy(win_info))
        record.pop('manual_deltas',None);record.pop('manual_pot_after',None)
    snapshot=record.get('context')
    if snapshot is not None:
        record.update(round_index=snapshot['round_index'],honba=snapshot['honba'],
                      label=_label(dict(roundIndex=snapshot['round_index'],honba=snapshot['honba'])))
        for i,player in enumerate(record['players']):player['riichi']=int(snapshot['riichi'][i])


def on_update(before, after, kind):
    b = ensure_broadcast(after)
    sync_profiles(after)
    if not b['hands'] and kind not in ('riichi','ron','tsumo','draw','new_match'):
        changed_scores=any(p['score']!=q['score'] for p,q in zip(before['players'],after['players']))
        if changed_scores:
            b['initial_scores']=[p['score'] for p in after['players']]
            b['initial_players']=copy.deepcopy(after['players'])
            b['initial_label']='记录起点'
        if after['roundIndex'] or after['honba'] or (changed_scores and len(set(b['initial_scores']))>1):b['partial']=True
    if kind in ('ron','tsumo','draw') and not before.get('broadcast',{}).get('hands') and before.get('lastResult'):
        b['initial_scores']=after['lastResult']['beforeScores'][:];b['partial']=True
    if kind in ('next_hand', 'clear'):
        b['current_hand'] = uuid.uuid4().hex
    elif kind in ('ron', 'tsumo', 'draw'):
        record_hand(after)
    elif kind != 'hand_correction' and after.get('lastResult') and any(h['id'] == b['current_hand'] for h in b['hands']):
        record_hand(after)
    if kind not in ('new_match', 'clear') and any(m['id'] == after['id'] and m['committed'] for m in b['matches']):
        archive_match(after, committed=True)


def configure(s, patch):
    if not isinstance(patch, dict):
        raise ValueError('赛事配置必须是对象')
    patch = copy.deepcopy(patch)
    b = ensure_broadcast(s)
    seats = patch.pop('seats', None)
    if 'roster' in patch:
        roster = patch.pop('roster')
        if not isinstance(roster, list):
            raise ValueError('名册必须是列表')
        referenced = {p['id'] for h in b['hands'] for p in h['players']}
        referenced |= {p['id'] for m in b['matches'] for p in m['results']}
        if not referenced.issubset({p.get('id') for p in roster if isinstance(p, dict)}):
            raise ValueError('不能删除已有比赛记录的选手，请屏蔽该选手')
        b['roster'] = roster
    b['config'].update(patch)
    teams = b.get('teams', {})
    for profile in b['roster']:
        if profile.get('follow_team_color'):
            profile['color'] = player_color(profile, teams)
    if seats is not None:
        if not isinstance(seats, list) or len(seats) != 4 or len(set(seats)) != 4:
            raise ValueError('四个座位必须选择不同选手')
        if seats != [p['id'] for p in s['players']] and (b['hands'] or s.get('lastResult') or s.get('pot')
                or any(p.get('riichi') or p.get('melds') for p in s['players'])
                or any(m['id'] == s['id'] and m.get('penalties') for m in b['matches'])):
            raise ValueError('本半庄已记录对局，请先新建半庄再换人')
        roster = {p['id']: p for p in b['roster']}
        if any(i not in roster for i in seats):
            raise ValueError('座位选手不在名册中')
        from event_roster import require_visible_seats
        require_visible_seats(s, (identity for p, identity in zip(s['players'], seats) if p['id'] != identity))
        for p, selected in zip(s['players'], seats):
            r = roster[selected]
            p['photo']=r.get('photo',p.get('photo','') if p['id']==selected else '')
            if 'team_logo' in r:p['team_logo']=r['team_logo']
            else:p.pop('team_logo',None)
            p.update(id=selected, name=r['name'], team=r.get('team', ''), team_id=r.get('team_id', r.get('team', '')), team_color=r.get('color', '#377e72'))
        if not b['hands']:
            b['initial_players'] = copy.deepcopy(s['players'])
    else:
        roster = {p.get('id'): p for p in b['roster']}
        for p in s['players']:
            if p['id'] in roster:
                r = roster[p['id']]
                if 'photo' in r:p['photo']=r['photo']
                if 'team_logo' in r:p['team_logo']=r['team_logo']
                p.update(name=r['name'], team=r.get('team', ''), team_id=r.get('team_id', r.get('team', '')), team_color=r.get('color', '#377e72'))
    validate_broadcast(s)
    s['rules'].update(returnPoints=b['config']['return_points'],uma=b['config']['uma'][:],okaEnabled=b['config']['oka_enabled'],tieBreak=b['config']['tie_break'])


def _match_results(s, scores=None):
    if scores is None:
        booked=next((m for m in s.get('broadcast',{}).get('matches',[]) if m['id']==s['id'] and m.get('committed')),None)
        if booked and isinstance(booked.get('config'),dict):
            s=copy.copy(s);s['broadcast']=copy.copy(s['broadcast']);s['broadcast']['config']=booked['config']
            s['rules']=copy.copy(s['rules']);s['rules']['tieBreak']=booked['config']['tie_break']
    b = s.get('broadcast') or ensure_broadcast(s)
    c = b['config']
    current_scores = scores is None
    scores = scores if scores is not None else [p['score'] for p in s['players']]
    order = sorted(range(4), key=lambda i: (-scores[i], (i - s['initialDealer']) % 4))
    groups = []
    for pos, i in enumerate(order):
        if c['tie_break'] == 'share' and groups and scores[groups[-1][0][1]] == scores[i]:
            groups[-1].append((pos, i))
        else:
            groups.append([(pos, i)])
    if current_scores and s.get('phase')=='finished' and s.get('finalRanking') and c['tie_break']==s['rules']['tieBreak']:
        groups=[list(zip([n-1 for n in g['places']],g['players'])) for g in s['finalRanking']['groups']]
    results = []
    roster = {p['id']: p for p in b['roster']}
    oka = (c['return_points'] - s['rules']['startPoints']) * 4 // 100 if c['oka_enabled'] else 0
    for group in groups:
        n = len(group)
        uma_total = sum(round(c['uma'][pos] * 10) for pos, _ in group)
        umas = [uma_total // n] * n
        umas[0] += uma_total - sum(umas)
        okas = [oka // n] * n if group[0][0] == 0 else [0] * n
        if group[0][0] == 0:
            okas[0] += oka - sum(okas)
        for j, (_, i) in enumerate(group):
            p = s['players'][i]
            r = roster[p['id']]
            results.append(dict(id=p['id'], name=p['name'], team=r.get('team', ''), team_id=r.get('team_id') or r.get('team', ''),
                                color=r.get('color', '#377e72'), photo=p.get('photo',r.get('photo','')), team_logo=p.get('team_logo',r.get('team_logo','')), rank=group[0][0] + 1, score=scores[i],
                                points=round((scores[i] - c['return_points']) / 1000 + (umas[j] + okas[j]) / 10, 4)))
    return results


def archive_match(s, committed=False):
    b = ensure_broadcast(s)
    if committed:b.pop('result_discarded',None)
    snapshot = dict(id=s['id'], title=s.get('title', ''), committed=committed, hands=copy.deepcopy(b['hands']), results=_match_results(s),
                    seats=[p['id'] for p in s['players']], initial_scores=b['initial_scores'][:],
                    initial_players=copy.deepcopy(b['initial_players']), partial=b['partial'], config=copy.deepcopy(b['config']))
    if b.get('active_node_id'):snapshot['bracket_node_id']=b['active_node_id']
    index = next((i for i, m in enumerate(b['matches']) if m['id'] == s['id']), None)
    snapshot['profiles']=[dict(id=p['id'],name=p['name'],team=p.get('team',''),team_id=p.get('team_id',''),color=p.get('team_color','#377e72'),photo=p.get('photo',''),team_logo=p.get('team_logo','')) for p in s['players']]
    if index is None:
        if committed:
            from event_metadata import now
            snapshot['recorded_at'] = now()
        b['matches'].append(snapshot)
    else:
        previous=b['matches'][index]
        if 'recorded_at' in previous:
            snapshot['recorded_at'] = previous['recorded_at']
        elif committed and not previous.get('committed'):
            from event_metadata import now
            snapshot['recorded_at'] = now()
        if previous.get('committed') and previous.get('config'):snapshot['config']=copy.deepcopy(previous['config'])
        if previous.get('bracket_node_id'):snapshot['bracket_node_id']=previous['bracket_node_id']
        if 'penalties' in previous:snapshot['penalties']=previous['penalties']
        initial={p['id']:p for p in previous.get('initial_players',[])}
        legacy_profiles=[{key:result.get(key,initial.get(result['id'],{}).get(key,'')) for key in ('id','name','team','team_id','color','photo','team_logo')} for result in previous['results']]
        snapshot['profiles']=copy.deepcopy(previous.get('profiles') or legacy_profiles)
        profiles={p['id']:p for p in snapshot['profiles']}
        for result in snapshot['results']:
            for key in ('name','team','team_id','color','photo','team_logo'):
                if key in profiles.get(result['id'],{}):result[key]=profiles[result['id']][key]
                elif key=='team_logo':result.pop(key,None)
        snapshot['committed'] = committed or b['matches'][index]['committed']
        b['matches'][index] = snapshot
    return snapshot


def _standing_field(state, mode, matches):
    broadcast = state['broadcast']
    roster = broadcast['roster']
    if mode == 'individual':
        return {p['id']: dict(id=p['id'], name=p['name'], color=p.get('color', '#377e72'),
                             team=p.get('team', ''), team_id=p.get('team_id') or p.get('team', ''),
                             points=float(p.get('base_points', 0)), games=p.get('games', 0),
                             status=p.get('status', 'active'), hidden=p.get('hidden', False))
                for p in roster}

    teams = {}
    registered = broadcast.get('teams', {})
    hidden = {p['id'] for p in roster if p.get('hidden')}

    def team_for(player):
        identity = team_identity(player)
        if identity not in teams:
            registered_team = registered.get(identity, {})
            name = registered_team.get('name') or player.get('team') or player['name']
            color = registered_team.get('color') or player.get('color', '#377e72')
            teams[identity] = dict(id=identity, name=name, color=color,
                                   points=0., games=0, status=player.get('status', 'active'),
                                   hidden=identity.startswith('unassigned:') and player['id'] in hidden)
        return teams[identity]

    for player in roster:
        team = team_for(player)
        team['points'] += player.get('base_points', 0)
        team['games'] += player.get('games', 0)
        if player.get('status', 'active') == 'active':
            team['status'] = 'active'
    for match in matches:
        for result in match['results']:
            team_for(result)
    for identity, baseline in broadcast['config'].get('team_baselines', {}).items():
        name = baseline.get('name', identity)
        team = team_for(dict(id='baseline:'+identity, team_id=identity, team=name, name=name,
                             color=baseline.get('color', '#377e72')))
        for key in ('points', 'games'):
            if key in baseline:
                team[key] = baseline[key]
    return teams


def _score_ledger(field, matches, mode):
    ledger = ScoreTimeline(field.values())
    for match in matches:
        for identity in ledger.record(match, mode):
            field[identity]['games'] += 1
    return ledger


def _score_projection(state, scores, booked=None):
    return {'results': _match_results(state, scores), 'seats': [p['id'] for p in state['players']],
            'penalties': booked.get('penalties', []) if booked else []}


def _standings(state, mode, live, scores=None, *, include_hidden=False):
    from ranking_reports import confirmed_matches

    matches = confirmed_matches(state)
    current = next((match for match in matches if match['id'] == state['id']), None)
    field = _standing_field(state, mode, matches)
    ledger = _score_ledger(field, (match for match in matches if match is not current), mode)
    before = ledger.ranked(field.values(), include_hidden=include_hidden)
    previous = {row['id']: row['rank'] for row in before}
    if current:
        # A correction replaces this half in place, including when later
        # results from another table have already been recorded.
        results = _score_projection(state, scores, current) if live else current
        if matches[-1] is not current:
            field = _standing_field(state, mode, matches)
            ledger = _score_ledger(field, (results if m is current else m for m in matches), mode)
        else:
            for identity in ledger.record(results, mode):
                field[identity]['games'] += 1
        ranked = ledger.ranked(field.values(), include_hidden=include_hidden)
    elif live:
        ranked = ledger.ranked(field.values(), projection=_score_projection(state, scores),
                               mode=mode, include_hidden=include_hidden)
    else:
        ranked = before
    for row in ranked:
        rank = row['rank']
        row['previous_rank'] = previous[row['id']]
        row['movement'] = row['previous_rank'] - rank if rank is not None else 0
    return ranked


def _table_state(s):
    # Old files may be passed directly to these read APIs, before Model migration.
    # Keep migration off the caller's object; normal imported states need no copy.
    if not s.get('broadcast'):
        s=copy.deepcopy(s);ensure_broadcast(s)
    return s


def standings(s, mode='individual', live=True, scores=None):
    """Ordinary standings are booked league points, never in-game projections.

    live/scores remain accepted for old callers but cannot add temporary pt.
    Current-half results enter only after both final settlement and confirmation.
    """
    if mode not in ('individual','team'):raise ValueError('排名类型无效')
    return _standings(_table_state(s),mode,False)


def _projected_standings(s, mode='individual', scores=None, *, include_hidden=False):
    """Explicit simulation path, reserved for qualification and result previews."""
    return _standings(_table_state(s), mode, True, scores, include_hidden=include_hidden)


def table_standings(s, mode='individual', live=True, scores=None):
    """Four fixed seat cards with ranks and points from the booked full field."""
    return _table_standings(s,mode,projected=False)


def _table_standings(s, mode='individual', projected=False, scores=None):
    """Four seat-ordered cards, each retaining its rank in the complete field.

    Shared teams deliberately occur once per occupied seat. match_points is the
    seated player's confirmed half-game pt (zero while unbooked). The private
    projected option is used only by explicit result previews, never live HUDs.
    """
    if mode not in ('individual','team'):raise ValueError('排名类型无效')
    s=_table_state(s)
    roster={r['id']:r for r in s['broadcast']['roster']}
    all_rows = {r['id']: r for r in _standings(s, mode, projected, scores, include_hidden=True)}
    committed=next((m for m in s['broadcast']['matches'] if m['id']==s['id'] and m['committed'] and s.get('phase')=='finished'),None)
    record = _score_projection(s, scores, committed) if projected else committed
    match = {r['id']: r for r in net_results(record)} if record else {}
    _, team_penalties = penalty_totals(record) if record else ({}, {})
    team_points = {identity: -points for identity, points in team_penalties.items()}
    for result in match.values():
        tid=result.get('team_id') or result.get('team') or 'unassigned:'+result['id']
        team_points[tid]=team_points.get(tid,0.)+result['points']
    rows=[]
    for seat,player in enumerate(s['players']):
        profile=roster[player['id']]
        tid=profile.get('team_id') or profile.get('team') or 'unassigned:'+player['id']
        key=player['id'] if mode=='individual' else tid
        row=copy.deepcopy(all_rows[key])
        row.update(seat=seat,player_id=player['id'],player_name=player['name'],team=profile.get('team',''),team_id=tid,
                   photo=player.get('photo',profile.get('photo','')),team_logo=player.get('team_logo',profile.get('team_logo','')),player_color=profile.get('color','#377e72'),
                   match_points=match.get(player['id'],{}).get('points',0.),match_rank=match.get(player['id'],{}).get('rank'),
                   team_match_points=round(team_points.get(tid,0.),4),current_table=True)
        rows.append(row)
    return rows


def _hand_start_state(before, after):
    """Recover the recorded start of this hand, including pre-existing deposits.

    A settlement's beforeScores already excludes current-hand riichi payments.
    Prefer the preceding recorded result (excluding a replaced current result),
    then the recording's initial scores. Never reconstruct stakes merely from
    display flags: operators can edit riichi indicators after settlement.
    """
    start=copy.deepcopy(before)
    b=start['broadcast'];token=after.get('broadcast',{}).get('current_hand',b.get('current_hand'))
    hands=b.get('hands',[])
    index=next((i for i,h in enumerate(hands) if h['id']==token),len(hands))
    previous=hands[index-1] if index else None
    if previous:
        recorded={p['id']:score for p,score in zip(previous['players'],previous['after_scores'])}
        source='previous_hand'
    else:
        recorded={p['id']:score for p,score in zip(b.get('initial_players',before['players']),b['initial_scores'])}
        source='recording_start' if b.get('partial') else 'match_start'
    scores=[recorded.get(p['id'],p['score']) for p in before['players']]
    event=after.get('lastResult') or {}
    payment_scores=event.get('beforeScores',[p['score'] for p in before['players']])
    payment_pot=event.get('potBefore',before.get('pot',0))
    # The total already contains the carried sticks. Moving current-hand
    # deposits back into their owners' starting scores leaves only that carry.
    pot=payment_pot+sum(payment_scores)-sum(scores)
    if pot<0 or pot%1000:
        # A partial imported recording may not preserve an accounting origin.
        # Its observed pot is safer than inventing an invalid negative stake.
        pot=previous.get('result',{}).get('potAfter',before.get('pot',0)) if previous else before.get('pot',0)
    for player,score in zip(start['players'],scores):player['score']=score
    start.update(phase='playing',lastResult=None,finalRanking=None,pot=pot,accountingTotal=sum(scores)+pot)
    return start,source


def settlement_summary(before, after, mode='team'):
    """Read-only pt changes from this hand's recorded start to its settlement.

    Both the personal and league stages include riichi paid during this hand.
    Recorded hands and a committed copy of this same half-game are never added
    a second time. Corrections retain the same hand-start comparison baseline.
    """
    before,after=_table_state(before),_table_state(after)
    before,start_source=_hand_start_state(before,after)
    old_match={r['id']:r for r in _match_results(before)}
    new_match={r['id']:r for r in _match_results(after)}
    old_rows={r['player_id']:r for r in _table_standings(before,mode,projected=True)}
    new_rows=_table_standings(after,mode,projected=True)
    before_players={r['id']:r for r in before['players']}
    players,rows=[],[]
    def changes(row,old_points,points,old_rank,rank):
        delta=round(points-old_points,4)
        movement = old_rank-rank if old_rank is not None and rank is not None else 0
        row.update(before_points=old_points,points=points,delta=delta,before_rank=old_rank,rank=rank,
                   points_before=old_points,points_after=points,points_delta=delta,rank_before=old_rank,rank_after=rank,
                   rank_delta=movement,movement=movement,previous_rank=old_rank)
        return row
    for table_row in new_rows:
        pid=table_row['player_id'];current=new_match[pid];previous=old_match.get(pid,current)
        original=old_rows.get(pid,table_row)
        base={k:copy.deepcopy(table_row[k]) for k in ('seat','player_id','player_name','team','team_id','color','photo','team_logo')}
        base.update(id=pid,name=table_row['player_name'],color=table_row['player_color'])
        player=changes(base,previous['points'],current['points'],previous['rank'],current['rank'])
        player.update(score_before=before_players.get(pid,after['players'][table_row['seat']])['score'],score_after=after['players'][table_row['seat']]['score'])
        player['score_delta']=player['score_after']-player['score_before']
        players.append(player)
        row=changes(copy.deepcopy(table_row),original['points'],table_row['points'],original['rank'],table_row['rank'])
        row.update(match_points_before=previous['points'],match_points_after=current['points'],match_points_delta=player['delta'])
        rows.append(row)
    highlighted=list(dict.fromkeys(r['id'] for r in rows))
    all_rows=_projected_standings(after,mode)
    for row in all_rows:row['current_table']=row['id'] in highlighted
    return dict(mode=mode,players=players,standings=rows,all_standings=all_rows,highlight_ids=highlighted,
                hand_start_scores=[p['score'] for p in before['players']],hand_start_pot=before.get('pot',0),
                hand_end_pot=after.get('pot',0),hand_start_source=start_source)


def final_settlement_summary(s, mode='team'):
    """Final booked half-game pt and the league change caused by that booking.

    No score projection or ledger mutation occurs here. Until the current
    half-game is finished AND committed there is no final presentation payload.
    """
    s=_table_state(s)
    current=next((m for m in s['broadcast']['matches'] if m['id']==s['id'] and m['committed']),None)
    if s.get('phase')!='finished' or current is None:return None
    opening=copy.copy(s);opening['broadcast']=copy.copy(s['broadcast'])
    opening['broadcast']['matches']=[m for m in s['broadcast']['matches'] if m['id']!=s['id']]
    before_rows={r['player_id']:r for r in table_standings(opening,mode)}
    final_rows=table_standings(s,mode)
    results={r['id']:r for r in net_results(current)}
    initial={p['id']:score for p,score in zip(current.get('initial_players',s['broadcast']['initial_players']),current.get('initial_scores',s['broadcast']['initial_scores']))}
    players,rows=[],[]
    def change(row,before,after,old_rank,new_rank):
        delta=round(after-before,4)
        movement = old_rank-new_rank if old_rank is not None and new_rank is not None else 0
        row.update(before_points=before,points=after,delta=delta,before_rank=old_rank,rank=new_rank,
                   points_before=before,points_after=after,points_delta=delta,rank_before=old_rank,rank_after=new_rank,
                   rank_delta=movement,movement=movement,previous_rank=old_rank)
        return row
    for final in final_rows:
        pid=final['player_id'];result=results[pid];old=before_rows[pid]
        identity={key:copy.deepcopy(final[key]) for key in ('seat','player_id','player_name','team','team_id','photo','team_logo')}
        identity.update(id=pid,name=final['player_name'],color=final['player_color'])
        player=change(identity,0.,result['points'],result['rank'],result['rank'])
        player.update(score_before=initial.get(pid,s['players'][final['seat']]['score']),score_after=result.get('score',s['players'][final['seat']]['score']))
        player['score_delta']=player['score_after']-player['score_before'];players.append(player)
        row=change(copy.deepcopy(final),old['points'],final['points'],old['rank'],final['rank'])
        row.update(match_points_before=0.,match_points_after=result['points'],match_points_delta=result['points'])
        rows.append(row)
    highlight_ids=list(dict.fromkeys(r['id'] for r in rows));all_rows=standings(s,mode)
    for row in all_rows:row['current_table']=row['id'] in highlight_ids
    return dict(scope='match',committed=True,mode=mode,players=players,standings=rows,all_standings=all_rows,highlight_ids=highlight_ids)


def statistics(s, scope='all'):
    b = s.get('broadcast') or ensure_broadcast(s)
    current = {p['id'] for p in s['players']}
    rows = {}
    for p in b['roster']:
        if (scope == 'match' and p['id'] not in current) or (scope != 'match' and p.get('hidden')):
            continue
        baseline = p.get('stats', p.get('baseline_stats', {})) if scope != 'match' else {}
        rows[p['id']] = dict(id=p['id'], name=p['name'], color=p.get('color', '#377e72'), team=p.get('team', ''), **{k: baseline.get(k, 0) for k in STAT_FIELDS})
    hands = list(b['hands']) if scope == 'match' or not b.get('result_discarded') else []
    if scope != 'match':
        for m in b['matches']:
            if m['id'] != s['id']:
                hands.extend(m['hands'])
    for h in hands:
        for p in h['players']:
            if p['id'] in rows:
                for k in STAT_FIELDS:
                    rows[p['id']][k] += p.get(k, 0)
    for r in rows.values():
        for key, count in [('call_rate', 'calls'), ('riichi_rate', 'riichi'), ('win_rate', 'wins'), ('deal_in_rate', 'deal_ins')]:
            r[key] = r[count] / r['hands'] if r['hands'] else None
        r['average_win'] = r['win_points'] / r['wins'] if r['wins'] else None
        r['average_deal_in'] = r['deal_in_points'] / r['deal_ins'] if r['deal_ins'] else None
    return list(rows.values())


def progression(s):
    b = s.get('broadcast') or ensure_broadcast(s)
    players = b.get('initial_players') or s['players']
    snapshots = [b['initial_scores']] + [h['after_scores'] for h in b['hands']]
    ranks = [[1 + sum(other > score for other in scores) for score in scores] for scores in snapshots]
    current = {p['id']: p for p in s['players']}
    return dict(labels=[b['initial_label']] + [h['label'] for h in b['hands']], hands=len(b['hands']), draws=sum(h['kind'] == 'draw' for h in b['hands']), partial=b['partial'],
                series=[dict(id=p['id'], name=current.get(p['id'], p)['name'], color=current.get(p['id'], p).get('team_color', '#377e72'),
                             scores=[v[i] for v in snapshots], ranks=[r[i] for r in ranks]) for i, p in enumerate(players)])


def _ceil100(v):
    return math.ceil(v / 100) * 100


def _payment_options(s, winner, kind):
    options, bases = {}, set()
    for han in range(1, 14):
        for fu in [20, 25] + list(range(30, 111, 10)):
            if fu == 20 and (kind == 'ron' or han < 2) or fu == 25 and han < (3 if kind == 'tsumo' else 2):
                continue
            if fu == 110 and han == 1:
                continue
            raw = fu * 2 ** (han + 2)
            if han >= 13 and s['rules']['kazoeYakuman']:
                base = 8000
            elif han >= 11:
                base = 6000
            elif han >= 8:
                base = 4000
            elif han >= 6:
                base = 3000
            elif han >= 5 or raw >= 2000 or (s['rules']['kiriageMangan'] and raw == 1920):
                base = 2000
            else:
                base = raw
            bases.add(base)
    bases.update([8000, 16000, 24000, 32000])
    for base in bases:
        yakuman = int(base // 8000) if base >= 8000 and base % 8000 == 0 else 0
        yakuman_text = ('单倍役满' if yakuman == 1 else f'{yakuman}倍役满') if yakuman else ''
        if kind == 'ron':
            v = _ceil100(base * (6 if winner == s['dealer'] else 4))
            options[(v,)] = dict(base=v, text=yakuman_text or f'{v:,}', total=v, yakuman=yakuman)
        else:
            payments = [_ceil100(base * (2 if winner == s['dealer'] or i == s['dealer'] else 1)) if i != winner else 0 for i in range(4)]
            actual = [v for v in payments if v]
            text = f'{actual[0]:,} ALL' if winner == s['dealer'] else f'{min(actual):,} / {max(actual):,}'
            options[tuple(payments)] = dict(payments=payments, text=yakuman_text or text, total=sum(payments), yakuman=yakuman)
    return sorted(options.values(), key=lambda o: (o['total'], o['text']))


def _qualification_eligible(row):
    return not row.get('hidden') and row['status'] not in ('eliminated', 'inactive')


def _qualifies(rows, target, slots, strict):
    eligible = [r for r in rows if _qualification_eligible(r)]
    i = next((i for i, r in enumerate(eligible) if r['id'] == target), None)
    if i is None or i >= slots:
        return False
    if strict and len(eligible) > slots and abs(eligible[i]['points'] - eligible[slots]['points']) < 1e-7:
        return False
    return True


def qualification(s):
    b = s.get('broadcast') or ensure_broadcast(s)
    roster=b['roster'];matches=b['matches'];config=copy.deepcopy(b['config'])
    if b.get('active_node_id'):
        # A knockout table starts afresh each round. Unrelated tables and prior
        # rounds cannot become opponents in its four-player qualification trial.
        ids={p['id'] for p in s['players']};roster=copy.deepcopy([p for p in roster if p['id'] in ids])
        for player in roster:player.update(base_points=0.,games=0,status='active')
        matches=[m for m in matches if m.get('bracket_node_id')==b['active_node_id']]
        config['qualification_mode']='individual'
    light = {k: copy.deepcopy(v) for k, v in s.items() if k in ('id', 'title', 'players', 'dealer', 'initialDealer', 'honba', 'pot', 'roundIndex', 'rules', 'phase', 'finalRanking')}
    light['broadcast'] = dict(version=1, config=config, roster=copy.deepcopy(roster), hands=[],
                             matches=[dict(id=m['id'], committed=m['committed'], results=m['results'],
                                           seats=match_seats(m), penalties=m.get('penalties', [])) for m in matches],
                             current_hand='projection', initial_scores=[p['score'] for p in s['players']], initial_players=[], partial=b['partial'], initial_label='记录起点')
    return copy.deepcopy(_qualification_cached(json.dumps(light, ensure_ascii=False, sort_keys=True)))


@lru_cache(maxsize=12)
def _qualification_cached(serialized):
    s = json.loads(serialized)
    b, c = s['broadcast'], s['broadcast']['config']
    mode, slots, strict = c.get('qualification_mode', 'individual'), c['qualification_slots'], c['qualification_tie_break'] == 'strict'
    now = _projected_standings(s, mode=mode, include_hidden=True)
    by_id, roster = {r['id']: r for r in now}, {r['id']: r for r in b['roster']}
    eligible = [r for r in now if _qualification_eligible(r)]
    cutoff = eligible[slots - 1]['points'] if len(eligible) >= slots else None
    if s.get('phase') == 'playing':
        from ranking_reports import confirmed_matches
        confirmed = confirmed_matches(s)
        field = _standing_field(s, mode, confirmed)
        ledger = _score_ledger(field, confirmed, mode)
    rows = []
    for i, p in enumerate(s['players']):
        rp = roster[p['id']]
        target = p['id'] if mode == 'individual' else (rp.get('team_id') or rp.get('team') or 'unassigned:' + p['id'])
        current = by_id[target]
        r = dict(id=p['id'], name=p['name'], color=rp.get('color', '#377e72'), rank=current['rank'], points=current['points'],
                 gap=max(0, round(cutoff - current['points'], 4)) if cutoff is not None else 0.,
                 status='inside' if _qualifies(now, target, slots, strict) else 'outside', scenarios=[], ron=[], minimum_tsumo=None, minimum_ron=[])
        if current.get('hidden'):
            r.update(condition='选手已屏蔽', manual=False, tsumo='', tsumo_count=0)
            rows.append(r)
            continue
        if s.get('phase') != 'playing':
            r.update(condition='本半庄已结束' if s.get('phase')=='finished' else '本局已结算',manual=False,tsumo='',tsumo_count=0)
            if c.get('qualification_calculation')=='manual':r.update(condition=c['manual_conditions'].get(p['id'],'').strip() or '待填写条件',manual=True)
            rows.append(r)
            continue
        for kind in ('tsumo', 'ron'):
            sources = [None] if kind == 'tsumo' else [j for j in range(4) if j != i]
            for source in sources:
                minimum = None
                for option in _payment_options(s, i, kind):
                    scores = [q['score'] for q in s['players']]
                    if kind == 'ron':
                        payment = option['base'] + s['honba'] * s['rules']['honbaRon']
                        scores[source] -= payment
                        scores[i] += payment
                        payments = [payment if j == source else 0 for j in range(4)]
                    else:
                        payments = [v + s['honba'] * s['rules']['honbaTsumoEach'] if j != i else 0 for j, v in enumerate(option['payments'])]
                        for j, amount in enumerate(payments):
                            scores[j] -= amount
                            scores[i] += amount
                    scores[i] += s['pot']
                    result = ledger.ranked(field.values(), projection=_score_projection(s, scores), mode=mode)
                    qualifies = _qualifies(result, target, slots, strict)
                    target_row = next(v for v in result if v['id'] == target)
                    scenario = dict(type=kind, **{'from': source}, name=s['players'][source]['name'] if source is not None else '自摸',
                                    payments=payments, total=sum(payments), base=option['total'], text=option['text'],
                                    qualifies=qualifies, rank=target_row['rank'], points=target_row['points'], yakuman=option.get('yakuman', 0))
                    if qualifies:
                        minimum=scenario
                        r['scenarios'].append(scenario)
                        break
                if kind == 'tsumo':
                    r['minimum_tsumo']=minimum
                    r['tsumo'] = minimum['text'] if minimum else '无满足档位'
                    r['tsumo_count'] = int(minimum is not None)
                else:
                    if minimum:r['minimum_ron'].append(minimum)
                    r['ron'].append(dict(**{'from': source}, name=s['players'][source]['name'], points=[minimum['base']] if minimum else [],
                                         text=minimum['text'] if minimum else '无满足档位'))
        r['manual'] = c.get('qualification_calculation','auto') == 'manual'
        r['condition'] = c['manual_conditions'].get(p['id'],'').strip() or '待填写条件' if r['manual'] else '自摸 ' + r['tsumo']
        rows.append(r)
    notes = ['按当前积分计算各和牌来源的最低满足档；支付档不含本场，计算已加本场与供托。',
             '仅枚举和牌支付档（至四倍役满），不推断实际手牌；流局和他家和牌不在本表内。',
             '累计积分同分时，先达到者在前。同一半庄达到同分时按席位顺序。' if not strict else '晋级线同分视为待裁定，不标为已满足。']
    if not c.get('qualification_final'):
        notes.append('未声明为最终局：这是本局结束时的假设排名，不是最终晋级保证。')
    notes.append('庄家和牌可能连庄，后续对局仍会改变晋级条件。')
    summary=f'前{slots}{"队" if mode == "team" else "名"}晋级 · 本局结束试算'
    if s.get('phase')!='playing':
        summary=f'前{slots}{"队" if mode == "team" else "名"}晋级 · 已结算排名'
        notes=['当前局已结算，显示结算后的积分与排名；进入下一局后重新计算和牌条件。']
    return dict(summary=summary, rows=rows, notes=notes)


def match_results(state):
    return _match_results(state)
