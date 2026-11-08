"""Project relocation, portable audio/images, retained originals and atomic import."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtGui import QColor,QImage
from PySide6.QtWidgets import QApplication
from corner_header import corner_logo
from event_store import EventStore,resolve_active_directory
from identity_media import load_identity_image
from match_opening import prepare_opening
from media_store import archive_import_file,import_media_file,resolve_media_path
from model import Model
from postmatch_player import PostMatchPlayback
from session_repository import atomic_json

APP=QApplication.instance() or QApplication([])


class ManagedMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='managed-media-',dir=ROOT/'tests')
        self.root=Path(self.tmp.name);self.project=self.root/'project'
        self.sources=self.root/'external';self.sources.mkdir()
        self.model=Model(self.project/'data',asset_root=self.project)
        self.portrait=self.image('portrait.png','#9258be',60,90)
        self.logo=self.image('own-logo.png','#2b9fa1',120,60)
        self.background=self.image('background.png','#334456',160,90)
        self.title=self.image('opening-title.png','#4e7524',160,90)
        self.music=self.sources/'original-music.wav'
        with wave.open(str(self.music),'wb') as stream:
            stream.setnchannels(2);stream.setsampwidth(2);stream.setframerate(48000)
            stream.writeframes(b'\0\0\0\0'*4800)

    def tearDown(self):APP.processEvents();self.tmp.cleanup()

    def image(self,name,colour,width,height):
        path=self.sources/name;image=QImage(width,height,QImage.Format.Format_ARGB32)
        image.fill(QColor(colour));self.assertTrue(image.save(str(path)));return path

    def move_directory(self,source,destination):
        """Actually rename; tolerate only bounded Windows sharing contention.

        Source WAVs use a context manager and the import writer closes each
        byte write before replacement. No reader for these files stays open.
        A scanner can still deny a just-written directory rename briefly.
        """
        source=Path(source).resolve();destination=Path(destination).resolve()
        self.assertTrue(source.is_relative_to(self.root.resolve()))
        self.assertTrue(destination.is_relative_to(self.root.resolve()))
        self.assertFalse(destination.exists())
        for delay in (.01,.025,.05,.1,.2,.4,None):
            try:
                source.rename(destination);break
            except PermissionError as error:
                if sys.platform!='win32' or getattr(error,'winerror',None) not in (5,32,33) or delay is None:raise
                APP.processEvents();time.sleep(delay)
        self.assertFalse(source.exists());self.assertTrue(destination.is_dir())

    def import_all(self):
        self.model.update(lambda state:state['players'][0].update(photo=str(self.portrait),team_logo=str(self.logo),team='真实测试队'))
        self.model.configure_corner_header({'corner_logo':str(self.logo)})
        self.model.configure_postmatch({'postmatch_music_path':str(self.music),'postmatch_background_path':str(self.background)})
        self.model.configure_opening({'opening_title':str(self.title)})
        config=self.model.state['broadcast']['config']
        return dict(photo=self.model.state['players'][0]['photo'],team_logo=self.model.state['players'][0]['team_logo'],
                    corner_logo=config['corner_logo'],postmatch_music_path=config['postmatch_music_path'],
                    postmatch_background_path=config['postmatch_background_path'],opening_title=config['opening_title'])

    def test_all_selected_media_use_one_fixed_project_library_and_survive_source_removal(self):
        store=EventStore(self.model)
        refs=self.import_all()
        self.assertEqual(refs['team_logo'],refs['corner_logo'])
        for field,reference in refs.items():
            with self.subTest(field=field):
                self.assertTrue(reference.startswith('assets/imported/'));self.assertFalse(Path(reference).is_absolute())
                self.assertTrue((self.project/reference).is_file())
        for source in self.sources.iterdir():source.unlink()
        restarted=Model(resolve_active_directory(store.root),asset_root=self.project)
        self.assertEqual(restarted.event['id'],self.model.event['id'])
        player=restarted.state['players'][0]
        self.assertEqual((player['photo'],player['team_logo']),(refs['photo'],refs['team_logo']))
        config=restarted.state['broadcast']['config']
        for key in ('corner_logo','opening_title','postmatch_background_path','postmatch_music_path'):
            self.assertEqual(config[key],refs[key])
        for key in ('photo','team_logo','postmatch_background_path'):
            with self.subTest(image=key):
                self.assertIsNotNone(load_identity_image(restarted,refs[key]))
        self.assertIsNotNone(corner_logo(restarted,config['corner_logo']))
        opening=prepare_opening(restarted,require_distinct_teams=False)
        self.assertEqual(opening.title_reference,refs['opening_title'])
        self.assertTrue(opening.title_image.png)
        playback=PostMatchPlayback(restarted)
        self.assertEqual(playback._resolve_path(refs['postmatch_music_path']),self.project/refs['postmatch_music_path'])
        playback.close()
        self.assertEqual(import_media_file(self.model.path,self.project/refs['corner_logo'],'corner_logo'),refs['corner_logo'])

    def test_whole_project_move_resolves_all_references_without_old_absolute_paths(self):
        refs=self.import_all();before=copy.deepcopy(self.model.state)
        for source in self.sources.iterdir():source.unlink()
        moved=self.root/'project-moved';self.move_directory(self.project,moved)
        restarted=Model(moved/'data',asset_root=moved)
        self.assertEqual(restarted.state,before)
        for reference in refs.values():self.assertTrue(resolve_media_path(restarted.path,reference).is_file())
        self.assertIsNotNone(corner_logo(restarted,refs['corner_logo']))
        self.assertIsNotNone(load_identity_image(restarted,refs['photo']))

    def test_event_copy_reuses_project_assets_instead_of_copying_per_event(self):
        refs=self.import_all();store=EventStore(self.model)
        before={path.relative_to(self.project) for path in (self.project/'assets'/'imported').rglob('*') if path.is_file()}
        store.create('复制设置的赛事','team_league',copy_config=True)
        self.assertEqual(self.model.state['broadcast']['config']['postmatch_music_path'],refs['postmatch_music_path'])
        after={path.relative_to(self.project) for path in (self.project/'assets'/'imported').rglob('*') if path.is_file()}
        self.assertEqual(before,after);self.assertFalse((self.model.path/'media').exists())
        self.assertTrue(resolve_media_path(self.model.path,refs['photo']).is_file())

    def test_generated_demo_scene_and_launcher_share_project_asset_root_after_move(self):
        from demo_suite import save_scene
        refs=self.import_all();EventStore(self.model)
        demo=self.project/'demo-suite'
        entry=save_scene(self.model,demo,'isolated-review',1,'独立素材测试',[],asset_root=self.project)
        atomic_json(demo/'library'/'library.json',dict(version=1,active_id=entry['id'],events=[entry['id']]))
        live=Model(demo/'library',asset_root=self.project);EventStore(live)
        self.assertTrue(resolve_media_path(live.path,refs['photo']).is_file())
        self.assertFalse((live.path/'assets').exists())
        moved=self.root/'moved-demo-project';self.move_directory(self.project,moved)
        restarted=Model(moved/'demo-suite'/'library',asset_root=moved);EventStore(restarted)
        self.assertIsNotNone(load_identity_image(restarted,refs['photo']))
        self.assertIsNotNone(corner_logo(restarted,refs['corner_logo']))
        self.assertTrue(resolve_media_path(restarted.path,refs['postmatch_music_path']).is_file())

    def test_json_transfer_embeds_music_background_logo_and_archives_original_json(self):
        refs=self.import_all();target=self.root/'transfer.json';self.model.export_data(target)
        original=target.read_bytes();payload=json.loads(original)
        self.assertEqual(len(payload['assets']),5)
        self.assertTrue(set(refs.values())<=payload['assets'].keys())
        self.move_directory(self.project,self.root/'source-project-offline')
        for source in self.sources.iterdir():source.unlink()
        destination=self.root/'destination';restored=Model(destination/'data',asset_root=destination)
        restored.import_data(target)
        for reference in refs.values():self.assertTrue(resolve_media_path(restored.path,reference).is_file())
        retained=list((destination/'assets'/'imported'/'documents').glob('*.json'))
        self.assertEqual(len(retained),1);self.assertEqual(retained[0].read_bytes(),original)
        store=EventStore(restored);store.import_new(target)
        self.assertTrue(resolve_media_path(restored.path,refs['postmatch_music_path']).is_file())
        self.assertEqual(len(list((destination/'assets'/'imported'/'documents').glob('*.json'))),1)
        target.unlink();self.assertIsNotNone(corner_logo(restored,refs['corner_logo']))

    def test_legacy_relative_media_are_resolved_before_original_json_is_archived(self):
        state=copy.deepcopy(self.model.state)
        state['players'][0].update(photo=self.portrait.name,team_logo=self.logo.name)
        state['broadcast']['config'].update(corner_logo=self.logo.name,postmatch_background_path=self.background.name,
                                            postmatch_music_path=self.music.name)
        source=self.sources/'legacy.json';atomic_json(source,self.model.repository.payload(state,{},None))
        original=source.read_bytes();self.model.import_data(source)
        self.move_directory(self.sources,self.root/'external-offline')
        config=self.model.state['broadcast']['config']
        self.assertIsNotNone(corner_logo(self.model,config['corner_logo']))
        self.assertTrue(resolve_media_path(self.model.path,config['postmatch_music_path']).is_file())
        retained=list((self.project/'assets'/'imported'/'documents').glob('*.json'))
        self.assertEqual(retained[0].read_bytes(),original)

    def test_original_photo_is_retained_before_any_cropping(self):
        original=self.portrait.read_bytes();reference=archive_import_file(self.model.path,self.portrait,category='images')
        self.portrait.unlink()
        self.assertTrue(reference.startswith('assets/imported/images/originals/'))
        self.assertEqual(resolve_media_path(self.model.path,reference).read_bytes(),original)

    def test_postmatch_media_preferences_stay_managed_across_undo_and_redo(self):
        self.model.set_score(0,26000);self.model.history('undo')
        lengths=(len(self.model.undo),len(self.model.redo))
        self.model.configure_postmatch({'postmatch_music_path':str(self.music),'postmatch_background_path':str(self.background)})
        self.music.unlink();self.background.unlink();self.model.history('redo')
        self.assertEqual((len(self.model.undo),len(self.model.redo)),(lengths[0]+1,lengths[1]-1))
        config=self.model.state['broadcast']['config']
        for key in ('postmatch_music_path','postmatch_background_path'):
            self.assertTrue(config[key].startswith('assets/imported/'))
            self.assertTrue(resolve_media_path(self.model.path,config[key]).is_file())

    def test_local_music_copy_is_independent_of_json_transfer_size_limit(self):
        with patch('media_store.MAX_TOTAL_BYTES',1024):
            self.model.configure_postmatch({'postmatch_music_path':str(self.music)})
            reference=self.model.state['broadcast']['config']['postmatch_music_path']
            self.assertTrue(resolve_media_path(self.model.path,reference).is_file())
            with self.assertRaisesRegex(ValueError,'素材总大小'):
                self.model.export_data(self.root/'too-large.json')
        self.assertFalse((self.root/'too-large.json').exists())

    def test_rejected_or_failed_import_does_not_replace_state_or_history(self):
        self.import_all();target=self.root/'transfer.json';self.model.export_data(target)
        before=copy.deepcopy((self.model.state,self.model.settings,self.model.undo,self.model.redo,self.model.last_transition))
        with patch.object(self.model.repository,'save',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):self.model.import_data(target)
        self.assertEqual((self.model.state,self.model.settings,self.model.undo,self.model.redo,self.model.last_transition),before)
        payload=json.loads(target.read_text(encoding='utf-8'))
        music_key=next(key for key in payload['assets'] if '/music/' in key)
        payload['assets'][music_key]['sha256']='bad';target.write_text(json.dumps(payload),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'校验'):self.model.import_data(target)
        self.assertEqual((self.model.state,self.model.settings,self.model.undo,self.model.redo,self.model.last_transition),before)


if __name__=='__main__':unittest.main()
