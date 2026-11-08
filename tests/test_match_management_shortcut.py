"""Live-desk match entry records the event once and carries its next lineup."""
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
from PySide6.QtWidgets import QApplication, QMessageBox
from console import Console
from event_store import EventStore
from model import Model
from overlays import OverlayManager
import tournament

APP = QApplication.instance() or QApplication([])


class MatchManagementShortcutTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.cache').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='match-shortcut-', dir=ROOT/'.cache')
        self.model = Model(self.temp.name)
        self.store = EventStore(self.model)
        self.model.settings.update(show_overlays=False, topmost=False)
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        for index in range(4):
            roster.append(dict(roster[index], id=f'next-{index}', name=f'下一组选手{index+1}'))
        self.model.configure_broadcast({'roster': roster})
        self.manager = OverlayManager(self.model)
        self.main = Console(self.model, self.manager)
        self.main.resize(1200, 800)
        self.main.show()
        self.main.navigation_buttons['matches'].click()
        APP.processEvents()
        self.dialog = self.main.event_workspace

    def tearDown(self):
        with patch('settings_window.SettingsWindow.ask_save_changes', return_value=QMessageBox.StandardButton.No):
            self.main.close()
        self.main.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def finish(self):
        self.model.field('roundIndex', 7)
        self.model.field('dealer', 3)
        self.model.settle(dict(type='ron', winner=0, loser=1, han=3, fu=30), None)
        self.assertEqual(self.model.state['phase'], 'finished')

    def choose_next(self):
        identities = [f'next-{index}' for index in range(4)]
        for control, identity in zip(self.dialog.table_seats, identities):
            control.setCurrentIndex(control.findData(identity))
        return identities

    def test_direct_entry_opens_record_and_next_lineup_on_the_same_page(self):
        self.assertTrue(self.main.navigation_buttons['matches'].isVisible())
        self.assertTrue(self.dialog.isVisible())
        self.assertEqual(self.dialog.nav.currentRow(), 2)
        self.assertTrue(self.dialog.commit_match_button.isVisible())
        self.assertFalse(self.dialog.commit_match_button.isEnabled())
        self.assertEqual(self.dialog.current_match_scores.rowCount(), 4)
        self.assertEqual(self.dialog.current_match_scores.verticalScrollBar().maximum(), 0)
        self.assertIn('进行中', self.dialog.match_record_status.text())
        self.main.navigation_buttons['matches'].click()
        self.assertIs(self.main.event_workspace, self.dialog)

    def test_record_once_then_start_selected_players_keeps_event_scores_after_restart(self):
        self.finish()
        identities = self.choose_next()
        expected_results = tournament.match_results(self.model.state)
        self.assertTrue(self.dialog.commit_match_button.isEnabled())
        self.dialog.commit_match_button.click()
        matches = copy.deepcopy(self.model.state['broadcast']['matches'])
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0]['committed'])
        self.assertEqual(matches[0]['results'], expected_results)
        self.assertFalse(self.dialog.commit_match_button.isEnabled())
        self.assertIn('已计入', self.dialog.match_record_status.text())
        self.assertEqual([control.currentData() for control in self.dialog.table_seats], identities)
        self.dialog.commit_match_button.click()
        self.dialog.confirm_current_match()
        self.assertEqual(self.model.state['broadcast']['matches'], matches)
        standings = tournament.standings(self.model.state, 'individual')
        previous_id = self.model.state['id']
        self.dialog.start_seats_button.click()
        self.assertNotEqual(self.model.state['id'], previous_id)
        self.assertEqual(self.model.state['phase'], 'playing')
        self.assertEqual([player['id'] for player in self.model.state['players']], identities)
        self.assertEqual(self.model.state['broadcast']['matches'], matches)
        stable = lambda rows: [{key:value for key,value in entry.items() if key not in ('previous_rank','movement')} for entry in rows]
        self.assertEqual(stable(tournament.standings(self.model.state, 'individual')), stable(standings))
        reopened = Model(self.model.path)
        self.assertEqual(reopened.state['broadcast']['matches'], matches)
        self.assertEqual([player['id'] for player in reopened.state['players']], identities)

    def test_unrecorded_finished_match_cannot_be_replaced_by_next_lineup(self):
        self.finish()
        self.choose_next()
        before = copy.deepcopy(self.model.state)
        self.dialog.start_seats_button.click()
        self.assertEqual(self.model.state, before)
        self.assertIn('尚未结束并确认', self.dialog.status.text())
        self.assertTrue(self.dialog.commit_match_button.isEnabled())

    def test_failed_record_keeps_uncommitted_state_and_next_lineup_draft(self):
        self.finish()
        identities = self.choose_next()
        before = copy.deepcopy(self.model.state)
        with patch.object(self.model, '_save_snapshot', side_effect=OSError('写入失败')):
            self.dialog.commit_match_button.click()
        self.assertEqual(self.model.state, before)
        self.assertIn('写入失败', self.dialog.status.text())
        self.assertTrue(self.dialog.commit_match_button.isEnabled())
        self.assertEqual([control.currentData() for control in self.dialog.table_seats], identities)

    def test_knockout_match_has_direct_schedule_navigation_and_event_switch_refreshes_state(self):
        self.store.create('淘汰赛预览', 'individual_knockout', False)
        self.assertFalse(self.dialog.start_seats_button.isEnabled())
        self.assertTrue(self.dialog.match_schedule_button.isVisible())
        self.assertFalse(self.dialog.commit_match_button.isEnabled())
        self.dialog.match_schedule_button.click()
        self.assertEqual(self.dialog.nav.currentRow(), 4)
        self.store.create('下一场积分赛', 'individual_league', False)
        self.dialog.nav.setCurrentRow(2)
        self.assertFalse(self.dialog.match_schedule_button.isVisible())
        self.assertTrue(self.dialog.start_seats_button.isEnabled())
        self.assertIn('已确认 0 半庄', self.dialog.match_record_status.text())


if __name__ == '__main__':
    unittest.main()
