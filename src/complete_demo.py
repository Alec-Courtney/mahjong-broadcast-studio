"""Build demonstration events with the M.League roster and simulated results."""

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import random
import tempfile
import uuid

from app_paths import ROOT
from demo_data import DEMO_DESCRIPTIONS, DEMO_SCENARIOS, _resolve_assets, _settings, _win, demo_league_event, load_demo_state
from event_metadata import validate_event
from model import Model
from ranking_schema import TECH, allowed_columns
import ranking_reports
import tournament


TEAM_EVENT_NAME = '【演示】M.League 团队积分赛'
INDIVIDUAL_EVENT_NAME = '【演示】M.League 个人积分赛'
KNOCKOUT_TITLE = 'M.League 个人淘汰赛'
SIMULATION_TITLE = 'M.League 名册 · 模拟赛果'
SIMULATION_NOTICE = '选手、队伍与肖像来自 M.League 官网。演示成绩和技术统计由计分引擎逐局模拟。'
COMPLETED_APPEARANCES = 5
PLANNED_APPEARANCES = 6
TEAM_QUALIFIERS = 6
INDIVIDUAL_TEAMS = ('ml-drivens', 'ml-sakuraknights', 'ml-fightclub', 'ml-pirates')
KNOCKOUT_TEAMS = ('ml-jets', 'ml-sakuraknights', 'ml-phoenix', 'ml-pirates',
                  'ml-drivens', 'ml-fightclub', 'ml-raiden', 'ml-beast')
LEAGUE_SEEDS = {'team_complete': 20261119, 'individual_complete': 20261113}
DRAW_TILES = ('1m', '4m', '7m', '2p', '5p', '8p', '3s', '6s', '9s', '2z', '5z')
CHOW_TILES = (('2m', '3m', '4m'), ('2p', '3p', '4p'), ('2s', '3s', '4s'), ('6m', '7m', '8m'))


def mleague_roster():
    catalog = json.loads((ROOT / 'resources/demo/roster.json').read_text(encoding='utf-8'))
    fields = ('id', 'name', 'team', 'team_id', 'photo', 'color')
    return [dict({field: player[field] for field in fields}, follow_team_color=True,
                 base_points=0., games=0, status='active', stats={})
            for player in catalog['players']]


def _schedule(roster, rng):
    clubs = {}
    for player in roster:
        clubs.setdefault(player['team_id'], []).append(player['id'])
    teams = list(clubs)
    for _ in range(COMPLETED_APPEARANCES):
        rng.shuffle(teams)
        for members in clubs.values():
            rng.shuffle(members)
        offsets = [0, *rng.sample(range(1, len(teams)), 3)]
        for table in range(len(teams)):
            seats = [clubs[teams[(table + offset) % len(teams)]][seat]
                     for seat, offset in enumerate(offsets)]
            rng.shuffle(seats)
            yield seats


def _table(engine, seed, seats, identity, title, dealer):
    broadcast = seed['broadcast']
    profiles = {player['id']: player for player in broadcast['roster']}
    rules = dict(seed['rules'], startPoints=broadcast['config']['start_points'], terminalPot='top')
    state = engine.invoke('createState', dict(
        players=[dict(id=pid, name=profiles[pid]['name']) for pid in seats],
        rules=rules, title=title, initialDealer=dealer))
    state.update(id=identity, manualDisplay=True)
    for player in state['players']:
        profile = profiles[player['id']]
        player.update(team=profile['team'], team_id=profile['team_id'], photo=profile['photo'],
                      team_color=profile['color'], color=profile['color'],
                      tenpai=False, waits=[], remaining=None)
    table = tournament.ensure_broadcast(state)
    table.update(roster=copy.deepcopy(broadcast['roster']), config=copy.deepcopy(broadcast['config']),
                 teams=copy.deepcopy(broadcast['teams']), current_hand=f'{identity}-h01')
    return state


def _play_half(engine, seed, seats, number, scenario, title, rng, styles):
    identity = f'demo-{scenario}-history-{number:02d}'
    state = _table(engine, seed, seats, identity, f'{title} · 第{number:02d}场', (number - 1) % 4)
    state['manualDisplay'] = False
    broadcast = state['broadcast']

    def apply(event):
        before = {key: value for key, value in state.items() if key != 'broadcast'}
        state.update(engine.apply(before, event))
        tournament.on_update(before, state, event['type'])

    while state['phase'] != 'finished':
        hand = len(broadcast['hands'])
        if state['phase'] == 'result':
            apply({'type': 'next_hand'})
            broadcast['current_hand'] = f'{identity}-h{hand + 1:02d}'
        apply(dict(type='dora', tile=rng.choice(DRAW_TILES)))
        triplets = ['1z', '3z', '4z', '6z', '7z']
        for seat, player in enumerate(state['players']):
            _, call_rate, riichi_rate = styles[player['id']]
            action = rng.random()
            if action < call_rate:
                if rng.random() < .35:
                    meld = dict(kind='chi', tiles=list(CHOW_TILES[seat]), calledIndex=0)
                    meld['from'] = (seat - 1) % 4
                else:
                    tile = triplets.pop(rng.randrange(len(triplets)))
                    meld = dict(kind='pon', tiles=[tile] * 3, calledIndex=0)
                    meld['from'] = (seat + rng.randrange(1, 4)) % 4
                apply(dict(type='meld', player=seat, meld=meld))
            elif action < call_rate + riichi_rate and player['score'] >= 1000:
                apply(dict(type='riichi', player=seat))
        if hand < 20 and rng.random() < .15:
            tenpai = [seat for seat, player in enumerate(state['players'])
                      if player['riichi'] or rng.random() < .38]
            apply(dict(type='draw', tenpai=tenpai))
        else:
            weights = [styles[player['id']][0] * (1.2 if player['riichi'] else 1.)
                       for player in state['players']]
            if hand >= 20:
                weights[state['dealer']] = 0
            winner = rng.choices(range(4), weights=weights)[0]
            tsumo = rng.random() < .37
            han = rng.choices([1, 2, 3, 4, 5, 6, 7, 8], [18, 26, 25, 16, 9, 3, 2, 1])[0]
            if tsumo and state['players'][winner]['riichi']:
                han = max(2, han)
            fu = rng.choices([30, 40, 50, 60], [61, 29, 8, 2])[0]
            event = dict(type='tsumo' if tsumo else 'ron', winner=winner, han=han, fu=fu)
            if not tsumo:
                event['loser'] = (winner + rng.randrange(1, 4)) % 4
            apply(event)
    engine.validate(state)
    match = tournament.archive_match(state, committed=True)
    match.update(completed=True, source='simulation')
    return match


def _live_half(model, title, scenario):
    selected = ['ml-drivens-1', 'ml-sakuraknights-1', 'ml-fightclub-3', 'ml-pirates-2']
    model.state = _table(model.engine, model.state, selected, f'demo-{scenario}-live', title, 0)
    # Four settlements followed by an editable fifth hand for live operation.
    model.dora('5m')
    model.riichi(1)
    _win(model, 1, 3, 3, 30, yaku=['立直', '平和', '宝牌 1'])
    model.next_hand()
    model.dora('2p')
    model.meld(2, 'pon', ['7s'] * 3, 0)
    _win(model, 0, None, 2, 30, yaku=['门前清自摸和', '宝牌 1'])
    model.next_hand()
    model.dora('3s')
    model.set_tenpai(0, True)
    model.set_tenpai(2, True)
    model.draw()
    model.next_hand()
    model.dora('4m')
    model.riichi(3)
    _win(model, 2, 3, 4, 30, yaku=['断幺九', '宝牌 3'])
    model.next_hand()
    model.dora('5p')
    model.riichi(0)
    model.set_waits(0, ['3m', '6m'])
    model.set_remaining(0, 5)
    model.meld(2, 'chi', ['8m', '7m', '9m'], 1, 0)
    for number, hand in enumerate(model.state['broadcast']['hands'], 1):
        hand['id'] = f'demo-{scenario}-live-h{number:02d}'
    model.state['broadcast']['current_hand'] = f'demo-{scenario}-live-h05'


def _validate_technical_rows(state):
    rows = ranking_reports.individual_rows(state, {})
    missing = {row['name']: [field for field in TECH if row[field] is None] for row in rows}
    missing = {name: fields for name, fields in missing.items() if fields}
    if missing:
        raise ValueError(f'演示逐局样本不足以计算所有技术指标：{missing}')
    if any(row['known_games'] != row['played'] or row['technical_matches'] != row['played'] for row in rows):
        raise ValueError('演示逐场统计覆盖不完整')


def _validate_history(state):
    matches = ranking_reports.confirmed_matches(state)
    for match in matches:
        total = state['rules']['startPoints'] * 4
        if sum(match['initial_scores']) != total or sum(row['score'] for row in match['results']) != total:
            raise ValueError('演示半庄起始与终局点数不符')
        for hand in match['hands']:
            result = hand['result']
            if (sum(result['beforeScores']) + result['potBefore'] != total or
                    sum(result['afterScores']) + result['potAfter'] != total):
                raise ValueError('演示逐局点数与供托合计不符')
    appearances = Counter(identity for match in matches for identity in match['seats'])
    if set(appearances.values()) != {COMPLETED_APPEARANCES} or len(appearances) != len(state['broadcast']['roster']):
        raise ValueError('演示名册未完整轮换')
    _validate_technical_rows(state)


def generate_complete_fixture(scenario, destination):
    is_team = scenario == 'team_complete'
    mode = 'team' if is_team else 'individual'
    roster = mleague_roster()
    if not is_team:
        roster = [player for player in roster if player['team_id'] in INDIVIDUAL_TEAMS]
    teams = {player['team_id']: dict(name=player['team'], color=player['color']) for player in roster}
    title = TEAM_EVENT_NAME if is_team else INDIVIDUAL_EVENT_NAME
    rng = random.Random(LEAGUE_SEEDS[scenario])
    styles = {player['id']: (rng.uniform(.82, 1.20), rng.uniform(.22, .38), rng.uniform(.19, .28))
              for player in roster}
    tables = list(_schedule(roster, rng))
    with tempfile.TemporaryDirectory(prefix='complete-fixture-', dir=destination) as temporary:
        model = Model(temporary)
        model.state['broadcast']['teams'] = teams
        tournament.configure(model.state, dict(roster=roster, seats=tables[0], title=title,
            start_points=25000, return_points=30000, uma=[50, 10, -10, -30],
            oka_enabled=False, tie_break='share', event_mode=mode, standings_mode=mode,
            sidebar_mode=mode, qualification_mode=mode, qualification_slots=TEAM_QUALIFIERS if is_team else 2,
            show_match_title=False, statistics_scope='all', statistics_view='rates',
            opening_title_text_only=True, settlement_enabled=True, commentary_builtin=False,
            commentary_title=SIMULATION_TITLE, commentary_body=SIMULATION_NOTICE))
        history = [_play_half(model.engine, model.state, seats, number, scenario, title, rng, styles)
                   for number, seats in enumerate(tables, 1)]
        _live_half(model, title, scenario)
        model.state['broadcast']['matches'] = history
        tournament.validate_broadcast(model.state)
        model.engine.validate(model.state)
        _validate_history(model.state)
        settings = _settings(['round', 'dora', 'player0', 'player1', 'player2', 'player3', 'yaku', 'qualification'])
        settings.update(player_group_linked=True, show_yaku=False)
        payload = dict(format='MahjongOverlayStudio', version=6, state=model.state, settings=settings,
            last_info=None, demo=dict(id=scenario, label=dict(DEMO_SCENARIOS)[scenario],
                description=DEMO_DESCRIPTIONS[scenario], fictional=True,
                photo_source='M.League public team pages',
                results='Synthetic settlements recorded by the scoring engine',
                planned_appearances_per_player=PLANNED_APPEARANCES,
                qualification_count=TEAM_QUALIFIERS if is_team else 0))
        (destination / (scenario + '.json')).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n', encoding='utf-8')
        return payload


def generate(destination=None):
    destination = Path(destination or ROOT / 'resources/demo')
    destination.mkdir(parents=True, exist_ok=True)
    return {scenario: generate_complete_fixture(scenario, destination)
            for scenario in ('team_complete', 'individual_complete')}


def _export_knockout(scratch, target):
    from demo_suite import knockout
    from event_schedule import node_matches, resolve_sources

    players = {player['id']: player for player in mleague_roster()}
    roster = _resolve_assets([players[f'{team}-{member}'] for member in (1, 2) for team in KNOCKOUT_TEAMS])
    model = knockout(scratch, 'mleague-knockout-9', KNOCKOUT_TITLE, 'finished',
                     roster=roster, games_per_table=4)
    state, event = model.state, model.event
    event.update(created_at='2026-10-25T09:00:00+00:00', updated_at='2026-10-31T12:00:00+00:00',
                 report_columns={'individual': allowed_columns('individual_knockout')},
                 demo_suite=dict(key='mleague_knockout', synthetic_results=True,
                                 description='16名选手，7桌28个完整模拟半庄，从首轮至决赛。'))
    state['broadcast']['config'].update(opening_title_text_only=True, commentary_title=SIMULATION_TITLE,
                                        commentary_body=SIMULATION_NOTICE)
    nodes = event['bracket']['nodes']
    event['bracket']['active_node'] = nodes[-1]['id']
    planned = Counter()
    for node in nodes:
        planned.update({player: node['planned_games'] for player in resolve_sources(event, node)})
        for game, match in enumerate(node_matches(state, node['id']), 1):
            match['title'] = f'{KNOCKOUT_TITLE} · 第{node["round"]}轮 {node["name"]} · 第{game}半庄'
            match['source'] = 'simulation'
    event['player_plans'] = dict(planned)
    _validate_technical_rows(state)
    validate_event(event, state)
    model.export_data(target)


def generate_portable():
    """Export all three demonstration events as self-contained imports."""
    output = ROOT / 'demo-suite'
    scratch = ROOT / '.cache'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='demo-export-', dir=scratch) as temporary:
        model = Model(temporary)
        for scenario, filename in (('team_complete', 'team-league-demo.json'),
                                   ('individual_complete', 'individual-league-demo.json')):
            payload = load_demo_state(scenario)
            state = payload['state']
            title = state['title'] + '（导入示例）'
            identity = uuid.uuid5(uuid.NAMESPACE_URL, 'MahjongOverlayStudio/portable/' + scenario).hex
            event = demo_league_event(payload, identity, title)
            event.update(created_at='2026-10-01T09:00:00+00:00', updated_at='2026-10-24T12:00:00+00:00')
            state['title'] = state['broadcast']['config']['title'] = title
            validate_event(event, state)
            target = output / filename
            model.repository.export(target, model.path, state, payload['settings'], payload['last_info'], event)
            print(target.name, target.stat().st_size, 'bytes')
        target = output / 'individual-knockout-demo.json'
        _export_knockout(Path(temporary), target)
        print(target.name, target.stat().st_size, 'bytes')


if __name__ == '__main__':
    from PySide6.QtWidgets import QApplication

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--portable', action='store_true', help='Also export all three events with embedded portraits')
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    for scenario, payload in generate().items():
        broadcast = payload['state']['broadcast']
        print(scenario, len(broadcast['roster']), 'players,', len(broadcast['matches']), 'completed games')
    if args.portable:
        generate_portable()
