"""Installation converts in place while retaining the existing live JSON files."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtWidgets import QApplication
from backup_migration import migrate_installation
from backup_package import read_package,json_bytes
from event_store import EventStore
from model import Model
APP=QApplication.instance() or QApplication([])


class BackupMigrationTests(unittest.TestCase):
    def test_update_converts_existing_data_and_exports_without_changing_originals_or_duplicating_packages(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
            root=Path(folder);model=Model(root/'data',asset_root=root);store=EventStore(model)
            model.set_score(0,32100)
            exports=root/'exports';exports.mkdir();old=exports/'赛事.json';old.write_bytes((model.path/'session.json').read_bytes())
            originals={p:p.read_bytes() for p in (root/'data').rglob('*.json')};originals[old]=old.read_bytes()
            first=migrate_installation(root);self.assertFalse(first['errors']);self.assertEqual(len(first['converted']),2)
            self.assertTrue(old.with_suffix('.mjbackup').is_file())
            self.assertEqual(read_package(model.path/'session.mjbackup')[0]['state']['players'][0]['score'],32100)
            before=set(root.rglob('*.mjbackup'));model.set_score(0,33100)
            current=(model.path/'session.json').read_bytes();second=migrate_installation(root)
            self.assertFalse(second['errors']);self.assertEqual(set(root.rglob('*.mjbackup')),before)
            self.assertEqual((model.path/'session.json').read_bytes(),current)
            for p,raw in originals.items():
                if p!=model.path/'session.json':self.assertEqual(p.read_bytes(),raw)
            self.assertEqual(read_package(model.path/'session.mjbackup')[0]['state']['players'][0]['score'],33100)

    def test_missing_media_are_reported_and_never_destroy_existing_data(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
            root=Path(folder);model=Model(root/'data',asset_root=root);EventStore(model)
            old=root/'missing.json';data=model.repository.payload(json.loads(json.dumps(model.state)),{},None,model.event)
            data['state']['players'][0]['photo']='absent.png';old.write_bytes(json_bytes(data));raw=old.read_bytes()
            result=migrate_installation(root)
            self.assertEqual(len(result['errors']),1);self.assertEqual(result['errors'][0]['file'],'missing.json')
            self.assertEqual(old.read_bytes(),raw);self.assertFalse(old.with_suffix('.mjbackup').exists())
            self.assertTrue((root/'data/backup-migration-0.1.4.json').is_file())
