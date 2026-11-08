"""Native layouts and display choices publish only successfully saved values."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from PySide6.QtCore import QEvent, QPoint, QRectF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from model import Model
from overlays import OverlayManager
from sidebar_presets import default_sidebar_presets

APP = QApplication.instance() or QApplication([])


class DisplayConfigurationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tmp-display-commit-', dir=ROOT/'tests')
        self.model = Model(self.temp.name)
        self.model.settings['show_overlays'] = False
        self.manager = OverlayManager(self.model)
        self.manager.save_geometry()

    def tearDown(self):
        self.manager.close(persist=False)
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete); APP.processEvents()
        self.temp.cleanup()

    def snapshot(self):
        manager = self.manager
        windows = {key:window.geometry().getRect() for key, window in manager.windows.items()}
        windows.update({key:window.geometry().getRect() for key, window in manager.layout_windows.items()})
        return copy.deepcopy((self.model.state, self.model.settings, self.model.undo, self.model.redo,
            windows, manager.enabled, manager.extension_transitions, manager.stage,
            manager.pending_win, manager.pending_next, manager.commentary_deadline,
            manager.chart_started, manager.sidebar_timer.isActive(), manager._sidebar_page,
            manager._sidebar_deadline, manager._sidebar_transition, manager._commentary_auto,
            manager._commentary_reference, manager._commentary_auto_geometry))

    def assert_rejected(self, action):
        before = self.snapshot()
        changes = []
        self.model.display_changed.connect(lambda:changes.append(True))
        with patch.object(self.model.repository, 'save', side_effect=OSError('storage unavailable')):
            with self.assertRaisesRegex(OSError, 'storage unavailable'):action()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(changes, [])

    def test_failed_named_geometry_edits_restore_every_capture_surface(self):
        self.manager.set_player_group(60, 580, 300, 12)
        actions = (lambda:self.manager.set_player_group(220, 440, 360, 20),
                   lambda:self.manager.scale_player_group(1.1), self.manager.reset_geometry,
                   lambda:self.manager.center_commentary(QRectF(0, 0, 1920, 1080)),
                   self.manager.place_table_scores)
        for action in actions:
            with self.subTest(action=action):self.assert_rejected(action)

    def test_failed_drag_release_restores_the_last_accepted_group(self):
        before = self.snapshot()
        self.manager.translate_player_group(self.manager.player_group_snapshot(), QPoint(90, 45))
        with patch.object(self.model.repository, 'save', side_effect=OSError('storage unavailable')):
            with self.assertRaises(OSError):self.manager.save_geometry()
        self.assertEqual(self.snapshot(), before)

    def test_failed_split_programme_drag_restores_the_native_window(self):
        before = self.snapshot()
        window = self.manager.split_programme_windows['opening']
        window.move(window.x()+80, window.y()+50)
        with patch.object(self.model.repository, 'save', side_effect=OSError('storage unavailable')):
            with self.assertRaises(OSError):window.save_geometry()
        self.assertEqual(self.snapshot(), before)

    def test_failed_drag_does_not_restore_an_obsolete_settlement_stage_layout(self):
        self.model.field('roundIndex',7);self.model.field('dealer',3)
        self.model.settle(dict(type='ron', winner=0, loser=1, han=3, fu=40), None)
        self.model.commit_match();self.manager.set_bottom_mode('match_result')
        before = self.snapshot()
        window = self.manager.fullscreen_capture
        window.move(window.x()+80,window.y()+30)
        with patch.object(self.model.repository,'save',side_effect=OSError('storage unavailable')):
            with self.assertRaises(OSError):self.manager.save_geometry()
        self.assertEqual(self.snapshot(),before)

    def test_group_layout_and_padding_commit_in_one_write(self):
        self.assert_rejected(lambda:self.manager.set_player_group(80, 420, 350, 11,
            settings_patch={'player_side_padding':9.}))
        with patch.object(self.model.repository, 'save', wraps=self.model.repository.save) as save:
            self.manager.set_player_group(80, 420, 350, 11, settings_patch={'player_side_padding':9.})
        self.assertEqual(save.call_count, 1)
        restarted = Model(self.model.path)
        self.assertEqual(restarted.settings['player_side_padding'], 9.)
        self.assertEqual(restarted.settings['windows']['player0'][:3], [80, 420, 350])

    def test_sidebar_mode_and_its_visibility_fail_together(self):
        self.assert_rejected(lambda:self.manager.set_sidebar_mode('individual', True))
        self.assert_rejected(lambda:self.manager.show_conditions('sidebar'))
        self.assert_rejected(lambda:self.manager.show_conditions('four'))
        self.manager.show_conditions('sidebar')
        self.assert_rejected(self.manager.hide_conditions)

    def test_preset_config_position_and_visibility_fail_together(self):
        preset = default_sidebar_presets()[0]
        preset.update(id='moved', title='Moved view', geometry=[150, 110, 400, 650])
        preset['settings'].update(sidebar_page_size=4, sidebar_page=2)
        self.manager.save_sidebar_presets([preset])
        self.assert_rejected(lambda:self.manager.apply_sidebar_preset('moved'))
        self.manager.apply_sidebar_preset('moved')
        restarted = Model(self.model.path)
        self.assertEqual(restarted.state['broadcast']['config']['sidebar_page_size'], 4)
        self.assertEqual(restarted.settings['windows']['qualification'][:2], [150, 110])
        self.assertTrue(restarted.settings['visible']['qualification'])

    def test_failed_preset_edit_does_not_replace_the_active_identity(self):
        self.manager.apply_sidebar_preset('personal-home')
        identity = self.manager._active_sidebar_preset_id
        self.assert_rejected(lambda:self.manager.save_sidebar_presets([]))
        self.assertEqual(self.manager._active_sidebar_preset_id, identity)

    def test_bottom_mode_combines_config_visibility_and_history(self):
        self.model.field('honba', 2); self.model.history('undo')
        depth = len(self.model.undo), len(self.model.redo)
        self.assert_rejected(lambda:self.manager.set_bottom_mode('team'))
        self.manager.set_bottom_mode('team')
        self.assertEqual((len(self.model.undo), len(self.model.redo)), depth)
        self.model.history('redo')
        self.assertEqual(self.model.state['broadcast']['config']['standings_mode'], 'team')
        self.assertEqual(self.model.state['honba'], 2)

    def test_failed_empty_scene_recovery_keeps_the_saved_clean_picture(self):
        self.manager.apply_scene('clean')
        self.assert_rejected(lambda:self.manager.set_overlays_visible(True))

    def test_programme_select_stop_and_toggle_do_not_clear_before_a_failed_write(self):
        self.manager.programme.select('statistics')
        for action in (lambda:self.manager.programme.select('chart'),
                       lambda:self.manager.programme.select('opening'),
                       self.manager.programme.stop,
                       lambda:self.manager.programme.toggle('statistics')):
            with self.subTest(action=action):
                with patch.object(self.manager.postmatch, 'stop') as stop:
                    self.assert_rejected(action)
                    stop.assert_not_called()

    def test_failed_replay_does_not_reset_current_presentation(self):
        self.model.settle(dict(type='ron', winner=1, loser=0, han=3, fu=30), None)
        self.assert_rejected(self.manager.replay)

    def test_failed_automatic_next_hand_keeps_pending_state_and_persisted_flags(self):
        self.model.settle(dict(type='ron', winner=1, loser=0, han=3, fu=30), None)
        self.manager.pending_next = True
        self.assert_rejected(lambda:self.manager._advance_after_settlement(time.monotonic()))

    def test_native_geometry_failure_is_reported_without_a_leaked_exception(self):
        messages = []
        self.manager.display_configuration.failed.connect(messages.append)
        before = self.snapshot()
        window = self.manager.fullscreen_capture
        window.move(window.x()+80, window.y()+30)
        with patch.object(self.model.repository, 'save', side_effect=OSError('storage unavailable')):
            window._save_geometry_from_input()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(messages, ['storage unavailable'])

    def test_automatic_timer_reports_a_failed_commit_once_and_keeps_its_pending_cue(self):
        messages = []
        self.manager.display_configuration.failed.connect(messages.append)
        self.manager.stage = 'final_points_out'
        self.manager.stage_started = time.monotonic()-1
        self.manager._final_target = 'pt'
        before = self.snapshot()
        with patch.object(self.model.repository, 'save', side_effect=OSError('storage unavailable')) as save:
            self.manager.timer.start(1)
            QTest.qWait(60)
        self.assertEqual(messages, ['storage unavailable'])
        self.assertEqual(save.call_count, 1)
        self.assertFalse(self.manager.timer.isActive())
        self.assertEqual(self.snapshot(), before)

    def test_commentary_timeout_reports_failure_without_hiding_the_saved_caption(self):
        self.manager.set_extension_visible('commentary',True)
        messages = []
        self.manager.display_configuration.failed.connect(messages.append)
        before = copy.deepcopy((self.model.settings,self.manager.enabled))
        self.manager.timer.stop()
        with patch.object(self.model.repository, 'save', side_effect=OSError('storage unavailable')):
            self.manager.commentary_timer.start(1)
            QTest.qWait(30)
        self.assertEqual(messages, ['storage unavailable'])
        self.assertEqual((self.model.settings,self.manager.enabled),before)

    def test_dispose_releases_window_snapshots_and_manager_reference(self):
        service = self.manager.display_configuration
        service.failed.connect(lambda _:None)
        self.manager.close(persist=False)
        self.assertTrue(service._disposed)
        self.assertIsNone(service.manager)
        self.assertIsNone(service.accepted)
        self.assertEqual(service.receivers('2failed(QString)'), 0)


if __name__ == '__main__':unittest.main()
