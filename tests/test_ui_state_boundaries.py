"""Shared editor drafts and shutdown must respect saved state boundaries."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QLabel
from console import Console, PlayerLayoutDialog
from event_store import EventStore
from model import Model
from overlays import OverlayManager
from settings_context import settings_windows
from settings_window import SettingsWindow
from settings_dialog import SettingsDialog

APP = QApplication.instance() or QApplication([])


class UIStateBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tmp-ui-boundary-', dir=ROOT/'tests')
        self.model = Model(self.temp.name)
        self.store = EventStore(self.model)
        self.model.settings['show_overlays'] = False
        self.manager = OverlayManager(self.model)
        self.main = Console(self.model, self.manager)
        self.close_question = patch.object(SettingsWindow, 'ask_save_changes', return_value=QMessageBox.StandardButton.No)
        self.close_question.start()
        self.main.open_event_workspace(1)
        self.workspace = self.main.event_workspace

    def tearDown(self):
        for window in settings_windows(self.model):window._allow_close = True
        self.main.close(); self.main.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete); APP.processEvents()
        self.close_question.stop()
        self.temp.cleanup()

    def other_event(self):
        original = self.model.event['id']
        target = self.store.create('另一赛事', 'individual_league')
        self.store.open(original)
        for index in range(self.workspace.library.rowCount()):
            if self.workspace.library.item(index, 0).data(Qt.ItemDataRole.UserRole) == target:
                self.workspace.library.selectRow(index)
        return original, target

    def test_general_save_preserves_another_windows_cutoff_change(self):
        self.workspace.event_name.setText('本地新名称')
        self.store.update(qualification_count=6)
        self.workspace.save_general()
        self.assertFalse(self.workspace._save_failed, self.workspace.status.text())
        self.assertEqual(self.model.event['name'], '本地新名称')
        self.assertEqual(self.model.event['qualification_count'], 6)
        self.assertEqual(self.workspace.advance_count.value(), 6)

    def test_conflicting_general_field_is_rejected_without_losing_draft(self):
        self.workspace.advance_count.setValue(3)
        self.store.update(qualification_count=6)
        before = copy.deepcopy((self.model.state, self.model.event))
        self.workspace.save_general()
        self.assertTrue(self.workspace._save_failed)
        self.assertEqual((self.model.state, self.model.event), before)
        self.assertEqual(self.workspace.advance_count.value(), 3)
        self.assertIn('其他窗口', self.workspace.status.text())

    def test_roster_same_field_conflict_is_rejected(self):
        self.workspace.roster.item(0, 0).setText('本地姓名')
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        roster[0]['name'] = '远端姓名'
        self.model.set_roster(roster)
        self.workspace.save_roster()
        self.assertTrue(self.workspace._save_failed)
        self.assertEqual(self.model.state['broadcast']['roster'][0]['name'], '远端姓名')
        self.assertEqual(self.workspace.roster.item(0, 0).text(), '本地姓名')

    def test_roster_different_field_edit_keeps_external_team(self):
        self.workspace.roster.item(0, 0).setText('本地姓名')
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        roster[0].update(team='新队伍', team_id='new-team')
        self.model.set_roster(roster)
        self.workspace.save_roster()
        self.assertFalse(self.workspace._save_failed, self.workspace.status.text())
        person = self.model.state['broadcast']['roster'][0]
        self.assertEqual((person['name'], person['team_id']), ('本地姓名', 'new-team'))

    def test_blank_historical_maximum_can_be_saved_repeatedly(self):
        for name in ('第一次保存', '第二次保存'):
            self.workspace.roster.item(0, 0).setText(name)
            self.workspace.save_roster()
            self.assertFalse(self.workspace._save_failed, self.workspace.status.text())
            self.assertEqual(self.workspace.roster.item(0, 11).text(), '')
        self.assertEqual(self.model.state['broadcast']['roster'][0]['name'], '第二次保存')

    def test_cancel_event_switch_protects_other_windows_drafts(self):
        original, target = self.other_event()
        panel = self.main.broadcast_panel()
        panel.title.setText('播出窗口未保存的标题')
        with patch.object(self.workspace, 'ask_event_change', return_value=QMessageBox.StandardButton.Cancel) as ask:
            self.workspace.open_selected()
        ask.assert_called_once()
        self.assertEqual(self.model.event['id'], original)
        self.assertEqual(panel.title.text(), '播出窗口未保存的标题')

    def test_save_before_switch_saves_other_windows_to_original_event(self):
        original, target = self.other_event()
        panel = self.main.broadcast_panel()
        panel.title.setText('原赛事保存的播出标题')
        with patch.object(self.workspace, 'ask_event_change', return_value=QMessageBox.StandardButton.Yes):
            self.workspace.open_selected()
        self.assertEqual(self.model.event['id'], target)
        self.store.open(original)
        self.assertEqual(self.model.state['broadcast']['config']['title'], '原赛事保存的播出标题')

    def test_failed_switch_after_discard_choice_keeps_other_window_draft(self):
        original, target = self.other_event()
        panel = self.main.broadcast_panel()
        panel.title.setText('不能丢弃的旧草稿')
        with patch.object(self.workspace, 'ask_event_change', return_value=QMessageBox.StandardButton.No), \
             patch.object(self.store, 'open', side_effect=OSError('打不开目标赛事')):
            self.workspace.open_selected()
        self.assertEqual(self.model.event['id'], original)
        self.assertEqual(panel.title.text(), '不能丢弃的旧草稿')
        self.assertIn('presentation', panel.dirty_sections())

    def test_context_change_keeps_read_only_report_preview_open(self):
        original, target = self.other_event()
        self.workspace.preview_report()
        preview = self.workspace.report_preview
        self.workspace.open_selected()
        self.assertEqual(self.model.event['id'], target)
        self.assertTrue(preview.isVisible())
        self.assertEqual(preview.data['event']['id'], target)

    def test_shutdown_save_failure_keeps_live_resources_usable(self):
        self.main.show(); APP.processEvents()
        with patch.object(self.model.repository, 'save', side_effect=OSError('磁盘不可写')):
            self.assertFalse(self.main.close())
        self.assertTrue(self.main.isVisible())
        self.assertFalse(self.manager.closing)
        self.assertTrue(self.main.workspace._ready)
        self.assertFalse(self.main.shortcuts._disposed)
        self.assertIn('关闭前保存失败', self.main.status.text())

    def test_failed_layout_save_does_not_publish_unsaved_layout(self):
        before = copy.deepcopy(self.model.settings)
        with patch.object(self.model.repository, 'save', side_effect=OSError('磁盘不可写')):
            self.assertFalse(self.main.workspace.save())
        self.assertEqual(self.model.settings, before)

    def test_failed_score_and_round_edits_restore_the_controls(self):
        self.main.wait_drafts[0] = dict(waits=['3m'], remaining=2, no_yaku=[], furiten=False)
        self.main.wait_dirty.add(0)
        with patch.object(self.model.repository, 'save', side_effect=OSError('磁盘不可写')):
            self.main.player_widgets[0][2].setValue(36000)
            self.main.honba.setValue(4)
        self.assertEqual(self.main.player_widgets[0][2].value(), self.model.state['players'][0]['score'])
        self.assertEqual(self.main.honba.value(), self.model.state['honba'])
        self.assertEqual(self.main.wait_drafts[0]['waits'], ['3m'])
        self.assertIn(0, self.main.wait_dirty)

    def test_delayed_status_clear_is_cancelled_when_its_widget_is_deleted(self):
        label = QLabel()
        errors = []
        single_shot = QTimer.singleShot
        with patch('console.QTimer.singleShot', side_effect=lambda delay, *args: single_shot(1, *args)), \
             patch.object(sys, 'excepthook', side_effect=lambda *args: errors.append(args)):
            Console.error(SimpleNamespace(status=label), '临时提示')
            label.deleteLater()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            QTest.qWait(20)
        self.assertEqual(errors, [])

    def test_reload_menu_recovers_a_stale_workspace_and_allows_new_edits(self):
        other = Model(self.model.path)
        other.set_score(0, 42000)
        with patch('PySide6.QtWidgets.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            self.main.reload_workspace()
        self.assertEqual(self.model.state['players'][0]['score'], 42000)
        self.assertEqual(self.model.undo, [])
        self.model.field('honba', 2)
        self.assertEqual(Model(self.model.path).state['honba'], 2)

    def test_compatibility_preview_resumes_after_its_handoff_worker_is_deleted(self):
        class DeletedWorker:
            def isRunning(self):raise RuntimeError('Internal C++ object already deleted')
        resumed = []
        owner = SimpleNamespace(main=SimpleNamespace(inline_automation=SimpleNamespace(resume_after_preview=resumed.append)),
            _recognition_paused={0, 2}, recognition_preview_session=SimpleNamespace(worker=None),
            _recognition_handoff=DeletedWorker())
        SettingsDialog._resume_recognition_sources(owner)
        self.assertCountEqual(resumed, [0, 2])
        self.assertEqual(owner._recognition_paused, set())

    def test_player_layout_and_padding_save_together_or_remain_unchanged(self):
        dialog = PlayerLayoutDialog(self.main)
        before = copy.deepcopy(self.model.settings)
        geometry = self.manager.player_group_geometry()
        dialog.x.setValue(dialog.x.value()+30)
        dialog.side_padding.setValue(10)
        with patch.object(self.model.repository, 'save', side_effect=OSError('磁盘不可写')):
            self.assertFalse(dialog.apply())
        self.assertEqual(self.model.settings, before)
        self.assertEqual(self.manager.player_group_geometry(), geometry)
        self.assertIn('layout', dialog.dirty_sections())
        self.assertTrue(dialog.apply())
        self.assertEqual(self.model.settings['player_side_padding'], 10)
        self.assertEqual(self.manager.player_group_geometry()[0], geometry[0]+30)
        dialog._allow_close = True; dialog.close(); dialog.deleteLater()

    def test_win_caption_failure_does_not_publish_unsaved_details(self):
        before = copy.deepcopy((self.model.last_info, self.model.settings, self.model.undo))
        with patch.object(self.model.repository, 'save', side_effect=OSError('磁盘不可写')):
            self.main.update_info()
        self.assertEqual((self.model.last_info, self.model.settings, self.model.undo), before)
        self.assertIn('磁盘不可写', self.main.status.text())

    def test_live_display_controls_revert_on_save_failure(self):
        before = copy.deepcopy(self.model.settings)
        with patch.object(self.model.repository, 'save', side_effect=OSError('磁盘不可写')):
            self.main.show_melds.click()
            self.main.speed.setCurrentIndex(0)
            self.main.final_scores_hold.setValue(9)
        self.assertEqual(self.model.settings, before)
        self.assertEqual(self.main.show_melds.isChecked(), before['show_melds'])
        self.assertEqual(self.main.speed.currentData(), before['speed'])
        self.assertEqual(self.main.final_scores_hold.value(), before['final_scores_hold'])


if __name__ == '__main__':
    unittest.main()
