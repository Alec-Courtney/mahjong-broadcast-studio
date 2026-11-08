"""Library indexes and application preferences survive failed and competing writes."""
import concurrent.futures
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QColor
from application_preferences import ApplicationPreferences, preference_defaults
from event_store import EventStore, resolve_active_directory
from model import Model
from media_store import import_media_file, resolve_media_path
import event_store
import application_preferences

APP = QApplication.instance() or QApplication([])


class LibraryIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='tmp-library-integrity-', dir=ROOT/'tests')
        self.root = Path(self.temporary.name)

    def tearDown(self):
        APP.processEvents()
        self.temporary.cleanup()

    def model_and_store(self):
        model = Model(self.root)
        return model, EventStore(model)

    def broken_index_writer(self, filename, payload):
        if Path(filename).name == 'library.json':raise OSError('index unavailable')
        return self.atomic_writer(filename, payload)

    def test_event_identity_cannot_be_changed_by_metadata_update(self):
        model, store = self.model_and_store()
        before = copy.deepcopy((model.event, model.state))
        saved = (model.path/'session.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '赛事编号创建后不可修改'):
            store.update(id='a'*32, name='不应保存')
        self.assertEqual((model.event, model.state), before)
        self.assertEqual((model.path/'session.json').read_bytes(), saved)
        restarted = Model(self.root)
        EventStore(restarted)
        self.assertEqual(restarted.event['id'], before[0]['id'])

    def test_failed_first_migration_leaves_model_and_legacy_files_intact(self):
        model = Model(self.root)
        model.set_score(0, 42000)
        original = (self.root/'session.json').read_bytes()
        preferences = model.preferences.path.read_bytes()
        before = (model.path, copy.deepcopy(model.state), model.event, model.preferences)
        self.atomic_writer = event_store.atomic_json
        with patch('event_store.atomic_json', side_effect=self.broken_index_writer):
            with self.assertRaisesRegex(OSError, 'index unavailable'):EventStore(model)
        self.assertEqual((model.path, model.state, model.event, model.preferences), before)
        self.assertIsNone(model.event_store)
        self.assertEqual((self.root/'session.json').read_bytes(), original)
        self.assertEqual(model.preferences.path.read_bytes(), preferences)
        self.assertFalse((self.root/'library.json').exists())
        self.assertFalse(list((self.root/'events').glob('*/session.json')))
        store = EventStore(model)
        self.assertEqual(model.state['players'][0]['score'], 42000)
        self.assertEqual(len(store.list_events()), 1)

    def test_failed_fresh_migration_does_not_leave_new_shared_preferences(self):
        model = Model(self.root)
        self.atomic_writer = event_store.atomic_json
        with patch('event_store.atomic_json', side_effect=self.broken_index_writer):
            with self.assertRaises(OSError):EventStore(model)
        self.assertEqual(model.path, self.root)
        self.assertIsNone(model.event)
        self.assertFalse(model.preferences.path.exists())

    def test_index_write_failure_does_not_rewrite_or_publish_target_event(self):
        model, store = self.model_and_store()
        original = model.event['id']
        target = store.create('目标赛事', 'individual_league')
        model.field('honba', 7)
        target_path = model.path/'session.json'
        target_bytes = target_path.read_bytes()
        store.open(original)
        model.setting('console_theme', 'light')
        before = copy.deepcopy((model.path, model.state, model.settings, model.event, model.undo, model.redo))
        index_bytes = store.index_path.read_bytes()
        self.atomic_writer = event_store.atomic_json
        with patch('event_store.atomic_json', side_effect=self.broken_index_writer):
            with self.assertRaises(OSError):store.open(target)
        self.assertEqual((model.path, model.state, model.settings, model.event, model.undo, model.redo), before)
        self.assertEqual(target_path.read_bytes(), target_bytes)
        self.assertEqual(store.index_path.read_bytes(), index_bytes)

    def test_failed_create_or_import_does_not_leave_unlisted_event_snapshots(self):
        model, store = self.model_and_store()
        exported = self.root/'portable.json'
        model.export_data(exported)
        original = model.event['id']
        directories = set((self.root/'events').iterdir())
        self.atomic_writer = event_store.atomic_json
        for operation in (lambda:store.create('不应登记', 'team_league'), lambda:store.import_new(exported)):
            with self.subTest(operation=operation), patch('event_store.atomic_json', side_effect=self.broken_index_writer):
                with self.assertRaises(OSError):operation()
            self.assertEqual(model.event['id'], original)
            self.assertEqual(store.index['events'], [original])
            self.assertEqual(json.loads(store.index_path.read_text(encoding='utf-8'))['events'], [original])
            self.assertEqual(set((self.root/'events').iterdir()), directories)

    def test_failed_replay_preparation_cannot_delete_a_published_library_entry(self):
        model, store = self.model_and_store()
        model.settings['local_demo_suite'] = True
        model.save()
        original = model.event['id']
        index = store.index_path.read_bytes()
        directories = set((self.root/'events').iterdir())
        with patch('demo_suite.restore_transition', side_effect=ValueError('invalid replay')):
            with self.assertRaisesRegex(ValueError, 'invalid replay'):
                store.create('无效回放', 'individual_league', copy_config=True)
        self.assertEqual(model.event['id'], original)
        self.assertEqual(store.index_path.read_bytes(), index)
        self.assertEqual(set((self.root/'events').iterdir()), directories)
        self.assertTrue((model.path/'session.json').exists())

    def test_two_stores_merge_new_events_and_refresh_visible_library(self):
        first, a = self.model_and_store()
        second = Model(self.root)
        b = EventStore(second)
        created_a = a.create('A 新建', 'individual_league')
        created_b = b.create('B 新建', 'team_league')
        expected = {first_event['id'] for first_event in b.list_events()}
        self.assertIn(created_a, expected)
        self.assertIn(created_b, expected)
        self.assertEqual({row['id'] for row in a.list_events()}, expected)
        a.open(created_b)
        self.assertEqual(first.event['id'], created_b)

    def test_malformed_indexes_are_rejected_without_mutating_the_model(self):
        identity = 'a'*32
        invalid = [[], {}, {'version':True,'active_id':identity,'events':[identity]},
                   {'version':1,'active_id':identity,'events':[]},
                   {'version':1,'active_id':'b'*32,'events':[identity]},
                   {'version':1,'active_id':identity,'events':[identity,identity]},
                   {'version':1,'active_id':identity,'events':[identity,'../outside']},
                   {'version':1,'active_id':identity,'events':[identity,{}]}]
        model = Model(self.root)
        before = copy.deepcopy((model.path, model.state, model.settings, model.event))
        for index in invalid:
            with self.subTest(index=index):
                raw = json.dumps(index).encode('utf-8')
                (self.root/'library.json').write_bytes(raw)
                with self.assertRaisesRegex(ValueError, '索引无效'):EventStore(model)
                self.assertEqual((model.path, model.state, model.settings, model.event), before)
                self.assertEqual((self.root/'library.json').read_bytes(), raw)

    def test_startup_directory_uses_validated_active_index_and_ignores_legacy_root_snapshot(self):
        self.assertEqual(resolve_active_directory(self.root), self.root.resolve())
        model, store = self.model_and_store()
        (self.root/'session.json').write_text('invalid legacy snapshot', encoding='utf-8')
        self.assertEqual(resolve_active_directory(self.root), model.path.resolve())
        index = copy.deepcopy(store.index)
        index['active_id'] = '../outside'
        store.index_path.write_text(json.dumps(index), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '索引无效'):resolve_active_directory(self.root)

    def test_concurrent_shared_preference_writes_keep_both_changed_fields(self):
        initial = preference_defaults()
        first = ApplicationPreferences(self.root)
        first.save(initial)
        second = ApplicationPreferences(self.root)
        first_values = dict(initial)
        second_values = dict(initial)
        first.apply(first_values)
        second.apply(second_values)
        first_values['console_theme'] = 'light'
        second_values['settings_font_size'] = 19
        first_entered = threading.Event()
        second_started = threading.Event()
        atomic_writer = application_preferences.atomic_json
        def slow_first_write(filename, payload):
            if payload['settings']['console_theme'] == 'light' and not first_entered.is_set():
                first_entered.set()
                self.assertTrue(second_started.wait(2))
            atomic_writer(filename, payload)
        def save_second():
            self.assertTrue(first_entered.wait(2))
            second_started.set()
            second.save(second_values)
        with patch('application_preferences.atomic_json', side_effect=slow_first_write):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(first.save, first_values)
                b = pool.submit(save_second)
                a.result(timeout=5)
                b.result(timeout=5)
        shared = first.read()
        self.assertEqual(shared['console_theme'], 'light')
        self.assertEqual(shared['settings_font_size'], 19)

    def portrait(self, filename, color):
        image = QImage(32, 48, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        path = self.root/filename
        self.assertTrue(image.save(str(path), 'PNG'))
        return path

    def test_embedded_backup_repairs_a_corrupted_existing_content_addressed_asset(self):
        model, store = self.model_and_store()
        source = self.portrait('original.png', '#224466')
        reference = import_media_file(model.path, source, 'photo')
        model.update(lambda state:state['players'][0].update(photo=reference))
        transfer = self.root/'portable.json'
        model.export_data(transfer)
        target = resolve_media_path(model.path, reference)
        original = target.read_bytes()
        target.write_bytes(b'truncated image')
        store.import_new(transfer)
        self.assertEqual(resolve_media_path(model.path, reference).read_bytes(), original)
        self.assertFalse(QImage(str(resolve_media_path(model.path, reference))).isNull())

    def test_export_rejects_valid_but_misnamed_asset_instead_of_creating_unimportable_json(self):
        model, store = self.model_and_store()
        source = self.portrait('original.png', '#224466')
        replacement = self.portrait('replacement.png', '#b83344')
        reference = import_media_file(model.path, source, 'photo')
        model.update(lambda state:state['players'][0].update(photo=reference))
        resolve_media_path(model.path, reference).write_bytes(replacement.read_bytes())
        transfer = self.root/'portable.json'
        with self.assertRaisesRegex(ValueError, '托管媒体素材校验失败'):
            model.export_data(transfer)
        self.assertFalse(transfer.exists())

    def test_copied_configuration_does_not_reuse_old_match_conditions(self):
        model, store = self.model_and_store()
        identity = model.state['players'][0]['id']
        model.configure_broadcast(dict(qualification_calculation='manual',
            manual_conditions={identity:'旧半庄需直击 12000 点'}, qualification_final=True,
            qualification_focus=identity, qualification_layout='sidebar'))
        model.setting('final_cards_visible', True)
        store.create('同赛制的新比赛', 'individual_league', copy_config=True)
        config = model.state['broadcast']['config']
        self.assertEqual(config['qualification_calculation'], 'auto')
        self.assertEqual(config['manual_conditions'], {})
        self.assertFalse(config['qualification_final'])
        self.assertEqual(config.get('qualification_focus', ''), '')
        self.assertEqual(config['qualification_layout'], 'sidebar')
        self.assertFalse(model.settings['final_cards_visible'])

    def test_copying_team_settings_to_individual_does_not_keep_active_team_rankings(self):
        model, store = self.model_and_store()
        store.create('团队来源', 'team_league')
        roster = copy.deepcopy(model.state['broadcast']['roster'])
        for index, profile in enumerate(roster):profile.update(team='队伍'+str(index), team_id='club-'+str(index))
        model.set_roster(roster)
        model.configure_broadcast(dict(standings_mode='team', sidebar_mode='team'))
        store.update(qualification_count=3)
        store.create('个人目标', 'individual_league', copy_config=True)
        config = model.state['broadcast']['config']
        self.assertEqual((config['event_mode'], config['qualification_mode'],
                          config['standings_mode'], config['sidebar_mode']), ('individual',)*4)
        self.assertEqual(model.event['qualification_count'], 0)
        self.assertEqual([p['team_id'] for p in model.state['broadcast']['roster']], [p['team_id'] for p in roster])


if __name__ == '__main__':unittest.main()
