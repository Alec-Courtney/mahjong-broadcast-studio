"""All event standings use chronological points and the recorded half-game seats."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from PySide6.QtWidgets import QApplication
from model import Model
import ranking_reports as reports
import tournament

APP = QApplication.instance() or QApplication([])


class RankingArrivalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tmp-rank-arrival-', dir=ROOT/'tests')
        self.addCleanup(self.tmp.cleanup)
        self.model = Model(self.tmp.name)
        self.state = self.model.state
        self.event = dict(kind='team_league', name='同分排序验证')
        self.roster = self.state['broadcast']['roster']
        for index, player in enumerate(self.roster):
            team = ('A', 'B', 'A', 'C')[index]
            player.update(team=team, team_id=team, seed=index+1)

    def match(self, points, seats, *, identity=None, legacy=False):
        results = [dict(p, rank=index+1, points=value)
                   for index, (p, value) in enumerate(zip(self.roster, points))]
        record = dict(id=identity or str(len(self.state['broadcast']['matches'])),
                      committed=True, hands=[], results=results)
        if legacy:
            record['initial_players'] = [dict(id=pid) for pid in seats]
        else:
            record['seats'] = seats
        self.state['broadcast']['matches'].append(record)

    def assert_top(self, expected, mode='individual'):
        result = tournament.standings(self.state, mode)
        report = reports.report(self.state, self.event, mode)['rows']
        self.assertEqual([(r['id'], r['points']) for r in result],
                         [(r['id'], r['points']) for r in report])
        self.assertEqual([r['id'] for r in result[:len(expected)]], expected)

    def test_first_attainment_wins_even_after_leaving_and_returning_to_the_total(self):
        seats = ['p1', 'p2', 'p3', 'p4']
        self.match([-10, 20, -5, -5], seats)
        self.match([30, -10, -10, -10], seats)
        self.match([0, 10, -5, -5], seats)
        self.assert_top(['p2', 'p1'])

    def test_same_half_uses_legacy_seats_instead_of_result_order_or_priority(self):
        self.match([20, 20, -10, -30], ['p2', 'p1', 'p3', 'p4'], legacy=True)
        self.assert_top(['p2', 'p1'])

    def test_team_reaches_a_total_only_after_all_member_contributions(self):
        seats = ['p1', 'p2', 'p3', 'p4']
        self.match([30, 30, -10, -50], seats)
        self.match([5, 0, 5, -10], seats)
        self.assert_top(['B', 'A'], 'team')

    def test_same_half_tied_teams_use_their_earliest_seated_member(self):
        self.match([20, 30, 10, -60], ['p3', 'p2', 'p1', 'p4'])
        self.assert_top(['A', 'B'], 'team')

    def test_earlier_current_archive_is_not_moved_after_other_table_records(self):
        seats = ['p1', 'p2', 'p3', 'p4']
        self.state['phase'] = 'finished'
        self.match([-5, 20, -10, -5], seats, identity=self.state['id'])
        self.match([25, 0, -10, -15], seats)
        self.assert_top(['p2', 'p1'])


if __name__ == '__main__':
    unittest.main()
