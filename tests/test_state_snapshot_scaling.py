"""Stable archives are shared safely while table undo and corrections isolate."""
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
sys.path.insert(0, str(ROOT/'src'))
from PySide6.QtWidgets import QApplication
from event_results import prepare_result
from model import Model
from state_snapshots import FrozenDict, FrozenList, mutable_copy
import tournament

APP = QApplication.instance() or QApplication([])


class StateSnapshotScalingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tmp-state-sharing-', dir=ROOT/'tests')
        self.model = Model(self.temp.name)
        self.model.settle(dict(type='ron', winner=1, loser=0, han=3, fu=30), None)
        self.model.commit_match()
        self.model.start_match()
        self.model.undo.clear()

    def tearDown(self):
        APP.processEvents()
        self.temp.cleanup()

    def test_json_compatible_archives_reject_nested_mutations(self):
        record = self.model.state['broadcast']['matches'][0]
        self.assertIsInstance(record, FrozenDict)
        self.assertIsInstance(record['hands'], FrozenList)
        self.assertIsInstance(record, dict)
        self.assertEqual(json.loads(json.dumps(record)), mutable_copy(record))
        original = mutable_copy(record)
        for action in (lambda: record.update(title='篡改'),
                       lambda: record['hands'].append({}),
                       lambda: record['results'][0].update(points=999),
                       lambda: record['hands'][0]['before_scores'].pop()):
            with self.assertRaises(TypeError):action()
        self.assertEqual(record, original)
        editable = mutable_copy(record)
        editable['results'][0]['points'] = 999
        self.assertNotEqual(editable, record)

    def test_sixty_table_undo_entries_share_archive_payloads_and_isolate_players(self):
        archive = self.model.state['broadcast']['matches'][0]
        initial = self.model.state['players'][0]['score']
        for index in range(60):self.model.set_score(0, initial+(index+1)*100)
        self.assertEqual(len(self.model.undo), 60)
        for state, info in self.model.undo:
            self.assertIs(state['broadcast']['matches'][0], archive)
            self.assertIsNot(state['players'], self.model.state['players'])
        current = copy.deepcopy(self.model.state)
        for _ in range(60):self.model.history('undo')
        self.assertEqual(self.model.state['players'][0]['score'], initial)
        for _ in range(60):self.model.history('redo')
        self.assertEqual(self.model.state, current)
        self.assertIs(self.model.state['broadcast']['matches'][0], archive)

    def test_manual_record_rebases_all_history_with_one_read_only_replacement(self):
        model = self.model
        model.field('honba', 1)
        model.field('honba', 2)
        seats = [player['id'] for player in model.state['players']]
        record = prepare_result(model, seats, [40000, 30000, 25000, 25000])
        model.records.write_manual(record)
        current = next(value for value in model.state['broadcast']['matches'] if value['id']==record['id'])
        self.assertIsInstance(current, FrozenDict)
        for state, info in model.undo:
            self.assertIs(next(value for value in state['broadcast']['matches'] if value['id']==record['id']), current)
        changed = prepare_result(model, seats, [20000, 40000, 30000, 30000], match_id=record['id'])
        model.records.write_manual(changed)
        corrected = next(value for value in model.state['broadcast']['matches'] if value['id']==record['id'])
        self.assertIsNot(current, corrected)
        self.assertEqual(current['results'], record['results'])
        model.history('undo')
        self.assertEqual(next(value for value in model.state['broadcast']['matches'] if value['id']==record['id']), corrected)

    def test_failed_correction_leaves_shared_archives_and_history_unchanged(self):
        self.model.field('honba', 1)
        before = copy.deepcopy((self.model.state, self.model.undo))
        seats = [player['id'] for player in self.model.state['players']]
        record = prepare_result(self.model, seats, [40000, 30000, 25000, 25000])
        with patch.object(self.model.repository, 'save', side_effect=OSError('磁盘失败')):
            with self.assertRaises(OSError):self.model.records.write_manual(record)
        self.assertEqual((self.model.state, self.model.undo), before)

    def test_engine_receives_only_live_table_and_returns_independent_config(self):
        engine = self.model.engine
        original = engine.call
        sent = []
        class Recorder:
            def call(self, args):
                sent.append(json.loads(args[1].toString()))
                return original.call(args)
        before = copy.deepcopy(self.model.state)
        with patch.object(engine, 'call', Recorder()):
            engine.validate(self.model.state)
            updated = engine.apply(self.model.state, dict(type='riichi', player=0))
            engine.preview(self.model.state, dict(type='ron', winner=1, loser=0, han=3, fu=30))
        self.assertTrue(sent)
        self.assertTrue(all('broadcast' not in payload for payload in sent))
        updated['broadcast']['config']['title'] = '新标题'
        self.assertEqual(self.model.state, before)
        self.assertIs(updated['broadcast']['matches'][0], self.model.state['broadcast']['matches'][0])

    def test_cached_archive_validation_still_checks_current_roster_membership(self):
        tournament.validate_broadcast(self.model.state)
        record = self.model.state['broadcast']['matches'][0]
        self.assertTrue(hasattr(record, '_validated_archive_references'))
        candidate = copy.deepcopy(self.model.state)
        removed = candidate['broadcast']['roster'].pop()
        substitute = dict(removed, id='new-entrant')
        candidate['broadcast']['roster'].append(substitute)
        for player in candidate['players']:
            if player['id']==removed['id']:player['id']='new-entrant'
        with self.assertRaisesRegex(ValueError, '半庄选手'):tournament.validate_broadcast(candidate)

    def test_display_config_and_settings_commit_without_consuming_history(self):
        self.model.field('honba', 1)
        self.model.history('undo')
        counts = len(self.model.undo), len(self.model.redo)
        settings = dict(self.model.settings, locked=True)
        self.model.configure_presentation({'sidebar_page':2}, settings=settings)
        self.assertEqual((len(self.model.undo),len(self.model.redo)), counts)
        self.assertTrue(self.model.settings['locked'])
        self.model.history('redo')
        self.assertEqual(self.model.state['broadcast']['config']['sidebar_page'], 2)
        with self.assertRaises(ValueError):self.model.configure_presentation({'return_points':10000})

    def test_win_info_save_is_atomic_and_preserves_table_history(self):
        before = copy.deepcopy((self.model.state, self.model.settings, self.model.last_info, self.model.undo))
        info = dict(yaku=['立直'], name='新详情', limit='')
        with patch.object(self.model.repository, 'save', side_effect=OSError('磁盘失败')):
            with self.assertRaises(OSError):self.model.save_win_info(info)
        self.assertEqual((self.model.state, self.model.settings, self.model.last_info, self.model.undo), before)
        self.model.save_win_info(info)
        self.assertEqual(self.model.last_info, info)
        self.assertEqual(self.model.undo, before[3])
        restored = Model(self.model.path)
        self.assertEqual(restored.last_info, info)


if __name__ == '__main__':unittest.main()
