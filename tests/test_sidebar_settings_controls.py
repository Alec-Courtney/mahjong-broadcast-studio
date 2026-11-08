"""Operator pagination choices and team/individual control gating persist."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
import time

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtWidgets import QApplication,QWidget
from broadcast_console import BroadcastDialog
from event_store import EventStore
from model import Model
from overlays import OverlayManager
import tournament

APP=QApplication.instance() or QApplication([])


class SidebarControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.root=Path(self.tmp.name)
        self.model=Model(self.root);self.store=EventStore(self.model);self.model.settings['show_overlays']=False
        self.model.configure_broadcast({'sidebar_mode':'individual'})
        self.manager=OverlayManager(self.model);self.parent=QWidget();self.parent.model=self.model;self.parent.manager=self.manager
        self.dialog=BroadcastDialog(self.parent)

    def tearDown(self):
        self.dialog.hide();self.dialog.deleteLater();self.parent.deleteLater();self.manager.close();APP.processEvents();self.tmp.cleanup()

    def test_scope_manual_and_automatic_settings_round_trip(self):
        self.dialog.sidebar_scope.setCurrentIndex(self.dialog.sidebar_scope.findData('current'));self.dialog.save_sidebar_page()
        self.assertEqual(self.model.state['broadcast']['config']['sidebar_scope'],'current')
        self.assertFalse(self.dialog.sidebar_auto_page.isEnabled());self.assertFalse(self.dialog.sidebar_page_interval.isEnabled());self.assertFalse(self.dialog.sidebar_page_size.isEnabled())
        self.dialog.sidebar_scope.setCurrentIndex(self.dialog.sidebar_scope.findData('all'));self.dialog.save_sidebar_page();self.dialog.sidebar_auto_page.setChecked(False)
        self.assertTrue(self.dialog.sidebar_page.isEnabled());self.assertFalse(self.dialog.sidebar_page_interval.isEnabled())
        self.dialog.sidebar_page_size.setValue(4);self.dialog.sidebar_auto_page.setChecked(True);self.dialog.sidebar_page_interval.setValue(25)
        restarted=Model(self.root);EventStore(restarted);config=restarted.state['broadcast']['config']
        self.assertEqual((config['sidebar_scope'],config['sidebar_auto_page'],config['sidebar_page_interval'],config['sidebar_page_size']),('all',True,25,4))

    def test_team_disables_top_pt_controls_without_destroying_personal_preference(self):
        previous=dict(self.manager.enabled);original=self.model.event['id'];self.store.create('团队赛','team_league',copy_config=True);self.dialog.reload()
        for widget in (self.dialog.visibility['table_scores'],self.dialog.table_score_mode,self.dialog.table_score_layout,self.dialog.place_table_scores_button):self.assertFalse(widget.isEnabled())
        self.assertEqual(self.manager.enabled.get('table_scores'),previous.get('table_scores'))
        self.store.open(original);self.dialog.reload()
        for widget in (self.dialog.visibility['table_scores'],self.dialog.table_score_mode,self.dialog.table_score_layout,self.dialog.place_table_scores_button):self.assertTrue(widget.isEnabled())

    def test_fixed_first_page_stops_real_timer_keeps_global_ranks_and_survives_restart(self):
        roster=[dict(profile,base_points=points) for profile,points in zip(self.model.state['broadcast']['roster'],[20,-15,99,0])]
        roster += [dict(id=f'extra-{i}',name=f'登记选手{i}',seed=i+1,base_points=140-i*11) for i in range(4,12)]
        self.model.set_roster(roster)
        self.model.configure_broadcast({'sidebar_mode':'team','sidebar_scope':'all','sidebar_auto_page':True,'sidebar_page_size':4,'sidebar_page':2})
        self.model.setting('show_overlays',True);self.manager.set_sidebar_mode('team',True)
        self.manager._sync_sidebar_paging(time.monotonic())
        self.assertTrue(self.manager.sidebar_timer.isActive());self.assertTrue(self.manager._sidebar_auto_active)
        self.model.field('honba',1);self.model.history('undo');history_lengths=(len(self.model.undo),len(self.model.redo))
        expected=[(row['player_id'],row['rank'],row['points']) for row in tournament.table_standings(self.model.state,'individual')]
        self.assertTrue(any(row[1]>4 for row in expected))
        self.dialog.fixed_first_page_button.click()
        self.assertEqual((len(self.model.undo),len(self.model.redo)),history_lengths)
        config=self.model.state['broadcast']['config']
        self.assertEqual((config['sidebar_mode'],config['sidebar_scope'],config['sidebar_auto_page'],config['sidebar_page']),('individual','all',False,0))
        self.assertTrue(self.manager.enabled['qualification']);self.assertFalse(self.manager.sidebar_timer.isActive());self.assertFalse(self.manager._sidebar_auto_active)
        self.assertIsNone(self.manager._sidebar_transition);self.assertEqual(self.manager.current_sidebar_page(),0)
        self.manager._advance_sidebar_paging(time.monotonic()+120);self.assertEqual(self.manager.current_sidebar_page(),0)
        self.assertEqual([(row['player_id'],row['rank'],row['points']) for row in tournament.table_standings(self.model.state,'individual')],expected)
        self.assertFalse(self.dialog.sidebar_auto_page.isChecked());self.assertEqual(self.dialog.sidebar_page.value(),1)
        restarted=Model(self.root);EventStore(restarted);reopened=OverlayManager(restarted)
        try:
            saved=restarted.state['broadcast']['config']
            self.assertEqual((saved['sidebar_mode'],saved['sidebar_scope'],saved['sidebar_auto_page'],saved['sidebar_page']),('individual','all',False,0))
            self.assertEqual(reopened.current_sidebar_page(),0);self.assertFalse(reopened.sidebar_timer.isActive());self.assertTrue(reopened.enabled['qualification'])
        finally:reopened.close()


if __name__=='__main__':unittest.main()
