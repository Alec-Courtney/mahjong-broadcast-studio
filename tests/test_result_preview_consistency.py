"""A manual-result preview cannot silently outlive a roster/team edit."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from PySide6.QtWidgets import QApplication
from event_results import prepare_result, record_result
from event_store import EventStore
from model import Model
import ranking_reports as reports

APP = QApplication.instance() or QApplication([])


class ResultPreviewConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tmp-result-preview-', dir=ROOT/'tests')
        self.model = Model(self.tmp.name)
        self.store = EventStore(self.model)
        self.store.create('补录预览一致性', 'team_league')
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        for index, player in enumerate(roster):
            player.update(team=f'队伍{index+1}', team_id=f'team-{index}', color='#2866bd')
        self.model.set_roster(roster)
        self.seats = [player['id'] for player in roster[:4]]

    def tearDown(self):
        APP.processEvents()
        self.tmp.cleanup()

    def preview(self, match_id=None):
        return prepare_result(self.model, self.seats, [40000, 30000, 25000, 25000], match_id=match_id)

    def test_team_name_or_stable_identity_changed_after_preview_rejects_without_writing(self):
        for patch in ({'team': '重新命名的队伍'}, {'team_id': 'transferred-team'},
                      {'team': '转入队伍', 'team_id': 'transferred-team'}):
            with self.subTest(patch=patch):
                record = self.preview()
                original = copy.deepcopy(record)
                roster = copy.deepcopy(self.model.state['broadcast']['roster'])
                roster[0].update(patch)
                self.model.set_roster(roster)
                before = copy.deepcopy((self.model.state, self.model.undo, self.model.redo))
                session = (self.model.path/'session.json').read_bytes()
                with self.assertRaisesRegex(ValueError, '重新预览'):
                    record_result(self.store, record)
                self.assertEqual((self.model.state, self.model.undo, self.model.redo), before)
                self.assertEqual((self.model.path/'session.json').read_bytes(), session)
                self.assertEqual(record, original)

    def test_fresh_preview_saves_current_team_once_and_repeated_confirm_is_idempotent(self):
        stale = self.preview()
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        roster[0].update(team='转入队伍', team_id='transferred-team')
        self.model.set_roster(roster)
        with self.assertRaisesRegex(ValueError, '重新预览'):
            record_result(self.store, stale)
        fresh = self.preview()
        record_result(self.store, fresh)
        record_result(self.store, fresh)
        self.assertEqual(self.model.state['broadcast']['matches'], [fresh])
        stored = next(row for row in fresh['results'] if row['id'] == self.seats[0])
        self.assertEqual((stored['team'], stored['team_id']), ('转入队伍', 'transferred-team'))
        team = next(row for row in reports.team_rows(self.model.state, self.model.event)
                    if row['id'] == 'transferred-team')
        self.assertEqual(team['played'], 1)
        self.assertEqual(team['points'], stored['points'])

    def test_historical_correction_after_transfer_keeps_the_original_team(self):
        first = self.preview()
        record_result(self.store, first)
        corrected = self.preview(match_id=first['id'])
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        roster[0].update(team='转入队伍', team_id='transferred-team')
        self.model.set_roster(roster)
        record_result(self.store, corrected)
        stored = next(row for row in self.model.state['broadcast']['matches'][0]['results']
                      if row['id'] == self.seats[0])
        self.assertEqual((stored['team'], stored['team_id']), ('队伍1', 'team-0'))
        self.assertEqual(len(self.model.state['broadcast']['matches']), 1)


if __name__ == '__main__':
    unittest.main()
