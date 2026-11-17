"""Portable migration, large media, corruption, and the live-listener contract."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage,QColor
from backup_package import convert_legacy,read_package,write_package,json_bytes
from event_store import EventStore
from model import Model
from ranking_reports import report

APP=QApplication.instance() or QApplication([])


class BackupPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='tmp-package-',dir=ROOT/'tests');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.model=Model(self.root/'source/data',asset_root=self.root/'source')
        self.store=EventStore(self.model)

    def legacy(self,embedded=False):
        payload=self.model.repository.payload(copy.deepcopy(self.model.state),copy.deepcopy(self.model.settings),self.model.last_info,self.model.event)
        if embedded:
            from media_store import export_assets
            payload['assets']=export_assets(payload['state'],self.model.path)
        path=self.root/'old.json';path.write_bytes(json_bytes(payload));return path

    def picture(self):
        path=self.root/'portrait.png';image=QImage(40,40,QImage.Format.Format_ARGB32);image.fill(QColor('#29aa88'));image.save(str(path));return path

    def change_zip(self,path,operation):
        with zipfile.ZipFile(path) as z:data={n:z.read(n) for n in z.namelist()}
        operation(data)
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
            for name,raw in data.items():z.writestr(name,raw)

    def test_converter_writes_sibling_preserves_original_and_new_app_rejects_legacy(self):
        path=self.legacy();raw=path.read_bytes()
        with self.assertRaisesRegex(ValueError,r'\.mjbackup'):self.model.import_data(path)
        target=convert_legacy(path)
        self.assertEqual(target,path.with_suffix('.mjbackup'));self.assertEqual(path.read_bytes(),raw)
        state,_,_,_,_=self.model.read_data(target);self.assertEqual(state,self.model.state)
        first=target.read_bytes()
        data=json.loads(path.read_text(encoding='utf-8'));data['state']['title']='更改后的标题';path.write_bytes(json_bytes(data))
        self.assertEqual(convert_legacy(path),target)
        self.assertEqual(read_package(target)[0]['state']['title'],'更改后的标题')
        self.assertEqual(len(list(path.parent.glob('old*.mjbackup'))),1)

    def test_final_scores_hands_penalties_and_rankings_survive_new_computer_import(self):
        self.model.field('roundIndex',7);self.model.field('dealer',3)
        self.model.riichi(0);self.model.settle(dict(type='ron',winner=0,loser=1,han=3,fu=30),None)
        self.model.matches.confirm_finished()
        match=self.model.state['broadcast']['matches'][0]
        self.model.records.save_penalty(match['id'],scope='player',target=self.model.state['players'][0]['id'],points=5,time='2026-11-17T00:00:00+00:00',reason='迟到')
        from event_results import prepare_result,record_result
        seats=[p['id'] for p in self.model.state['players']]
        record_result(self.store,prepare_result(self.model,seats,[20000,40000,30000,30000],match_id=match['id']))
        backup=self.root/'event.mjbackup';self.model.export_data(backup)
        data,assets,kind=read_package(backup)
        self.assertEqual(kind,'event');self.assertEqual(data['state'],self.model.state)
        with zipfile.ZipFile(backup) as z:
            self.assertIn('matches/000001.json',z.namelist());self.assertNotIn('matches',json.loads(z.read('live.json'))['broadcast'])
        other=Model(self.root/'other/data',asset_root=self.root/'other');store=EventStore(other);store.import_new(backup)
        self.assertEqual(other.state,self.model.state)
        self.assertEqual(report(other.state,other.event,'individual')['rows'],report(self.model.state,self.model.event,'individual')['rows'])

    def test_music_over_old_total_limit_and_embedded_legacy_images_are_self_contained(self):
        music=self.root/'large.wav'
        with wave.open(str(music),'wb') as f:
            f.setparams((2,2,48000,0,'NONE','not compressed'))
            for _ in range(56):f.writeframes(b'\0'*(1024*1024))
        self.model.configure_postmatch({'postmatch_music_path':str(music)})
        self.model.update(lambda s:s['players'][0].update(photo=str(self.picture())))
        backup=self.root/'large.mjbackup';self.model.export_data(backup)
        raw=json.loads((self.model.path/'session.json').read_text(encoding='utf-8'))
        self.assertEqual(raw['version'],6)
        data,assets,_=read_package(backup);self.assertEqual(len(assets),2)
        key=data['state']['broadcast']['config']['postmatch_music_path']
        self.assertEqual(hashlib.sha256(assets[key]).hexdigest(),Path(key).stem)
        self.assertGreater(len(assets[key]),50_000_000)
        # Remove every original source and still import a complete independent event.
        import shutil
        shutil.rmtree(self.root/'source');music.unlink();(self.root/'portrait.png').unlink()
        other=Model(self.root/'other/data',asset_root=self.root/'other');EventStore(other).import_new(backup)
        self.assertTrue((self.root/'other'/key).is_file())
        self.assertTrue(list((other.path/'imports').glob('*.mjbackup')))

    def test_embedded_json_conversion_does_not_need_original_asset_paths(self):
        self.model.update(lambda s:s['players'][0].update(photo=str(self.picture())))
        path=self.legacy(embedded=True)
        import shutil
        shutil.rmtree(self.root/'source');(self.root/'portrait.png').unlink()
        data,assets,_=read_package(convert_legacy(path));self.assertEqual(len(assets),1)
        self.assertTrue(data['state']['players'][0]['photo'] in assets)

    def test_local_json_conversion_finds_shared_assets_above_event_directory(self):
        self.model.update(lambda s:s['players'][0].update(photo=str(self.picture())))
        source=self.model.path/'session.json';original=source.read_bytes()
        package=convert_legacy(source);_,assets,_=read_package(package)
        self.assertEqual(len(assets),1);self.assertEqual(source.read_bytes(),original)

    def test_corruption_missing_file_traversal_and_future_version_reject_before_state_changes(self):
        backup=self.root/'bad.mjbackup';self.model.export_data(backup);original=backup.read_bytes()
        snapshot=copy.deepcopy((self.model.state,self.model.undo,self.model.redo));saved=(self.model.path/'session.json').read_bytes()
        def bad_version(d):
            m=json.loads(d['manifest.json']);m['format_version']=100;d['manifest.json']=json_bytes(m)
        for mutation in (lambda d:d.pop('live.json'),lambda d:d.update({'../escape.txt':b'bad'}),
                         lambda d:d.update({'live.json':b'{}'}),bad_version):
            backup.write_bytes(original);self.change_zip(backup,mutation)
            with self.assertRaises(ValueError):self.model.import_data(backup)
            self.assertEqual((self.model.state,self.model.undo,self.model.redo),snapshot)
            self.assertEqual((self.model.path/'session.json').read_bytes(),saved)

    def test_failed_export_preserves_previous_package_and_source_json(self):
        target=self.root/'keep.mjbackup';self.model.export_data(target);original=target.read_bytes()
        saved=(self.model.path/'session.json').read_bytes()
        with patch('backup_package.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.model.export_data(target)
        self.assertEqual(target.read_bytes(),original);self.assertEqual((self.model.path/'session.json').read_bytes(),saved)
        self.assertFalse(list(self.root.glob('.backup-*.tmp')))

    def test_missing_legacy_media_fails_without_creating_a_partial_package(self):
        data=self.model.repository.payload(copy.deepcopy(self.model.state),{},None,self.model.event)
        data['state']['players'][0]['photo']='missing.png';source=self.root/'missing.json';source.write_bytes(json_bytes(data))
        with self.assertRaisesRegex(ValueError,'素材缺失'):convert_legacy(source)
        self.assertFalse(source.with_suffix('.mjbackup').exists());self.assertTrue(source.is_file())

    def test_live_saved_document_keeps_the_existing_point_listener_contract(self):
        self.model.setting('riichi_payment_mode','deferred');self.model.riichi(0)
        source=self.model.path/'session.json';first=json.loads(source.read_text(encoding='utf-8'))
        self.assertEqual(first['version'],6);self.assertEqual(first['settings']['riichi_payment_mode'],'deferred')
        self.assertTrue(first['state']['players'][0]['riichi'])
        self.model.set_score(1,31000);second=json.loads(source.read_text(encoding='utf-8'))
        self.assertEqual(second['state']['players'][1]['score'],31000)
        self.assertEqual([p['id'] for p in first['state']['players']],[p['id'] for p in second['state']['players']])


if __name__=='__main__':unittest.main()
