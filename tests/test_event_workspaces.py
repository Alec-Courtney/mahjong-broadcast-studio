"""Event isolation, official reports, knockout progression and transfer regressions."""
import copy
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage,QColor,QPainter
from model import Model
from media_store import resolve_media_path
from event_store import BUILTIN_DEMO_ID,EventStore,new_event
from event_results import prepare_result,record_result
import ranking_reports as reports
import event_schedule as schedule
import tournament

APP=QApplication.instance() or QApplication([])
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


class EventTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.root=Path(self.tmp.name)
        self.model=Model(self.root);self.store=EventStore(self.model)
    def tearDown(self):
        APP.processEvents();self.tmp.cleanup()
    def add_roster(self,count):
        roster=copy.deepcopy(self.model.state['broadcast']['roster'])
        for index in range(4,count):roster.append(dict(id='extra-'+str(index),name='选手'+str(index+1),seed=index+1))
        self.model.set_roster(roster);return roster
    def record(self,scores=(40000,30000,25000,25000),seats=None,node=None,match_id=None):
        seats=seats or [p['id'] for p in self.model.state['players']]
        record=prepare_result(self.model,seats,list(scores),node_id=node or '',match_id=match_id)
        record_result(self.store,record);return record
    def test_legacy_migration_keeps_original_and_portrait(self):
        old=Model(self.root/'legacy');image=QImage(8,8,QImage.Format.Format_RGB32);image.fill(QColor('#224499'));photo=self.root/'source.png';image.save(str(photo))
        old.update(lambda state:state['players'][0].update(name='原有姓名',photo=str(photo)));original=(old.path/'session.json').read_bytes();legacy=old.path
        store=EventStore(old);self.assertEqual((legacy/'session.json').read_bytes(),original)
        self.assertTrue(resolve_media_path(old.path,old.state['players'][0]['photo']).is_file());self.assertNotEqual(old.path,legacy)
        restarted=Model(legacy);EventStore(restarted);self.assertEqual(restarted.state['players'][0]['name'],'原有姓名')
        self.assertEqual(restarted.event['id'],store.model.event['id'])
    def test_two_events_restore_complete_settings_and_clear_undo(self):
        first=self.model.event['id'];self.model.setting('overlay_font_scale',1.2);self.model.setting('hotkeys',{'draw':'F10'})
        self.model.setting('special_event_only',True);self.model.field('honba',3);self.model.setting('riichi_payment_mode','deferred')
        self.store.create('另一赛事','team_league');second=self.model.event['id']
        self.assertNotIn('special_event_only',self.model.settings);self.assertNotIn('hotkeys',self.model.settings)
        self.assertEqual(self.model.settings['riichi_payment_mode'],'immediate');self.assertFalse(self.model.undo)
        self.model.field('honba',7);self.store.open(first)
        self.assertEqual(self.model.state['honba'],3);self.assertEqual(self.model.settings['overlay_font_scale'],1.2);self.assertFalse(self.model.undo)
        self.store.open(second);self.assertEqual(self.model.state['honba'],7)
        restarted=Model(self.root);EventStore(restarted);self.assertEqual(restarted.event['id'],second)
    def test_invalid_target_does_not_replace_current_event(self):
        first=self.model.event['id'];self.store.create('目标赛事','individual_league');target=self.model.path/'session.json';self.store.open(first)
        before=copy.deepcopy((self.model.event,self.model.state,self.model.settings,self.model.path))
        payload=json.loads(target.read_text(encoding='utf-8'));payload['settings']['speed']='bad';target.write_text(json.dumps(payload),encoding='utf-8')
        with self.assertRaises(ValueError):self.store.open(payload['event']['id'])
        self.assertEqual((self.model.event,self.model.state,self.model.settings,self.model.path),before)
    def test_import_is_new_event_and_embeds_metadata(self):
        self.store.update(planned_games=8,report_columns={'individual':['name','rank','points']})
        target=self.root/'portable.json';self.model.export_data(target);first=self.model.event['id'];self.store.import_new(target)
        self.assertNotEqual(first,self.model.event['id']);self.assertEqual(self.model.event['planned_games'],8)
        self.assertEqual(reports.columns(self.model.event),['name','rank','points']);self.assertEqual(len(self.store.list_events()),2)
        imported = self.model.path
        archived, = (imported/'imports').glob('*.json')
        self.assertEqual(archived.read_bytes(), target.read_bytes())
        self.store.delete(self.model.event['id'], self.model.event['name'])
        self.assertFalse(imported.exists())
        self.assertTrue(target.exists())
    def test_copy_configuration_resets_all_scores_history_and_baselines(self):
        self.model.configure_broadcast({'start_points':35000});self.record();roster=copy.deepcopy(self.model.state['broadcast']['roster']);roster[0].update(games=2,base_points=100,result_baseline={'counts':[2,0,0,0],'max_score':50000});self.model.set_roster(roster)
        self.store.create('复制配置','team_league',True)
        self.assertFalse(self.model.state['broadcast']['matches']);self.assertEqual([p['score'] for p in self.model.state['players']],[25000]*4)
        self.assertTrue(all(p.get('base_points')==0 and p.get('games')==0 and not p.get('result_baseline') for p in self.model.state['broadcast']['roster']))
        self.model.engine.validate(self.model.state)
        self.assertEqual(self.model.state['initialScores'],[25000]*4)
        self.assertEqual(self.model.state['broadcast']['config']['start_points'],25000)
    def test_each_new_event_type_starts_at_25000(self):
        for kind in ('team_league', 'individual_league', 'individual_knockout'):
            with self.subTest(kind=kind):
                self.store.create('起点测试', kind)
                self.assertEqual(self.model.state['rules']['startPoints'], 25000)
                self.assertEqual(self.model.state['accountingTotal'], 100000)
                self.assertEqual(self.model.state['broadcast']['initial_scores'], [25000]*4)
    def test_builtin_demo_installs_once_without_touching_the_active_event(self):
        live = self.model.event['id']
        session = self.model.path/'session.json'
        original = session.read_bytes()
        self.store.ensure_builtin_demo()
        self.assertEqual(self.model.event['id'], live)
        self.assertEqual(session.read_bytes(), original)
        state, event, simulated = self.store.read_history(BUILTIN_DEMO_ID)
        self.assertTrue(simulated)
        self.assertEqual(len(reports.confirmed_matches(state)), 50)
        self.assertEqual(event['qualification_count'], 6)
        self.assertTrue(set(reports.TECH).issubset(event['report_columns']['individual']))
        self.assertEqual(len(state['broadcast']['roster']), 40)
        self.assertTrue(all(resolve_media_path(self.store.directory(BUILTIN_DEMO_ID), p['photo']).is_file()
                            for p in state['broadcast']['roster']))
        demo_file = self.store.directory(BUILTIN_DEMO_ID)/'session.json'
        demo = demo_file.read_bytes()
        self.store.ensure_builtin_demo()
        self.assertEqual(demo_file.read_bytes(), demo)
        self.store.delete(BUILTIN_DEMO_ID, event['name'])
        self.store.ensure_builtin_demo()
        self.assertNotIn(BUILTIN_DEMO_ID, self.store.index['events'])
    def test_ids_cannot_escape_library(self):
        with self.assertRaises(ValueError):self.store.directory('../x')
    def test_reports_are_confirmed_and_result_corrections_replace(self):
        record=self.record();rows=reports.individual_rows(self.model.state,self.model.event);winner=next(r for r in rows if r['id']==record['results'][0]['id'])
        self.assertEqual((winner['played'],winner['first'],winner['average_rank'],winner['top_rate'],winner['max_score']),(1,1,1.,1.,40000))
        record_result(self.store,record);self.assertEqual(len(self.model.state['broadcast']['matches']),1)
        corrected=self.record((20000,40000,30000,30000),match_id=record['id']);rows=reports.individual_rows(self.model.state,self.model.event)
        winner=next(r for r in rows if r['id']==corrected['results'][0]['id']);self.assertEqual(winner['played'],1);self.assertEqual(winner['max_score'],40000)
        self.assertTrue(all(not m['hands'] for m in self.model.state['broadcast']['matches']))
        self.assertTrue(all(r['hands']==0 for r in tournament.statistics(self.model.state)))
    def test_current_half_early_commit_stays_out_of_reports(self):
        self.model.settle(dict(type='ron',winner=0,loser=1,han=3,fu=30),None);self.model.commit_match()
        self.assertTrue(all(r['played']==0 for r in reports.individual_rows(self.model.state,self.model.event)))
    def test_missing_history_is_explicit_and_uses_known_denominator(self):
        roster=copy.deepcopy(self.model.state['broadcast']['roster']);roster[0].update(games=10,base_points=100);self.model.set_roster(roster)
        rows=reports.individual_rows(self.model.state,self.model.event);entry=next(r for r in rows if r['id']==roster[0]['id'])
        self.assertIsNone(entry['first']);self.assertIsNone(entry['average_rank']);self.assertIsNone(entry['top_rate'])
        self.record();entry=next(r for r in reports.individual_rows(self.model.state,self.model.event) if r['id']==roster[0]['id'])
        self.assertEqual(entry['played'],11);self.assertEqual(entry['known_games'],1);self.assertEqual(entry['top_rate'],1)
        self.assertTrue(any('已知顺位 1' in line for line in reports.report(self.model.state,self.model.event)['notes']))
    def test_team_games_deduplicate_within_table_and_match_sidebar_totals(self):
        self.store.create('团队积分测试','team_league',True);self.store.update(planned_games=12,qualification_count=1)
        roster=copy.deepcopy(self.model.state['broadcast']['roster'])
        for player,team in zip(roster,['A','A','B','C']):player.update(team=team,team_id=team)
        self.model.set_roster(roster);self.record();self.store.update(team_baselines={'A':{'points':120,'games':5}})
        entries={r['id']:r for r in reports.team_rows(self.model.state,self.model.event)};sidebar={r['id']:r for r in tournament.standings(self.model.state,'team')}
        self.assertEqual(entries['A']['games'],'6/12');self.assertEqual(entries['A']['known_games'],2)
        for key in entries:self.assertEqual(entries[key]['points'],sidebar[key]['points']);self.assertEqual(entries[key]['played'],sidebar[key]['games'])
        self.assertEqual(entries['A']['leader_gap'],0);self.assertEqual(entries['A']['cutoff_gap'],0)
    def test_team_results_keep_original_team_after_transfer(self):
        roster=copy.deepcopy(self.model.state['broadcast']['roster'])
        for player,team in zip(roster,['A','B','C','D']):player.update(team=team,team_id=team)
        self.model.set_roster(roster);self.record();roster[0].update(team='E',team_id='E');self.model.set_roster(roster)
        entries={r['id']:r for r in reports.team_rows(self.model.state,self.model.event)};self.assertEqual(entries['A']['played'],1);self.assertEqual(entries['E']['played'],0)
    def test_xlsx_has_both_sheets_numeric_rates_and_literal_names(self):
        self.store.create('团队报表测试','team_league',True);self.store.update(report_columns={'individual':['name','rank','top_rate','points']})
        roster=copy.deepcopy(self.model.state['broadcast']['roster']);roster[0]['name']='=1+1';self.model.set_roster(roster);self.record()
        target=self.root/'table.xlsx';reports.export_xlsx(self.model.state,self.model.event,target)
        with zipfile.ZipFile(target) as archive:
            for filename in archive.namelist():ET.fromstring(archive.read(filename))
            workbook=ET.fromstring(archive.read('xl/workbook.xml'));self.assertEqual([s.attrib['name'] for s in workbook.findall('s:sheets/s:sheet',NS)],['个人积分榜','团队积分榜'])
            sheet=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'));self.assertFalse(sheet.findall('.//s:f',NS))
            self.assertEqual(sheet.find('.//s:c[@r="A5"]/s:is/s:t',NS).text,'=1+1')
            self.assertEqual(sheet.find('.//s:c[@r="C5"]/s:v',NS).text,'1.0')
    def test_individual_export_excludes_team_column(self):
        data=reports.report(self.model.state,self.model.event);self.assertNotIn('team',data['columns'])
        with self.assertRaises(ValueError):reports.report(self.model.state,self.model.event,'team')
        self.assertEqual(reports.export_png(data,self.root/'ranking.png'),[self.root/'ranking.png'])
        self.assertFalse(QImage(str(self.root/'ranking.png')).isNull())
    def test_custom_headers_persist_and_do_not_change_calculations(self):
        self.record();before=reports.individual_rows(self.model.state,self.model.event)
        self.store.update(report_headers={'individual':{'points':'赛季总成绩','top_rate':'头名率','name':'选手称呼','rank':'名次'}})
        self.assertEqual(reports.individual_rows(self.model.state,self.model.event),before)
        self.assertEqual(reports.header('points','individual',self.model.event),'赛季总成绩')
        target=self.root/'renamed.xlsx';reports.export_xlsx(self.model.state,self.model.event,target)
        with zipfile.ZipFile(target) as archive:
            sheet=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'));headers=[c.find('s:is/s:t',NS).text for c in sheet.findall('.//s:row[@r="4"]/s:c',NS)]
            self.assertIn('赛季总成绩',headers);self.assertIn('头名率',headers)
        restarted=Model(self.root);EventStore(restarted);self.assertEqual(reports.header('top_rate','individual',restarted.event),'头名率')
        self.assertFalse(reports.render_report(reports.report(self.model.state,self.model.event)).isNull())
    def test_invalid_custom_header_is_rejected_without_partial_change(self):
        before=copy.deepcopy(self.model.event)
        with self.assertRaises(ValueError):self.store.update(report_headers={'individual':{'points':''}})
        self.assertEqual(self.model.event,before)
    def test_official_ml_metrics_only_use_confirmed_hand_details(self):
        self.model.meld(0,'pon',['5m']*3,1);self.model.riichi(1)
        self.model.field('roundIndex',7);self.model.field('dealer',3)
        self.model.settle(dict(type='ron',winner=0,loser=1,han=3,fu=30),None)
        self.assertEqual(self.model.state['phase'],'finished');self.model.commit_match()
        rows={r['id']:r for r in reports.individual_rows(self.model.state,self.model.event)}
        winner=rows[self.model.state['players'][0]['id']];loser=rows[self.model.state['players'][1]['id']]
        self.assertEqual(winner['hands'],1);self.assertEqual(winner['call_rate'],1);self.assertEqual(winner['win_rate'],1)
        self.assertEqual(winner['call_win_rate'],1);self.assertEqual(winner['average_win'],3900)
        self.assertEqual(loser['riichi_rate'],1);self.assertEqual(loser['deal_in_rate'],1);self.assertEqual(loser['riichi_deal_in_rate'],1)
        self.assertEqual(loser['average_deal_in'],3900)
        self.store.update(report_columns={'individual':reports.preset_columns(self.model.event['kind'],'ml')})
        data=reports.report(self.model.state,self.model.event);self.assertIn('average_win',data['columns']);self.assertIn('hands',data['columns'])
    def test_result_only_entry_does_not_invent_technical_stats(self):
        self.record();entry=reports.individual_rows(self.model.state,self.model.event)[0]
        for key in ('hands','average_win','average_deal_in','win_rate','call_rate','riichi_rate','deal_in_rate','tsumo_rate'):self.assertIsNone(entry[key])
    def test_bracket_uses_confirmed_node_results_and_locks_progression(self):
        self.store.create('淘汰赛测试','individual_knockout',True);roster=self.add_roster(8);self.store.update(bracket=schedule.generate(roster,1,2));nodes=self.model.event['bracket']['nodes']
        with self.assertRaisesRegex(ValueError,'上游'):schedule.start_node(self.store,nodes[-1]['id'])
        for node in nodes[:2]:
            seats=schedule.resolve_sources(self.model.event,node);self.record(seats=seats,node=node['id']);winners=schedule.confirm_advancement(self.store,node['id']);self.assertEqual(len(winners),2)
        self.assertFalse(self.model.undo);schedule.start_node(self.store,nodes[-1]['id'])
        self.assertEqual(len(set(p['id'] for p in self.model.state['players'])),4)
        self.assertFalse(schedule.render_bracket(self.model.state,self.model.event).isNull())
        target=self.root/'bracket.xlsx';reports.export_xlsx(self.model.state,self.model.event,target)
        with zipfile.ZipFile(target) as archive:
            workbook=ET.fromstring(archive.read('xl/workbook.xml'));self.assertEqual(len(workbook.findall('s:sheets/s:sheet',NS)),2)
    def test_changed_upstream_results_require_reconfirmation(self):
        self.store.create('淘汰赛测试','individual_knockout',True);roster=self.add_roster(8);self.store.update(bracket=schedule.generate(roster));nodes=self.model.event['bracket']['nodes'];records=[]
        for node in nodes[:2]:
            records.append(self.record(seats=schedule.resolve_sources(self.model.event,node),node=node['id']));schedule.confirm_advancement(self.store,node['id'])
        self.record((10000,20000,40000,50000),seats=records[0]['seats'],node=nodes[0]['id'],match_id=records[0]['id'])
        with self.assertRaisesRegex(ValueError,'上游成绩'):schedule.start_node(self.store,nodes[-1]['id'])
        with self.assertRaisesRegex(ValueError,'上游成绩'):
            prepare_result(self.model,schedule.resolve_sources(self.model.event,nodes[-1]),[40000,30000,25000,25000],node_id=nodes[-1]['id'])
        with self.assertRaisesRegex(ValueError,'上游成绩'):schedule.preview_advancement(self.model.state,self.model.event,nodes[-1]['id'])
    def test_team_baseline_cannot_split_from_broadcast_after_undo(self):
        roster=copy.deepcopy(self.model.state['broadcast']['roster']);roster[0].update(team='A',team_id='A');self.model.set_roster(roster)
        self.store.update(team_baselines={'A':{'points':100}});self.assertFalse(self.model.undo)
        self.model.field('honba',2);self.model.history('undo')
        self.assertEqual(next(r['points'] for r in tournament.standings(self.model.state,'team') if r['id']=='A'),100)
        self.assertEqual(next(r['points'] for r in reports.team_rows(self.model.state,self.model.event) if r['id']=='A'),100)
    def test_team_baseline_stays_when_last_member_changes_team(self):
        roster=copy.deepcopy(self.model.state['broadcast']['roster']);roster[0].update(team='A',team_id='A');self.model.set_roster(roster)
        self.store.update(team_baselines={'A':{'points':100,'games':2}});self.record();before=next(r for r in reports.team_rows(self.model.state,self.model.event) if r['id']=='A')
        roster[0].update(team='NEW',team_id='NEW');self.model.set_roster(roster)
        after=next(r for r in reports.team_rows(self.model.state,self.model.event) if r['id']=='A');self.assertEqual((before['points'],before['played']),(after['points'],after['played']))
        self.assertEqual(next(r['points'] for r in tournament.standings(self.model.state,'team') if r['id']=='A'),after['points'])
    def test_rule_change_does_not_reprice_previously_confirmed_half(self):
        self.model.field('roundIndex',7);self.model.field('dealer',3);self.model.settle(dict(type='ron',winner=0,loser=1,han=3,fu=30),None);self.model.commit_match()
        before=copy.deepcopy(self.model.state['broadcast']['matches'][0]['results'])
        self.model.configure_broadcast({'return_points':35000});self.model.configure_broadcast({'sidebar_mode':'individual'});self.model.commit_match()
        self.assertEqual(self.model.state['broadcast']['matches'][0]['results'],before);self.assertEqual(tournament.match_results(self.model.state),before)
        self.model.start_match();self.assertEqual(self.model.state['rules']['returnPoints'],35000)
    def test_legacy_next_half_button_keeps_knockout_node_binding(self):
        from console import Console
        from overlays import OverlayManager
        self.store.create('淘汰赛测试','individual_knockout',True);self.store.update(bracket=schedule.generate(self.model.state['broadcast']['roster'],2,2));node=self.model.event['bracket']['nodes'][0]
        schedule.start_node(self.store,node['id']);self.model.field('roundIndex',7);self.model.field('dealer',3)
        self.model.settle(dict(type='ron',winner=0,loser=1,han=3,fu=30),None);self.model.commit_match();first=self.model.state['id']
        manager=OverlayManager(self.model);ui=Console(self.model,manager)
        try:
            ui.broadcast_panel().start_match();self.assertNotEqual(first,self.model.state['id'])
            self.assertEqual(self.model.state['broadcast']['active_node_id'],node['id'])
            self.model.field('roundIndex',7);self.model.field('dealer',3);self.model.settle(dict(type='ron',winner=1,loser=0,han=3,fu=30),None);self.model.commit_match()
            self.assertEqual(len(schedule.node_matches(self.model.state,node['id'])),2)
            self.assertEqual(len(schedule.preview_advancement(self.model.state,self.model.event,node['id'])),2)
        finally:ui.close();APP.processEvents()
    def test_wait_tile_brightness_and_grass_overlay_render(self):
        from artwork import Artwork,Animation,render_image
        from player_layout import PLAYER_LAYOUT
        self.model.submit_waits(0,['1m','9m'],None,['1m'],False)
        art=Artwork(self.model,Animation());normal=render_image(art,'player',0)
        self.model.submit_waits(0,['1m','9m'],None,['1m'],True);grass=render_image(art,'player',0)
        # Transparent tile dimming is visible after composition, not in the
        # unpremultiplied RGB channels of a nearly transparent source pixel.
        for backdrop in ('#182028','#b4bec8'):
            frames=[]
            for source in (normal,grass):
                image=QImage(source.size(),QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor(backdrop))
                painter=QPainter(image);painter.drawImage(0,0,source);painter.end();frames.append(image)
            transform=PLAYER_LAYOUT.body_transform()
            sample_y=PLAYER_LAYOUT.status_y+PLAYER_LAYOUT.status_height//4
            mapped=[transform.map(QPointF(x,sample_y)).toPoint() for x in (213,244)]
            dim,bright=(frames[0].pixelColor(point) for point in mapped)
            luminance=lambda c:.2126*c.red()+.7152*c.green()+.0722*c.blue()
            self.assertGreater(luminance(bright),luminance(dim)+40)
            point=transform.map(QPointF(390,PLAYER_LAYOUT.status_y+4)).toPoint()
            plain,furiten=(image.pixelColor(point) for image in frames)
            green_cast=lambda c:c.green()-(c.red()+c.blue())/2
            self.assertGreater(green_cast(furiten),green_cast(plain)+20)
    def test_in_progress_rule_change_is_rejected_without_altering_hand(self):
        self.model.settle(dict(type='ron',winner=0,loser=1,han=3,fu=30),None);before=copy.deepcopy(self.model.state)
        with self.assertRaisesRegex(ValueError,'进行中'):self.model.configure_broadcast({'return_points':35000})
        self.assertEqual(self.model.state,before)
    def test_bracket_rejects_duplicate_sources_and_cycles(self):
        roster=self.add_roster(8);bracket=schedule.generate(roster);bracket['nodes'][0]['sources'][1]=bracket['nodes'][0]['sources'][0]
        with self.assertRaises(ValueError):schedule.validate_bracket(bracket,self.model.state)
    def test_wait_flags_are_atomic_portable_and_reset_next_hand(self):
        self.model.submit_waits(0,['3m','6m'],7,['3m'],True);self.assertTrue(self.model.state['players'][0]['furiten'])
        self.model.history('undo');self.assertNotIn('furiten',self.model.state['players'][0]);self.model.history('redo')
        self.model.settle(dict(type='ron',winner=1,loser=0,han=3,fu=30),None);self.model.next_hand()
        self.assertFalse(self.model.state['players'][0].get('furiten'));self.assertFalse(self.model.state['players'][0].get('waits_no_yaku'))
    def test_workspace_switch_reloads_all_editors_and_keeps_native_windows(self):
        from console import Console
        from overlays import OverlayManager
        manager=OverlayManager(self.model);ui=Console(self.model,manager)
        try:
            ui.open_event_workspace(3);APP.processEvents();identities={key:int(w.winId()) for key,w in manager.windows.items()}
            ui.add_wait_draft('3m');ui.furiten.setChecked(True);ui.broadcast_panel().title.setText('未提交旧赛事草稿')
            self.store.create('新的个人赛','individual_league');APP.processEvents()
            self.assertFalse(ui.wait_dirty);self.assertEqual(ui.broadcast_panel().title.text(),'新的个人赛')
            self.assertEqual(ui.event_workspace.event_name.text(),'新的个人赛');self.assertEqual(identities,{key:int(w.winId()) for key,w in manager.windows.items()})
        finally:ui.close();APP.processEvents()


if __name__=='__main__':unittest.main()
