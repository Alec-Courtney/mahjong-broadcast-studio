"""Half-game penalties persist separately and are included exactly once."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget
from event_history import EventHistoryDialog
from event_results import prepare_result, record_result
from event_store import EventStore, resolve_active_directory
from event_workspace import EventWorkspaceDialog, ResultsDialog
from model import Model
from penalty_dialog import MatchPenaltyDialog, PenaltyEditorDialog
import ranking_reports as reports
import event_schedule as schedule
import tournament

APP = QApplication.instance() or QApplication([])
TIME = '2026-11-07T20:45:12+08:00'


class MatchPenaltyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tmp-match-penalties-', dir=ROOT/'tests')
        self.addCleanup(self.tmp.cleanup)
        self.model = Model(self.tmp.name)
        self.store = EventStore(self.model)
        self.store.create('个人判罚测试', 'individual_league')
        self.seats = ['p1', 'p2', 'p3', 'p4']
        self.scores = [41000, 29000, 19000, 11000]

    def record(self):
        record = prepare_result(self.model, self.seats, self.scores)
        record_result(self.store, record)
        return record

    def penalty(self, match, **changes):
        values = dict(scope='player', target='p1', points=20, time=TIME,
                      reason='误和', note='现场裁定')
        values.update(changes)
        return self.model.records.save_penalty(match['id'], **values)

    def totals(self, mode='individual'):
        data = reports.report(self.model.state, self.model.event, mode)
        visible = {row['id']: row['points'] for row in tournament.standings(self.model.state, mode)}
        self.assertEqual(visible, {row['id']: row['points'] for row in data['rows']})
        return visible

    def test_personal_penalty_reopens_and_survives_manual_correction(self):
        match = self.record()
        original = self.totals()
        identity = self.penalty(match, points=20.5)
        self.assertAlmostEqual(self.totals()['p1'], original['p1']-20.5)
        booked = self.model.records.penalty_match(match['id'])
        self.assertEqual(booked['results'], match['results'])
        self.assertEqual(booked['penalties'][0], dict(id=identity, scope='player', target='p1',
            points=20.5, time=TIME, reason='误和', note='现场裁定'))
        restarted = Model(resolve_active_directory(self.store.root), asset_root=self.model.asset_root)
        self.assertEqual(restarted.records.penalty_match(match['id']), booked)
        correction = prepare_result(self.model, self.seats, [40000, 30000, 19000, 11000], match_id=match['id'])
        record_result(self.store, correction)
        booked = self.model.records.penalty_match(match['id'])
        self.assertEqual(booked['penalties'][0]['id'], identity)
        self.assertAlmostEqual(self.totals()['p1'], correction['results'][0]['points']-20.5)
        self.model.records.void_penalty(match['id'], identity)
        self.assertTrue(self.model.records.penalty_match(match['id'])['penalties'][0]['voided_at'])
        self.assertEqual(self.totals()['p1'], correction['results'][0]['points'])

    def test_team_player_and_collective_penalties_use_the_archived_team_once(self):
        self.store.create('团队判罚测试', 'team_league')
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        for player, team in zip(roster, ('A', 'A', 'B', 'C')):
            player.update(team=team, team_id=team)
        self.model.set_roster(roster)
        match = self.record()
        before_individual = self.totals()
        before_team = self.totals('team')
        self.penalty(match, points=20)
        collective = self.penalty(match, scope='team', target='A', points=4.5, reason='队伍迟到', note='')
        self.assertAlmostEqual(self.totals()['p1'], before_individual['p1']-20)
        self.assertEqual(self.totals()['p2'], before_individual['p2'])
        self.assertAlmostEqual(self.totals('team')['A'], before_team['A']-24.5)
        moved = copy.deepcopy(self.model.state['broadcast']['roster'])
        moved[0].update(team='B', team_id='B')
        self.model.set_roster(moved)
        self.assertAlmostEqual(self.totals('team')['A'], before_team['A']-24.5)
        self.penalty(match, scope='team', target='A', points=2.5, reason='裁定更正', penalty_id=collective)
        self.assertEqual(len(self.model.records.penalty_match(match['id'])['penalties']), 2)
        self.assertAlmostEqual(self.totals('team')['A'], before_team['A']-22.5)

    def test_live_confirmation_can_be_undone_and_rebooked_without_losing_the_penalty(self):
        self.model.field('roundIndex', 7)
        self.model.field('dealer', 3)
        self.model.settle(dict(type='ron', winner=0, loser=1, han=3, fu=40), None)
        identity = self.model.state['id']
        with self.assertRaisesRegex(ValueError, '请先确认'):
            self.model.records.penalty_match(identity)
        self.model.matches.confirm_finished()
        match = self.model.records.penalty_match(identity)
        other_table = self.record()
        self.penalty(match)
        net = self.totals()['p1']
        summary = tournament.final_settlement_summary(self.model.state, 'individual')
        self.assertEqual(summary['players'][0]['points'], match['results'][0]['points']-20)
        self.model.history('undo')
        self.assertEqual([m['id'] for m in reports.confirmed_matches(self.model.state)], [other_table['id']])
        self.assertEqual(self.model.state['broadcast']['matches'][0]['penalties'][0]['points'], 20)
        self.model.matches.confirm_finished()
        self.assertEqual([m['id'] for m in reports.confirmed_matches(self.model.state)], [identity, other_table['id']])
        self.assertEqual(self.totals()['p1'], net)
        self.model.matches.start_next()
        self.assertEqual(self.totals()['p1'], net)

    def test_invalid_or_failed_writes_do_not_publish_a_penalty(self):
        match = self.record()
        self.model.set_score(0, 26000)
        before = copy.deepcopy((self.model.state, self.model.undo, self.model.redo))
        for changes in (dict(target='absent'), dict(points=-5), dict(points=.55), dict(reason=' '),
                        dict(time='2026-11-07'), dict(scope='team', target='A')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.penalty(match, **changes)
        with patch.object(self.model.repository, 'save', side_effect=OSError('write failed')):
            with self.assertRaisesRegex(OSError, 'write failed'):
                self.penalty(match)
        self.assertEqual((self.model.state, self.model.undo, self.model.redo), before)

    def test_knockout_advancement_rechecks_results_when_a_penalty_changes_the_order(self):
        self.store.create('淘汰赛判罚测试', 'individual_knockout')
        bracket = schedule.generate(self.model.state['broadcast']['roster'], advance_count=1)
        self.store.update(bracket=bracket)
        node_id = bracket['nodes'][0]['id']
        match = prepare_result(self.model, self.seats, self.scores, node_id=node_id)
        record_result(self.store, match)
        self.assertEqual(schedule.confirm_advancement(self.store, node_id), ['p1'])
        node = self.model.event['bracket']['nodes'][0]
        self.assertEqual(node['result_signature'], schedule.signature(self.model.state, node))
        self.penalty(match, points=200)
        self.assertNotEqual(node['result_signature'], schedule.signature(self.model.state, node))
        self.assertEqual(schedule.preview_advancement(self.model.state, self.model.event, node_id)[0]['id'], 'p2')

    def test_results_and_history_open_the_penalty_record_and_show_saved_values(self):
        match = self.record()
        owner = QWidget()
        owner.model = self.model
        workspace = EventWorkspaceDialog(owner)
        results = ResultsDialog(workspace)
        results.table.selectRow(0)
        dialog = MatchPenaltyDialog(results, match, self.model.settings, model=self.model)
        editor = PenaltyEditorDialog(dialog, self.model, match)
        try:
            editor.target.setCurrentIndex(editor.target.findData('p1'))
            editor.points.setValue(12.5)
            editor.reason.setText('误和')
            editor.remark.setText('复核备注')
            editor.save()
            self.assertEqual(editor.result(), QDialog.DialogCode.Accepted, editor.error.text())
            dialog.reload()
            self.assertEqual(dialog.table.item(0, 2).text(), '−12.5')
            self.assertEqual(dialog.table.item(0, 4).text(), '复核备注')
            dialog.table.selectRow(0)
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
                dialog.void()
            self.assertTrue(dialog.void_button.isEnabled())
            results.reload()
            self.assertIn('−12.5 pt · 误和', results.table.item(0, 3).text())
            self.assertIn(match['results'][0]['name'], results.table.item(0, 3).text())
            self.assertEqual(results.table.columnCount(), 7)
            history = EventHistoryDialog(workspace, self.model.state, self.model.event)
            self.assertTrue(history.penalties_button.isEnabled())
            self.assertEqual(history.final_table.item(0, 4).text(),
                             reports.display('points', match['results'][0]['points']-12.5))
        finally:
            workspace._allow_close = True
            workspace.close()
            owner.deleteLater()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            APP.processEvents()


if __name__ == '__main__':
    unittest.main()
