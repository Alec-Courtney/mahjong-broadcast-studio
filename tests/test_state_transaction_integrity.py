"""Failed commands and competing workspaces never publish or lose table data."""
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
from model import Model
from event_store import EventStore
from session_repository import atomic_json

APP = QApplication.instance() or QApplication([])
WIN = dict(type='ron', winner=1, loser=0, han=3, fu=30)
INFO = dict(yaku=['立直'], winner=1, name='选手二', kind='ron', han=3, fu=30, yakuman=0)


class StateTransactionIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tmp-state-transaction-', dir=ROOT/'tests')
        self.root = Path(self.temp.name)
        self.model = Model(self.root)
        self.model.save()

    def tearDown(self):
        APP.processEvents()
        self.temp.cleanup()

    def snapshot(self, model=None):
        model = model or self.model
        return copy.deepcopy((model.state, model.settings, model.last_info, model.undo,
                              model.redo, model.last_transition, model.event))

    def test_failed_live_commands_preserve_memory_disk_history_and_observers(self):
        cases = ('score', 'riichi', 'settle', 'draw', 'clear', 'next_hand', 'start_match')
        for name in cases:
            with self.subTest(command=name):
                model = Model(self.root/name)
                if name in ('clear', 'next_hand', 'start_match'):model.settle(WIN, INFO)
                model.save()
                before = self.snapshot(model)
                saved = (model.path/'session.json').read_bytes()
                observed = []
                model.changed.connect(lambda *args: observed.append('changed'))
                model.display_changed.connect(lambda: observed.append('display'))
                command = {'score': lambda: model.set_score(0, 36000),
                           'riichi': lambda: model.riichi(0),
                           'settle': lambda: model.settle(WIN, INFO),
                           'draw': model.draw, 'clear': model.clear,
                           'next_hand': model.next_hand, 'start_match': model.start_match}[name]
                with patch.object(model.repository, 'save', side_effect=OSError('disk full')):
                    with self.assertRaises(OSError):command()
                self.assertEqual(self.snapshot(model), before)
                self.assertEqual((model.path/'session.json').read_bytes(), saved)
                self.assertEqual(observed, [])

    def test_failed_undo_and_redo_do_not_consume_either_stack(self):
        self.model.field('honba', 2)
        for direction in ('undo', 'redo'):
            if direction == 'redo':self.model.history('undo')
            before = self.snapshot()
            observed = []
            self.model.changed.connect(lambda *args: observed.append(True))
            with patch.object(self.model.repository, 'save', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):self.model.history(direction)
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(observed, [])

    def test_settlement_and_its_details_are_one_write_before_notification(self):
        snapshots = []
        def observe(before, after, kind):
            saved = json.loads((self.model.path/'session.json').read_text(encoding='utf-8'))
            snapshots.append((saved, copy.deepcopy(self.model.last_info), kind))
        self.model.changed.connect(observe)
        with patch.object(self.model.repository, 'save', wraps=self.model.repository.save) as save:
            self.model.settle(WIN, INFO)
        self.assertEqual(save.call_count, 1)
        self.assertEqual(len(snapshots), 1)
        saved, info, kind = snapshots[0]
        self.assertEqual(saved['state'], self.model.state)
        self.assertEqual(saved['last_info'], INFO)
        self.assertEqual(info, INFO)
        self.assertEqual(kind, 'ron')
        self.assertTrue(saved['settings']['show_yaku'])
        self.assertFalse(saved['settings']['final_cards_visible'])

    def test_new_hand_and_new_match_have_one_complete_write(self):
        self.model.settle(WIN, INFO)
        for command in (self.model.next_hand, self.model.start_match):
            with patch.object(self.model.repository, 'save', wraps=self.model.repository.save) as save:
                command()
            self.assertEqual(save.call_count, 1)
            saved = json.loads((self.model.path/'session.json').read_text(encoding='utf-8'))
            self.assertIsNone(saved['last_info'])
            self.assertFalse(saved['settings']['show_yaku'])
            self.assertEqual(saved['state'], self.model.state)

    def test_stale_workspace_cannot_overwrite_another_instances_score(self):
        other = Model(self.root)
        self.model.set_score(0, 35000)
        before = self.snapshot(other)
        observed = []
        other.changed.connect(lambda *args: observed.append(True))
        with self.assertRaisesRegex(ValueError, '重新载入'):
            other.set_score(1, 40000)
        self.assertEqual(self.snapshot(other), before)
        self.assertEqual(observed, [])
        self.assertEqual(Model(self.root).state['players'][0]['score'], 35000)

    def test_stale_undo_cannot_erase_an_external_update(self):
        self.model.field('honba', 2)
        other = Model(self.root)
        other.set_score(0, 35000)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, '重新载入'):self.model.history('undo')
        self.assertEqual(self.snapshot(), before)
        restarted = Model(self.root)
        self.assertEqual(restarted.state['honba'], 2)
        self.assertEqual(restarted.state['players'][0]['score'], 35000)

    def test_explicit_reload_recovers_a_stale_workspace_without_rewriting_the_file(self):
        self.model.field('honba', 2)
        other = Model(self.root)
        other.set_score(0, 35000)
        saved = (self.root/'session.json').read_bytes()
        observed = []
        self.model.changed.connect(lambda before, after, kind: observed.append(kind))
        self.model.reload_current()
        self.assertEqual(observed, ['import'])
        self.assertEqual(self.model.state['players'][0]['score'], 35000)
        self.assertEqual((self.root/'session.json').read_bytes(), saved)
        self.assertEqual(self.model.undo, [])
        self.assertEqual(self.model.redo, [])
        self.model.set_score(1, 37000)
        self.assertEqual(Model(self.root).state['players'][1]['score'], 37000)

    def test_cosmetic_json_reformat_does_not_conflict_with_an_identical_save(self):
        other = Model(self.root)
        path = self.root/'session.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        other.save()
        other.set_score(0, 34000)
        self.assertEqual(Model(self.root).state['players'][0]['score'], 34000)

    def test_unreadable_saved_workspace_is_preserved_instead_of_fresh_overwrite(self):
        path = self.root/'session.json'
        path.write_text('{broken json', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '原文件已保留'):Model(self.root)
        self.assertEqual(path.read_text(encoding='utf-8'), '{broken json')

    def test_startup_uses_the_same_version_and_settings_guards_as_import(self):
        path = self.root/'session.json'
        original = json.loads(path.read_text(encoding='utf-8'))
        for patch_value in ('future_version', 'invalid_windows'):
            with self.subTest(value=patch_value):
                payload = copy.deepcopy(original)
                if patch_value == 'future_version':payload['version'] = 999
                else:payload['settings']['windows'] = {'players': [10, 20, 0, 500]}
                encoded = json.dumps(payload).encode('utf-8')
                path.write_bytes(encoded)
                with self.assertRaisesRegex(ValueError, '原文件已保留'):Model(self.root)
                self.assertEqual(path.read_bytes(), encoded)

    def test_atomic_writers_do_not_share_a_temporary_file(self):
        path = self.root/'concurrent.json'
        barrier = threading.Barrier(2)
        replace = Path.replace
        sources = []
        def synchronized_replace(source, destination):
            if source not in sources:
                sources.append(source)
                barrier.wait(timeout=10)
            return replace(source, destination)
        with patch.object(Path, 'replace', synchronized_replace):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda value: atomic_json(path, {'value': value}), (1, 2)))
        self.assertEqual(len(set(sources)), 2)
        self.assertIn(json.loads(path.read_text(encoding='utf-8'))['value'], (1, 2))
        self.assertFalse(list(self.root.glob('.concurrent.json.*.tmp')))

    def test_atomic_write_failure_leaves_no_temporary_file(self):
        path = self.root/'failure.json'
        with patch.object(Path, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):atomic_json(path, {'value': 1})
        self.assertFalse(list(self.root.glob('.failure.json.*.tmp')))

    def test_partial_temp_write_failure_preserves_destination_and_cleans_partial_data(self):
        path = self.root/'partial.json'
        path.write_text('original', encoding='utf-8')
        factory = tempfile.NamedTemporaryFile
        def partial_writer(**kwargs):
            stream = factory(**kwargs)
            def fail(data):
                stream.file.write(data[:3])
                raise OSError('disk full')
            stream.write = fail
            return stream
        with patch('session_repository.tempfile.NamedTemporaryFile', side_effect=partial_writer):
            with self.assertRaises(OSError):atomic_json(path, {'value': 1})
        self.assertEqual(path.read_text(encoding='utf-8'), 'original')
        self.assertFalse(list(self.root.glob('.partial.json.*.tmp')))

    def test_managed_import_cannot_change_type_or_identity(self):
        store = EventStore(self.model)
        target = self.root/'another-kind.json'
        self.model.export_data(target)
        payload = json.loads(target.read_text(encoding='utf-8'))
        payload['event']['kind'] = 'team_league'
        payload['state']['broadcast']['config']['event_mode'] = 'team'
        payload['event']['id'] = 'f'*32
        target.write_text(json.dumps(payload), encoding='utf-8')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, '比赛类型'):self.model.import_data(target)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(store.index['active_id'], self.model.event['id'])

    def test_rejected_reload_preserves_memory_and_does_not_acknowledge_foreign_identity(self):
        EventStore(self.model)
        path = self.model.path/'session.json'
        saved = json.loads(path.read_text(encoding='utf-8'))
        saved['event']['id'] = 'f'*32
        path.write_text(json.dumps(saved), encoding='utf-8')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, '赛事编号'):self.model.reload_current()
        self.assertEqual(self.snapshot(), before)
        with self.assertRaisesRegex(ValueError, '重新载入'):self.model.set_score(0, 34000)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['event']['id'], 'f'*32)


if __name__ == '__main__':unittest.main()
