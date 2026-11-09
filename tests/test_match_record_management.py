"""Confirmed records delete transactionally and retain their original save time."""
import copy
from datetime import datetime
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src')]

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from event_results import prepare_result, record_result, recorded_time
from event_store import EventStore
from event_workspace import EventWorkspaceDialog, ResultsDialog
from model import Model
import event_schedule as schedule
import ranking_reports as reports
import tournament

APP = QApplication.instance() or QApplication([])
TIME = '2026-11-09T03:20:30+00:00'
LATER = '2026-11-10T05:00:00+00:00'


class MatchRecordManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tmp-record-management-', dir=ROOT/'tests')
        self.addCleanup(self.tmp.cleanup)
        self.model = Model(self.tmp.name)
        self.store = EventStore(self.model)
        self.seats = [player['id'] for player in self.model.state['players']]

    def manual(self, **changes):
        record = prepare_result(self.model, self.seats, [41000, 29000, 19000, 11000], **changes)
        with patch('event_record_service.now', return_value=TIME):
            record_result(self.store, record)
        return next(match for match in self.model.state['broadcast']['matches'] if match['id'] == record['id'])

    def finish(self):
        self.model.field('roundIndex', 7)
        self.model.field('dealer', 3)
        self.model.settle(dict(type='ron', winner=0, loser=1, han=3, fu=30), None)
        self.model.matches.confirm_finished()
        return self.model.records.penalty_match(self.model.state['id'])

    def snapshot(self):
        return copy.deepcopy((self.model.state, self.model.settings, self.model.event, self.model.undo,
                              self.model.redo, self.model.last_transition))

    def test_record_time_starts_at_save_and_survives_correction_penalty_and_restart(self):
        match = self.manual()
        self.assertEqual(match['recorded_at'], TIME)
        correction = prepare_result(self.model, self.seats, [40000, 30000, 19000, 11000], match_id=match['id'])
        correction['recorded_at'] = LATER
        with patch('event_record_service.now', return_value=LATER):
            record_result(self.store, correction)
            self.model.records.save_penalty(match['id'], scope='player', target=self.seats[0],
                                           points=10, time=LATER, reason='误和')
        saved = Model(self.model.path).records.penalty_match(match['id'])
        self.assertEqual(saved['recorded_at'], TIME)
        self.assertEqual(recorded_time(saved), datetime.fromisoformat(TIME).astimezone().strftime('%Y-%m-%d %H:%M'))

    def test_live_record_time_survives_repeat_confirmation_and_score_edit(self):
        with patch('event_metadata.now', return_value=TIME):
            match = self.finish()
        with patch('event_metadata.now', return_value=LATER):
            self.model.matches.confirm_finished()
            self.model.set_score(0, self.model.state['players'][0]['score']+100)
        self.assertEqual(self.model.records.penalty_match(match['id'])['recorded_at'], TIME)

    def test_legacy_records_keep_unknown_time_after_correction(self):
        record = dict(self.manual())
        record.pop('recorded_at')
        self.model.state['broadcast']['matches'] = [record]
        self.model.save()
        correction = prepare_result(self.model, self.seats, [40000, 30000, 19000, 11000], match_id=record['id'])
        record_result(self.store, correction)
        self.assertNotIn('recorded_at', self.model.records.penalty_match(record['id']))
        self.assertEqual(recorded_time(record), '未记录')

    def test_delete_removes_team_points_penalties_and_games_and_keeps_other_record(self):
        self.store.create('团队删除测试', 'team_league')
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        for player in roster:player.update(team=player['id'], team_id=player['id'])
        self.model.set_roster(roster)
        first, second = self.manual(), self.manual()
        self.model.records.save_penalty(first['id'], scope='team', target=self.seats[0], points=20,
                                       time=TIME, reason='迟到')
        current_table = copy.deepcopy({key:value for key,value in self.model.state.items() if key!='broadcast'})
        self.model.records.delete_match(first['id'])
        self.assertEqual([record['id'] for record in reports.confirmed_matches(self.model.state)], [second['id']])
        for mode in ('individual', 'team'):
            values = {row['id']:row for row in reports.report(self.model.state, self.model.event, mode)['rows']}
            self.assertEqual(values[self.seats[0]]['points'], second['results'][0]['points'])
            self.assertEqual(values[self.seats[0]]['played'], 1)
        self.assertEqual({key:value for key,value in self.model.state.items() if key!='broadcast'}, current_table)
        self.assertEqual(reports.confirmed_matches(Model(self.model.path).state), [second])

    def test_delete_live_archive_removes_technical_stats_and_cannot_return_through_undo_redo(self):
        first = self.finish()
        second = self.manual()
        self.model.matches.start_with_seats(self.seats)
        self.model.field('honba', 2)
        self.model.history('undo')
        self.assertTrue(self.model.redo)
        self.model.records.delete_match(first['id'])
        self.assertEqual(reports.technical_rows(self.model.state)[self.seats[0]]['hands'], 0)
        for direction in ('redo', 'undo', 'undo', 'redo'):
            self.model.history(direction)
            self.assertNotIn(first['id'], [record['id'] for record in self.model.state['broadcast']['matches']])
            self.assertIn(second['id'], [record['id'] for record in reports.confirmed_matches(self.model.state)])

    def test_delete_current_result_keeps_final_table_and_allows_explicit_reconfirmation(self):
        match = self.finish()
        self.model.records.delete_match(match['id'])
        self.assertEqual(self.model.state['phase'], 'finished')
        self.assertEqual(len(self.model.state['broadcast']['hands']), 1)
        self.assertEqual(reports.confirmed_matches(self.model.state), [])
        self.model.field('honba', 2)
        self.assertEqual(reports.confirmed_matches(self.model.state), [])
        with patch('event_metadata.now', return_value=LATER):self.model.matches.confirm_finished()
        self.assertEqual(self.model.records.penalty_match(match['id'])['recorded_at'], LATER)

    def test_deleted_current_result_can_be_excluded_when_starting_the_next_table(self):
        match = self.finish()
        self.model.records.delete_match(match['id'])
        restarted = Model(self.model.path)
        self.assertTrue(restarted.state['broadcast']['result_discarded'])
        restarted.matches.start_with_seats(self.seats)
        self.assertEqual(restarted.state['phase'], 'playing')
        self.assertNotEqual(restarted.state['id'], match['id'])
        self.assertEqual(restarted.state['broadcast']['matches'], [])
        self.assertNotIn('result_discarded', restarted.state['broadcast'])

    def test_deleted_current_result_leaves_cumulative_stats_but_retains_table_review(self):
        self.finish()
        self.model.matches.start_with_seats(self.seats)
        self.model.draw([])
        self.assertTrue(self.model.next_hand())
        current = self.finish()
        self.assertEqual([row['hands'] for row in tournament.statistics(self.model.state)], [3]*4)
        self.model.records.delete_match(current['id'])
        restarted = Model(self.model.path)
        for model in (self.model, restarted):
            self.assertEqual([row['hands'] for row in tournament.statistics(model.state)], [1]*4)
            self.assertEqual([row['hands'] for row in tournament.statistics(model.state, 'match')], [2]*4)
            self.assertEqual([row['wins'] for row in tournament.statistics(model.state)], [1,0,0,0])
        self.model.history('undo')
        self.model.history('undo')
        self.assertEqual(self.model.state['phase'], 'playing')
        self.assertEqual([row['hands'] for row in tournament.statistics(self.model.state, 'match')], [1]*4)
        self.assertEqual([row['hands'] for row in tournament.statistics(self.model.state)], [1]*4)
        with self.assertRaisesRegex(ValueError, '尚未结束并确认'):
            self.model.matches.start_with_seats(self.seats)
        self.model.history('redo')
        self.model.history('redo')
        self.assertEqual([row['hands'] for row in tournament.statistics(self.model.state)], [1]*4)
        self.model.matches.confirm_finished()
        self.assertEqual([row['hands'] for row in tournament.statistics(self.model.state)], [3]*4)

    def test_stale_correction_preview_cannot_restore_a_deleted_result(self):
        match = self.manual()
        correction = prepare_result(self.model, self.seats, [40000, 30000, 19000, 11000], match_id=match['id'])
        self.model.records.delete_match(match['id'])
        before = self.snapshot()
        saved = (self.model.path/'session.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '记录已删除'):record_result(self.store, correction)
        with self.assertRaisesRegex(ValueError, '记录已删除'):self.model.records.write_manual(correction)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.model.path/'session.json').read_bytes(), saved)

    def test_failed_delete_preserves_disk_state_history_and_notifications(self):
        match = self.finish()
        self.model.history('undo')
        self.model.history('redo')
        before = self.snapshot()
        saved = (self.model.path/'session.json').read_bytes()
        observed = []
        self.model.changed.connect(lambda *_: observed.append('changed'))
        self.model.display_changed.connect(lambda: observed.append('display'))
        with patch.object(self.model.repository, 'save', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):self.model.records.delete_match(match['id'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.model.path/'session.json').read_bytes(), saved)
        self.assertEqual(observed, [])

    def test_delete_rejects_changed_and_missing_record(self):
        match = self.manual()
        correction = prepare_result(self.model, self.seats, [40000, 30000, 19000, 11000], match_id=match['id'])
        record_result(self.store, correction)
        with self.assertRaisesRegex(ValueError, '已改变'):self.model.records.delete_match(match['id'], match)
        self.model.records.delete_match(match['id'])
        with self.assertRaisesRegex(ValueError, '已删除'):self.model.records.delete_match(match['id'])

    def test_knockout_delete_reopens_advancement_and_replacement_can_be_confirmed(self):
        self.store.create('淘汰删除测试', 'individual_knockout')
        self.store.update(bracket=schedule.generate(self.model.state['broadcast']['roster']))
        node_id = self.model.event['bracket']['nodes'][0]['id']
        match = self.manual(node_id=node_id)
        schedule.confirm_advancement(self.store, node_id)
        self.model.records.delete_match(match['id'])
        node = self.model.event['bracket']['nodes'][0]
        self.assertEqual(node['status'], 'running')
        self.assertEqual(node['winners'], [])
        self.assertNotIn('result_signature', node)
        self.manual(node_id=node_id)
        self.assertEqual(len(schedule.confirm_advancement(self.store, node_id)), 2)

    def test_invalid_record_timestamp_is_rejected(self):
        state = copy.deepcopy(self.model.state)
        state['broadcast']['matches'] = [dict(self.manual())]
        state['broadcast']['matches'][0]['recorded_at'] = '2026-11-09'
        with self.assertRaisesRegex(ValueError, '记录时间无效'):tournament.validate_broadcast(state)

    def test_knockout_delete_cannot_break_a_downstream_table_that_has_started(self):
        self.store.create('淘汰上下游测试', 'individual_knockout')
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        roster += [dict(roster[index], id=f'extra-{index}') for index in range(4)]
        self.model.set_roster(roster)
        self.store.update(bracket=schedule.generate(roster))
        nodes = self.model.event['bracket']['nodes']
        first = self.manual(node_id=nodes[0]['id'])
        schedule.confirm_advancement(self.store, nodes[0]['id'])
        self.seats = [player['id'] for player in roster[4:]]
        self.manual(node_id=nodes[1]['id'])
        schedule.confirm_advancement(self.store, nodes[1]['id'])
        schedule.start_node(self.store, nodes[2]['id'])
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, '下游淘汰桌已经开赛'):
            self.model.records.delete_match(first['id'])
        self.assertEqual(self.snapshot(), before)

    def test_two_row_selection_delete_confirmation_and_empty_controls(self):
        first, second = self.manual(title='半庄一'), self.manual(title='半庄二')
        self.model.records.save_penalty(second['id'], scope='player', target=self.seats[3], points=12.5,
                                       time=TIME, reason='误和', note='现场复核确认')
        owner = QWidget()
        owner.model = self.model
        workspace = EventWorkspaceDialog(owner)
        dialog = ResultsDialog(workspace)
        try:
            self.assertEqual(dialog.table.rowCount(), 4)
            self.assertEqual(dialog.table.rowSpan(0, 0), 2)
            for index,result in enumerate(first['results']):
                self.assertEqual(dialog.table.item(index//2, 3+(index%2)*2).text(), result['name'])
                self.assertEqual(dialog.table.item(index//2, 4+(index%2)*2).text(), f"{result['points']:+.1f}".replace('-', '−'))
            self.assertEqual(dialog.table.item(0, 2).text(), recorded_time(first).replace(' ', '\n'))
            self.assertEqual(dialog.table.columnCount(), 7)
            self.assertIn('−12.5 pt · 误和', dialog.table.item(3, 5).text())
            self.assertIn('现场复核确认', dialog.table.item(3, 5).toolTip())
            self.assertEqual(dialog.table.item(3, 6).text(), '−61.5')
            self.assertNotIn('误和', dialog.table.item(2, 5).text())
            dialog.table.setCurrentCell(3, 5)
            self.assertEqual(dialog.selected_record()['id'], second['id'])
            self.assertEqual({cell.row() for cell in dialog.table.selectedIndexes()}, {2, 3})
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
                dialog.delete_button.click()
            self.assertEqual(len(reports.confirmed_matches(self.model.state)), 2)
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
                dialog.delete_button.click()
                self.assertEqual([match['id'] for match in reports.confirmed_matches(self.model.state)], [first['id']])
                dialog.delete_button.click()
            self.assertEqual(dialog.table.rowCount(), 0)
            self.assertFalse(dialog.delete_button.isEnabled())
            self.assertFalse(dialog.penalties_button.isEnabled())
        finally:
            dialog.deleteLater()
            workspace._allow_close = True
            workspace.close()
            owner.deleteLater()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            APP.processEvents()

    def test_player_penalties_show_each_active_reason_and_team_penalty_names_its_team(self):
        self.store.create('判罚展示测试', 'team_league')
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        for player in roster:player.update(team='队伍甲', team_id='team-a')
        self.model.set_roster(roster)
        match = self.manual()
        for points,reason in ((2,'迟到'),(4,'误和')):
            self.model.records.save_penalty(match['id'], scope='player', target=self.seats[2],
                                           points=points, time=TIME, reason=reason)
        voided = self.model.records.save_penalty(match['id'], scope='player', target=self.seats[2],
                                               points=8, time=TIME, reason='已撤销原因')
        self.model.records.void_penalty(match['id'], voided)
        self.model.records.save_penalty(match['id'], scope='team', target='team-a', points=10,
                                       time=TIME, reason='队伍迟到')
        owner = QWidget()
        owner.model = self.model
        workspace = EventWorkspaceDialog(owner)
        dialog = ResultsDialog(workspace)
        try:
            self.assertIn('−2 pt · 迟到', dialog.table.item(1, 3).text())
            self.assertIn('−4 pt · 误和', dialog.table.item(1, 3).text())
            self.assertNotIn('已撤销原因', dialog.table.item(1, 3).text())
            self.assertIn('队伍甲 −10 pt · 队伍迟到', dialog.table.item(0, 0).text())
            self.assertNotIn('队伍迟到', dialog.table.item(1, 3).text())
            self.assertEqual(dialog.table.item(1, 4).text(), '−27.0')
        finally:
            dialog.deleteLater()
            workspace._allow_close = True
            workspace.close()
            owner.deleteLater()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            APP.processEvents()


if __name__ == '__main__':
    unittest.main()
