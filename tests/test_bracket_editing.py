"""Editable entrant names retain roster IDs, automatic sources and old records."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtWidgets import QApplication,QDialog
from event_store import EventStore
from event_workspace import NodeDialog
from event_results import prepare_result,record_result
from model import Model
import event_schedule as schedule
import ranking_reports

APP=QApplication.instance() or QApplication([])


class BracketEditingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.root=Path(self.tmp.name)
        self.model=Model(self.root);self.store=EventStore(self.model);self.store.create('淘汰赛测试','individual_knockout')
        roster=copy.deepcopy(self.model.state['broadcast']['roster'])
        for index in range(4,8):roster.append(dict(id=f'extra-{index}',name=f'选手{index+1}',seed=index+1))
        self.model.set_roster(roster)
        self.store.update(bracket=schedule.generate(roster,1,2))

    def tearDown(self):APP.processEvents();self.tmp.cleanup()

    def node(self,index=-1):return self.model.event['bracket']['nodes'][index]

    def finish_source(self,index):
        node=self.node(index);seats=schedule.resolve_sources(self.model.event,node)
        start=self.model.state['broadcast']['config']['start_points']
        record=prepare_result(self.model,seats,[start+15000,start+5000,start-5000,start-15000],node_id=node['id'])
        record_result(self.store,record);schedule.confirm_advancement(self.store,node['id'])
        return record

    def draw_texts(self):
        texts=[]
        import panel_typography
        original=panel_typography.draw_single_line
        def record(*args,**kwargs):
            placement=original(*args,**kwargs);texts.append(placement.value);return placement
        with patch.object(panel_typography,'draw_single_line',side_effect=record):
            image=schedule.render_bracket(self.model.state,self.model.event)
        self.assertFalse(image.isNull());return texts

    def test_table_letters_continue_across_rounds_and_after_z(self):
        roster=[dict(id=f'p{index}',name=f'选手{index}',seed=index) for index in range(128)]
        nodes=schedule.generate(roster)['nodes']
        self.assertEqual(nodes[0]['name'],'A桌');self.assertEqual(nodes[25]['name'],'Z桌')
        self.assertEqual(nodes[26]['name'],'AA桌');self.assertEqual(nodes[31]['name'],'AF桌')
        self.assertEqual(nodes[32]['name'],'AG桌');self.assertEqual(nodes[-1]['name'],'BK桌')
        self.assertEqual(schedule.next_table_name([dict(name='Z桌')]),'AA桌')
        self.assertEqual(schedule.next_table_name([dict(name='AA桌'),dict(name='特邀桌')]),'AB桌')
        for value in (0,-1,True):
            with self.assertRaises(ValueError):schedule.table_name(value)

    def test_pending_slots_identify_the_exact_source_without_pending_suffix(self):
        self.assertEqual([node['name'] for node in self.model.event['bracket']['nodes']],['A桌','B桌','C桌'])
        texts=self.draw_texts()
        for label in ('A桌1位','A桌2位','B桌1位','B桌2位'):self.assertIn(label,texts)
        self.assertFalse(any('待晋级' in value or '上游第' in value for value in texts))

    def test_editor_accepts_real_typed_names_and_reopens_with_automatic_sources_intact(self):
        target=copy.deepcopy(self.node());original_sources=copy.deepcopy(target['sources'])
        dialog=NodeDialog(None,self.model.state,self.model.event,target)
        try:
            dialog.manual_players[0].setEditText('新晋级选手张小林')
            ids=[player['id'] for player in self.model.state['broadcast']['roster']]
            for combo,identity in zip(dialog.manual_players[1:],ids[1:4]):combo.setCurrentIndex(combo.findData(identity))
            dialog.accept();self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)
            value=dialog.value();identity=value['source_overrides']['0']
            schedule.save_node(self.store,value,dialog.additional_players)
            self.assertEqual(self.node()['sources'],original_sources)
            self.assertEqual(next(p['name'] for p in self.model.state['broadcast']['roster'] if p['id']==identity),'新晋级选手张小林')
            again=NodeDialog(None,self.model.state,self.model.event,self.node())
            try:
                self.assertEqual(again.manual_players[0].currentData(),identity)
                self.assertEqual(again.manual_players[0].currentText(),'新晋级选手张小林')
                self.assertEqual(again.sources[0].currentData(),original_sources[0])
            finally:again.deleteLater()
            restarted=Model(self.root);EventStore(restarted)
            self.assertEqual(restarted.event['bracket']['nodes'][-1]['source_overrides']['0'],identity)
            schedule.start_node(restarted.event_store,target['id'])
            self.assertEqual(restarted.state['players'][0]['id'],identity)
            self.assertEqual(restarted.state['players'][0]['name'],'新晋级选手张小林')
            self.assertEqual([p['id'] for p in restarted.state['players']],[value['source_overrides'][str(i)] for i in range(4)])
        finally:dialog.deleteLater()

    def test_clearing_manual_names_restores_automatic_entrants_after_confirmation(self):
        target=copy.deepcopy(self.node());target['source_overrides']={str(i):p['id'] for i,p in enumerate(self.model.state['broadcast']['roster'][:4])}
        schedule.save_node(self.store,target)
        dialog=NodeDialog(None,self.model.state,self.model.event,self.node())
        try:
            for combo in dialog.manual_players:combo.setCurrentIndex(0)
            schedule.save_node(self.store,dialog.value(),dialog.additional_players)
        finally:dialog.deleteLater()
        self.assertNotIn('source_overrides',self.node())
        self.assertEqual(schedule.resolve_sources(self.model.event,self.node()),[None]*4)
        self.finish_source(0);self.finish_source(1)
        resolved=schedule.resolve_sources(self.model.event,self.node())
        self.assertEqual(resolved,[*self.node(0)['winners'],*self.node(1)['winners']])
        texts=self.draw_texts();names={p['id']:p['name'] for p in self.model.state['broadcast']['roster']}
        for identity in resolved:self.assertIn(names[identity],texts)
        self.assertNotIn('A桌1位',texts)

    def test_manual_override_does_not_bypass_other_automatic_slots(self):
        target=copy.deepcopy(self.node());target['source_overrides']={'0':self.model.state['broadcast']['roster'][0]['id']}
        schedule.save_node(self.store,target)
        with self.assertRaisesRegex(ValueError,'确认上游'):schedule.start_node(self.store,target['id'])

    def test_completed_table_can_be_renamed_without_rewriting_records_or_ids(self):
        self.finish_source(0);target=copy.deepcopy(self.node(0));records=copy.deepcopy(self.model.state['broadcast']['matches'])
        dialog=NodeDialog(None,self.model.state,self.model.event,target)
        try:
            self.assertTrue(dialog.structure_locked);self.assertFalse(dialog.sources[0].isEnabled())
            self.assertFalse(dialog.manual_players[0].isEnabled());dialog.name.setText('东侧主桌')
            schedule.save_node(self.store,dialog.value(),dialog.additional_players)
        finally:dialog.deleteLater()
        self.assertEqual(self.node(0)['name'],'东侧主桌');self.assertEqual(self.node(0)['id'],target['id'])
        self.assertEqual(self.model.state['broadcast']['matches'],records)
        self.assertEqual(self.node(0)['winners'],target['winners'])
        invalid=copy.deepcopy(self.node(0));invalid['source_overrides']={'0':'extra-4'}
        with self.assertRaisesRegex(ValueError,'仅可修改桌名'):schedule.save_node(self.store,invalid)

    def test_duplicate_manual_identity_is_rejected_without_changing_the_roster(self):
        before=copy.deepcopy((self.model.state,self.model.event));dialog=NodeDialog(None,self.model.state,self.model.event,self.node())
        try:
            for combo in dialog.manual_players[:2]:combo.setEditText('重复新名字')
            dialog.accept();self.assertNotEqual(dialog.result(),QDialog.DialogCode.Accepted)
            self.assertIn('同一选手',dialog.error.text())
            self.assertEqual((self.model.state,self.model.event),before)
        finally:dialog.deleteLater()

    def test_new_names_and_schedule_are_atomic_when_persistence_fails(self):
        dialog=NodeDialog(None,self.model.state,self.model.event,self.node())
        try:
            dialog.manual_players[0].setEditText('不应写入的新名字');value=dialog.value()
            before=copy.deepcopy((self.model.state,self.model.event,self.model.undo,self.model.redo))
            with patch.object(self.model.repository,'save',side_effect=OSError('write failure')):
                with self.assertRaises(OSError):schedule.save_node(self.store,value,dialog.additional_players)
            self.assertEqual((self.model.state,self.model.event,self.model.undo,self.model.redo),before)
        finally:dialog.deleteLater()

    def test_manual_editor_default_name_and_upstream_choices_follow_round(self):
        dialog=NodeDialog(None,self.model.state,self.model.event)
        try:
            self.assertEqual(dialog.name.text(),'D桌');dialog.round.setValue(2)
            labels=[dialog.sources[0].itemText(i) for i in range(dialog.sources[0].count())]
            self.assertIn('A桌1位',labels);self.assertIn('B桌2位',labels);self.assertNotIn('C桌1位',labels)
            selected={'node':self.node(0)['id'],'place':1}
            dialog.sources[0].setCurrentIndex(dialog.sources[0].findData(selected));dialog.round.setValue(1)
            self.assertIsNone(dialog.sources[0].currentData())
        finally:dialog.deleteLater()

    def test_long_names_are_fitted_in_full_instead_of_elided(self):
        roster=copy.deepcopy(self.model.state['broadcast']['roster']);name='这是用于验证完整显示的较长选手姓名ABC'
        roster[0]['name']=name;self.model.set_roster(roster)
        self.assertIn(name,self.draw_texts())

    def test_schedule_report_and_xlsx_keep_sources_custom_table_names_and_manual_real_names(self):
        data=ranking_reports.schedule_report(self.model.state,self.model.event)
        final=next(row for row in data['rows'] if row['node_name']=='C桌')
        self.assertEqual([final[f'seat{i}'] for i in range(1,5)],['A桌1位','A桌2位','B桌1位','B桌2位'])
        renamed=copy.deepcopy(self.node(0));renamed['name']='特别晋级组'
        schedule.save_node(self.store,renamed)
        data=ranking_reports.schedule_report(self.model.state,self.model.event)
        final=next(row for row in data['rows'] if row['node_name']=='C桌')
        self.assertEqual(final['seat1'],'特别晋级组1位')
        target=copy.deepcopy(self.node());original=copy.deepcopy(target['sources'])
        target['source_overrides']={'0':self.model.state['broadcast']['roster'][0]['id']}
        schedule.save_node(self.store,target)
        data=ranking_reports.schedule_report(self.model.state,self.model.event)
        final=next(row for row in data['rows'] if row['node_name']=='C桌')
        expected=self.model.state['broadcast']['roster'][0]['name']
        self.assertEqual(final['seat1'],expected);self.assertEqual(final['seat2'],'特别晋级组2位')
        self.assertEqual(self.node()['sources'],original)
        path=self.root/'schedule.xlsx';ranking_reports.export_schedule_xlsx(self.model.state,self.model.event,path)
        with zipfile.ZipFile(path) as archive:
            sheets=''.join(archive.read(name).decode('utf-8') for name in archive.namelist() if name.startswith('xl/worksheets/'))
        for value in (expected,'特别晋级组2位','B桌1位','C桌'):self.assertIn(value,sheets)
        self.assertNotIn('待晋级',sheets)


if __name__=='__main__':unittest.main()
