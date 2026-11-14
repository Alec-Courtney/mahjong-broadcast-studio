"""Deleting event results refreshes live output without consuming operator drafts."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests')]
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from console import Console
from event_results import prepare_result, record_result
from event_store import EventStore
from model import Model
from overlays import OverlayManager
from test_postmatch_player import FakeAudio
import tournament

APP = QApplication.instance() or QApplication([])


class RecordDeletionBroadcastTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tmp-record-broadcast-', dir=ROOT/'tests')
        self.model = Model(self.temp.name)
        self.store = EventStore(self.model)
        self.model.settings['show_overlays'] = False
        seats = [player['id'] for player in self.model.state['players']]
        self.other = prepare_result(self.model, seats, [41000, 29000, 19000, 11000])
        record_result(self.store, self.other)
        self.audio_patch = patch('postmatch_audio.PostMatchAudio', FakeAudio)
        self.audio_patch.start()
        self.manager = OverlayManager(self.model)
        self.main = Console(self.model, self.manager)

    def tearDown(self):
        with patch('settings_window.SettingsWindow.ask_save_changes', return_value=QMessageBox.StandardButton.No):
            self.main.close()
        self.main.deleteLater()
        self.manager.close(persist=False)
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.audio_patch.stop()
        self.temp.cleanup()

    def finish(self):
        self.model.field('roundIndex', 7)
        self.model.field('dealer', 3)
        self.model.settle(dict(type='ron', winner=0, loser=1, han=3, fu=30), None)
        self.model.matches.confirm_finished()

    def test_historical_delete_refreshes_current_match_cumulative_settlement(self):
        self.finish()
        old = copy.deepcopy(self.manager.settlement_data)
        self.manager.set_bottom_mode('total_result')
        self.model.records.delete_match(self.other['id'])
        expected = tournament.final_settlement_summary(self.model.state, mode='individual')
        self.assertNotEqual(self.manager.settlement_data['all_standings'], old['all_standings'])
        self.assertEqual(self.manager.settlement_data, expected)
        self.assertTrue(self.manager.can_show_settlement())
        self.model.records.delete_match(self.model.state['id'])
        self.assertIsNone(self.manager.settlement_data)
        self.assertFalse(self.manager.can_show_settlement())
        self.assertEqual(self.manager.stage, 'table')

    def test_delete_preserves_unsubmitted_live_title_and_win_inputs(self):
        self.main.match_title.setText('尚未提交的新标题')
        self.main.han.setValue(7)
        self.model.records.delete_match(self.other['id'])
        self.assertEqual(self.main.match_title.text(), '尚未提交的新标题')
        self.assertEqual(self.main.han.value(), 7)

    def test_delete_stops_a_paused_postmatch_snapshot(self):
        self.finish()
        playback = self.manager.postmatch
        playback.start()
        for _ in range(100):
            APP.processEvents()
            if playback.state == 'playing':break
            QTest.qWait(10)
        self.assertEqual(playback.state, 'playing', playback.message)
        playback.pause_resume()
        self.assertTrue(playback.paused)
        self.model.records.delete_match(self.other['id'])
        self.assertFalse(playback.active)
        self.assertFalse(playback.preparing)
        self.assertFalse(self.manager.programme.paused)

    def test_delete_invalidates_a_paused_static_statistics_frame(self):
        self.manager.programme.select('statistics')
        self.manager.programme.pause_resume()
        self.assertIsNotNone(self.manager.programme.frozen_frame)
        self.assertTrue(self.manager.programme.paused)
        self.model.records.delete_match(self.other['id'])
        self.assertIsNone(self.manager.programme.frozen_frame)
        self.assertFalse(self.manager.programme.paused)
