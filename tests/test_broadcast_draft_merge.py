"""Broadcast editor drafts merge with changes made in other open windows."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget
from broadcast_console import BroadcastDialog
from event_store import EventStore
from model import Model
from overlays import OverlayManager

APP = QApplication.instance() or QApplication([])


class BroadcastDraftMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'tests')
        self.model = Model(self.temp.name)
        self.store = EventStore(self.model)
        self.model.settings['show_overlays'] = False
        self.manager = OverlayManager(self.model)
        self.main = QWidget()
        self.main.model = self.model
        self.main.manager = self.manager
        self.dialog = BroadcastDialog(self.main)

    def tearDown(self):
        self.dialog._allow_close = True
        self.dialog.close(); self.dialog.deleteLater(); self.main.deleteLater()
        self.manager.close(persist=False)
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete); APP.processEvents()
        self.temp.cleanup()

    def config(self):return self.model.state['broadcast']['config']

    def assert_saved(self):self.assertFalse(self.dialog._save_failed, self.dialog.status.text())

    def test_presentation_statistics_edit_preserves_other_windows_title_and_duration(self):
        self.dialog.statistics_page.setValue(3)
        self.model.configure_broadcast(dict(title='另一窗口的新标题', settlement_hold=6.5))
        self.dialog.save_presentation()
        self.assert_saved()
        self.assertEqual(self.config()['statistics_page'], 2)
        self.assertEqual(self.config()['title'], '另一窗口的新标题')
        self.assertEqual(self.config()['settlement_hold'], 6.5)
        self.assertEqual(self.dialog.title.text(), '另一窗口的新标题')
        self.assertEqual(self.dialog.settlement_hold.value(), 6.5)

    def test_presentation_conflict_keeps_the_local_draft_and_does_not_write(self):
        self.dialog.title.setText('本地标题')
        self.model.configure_broadcast(dict(title='另一窗口标题'))
        before = copy.deepcopy((self.model.state, self.model.undo, self.model.redo))
        saved = (self.model.path/'session.json').read_bytes()
        self.dialog.save_presentation()
        self.assertTrue(self.dialog._save_failed)
        self.assertIn('其他窗口', self.dialog.status.text())
        self.assertEqual(self.dialog.title.text(), '本地标题')
        self.assertEqual((self.model.state, self.model.undo, self.model.redo), before)
        self.assertEqual((self.model.path/'session.json').read_bytes(), saved)

    def test_postmatch_edit_preserves_other_windows_unedited_fields(self):
        self.dialog.postmatch_chart_hold.setValue(19)
        self.model.configure_postmatch(dict(postmatch_stats_hold=27., postmatch_sfx_volume=.42))
        self.dialog.save_postmatch()
        self.assert_saved()
        self.assertEqual(self.config()['postmatch_chart_hold'], 19)
        self.assertEqual(self.config()['postmatch_stats_hold'], 27)
        self.assertEqual(self.config()['postmatch_sfx_volume'], .42)
        self.assertEqual(self.dialog.postmatch_stats_hold.value(), 27)
        self.assertEqual(self.dialog.postmatch_sfx_volume.value(), 42)

    def test_postmatch_conflict_does_not_overwrite_newer_hold(self):
        self.dialog.postmatch_chart_hold.setValue(19)
        self.model.configure_postmatch(dict(postmatch_chart_hold=24.))
        saved = (self.model.path/'session.json').read_bytes()
        self.dialog.save_postmatch()
        self.assertTrue(self.dialog._save_failed)
        self.assertEqual(self.dialog.postmatch_chart_hold.value(), 19)
        self.assertEqual(self.config()['postmatch_chart_hold'], 24)
        self.assertEqual((self.model.path/'session.json').read_bytes(), saved)

    def test_qualification_title_edit_does_not_restore_an_old_cutoff(self):
        self.dialog.qualification_title.setText('下一阶段条件')
        old_cutoff = self.dialog.qualification_slots.value()
        self.store.update(qualification_count=6)
        self.assertEqual(self.dialog.qualification_slots.value(), old_cutoff)
        self.dialog.save_qualification()
        self.assert_saved()
        self.assertEqual(self.config()['qualification_title'], '下一阶段条件')
        self.assertEqual(self.config()['qualification_slots'], 6)
        self.assertEqual(self.model.event['qualification_count'], 6)
        self.assertEqual(self.dialog.qualification_slots.value(), 6)

    def test_qualification_conflict_does_not_change_other_fields(self):
        self.dialog.qualification_title.setText('本地条件标题')
        self.dialog.qualification_slots.setValue(3)
        self.model.configure_broadcast(dict(qualification_title='另一窗口条件标题'))
        before = copy.deepcopy((self.model.state, self.model.event))
        self.dialog.save_qualification()
        self.assertTrue(self.dialog._save_failed)
        self.assertEqual((self.model.state, self.model.event), before)
        self.assertEqual(self.dialog.qualification_title.text(), '本地条件标题')

    def test_per_player_title_deletion_preserves_another_players_new_title(self):
        first, second = [player['id'] for player in self.model.state['players'][:2]]
        self.model.configure_broadcast(dict(qualification_titles={first:'旧标题', second:'原始标题'}))
        self.dialog.reload()
        self.dialog.manual_conditions.item(0, 2).setText('')
        self.model.configure_broadcast(dict(qualification_titles={first:'旧标题', second:'另一窗口新标题', 'bench':'未出场标题'}))
        self.dialog.save_qualification()
        self.assert_saved()
        self.assertNotIn(first, self.config()['qualification_titles'])
        self.assertEqual(self.config()['qualification_titles'][second], '另一窗口新标题')
        self.assertEqual(self.config()['qualification_titles']['bench'], '未出场标题')

    def test_saving_one_section_keeps_another_sections_pending_edit(self):
        self.dialog.title.setText('未保存的显示标题')
        self.dialog.postmatch_chart_hold.setValue(18)
        self.dialog.save_postmatch()
        self.assert_saved()
        self.assertEqual(self.dialog.title.text(), '未保存的显示标题')
        self.assertNotEqual(self.config()['title'], '未保存的显示标题')
        self.assertIn('presentation', self.dialog.dirty_sections())


if __name__ == '__main__':unittest.main()
