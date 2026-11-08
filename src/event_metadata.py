"""Event metadata and validation, independent of storage and desktop windows."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

KINDS = {'team_league': '团队积分赛', 'individual_league': '个人积分赛',
         'individual_knockout': '个人淘汰赛'}
ID = re.compile(r'[0-9a-f]{32}')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def new_event(name, kind='individual_league', identity=None):
    return dict(version=1, id=identity or uuid.uuid4().hex, name=name.strip(), kind=kind,
                created_at=now(), updated_at=now(), planned_games=0, qualification_count=0,
                player_plans={}, team_plans={}, team_names={}, team_baselines={},
                report_columns={}, report_headers={}, bracket={'nodes': [], 'active_node': ''})


def validate_event(event, state=None):
    from tournament import _integer, _finite
    if not isinstance(event, dict) or event.get('version') != 1:
        raise ValueError('赛事配置版本无效')
    if not isinstance(event.get('id'), str) or not ID.fullmatch(event['id']):
        raise ValueError('赛事编号无效')
    if not isinstance(event.get('name'), str) or not event['name'].strip() or len(event['name']) > 100:
        raise ValueError('赛事名称须为1至100个字符')
    if event.get('kind') not in KINDS:
        raise ValueError('赛制无效')
    _integer(event.get('planned_games', 0), '计划场次', 0, 1000000)
    _integer(event.get('qualification_count', 0), '晋级名额', 0, 200)
    for key in ('player_plans', 'team_plans'):
        values = event.get(key, {})
        if not isinstance(values, dict) or len(values) > 1000:
            raise ValueError('参赛场次配置无效')
        for identity, value in values.items():
            if not isinstance(identity, str):
                raise ValueError('参赛场次编号无效')
            _integer(value, '计划场次', 0, 1000000)
    names = event.get('team_names', {})
    if not isinstance(names, dict) or any(not isinstance(k, str) or not isinstance(v, list)
        or len(v) != 2 or any(not isinstance(t, str) or len(t) > 80 for t in v) for k, v in names.items()):
        raise ValueError('队伍双行名称无效')
    baselines = event.get('team_baselines', {})
    if not isinstance(baselines, dict):
        raise ValueError('队伍历史数据无效')
    for item in baselines.values():
        if not isinstance(item, dict):
            raise ValueError('队伍历史数据无效')
        if 'points' in item:_finite(item['points'], '队伍历史积分')
        if 'games' in item:_integer(item['games'], '队伍历史场次', 0, 1000000)
    from ranking_schema import validate_columns, validate_baseline,validate_headers
    validate_columns(event.get('report_columns', {}))
    validate_headers(event.get('report_headers',{}))
    if state:
        for player in state['broadcast']['roster']:
            validate_baseline(player)
    from event_schedule import validate_bracket
    validate_bracket(event.get('bracket', {'nodes': [], 'active_node': ''}), state)
    return event


