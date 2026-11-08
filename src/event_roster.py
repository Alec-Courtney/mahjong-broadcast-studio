"""Safe roster changes independent of the live table's undo actions."""
from __future__ import annotations

import copy

from event_teams import assigned_team_id
from state_snapshots import copy_state


PLAYER_STATUSES = (
    ('参赛', 'active'), ('已晋级', 'qualified'), ('淘汰', 'eliminated'), ('未出场', 'inactive'),
)


def selected_profile(state, identity):
    return next((p for p in state['broadcast']['roster'] if p['id'] == identity), None)


def require_visible_seats(state, seats):
    hidden = {p['id'] for p in state['broadcast']['roster'] if p.get('hidden')}
    if hidden.intersection(seats):
        raise ValueError('阵容含有已屏蔽选手，请改选或先恢复该选手')


def removal_reasons(model, player):
    identity = player['id']
    event = model.event or {}
    config = model.state['broadcast']['config']
    reasons = []
    if assigned_team_id(player):
        reasons.append('已有队伍关联')
    baseline = player.get('result_baseline', {})
    has_stats = any(value for key in ('stats', 'baseline_stats') for value in player.get(key, {}).values())
    if (player.get('base_points') or player.get('games') or has_stats
            or any(baseline.get('counts', [])) or baseline.get('max_score') is not None):
        reasons.append('已有历史积分、场次或统计')
    if (identity in event.get('player_plans', {}) or config.get('qualification_focus') == identity
            or any(config.get(key, {}).get(identity) for key in ('manual_conditions', 'qualification_titles'))):
        reasons.append('已有个人场次或晋级设置')
    for node in event.get('bracket', {}).get('nodes', []):
        if (any(source.get('player') == identity for source in node['sources'])
                or identity in node.get('source_overrides', {}).values()
                or identity in node.get('winners', [])):
            reasons.append('已被淘汰赛程引用')
            break
    history = [state for state, _ in model.undo + model.redo]
    if model.last_transition:
        history.extend(model.last_transition[:2])
    if any(p['id'] == identity for state in (model.state, *history)
           for p in (*state['players'], *state['broadcast'].get('initial_players', []))):
        reasons.append('已关联当前席位或可撤销的席位')
    seen = set()
    for state in (model.state, *history):
        broadcast = state['broadcast']
        if any(p['id'] == identity for hand in broadcast['hands'] for p in hand['players']):
            reasons.append('已有逐局或半庄记录')
            break
        for match in broadcast['matches']:
            if id(match) in seen:
                continue
            seen.add(id(match))
            if (identity in match.get('seats', [])
                    or any(p['id'] == identity for key in ('results', 'initial_players', 'profiles')
                           for p in match.get(key, []))
                    or any(p['id'] == identity for hand in match['hands'] for p in hand['players'])):
                reasons.append('已有逐局或半庄记录')
                return reasons
    return reasons


def change_player(model, identity, action):
    """Commit one confirmed action and rebase it into table undo/redo."""
    player = selected_profile(model.state, identity)
    if player is None:
        raise ValueError('选手已从名册移除，请重新选择')
    if action == 'delete' and removal_reasons(model, player):
        raise ValueError('该选手已有赛事数据或关联，只能屏蔽')
    if action not in ('delete', 'hide', 'restore'):
        raise ValueError('选手操作无效')

    def apply(state):
        roster = state['broadcast']['roster']
        profile = selected_profile(state, identity)
        if action == 'delete':
            if profile is not None:
                roster.remove(profile)
        elif profile is not None:
            if action == 'hide':
                profile['hidden'] = True
            else:
                profile.pop('hidden', None)

    before = copy_state(model.state)
    after = copy_state(before)
    apply(after)
    undo, redo = copy.deepcopy((model.undo, model.redo))
    for state, _ in undo + redo:
        apply(state)
    transition = copy.deepcopy(model.last_transition)
    if transition:
        for state in transition[:2]:
            apply(state)
    import tournament
    tournament.validate_broadcast(after)
    model._publish(before, after, copy.deepcopy(model.settings), model.last_info,
                   undo, redo, transition, 'roster_management', display=True)
