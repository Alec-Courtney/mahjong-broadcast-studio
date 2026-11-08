"""Real operator selection, event isolation and league identity continuity."""
import copy
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor,QImage
from PySide6.QtWidgets import QApplication,QComboBox,QWidget
from model import Model
from media_store import resolve_media_path
from event_store import EventStore
from event_workspace import EventWorkspaceDialog
from broadcast_console import BroadcastDialog,seat_choice_entries
from overlays import OverlayManager
import ranking_reports as reports
import tournament

APP=QApplication.instance() or QApplication([])


class TeamSeatSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.root=Path(self.tmp.name)
        self.model=Model(self.root/'library');self.store=EventStore(self.model);self.store.create('团队赛','team_league')
        image=QImage(20,30,QImage.Format.Format_ARGB32);image.fill(QColor('#1199aa'));photo=self.root/'photo.png';image.save(str(photo))
        logo=ROOT/'resources/demo_team_logos/demo_wave_teal_v1.png'
        self.roster=[]
        for i in range(8):
            self.roster.append(dict(id=f'registered-{i}',name='同名选手' if i in (0,4) else f'选手{i+1}',
                team=f'演示队{i%4+1}',team_id=f'team-{i%4}',color=['#218484','#ae4a81','#a88421','#2b70a4'][i%4],
                photo=str(photo),team_logo=str(logo),base_points=[100,-40,50,0,200,-100,75,20][i],games=10,seed=i+1,status='active',
                stats=dict(hands=20,calls=6,riichi=3,wins=4,deal_ins=2,win_points=16000,deal_in_points=8000),
                result_baseline=dict(counts=[3,3,2,2],max_score=45000),registration_number=f'entry-{i}'))
        self.model.configure_broadcast(dict(roster=self.roster,seats=[p['id'] for p in self.roster[:4]]))
        self.roster=copy.deepcopy(self.model.state['broadcast']['roster']);self.widgets=[]

    def tearDown(self):
        for widget in reversed(self.widgets):widget.hide();widget.deleteLater()
        APP.processEvents();self.tmp.cleanup()

    def workspace(self):
        parent=QWidget();parent.model=self.model;parent.manager=SimpleNamespace(save_geometry=lambda:None)
        parent.open_settings=lambda:None;parent.open_player_layout=lambda:None;parent.shortcuts=SimpleNamespace(open_dialog=lambda:None)
        dialog=EventWorkspaceDialog(parent);self.widgets.extend([parent,dialog]);return dialog

    def choose(self,widgets,ids):
        for widget,identity in zip(widgets,ids):widget.setCurrentIndex(widget.findData(identity))

    def finish(self):
        self.model.meld(0,'pon',['5m']*3,1);self.model.riichi(1)
        self.model.field('roundIndex',7);self.model.field('dealer',3)
        self.model.settle(dict(type='ron',winner=0,loser=1,han=3,fu=30),None)
        self.assertEqual(self.model.state['phase'],'finished');self.model.matches.confirm_finished()

    def assert_identity(self,ids):
        roster={p['id']:p for p in self.model.state['broadcast']['roster']}
        for player,identity in zip(self.model.state['players'],ids):
            profile=roster[identity];self.assertEqual(player['id'],identity)
            for key in ('name','photo','team_logo','team','team_id'):self.assertEqual(player.get(key),profile.get(key))
            self.assertEqual(player['team_color'],profile['color'])

    def test_missing_placement_counts_display_zero_only_with_no_history(self):
        roster = copy.deepcopy(self.roster)
        for player in roster[:2]:
            player['games'] = 0
        roster[0].pop('result_baseline')
        roster[1]['result_baseline'] = {'counts': [0] * 4, 'max_score': None}
        roster[2].pop('result_baseline')
        roster[3]['result_baseline'] = {'counts': [0] * 4, 'max_score': 45000}
        self.model.set_roster(roster)
        dialog = self.workspace()

        def counts(index):
            return [dialog.roster.item(index, column).text() for column in range(7, 11)]

        self.assertEqual(counts(0), ['0'] * 4)
        self.assertEqual(counts(1), counts(0))
        self.assertEqual(counts(2), [''] * 4)
        self.assertEqual(counts(3), counts(2))
        self.assertEqual(counts(4), ['3', '3', '2', '2'])
        dialog.roster.item(0, 0).setText('改名')
        dialog.save_roster()
        self.assertFalse(dialog._save_failed, dialog.status.text())
        self.assertNotIn('result_baseline', self.model.state['broadcast']['roster'][0])
        self.assertEqual(self.model.state['broadcast']['roster'][3]['result_baseline'], roster[3]['result_baseline'])

    def test_inline_player_details_follow_team_color_or_keep_a_custom_color(self):
        dialog = self.workspace()
        dialog.roster.selectRow(0)
        dialog.detail_source.setCurrentIndex(dialog.detail_source.findData(True))
        self.assertFalse(dialog.detail_color.isEnabled())
        dialog.detail_status.setCurrentIndex(dialog.detail_status.findData('qualified'))
        dialog.detail_clear_photo.click()
        dialog.roster.selectRow(4)
        self.assertEqual(dialog.detail_source.currentData(), False)
        dialog.detail_color.edit.setText('#123456')
        new_photo = self.root/'new-photo.png'
        image = QImage(20, 30, QImage.Format.Format_ARGB32)
        image.fill(QColor('#eeddee'))
        self.assertTrue(image.save(str(new_photo)))
        with patch('event_workspace.QFileDialog.getOpenFileName', return_value=(str(new_photo), 'PNG')):
            dialog.detail_photo.click()
        self.assertFalse(dialog.detail_photo.icon().isNull())

        dialog.teams.item(0, 8).setText('#b03050')
        dialog.save_teams()
        self.assertFalse(dialog._save_failed, dialog.status.text())
        self.assertEqual(dialog.dirty_sections(), ['roster'])
        dialog.roster.selectRow(0)
        self.assertEqual(dialog.detail_status.currentData(), 'qualified')
        self.assertEqual(dialog.detail_color.value(), '#b03050')
        self.assertFalse(dialog.detail_clear_photo.isEnabled())
        dialog.save_roster()
        self.assertFalse(dialog._save_failed, dialog.status.text())
        dialog.teams.item(0, 8).setText('#c05030')
        dialog.save_teams()
        self.assertFalse(dialog._save_failed, dialog.status.text())

        restarted = Model(self.model.path)
        first, custom = (restarted.state['broadcast']['roster'][index] for index in (0, 4))
        self.assertEqual((first['follow_team_color'], first['color'], first['status'], first['photo']),
                         (True, '#c05030', 'qualified', ''))
        self.assertEqual(restarted.state['players'][0]['team_color'], '#c05030')
        self.assertEqual(custom['color'], '#123456')
        self.assertEqual(resolve_media_path(restarted.path, custom['photo']).read_bytes(), new_photo.read_bytes())
        team = next(row for row in reports.team_rows(restarted.state, restarted.event) if row['id'] == 'team-0')
        self.assertEqual(team['color'], '#c05030')

        dialog.roster.item(0, 1).setData(Qt.ItemDataRole.UserRole, 'team-1')
        dialog.save_roster()
        self.assertFalse(dialog._save_failed, dialog.status.text())
        self.assertEqual(self.model.state['players'][0]['team_color'], self.roster[1]['color'])
        dialog.roster.selectRow(4)
        dialog.detail_source.setCurrentIndex(dialog.detail_source.findData(True))
        dialog.save_roster()
        self.assertEqual(self.model.state['broadcast']['roster'][4]['color'], '#c05030')

        dialog.detail_source.setCurrentIndex(dialog.detail_source.findData(False))
        dialog.detail_color.edit.setText('#704090')
        dialog.teams.item(0, 8).setText('#1f6090')
        dialog.save_teams()
        dialog.save_roster()
        self.assertFalse(dialog._save_failed, dialog.status.text())
        updated = self.model.state['broadcast']['roster'][4]
        self.assertEqual((updated['follow_team_color'], updated['color']), (False, '#704090'))
        team = next(row for row in reports.team_rows(self.model.state, self.model.event) if row['id'] == 'team-0')
        self.assertEqual(team['color'], '#1f6090')

    def test_follow_team_colors_saves_every_player_without_saving_other_roster_drafts(self):
        roster = copy.deepcopy(self.roster)
        roster[4].update(hidden=True, color='#d03651')
        roster[7].update(team='', team_id='')
        self.model.set_roster(roster)
        dialog = self.workspace()
        dialog.teams.item(0,8).setText('#135ca0')
        dialog.save_teams()
        dialog.roster.item(1,0).setText('待保存昵称')
        dialog.roster.selectRow(0)
        dialog.detail_color.edit.setText('#cccccc')
        dialog.follow_team_colors_button.click()
        self.assertFalse(dialog._save_failed, dialog.status.text())
        saved = Model(self.model.path).state['broadcast']['roster']
        self.assertTrue(all(player['follow_team_color'] for player in saved))
        self.assertEqual([saved[index]['color'] for index in (0, 4)], ['#135ca0']*2)
        self.assertEqual(saved[7]['color'], self.roster[7]['color'])
        self.assertEqual(saved[1]['name'], self.roster[1]['name'])
        self.assertEqual(dialog.detail_source.currentData(), True)
        self.assertEqual(dialog.dirty_sections(), ['roster'])
        dialog.save_roster()
        self.assertEqual(self.model.state['broadcast']['roster'][1]['name'], '待保存昵称')
        self.assertTrue(all(player['follow_team_color'] for player in self.model.state['broadcast']['roster']))

    def test_workspace_four_dropdowns_disambiguate_same_names_and_block_duplicates(self):
        dialog=self.workspace();widgets=dialog.table_seats
        self.assertEqual(len(widgets),4);self.assertTrue(all(not widget.isEditable() for widget in widgets))
        texts={identity:label for label,identity in seat_choice_entries(self.roster)}
        self.assertNotEqual(texts['registered-0'],texts['registered-4'])
        self.assertIn('演示队1 · 同名选手',texts['registered-0']);self.assertIn('registered-0',texts['registered-0'])
        self.assertFalse(widgets[1].model().item(widgets[1].findData('registered-0')).isEnabled())
        before=copy.deepcopy(self.model.state)
        self.choose(widgets,['registered-4']*4);dialog.save_table_seats()
        self.assertEqual(self.model.state,before);self.assertIn('不同',dialog.status.text())

    def test_workspace_save_preserves_complete_profiles_and_reopens_with_stable_ids(self):
        dialog=self.workspace();ids=[p['id'] for p in self.roster[4:]]
        self.choose(dialog.table_seats,ids);dialog.save_table_seats();self.assert_identity(ids)
        self.assertEqual(self.model.state['broadcast']['roster'],self.roster)
        restarted=Model(self.root/'library');EventStore(restarted)
        self.assertEqual([p['id'] for p in restarted.state['players']],ids)
        self.assertEqual(restarted.state['broadcast']['roster'],self.roster)
        for player in restarted.state['players']:
            self.assertTrue(resolve_media_path(restarted.path,player['photo']).is_file());self.assertTrue(resolve_media_path(restarted.path,player['team_logo']).is_file())

    def test_team_filter_and_player_choice_stay_linked_per_seat(self):
        dialog = self.workspace()
        team, player = dialog.table_seat_teams[0], dialog.table_seats[0]
        self.assertEqual(team.currentData(), 'team-0')
        self.assertEqual(player.count(), 9)
        team.setCurrentIndex(team.findData('team-2'))
        self.assertIsNone(player.currentData())
        self.assertEqual([player.itemData(i) for i in range(player.count())],
                         [None, 'registered-2', 'registered-6'])
        player.setCurrentIndex(player.findData('registered-6'))
        self.assertEqual(team.currentData(), 'team-2')
        team.setCurrentIndex(0)
        self.assertEqual(player.count(), 9)
        player.setCurrentIndex(player.findData('registered-5'))
        self.assertEqual(team.currentData(), 'team-1')
        self.assertEqual(player.count(), 9)
        self.model.set_score(0, 26000)
        self.assertEqual((team.currentData(), player.currentData()), ('team-1', 'registered-5'))
        team.activated.emit(team.currentIndex())
        self.assertEqual([player.itemData(i) for i in range(player.count())],
                         [None, 'registered-1', 'registered-5'])
        self.assertFalse(player.model().item(1).isEnabled())
        self.assertEqual(dialog.table_seats[1].count(), 9)

    def test_renaming_a_team_keeps_historical_membership_and_points(self):
        from ranking_reports import team_rows
        self.finish()
        match = self.model.state['broadcast']['matches'][0]
        initial = next(row for row in team_rows(self.model.state,self.model.event) if row['id']=='team-0')
        dialog = self.workspace()
        index = next(row for row in range(dialog.teams.rowCount())
                     if dialog.teams.item(row,0).data(Qt.ItemDataRole.UserRole)=='team-0')
        dialog.teams.item(index,1).setText('更名后的队伍')
        dialog.save_teams()
        self.assertFalse(dialog._save_failed,dialog.status.text())
        self.assertEqual(self.model.state['players'][0]['team'],'更名后的队伍')
        archived = self.model.state['broadcast']['matches'][0]
        self.assertEqual(archived['results'],match['results'])
        updated = next(row for row in team_rows(self.model.state,self.model.event) if row['id']=='team-0')
        self.assertEqual((updated['team'],updated['points'],updated['played']),
                         ('更名后的队伍',initial['points'],initial['played']))

    def test_selected_next_half_retains_whole_event_ranks_baselines_stats_and_archives(self):
        self.finish();dialog=self.workspace();before=copy.deepcopy(self.model.state)
        expected_standings=tournament.standings(before,'individual');expected_stats=tournament.statistics(before)
        ids=[p['id'] for p in self.roster[4:]];self.choose(dialog.table_seats,ids);dialog.start_selected_match()
        self.assertNotEqual(self.model.state['id'],before['id']);self.assertEqual(self.model.state['phase'],'playing');self.assert_identity(ids)
        self.assertEqual(tournament.standings(self.model.state,'individual'),expected_standings)
        self.assertEqual(tournament.statistics(self.model.state),expected_stats)
        self.assertEqual(self.model.state['broadcast']['matches'],before['broadcast']['matches'])
        self.assertEqual(self.model.state['broadcast']['roster'],self.roster)
        self.assertFalse(self.model.state['broadcast']['hands']);self.assertNotIn('active_node_id',self.model.state['broadcast'])
        all_rows={r['id']:r for r in expected_standings}
        for row,identity in zip(tournament.table_standings(self.model.state,'individual'),ids):
            self.assertEqual((row['player_id'],row['rank'],row['points']),(identity,all_rows[identity]['rank'],all_rows[identity]['points']))
        self.assertTrue(any(row['rank']>4 for row in tournament.table_standings(self.model.state,'individual')))

    def test_selected_next_half_rejects_unfinished_unconfirmed_and_premature_commit(self):
        ids=[p['id'] for p in self.roster[4:]]
        self.model.riichi(1);before=copy.deepcopy(self.model.state)
        with self.assertRaisesRegex(ValueError,'尚未结束并确认'):self.model.matches.start_with_seats(ids)
        self.assertEqual(self.model.state,before)
        with self.assertRaisesRegex(ValueError,'新建半庄'):self.model.configure_broadcast({'seats':ids})
        self.assertEqual(self.model.state,before)
        self.model.settle(dict(type='ron',winner=0,loser=1,han=3,fu=30),None);self.model.commit_match()
        before=copy.deepcopy(self.model.state)
        with self.assertRaisesRegex(ValueError,'尚未结束并确认'):self.model.matches.start_with_seats(ids)
        self.assertEqual(self.model.state,before)

    def test_save_current_seats_cannot_replace_players_after_recorded_hand(self):
        self.model.settle(dict(type='ron',winner=0,loser=1,han=3,fu=30),None);dialog=self.workspace();before=copy.deepcopy(self.model.state)
        self.choose(dialog.table_seats,[p['id'] for p in self.roster[4:]]);dialog.save_table_seats()
        self.assertEqual(self.model.state,before);self.assertIn('新建半庄',dialog.status.text())

    def test_atomic_selection_rejects_duplicate_unknown_id_and_storage_failure(self):
        before=copy.deepcopy((self.model.state,self.model.settings,self.model.last_info,self.model.undo,self.model.redo))
        for seats in (None,[],['registered-4']*4,['registered-4','registered-5','registered-6','missing']):
            with self.assertRaises(ValueError):self.model.matches.start_with_seats(seats)
            self.assertEqual((self.model.state,self.model.settings,self.model.last_info,self.model.undo,self.model.redo),before)
        with patch.object(self.model.repository,'save',side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError):self.model.matches.start_with_seats([p['id'] for p in self.roster[4:]])
        self.assertEqual((self.model.state,self.model.settings,self.model.last_info,self.model.undo,self.model.redo),before)

    def test_copy_roster_new_event_clears_scores_without_inserting_default_players(self):
        self.finish();original=self.model.event['id'];old_state=copy.deepcopy(self.model.state)
        roster=copy.deepcopy(self.roster);roster[0]['baseline_stats']={'hands':100};self.model.set_roster(roster)
        self.store.update(team_baselines={'team-0':{'points':999,'games':30}})
        source_roster=copy.deepcopy(self.model.state['broadcast']['roster']);self.store.create('独立新团队赛','team_league',True)
        self.assertNotEqual(self.model.event['id'],original);self.assertEqual(len(self.model.state['broadcast']['roster']),8)
        self.assertEqual({p['id'] for p in self.model.state['broadcast']['roster']},{p['id'] for p in source_roster})
        self.assertFalse(self.model.state['broadcast']['matches']);self.assertFalse(self.model.state['broadcast']['hands'])
        self.assertFalse(self.model.state['broadcast']['config'].get('team_baselines'));self.assertFalse(self.model.event.get('team_baselines'))
        for profile,source in zip(self.model.state['broadcast']['roster'],source_roster):
            self.assertEqual((profile['base_points'],profile['games'],profile['stats']),(0,0,{}))
            self.assertNotIn('result_baseline',profile);self.assertNotIn('baseline_stats',profile)
            for key in ('id','name','team','team_id','photo','team_logo','color','registration_number'):self.assertEqual(profile[key],source[key])
        self.assertTrue(all(row['points']==0 and row['games']==0 for row in tournament.standings(self.model.state)))
        self.assertTrue(all(row['hands']==0 for row in tournament.statistics(self.model.state)))
        dialog=self.workspace();self.assertTrue(all(not w.isEditable() and w.count()==9 for w in dialog.table_seats))
        self.choose(dialog.table_seats,[p['id'] for p in source_roster[4:]]);dialog.save_table_seats()
        self.store.open(original);self.assertEqual(self.model.state['broadcast']['matches'],old_state['broadcast']['matches'])

    def test_new_event_without_copy_still_uses_dropdown_roster_and_can_add_select_players(self):
        dialog=self.workspace();dialog.new_name.setText('空白团队赛');dialog.new_kind.setCurrentIndex(dialog.new_kind.findData('team_league'));dialog.create_event()
        self.assertEqual(dialog.nav.currentRow(),2);self.assertEqual(len(self.model.state['broadcast']['roster']),4)
        self.assertTrue(all(not w.isEditable() and w.count()==5 for w in dialog.table_seats))
        self.assertFalse({p['id'] for p in self.roster}&{p['id'] for p in self.model.state['broadcast']['roster']})
        self.assertEqual(dialog.teams.rowCount(), 0)
        self.assertEqual(dialog.roster.item(0,1).text(), '未分队')
        team_ids = [dialog.add_team() for _ in range(4)]
        self.assertEqual([dialog.teams.item(i,0).text() for i in range(4)], ['01','02','03','04'])
        with patch('event_workspace.QFileDialog.getOpenFileName', return_value=(str(self.root/'photo.png'), 'PNG')) as picker:
            dialog.teams.cellWidget(0,7).click()
        picker.assert_called_once()
        dialog.teams.item(0,8).setText('#287090')
        dialog.save_teams()
        self.assertFalse(dialog._save_failed, dialog.status.text())
        logo = self.model.state['broadcast']['teams'][team_ids[0]]['team_logo']
        self.assertTrue(resolve_media_path(self.model.path,logo).is_file())
        self.assertFalse(any(p.get('team_id') for p in self.model.state['broadcast']['roster']))
        portable = self.root/'empty-team.json'
        self.model.export_data(portable)
        saved, _, _, embedded, _ = self.model.read_data(portable)
        self.assertEqual(saved['broadcast']['teams'][team_ids[0]]['team_logo'], logo)
        self.assertIn(logo, embedded)
        dialog.show();APP.processEvents()
        for index in range(4):
            dialog.add_player();row=dialog.roster.rowCount()-1;dialog.roster.item(row,0).setText(f'新报名{index+1}')
            dialog.roster.editItem(dialog.roster.item(row,1))
            editor = next((control for control in dialog.roster.findChildren(QComboBox) if control.isVisible()), None)
            self.assertIsNotNone(editor)
            editor.setCurrentIndex(editor.findData(team_ids[index]))
            editor.activated.emit(editor.currentIndex())
            APP.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            APP.processEvents()
        dialog.save_roster();ids=[p['id'] for p in self.model.state['broadcast']['roster'][4:]]
        self.assertEqual([p['team_id'] for p in self.model.state['broadcast']['roster'][4:]], team_ids)
        self.assertEqual(self.model.state['broadcast']['roster'][4]['team_logo'], logo)
        self.assertEqual(self.model.state['broadcast']['roster'][4]['color'], '#287090')
        self.choose(dialog.table_seats,ids);dialog.save_table_seats();self.assert_identity(ids)
        restarted=Model(self.model.path)
        self.assertEqual(restarted.state['broadcast']['teams'][team_ids[0]]['team_logo'],logo)

    def test_advanced_dropdown_uses_identical_ids_and_starts_selected_lineup(self):
        self.finish();self.model.settings['show_overlays']=False;manager=OverlayManager(self.model)
        parent=QWidget();parent.model=self.model;parent.manager=manager;parent.error=lambda message:self.fail(message)
        dialog=BroadcastDialog(parent);self.widgets.extend([parent,dialog])
        self.assertTrue(all(not w.isEditable() for w in dialog.seats));ids=[p['id'] for p in self.roster[4:]]
        self.choose(dialog.seats,ids);dialog.start_selected_match();self.assert_identity(ids)
        self.assertEqual(len(self.model.state['broadcast']['matches']),1)
        manager.close()

    def test_knockout_selection_cannot_bypass_schedule(self):
        import event_schedule
        self.store.create('淘汰赛','individual_knockout',copy_config=True);self.store.update(bracket=event_schedule.generate(self.roster,2,2));dialog=self.workspace()
        self.assertFalse(dialog.start_seats_button.isEnabled());self.assertFalse(dialog.save_seats_button.isEnabled())
        before=copy.deepcopy(self.model.state)
        with self.assertRaisesRegex(ValueError,'淘汰赛程'):self.model.matches.start_with_seats([p['id'] for p in self.roster[4:]])
        self.assertEqual(self.model.state,before)


if __name__=='__main__':unittest.main()
