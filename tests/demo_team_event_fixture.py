"""Reusable isolated ML team league: actual engine events, never invented totals.

build_realistic_team_demo(directory) returns a Model with its own EventStore,
160+ committed complete halves, zero historical baselines and a fresh live table.
Every simulated event is retained in each match's simulation.event_trace so its
payments can be replayed independently. All names/photos are bundled profiles;
every score and statistic is explicitly synthetic, not a real ML event result.
"""
from __future__ import annotations

import copy
from collections import Counter
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path:sys.path.insert(0, str(ROOT/'src'))
from demo_data import load_demo_state
from event_store import EventStore
from model import Model
import ranking_reports as reports
import tournament

FIXTURE_VERSION = 1
VALIDATION_FILENAME = 'realistic-team-validation.json'
_CACHE = None


def _schedule(roster, rng):
    clubs = {}
    for player in roster:clubs.setdefault(reports.team_id(player), []).append(player['id'])
    clubs = list(clubs.values());tables = []
    # Four seat pools each contain exactly one member of every club. Permuting
    # each pool makes 10 tables with no two members of one club facing each other.
    for cycle in range(14):
        pools = [[] for _ in range(4)]
        for members in clubs:
            members = members[:];rng.shuffle(members)
            for seat, player in enumerate(members):pools[seat].append(player)
        columns = [pools[0]]
        identity = {p['id']:reports.team_id(p) for p in roster}
        for pool in pools[1:]:
            for attempt in range(10000):
                rng.shuffle(pool)
                if all(all(identity[pool[i]] != identity[column[i]] for column in columns) for i in range(10)):break
            else:raise AssertionError('无法生成不同队伍的模拟桌')
            columns.append(pool[:])
        tables.extend([[column[index] for column in columns] for index in range(10)])
    remaining = {p['id']:index%5 for index, p in enumerate(roster)}
    while sum(remaining.values()):
        order = sorted(clubs, key=lambda club:(-sum(remaining[p] for p in club), rng.random()))
        selected = []
        for club in order[:4]:
            choices = [p for p in club if remaining[p]]
            if not choices:raise AssertionError('额外轮换场次无法完整分桌')
            player = rng.choice(choices);remaining[player] -= 1;selected.append(player)
        rng.shuffle(selected);tables.append(selected)
    return tables


def _table(engine, roster, rules, config, seats, number):
    profiles = {p['id']:p for p in roster}
    state = engine.invoke('createState', dict(players=[dict(id=p, name=profiles[p]['name']) for p in seats],
        rules=rules, title=f'ML团队赛模拟 · 第{number+1}场', initialDealer=number%4))
    state['id'] = f'realistic-team-half-{number+1:03d}'
    state['manualDisplay'] = True
    for player in state['players']:
        profile = profiles[player['id']]
        player.update(photo=profile.get('photo', ''), team=profile.get('team', ''),
            team_id=profile.get('team_id', ''), team_color=profile['color'], color=profile['color'],
            tenpai=False, waits=[], remaining=None)
    broadcast = tournament.ensure_broadcast(state)
    broadcast['roster'] = copy.deepcopy(roster);broadcast['config'] = copy.deepcopy(config)
    broadcast['current_hand'] = state['id']+'-hand-001'
    broadcast['initial_players'] = copy.deepcopy(state['players'])
    broadcast['partial'] = False
    return state


def apply_recorded_event(engine, state, event):
    """Same engine and recording hook as Model, with no growing ledger in JS."""
    before = dict(state)
    payload = {key:value for key, value in state.items() if key != 'broadcast'}
    state.update(engine.apply(payload, event))
    tournament.on_update(before, state, event['type'])
    if event['type'] == 'next_hand':
        state['broadcast']['current_hand'] = state['id']+f'-hand-{len(state["broadcast"]["hands"])+1:03d}'


def _play_half(engine, roster, rules, config, seats, number, rng, skill, traits):
    state = _table(engine, roster, rules, config, seats, number);trace = []
    def apply(event):
        apply_recorded_event(engine, state, event);trace.append(copy.deepcopy(event))
    while state['phase'] != 'finished':
        if state['phase'] == 'result':apply(dict(type='next_hand'))
        hand = len(state['broadcast']['hands'])
        apply(dict(type='dora', tile=rng.choice(['1m', '4p', '7s', '5z', '2z'])))
        for seat, player in enumerate(state['players']):
            call_rate, riichi_rate = traits[player['id']]
            if rng.random() < call_rate:
                chi = rng.random()<.36
                apply(dict(type='meld', player=seat, meld=dict(kind='chi' if chi else 'pon',
                    tiles=['2m','3m','4m'] if chi else [rng.choice(['5p','7s','5z'])]*3,
                    **{'from':(seat+3)%4 if chi else (seat+rng.choice([1,2,3]))%4}, calledIndex=0)))
            elif player['score']>=1000 and rng.random() < riichi_rate/(1-call_rate):
                apply(dict(type='riichi', player=seat))
        # Drawn hands include actual noten payment/dealer continuation. A long
        # accidental chain eventually resolves through a nondealer win.
        if hand<25 and rng.random()<.13:
            tenpai = [seat for seat, player in enumerate(state['players']) if player['riichi'] or rng.random()<.40]
            for seat, player in enumerate(state['players']):player['tenpai'] = seat in tenpai
            apply(dict(type='draw', tenpai=tenpai));continue
        weights = [skill[p['id']] for p in state['players']]
        if hand>=25:weights[state['dealer']] = 0
        winner = rng.choices(range(4), weights=weights)[0]
        player = state['players'][winner]
        kind = 'tsumo' if rng.random()<.36 else 'ron'
        han = rng.choices([1,2,3,4,5,6], weights=[18,28,25,16,10,3])[0]
        if player['riichi'] and kind=='tsumo':han = max(han, 2)
        # Open wins use a plausible nonzero yaku, closed riichi-tsumo at least2han.
        event = dict(type=kind, winner=winner, han=han, fu=rng.choices([30,40,50], [65,30,5])[0])
        if kind=='ron':event['loser'] = rng.choice([seat for seat in range(4) if seat!=winner])
        apply(event)
    engine.validate(state)
    archive = tournament.archive_match(state, True)
    archive.update(completed=True, source='simulation', simulation=dict(fixture_version=FIXTURE_VERSION,
        initial_dealer=number%4, event_trace=trace))
    return archive


def validate_realistic_team_demo(model):
    model.engine.validate(model.state);tournament.validate_broadcast(model.state)
    personal = reports.individual_rows(model.state, model.event)
    teams = reports.team_rows(model.state, model.event)
    games = [row['played'] for row in personal]
    metrics = ('riichi_rate','call_rate','win_rate','deal_in_rate')
    matches = reports.confirmed_matches(model.state)
    summary = dict(fixture_version=FIXTURE_VERSION, synthetic=True, formal_data_opened=False,
        provenance='160 completed simulated halves executed by the actual payment and recording engine; no baseline scores or fabricated statistics',
        completed_half_games=len(matches), settled_hands=sum(len(m['hands']) for m in matches),
        rules=copy.deepcopy(model.state['rules']),
        terminal_unallocated_pot=sum(m['hands'][-1]['result']['potAfter'] for m in matches),
        players=len(personal), teams=len(teams), min_games=min(games), max_games=max(games),
        games_distribution=dict(sorted(Counter(games).items())),
        unique_personal_points=len({row['points'] for row in personal}),
        unique_team_points=len({row['points'] for row in teams}),
        metric_distinct_values={key:len({round(row[key], 6) for row in personal}) for key in metrics},
        technical_coverage='Every played half has complete settled-hand records; no imported technical baseline.',
        current_four=[dict(id=row['id'], name=row['name'], rank=row['rank'], points=row['points'], played=row['games'])
                      for row in tournament.table_standings(model.state, 'individual')],
        player_rows=[{key:row[key] for key in ('id','name','team_id','rank','points','played','first','second','third','fourth','hands','calls','riichi','wins','deal_ins')+metrics} for row in personal],
        team_rows=[{key:row[key] for key in ('id','team','rank','points','played')} for row in teams])
    assert len(personal)==40 and len(teams)==10
    assert min(games)>=12 and max(games)<=20 and len(set(games))>=3
    assert summary['unique_personal_points']==40 and summary['unique_team_points']==10
    assert all(row['known_games']==row['played'] and row['technical_matches']==row['played'] for row in personal)
    assert all(sum(row['counts'])==row['played'] and row['hands']>=row['played']*5 for row in personal)
    assert all(0<row[key]<.60 for row in personal for key in metrics)
    assert all(summary['metric_distinct_values'][key]>=30 for key in metrics)
    assert all(match['committed'] and match.get('completed') and match['hands'] for match in matches)
    assert all(player.get('base_points', 0)==0 and player.get('games', 0)==0 and not player.get('stats')
               for player in model.state['broadcast']['roster'])
    return summary


def build_realistic_team_demo(directory):
    """Return one independent persisted Model, reusing only an immutable cache."""
    global _CACHE
    directory = Path(directory).resolve();directory.mkdir(parents=True, exist_ok=True)
    if (directory/'library.json').exists():
        if not (directory/VALIDATION_FILENAME).is_file():raise ValueError('请使用独立演示目录，不能覆盖已有赛事库')
        model = Model(directory);EventStore(model)
        model.realistic_demo_validation = validate_realistic_team_demo(model)
        return model
    model = Model(directory)
    payload = load_demo_state('team_complete')
    roster = copy.deepcopy(payload['state']['broadcast']['roster'])
    for player in roster:
        player.update(base_points=0., games=0, stats={}, status='active')
        player.pop('result_baseline', None);player.pop('baseline_stats', None)
    config = copy.deepcopy(payload['state']['broadcast']['config'])
    config.pop('team_baselines', None)
    config.update(title='ML团队赛多轮模拟', event_mode='team', sidebar_mode='individual',
        sidebar_scope='all', sidebar_auto_page=True, sidebar_page=0, sidebar_page_size=10,
        standings_live=False, statistics_scope='all', statistics_view='rates')
    rules = copy.deepcopy(payload['state']['rules'])
    if _CACHE is None:
        for attempt in range(10):
            rng = random.Random(20261101+attempt)
            schedule = _schedule(roster, rng)
            skill = {p['id']:.76+rng.random()*.48 for p in roster}
            traits = {p['id']:(.20+rng.random()*.16, .15+rng.random()*.12) for p in roster}
            archives = [_play_half(model.engine, roster, rules, config, seats, number, rng, skill, traits)
                        for number, seats in enumerate(schedule)]
            candidate = _table(model.engine, roster, rules, config, schedule[0], len(archives))
            candidate['broadcast']['matches'] = archives
            rows = reports.individual_rows(candidate, dict(kind='team_league'))
            teams = reports.team_rows(candidate, dict(kind='team_league'))
            if len({row['points'] for row in rows})==40 and len({row['points'] for row in teams})==10:break
        else:raise AssertionError('未生成足够不同的实际模拟积分')
        chosen, seen = [], set()
        for position in (0, 13, 25, 39):
            ordered = rows[position:]+list(reversed(rows[:position]))
            row = next(row for row in ordered if row['id'] not in chosen and row['team_id'] not in seen)
            chosen.append(row['id']);seen.add(row['team_id'])
        current = _table(model.engine, roster, rules, config, chosen, len(archives))
        current['broadcast']['matches'] = archives
        _CACHE = copy.deepcopy(current)
    model.state = copy.deepcopy(_CACHE);model.settings.update(copy.deepcopy(payload['settings']))
    model.settings.update(demo_active=False, show_yaku=False, local_demo_suite=False)
    model.last_info = None
    store = EventStore(model)
    store.update(name='ML团队赛多轮模拟', kind='team_league', planned_games=80,
        player_plans={p['id']:20 for p in roster},
        report_columns={'individual':['rank','name','team','points','games','average_rank','first','second','third','fourth'],
                        'team':['rank','team','points','games','first','second','third','fourth']})
    summary = validate_realistic_team_demo(model)
    (directory/VALIDATION_FILENAME).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    model.realistic_demo_validation = summary
    model.undo.clear();model.redo.clear();model.save()
    return model
