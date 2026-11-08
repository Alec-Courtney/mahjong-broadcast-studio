"""Behavioral checks for independent table, event-record and persistence services."""
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
from event_results import prepare_result, record_result
from event_store import EventStore
from model import Model
from session_repository import atomic_json
import event_schedule

APP = QApplication.instance() or QApplication([])


class ServiceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / 'tests')
        self.directory = Path(self.temporary.name)
        self.model = Model(self.directory)
        self.store = EventStore(self.model)

    def tearDown(self):
        APP.processEvents()
        self.temporary.cleanup()

    def result(self, scores=None, match_id=None, seats=None):
        return prepare_result(
            self.model, seats or [player['id'] for player in self.model.state['players']],
            scores or [40000, 30000, 25000, 25000], match_id=match_id)

    def test_atomic_writer_recovers_from_a_transient_replace_lock(self):
        destination = self.directory / 'locked.json'
        destination.write_text('{"previous": true}', encoding='utf-8')
        replace = Path.replace
        attempts = []

        def briefly_locked(source, target):
            attempts.append(target)
            if len(attempts) < 3:
                raise PermissionError('temporarily locked')
            return replace(source, target)

        with patch.object(Path, 'replace', briefly_locked), patch('session_repository.time.sleep') as sleep:
            atomic_json(destination, {'updated': True})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(json.loads(destination.read_text(encoding='utf-8')), {'updated': True})
        self.assertFalse(destination.with_name('locked.json.tmp').exists())

    def test_atomic_writer_stops_retrying_and_preserves_original_on_persistent_lock(self):
        destination = self.directory / 'locked.json'
        original = '{"previous": true}'
        destination.write_text(original, encoding='utf-8')
        with patch.object(Path, 'replace', side_effect=PermissionError('locked')) as replace:
            with patch('session_repository.time.sleep') as sleep:
                with self.assertRaises(PermissionError):
                    atomic_json(destination, {'updated': True})
        self.assertEqual(replace.call_count, 5)
        self.assertEqual(sleep.call_count, 4)
        self.assertEqual(destination.read_text(encoding='utf-8'), original)

    def test_atomic_writer_does_not_retry_unrelated_io_errors(self):
        with patch.object(Path, 'replace', side_effect=OSError('device failure')) as replace:
            with patch('session_repository.time.sleep') as sleep:
                with self.assertRaises(OSError):
                    atomic_json(self.directory / 'failed.json', {'updated': True})
        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()

    def test_other_table_result_keeps_live_undo_and_redo(self):
        self.model.set_score(0, 32000)
        self.model.set_score(0, 34000)
        self.model.history('undo')
        depth = (len(self.model.undo), len(self.model.redo))
        record = self.result()
        record_result(self.store, record)
        self.assertEqual((len(self.model.undo), len(self.model.redo)), depth)
        self.model.history('undo')
        self.assertEqual(self.model.state['players'][0]['score'], 25000)
        self.assertEqual(self.model.state['broadcast']['matches'], [record])
        self.model.history('redo')
        self.assertEqual(self.model.state['players'][0]['score'], 32000)
        self.model.history('redo')
        self.assertEqual(self.model.state['players'][0]['score'], 34000)
        self.assertEqual(self.model.state['broadcast']['matches'], [record])

    def test_corrected_other_table_result_survives_table_undo(self):
        first = self.result()
        record_result(self.store, first)
        self.model.field('honba', 2)
        corrected = self.result([20000, 40000, 30000, 30000], first['id'])
        record_result(self.store, corrected)
        self.model.history('undo')
        self.assertEqual(self.model.state['honba'], 0)
        self.assertEqual(self.model.state['broadcast']['matches'], [corrected])
        restored = Model(self.model.path)
        self.assertEqual(restored.state['broadcast']['matches'], [corrected])

    def test_record_write_failure_does_not_publish_or_change_history(self):
        self.model.field('honba', 2)
        before = copy.deepcopy((self.model.state, self.model.undo, self.model.redo))
        emitted = []
        self.model.changed.connect(lambda *args: emitted.append(args))
        with patch.object(self.model.repository, 'save', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                record_result(self.store, self.result())
        self.assertEqual((self.model.state, self.model.undo, self.model.redo), before)
        self.assertEqual(emitted, [])

    def test_record_write_does_not_reprocess_current_hand_or_transition(self):
        self.model.riichi(0)
        self.model.settle(dict(type='ron', winner=1, loser=0, han=3, fu=30), None)
        self.model.commit_match()
        before = copy.deepcopy(self.model.state)
        transition = copy.deepcopy(self.model.last_transition)
        record = self.result()
        record_result(self.store, record)
        after = copy.deepcopy(self.model.state)
        after['broadcast']['matches'] = [match for match in after['broadcast']['matches']
                                         if match['id'] != record['id']]
        self.assertEqual(after, before)
        self.assertEqual(self.model.last_transition, transition)

    def test_live_correction_undo_preserves_other_table_archive(self):
        self.model.field('roundIndex', 7)
        self.model.field('dealer', 3)
        self.model.settle(dict(type='ron', winner=0, loser=1, han=3, fu=30), None)
        self.model.matches.confirm_finished()
        record = self.result()
        record_result(self.store, record)
        before = copy.deepcopy(self.model.state)
        self.model.set_score(0, self.model.state['players'][0]['score'] + 1000)
        self.model.history('undo')
        self.assertEqual(self.model.state, before)
        manual = next(match for match in self.model.state['broadcast']['matches']
                      if match['id'] == record['id'])
        self.assertEqual(manual, record)

    def test_undo_keeps_roster_ids_referenced_by_external_results(self):
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        roster.append(dict(id='guest', name='补录选手', seed=5))
        self.model.set_roster(roster)
        seats = [player['id'] for player in roster[:3]] + ['guest']
        record = self.result(seats=seats)
        record_result(self.store, record)
        self.model.history('undo')
        self.model.engine.validate(self.model.state)
        self.assertIn('guest', {player['id'] for player in self.model.state['broadcast']['roster']})
        self.assertEqual(self.model.state['broadcast']['matches'], [record])

    def test_production_lifecycle_guards_are_shared(self):
        before = copy.deepcopy(self.model.state)
        with self.assertRaisesRegex(ValueError, '终局'):
            self.model.matches.confirm_finished()
        self.assertEqual(self.model.state, before)
        self.model.settle(dict(type='ron', winner=1, loser=0, han=3, fu=30), None)
        before = copy.deepcopy(self.model.state)
        with self.assertRaisesRegex(ValueError, '尚未结束并确认'):
            self.model.matches.start_next()
        self.assertEqual(self.model.state, before)
        # Explicit legacy calls retain their former behavior for existing scripts.
        self.model.commit_match()
        self.assertTrue(self.model.state['broadcast']['matches'][0]['committed'])

    def test_knockout_lifecycle_requests_schedule_without_side_effects(self):
        self.store.create('淘汰赛', 'individual_knockout')
        bracket = event_schedule.generate(self.model.state['broadcast']['roster'])
        self.store.update(bracket=bracket)
        before = copy.deepcopy(self.model.state)
        self.assertEqual(self.model.matches.start_next(), 'schedule')
        self.assertEqual(self.model.state, before)

    def test_scheduled_table_publishes_once_with_consistent_metadata(self):
        self.store.create('淘汰赛', 'individual_knockout')
        bracket = event_schedule.generate(self.model.state['broadcast']['roster'])
        self.store.update(bracket=bracket)
        node = bracket['nodes'][0]
        observed = []
        self.model.changed.connect(lambda before, after, kind: observed.append(
            (kind, after['broadcast'].get('active_node_id'),
             self.model.event['bracket']['active_node'])))
        event_schedule.start_node(self.store, node['id'])
        self.assertEqual(observed, [('new_match', node['id'], node['id'])])
        saved = json.loads((self.model.path / 'session.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['state']['id'], self.model.state['id'])
        self.assertEqual(saved['event']['bracket']['active_node'], node['id'])

    def test_scheduled_table_write_failure_leaves_entire_workspace_unchanged(self):
        self.store.create('淘汰赛', 'individual_knockout')
        bracket = event_schedule.generate(self.model.state['broadcast']['roster'])
        self.store.update(bracket=bracket)
        self.model.field('honba', 2)
        before = copy.deepcopy((self.model.state, self.model.event, self.model.settings,
                                self.model.undo, self.model.redo))
        emitted = []
        self.model.changed.connect(lambda *args: emitted.append(args))
        with patch.object(self.model.repository, 'save', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                event_schedule.start_node(self.store, bracket['nodes'][0]['id'])
        self.assertEqual((self.model.state, self.model.event, self.model.settings,
                          self.model.undo, self.model.redo), before)
        self.assertEqual(emitted, [])

    def test_persisted_v6_settings_and_legacy_theme_migrate(self):
        target = self.directory / 'portable.json'
        self.model.export_data(target)
        saved = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(saved['version'], 6)
        saved['settings'].update(theme='saikouisen', console_theme='light')
        saved['settings'].pop('player_style_version', None)
        target.write_text(json.dumps(saved), encoding='utf-8')
        self.model.import_data(target)
        self.assertEqual(self.model.settings['theme'], 'mleague')
        self.assertEqual(self.model.settings['console_theme'], 'light')
        saved['settings']['console_theme'] = 'unknown'
        target.write_text(json.dumps(saved), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '后台主题'):
            self.model.read_data(target)

    def test_v5_portable_without_event_metadata_imports_into_new_event(self):
        target = self.directory / 'legacy-v5.json'
        self.model.export_data(target)
        saved = json.loads(target.read_text(encoding='utf-8'))
        saved['version'] = 5
        saved.pop('event')
        saved['settings'].pop('console_theme', None)
        saved['settings'].pop('player_style_version', None)
        saved['settings']['theme'] = 'saikouisen'
        target.write_text(json.dumps(saved), encoding='utf-8')
        original_id = self.model.event['id']
        new_id = self.store.import_new(target)
        self.assertNotEqual(original_id, new_id)
        self.assertEqual(self.model.settings['theme'], 'mleague')
        self.assertEqual(self.model.settings['console_theme'], 'dark')
        self.assertEqual(len(self.store.list_events()), 2)

    def test_explicit_legacy_player_style_survives_restart_and_portable_import(self):
        self.model.setting('theme', 'saikouisen')
        self.assertEqual(self.model.settings['player_style_version'], 1)
        restarted = Model(self.model.path)
        self.assertEqual(restarted.settings['theme'], 'saikouisen')
        target = self.directory / 'legacy-style.json'
        self.model.export_data(target)
        self.model.setting('theme', 'mleague')
        self.model.import_data(target)
        self.assertEqual(self.model.settings['theme'], 'saikouisen')
        self.assertEqual(self.model.settings['player_style_version'], 1)

    def test_card_profiles_survive_theme_switch_restart_and_export_independently(self):
        state = copy.deepcopy(self.model.state)
        undo = copy.deepcopy(self.model.undo)
        ml = copy.deepcopy(self.model.settings['player_card_style'])
        legacy = copy.deepcopy(self.model.settings['saikouisen_card_style'])
        ml['text']['name'].update(family='Microsoft YaHei', size=34, letter_spacing=2)
        ml['text']['name']['paint'].update(mode='gradient', start='#FFAA4400', end='#FFFFFFFF')
        legacy['team_name_gap'] = 9
        legacy['backgrounds']['score'].update(mode='gradient', start='#FF112233', end='#FF778899')
        self.model.setting('player_card_style', ml)
        self.model.setting('saikouisen_card_style', legacy)
        for theme in ('saikouisen', 'mleague', 'saikouisen'):
            self.model.setting('theme', theme)
        self.assertEqual((self.model.state, self.model.undo), (state, undo))
        target = self.directory / 'card-profiles.json'
        self.model.export_data(target)
        restarted = Model(self.model.path)
        restarted.import_data(target)
        self.assertEqual(restarted.settings['theme'], 'saikouisen')
        self.assertEqual(restarted.settings['player_card_style'], ml)
        self.assertEqual(restarted.settings['saikouisen_card_style'], legacy)
        ml['text']['name']['size'] = 10
        self.assertEqual(self.model.settings['player_card_style']['text']['name']['size'], 34)

    def test_invalid_card_profile_import_preserves_current_session(self):
        target = self.directory / 'invalid-card.json'
        self.model.export_data(target)
        saved = json.loads(target.read_text(encoding='utf-8'))
        before = copy.deepcopy((self.model.state, self.model.settings, self.model.undo))
        for invalid in (None, {'text': {'name': {'size': 200}}},
                        {'backgrounds': {'score': {'start': 'red'}}}):
            saved['settings']['player_card_style'] = invalid
            target.write_text(json.dumps(saved), encoding='utf-8')
            with self.assertRaises(ValueError):
                self.model.import_data(target)
            self.assertEqual((self.model.state, self.model.settings, self.model.undo), before)

    def test_old_session_without_card_profiles_receives_distinct_defaults(self):
        target = self.directory / 'old-card-settings.json'
        self.model.export_data(target)
        saved = json.loads(target.read_text(encoding='utf-8'))
        for key in ('player_card_style', 'saikouisen_card_style'):
            saved['settings'].pop(key)
        target.write_text(json.dumps(saved), encoding='utf-8')
        self.model.import_data(target, replace_settings=True)
        self.assertEqual(self.model.settings['player_card_style']['backgrounds']['score']['mode'], 'gradient')
        self.assertEqual(self.model.settings['saikouisen_card_style']['backgrounds']['score']['mode'], 'solid')
        self.assertEqual(self.model.settings['saikouisen_card_style']['text']['score']['paint']['start'], '#FF070909')

    def test_commentary_changes_survive_table_undo_without_creating_score_history(self):
        self.model.set_score(0, 31000)
        self.model.set_score(0, 32000)
        self.model.history('undo')
        depth = len(self.model.undo), len(self.model.redo)
        patch = dict(custom_terms=[['安全牌', '根据场面信息判断较安全的牌。']],
                     term_button_columns=4, term_favorites=['custom:安全牌'],
                     term_buttons=[dict(slot=7, ref='custom:安全牌', label='防守提示')],
                     commentary_title='安全牌', commentary_body='根据场面信息判断较安全的牌。')
        self.model.configure_commentary(patch)
        self.assertEqual((len(self.model.undo), len(self.model.redo)), depth)
        self.model.history('redo')
        self.assertEqual(self.model.state['players'][0]['score'], 32000)
        self.model.history('undo')
        self.assertEqual(self.model.state['players'][0]['score'], 31000)
        for key, value in patch.items():
            self.assertEqual(self.model.state['broadcast']['config'][key], value)
        restarted = Model(self.model.path)
        self.assertEqual(restarted.state['broadcast']['config']['term_buttons'], patch['term_buttons'])

    def test_invalid_or_failed_commentary_save_preserves_table_and_history(self):
        before = copy.deepcopy((self.model.state, self.model.undo, self.model.redo))
        for values in ({'seats': []}, {'term_button_columns': 0},
                      {'term_buttons': [dict(slot=12, ref='one_chance')]},
                      {'term_buttons': [dict(slot=1, ref='missing')]},
                      {'term_buttons': [dict(slot=1, ref='one_chance'), dict(slot=1, ref='no_chance')]}):
            with self.assertRaises(ValueError):
                self.model.configure_commentary(values)
        with patch.object(self.model.repository, 'save', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.model.configure_commentary({'commentary_title': '未保存'})
        self.assertEqual((self.model.state, self.model.undo, self.model.redo), before)

    def test_unmarked_local_session_upgrades_once_then_allows_legacy_style(self):
        session = self.model.path / 'session.json'
        saved = json.loads(session.read_text(encoding='utf-8'))
        saved['settings'].pop('player_style_version', None)
        saved['settings']['theme'] = 'saikouisen'
        session.write_text(json.dumps(saved), encoding='utf-8')
        upgraded = Model(self.model.path)
        self.assertEqual(upgraded.settings['theme'], 'mleague')
        upgraded.setting('theme', 'saikouisen')
        restarted = Model(self.model.path)
        self.assertEqual(restarted.settings['theme'], 'saikouisen')
        self.assertEqual(restarted.settings['player_style_version'], 1)


if __name__ == '__main__':
    unittest.main()
