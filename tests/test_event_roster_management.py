"""Roster actions protect records and persist independently of table undo."""
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

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from event_results import prepare_result, record_result
from event_roster import change_player, removal_reasons, selected_profile
from event_store import EventStore, resolve_active_directory
from event_workspace import EventWorkspaceDialog, ResultEntryDialog
from model import Model
import ranking_reports as reports
import tournament

APP = QApplication.instance() or QApplication([])


class EventRosterManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tmp-roster-management-', dir=ROOT/'tests')
        self.model = Model(self.tmp.name)
        self.store = EventStore(self.model)
        self.store.create('名册操作测试', 'team_league')
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        roster += [dict(id='empty', name='未参赛选手'),
                   dict(id='booked', name='已记分选手', team='青队', team_id='blue')]
        self.model.set_roster(roster)
        self.parent = QWidget()
        self.parent.model = self.model
        self.window = EventWorkspaceDialog(self.parent)

    def tearDown(self):
        self.window._allow_close = True
        self.window.close()
        self.window.deleteLater()
        self.parent.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.tmp.cleanup()

    def select(self, identity):
        roster = self.window.roster
        for index in range(roster.rowCount()):
            if roster.item(index, 0).data(Qt.ItemDataRole.UserRole) == identity:
                roster.selectRow(index)
                return
        self.fail(f'Missing row {identity}')

    def action(self, answer):
        with patch.object(QMessageBox, 'exec', return_value=answer) as dialog:
            self.window.player_action_button.click()
        dialog.assert_called_once()

    def restart(self):
        return Model(resolve_active_directory(self.store.root), asset_root=self.model.asset_root)

    def test_delete_requires_confirmation_and_table_undo_cannot_restore_the_profile(self):
        self.model.set_score(0, 27000)
        self.select('empty')
        self.action(QMessageBox.StandardButton.No)
        self.assertIsNotNone(selected_profile(self.model.state, 'empty'))
        self.action(QMessageBox.StandardButton.Yes)
        self.assertIsNone(selected_profile(self.restart().state, 'empty'))
        self.assertTrue(self.model.undo)
        self.model.history('undo')
        self.assertIsNone(selected_profile(self.model.state, 'empty'))

    def test_hide_keeps_results_and_team_points_and_can_be_restored(self):
        seats = ['booked', 'p2', 'p3', 'p4']
        record = prepare_result(self.model, seats, [40000, 30000, 20000, 10000])
        record_result(self.store, record)
        matches = reports.confirmed_matches(self.model.state)
        team_before = reports.team_rows(self.model.state, self.model.event)
        with self.assertRaisesRegex(ValueError, '只能屏蔽'):
            change_player(self.model, 'booked', 'delete')
        self.select('booked')
        self.action(QMessageBox.StandardButton.Yes)
        self.assertTrue(selected_profile(self.restart().state, 'booked')['hidden'])
        self.assertEqual(reports.confirmed_matches(self.model.state), matches)
        self.assertEqual(reports.team_rows(self.model.state, self.model.event), team_before)
        self.assertNotIn('booked', [r['id'] for r in tournament.standings(self.model.state)])
        self.assertNotIn('booked', [r['id'] for r in reports.individual_rows(self.model.state, self.model.event)])
        self.assertTrue(all(control.findData('booked') == -1 for control in self.window.table_seats))
        with self.assertRaisesRegex(ValueError, '已屏蔽'):
            self.model.matches.start_with_seats(seats)
        with self.assertRaisesRegex(ValueError, '已屏蔽'):
            prepare_result(self.model, seats, [40000, 30000, 20000, 10000])
        self.assertEqual(prepare_result(self.model, seats, [40000, 30000, 20000, 10000],
                                        match_id=record['id'])['id'], record['id'])
        self.select('booked')
        self.assertEqual(self.window.player_action_button.text(), '恢复选手')
        self.action(QMessageBox.StandardButton.Yes)
        self.assertFalse(selected_profile(self.restart().state, 'booked').get('hidden', False))
        self.assertTrue(all(control.findData('booked') >= 0 for control in self.window.table_seats))

    def test_live_seat_remains_usable_while_hidden_and_blocks_the_next_match(self):
        self.select('p1')
        self.action(QMessageBox.StandardButton.Yes)
        self.assertEqual(self.model.state['players'][0]['id'], 'p1')
        self.assertIsNone(tournament.table_standings(self.model.state)[0]['rank'])
        with self.assertRaisesRegex(ValueError, '已屏蔽'):
            self.model.matches.start_next()
        dialog = ResultEntryDialog(self.window)
        self.assertTrue(all(control.findData('p1') == -1 for control in dialog.seats))
        dialog.deleteLater()
        before = copy.deepcopy(self.model.state)
        self.model.settle(dict(type='ron', winner=1, loser=0, han=3, fu=30), None)
        self.assertIsNone(tournament.settlement_summary(before, self.model.state, 'individual')['standings'][0]['rank'])

    def test_plan_and_baseline_are_protected_and_a_failed_delete_preserves_state(self):
        self.store.update(player_plans={'empty': 2})
        self.assertIn('已有个人场次或晋级设置',
                      removal_reasons(self.model, selected_profile(self.model.state, 'empty')))
        self.store.update(player_plans={})
        before = copy.deepcopy((self.model.state, self.model.undo, self.model.redo))
        with patch.object(self.model.repository, 'save', side_effect=OSError('write failed')):
            with self.assertRaisesRegex(OSError, 'write failed'):
                change_player(self.model, 'empty', 'delete')
        self.assertEqual((self.model.state, self.model.undo, self.model.redo), before)
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        next(p for p in roster if p['id'] == 'empty')['result_baseline'] = {'max_score': 0}
        self.model.set_roster(roster)
        with self.assertRaisesRegex(ValueError, '只能屏蔽'):
            change_player(self.model, 'empty', 'delete')


if __name__ == '__main__':
    unittest.main()
