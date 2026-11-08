"""Operator card previews are presentation-only and cannot interrupt gameplay."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtWidgets import QApplication
from model import Model
from overlays import OverlayManager

APP=QApplication.instance() or QApplication([])


class PlayerAnimationControlTests(unittest.TestCase):
    def setUp(self):
        self.now=1000.;self.clock=patch('overlays.time.monotonic',side_effect=lambda:self.now);self.clock.start()
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.model=Model(self.tmp.name)
        self.manager=OverlayManager(self.model);self.manager.on_display()

    def tearDown(self):
        self.manager.close();APP.processEvents();self.tmp.cleanup();self.clock.stop()

    def at(self,seconds):
        self.now=1000.+seconds;self.manager.tick()

    def test_entry_and_exit_return_to_full_cards_without_history_or_score_changes(self):
        before=copy.deepcopy((self.model.state,self.model.undo,self.model.redo,self.model.last_transition))
        self.manager.replay_players('cycle');self.assertEqual(self.manager.player_opacity(),1.)
        self.at(.23);self.assertLess(self.manager.player_opacity(),.5)
        self.at(.46);self.assertEqual(self.manager.stage,'players_demo_in');self.assertEqual(self.manager.player_opacity(),0.)
        self.at(.92);self.assertEqual(self.manager.stage,'table');self.assertEqual(self.manager.player_opacity(),1.)
        self.manager.replay_players();self.assertEqual(self.manager.player_opacity(),0.)
        self.at(1.4);self.assertEqual(self.manager.player_opacity(),1.)
        self.assertEqual((self.model.state,self.model.undo,self.model.redo,self.model.last_transition),before)

    def test_skip_restores_preview_and_real_riichi_takes_priority(self):
        self.manager.replay_players('cycle');self.manager.finish()
        self.assertEqual(self.manager.stage,'table');self.assertEqual(self.manager.player_opacity(),1.)
        self.manager.replay_players();self.model.riichi(0)
        self.assertEqual(self.manager.stage,'table');self.assertTrue(self.manager.riichi_queue.busy)
        state=copy.deepcopy(self.model.state)
        with self.assertRaises(ValueError):self.manager.replay_players()
        self.assertEqual(self.model.state,state);self.assertTrue(self.manager.riichi_queue.busy)

    def test_win_and_payment_animation_cannot_be_interrupted(self):
        self.manager.set_stage('win')
        with self.assertRaises(ValueError):self.manager.replay_players()
        self.assertEqual(self.manager.stage,'win')
        self.manager.set_stage('table');self.manager.animation.begin(self.model.state,self.model.state,1.,'immediate')
        with self.assertRaises(ValueError):self.manager.replay_players('cycle')
        self.assertTrue(self.manager.animation.active)


if __name__=='__main__':unittest.main()
