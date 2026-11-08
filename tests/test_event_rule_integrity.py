"""Event type, league cutoffs and standalone scoring use coherent rules."""
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
from PySide6.QtWidgets import QApplication, QLabel, QWidget, QMessageBox
from broadcast_console import BroadcastDialog
from event_store import EventStore
from event_workspace import EventWorkspaceDialog
from model import Model
from overlays import OverlayManager
from qualification_presentation import qualification_context, qualification_data

APP = QApplication.instance() or QApplication([])


class EventRuleIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tmp-event-rules-', dir=ROOT/'tests')
        self.model = Model(self.temp.name)
        self.store = EventStore(self.model)
        self.widgets = []
        self.managers = []

    def tearDown(self):
        for widget in reversed(self.widgets):
            widget._allow_close = True
            widget.close()
            widget.deleteLater()
        for manager in self.managers:manager.close()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def parent(self, model=None):
        model = model or self.model
        model.settings['show_overlays'] = False
        main = QWidget()
        main.model = model
        main.manager = OverlayManager(model)
        self.managers.append(main.manager)
        self.widgets.append(main)
        return main

    def broadcast(self, model=None):
        dialog = BroadcastDialog(self.parent(model))
        self.widgets.append(dialog)
        return dialog

    def workspace(self):
        dialog = EventWorkspaceDialog(self.parent())
        self.widgets.append(dialog)
        return dialog

    def team_event(self, count=10):
        self.store.create('十队联赛', 'team_league')
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        for index in range(4, count):
            roster.append(dict(id=f'bench-{index}', name=f'替补{index}', seed=index+1))
        for index, player in enumerate(roster):
            player.update(team=f'队伍{index}', team_id=f'team-{index}', base_points=index*100)
        self.model.set_roster(roster)
        return roster

    def test_event_kind_is_locked_even_without_results_and_rejection_is_atomic(self):
        before = copy.deepcopy((self.model.event, self.model.state))
        with self.assertRaisesRegex(ValueError, '创建后不可修改'):
            self.store.update(kind='team_league', name='不应保存')
        self.assertEqual((self.model.event, self.model.state), before)
        self.store.update(kind=self.model.event['kind'], name='允许重命名')
        self.assertEqual(self.model.event['name'], '允许重命名')
        dialog = self.workspace()
        self.assertFalse(dialog.event_kind.isEnabled())
        self.assertTrue(any('比赛类型（创建后固定）' in label.text() for label in dialog.findChildren(QLabel)))

    def test_team_cutoff_above_three_includes_teams_outside_current_table(self):
        self.team_event()
        self.store.update(qualification_count=6)
        # Reproduce an old team event that retained an individual trial setting.
        self.model.state['broadcast']['config']['qualification_mode'] = 'individual'
        data = qualification_data(self.model)
        self.assertIn('前6队', data['summary'])
        self.assertIn('目标前6队', data['display_note'])
        self.assertTrue(all(row['rank'] > 6 and row['status'] == 'outside' for row in data['rows']))
        self.assertEqual(len(data['rows']), 4)
        dialog = self.broadcast()
        self.assertEqual(dialog.qualification_slots.value(), 6)
        self.assertEqual(dialog.qualification_target.currentData(), 'team')
        self.assertFalse(dialog.qualification_target.isEnabled())
        self.assertEqual(dialog.qualification_slots_label.text(), '晋级队数')

    def test_saved_team_cutoff_updates_report_metadata_and_survives_score_undo(self):
        self.team_event()
        dialog = self.broadcast()
        dialog.qualification_slots.setValue(6)
        dialog.save_qualification()
        self.assertFalse(dialog._save_failed, dialog.status.text())
        self.assertEqual(self.model.event['qualification_count'], 6)
        self.assertEqual(self.model.state['broadcast']['config']['qualification_slots'], 6)
        self.model.field('honba', 2)
        self.model.history('undo')
        self.assertEqual(qualification_context(self.model)['slots'], 6)
        self.assertEqual(self.model.state['broadcast']['config']['qualification_slots'], 6)

    def test_event_settings_cutoff_refreshes_clean_condition_editor(self):
        self.team_event()
        dialog = self.broadcast()
        workspace = self.workspace()
        workspace.advance_count.setValue(7)
        workspace.save_general()
        self.assertFalse(workspace._save_failed, workspace.status.text())
        self.assertEqual(dialog.qualification_slots.value(), 7)
        self.assertEqual(self.model.state['broadcast']['config']['qualification_slots'], 7)
        self.assertIn('前7队', qualification_data(self.model)['summary'])

    def test_knockout_uses_node_count_and_does_not_offer_a_league_cutoff(self):
        import event_schedule
        self.store.create('淘汰赛', 'individual_knockout')
        bracket = event_schedule.generate(self.model.state['broadcast']['roster'], 1, 2)
        self.store.update(bracket=bracket)
        event_schedule.start_node(self.store, bracket['nodes'][0]['id'])
        dialog = self.broadcast()
        self.assertFalse(dialog.qualification_slots.isEnabled())
        self.assertFalse(dialog.qualification_target.isEnabled())
        self.assertEqual(dialog.qualification_slots.value(), 1)  # A single final decides its champion.
        self.assertFalse(self.workspace().advance_count.isEnabled())

    def test_ui_only_can_edit_scoring_without_an_event_library(self):
        standalone = Model(Path(self.temp.name)/'standalone')
        dialog = self.broadcast(standalone)
        self.assertFalse(dialog.return_points.isHidden())
        dialog.return_points.setValue(35000)
        dialog.start_points.setValue(30000)
        self.assertIn('qualification', dialog.dirty_sections())
        dialog.save_qualification()
        self.assertFalse(dialog._save_failed, dialog.status.text())
        config = standalone.state['broadcast']['config']
        self.assertEqual((config['start_points'], config['return_points']), (30000, 35000))
        standalone.start_match()
        self.assertEqual([player['score'] for player in standalone.state['players']], [30000]*4)
        self.assertIsNone(standalone.event_store)

    def select_other_event(self):
        original = self.model.event['id']
        target = self.store.create('另一个赛事', 'individual_league')
        self.store.open(original)
        dialog = self.workspace()
        index = next(row for row in range(dialog.library.rowCount())
                     if dialog.library.item(row, 0).data(Qt.ItemDataRole.UserRole) == target)
        dialog.library.selectRow(index)
        return dialog, original, target

    def test_cancel_event_open_retains_current_event_and_draft(self):
        dialog, original, target = self.select_other_event()
        dialog.event_name.setText('尚未保存的名称')
        with patch.object(dialog, 'ask_event_change', return_value=QMessageBox.StandardButton.Cancel):
            dialog.open_selected()
        self.assertEqual(self.model.event['id'], original)
        self.assertEqual(dialog.event_name.text(), '尚未保存的名称')
        self.assertIn('general', dialog.dirty_sections())

    def test_failed_save_blocks_switch_without_publishing_partial_configuration(self):
        dialog, original, target = self.select_other_event()
        dialog.event_name.setText('保存失败的名称')
        dialog.return_points.setValue(35000)
        before = copy.deepcopy((self.model.state, self.model.event, self.model.settings, self.model.undo, self.model.redo))
        saved = (self.model.path/'session.json').read_bytes()
        with patch.object(dialog, 'ask_event_change', return_value=QMessageBox.StandardButton.Yes), \
             patch.object(self.model.repository, 'save', side_effect=OSError('保存失败')):
            dialog.open_selected()
        self.assertEqual((self.model.state, self.model.event, self.model.settings, self.model.undo, self.model.redo), before)
        self.assertEqual((self.model.path/'session.json').read_bytes(), saved)
        self.assertEqual(dialog.event_name.text(), '保存失败的名称')
        self.assertEqual(self.model.event['id'], original)
        self.assertTrue(dialog._save_failed)

    def test_discard_or_save_explicitly_allows_event_switch(self):
        for answer in (QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes):
            with self.subTest(answer=answer):
                dialog, original, target = self.select_other_event()
                old_name = self.model.event['name']
                dialog.event_name.setText('操作员修改的名称')
                with patch.object(dialog, 'ask_event_change', return_value=answer):dialog.open_selected()
                self.assertEqual(self.model.event['id'], target)
                self.store.open(original)
                self.assertEqual(self.model.event['name'], '操作员修改的名称' if answer == QMessageBox.StandardButton.Yes else old_name)

    def test_cancel_new_and_import_do_not_discard_drafts(self):
        dialog = self.workspace()
        dialog.event_name.setText('待保存')
        dialog.new_name.setText('不应创建')
        exported = Path(self.temp.name)/'import.json'
        self.model.export_data(exported)
        with patch.object(dialog, 'ask_event_change', return_value=QMessageBox.StandardButton.Cancel), \
             patch.object(self.store, 'create') as create, patch.object(self.store, 'import_new') as import_new, \
             patch('event_workspace.QFileDialog.getOpenFileName', return_value=(str(exported), 'JSON')):
            dialog.create_event()
            dialog.import_event()
            create.assert_not_called()
            import_new.assert_not_called()
        self.assertEqual(dialog.event_name.text(), '待保存')
        self.assertIn('general', dialog.dirty_sections())

    def test_failed_qualification_save_does_not_publish_new_cutoff(self):
        self.team_event()
        dialog = self.broadcast()
        dialog.qualification_slots.setValue(6)
        before = copy.deepcopy((self.model.state, self.model.event, self.model.undo, self.model.redo))
        with patch.object(self.model.repository, 'save', side_effect=OSError('保存失败')):
            dialog.save_qualification()
        self.assertTrue(dialog._save_failed)
        self.assertEqual((self.model.state, self.model.event, self.model.undo, self.model.redo), before)
        self.assertEqual(dialog.qualification_slots.value(), 6)
        self.assertIn('qualification', dialog.dirty_sections())


if __name__ == '__main__':unittest.main()
