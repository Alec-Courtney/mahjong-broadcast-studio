"""Real declaration clicks enqueue one effect each and retain operator access."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QWidget,QPushButton,QLabel
from broadcast_console import BroadcastDialog
from model import Model,display_score,payment_lines
from overlays import OverlayManager

APP=QApplication.instance() or QApplication([])


class RiichiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.model=Model(self.tmp.name)
        self.clock=patch('overlays.time.monotonic',return_value=100.);self.now=self.clock.start()
        self.manager=OverlayManager(self.model)
        self.console=None

    def tearDown(self):
        if self.console is not None:
            self.console.workspace.dispose();self.console.shortcuts.dispose();self.console.language_controller.dispose()
            APP.removeEventFilter(self.console);self.console.hide();self.console.deleteLater()
        self.manager.close();APP.processEvents();self.clock.stop();self.tmp.cleanup()

    def test_existing_seat_quick_and_hotkey_buttons_reveal_each_player_only_after_its_effect(self):
        from console import Console
        from artwork import render_image
        self.console=Console(self.model,self.manager);self.console.show();APP.processEvents()
        baseline=render_image(self.manager.art,'player',0,now=100.).copy(12,162,124,169)
        self.console.player_widgets[0][3].click()
        self.now.return_value=100.2;self.console.select(2);self.console.quick_actions['riichi'].click()
        self.now.return_value=100.4;self.console.select(1)
        dock=self.console.workspace.docks['players'];dock.setFloating(True);dock.show();dock.activateWindow()
        QApplication.setActiveWindow(dock);self.console.player_widgets[1][1].setFocus();APP.processEvents()
        self.console.shortcuts.sync_enabled();self.assertTrue(self.console.shortcuts.can_trigger())
        QTest.keyClick(self.console.player_widgets[1][1],Qt.Key.Key_R);APP.processEvents()
        self.assertEqual([p['riichi'] for p in self.model.state['players']],[True,True,True,False])
        self.assertEqual(self.model.state['pot'],3000)
        self.assertEqual([p['score'] for p in self.model.state['players']],[29000,29000,29000,30000])
        self.assertEqual([p['riichi'] for p in self.manager.art.model.state['players']],[False]*4)
        self.assertEqual([p['score'] for p in self.manager.art.model.state['players']],[30000]*4)
        self.assertEqual(self.manager.art.model.state['pot'],0)
        self.assertFalse(self.manager.animation.active);self.assertEqual(self.manager.art.frame_start,{})
        ledger=copy.deepcopy(self.model.state);history=copy.deepcopy(self.model.undo)
        self.now.return_value=102.49;self.manager.tick()
        self.assertEqual(render_image(self.manager.art,'player',0,now=102.49).copy(12,162,124,169),baseline)
        self.now.return_value=102.5;self.manager.tick()
        shown=self.manager.riichi_display_state()
        self.assertEqual([p['riichi'] for p in shown['players']],[True,False,False,False])
        self.assertEqual(shown['pot'],1000);self.assertEqual(self.manager.art.frame_start,{0:102.5})
        self.assertEqual(self.manager.animation.lines,[(-1000,0),(0,0),(0,0),(0,0)])
        self.assertEqual(self.manager.animation.started,102.5)
        self.now.return_value=102.8;self.manager.tick()
        self.assertNotEqual(render_image(self.manager.art,'player',0,now=102.8).copy(12,162,124,169),baseline)
        self.now.return_value=105.;self.manager.tick()
        self.assertEqual([p['riichi'] for p in self.manager.riichi_display_state()['players']],[True,False,True,False])
        self.assertEqual(self.manager.animation.lines,[(-1000,0),(0,0),(-1000,0),(0,0)])
        self.assertAlmostEqual(self.manager.animation.elapsed(index=0),2500.)
        self.assertAlmostEqual(self.manager.animation.elapsed(index=2),0.)
        self.assertEqual(self.manager.animation.after['players'][1]['score'],30000)
        self.now.return_value=107.5;self.manager.tick()
        self.assertIs(self.manager.riichi_display_state(),self.model.state)
        self.assertEqual(self.model.state,ledger);self.assertEqual(self.model.undo,history)

    def test_pending_cancellation_never_refunds_a_deposit_that_was_not_shown(self):
        self.model.riichi(0);self.model.riichi(1);self.model.riichi(2)
        self.now.return_value=100.3;self.model.riichi(1)
        self.assertFalse(self.manager.animation.active)
        self.assertEqual([p['score'] for p in self.manager.riichi_display_state()['players']],[30000]*4)
        self.model.riichi(0)
        self.assertFalse(self.manager.animation.active)
        self.assertEqual(self.manager.riichi_queue.active.seat,2)
        self.assertEqual(self.manager.riichi_queue.elapsed(),0.)
        self.now.return_value=102.8;self.manager.tick()
        self.assertEqual(self.manager.animation.lines,[(0,0),(0,0),(-1000,0),(0,0)])

    def test_waits_names_and_other_score_edits_keep_live_identity_without_restarting_the_effect(self):
        self.model.set_waits(0,['2m']);self.model.set_remaining(0,4)
        self.model.riichi(0);self.model.riichi(1);start=self.manager.riichi_queue._started
        self.model.update(lambda state:state['players'][0].update(name='Current name'))
        self.model.set_waits(0,['3p']);self.model.set_remaining(0,0);self.model.set_score(3,42000)
        shown=self.manager.riichi_display_state()
        self.assertEqual(shown['players'][0]['name'],'Current name')
        self.assertEqual(shown['players'][0]['waits'],['3p']);self.assertEqual(shown['players'][0]['remaining'],0)
        self.assertTrue(shown['players'][0]['tenpai']);self.assertFalse(shown['players'][0]['riichi'])
        self.assertEqual(shown['players'][3]['score'],42000)
        self.assertEqual(self.manager.riichi_queue._started,start)
        self.assertNotEqual(self.manager.riichi_queue.active.player_name,'Current name')
        self.assertFalse(self.manager.riichi_display_state()['players'][1]['riichi'])

    def test_hiding_disabling_or_clearing_effects_flushes_masks_without_stale_replay(self):
        for action in ('hidden','disabled','clean','clear'):
            with self.subTest(action=action):
                self.model.settings.update(show_overlays=True,broadcast_scene='table')
                self.model.set_riichi_effect_enabled(True);self.model.start_match()
                self.model.riichi(0);self.model.riichi(1)
                if action=='hidden':self.manager.set_overlays_visible(False)
                elif action=='disabled':self.model.set_riichi_effect_enabled(False)
                elif action=='clean':self.manager.apply_scene('clean')
                else:self.manager.clear_riichi_effects()
                self.assertFalse(self.manager.riichi_queue.busy)
                self.assertIs(self.manager.riichi_display_state(),self.model.state)
                self.assertFalse(self.manager.animation.active)
                self.assertTrue(self.manager.art.model.state['players'][0]['riichi'])

    def test_settlement_flushes_declarations_and_uses_the_real_payment_ledger_in_both_modes(self):
        for mode in ('immediate','deferred'):
            for kind in ('ron','tsumo','draw'):
                with self.subTest(mode=mode,kind=kind):
                    self.model.settings['riichi_payment_mode']=mode;self.model.start_match()
                    self.model.riichi(0);self.model.riichi(1);before=copy.deepcopy(self.model.state)
                    if kind=='draw':self.model.draw()
                    else:self.model.settle(dict(type=kind,winner=2,loser=3,han=3,fu=40),dict(winner=2,name='Winner',han=3,fu=40,yaku=[]))
                    self.assertFalse(self.manager.riichi_queue.busy)
                    self.assertIs(self.manager.riichi_display_state(),self.model.state)
                    self.assertEqual(self.manager.animation.before,before)
                    self.assertEqual(self.manager.animation.lines,payment_lines(before,self.model.state,mode))

    def test_deferred_mode_and_missing_logo_still_wait_for_completion(self):
        self.model.setting('riichi_payment_mode','deferred');self.model.riichi(0)
        self.assertIsNone(self.manager.riichi_queue.active.logo)
        self.now.return_value=102.49;self.manager.tick()
        self.assertFalse(self.manager.art.model.state['players'][0]['riichi'])
        self.assertEqual(display_score(self.manager.art.model.state,0,'deferred'),30000)
        self.now.return_value=102.5;self.manager.tick()
        self.assertTrue(self.manager.art.model.state['players'][0]['riichi'])
        self.assertEqual(display_score(self.manager.art.model.state,0,'deferred'),30000)
        self.assertFalse(self.manager.animation.active);self.assertEqual(self.manager.art.model.state['pot'],1000)

    def test_disabled_effect_keeps_the_original_immediate_and_deferred_payment_policy(self):
        self.model.set_riichi_effect_enabled(False)
        for mode in ('immediate','deferred'):
            with self.subTest(mode=mode):
                self.model.settings['riichi_payment_mode']=mode;self.model.start_match();self.model.riichi(0)
                self.assertFalse(self.manager.riichi_queue.busy)
                self.assertIs(self.manager.riichi_display_state(),self.model.state)
                self.assertEqual(self.manager.animation.active,mode=='immediate')
                self.assertEqual(self.model.state['players'][0]['score'],29000)
                self.assertEqual(display_score(self.manager.art.model.state,0,mode),29000 if mode=='immediate' else 30000)

    def test_a_late_second_click_completes_only_the_first_players_presentation(self):
        self.model.riichi(0);self.now.return_value=102.6;self.model.riichi(1)
        shown=self.manager.riichi_display_state()
        self.assertEqual([p['riichi'] for p in shown['players']],[True,False,False,False])
        self.assertEqual(shown['pot'],1000)
        self.assertEqual(self.manager.riichi_queue.active.seat,1)
        self.assertEqual(self.manager.riichi_queue.elapsed(),0.)
        self.assertEqual(self.manager.animation.lines,[(-1000,0),(0,0),(0,0),(0,0)])

    def test_import_new_match_and_identity_replacement_discard_unfinished_display_masks(self):
        source=Path(self.tmp.name)/'before-declarations.json';self.model.export_data(source)
        self.model.riichi(0);self.model.riichi(1);self.model.import_data(source,clear_history=True)
        self.assertFalse(self.manager.riichi_queue.busy);self.assertFalse(self.manager._riichi_holds)
        self.assertIs(self.manager.riichi_display_state(),self.model.state)
        self.model.riichi(0);self.model.start_match()
        self.assertFalse(self.manager.riichi_queue.busy);self.assertFalse(self.manager._riichi_holds)
        self.model.riichi(0)
        self.model.update(lambda state:state['players'][0].update(id='replacement-player'))
        self.assertFalse(self.manager.riichi_queue.busy);self.assertFalse(self.manager._riichi_holds)
        self.assertEqual(self.manager.art.model.state['players'][0]['id'],'replacement-player')

    def test_replay_repeats_declaration_before_the_existing_riichi_frame_and_payment(self):
        self.model.riichi(0);self.now.return_value=102.5;self.manager.tick()
        self.now.return_value=105.;self.manager.tick();self.manager.replay()
        self.assertFalse(self.manager.art.model.state['players'][0]['riichi'])
        self.assertFalse(self.manager.animation.active);self.assertNotIn(0,self.manager.art.frame_start)
        self.now.return_value=107.5;self.manager.tick()
        self.assertTrue(self.manager.art.model.state['players'][0]['riichi'])
        self.assertEqual(self.manager.animation.started,107.5)

    def test_replay_during_declarations_cannot_release_or_restart_waiting_players(self):
        self.model.riichi(0);self.model.riichi(1)
        active=self.manager.riichi_queue.active;started=self.manager.riichi_queue._started
        self.now.return_value=101.;self.manager.replay()
        self.assertIs(self.manager.riichi_queue.active,active)
        self.assertEqual(self.manager.riichi_queue._started,started)
        self.assertEqual([p['riichi'] for p in self.manager.riichi_display_state()['players']],[False]*4)
        self.assertEqual(self.manager.riichi_queue.pending_count,1)

    def test_fast_real_clicks_wait_fifo_and_do_not_restart_active(self):
        ids=[p['id'] for p in self.model.state['players']]
        self.model.riichi(0);self.now.return_value=100.2;self.model.riichi(2);self.now.return_value=100.4;self.model.riichi(1)
        queue=self.manager.riichi_queue
        self.assertEqual(queue.active.player_id,ids[0]);self.assertAlmostEqual(queue.elapsed(),.4)
        self.assertEqual([p.player_id for p in queue.pending],[ids[2],ids[1]])
        self.now.return_value=102.49;self.manager.tick();self.assertEqual(queue.active.player_id,ids[0])
        self.now.return_value=102.5;self.manager.tick();self.assertEqual(queue.active.player_id,ids[2]);self.assertEqual(queue.elapsed(),0.)
        self.now.return_value=105.;self.manager.tick();self.assertEqual(queue.active.player_id,ids[1]);self.assertEqual(queue.elapsed(),0.)
        self.now.return_value=107.5;self.manager.tick();self.assertIsNone(queue.active);self.assertFalse(self.manager.content_visible('riichi_effect'))

    def test_effect_window_is_transparent_and_always_click_through(self):
        self.model.settings['locked']=False;self.model.riichi(0)
        window=self.manager.fullscreen_capture
        self.assertEqual(window.base,(1920,1080));self.assertTrue(self.manager.content_visible('riichi_effect'))
        self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))

    def test_undo_import_and_new_hand_never_replay_stale_queue(self):
        self.model.riichi(0);self.model.riichi(1);self.model.history('undo')
        self.assertFalse(self.manager.riichi_queue.busy);self.assertEqual(self.manager.riichi_queue.pending_count,0)
        self.assertIs(self.manager.riichi_display_state(),self.model.state)
        self.model.history('redo');self.assertFalse(self.manager.riichi_queue.busy)
        self.model.riichi(2);self.assertTrue(self.manager.riichi_queue.busy)
        self.manager.apply_scene('clean');self.assertFalse(self.manager.riichi_queue.busy)
        self.manager.apply_scene('table');self.assertFalse(self.manager.riichi_queue.busy)
        self.model.riichi(3);self.manager.set_overlays_visible(False);self.assertFalse(self.manager.riichi_queue.busy)

    def test_cancelling_pending_declaration_removes_only_its_item(self):
        self.model.riichi(0);self.model.riichi(1);self.model.riichi(2);self.model.riichi(1)
        queue=self.manager.riichi_queue;players=self.model.state['players']
        self.assertEqual(queue.active.player_id,players[0]['id']);self.assertEqual([p.player_id for p in queue.pending],[players[2]['id']])

    def test_queue_runs_in_background_without_separate_operator_controls(self):
        parent=QWidget();parent.model=self.model;parent.manager=self.manager;dialog=BroadcastDialog(parent)
        try:
            self.model.riichi(0);self.model.riichi(1)
            self.assertFalse(any('队列' in button.text() for button in dialog.findChildren(QPushButton)))
            self.assertFalse(any('待播' in label.text() for label in dialog.findChildren(QLabel)))
            self.assertFalse(hasattr(dialog,'riichi_effect_status'))
            self.assertEqual(self.manager.riichi_queue.pending_count,1)
            self.now.return_value=102.5;self.manager.tick()
            self.assertEqual(self.manager.riichi_queue.pending_count,0)
            self.assertEqual(self.manager.riichi_queue.active.player_id,self.model.state['players'][1]['id'])
            lengths=(len(self.model.undo),len(self.model.redo));transition=copy.deepcopy(self.model.last_transition)
            self.model.set_riichi_effect_enabled(False)
            self.assertFalse(self.manager.riichi_queue.busy)
            self.assertEqual((len(self.model.undo),len(self.model.redo)),lengths);self.assertEqual(self.model.last_transition,transition)
        finally:
            dialog.hide();dialog.deleteLater();parent.deleteLater();APP.processEvents()


if __name__=='__main__':unittest.main()
