"""A section save keeps unrelated event-workspace drafts editable."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication,QWidget
from event_store import EventStore
from event_workspace import EventWorkspaceDialog
from model import Model
from overlays import OverlayManager
import ranking_reports as reports

APP=QApplication.instance() or QApplication([])


class EventWorkspaceDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='tmp-event-drafts-',dir=ROOT/'tests')
        self.model=Model(self.temp.name);self.store=EventStore(self.model)
        self.store.create('团队草稿测试','team_league')
        roster=copy.deepcopy(self.model.state['broadcast']['roster'])
        for index,player in enumerate(roster):player.update(team=f'队伍{index}',team_id=f'team-{index}')
        self.model.set_roster(roster);self.model.settings['show_overlays']=False
        self.manager=OverlayManager(self.model);self.main=QWidget()
        self.main.model=self.model;self.main.manager=self.manager
        self.workspace=EventWorkspaceDialog(self.main)

    def tearDown(self):
        self.workspace._allow_close=True;self.workspace.close();self.workspace.deleteLater()
        self.manager.close();self.main.deleteLater()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents();self.temp.cleanup()

    def test_selected_team_sidebar_previews_draft_names_and_persists_them_on_save(self):
        window=self.workspace
        window.teams.selectRow(0)
        identity=window.selected_id(window.teams)
        preview=window.team_sidebar_preview
        default=preview.pixmap().toImage()
        self.assertFalse(default.isNull())
        window.team_sidebar_display_name.setText('正式播出队名')
        single_line=preview.pixmap().toImage()
        self.assertNotEqual(single_line,default)
        window.team_sidebar_subtitle.setText('CLUB A')
        two_lines=preview.pixmap().toImage()
        self.assertNotEqual(two_lines,single_line)
        self.assertEqual(window.dirty_sections(),['teams'])
        self.assertNotIn(identity,self.model.event['team_names'])
        window.teams.selectRow(1)
        self.assertEqual(window.team_sidebar_display_name.text(),'')
        window.teams.selectRow(0)
        self.assertEqual(window.team_sidebar_subtitle.text(),'CLUB A')
        self.assertEqual(preview.pixmap().toImage(),two_lines)
        window.save_teams()
        self.assertEqual(self.model.event['team_names'][identity],['CLUB A','正式播出队名'])
        self.assertEqual(window.selected_id(window.teams),identity)
        self.assertEqual(window.dirty_sections(),[])

    def test_roster_save_preserves_team_and_seat_drafts(self):
        window=self.workspace
        identity=window.teams.item(0,0).data(Qt.ItemDataRole.UserRole)
        window.teams.item(0,3).setText('尚未保存的侧栏标题')
        window.roster.item(0,0).setText('已保存的姓名')
        seats=[player['id'] for player in self.model.state['players']]
        draft_seats=seats[1:]+seats[:1]
        for control,value in zip(window.table_seats,draft_seats):control.setCurrentIndex(control.findData(value))
        window.save_roster()
        self.assertEqual(window.teams.item(0,3).text(),'尚未保存的侧栏标题')
        self.assertCountEqual(window.dirty_sections(),['teams','seats'])
        self.assertEqual([control.currentData() for control in window.table_seats],draft_seats)
        self.assertEqual(self.model.state['broadcast']['roster'][0]['name'],'已保存的姓名')
        self.assertNotEqual(self.model.event.get('team_names',{}).get(identity,[''])[0],'尚未保存的侧栏标题')
        self.assertTrue(window.save_changes(),window.status.text())
        self.assertEqual(self.model.event['team_names'][identity][0],'尚未保存的侧栏标题')
        self.assertEqual([player['id'] for player in self.model.state['players']],draft_seats)
        self.assertEqual(window.dirty_sections(),[])

    def test_team_save_preserves_roster_draft_until_save_all(self):
        window=self.workspace
        original_name=self.model.state['broadcast']['roster'][0]['name']
        identity=window.teams.item(0,0).data(Qt.ItemDataRole.UserRole)
        window.roster.item(0,0).setText('尚未保存的姓名')
        window.teams.item(0,3).setText('已保存的侧栏标题')
        window.save_teams()
        self.assertEqual(window.roster.item(0,0).text(),'尚未保存的姓名')
        self.assertEqual(window.dirty_sections(),['roster'])
        self.assertEqual(self.model.state['broadcast']['roster'][0]['name'],original_name)
        self.assertEqual(self.model.event['team_names'][identity][0],'已保存的侧栏标题')
        self.assertTrue(window.save_changes(),window.status.text())
        self.assertEqual(self.model.state['broadcast']['roster'][0]['name'],'尚未保存的姓名')
        self.assertEqual(window.dirty_sections(),[])

    def test_clean_other_table_refreshes_without_a_false_unsaved_warning(self):
        window=self.workspace
        window.teams.item(0,1).setText('重新命名的队伍')
        window.save_teams()
        self.assertEqual(window.roster.item(0,1).text(),'重新命名的队伍')
        self.assertEqual(window.dirty_sections(),[])
        identity=window.teams.item(0,0).data(Qt.ItemDataRole.UserRole)
        window.set_logo_draft(identity,'')
        window.save_teams()
        self.assertEqual(window.team_logo_drafts,{})
        self.assertEqual(window.dirty_sections(),[])

    def test_start_selected_match_does_not_leave_an_unsaved_seat_draft(self):
        window = self.workspace
        seats = [player['id'] for player in self.model.state['players']]
        seats = seats[1:] + seats[:1]
        for control, identity in zip(window.table_seats, seats):
            control.setCurrentIndex(control.findData(identity))
        window.start_selected_match()
        self.assertEqual([player['id'] for player in self.model.state['players']], seats)
        self.assertEqual(window.dirty_sections(), [])

    def test_metadata_only_plan_changes_refresh_clean_tables_and_hidden_reports(self):
        window=self.workspace
        player=self.model.state['broadcast']['roster'][0]['id']
        team=window.selected_id(window.teams)
        self.store.update(player_plans={player:8},team_plans={team:24})
        self.assertEqual(window.roster.item(0,6).text(),'8')
        self.assertEqual(window.teams.item(0,2).text(),'24')
        row=next(row for row in window.report_data['rows'] if row['id']==player)
        self.assertEqual(row['planned'],8)
        self.assertEqual(window.dirty_sections(),[])

    def test_saved_roster_and_team_fields_reach_reports_seats_and_disk(self):
        window=self.workspace
        player=self.model.state['broadcast']['roster'][0]['id']
        team=window.selected_id(window.teams)
        window.roster.item(0,0).setText('同步后的选手')
        window.roster.item(0,4).setText('125.5')
        window.roster.item(0,5).setText('4')
        window.roster.item(0,6).setText('8')
        window.roster.item(0,7).setText('4')
        window.save_roster()
        self.assertFalse(window._save_failed,window.status.text())
        row=next(row for row in window.report_data['rows'] if row['id']==player)
        self.assertEqual((row['name'],row['points'],row['played'],row['first']),('同步后的选手',125.5,4,4))
        self.assertEqual((row['planned'],row['remaining'],row['progress'],row['games']),(8,4,.5,4))
        window.teams.item(0,1).setText('同步后的队伍')
        window.teams.item(0,2).setText('20')
        window.teams.item(0,5).setText('300')
        window.teams.item(0,6).setText('6')
        window.teams.item(0,8).setText('#123456')
        window.save_teams()
        self.assertFalse(window._save_failed,window.status.text())
        row=next(row for row in window.report_data['rows'] if row['id']==player)
        self.assertEqual((row['team'],row['color']),('同步后的队伍','#123456'))
        self.assertEqual(self.model.state['players'][0]['name'],'同步后的选手')
        self.assertEqual(self.model.state['players'][0]['team'],'同步后的队伍')
        self.assertIn('同步后的选手',window.table_seats[0].currentText())
        team_row=next(row for row in reports.team_rows(self.model.state,self.model.event) if row['id']==team)
        self.assertEqual((team_row['points'],team_row['games'],team_row['remaining'],team_row['progress']),(300.,'6/20',14,.3))
        restarted=Model(self.model.path)
        self.assertEqual(reports.report(restarted.state,restarted.event)['rows'],window.report_data['rows'])
        self.assertEqual(window.dirty_sections(),[])

    def test_plan_refresh_keeps_other_table_draft_and_merges_separate_changes(self):
        window=self.workspace
        player=self.model.state['broadcast']['roster'][0]['id']
        team=window.selected_id(window.teams)
        window.roster.item(0,0).setText('保留的姓名草稿')
        self.store.update(player_plans={player:8},team_plans={team:24})
        self.assertEqual(window.roster.item(0,0).text(),'保留的姓名草稿')
        self.assertEqual(window.teams.item(0,2).text(),'24')
        window.save_roster()
        self.assertFalse(window._save_failed,window.status.text())
        self.assertEqual(self.model.event['player_plans'][player],8)
        self.assertEqual(window.roster.item(0,6).text(),'8')
        self.assertEqual(window.dirty_sections(),[])

    def test_plans_are_optional_display_fields_and_do_not_limit_further_games(self):
        player=self.model.state['broadcast']['roster'][0]['id']
        roster=copy.deepcopy(self.model.state['broadcast']['roster'])
        roster[0]['games']=6
        self.model.set_roster(roster)
        self.store.update(planned_games=24,player_plans={player:4})
        data=self.workspace.report_data
        row=next(row for row in data['rows'] if row['id']==player)
        self.assertEqual((row['games'],row['planned'],row['remaining'],row['progress']),(6,4,0,1.5))
        self.assertNotIn('planned',data['columns'])
        self.assertEqual(data['columns'][:3],['rank','team','name'])
        other=next(row for row in data['rows'] if row['id']!=player)
        self.assertEqual((other['planned'],other['remaining'],other['progress']),(24,24,0))
        self.store.update(report_columns={'individual':['rank','team','name','games','planned','remaining','progress']})
        self.assertEqual(self.workspace.report_data['columns'][-3:],['planned','remaining','progress'])
        self.model.matches.start_with_seats([p['id'] for p in self.model.state['players']])
        self.assertEqual(self.model.state['phase'],'playing')


if __name__=='__main__':unittest.main()
