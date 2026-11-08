"""Continuous operator scenarios using real commands, windows and event files.

No round, pot or score is assigned by these fixtures. Half-games reach South 4
through engine settlements/advances, and only their final confirmation books pt.
"""
import copy
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from event_store import EventStore
import event_schedule as schedule
from model import Model
from overlays import OverlayManager
from player_status_frame import status_mode
from qualification_presentation import qualification_data, automatic_qualification_title
from ranking_reports import confirmed_matches
from table_score_strip import table_score_strip
import tournament
import ranking_reports as reports

APP = QApplication.instance() or QApplication([])


class RealisticBroadcastScenarios(unittest.TestCase):
    def setUp(self):
        self.now = 1000.
        self.clock = patch('overlays.time.monotonic', side_effect=lambda: self.now)
        self.clock.start()
        self.directory = tempfile.TemporaryDirectory(dir=ROOT/'tests', prefix='live_scenario_')
        self.root = Path(self.directory.name)
        self.model = Model(self.root)
        self.store = EventStore(self.model)
        self.model.configure_broadcast(dict(start_points=25000, return_points=30000,
                                           uma=[50, 10, -10, -30], oka_enabled=False,
                                           tie_break='share', qualification_layout='four',
                                           settlement_hold=1., settlement_total_hold=1.))
        self.model.matches.start()
        self.manager = OverlayManager(self.model)
        self.manager.apply_scene('qualification')

    def tearDown(self):
        self.manager.close()
        APP.processEvents()
        self.directory.cleanup()
        self.clock.stop()

    def advance_picture(self, seconds):
        self.now += seconds/self.manager.speed
        self.manager.tick()

    def assert_conserved(self):
        state = self.model.state
        self.model.engine.validate(state)
        self.assertEqual(sum(player['score'] for player in state['players'])+state['pot'], 100000)
        self.assertEqual(state['accountingTotal'], 100000)
        self.assertEqual(state.get('accountingAdjustment', 0), 0)

    def booked(self):
        return {row['id']: (row['points'], row['games'])
                for row in tournament.standings(self.model.state)}

    def strip(self):
        return table_score_strip(self.model.state, self.model.event)

    def win(self, winner=None, loser=None, kind='ron', han=1):
        state = self.model.state
        winner = (state['dealer']+1) % 4 if winner is None else winner
        event = dict(type=kind, winner=winner, han=han, fu=30)
        if kind == 'ron':
            event['loser'] = (winner+1) % 4 if loser is None else loser
        self.model.settle(event, dict(winner=winner, name=state['players'][winner]['name'],
                                     han=han, fu=30, yaku=['断幺九']))
        self.assert_conserved()

    def finish_half_game(self, offset=0):
        """Rotate naturally through all remaining hands without repeat wins."""
        count = 0
        while self.model.state['phase'] != 'finished':
            if self.model.state['phase'] == 'result':
                self.assertTrue(self.model.next_hand())
            dealer = self.model.state['dealer']
            self.win(winner=(dealer+1+offset % 3) % 4)
            count += 1
            self.assertLess(count, 20, 'continuous engine flow did not reach the half-game end')
        self.assertEqual(self.model.state['roundIndex'], 7)
        self.assertFalse(self.model.next_hand())
        return count

    def render(self, kind, index=0):
        before = copy.deepcopy(self.model.state)
        width, height = self.manager.base_size(kind)
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            self.manager.draw_content(painter, kind, index, static=True)
        finally:
            painter.end()
        digest = hashlib.sha256(bytes(image.constBits())).hexdigest()
        blank = hashlib.sha256(bytes(image.sizeInBytes())).hexdigest()
        self.assertNotEqual(digest, blank, kind+' rendered an empty capture')
        self.assertEqual(self.model.state, before, 'rendering changed the live table')
        return digest

    def test_continuous_dama_riichi_cancel_waits_calls_draw_repeat_and_replacement(self):
        initial_booked = self.booked()
        self.model.dora('5m')
        self.model.submit_waits(0, ['3m', '6m'], 5, no_yaku=['6m'], furiten=False)
        self.assertIsNone(status_mode(self.model.state['players'][0]))
        self.assertEqual(self.model.state['players'][0]['score'], 25000)
        self.model.riichi(0)
        self.assertEqual(status_mode(self.model.state['players'][0]), 'riichi')
        self.assertEqual(self.model.state['players'][0]['waits'], ['3m', '6m'])
        self.assertEqual(self.model.state['pot'], 1000)

        self.model.submit_waits(1, ['2p', '5p'], 3, furiten=True)
        self.model.set_tenpai(1, False)
        self.assertFalse(self.model.state['players'][1]['tenpai'])
        self.assertIsNone(status_mode(self.model.state['players'][1]))
        self.model.meld(2, 'pon', ['7s']*3, 3)
        self.model.submit_waits(2, ['4m'], 2, no_yaku=[])
        self.model.riichi(3)
        self.assertEqual(self.model.state['pot'], 2000)
        self.render('player', 0)
        self.render('player', 2)
        self.render('qualification')

        self.model.draw()
        drawn = copy.deepcopy(self.model.state)
        self.assertEqual(drawn['lastResult']['next']['reason'], 'dealer_tenpai')
        self.assertEqual(drawn['lastResult']['next']['honba'], 1)
        self.assertEqual(drawn['pot'], 2000)
        self.assertEqual(self.booked(), initial_booked)
        self.model.history('undo')
        self.assertEqual(self.model.state['phase'], 'playing')
        self.assertEqual(len(self.model.state['players'][2]['melds']), 1)
        self.assertEqual(self.model.state['pot'], 2000)
        self.model.history('redo')
        self.assertEqual(self.model.state, drawn)
        self.assertTrue(self.model.next_hand())
        self.assertEqual((self.model.state['roundIndex'], self.model.state['dealer'], self.model.state['honba']), (0, 0, 1))
        self.assertTrue(all(not player['waits'] and not player['melds'] and not player['riichi']
                            for player in self.model.state['players']))

        self.model.submit_waits(1, ['1s', '4s'], 4, furiten=False)
        self.model.meld(2, 'chi', ['2p', '3p', '4p'], 1)
        self.assertIsNone(status_mode(self.model.state['players'][1]))
        self.win(winner=1, kind='tsumo', han=2)
        tsumo = copy.deepcopy(self.model.state)
        self.assertEqual(tsumo['pot'], 0)
        self.assertEqual(len(tsumo['broadcast']['hands']), 2)
        self.win(winner=2, loser=3)
        replacement = copy.deepcopy(self.model.state)
        self.assertEqual(len(replacement['broadcast']['hands']), 2)
        self.assertEqual(replacement['pot'], 0)
        self.assertEqual(replacement['lastResult']['potBefore'], 2000)
        self.model.history('undo')
        self.assertEqual(self.model.state, tsumo)
        self.model.history('redo')
        self.assertEqual(self.model.state, replacement)
        self.assertEqual(self.booked(), initial_booked)
        self.assertEqual(confirmed_matches(self.model.state), [])

        self.finish_half_game()
        self.assertEqual(self.booked(), initial_booked, 'an unconfirmed final must still be absent from standings')
        self.model.matches.confirm_finished()
        self.assertEqual(len(confirmed_matches(self.model.state)), 1)
        self.assertTrue(all(games == 1 for points, games in self.booked().values()))
        self.assertGreaterEqual(len(self.model.state['broadcast']['hands']), 9)
        self.assert_conserved()

    def test_live_windows_cross_switch_during_draw_and_return_to_next_hand(self):
        self.model.event['kind']='individual_league'
        self.model.state['broadcast']['config']['event_mode']='individual'
        self.model.submit_waits(0, ['3m', '6m'], 4)
        self.model.riichi(0)
        self.model.draw()
        self.manager.apply_scene('clean')
        self.advance_picture(.4)
        self.assertFalse(any(self.manager.content_visible(key) for key in self.manager.windows))
        self.manager.apply_scene('qualification')
        self.model.configure_commentary(dict(commentary_title='本场提示', commentary_body='立直棒留至下一局。', commentary_duration=0))
        self.manager.set_extension_visible('commentary', True)
        settled = copy.deepcopy(self.model.state)
        self.advance_picture(self.manager.animation.duration_ms/1000)
        self.assertEqual(self.manager.stage, 'draw_hold')
        self.assertEqual(self.model.state, settled)
        self.assertFalse(self.manager.content_visible('yaku'))
        self.assertTrue(self.manager.content_visible('table_scores'))

        self.manager.apply_scene('review')
        self.advance_picture(4.01)
        self.assertEqual(self.model.state['phase'], 'playing')
        self.assertEqual(self.model.state['pot'], 1000)
        self.assertEqual(self.model.state['honba'], 1)
        self.assertEqual(self.model.state['roundIndex'], 0)
        self.advance_picture(.46)
        self.assertEqual(self.manager.stage, 'table')
        self.assertTrue(self.manager.content_visible('chart'))
        self.render('chart')

        self.manager.apply_scene('qualification')
        self.manager.set_extension_visible('commentary', True)
        self.advance_picture(.4)
        self.assertTrue(all(self.manager.content_visible(key) for key in ('round', 'dora', 'table_scores', 'four_conditions', 'player0', 'commentary')))
        before = copy.deepcopy(self.model.state)
        self.render('table_scores')
        four = self.render('four_conditions')
        self.model.configure_broadcast({'qualification_layout': 'sidebar'})
        self.manager.show_conditions()
        self.assertTrue(self.manager.content_visible('qualification'))
        self.assertFalse(self.manager.enabled['four_conditions'])
        single = self.render('qualification')
        self.assertNotEqual(four, single)
        self.model.configure_broadcast({'qualification_layout': 'four'})
        self.manager.set_visible('player2', False)
        self.manager.set_bottom_mode('individual')
        self.advance_picture(.4)
        self.assertTrue(self.manager.content_visible('standings'))
        self.assertFalse(any(self.manager.content_visible(f'player{i}') for i in range(4)))
        self.render('standings')
        self.manager.set_bottom_mode('players')
        self.advance_picture(.4)
        self.assertFalse(self.manager.content_visible('player2'))
        self.assertTrue(self.manager.content_visible('player1'))
        for key in ('players', 'pot', 'phase', 'roundIndex', 'honba', 'lastResult'):
            self.assertEqual(self.model.state[key], before[key], 'presentation altered '+key)
        self.assert_conserved()

    def test_two_half_game_semifinals_and_final_use_only_the_active_node(self):
        self.store.create('淘汰赛','individual_knockout',copy_config=True)
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        roster.extend(dict(id=f'guest-{i}', name=f'晋级选手{i}', seed=i) for i in range(5, 9))
        self.model.set_roster(roster)
        bracket = schedule.generate(roster, planned_games=2, advance_count=2)
        self.store.update(bracket=bracket)
        semifinal_ids = [node['id'] for node in bracket['nodes'] if node['round'] == 1]
        final_id = next(node['id'] for node in bracket['nodes'] if node['round'] == 2)
        archive_count = 0

        for table_index, node_id in enumerate(semifinal_ids+[final_id]):
            schedule.start_node(self.store, node_id)
            self.assertEqual(self.strip()['mode'], 'first_game')
            self.assertTrue(all(row['points'] is None for row in self.strip()['rows']))
            data = qualification_data(self.model)
            self.assertEqual(len(data['rows']), 4)
            self.assertTrue(all(abs(row['points']) < .0001 for row in data['rows']),
                            'previous-round pt leaked into the new table projection')
            title = '冠军条件' if node_id == final_id else '决赛晋级条件'
            self.assertEqual(automatic_qualification_title(self.model), title)
            self.assertTrue(all(row['title'] == title for row in data['rows']))
            self.assertIn('第1/2半庄', data['display_note'])
            opening = self.booked()
            # A riichi draw carries its stick into the repeat, then an ordinary
            # win and the remaining rotations complete this genuine half-game.
            self.model.riichi(0)
            self.model.draw()
            self.assertEqual(self.model.state['pot'], 1000)
            self.assertEqual(self.booked(), opening)
            self.finish_half_game(offset=table_index)
            self.assertEqual(self.booked(), opening)
            self.model.matches.confirm_finished()
            archive_count += 1
            self.assertEqual(len(confirmed_matches(self.model.state)), archive_count)
            first = {row['id']: row['points'] for row in self.strip()['rows']}
            self.assertTrue(all(value is not None for value in first.values()))

            self.assertEqual(self.model.matches.start_next(), 'started')
            self.assertEqual(self.model.state['broadcast']['active_node_id'], node_id)
            self.assertEqual({row['id']: row['points'] for row in self.strip()['rows']}, first)
            data = qualification_data(self.model)
            self.assertNotIn('尚非本轮最后半庄', data['display_note'])
            second_opening = self.booked()
            self.model.submit_waits(1, ['2s', '5s'], 6)
            self.win(winner=1, kind='tsumo')
            self.assertEqual({row['id']: row['points'] for row in self.strip()['rows']}, first)
            self.assertEqual(self.booked(), second_opening)
            self.finish_half_game(offset=table_index+1)
            self.model.matches.confirm_finished()
            archive_count += 1
            self.assertEqual(len(confirmed_matches(self.model.state)), archive_count)
            self.assertEqual({row['id']: row['points'] for row in self.strip()['rows']}, first)
            self.assertEqual(len(schedule.node_matches(self.model.state, node_id)), 2)
            self.assertEqual(self.model.matches.start_next(), 'schedule')
            if node_id != final_id:
                self.assertEqual(len(schedule.confirm_advancement(self.store, node_id)), 2)

        final_node = next(node for node in self.model.event['bracket']['nodes'] if node['id'] == final_id)
        expected = {row['id']: round(row['points'], 4) for row in schedule.node_results(self.model.state, self.model.event, final_node)}
        self.model.configure_broadcast({'table_score_mode': 'cumulative'})
        self.assertEqual({row['id']: row['points'] for row in self.strip()['rows']}, expected)
        self.assertTrue(any(abs(self.booked()[identity][0]-value) > .001 for identity, value in expected.items()))
        self.assertEqual(len(confirmed_matches(self.model.state)), 6)
        self.assert_conserved()

    def test_switch_events_and_restart_preserves_live_actions_and_window_positions(self):
        first_event = self.model.event['id']
        self.model.submit_waits(0, ['1m', '4m'], 7, no_yaku=['4m'], furiten=True)
        self.model.riichi(0)
        self.model.meld(2, 'pon', ['5p']*3, 3)
        self.model.submit_waits(2, ['7s'], 2)
        self.manager.windows['table_scores'].move(260, 95)
        self.manager.windows['qualification'].move(80, 260)
        self.manager.save_geometry()
        live = copy.deepcopy(self.model.state)
        geometry = self.model.settings['windows']['table_scores'][:]
        second_event = self.store.create('隔离的另一场比赛', 'individual_league')
        self.assertNotEqual(first_event, second_event)
        self.assertEqual(self.model.state['pot'], 0)
        self.assertFalse(any(player['riichi'] or player['melds'] or player['waits'] for player in self.model.state['players']))
        self.store.open(first_event)
        self.assertEqual(self.model.state, live)
        self.assertEqual(self.model.settings['windows']['table_scores'], geometry)
        self.assertEqual(self.manager._geometry_list(self.manager.windows['table_scores']), geometry)
        self.render('player', 0)
        self.assertTrue(self.manager.content_visible('table_scores'))
        self.render('table_scores')
        self.assert_conserved()

        # Cold-load the same temporary library, retaining the pending riichi
        # deposit and open call rather than reconstructing them from a preview.
        restarted = Model(self.root)
        EventStore(restarted)
        self.assertEqual(restarted.event['id'], first_event)
        self.assertEqual(restarted.state, live)
        self.assertEqual(restarted.settings['windows']['table_scores'], geometry)
        self.assertEqual(len(confirmed_matches(restarted.state)), 0)

    def test_team_league_rotates_members_across_three_games_corrects_and_exports(self):
        """Four teams rotate eight people; personal and club ledgers stay distinct."""
        self.store.create('团队赛','team_league',copy_config=True)
        clubs = [('club-a', '甲队', '#a53f4b'), ('club-b', '乙队', '#316e9c'),
                 ('club-c', '丙队', '#377e72'), ('club-d', '丁队', '#6a5b99')]
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        for player, (identity, name, color) in zip(roster, clubs):
            player.update(name=name+'选手一', team=name, team_id=identity, color=color)
        roster.extend(dict(id=identity+'-reserve', name=name+'选手二', team=name,
                           team_id=identity, color=color, seed=number)
                      for number, (identity, name, color) in enumerate(clubs, 5))
        self.model.set_roster(roster)
        self.store.update(name='四队轮换连续联赛', planned_games=3,
                          qualification_count=2,
                          report_columns={'individual': ['rank', 'name', 'team', 'points', 'games'],
                                          'team': ['rank', 'team', 'points', 'games']})
        self.manager.set_sidebar_mode('team')
        self.manager.set_bottom_mode('team')
        original_seats = [player['id'] for player in roster[:4]]
        reserve_seats = [player['id'] for player in roster[4:]]

        def team_ledger():
            return {row['id']: (row['points'], row['games'])
                    for row in tournament.standings(self.model.state, 'team')}

        def verify_all_sources():
            personal = {player['id']: 0. for player in roster}
            club = {identity: 0. for identity, name, color in clubs}
            for archive in confirmed_matches(self.model.state):
                for result in archive['results']:
                    personal[result['id']] += result['points']
                    club[result['team_id']] += result['points']
            personal = {identity: round(value, 4) for identity, value in personal.items()}
            club = {identity: round(value, 4) for identity, value in club.items()}
            self.assertEqual({identity: points for identity, (points, games) in self.booked().items()}, personal)
            self.assertEqual({identity: points for identity, (points, games) in team_ledger().items()}, club)
            for mode, expected in [('individual', personal), ('team', club)]:
                data = reports.report(self.model.state, self.model.event, mode)
                self.assertEqual({row['id']: row['points'] for row in data['rows']}, expected)
                table = tournament.table_standings(self.model.state, mode)
                self.assertEqual(len(table), 4)
                self.assertEqual([row['player_id'] for row in table],
                                 [player['id'] for player in self.model.state['players']])
                for row in table:
                    self.assertEqual(row['points'], expected[row['id']])
            self.assertEqual({row['id']: row['points'] for row in self.strip()['rows']},
                             {player['id']: personal[player['id']] for player in self.model.state['players']})
            return personal, club

        # First lineup: a closed riichi/tsumo, undo/redo, then all remaining
        # hands settle naturally. Nothing enters either ledger mid-half-game.
        self.assertEqual(self.model.matches.start_next(), 'started')
        empty_personal, empty_team = self.booked(), team_ledger()
        self.model.submit_waits(1, ['3m', '6m'], 4)
        self.model.riichi(1)
        self.model.meld(0, 'pon', ['5m']*3, 3)
        self.win(winner=1, kind='tsumo', han=2)
        paid = copy.deepcopy(self.model.state)
        self.model.history('undo')
        self.assertEqual(self.model.state['pot'], 1000)
        self.assertTrue(self.model.state['players'][1]['riichi'])
        self.model.history('redo')
        self.assertEqual(self.model.state, paid)
        self.assertEqual(self.booked(), empty_personal)
        self.assertEqual(team_ledger(), empty_team)
        self.assertTrue(all(row['played'] == 0 for row in reports.report(self.model.state, self.model.event, 'team')['rows']))
        self.finish_half_game()
        self.assertEqual(self.booked(), empty_personal)
        self.assertEqual(team_ledger(), empty_team)
        self.model.matches.confirm_finished()
        first_confirmation = copy.deepcopy(self.model.state)
        self.model.matches.confirm_finished()
        self.assertEqual(self.model.state, first_confirmation)
        self.assertEqual(len(confirmed_matches(self.model.state)), 1)
        self.assertEqual(self.manager.settlement_data['mode'], 'team')
        self.assertEqual(len(self.manager.settlement_data['standings']), 4)
        verify_all_sources()

        # Complete the real win/settlement timeline, rendering the team's
        # aggregate in the four seat slots without paying anything a second time.
        self.advance_picture(self.manager.animation.duration_ms/1000+.01)
        self.advance_picture(.46)
        self.advance_picture(self.manager.art.win_content_duration()+4.01)
        self.advance_picture(.46)
        self.manager.set_bottom_mode('match_result')
        self.assertEqual(self.manager.stage, 'settlement_hand')
        self.render('standings')
        self.manager.set_bottom_mode('total_result')
        self.assertEqual(self.manager.stage, 'settlement_total')
        self.assertEqual(self.manager.settlement_window(), 'standings')
        self.render('standings')
        self.assertEqual(self.model.state, first_confirmation)

        # Second lineup: all four clubs send their other member. The small
        # strip correctly starts each new person's personal history at zero,
        # while the four club totals retain the first lineup's results.
        self.assertEqual(self.model.matches.start_next(), 'started')
        self.model.configure_broadcast({'seats': reserve_seats})
        self.assertTrue(all(row['points'] == 0. for row in self.strip()['rows']))
        self.assertTrue(any(row['points'] != 0. for row in tournament.table_standings(self.model.state, 'team')))
        before_personal, before_team = self.booked(), team_ledger()
        self.manager.set_bottom_mode('individual')
        self.advance_picture(.4)
        personal_image = self.render('standings')
        self.manager.set_bottom_mode('team')
        self.advance_picture(.4)
        self.assertNotEqual(personal_image, self.render('standings'))
        self.manager.set_sidebar_mode('individual')
        self.render('qualification')
        self.manager.set_sidebar_mode('team')
        self.render('qualification')
        self.assertEqual(self.booked(), before_personal)
        self.assertEqual(team_ledger(), before_team)
        self.model.meld(0, 'pon', ['3p']*3, 1)
        self.model.submit_waits(0, ['1s', '4s'], 5)
        self.model.set_tenpai(0, False)
        self.model.riichi(3)
        self.model.draw()
        self.assertEqual(self.model.state['pot'], 1000)
        self.assertEqual(self.booked(), before_personal)
        self.assertEqual(team_ledger(), before_team)
        self.finish_half_game(offset=1)
        self.assertEqual(team_ledger(), before_team)
        self.model.matches.confirm_finished()
        before_correction = copy.deepcopy(self.model.state)
        before_correction_team = team_ledger()
        self.win(winner=(self.model.state['dealer']+2) % 4, han=3)
        corrected = copy.deepcopy(self.model.state)
        self.assertEqual(len(confirmed_matches(self.model.state)), 2)
        self.assertNotEqual(team_ledger(), before_correction_team)
        self.model.history('undo')
        self.assertEqual(self.model.state, before_correction)
        self.assertEqual(team_ledger(), before_correction_team)
        self.model.history('redo')
        self.assertEqual(self.model.state, corrected)
        self.model.matches.confirm_finished()
        self.assertEqual(self.model.state, corrected)
        self.assertEqual(len(confirmed_matches(self.model.state)), 2)
        verify_all_sources()

        # Third lineup mixes starters and reserves, one member from each club.
        # A dealer-tenpai draw carries the stick to the repeat; finish the
        # half-game through eight rotations, then book it exactly once.
        self.assertEqual(self.model.matches.start_next(), 'started')
        self.model.configure_broadcast({'seats': [original_seats[0], reserve_seats[1],
                                                 original_seats[2], reserve_seats[3]]})
        before_personal, before_team = self.booked(), team_ledger()
        self.model.riichi(0)
        self.model.draw()
        self.assertEqual(self.model.state['lastResult']['next']['reason'], 'dealer_tenpai')
        self.assertEqual(self.booked(), before_personal)
        self.assertEqual(team_ledger(), before_team)
        self.finish_half_game(offset=2)
        self.assertEqual(self.booked(), before_personal)
        self.assertEqual(team_ledger(), before_team)
        self.model.matches.confirm_finished()
        archived = copy.deepcopy(self.model.state)
        self.model.matches.confirm_finished()
        self.assertEqual(self.model.state, archived)
        self.assertEqual(len(confirmed_matches(self.model.state)), 3)
        self.assertEqual(sum(len(match['hands']) for match in confirmed_matches(self.model.state)), 25)
        self.assertEqual({games for points, games in team_ledger().values()}, {3})
        self.assertEqual(sum(games for points, games in self.booked().values()), 12)
        personal, club = verify_all_sources()

        # Export the actual two-sheet XLSX and inspect each numeric points cell
        # against the same report source used by the live individual/team views.
        filename = self.root/'team-rotation-results.xlsx'
        reports.export_xlsx(self.model.state, self.model.event, filename)
        ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(filename) as archive:
            for number, mode in enumerate(('individual', 'team'), 1):
                source = reports.report(self.model.state, self.model.event, mode)
                sheet = ET.fromstring(archive.read(f'xl/worksheets/sheet{number}.xml'))
                points_column = chr(65+source['columns'].index('points'))
                for row_number, row in enumerate(source['rows'], 5):
                    cell = sheet.find(f'.//s:c[@r="{points_column}{row_number}"]/s:v', ns)
                    self.assertIsNotNone(cell)
                    self.assertAlmostEqual(float(cell.text), row['points'], places=4)
        image_files = reports.export_png(reports.report(self.model.state, self.model.event, 'team'), self.root/'team-table.png')
        self.assertTrue(image_files)
        self.assertFalse(QImage(str(image_files[0])).isNull())

        restarted = Model(self.root)
        EventStore(restarted)
        self.assertEqual(restarted.event['kind'], 'team_league')
        self.assertEqual(restarted.state, archived)
        self.assertEqual({row['id']: row['points'] for row in reports.individual_rows(restarted.state, restarted.event)}, personal)
        self.assertEqual({row['id']: row['points'] for row in reports.team_rows(restarted.state, restarted.event)}, club)
        self.assertEqual(len(confirmed_matches(restarted.state)), 3)
        self.assert_conserved()


if __name__ == '__main__':
    unittest.main()
