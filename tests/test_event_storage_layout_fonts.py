"""Behavioral checks for linked layouts, typography and portable event data."""
import copy,json,os,sys,tempfile,unittest
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QPoint,Qt
from PySide6.QtGui import QImage,QFont,QColor,QPainter
from PySide6.QtWidgets import QApplication
from model import Model
from media_store import resolve_media_path
from overlays import OverlayManager
from console import Console,LayoutDialog
from demo_console import DemoSession
from demo_data import load_into
from event_store import EventStore
from ranking_reports import individual_rows
from ranking_schema import TECH
from typography import overlay_scope,overlay_font,apply_desk
import tournament
APP=QApplication.instance() or QApplication([])


class EventStorageTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.root=Path(self.tmp.name);self.model=Model(self.root/'live')
 def tearDown(self):self.tmp.cleanup()
 def photo(self,name='source.png',color='#487f9a'):
  path=self.root/name;image=QImage(40,60,QImage.Format.Format_ARGB32);image.fill(QColor(color));image.save(str(path));return path
 def assign(self,path):
  self.model.update(lambda state:state['players'][0].update(photo=str(path),name='测试选手'))
 def test_photos_and_identity_survive_export_relocation_and_restart(self):
  source=self.photo();self.assign(source);reference=self.model.state['players'][0]['photo']
  self.assertTrue(reference.startswith('assets/imported/images/'));self.assertTrue(resolve_media_path(self.model.path,reference).is_file())
  self.model.settle(dict(type='ron',winner=1,loser=0,han=3,fu=30),None);self.model.commit_match()
  before=copy.deepcopy(self.model.state);target=self.root/'event.json';self.model.export_data(target)
  data=json.loads(target.read_text(encoding='utf-8'));self.assertEqual(len(data['assets']),1)
  source.unlink();resolve_media_path(self.model.path,reference).unlink()
  relocated=Model(self.root/'other');relocated.import_data(target)
  self.assertEqual(relocated.state,before);self.assertFalse(QImage(str(relocated.path/reference)).isNull())
  restarted=Model(relocated.path);self.assertEqual(restarted.state,before)
 def test_corrupt_asset_rejected_without_changing_session(self):
  self.assign(self.photo());target=self.root/'event.json';self.model.export_data(target)
  payload=json.loads(target.read_text(encoding='utf-8'));next(iter(payload['assets'].values()))['sha256']='bad'
  target.write_text(json.dumps(payload),encoding='utf-8');before=copy.deepcopy(self.model.state)
  with self.assertRaisesRegex(ValueError,'校验'):self.model.import_data(target)
  self.assertEqual(self.model.state,before)
 def test_old_records_keep_profile_snapshot_after_live_rename_and_rephoto(self):
  self.assign(self.photo());photo=self.model.state['players'][0]['photo']
  self.model.settle(dict(type='ron',winner=0,loser=1,han=3,fu=30),None);self.model.commit_match()
  from state_snapshots import mutable_copy
  legacy=mutable_copy(self.model.state['broadcast']['matches'][0]);legacy.pop('profiles',None)
  self.model.state['broadcast']['matches'][0]=legacy
  self.model.update(lambda state:state['players'][0].update(name='新名字',photo=str(self.photo('new.png','#bb5544'))))
  hand=self.model.state['broadcast']['hands'][0];archive=self.model.state['broadcast']['matches'][0]
  self.assertEqual(hand['players'][0]['name'],'测试选手');self.assertEqual(hand['players'][0]['photo'],photo)
  result=next(row for row in archive['results'] if row['id']==self.model.state['players'][0]['id'])
  self.assertEqual(result['name'],'测试选手');self.assertEqual(result['photo'],photo)
 def test_missing_photo_is_reported_at_export(self):
  self.model.update(lambda state:state['players'][0].update(photo='missing.png'))
  with self.assertRaisesRegex(ValueError,'照片文件缺失'):self.model.export_data(self.root/'bad.json')
  self.assertFalse((self.root/'bad.json').exists())
 def test_complete_leagues_have_photos_and_recorded_data_for_every_player(self):
  for scenario,players,matches in [('team_complete',40,50),('individual_complete',16,20)]:
   load_into(self.model,scenario);state=self.model.state;broadcast=state['broadcast']
   self.assertEqual(len(broadcast['roster']),players);self.assertEqual(len(broadcast['matches']),matches)
   self.assertEqual(len(broadcast['hands']),4);self.assertEqual(state['phase'],'playing')
   self.assertTrue(all(row['hands']>0 for row in tournament.statistics(state)))
   self.assertTrue(all(resolve_media_path(self.model.path,p['photo']).is_file() for p in broadcast['roster']))
   self.assertEqual(len({p['photo'] for p in broadcast['roster']}),players)
   self.assertTrue(all(m['committed'] and m['completed'] and len(m['hands'])>=8 and len(m['profiles'])==4
                       for m in broadcast['matches']))
   self.assertGreater(len({len(m['hands']) for m in broadcast['matches']}),1)
   for row in individual_rows(state,{}):
    self.assertEqual((row['played'],row['known_games'],row['technical_matches']),(5,5,5))
    self.assertTrue(all(row[field] is not None for field in TECH),row['name'])
   self.model.engine.validate(state);tournament.validate_broadcast(state)
 def test_demo_restore_retains_real_session_assets(self):
  self.assign(self.photo());state=copy.deepcopy(self.model.state);demo=DemoSession(self.model)
  self.model.export_data(demo.backup_path)
  load_into(self.model,'individual_complete')
  self.model.save_preferences(dict(self.model.settings,demo_active=True,demo_id='individual_complete'))
  demo.restore()
  self.assertEqual(self.model.state,state)
  self.assertNotIn('demo_active',self.model.settings)
  self.assertNotIn('demo_id',self.model.settings)
  self.assertEqual(Model(self.model.path).state,state)
  self.assertTrue(resolve_media_path(self.model.path,state['players'][0]['photo']).is_file())

 def test_fixture_loading_cannot_replace_live_workspaces(self):
  EventStore(self.model)
  standalone=Model(self.root/'standalone')
  standalone.ui_only=True
  for model in (self.model,standalone):
   before=copy.deepcopy(model.state)
   with self.assertRaisesRegex(ValueError,'独立演示赛事'):load_into(model,'team_complete')
   self.assertEqual(model.state,before)


class LayoutTypographyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.model=Model(self.tmp.name);self.manager=OverlayManager(self.model);self.ui=None
 def tearDown(self):
  if self.ui:
   self.ui.shortcuts.dispose();self.ui.language_controller.dispose();APP.removeEventFilter(self.ui);self.ui.hide();self.ui.broadcast_dialog.hide();self.ui.deleteLater()
  self.manager.close();APP.processEvents();self.tmp.cleanup()
 def test_group_geometry_updates_replacement_cards_and_survives_restart(self):
  identities={key:int(window.winId()) for key,window in self.manager.windows.items()}
  self.manager.set_player_group(80,140,240,18);self.manager.set_bottom_mode('team')
  snapshot=self.manager.player_group_snapshot();self.manager.translate_player_group(snapshot,QPoint(22,-10));self.manager.save_geometry()
  self.assertEqual(self.manager.windows['standings'].geometry(),self.manager.bottom_bounds().toAlignedRect())
  self.manager.scale_player_group(1.25)
  self.assertEqual([self.manager.windows[f'player{i}'].width() for i in range(4)],[300]*4)
  self.assertEqual(identities,{key:int(window.winId()) for key,window in self.manager.windows.items()})
  geometry=copy.deepcopy(self.model.settings['windows']);restored=Model(self.tmp.name)
  self.assertEqual(restored.settings['windows'],geometry)
 def test_independent_font_choices_apply_to_existing_console_and_broadcast(self):
  self.ui=Console(self.model,self.manager);dialog=LayoutDialog(self.ui)
  dialog.console_font_size.setValue(15);dialog.settings_font_size.setValue(17);dialog.overlay_font_scale.setValue(125);dialog.apply()
  self.assertIn('font-size:15px',self.ui.styleSheet())
  self.assertIn('font-size:17px',self.ui.broadcast_dialog.styleSheet())
  original=self.ui.styleSheet();self.model.display_changed.emit();self.assertEqual(self.ui.styleSheet(),original)
  self.assertEqual(Model(self.tmp.name).settings['overlay_font_scale'],1.25)
  with overlay_scope(self.model.settings):self.assertEqual(overlay_font(40).pixelSize(),50)
  with overlay_scope({}):self.assertEqual(overlay_font(40).pixelSize(),40)
 def test_overlay_font_change_invalidates_recorded_picture_cache(self):
  self.manager._extension_picture('statistics');self.assertTrue(self.manager.extension_cache)
  self.model.setting('overlay_font_scale',1.2);self.assertFalse(self.manager.extension_cache)
 def test_drag_and_control_wheel_respect_link_and_lock(self):
  from PySide6.QtCore import QEvent,QPointF
  from PySide6.QtGui import QMouseEvent,QWheelEvent
  self.manager.set_player_group(40,120,240,18);self.model.setting('player_group_linked',True)
  window=self.manager.windows['player2'];before=self.manager.player_group_snapshot()
  press=QMouseEvent(QEvent.Type.MouseButtonPress,QPointF(10,10),QPointF(100,100),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
  move=QMouseEvent(QEvent.Type.MouseMove,QPointF(50,35),QPointF(140,125),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
  release=QMouseEvent(QEvent.Type.MouseButtonRelease,QPointF(50,35),QPointF(140,125),Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier)
  window.mousePressEvent(press);window.mouseMoveEvent(move);window.mouseReleaseEvent(release)
  for key,geometry in before.items():self.assertEqual(self.manager.windows[key].pos(),QPoint(geometry[0]+40,geometry[1]+25))
  wheel=QWheelEvent(QPointF(10,10),QPointF(140,125),QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.ControlModifier,Qt.ScrollPhase.NoScrollPhase,False)
  window.wheelEvent(wheel)
  self.assertEqual([self.manager.windows[f'player{i}'].width() for i in range(4)],[254]*4)
  self.model.setting('player_group_linked',False);before=self.manager.player_group_snapshot();window.wheelEvent(wheel)
  self.assertEqual(self.manager.windows['player0'].width(),before['player0'][2]);self.assertGreater(window.width(),before['player2'][2])
  self.model.setting('locked',True);before=self.manager.player_group_snapshot();window.wheelEvent(wheel);window.mousePressEvent(press);window.mouseMoveEvent(move)
  self.assertEqual(self.manager.player_group_snapshot(),before)
 def test_invalid_font_settings_do_not_replace_event(self):
  target=Path(self.tmp.name)/'event.json';self.model.export_data(target);data=json.loads(target.read_text(encoding='utf-8'));data['settings']['overlay_font_scale']=4
  target.write_text(json.dumps(data),encoding='utf-8');before=copy.deepcopy(self.model.state)
  with self.assertRaisesRegex(ValueError,'字号'):self.model.import_data(target)
  self.assertEqual(self.model.state,before)

if __name__=='__main__':unittest.main()
