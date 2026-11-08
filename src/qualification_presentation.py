"""Presentation of the current table's four independent qualification trials."""
from __future__ import annotations

import copy


MT_CARD_WIDTH = 432
MT_CARD_GAP = 24
FOUR_PANEL_SIZE = (4*MT_CARD_WIDTH+3*MT_CARD_GAP, 588)
SIDEBAR_SIZE = (400, 650)


def condition_label(value):
    """MT caption wording; keep the numeric trial result itself unchanged."""
    from i18n import tr
    text = str(value).strip()
    if not text or text == '无满足档位':
        return tr(text)
    if text.endswith('以上'):
        text = text[:-2].rstrip()
    return tr(text)+'以上'


def active_node(model):
    event = getattr(model, 'event', None) or {}
    bracket = event.get('bracket') or {}
    # A free table clears active_node_id; the schedule's last selected node can
    # remain set and must not relabel or rescope that unrelated live table.
    identity = model.state.get('broadcast', {}).get('active_node_id')
    return next((node for node in bracket.get('nodes', []) if node.get('id') == identity), None)


def automatic_qualification_title(model):
    """Name the destination being fought for, rather than the current stage."""
    event = getattr(model, 'event', None) or {}
    nodes = (event.get('bracket') or {}).get('nodes', [])
    current = active_node(model)
    if not current or not nodes:
        return '晋级条件'
    last_round = max(node.get('round', 1) for node in nodes)
    final_nodes = [node for node in nodes if node.get('round', 1) == last_round]
    if current.get('round') == last_round and len(final_nodes) == 1:
        return '冠军条件'
    children = [node for node in nodes if any(source.get('node') == current.get('id')
                for source in node.get('sources', []))]
    target_round = min((node.get('round', 1) for node in children),
                       default=current.get('round', 1)+1)
    if target_round == last_round and len(final_nodes) == 1:
        return '决赛晋级条件'
    if target_round == last_round-1 and len(final_nodes) == 1:
        return '半决赛晋级条件'
    return f'第{target_round}轮晋级条件'


def qualification_titles(model):
    config = model.state.get('broadcast', {}).get('config', {})
    default = config.get('qualification_title', '').strip() or automatic_qualification_title(model)
    individual = config.get('qualification_titles') or {}
    return {player['id']: individual.get(player['id'], '').strip() or default
            for player in model.state.get('players', [])}


def qualification_panel_size(model):
    """The OBS sidebar envelope stays fixed across contents and page scopes."""
    return SIDEBAR_SIZE


def qualification_context(model):
    """Use the same participants and cutoff in reports, editors and trials."""
    config = model.state['broadcast']['config']
    event = getattr(model, 'event', None) or {}
    node = active_node(model)
    if node:
        slots = 1 if automatic_qualification_title(model) == '冠军条件' else node.get('advance_count', 2)
        return dict(mode='individual', slots=slots, locked=True, node=node,
                    note='按当前淘汰桌的累计积分计算，晋级人数在淘汰赛程中设置。')
    managed = bool(getattr(model, 'event_store', None) and event)
    mode = ('team' if event.get('kind') == 'team_league' else 'individual') if managed else config.get('qualification_mode', 'individual')
    slots = event.get('qualification_count') if managed and event.get('kind') != 'individual_knockout' else 0
    slots = slots or config.get('qualification_slots', 2)
    unit = '队' if mode == 'team' else '名'
    return dict(mode=mode, slots=slots, locked=False, node=None,
                note=f'按全赛事累计积分的前 N {unit}计算，包含未在当前桌出场的对手；当前四席分别显示和牌条件。')


def fixed_sidebar_geometry(settings, default):
    """Keep a saved vertical placement; retire the old four-card envelope once."""
    saved = settings.get('windows', {}).get('qualification')
    layouts = settings.get('qualification_geometries', {})
    mode = settings.get('qualification_geometry_mode')
    if mode == 'four' or (mode is None and saved and saved[2] > saved[3]*1.5):
        saved = layouts.get('sidebar')
    geometry = list(saved or layouts.get('sidebar') or default)
    if settings.get('sidebar_fixed_canvas_version', 0) < 1 and (saved or layouts.get('sidebar')):
        old_height = settings.get('qualification_sidebar_base_height') or SIDEBAR_SIZE[1]
        geometry[3] = max(30, round(geometry[3]*SIDEBAR_SIZE[1]/old_height))
    return geometry


def qualification_data(model):
    """Keep calculations in tournament; add stage-aware labels and trial scope."""
    from ranking_reports import confirmed_matches
    from tournament import qualification

    state = model.state
    context = qualification_context(model)
    node = context['node']
    champion = automatic_qualification_title(model) == '冠军条件'
    state = copy.deepcopy(state)
    state['broadcast']['config'].update(qualification_mode=context['mode'], qualification_slots=context['slots'])
    data = qualification(state)
    titles = qualification_titles(model)
    by_id = {row['id']: row for row in data.get('rows', [])}
    data['rows'] = [by_id[player['id']] for player in model.state.get('players', [])
                    if player['id'] in by_id]
    for row in data['rows']:
        row['title'] = titles.get(row['id'], '晋级条件')
    config = state['broadcast']['config']
    manual = config.get('qualification_calculation', 'auto') == 'manual'
    if manual:
        data['display_note'] = '手动条件'
    elif state.get('phase') != 'playing':
        data['display_note'] = '已结算排名 · 下一局重新计算'
    else:
        slots = state['broadcast']['config'].get('qualification_slots', 2)
        target = '第一名' if champion else f'前{slots}{"队" if context["mode"] == "team" else "名"}'
        data['display_note'] = f'目标{target} · 本局结束试算'
        if node:
            completed = sum(match.get('bracket_node_id') == node['id'] and match.get('id') != state['id']
                            for match in confirmed_matches(state))
            planned = node.get('planned_games', 1)
            game = min(planned, completed+1)
            if game < planned:
                data['display_note'] = f'第{game}/{planned}半庄 · 尚非本轮最后半庄\n本局结束试算，后续半庄仍会改变条件'
        if '\n' not in data['display_note']:
            data['display_note'] += '\n仅列和牌最低档；连庄及后续对局会改变条件'
        if state.get('honba') or state.get('pot'):
            data['display_note'] = '基础支付档不含本场；计算已计本场与供托\n' + data['display_note'].split('\n', 1)[0]
    return data
