"""Booked first-half and cumulative values must never contain a live projection."""
import copy
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from table_score_strip import table_score_strip, format_points


def session():
    players = [dict(id=f'p{i}', name=f'选手{i}', score=25000) for i in range(4)]
    return dict(id='current', phase='playing', players=players,
                broadcast=dict(config={}, roster=copy.deepcopy(players), matches=[], active_node_id='table-a'))


def match(identity, values, node='table-a', committed=True, ids=None):
    return dict(id=identity, bracket_node_id=node, committed=committed,
                results=[dict(id=player_id, points=value) for player_id, value in
                         zip(ids or [f'p{i}' for i in range(4)], values)])


def event(games=2):
    return dict(kind='individual_knockout', bracket=dict(nodes=[dict(id='table-a', planned_games=games)]))


class TableScoreStripTests(unittest.TestCase):
    def test_team_direct_render_is_hidden_and_personal_event_metadata_restores_it(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage,QPainter
        from PySide6.QtWidgets import QApplication
        from table_score_strip import draw_table_score_strip,SCORE_STRIP_SIZE
        app=QApplication.instance() or QApplication([])
        state=session();state['broadcast']['config']['event_mode']='team'
        model=SimpleNamespace(state=state,event={'kind':'team_league'},settings={})
        before=copy.deepcopy(state)
        for kind,visible in (('team_league',False),('individual_league',True)):
            model.event['kind']=kind
            frame=QImage(*SCORE_STRIP_SIZE,QImage.Format.Format_ARGB32_Premultiplied)
            frame.fill(Qt.GlobalColor.transparent);painter=QPainter(frame)
            try:draw_table_score_strip(painter,model)
            finally:painter.end()
            self.assertEqual(frame.pixelColor(2,2).alpha()>0,visible)
        self.assertEqual(state,before)

    def test_two_and_three_digit_totals_do_not_resize_any_of_the_four_players(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage,QPainter
        from PySide6.QtWidgets import QApplication
        import panel_typography
        from table_score_strip import draw_table_score_strip,SCORE_STRIP_SIZE
        app=QApplication.instance() or QApplication([])
        state=session();state['broadcast'].pop('active_node_id')
        state['broadcast']['config']['event_mode']='individual'
        for player,name in zip(state['players'],('園田賢','岡田紗佳','伊達朱里紗','鈴木優')):player['name']=name
        model=SimpleNamespace(state=state,event={},settings={})
        original=panel_typography.draw_single_line
        for layout in ('compact','columns'):
            state['broadcast']['config']['table_score_layout']=layout
            sizes=[]
            for totals in ((67.7,-46.7,1.4,-22.4),(167.7,-146.7,101.4,-122.4)):
                for profile,points in zip(state['broadcast']['roster'],totals):profile['base_points']=points
                drawn=[]
                def capture(*args,**kwargs):
                    result=original(*args,**kwargs);drawn.append(result.font.pixelSize());return result
                frame=QImage(*SCORE_STRIP_SIZE,QImage.Format.Format_ARGB32_Premultiplied)
                frame.fill(Qt.GlobalColor.transparent);painter=QPainter(frame)
                try:
                    with patch.object(panel_typography,'draw_single_line',side_effect=capture):draw_table_score_strip(painter,model)
                finally:painter.end()
                sizes.append(drawn[1:])
            self.assertEqual(sizes[0],sizes[1])
            self.assertEqual(len(set(sizes[0])),1)

    def test_all_four_names_and_points_share_one_font_and_keep_complete_text(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage,QPainter
        from PySide6.QtWidgets import QApplication
        import panel_typography
        from table_score_strip import draw_table_score_strip,SCORE_STRIP_SIZE
        app=QApplication.instance() or QApplication([])
        state=session();state['broadcast'].pop('active_node_id')
        state['broadcast']['config']['event_mode']='individual'
        names=('鈴木優','伊達朱里紗','佐々木寿人','Jean-Christophe Dupont')
        points=(1.2,-12.3,123.4,-2345.6)
        for player,profile,name,point in zip(state['players'],state['broadcast']['roster'],names,points):
            player['name']=name;profile.update(name=name,base_points=point)
        model=SimpleNamespace(state=state,event={},settings={})
        original=panel_typography.draw_single_line
        for layout in ('compact','columns'):
            state['broadcast']['config']['table_score_layout']=layout
            for scale in (.6,1.,1.6):
                with self.subTest(layout=layout,scale=scale):
                    model.settings['overlay_font_scale']=scale;placements=[]
                    def capture(*args,**kwargs):
                        result=original(*args,**kwargs);placements.append(result);return result
                    frame=QImage(*SCORE_STRIP_SIZE,QImage.Format.Format_ARGB32_Premultiplied)
                    frame.fill(Qt.GlobalColor.transparent);painter=QPainter(frame)
                    try:
                        with patch.object(panel_typography,'draw_single_line',side_effect=capture):
                            draw_table_score_strip(painter,model)
                    finally:painter.end()
                    body=placements[1:]
                    self.assertEqual(len(body),8)
                    self.assertEqual(len({item.font.pixelSize() for item in body}),1)
                    self.assertEqual([item.value for item in body],
                                     [value for name,point in zip(names,points) for value in (name,format_points(point))])
                    for item in body:
                        self.assertLessEqual(item.ink_bounds.width(),item.box.width())
                        self.assertLessEqual(item.ink_bounds.height(),item.box.height())
                        self.assertTrue(item.box.adjusted(-.01,-.01,.01,.01).contains(item.ink_bounds))

    def test_real_raw_scores_are_converted_to_booked_pt_before_display(self):
        import tournament
        state = session()
        state.update(phase='finished', initialDealer=0,
                     rules=dict(startPoints=25000, returnPoints=30000,
                                uma=[50, 10, -10, -30], okaEnabled=False, tieBreak='share'))
        for player, score in zip(state['players'], [42000, 30000, 18000, 10000]):
            player['score'] = score
        tournament.ensure_broadcast(state)
        tournament.archive_match(state, committed=True)
        data = table_score_strip(state, event())
        self.assertEqual([row['points'] for row in data['rows']], [62., 10., -22., -50.])

    def test_first_game_unknown_until_a_confirmed_result_exists(self):
        state = session()
        state['broadcast']['matches'].extend([
            match('unbooked', [60, 10, -20, -50], committed=False),
            match('current', [80, 20, -30, -70]),
        ])
        before = copy.deepcopy(state)
        data = table_score_strip(state, event())
        self.assertEqual(data['mode'], 'first_game')
        self.assertEqual([row['points'] for row in data['rows']], [None]*4)
        self.assertIn('等待', data['detail'])
        self.assertEqual(state, before)

    def test_second_game_keeps_only_first_result_in_seat_order(self):
        state = session()
        state['players'].reverse()
        state['broadcast']['matches'].extend([
            match('prior-round', [300, 100, -100, -300], 'prior-round'),
            match('same-round-other-table', [900]*4, 'table-b'),
            match('first', [67.7, 1.4, -46.7, -22.4]),
            match('current', [99, 30, -40, -89]),
        ])
        for profile in state['broadcast']['roster']:
            profile['base_points'] = 1000
        data = table_score_strip(state, event())
        self.assertEqual(data['source_match_id'], 'first')
        self.assertEqual([row['points'] for row in data['rows']], [-22.4, -46.7, 1.4, 67.7])
        # Finishing/confirming game two must not turn the fixed first result
        # into a two-game aggregate or replace it with the second result.
        state['phase'] = 'finished'
        self.assertEqual(table_score_strip(state, event())['rows'], data['rows'])

    def test_multi_game_node_sums_only_that_round_without_baselines(self):
        state = session()
        state['broadcast']['roster'][0]['base_points'] = 500
        state['broadcast']['matches'].extend([
            match('prior-round', [100]*4, 'prior-round'),
            match('first', [60, 10, -20, -50]),
            match('second', [-30, 40, 10, -20]),
            match('current', [99]*4),
            match('unconfirmed', [80]*4, committed=False),
        ])
        data = table_score_strip(state, event(4))
        self.assertEqual(data['mode'], 'cumulative')
        self.assertEqual(data['scope'], 'round')
        self.assertEqual([row['points'] for row in data['rows']], [30, 50, -10, -70])
        self.assertTrue(all(row['games'] == 2 for row in data['rows']))

    def test_league_totals_use_individual_baselines_and_confirmed_games(self):
        state = session()
        state['broadcast'].pop('active_node_id')
        state['broadcast']['roster'][0].update(base_points=12.3, games=4)
        state['broadcast']['matches'].extend([
            match('first', [10, -20, 30, -20]),
            match('second', [9, 8, 7, 6], ids=['outsider', 'p2', 'p3', 'p1']),
            match('current', [500]*4),
        ])
        data = table_score_strip(state, dict(kind='individual_league', planned_games=2))
        self.assertEqual(data['mode'], 'cumulative')
        self.assertEqual([row['points'] for row in data['rows']], [22.3, -14, 38, -13])
        self.assertEqual(data['rows'][0]['games'], 5)
        state['phase'] = 'finished'
        self.assertEqual(table_score_strip(state)['rows'][0]['points'], 522.3)

    def test_corrected_duplicate_archive_counts_once_and_uses_correction(self):
        state = session()
        state['broadcast']['matches'].extend([
            match('first', [10, 20, -10, -20]),
            match('first', [11, 19, -10, -20]),
        ])
        data = table_score_strip(state, event())
        self.assertEqual(data['confirmed_games'], 1)
        self.assertEqual(data['rows'][0]['points'], 11)

    def test_manual_first_game_mode_ignores_unrelated_table(self):
        state = session()
        state['broadcast'].pop('active_node_id')
        state['broadcast']['config']['table_score_mode'] = 'first_game'
        state['broadcast']['matches'].extend([
            match('other', [100]*4, ids=['p0', 'p1', 'p2', 'outsider']),
            match('first', [10, 20, -10, -20]),
        ])
        self.assertEqual(table_score_strip(state)['source_match_id'], 'first')

    def test_format_has_sign_decimal_and_unknown_distinction(self):
        self.assertEqual(format_points(None), '—')
        self.assertEqual(format_points(0), '+0.0')
        self.assertEqual(format_points(-0.00001), '+0.0')
        self.assertEqual(format_points(-22.4), '−22.4')
        self.assertEqual(format_points(67.7), '+67.7')


if __name__ == '__main__':
    unittest.main()
