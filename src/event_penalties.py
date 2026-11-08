"""Half-game penalties are independent of the original scores and places."""
from __future__ import annotations

from datetime import datetime

from event_metadata import ID
from event_teams import assigned_team_id

MAX_PENALTY_PT = 1000000
MAX_REASON = 160
MAX_NOTE = 500


def penalty_targets(match, scope):
    if scope == 'player':
        return {row['id']: row.get('name') or row['id'] for row in match['results']}
    if scope == 'team':
        return {identity: row.get('team') or identity for row in match['results']
                if (identity := assigned_team_id(row))}
    return {}


def _valid_timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def validate_penalties(match):
    entries = match.get('penalties', [])
    if not isinstance(entries, list) or len(entries) > 1000:
        raise ValueError('半庄判罚记录格式无效')
    if not entries:
        return
    targets = {scope: penalty_targets(match, scope) for scope in ('player', 'team')}
    ids = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('半庄判罚记录格式无效')
        identity, scope, target = entry.get('id'), entry.get('scope'), entry.get('target')
        if not isinstance(identity, str) or not ID.fullmatch(identity) or identity in ids:
            raise ValueError('判罚编号重复或无效')
        ids.add(identity)
        if (not isinstance(scope, str) or scope not in targets
                or not isinstance(target, str) or target not in targets[scope]):
            raise ValueError('请选择该半庄的参赛选手或队伍')
        points = entry.get('points')
        if (type(points) not in (int, float) or not .1 <= points <= MAX_PENALTY_PT
                or abs(points*10 - round(points*10)) > 1e-7):
            raise ValueError('扣除 pt 必须为正数，最多一位小数')
        if not _valid_timestamp(entry.get('time')) or ('voided_at' in entry and not _valid_timestamp(entry['voided_at'])):
            raise ValueError('判罚时间格式无效')
        reason, note = entry.get('reason'), entry.get('note', '')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON:
            raise ValueError('请填写判罚原因（1 至 160 个字符）')
        if not isinstance(note, str) or len(note) > MAX_NOTE:
            raise ValueError('判罚备注最多 500 个字符')


def penalty_totals(match):
    players, teams = {}, {}
    for entry in match.get('penalties', []):
        if entry.get('voided_at'):
            continue
        target = entry['target']
        bucket = players if entry['scope'] == 'player' else teams
        bucket[target] = round(bucket.get(target, 0.) + entry['points'], 1)
    return players, teams


def net_results(match):
    players, _ = penalty_totals(match)
    return [dict(result, points=round(result['points']-players[result['id']], 4))
            if result['id'] in players else result for result in match['results']]
