"""Operator preferences survive event changes and work without an event library."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtWidgets import QApplication
from application_preferences import APPLICATION_KEYS, capture_preferences
from event_store import EventStore
from model import Model

APP = QApplication.instance() or QApplication([])


class ApplicationPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / 'tests')
        self.root = Path(self.temporary.name)
        self.model = Model(self.root)
        self.store = EventStore(self.model)

    def tearDown(self):
        APP.processEvents()
        self.temporary.cleanup()

    def set_preferences(self):
        settings = copy.deepcopy(self.model.settings)
        settings.update(console_theme='light', console_font_family='Microsoft YaHei',
                        console_font_size=17, settings_font_size=18, language='ja_JP')
        self.model.save_preferences(settings)
        return capture_preferences(self.model.settings)

    def test_new_switch_import_and_restart_keep_operator_preferences(self):
        first = self.model.event['id']
        self.model.setting('overlay_font_scale', 1.25)
        self.model.field('honba', 3)
        preferences = self.set_preferences()
        export = self.root / 'old-export.json'
        self.model.export_data(export)
        payload = json.loads(export.read_text(encoding='utf-8'))
        payload['settings'].update(console_theme='dark', console_font_size=10,
                                   settings_font_size=10, language='zh_CN')
        export.write_text(json.dumps(payload), encoding='utf-8')
        second = self.store.create('新团队赛事', 'team_league')
        self.assertEqual(capture_preferences(self.model.settings), preferences)
        self.assertNotIn('overlay_font_scale', self.model.settings)
        self.assertEqual(self.model.state['honba'], 0)
        self.store.open(first)
        self.assertEqual(capture_preferences(self.model.settings), preferences)
        self.assertEqual(self.model.settings['overlay_font_scale'], 1.25)
        self.assertEqual(self.model.state['honba'], 3)
        self.store.open(second)
        self.store.import_new(export)
        self.assertEqual(capture_preferences(self.model.settings), preferences)
        restarted = Model(self.root)
        EventStore(restarted)
        self.assertEqual(capture_preferences(restarted.settings), preferences)
        self.assertEqual(restarted.event['id'], self.model.event['id'])

    def test_global_changes_apply_when_returning_to_older_event(self):
        first = self.model.event['id']
        self.store.create('第二场', 'individual_league')
        preferences = self.set_preferences()
        self.store.open(first)
        self.assertEqual(capture_preferences(self.model.settings), preferences)
        restarted = Model(self.model.path)
        self.assertEqual(restarted.preferences.path, self.root / 'application-preferences.json')
        self.assertEqual(capture_preferences(restarted.settings), preferences)

    def test_migration_uses_active_event_instead_of_stale_root_session(self):
        second = self.store.create('当前赛事', 'individual_league')
        preferences = self.set_preferences()
        self.model.preferences.path.unlink()
        stale = self.root / 'session.json'
        payload = json.loads((self.model.path / 'session.json').read_text(encoding='utf-8'))
        payload['settings'].update(console_theme='dark', console_font_size=9)
        stale.write_text(json.dumps(payload), encoding='utf-8')
        restarted = Model(self.root)
        EventStore(restarted)
        self.assertEqual(restarted.event['id'], second)
        self.assertEqual(capture_preferences(restarted.settings), preferences)
        self.assertTrue(restarted.preferences.path.is_file())

    def test_independent_workspace_shares_preferences_without_creating_event_library(self):
        preferences = self.set_preferences()
        standalone = Model(self.root / 'standalone', preferences_root=self.root)
        self.assertIsNone(standalone.event_store)
        self.assertIsNone(standalone.event)
        self.assertEqual(capture_preferences(standalone.settings), preferences)
        standalone.set_score(0, 33000)
        standalone.setting('console_theme', 'dark')
        restarted = Model(self.root / 'standalone', preferences_root=self.root)
        self.assertEqual(restarted.state['players'][0]['score'], 33000)
        self.assertEqual(restarted.settings['console_theme'], 'dark')
        self.assertFalse((standalone.path / 'library.json').exists())
        event_model = Model(self.root)
        EventStore(event_model)
        self.assertEqual(event_model.settings['console_theme'], 'dark')
        self.assertEqual(event_model.state['players'][0]['score'], 25000)

    def test_local_preference_file_contains_no_event_or_output_state(self):
        self.set_preferences()
        payload = json.loads(self.model.preferences.path.read_text(encoding='utf-8'))
        self.assertEqual(set(payload['settings']), set(APPLICATION_KEYS))
        self.assertNotIn('windows', payload['settings'])
        self.assertNotIn('state', payload)

    def test_failed_setting_save_does_not_publish_or_mutate(self):
        before = copy.deepcopy((self.model.state, self.model.settings, self.model.undo))
        preference_bytes = self.model.preferences.path.read_bytes()
        events = []
        self.model.display_changed.connect(lambda: events.append(True))
        with patch.object(self.model.repository, 'save', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.model.setting('theme', 'saikouisen')
        self.assertEqual((self.model.state, self.model.settings, self.model.undo), before)
        self.assertEqual(self.model.preferences.path.read_bytes(), preference_bytes)
        self.assertEqual(events, [])

    def test_invalid_setting_rejected_without_changing_preferences(self):
        before = copy.deepcopy(self.model.settings)
        with self.assertRaises(ValueError):
            self.model.setting('console_font_size', 200)
        self.assertEqual(self.model.settings, before)

    def test_preference_write_failure_restores_session_before_restart(self):
        self.set_preferences()
        before = copy.deepcopy((self.model.state, self.model.settings, self.model.undo))
        session_bytes = (self.model.path / 'session.json').read_bytes()
        preference_bytes = self.model.preferences.path.read_bytes()
        events = []
        self.model.display_changed.connect(lambda: events.append(True))
        with patch.object(self.model.preferences, 'save', side_effect=OSError('preferences unavailable')):
            with self.assertRaises(OSError):
                self.model.setting('console_theme', 'dark')
        self.assertEqual((self.model.state, self.model.settings, self.model.undo), before)
        self.assertEqual((self.model.path / 'session.json').read_bytes(), session_bytes)
        self.assertEqual(self.model.preferences.path.read_bytes(), preference_bytes)
        self.assertEqual(events, [])
        restarted = Model(self.root)
        EventStore(restarted)
        self.assertEqual(capture_preferences(restarted.settings), capture_preferences(before[1]))

    def test_new_team_configuration_uses_team_qualification(self):
        self.store.create('团队赛事', 'team_league')
        self.assertEqual(self.model.state['broadcast']['config']['qualification_mode'], 'team')
        self.assertEqual(self.model.state['broadcast']['config']['qualification_slots'], 2)

    def test_failed_event_setting_does_not_publish_metadata_or_broadcast_mirror(self):
        self.model.field('honba', 1)
        before = copy.deepcopy((self.model.state, self.model.event, self.model.undo, self.model.redo))
        events = []
        self.model.changed.connect(lambda *_: events.append(True))
        self.model.display_changed.connect(lambda: events.append(True))
        with patch.object(self.model.repository, 'save', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.store.update(name='未保存的标题', qualification_count=3)
        self.assertEqual((self.model.state, self.model.event, self.model.undo, self.model.redo), before)
        self.assertEqual(events, [])

    def test_qualification_cutoff_metadata_and_config_survive_table_undo_together(self):
        self.store.update(qualification_count=3)
        self.model.field('honba', 1)
        self.model.history('undo')
        self.assertEqual(self.model.event['qualification_count'], 3)
        self.assertEqual(self.model.state['broadcast']['config']['qualification_slots'], 3)

    def test_score_only_save_does_not_overwrite_another_workspaces_new_theme(self):
        other = Model(self.root / 'standalone', preferences_root=self.root)
        other.setting('console_theme', 'light')
        self.model.set_score(0, 31000)
        self.assertEqual(self.model.preferences.read()['console_theme'], 'light')
        self.assertEqual(self.model.settings['console_theme'], 'light')
        restarted = Model(self.root)
        EventStore(restarted)
        self.assertEqual(restarted.settings['console_theme'], 'light')

    def test_two_workspaces_merge_different_preference_edits_and_restart_consistently(self):
        other = Model(self.root / 'standalone', preferences_root=self.root)
        other.setting('console_theme', 'light')
        candidate = copy.deepcopy(self.model.settings)
        candidate['settings_font_size'] = 19
        self.model.save_preferences(candidate)
        self.assertEqual(self.model.settings['console_theme'], 'light')
        other.set_score(0, 35000)
        self.assertEqual(other.settings['settings_font_size'], 19)
        self.assertEqual(other.settings['console_theme'], 'light')
        other.setting('console_font_size', 17)
        self.model.field('honba', 2)
        expected = capture_preferences(other.settings)
        self.assertEqual(capture_preferences(self.model.settings), expected)
        managed_restart = Model(self.root)
        EventStore(managed_restart)
        standalone_restart = Model(self.root / 'standalone', preferences_root=self.root)
        self.assertEqual(capture_preferences(managed_restart.settings), expected)
        self.assertEqual(capture_preferences(standalone_restart.settings), expected)

    def test_event_switch_adopts_new_shared_preferences_without_using_old_event_snapshot(self):
        first = self.model.event['id']
        self.store.create('另一赛事', 'individual_league')
        other = Model(self.root / 'standalone', preferences_root=self.root)
        other.setting('console_theme', 'light')
        other.setting('settings_font_size', 20)
        self.store.open(first)
        self.assertEqual(capture_preferences(self.model.settings), capture_preferences(other.settings))

    def test_explicit_setting_can_replace_a_newer_value_with_the_local_selected_value(self):
        other = Model(self.root / 'standalone', preferences_root=self.root)
        other.setting('console_theme', 'light')
        # This model still has the original dark value; an explicit choice is
        # different from an unrelated score save and must win for that field.
        self.model.setting('console_theme', 'dark')
        self.assertEqual(self.model.preferences.read()['console_theme'], 'dark')

    def test_ui_only_first_upgrade_migrates_old_active_event_preferences(self):
        preferences = self.set_preferences()
        event_bytes = (self.model.path / 'session.json').read_bytes()
        self.model.preferences.path.unlink()
        standalone = Model(self.root / 'standalone', preferences_root=self.root)
        self.assertEqual(capture_preferences(standalone.settings), preferences)
        standalone.set_score(0, 31000)
        self.assertEqual((self.model.path / 'session.json').read_bytes(), event_bytes)
        restarted = Model(self.root)
        EventStore(restarted)
        self.assertEqual(capture_preferences(restarted.settings), preferences)


if __name__ == '__main__':
    unittest.main()
