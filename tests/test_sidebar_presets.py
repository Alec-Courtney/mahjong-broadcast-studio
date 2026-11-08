"""Event-owned named views restore presentation without freezing match data."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication
from event_store import EventStore
from model import Model
from overlays import OverlayManager
from sidebar_paging import sidebar_page_data
from sidebar_presets import (default_sidebar_presets,sidebar_presets,make_current_preset,
    preset_available,validate_sidebar_presets,SIDEBAR_SETTING_KEYS)

APP=QApplication.instance() or QApplication([])


class SidebarPresetTests(unittest.TestCase):
    def setUp(self):
        self.now=1000.;self.clock=patch('overlays.time.monotonic',side_effect=lambda:self.now);self.clock.start()
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.model=Model(self.tmp.name);self.store=EventStore(self.model)
        roster=copy.deepcopy(self.model.state['broadcast']['roster'])
        for index in range(4,24):roster.append(dict(id=f'person{index}',name=f'选手{index}',seed=index+1))
        for index,person in enumerate(roster):person.update(team=f'队伍{index%12}',team_id=f'team{index%12}',base_points=240-index*10)
        self.model.set_roster(roster);self.model.configure_broadcast(dict(sidebar_mode='individual',sidebar_page_interval=5))
        self.manager=OverlayManager(self.model);self.manager.apply_scene('qualification')

    def tearDown(self):
        self.manager.close();APP.processEvents();self.tmp.cleanup();self.clock.stop()

    def ledger(self):
        state=self.model.state
        return copy.deepcopy(dict(players=state['players'],rules=state['rules'],pot=state['pot'],
            matches=state['broadcast']['matches'],hands=state['broadcast']['hands'],roster=state['broadcast']['roster']))

    def test_default_views_are_stable_copy_safe_and_team_availability_is_explicit(self):
        values=sidebar_presets(self.model)
        self.assertEqual([value['id'] for value in values],['personal-home','personal-auto','current-four','team-ranking'])
        self.assertEqual([value['title'] for value in values],['个人首页','个人自动轮播','当前四席','团队排名'])
        self.assertEqual([preset_available(self.model,value) for value in values],[True,True,True,False])
        values[0]['title']='外部草稿';self.assertEqual(sidebar_presets(self.model)[0]['title'],'个人首页')
        with self.assertRaises(ValueError):self.manager.apply_sidebar_preset('team-ranking')
        self.store.create('团队赛','team_league',copy_config=True);self.assertTrue(preset_available(self.model,sidebar_presets(self.model)[3]))

    def test_save_current_page_and_position_then_apply_without_changing_accounts(self):
        self.now=1005.;self.manager.tick();self.now=1005.51;self.manager.tick()
        self.assertEqual(self.manager.current_sidebar_page(),1)
        geometry=QRect(55,110,360,585);self.manager.windows['qualification'].setGeometry(geometry)
        saved=make_current_preset('第二页轮播',self.manager)
        self.assertEqual(saved['settings']['sidebar_page'],1);self.assertEqual(saved['geometry'],[55,110,360,585])
        self.manager.save_sidebar_presets([saved])
        self.model.configure_broadcast(dict(sidebar_scope='current',sidebar_auto_page=False));self.manager.windows['qualification'].move(20,20)
        before=self.ledger();self.manager.apply_sidebar_preset(saved['id'])
        self.assertEqual(self.manager.current_sidebar_page(),1);self.assertTrue(self.manager._sidebar_auto_active)
        self.assertEqual(self.manager.windows['qualification'].geometry(),geometry);self.assertEqual(self.ledger(),before)
        loaded=Model(self.tmp.name);EventStore(loaded)
        self.assertEqual(sidebar_presets(loaded),[saved]);self.assertEqual(loaded.settings['windows']['qualification'],[55,110,360,585])

    def test_saved_preset_position_never_overrides_the_current_window_size(self):
        window=self.manager.windows['qualification'];window.setGeometry(55,110,360,585)
        preset=make_current_preset('保存位置',self.manager);self.manager.save_sidebar_presets([preset])
        window.setGeometry(70,90,400,650)
        self.manager.apply_sidebar_preset(preset['id'])
        self.assertEqual(window.geometry(),QRect(55,110,400,650))

    def test_legacy_four_condition_preset_targets_fullscreen_without_moving_sidebar(self):
        entry=default_sidebar_presets()[0];entry.update(id='four',title='四人条件',geometry=[60,204,1800,588])
        entry['settings'].update(sidebar_mode='qualification',qualification_layout='four')
        self.manager.save_sidebar_presets([entry]);self.manager.set_sidebar_mode('individual',True)
        sidebar=self.manager.windows['qualification'].geometry();fullscreen=self.manager.fullscreen_capture.geometry()
        history=copy.deepcopy((self.model.undo,self.model.redo));ledger=self.ledger()
        self.manager.apply_sidebar_preset('four')
        self.assertEqual(self.manager.sidebar_mode(),'individual')
        self.assertEqual(self.manager.fullscreen_panel(),'four_conditions')
        self.assertEqual(self.manager.windows['qualification'].geometry(),sidebar)
        self.assertEqual(self.manager.fullscreen_capture.geometry(),fullscreen)
        saved=make_current_preset('保存全屏条件',self.manager)
        self.assertEqual(saved['settings']['sidebar_mode'],'qualification');self.assertNotIn('geometry',saved)
        self.manager.toggle_sidebar_preset('four');self.assertFalse(self.manager.enabled['four_conditions'])
        self.assertEqual(self.ledger(),ledger);self.assertEqual((self.model.undo,self.model.redo),history)

    def test_home_auto_and_current_four_presets_apply_existing_paging_modes(self):
        before=self.ledger()
        self.manager.apply_sidebar_preset('personal-home')
        self.assertEqual(self.manager.sidebar_mode(),'individual');self.assertEqual(self.manager.current_sidebar_page(),0)
        self.assertFalse(self.manager._sidebar_auto_active);self.assertFalse(self.manager.sidebar_timer.isActive())
        self.manager.apply_sidebar_preset('personal-auto');self.assertTrue(self.manager._sidebar_auto_active)
        self.manager.apply_sidebar_preset('current-four');self.assertEqual(len(sidebar_page_data(self.model)['rows']),4)
        self.assertFalse(self.manager._sidebar_auto_active);self.assertEqual(self.ledger(),before)

    def test_rename_reorder_delete_and_clear_persist_per_event(self):
        original=self.model.event['id'];values=sidebar_presets(self.model);values[0]['title']='自定义第一页'
        changed=[values[1],values[0],values[3]];self.manager.save_sidebar_presets(changed)
        self.assertEqual(sidebar_presets(self.model),changed)
        self.store.create('另一场赛事','individual_league');self.assertEqual(sidebar_presets(self.model),default_sidebar_presets())
        self.store.open(original);self.assertEqual(sidebar_presets(self.model),changed)
        self.manager.save_sidebar_presets([])
        loaded=Model(self.tmp.name);EventStore(loaded);self.assertEqual(sidebar_presets(loaded),[])
        with self.assertRaises(ValueError):self.manager.apply_sidebar_preset('personal-home')

    def test_saved_view_uses_new_names_and_points_instead_of_snapshotting_people(self):
        saved=make_current_preset('直播排名',self.manager);self.manager.save_sidebar_presets([saved])
        roster=copy.deepcopy(self.model.state['broadcast']['roster']);roster[-1].update(name='更新后的新榜首',base_points=1500)
        self.model.set_roster(roster);before=self.ledger();self.manager.apply_sidebar_preset(saved['id'])
        first=sidebar_page_data(self.model)['rows'][0]
        self.assertEqual((first['name'],first['points']),('更新后的新榜首',1500.))
        self.assertEqual(self.ledger(),before)

    def test_geometry_and_foreign_keys_are_rejected_without_partial_changes(self):
        base=make_current_preset('合法方案',self.manager)
        invalid=[]
        for field,value in (('title',''),('title','x'*81),('title','两\n行'),('id','../invalid'),('geometry',[0,0,100000,650]),('geometry',[float('nan'),0,400,650]),('geometry',[0,0,400,True])):
            candidate=copy.deepcopy(base);candidate[field]=value;invalid.append([candidate])
        candidate=copy.deepcopy(base);candidate['settings']['players']=[];invalid.append([candidate])
        candidate=copy.deepcopy(base);candidate['settings']['sidebar_mode']='invalid';invalid.append([candidate])
        candidate=copy.deepcopy(base);candidate['settings']['sidebar_page_interval']=61;invalid.append([candidate])
        invalid.extend([[base,copy.deepcopy(base)],[copy.deepcopy(base) for _ in range(21)]])
        before=copy.deepcopy(self.model.state);geometry=self.manager.windows['qualification'].geometry()
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):self.manager.save_sidebar_presets(values)
                self.assertEqual(self.model.state,before);self.assertEqual(self.manager.windows['qualification'].geometry(),geometry)
        bad=copy.deepcopy(base);bad['settings']['rules']={}
        with self.assertRaises(ValueError):self.model.configure_broadcast({'sidebar_presets':[bad]})
        self.assertEqual(self.model.state,before)

    def test_optional_geometry_and_twenty_distinct_presets_are_accepted(self):
        values=[]
        for index in range(20):
            entry=copy.deepcopy(default_sidebar_presets()[0]);entry.update(id=f'view-{index}',title=f'方案{index}')
            values.append(entry)
        self.assertEqual(validate_sidebar_presets(values),values);self.manager.save_sidebar_presets(values)
        self.manager.apply_sidebar_preset('view-19');self.assertEqual(len(sidebar_presets(self.model)),20)

    def history_without_presentation(self,stack):
        result=copy.deepcopy(stack)
        for state,_ in result:
            for key in SIDEBAR_SETTING_KEYS|{'sidebar_presets'}:state['broadcast']['config'].pop(key,None)
        return result

    def test_shortcuts_and_preset_edits_do_not_consume_real_riichi_undo(self):
        self.model.undo.clear();self.model.redo.clear()
        initial_score=self.model.state['players'][0]['score'];self.model.riichi(0)
        undo=self.history_without_presentation(self.model.undo);transition=copy.deepcopy(self.model.last_transition)
        self.manager.apply_sidebar_preset('personal-auto')
        values=sidebar_presets(self.model);values[0]['title']='现场个人榜';self.manager.save_sidebar_presets(values)
        self.manager.apply_sidebar_preset('personal-home')
        self.assertEqual(len(self.model.undo),1);self.assertEqual(self.model.redo,[])
        self.assertEqual(self.history_without_presentation(self.model.undo),undo)
        self.assertEqual(self.model.last_transition,transition)
        view={key:copy.deepcopy(self.model.state['broadcast']['config'][key]) for key in SIDEBAR_SETTING_KEYS|{'sidebar_presets'}}
        self.model.history('undo')
        self.assertFalse(self.model.state['players'][0]['riichi']);self.assertEqual(self.model.state['players'][0]['score'],initial_score)
        self.assertEqual(self.model.state['pot'],0)
        self.assertEqual({key:self.model.state['broadcast']['config'][key] for key in view},view)
        self.model.history('redo');self.assertTrue(self.model.state['players'][0]['riichi'])
        self.assertEqual(self.model.state['players'][0]['score'],initial_score-1000);self.assertEqual(self.model.state['pot'],1000)
        self.assertEqual({key:self.model.state['broadcast']['config'][key] for key in view},view)

    def test_display_changes_keep_an_existing_redo_and_sync_its_view(self):
        self.model.undo.clear();self.model.redo.clear();self.model.riichi(0);self.model.history('undo')
        redo=self.history_without_presentation(self.model.redo)
        self.manager.apply_sidebar_preset('current-four')
        self.model.configure_sidebar({'sidebar_page_size':4,'sidebar_page_interval':20})
        values=sidebar_presets(self.model);self.manager.save_sidebar_presets(list(reversed(values)))
        self.assertEqual(len(self.model.undo),0);self.assertEqual(len(self.model.redo),1)
        self.assertEqual(self.history_without_presentation(self.model.redo),redo)
        self.model.history('redo')
        self.assertTrue(self.model.state['players'][0]['riichi'])
        config=self.model.state['broadcast']['config']
        self.assertEqual((config['sidebar_scope'],config['sidebar_page_size'],config['sidebar_page_interval']),('current',4,20))
        self.assertEqual(config['sidebar_presets'],list(reversed(values)))

    def test_display_change_preserves_a_real_win_info_transition_and_statistics(self):
        self.model.undo.clear();self.model.redo.clear();self.model.riichi(0)
        self.model.settle(dict(type='ron',winner=1,loser=2,han=1,fu=30),
                          dict(winner=1,name=self.model.state['players'][1]['name'],han=1,fu=30,yaku=['断幺九']))
        info=copy.deepcopy(self.model.last_info);transition=copy.deepcopy(self.model.last_transition);before=self.ledger()
        lengths=(len(self.model.undo),len(self.model.redo))
        self.model.configure_sidebar({'sidebar_scope':'current','sidebar_auto_page':False})
        self.assertEqual(self.model.last_info,info);self.assertEqual(self.model.last_transition,transition)
        self.assertEqual(self.ledger(),before);self.assertEqual((len(self.model.undo),len(self.model.redo)),lengths)

    def test_failed_display_save_and_foreign_fields_leave_state_and_history_unchanged(self):
        self.model.undo.clear();self.model.redo.clear();self.model.riichi(0);self.model.history('undo')
        before=copy.deepcopy((self.model.state,self.model.undo,self.model.redo,self.model.settings,self.model.last_info,self.model.last_transition))
        geometry=self.manager.windows['qualification'].geometry();page=self.manager.current_sidebar_page()
        with patch.object(self.model.repository,'save',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):self.model.configure_sidebar({'sidebar_auto_page':False})
            with self.assertRaises(OSError):self.manager.apply_sidebar_preset('personal-home')
        self.assertEqual((self.model.state,self.model.undo,self.model.redo,self.model.settings,self.model.last_info,self.model.last_transition),before)
        self.assertEqual(self.manager.windows['qualification'].geometry(),geometry);self.assertEqual(self.manager.current_sidebar_page(),page)
        for values in ({'title':'不能从显示通道改赛事'}, {'rules':{}}, {'sidebar_page_interval':3}):
            with self.assertRaises(ValueError):self.model.configure_sidebar(values)
            self.assertEqual((self.model.state,self.model.undo,self.model.redo,self.model.settings,self.model.last_info,self.model.last_transition),before)


if __name__=='__main__':unittest.main()
