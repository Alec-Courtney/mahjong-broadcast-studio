"""Complete compatible JSON, cached archives and simple paired-save failures."""
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
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from file_transaction import atomic_writes
from media_store import import_media_file, manage_photos
from model import Model
from event_store import EventStore
from session_repository import atomic_bytes, encode_session, SessionRepository
from state_snapshots import freeze, FrozenDict

APP = QApplication.instance() or QApplication([])


class PersistenceScalingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tmp-persist-scaling-', dir=ROOT/'tests')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_json_encoding_matches_the_existing_v6_payload(self):
        model = Model(self.root)
        model.save()
        actual = json.loads((model.path/'session.json').read_text(encoding='utf-8'))
        self.assertEqual(actual, SessionRepository.payload(model.state, model.settings, model.last_info, model.event))
        self.assertEqual(json.loads(encode_session({'broadcast': {}}, {}, None)),
                         dict(version=6, state={'broadcast': {'matches': []}}, settings={}, last_info=None))

    def test_sealed_archive_reuses_encoded_bytes_without_reencoding_its_tree(self):
        archive = freeze(dict(id='saved', hands=[dict(name='选手', score=30000)]))
        state = dict(players=[], broadcast=dict(matches=[archive], roster=[]))
        first = encode_session(state, {}, None)
        cached = archive._session_json_bytes
        import session_repository
        encoder = session_repository._json_bytes
        def no_archive_reencode(value):
            if value is archive:raise AssertionError('A stable archive was encoded again')
            return encoder(value)
        with patch('session_repository._json_bytes', side_effect=no_archive_reencode):
            second = encode_session(state, {}, None)
        self.assertEqual(first, second)
        self.assertIs(archive._session_json_bytes, cached)

    def test_replacement_and_mutable_archives_never_reuse_stale_encoding(self):
        archive = freeze(dict(id='saved', points=12))
        state = dict(broadcast=dict(matches=[archive]))
        encode_session(state, {}, None)
        state['broadcast']['matches'][0] = freeze(dict(id='saved', points=30))
        self.assertEqual(json.loads(encode_session(state, {}, None))['state']['broadcast']['matches'][0]['points'], 30)
        mutable = dict(id='new', points=3)
        state['broadcast']['matches'] = [mutable]
        encode_session(state, {}, None)
        mutable['points'] = 9
        self.assertEqual(json.loads(encode_session(state, {}, None))['state']['broadcast']['matches'][0]['points'], 9)

    def test_local_limit_rejects_a_save_before_changing_memory_or_existing_file(self):
        model = Model(self.root)
        model.save()
        before = copy.deepcopy((model.state, model.settings, model.undo, model.redo))
        saved = (self.root/'session.json').read_bytes()
        with patch('session_repository.MAX_LOCAL_SESSION_BYTES', 10):
            with self.assertRaisesRegex(ValueError, '256MiB'):model.set_score(0, 36000)
        self.assertEqual((model.state, model.settings, model.undo, model.redo), before)
        self.assertEqual((self.root/'session.json').read_bytes(), saved)

    def test_local_reads_have_their_own_limit_without_relaxing_external_imports(self):
        model = Model(self.root)
        model.save()
        source = self.root/'session.json'
        with patch('session_repository.MAX_SESSION_BYTES', 10):
            with self.assertRaisesRegex(ValueError, '50MB'):model.repository.read(source)
            loaded = model.repository.read(source, local=True)
        self.assertEqual(loaded[0], model.state)

    def test_paired_save_buffers_files_until_the_group_is_ready(self):
        session, preferences = self.root/'session.json', self.root/'application-preferences.json'
        session.write_bytes(b'old session'); preferences.write_bytes(b'old preferences')
        with atomic_writes(self.root):
            atomic_bytes(session, b'new session')
            atomic_bytes(preferences, b'new preferences')
            self.assertEqual(session.read_bytes(), b'old session')
            self.assertEqual(preferences.read_bytes(), b'old preferences')
        self.assertEqual(session.read_bytes(), b'new session')
        self.assertEqual(preferences.read_bytes(), b'new preferences')
        self.assertEqual({p.name for p in self.root.iterdir()}, {'session.json', 'application-preferences.json'})

    def test_paired_write_failure_restores_completed_files_and_leaves_no_logs(self):
        session, preferences = self.root/'session.json', self.root/'application-preferences.json'
        session.write_bytes(b'old session'); preferences.write_bytes(b'old preferences')
        import file_transaction
        writer = file_transaction._write
        def fail_preferences(path, content):
            if path == preferences:raise OSError('preferences unavailable')
            writer(path, content)
        with patch('file_transaction._write', side_effect=fail_preferences):
            with self.assertRaisesRegex(OSError, 'preferences unavailable'):
                with atomic_writes(self.root):
                    atomic_bytes(session, b'new session')
                    atomic_bytes(preferences, b'new preferences')
        self.assertEqual(session.read_bytes(), b'old session')
        self.assertEqual(preferences.read_bytes(), b'old preferences')
        self.assertEqual({p.name for p in self.root.iterdir()}, {'session.json', 'application-preferences.json'})

    def test_preparation_failure_does_not_create_new_files(self):
        with self.assertRaisesRegex(OSError, 'validation failure'):
            with atomic_writes(self.root):
                atomic_bytes(self.root/'session.json', b'prepared')
                raise OSError('validation failure')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_model_preferences_commit_failure_restores_session_preferences_and_observers(self):
        model = Model(self.root)
        model.save()
        session, preferences = self.root/'session.json', model.preferences.path
        saved, saved_preferences = session.read_bytes(), preferences.read_bytes()
        before = copy.deepcopy((model.state, model.settings, model.undo))
        events = []
        model.display_changed.connect(lambda:events.append(True))
        import file_transaction
        writer = file_transaction._write
        def fail_preferences(path, content):
            if path == preferences:raise OSError('preferences unavailable')
            writer(path, content)
        with patch('file_transaction._write', side_effect=fail_preferences):
            with self.assertRaises(OSError):model.setting('console_theme', 'light')
        self.assertEqual((model.state, model.settings, model.undo), before)
        self.assertEqual(session.read_bytes(), saved)
        self.assertEqual(preferences.read_bytes(), saved_preferences)
        self.assertEqual(events, [])
        model.set_score(0, 35000)  # Rollback must restore the repository revision too.
        self.assertEqual(Model(self.root).state['players'][0]['score'], 35000)

    def test_first_library_and_new_event_commit_failures_remove_only_unpublished_snapshots(self):
        model = Model(self.root)
        before = copy.deepcopy(model.state)
        import file_transaction
        writer = file_transaction._write
        def fail_index(path, content):
            if path.name == 'library.json':raise OSError('index unavailable')
            writer(path, content)
        with patch('file_transaction._write', side_effect=fail_index):
            with self.assertRaises(OSError):EventStore(model)
        self.assertEqual(model.state, before)
        self.assertIsNone(model.event)
        self.assertFalse(model.preferences.path.exists())
        self.assertFalse((self.root/'library.json').exists())
        self.assertEqual(list((self.root/'events').glob('*/session.json')), [])
        store = EventStore(model)
        model.save()
        before = copy.deepcopy((model.path, model.state, model.settings, model.event))
        saved_index = store.index_path.read_bytes()
        with patch('file_transaction._write', side_effect=fail_index):
            with self.assertRaises(OSError):store.create('不应登记的新赛事', 'individual_league')
        self.assertEqual((model.path, model.state, model.settings, model.event), before)
        self.assertEqual(store.index_path.read_bytes(), saved_index)
        self.assertEqual(len(list((self.root/'events').glob('*/session.json'))), 1)

    def test_paired_target_must_remain_inside_its_workspace(self):
        with self.assertRaisesRegex(ValueError, '同一工作区'):
            with atomic_writes(self.root):atomic_bytes(self.root.parent/'session.json', b'invalid')

    def test_stable_archive_media_scan_is_cached_but_strict_checks_get_a_writable_copy(self):
        source = self.root/'source.png'
        picture = QImage(12, 16, QImage.Format.Format_ARGB32)
        picture.fill(QColor('#227799')); picture.save(str(source))
        reference = import_media_file(self.root, source, 'photo')
        archive = freeze(dict(id='saved', players=[dict(name='选手', photo=reference)]))
        state = dict(broadcast=dict(matches=[archive]))
        manage_photos(state, self.root)
        self.assertEqual(archive._managed_asset_root, str(self.root.resolve()))
        with patch('media_store.media_records', side_effect=AssertionError('A stable archive was scanned again')):
            manage_photos(state, self.root)
        manage_photos(state, self.root, strict=True)
        self.assertNotIsInstance(state['broadcast']['matches'][0], FrozenDict)
        self.assertEqual(state['broadcast']['matches'][0]['players'][0]['photo'], reference)
        self.assertEqual(archive['players'][0]['photo'], reference)

    def test_uncached_legacy_media_in_a_sealed_archive_is_normalized_without_mutating_history(self):
        source = self.root/'old-portrait.png'
        picture = QImage(12, 16, QImage.Format.Format_ARGB32)
        picture.fill(QColor('#227799')); picture.save(str(source))
        archive = freeze(dict(id='saved', players=[dict(name='选手', photo=str(source))]))
        state = dict(broadcast=dict(matches=[archive]))
        manage_photos(state, self.root)
        current = state['broadcast']['matches'][0]
        self.assertNotIsInstance(current, FrozenDict)
        self.assertTrue(current['players'][0]['photo'].startswith('assets/imported/'))
        self.assertEqual(archive['players'][0]['photo'], str(source))


if __name__ == '__main__':unittest.main()
