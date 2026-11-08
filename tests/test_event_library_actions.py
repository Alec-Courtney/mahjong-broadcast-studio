"""Check destructive confirmation and read-only historical results."""
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

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QWidget
from event_history import EventHistoryDialog
from event_results import prepare_result, record_result
from event_store import EventStore, resolve_active_directory
from event_workspace import DeleteEventDialog, EventWorkspaceDialog
from model import Model
from overlays import OverlayManager

APP = QApplication.instance() or QApplication([])


class EventLibraryActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'tests')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.flush_widgets)
        self.root = Path(self.temp.name)
        self.model = Model(self.root)
        self.store = EventStore(self.model)

    @staticmethod
    def flush_widgets():
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()

    def workspace(self):
        parent = QWidget()
        parent.model = self.model
        parent.manager = OverlayManager(self.model)
        window = EventWorkspaceDialog(parent)
        self.addCleanup(parent.deleteLater)
        self.addCleanup(parent.manager.close, persist=False)
        self.addCleanup(window.deleteLater)
        return window

    def test_delete_exact_event_and_owned_files_with_other_event_untouched(self):
        original = self.model.event['id']
        self.store.update(name='同名赛事')
        target = self.store.create('同名赛事', 'team_league')
        owned_file = self.model.path/'exports'/'scores.csv'
        owned_file.parent.mkdir()
        owned_file.write_text('saved scores', encoding='utf-8')
        outside = self.root/'exported-scores.csv'
        outside.write_text('external backup', encoding='utf-8')
        self.store.open(original)
        index = self.store.index_path.read_bytes()
        current_file = self.model.path/'session.json'
        current_bytes = current_file.read_bytes()

        with self.assertRaisesRegex(ValueError, '名称不一致'):
            self.store.delete(target, '同名赛事 ')
        self.assertEqual(self.store.index_path.read_bytes(), index)
        self.assertTrue(owned_file.is_file())

        self.store.delete(target, '同名赛事')
        self.assertFalse(self.store.directory(target).exists())
        self.assertEqual(current_file.read_bytes(), current_bytes)
        self.assertEqual(outside.read_text(encoding='utf-8'), 'external backup')
        self.assertEqual([event['id'] for event in self.store.list_events()], [original])

    def test_delete_current_switches_and_stale_writer_cannot_recreate(self):
        original = self.model.event['id']
        target = self.store.create('活动赛事', 'individual_league')
        stale = Model(self.root)
        EventStore(stale)
        target_dir = stale.path
        self.store.delete(target, '活动赛事')
        self.assertEqual(self.model.event['id'], original)
        self.assertEqual(resolve_active_directory(self.root), self.model.path)
        with self.assertRaisesRegex(ValueError, '赛事文件已删除'):
            stale.field('honba', 1)
        self.assertFalse(target_dir.exists())
        stale.event_store.open(original)
        self.assertEqual(stale.event['id'], original)

    def test_index_error_restores_target_directory(self):
        original = self.model.event['id']
        target = self.store.create('待删赛事', 'individual_league')
        path = self.model.path/'session.json'
        previous = path.read_bytes()
        self.store.open(original)
        with patch('event_store.atomic_json', side_effect=OSError('index unavailable')):
            with self.assertRaisesRegex(OSError, 'index unavailable'):
                self.store.delete(target, '待删赛事')
        self.assertEqual(path.read_bytes(), previous)
        self.assertIn(target, self.store.index['events'])
        self.assertFalse(path.parent.with_name(f'.deleting-{target}').exists())

    def test_confirmation_requires_typed_exact_name(self):
        dialog = DeleteEventDialog(None, 'Final A', False)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.confirm.isEnabled())
        self.assertFalse(dialog.confirmation.acceptDrops())
        clipboard = APP.clipboard()
        original = clipboard.text()
        try:
            clipboard.setText('Final A')
            QTest.keyClick(dialog.confirmation, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
            self.assertFalse(dialog.confirm.isEnabled())
        finally:
            clipboard.setText(original)
        QTest.keyClicks(dialog.confirmation, 'Final a')
        dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        QTest.keyClick(dialog.confirmation, Qt.Key.Key_Backspace)
        QTest.keyClicks(dialog.confirmation, 'A')
        self.assertTrue(dialog.confirm.isEnabled())
        dialog.confirm.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_library_button_deletes_the_selected_current_event(self):
        original = self.model.event['id']
        target = self.store.create('Final A', 'individual_league')
        window = self.workspace()

        def confirm():
            dialog = APP.activeModalWidget()
            QTest.keyClicks(dialog.confirmation, 'Final A')
            dialog.confirm.click()

        QTimer.singleShot(0, confirm)
        window.library_delete.click()
        self.assertFalse(self.store.directory(target).exists())
        self.assertEqual(self.model.event['id'], original)
        self.assertFalse(window.library_delete.isEnabled())
        self.assertIn('最后一场赛事', window.library_delete.toolTip())

    def test_historical_results_show_saved_hands_and_manual_scores_without_switching(self):
        historical = self.store.create('已打完的赛事', 'individual_league')
        self.model.field('roundIndex', 7)
        self.model.field('dealer', 3)
        self.model.settle(dict(type='ron', winner=0, loser=1, han=3, fu=30), None)
        self.model.matches.confirm_finished()
        live_record = self.model.state['broadcast']['matches'][0]
        seats = [player['id'] for player in self.model.state['players']]
        manual = prepare_result(self.model, seats, [40100, 30900, 19000, 10000], title='其他桌')
        record_result(self.store, manual)
        historical_bytes = (self.model.path/'session.json').read_bytes()
        current = self.store.create('直播中的赛事', 'team_league')
        before = copy.deepcopy((self.model.state, self.model.event, self.model.settings, self.model.undo))

        parent = QWidget()
        parent.model = self.model
        dialog = EventHistoryDialog(parent, *self.store.read_history(historical))
        self.addCleanup(parent.deleteLater)
        self.assertEqual([match['id'] for match in dialog.matches], [manual['id'], live_record['id']])
        self.assertEqual(dialog.final_table.item(0, 3).text(), '40,100')
        self.assertEqual(dialog.hand_table.rowCount(), 0)
        self.assertIn('补录', dialog.hand_note.text())
        dialog.match_table.selectRow(1)
        self.assertEqual(dialog.hand_table.rowCount(), 1)
        self.assertIn('荣和', dialog.hand_table.item(0, 1).text())
        self.assertIn('+', dialog.hand_table.item(0, 2).text())
        self.assertEqual(dialog.ranking_table.rowCount(), 4)
        self.assertEqual((self.model.state, self.model.event, self.model.settings, self.model.undo), before)
        self.assertEqual(self.model.event['id'], current)
        self.assertEqual((self.store.directory(historical)/'session.json').read_bytes(), historical_bytes)

    def test_workspace_history_button_uses_selected_event(self):
        previous = self.model.event['id']
        active = self.store.create('当前赛事', 'individual_league')
        window = self.workspace()
        for index in range(window.library.rowCount()):
            if window.library.item(index, 0).data(Qt.ItemDataRole.UserRole) == previous:
                window.library.selectRow(index)
                break
        with patch('event_workspace.EventHistoryDialog.exec', return_value=QDialog.DialogCode.Rejected) as execute:
            with patch.object(self.store, 'read_history', wraps=self.store.read_history) as read:
                window.library_history.click()
                read.assert_called_once_with(previous)
                execute.assert_called_once()
        self.assertEqual(self.model.event['id'], active)


if __name__ == '__main__':
    unittest.main()
