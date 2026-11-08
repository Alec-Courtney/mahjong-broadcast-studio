"""Operator choices preserve suit order, dense faces and selected seat identity."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from console import Console
from model import Model,parse_tiles
from overlays import OverlayManager

APP=QApplication.instance() or QApplication([])


class TilePickerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='tile-picker-',dir=ROOT/'tests')
  self.model=Model(self.tmp.name);self.model.settings['show_overlays']=False
  self.manager=OverlayManager(self.model);self.ui=Console(self.model,self.manager)
  self.ui.resize(1280,800);self.ui.show();APP.processEvents();APP.processEvents()
 def tearDown(self):
  self.ui.close();APP.processEvents();self.tmp.cleanup()
 def test_fixed_suit_rows_are_contiguous_with_proportional_full_faces(self):
  for row,suit in enumerate('mps'):
   for number in range(1,10):
    control=self.ui.tile_buttons[str(number)+suit]
    position=self.ui.picker_grid.getItemPosition(self.ui.picker_grid.indexOf(control))
    self.assertEqual(position[:2],(row,number-1))
    self.assertEqual(control.iconSize(),control.size())
    self.assertGreaterEqual(control.width(),1)
    self.assertGreaterEqual(control.height(),1)
    if number<9:
     next_control=self.ui.tile_buttons[str(number+1)+suit]
     self.assertEqual(control.geometry().right()+1,next_control.geometry().left())
    if row<2:
     below=self.ui.tile_buttons[str(number)+'mps'[row+1]]
     self.assertEqual(control.geometry().bottom()+1,below.geometry().top())
  for number in range(1,8):
   control=self.ui.tile_buttons[str(number)+'z']
   self.assertEqual(self.ui.picker_grid.getItemPosition(self.ui.picker_grid.indexOf(control))[:2],(3,number-1))
  self.assertEqual(len(self.ui.tile_buttons),37)
 def test_detached_resize_keeps_each_suit_in_its_assigned_row(self):
  dock=self.ui.workspace.docks['tiles'];dock.setFloating(True);dock.resize(380,360)
  APP.processEvents();APP.processEvents()
  for suit,row in zip('mpsz',range(4)):
   button=self.ui.tile_buttons['1'+suit]
   self.assertEqual(self.ui.picker_grid.getItemPosition(self.ui.picker_grid.indexOf(button))[:2],(row,0))
  self.ui.focus_tile_target('waits');self.ui.tile_buttons['0p'].click()
  self.assertEqual(self.ui.wait_draft()['waits'],['5p'])
  self.assertEqual(self.model.state['players'][0]['waits'],[])
  self.ui.focus_tile_target('win_tile');self.ui.tile_buttons['0p'].click()
  self.assertEqual(self.ui.win_tile.text(),'0p')
  self.ui.focus_tile_target('ura');self.ui.tile_buttons['4m'].click();self.ui.tile_buttons['1z'].click()
  self.assertEqual(self.ui.ura_indicators.text(),'4m 1z')
  self.assertEqual(self.model.state['phase'],'playing')
 def test_seat_buttons_select_same_players_and_disable_self_deal_in(self):
  before=copy.deepcopy(self.model.state)
  self.ui.winner.buttons[2].click();self.ui.loser.buttons[3].click()
  self.assertEqual(self.ui.selected,2);self.assertEqual(self.ui.loser.currentIndex(),3)
  self.assertTrue(self.ui.winner.buttons[2].isChecked())
  self.assertTrue(self.ui.loser.buttons[3].isChecked())
  self.assertFalse(self.ui.loser.buttons[2].isEnabled())
  self.assertEqual(self.model.state,before)
  self.ui.tsumo.click();self.assertFalse(self.ui.loser.isEnabled());self.assertTrue(self.ui.tsumo.isChecked());self.assertFalse(self.ui.ron.isChecked())
 def test_yaku_buttons_keep_draft_selection_until_cleared(self):
  before=copy.deepcopy(self.model.state)
  self.ui.clear_yaku();self.ui.yaku_buttons['立直'].click();self.ui.yaku_buttons['一发'].click()
  self.assertTrue(self.ui.yaku_buttons['立直'].isChecked());self.assertTrue(self.ui.yaku_buttons['一发'].isChecked())
  self.assertEqual(self.ui.win_event()[0]['han'],2);self.assertEqual(self.model.state,before)
  self.ui.clear_yaku();self.assertFalse(any(control.isChecked() for control in self.ui.yaku_buttons.values()))
  # Correct a face through the shared palette; only the row's Apply adopts it.
  self.ui.inline_automation.receive_tiles(0,parse_tiles('123m123p123s1112z'),source='验收手牌')
  self.ui.han.setValue(6);APP.processEvents()
  hand=self.ui.recognized_hands[0]
  QTest.mouseClick(hand,Qt.MouseButton.LeftButton,pos=hand.tile_rects()[-1].center().toPoint())
  self.assertTrue(hand.is_tile_editing);self.assertEqual(self.ui.tile_target,'hand')
  self.assertFalse(self.ui.submit_wait_button.isEnabled());self.assertFalse(self.ui.settle_button.isEnabled())
  self.ui.submit_waits();self.ui.toggle_player_status(0,'riichi')
  self.assertEqual(self.model.state,before)
  self.ui.tile_buttons['3z'].click()
  self.assertEqual(hand.draft_tiles[-1],'3z');self.assertEqual(hand.tiles[-1],'2z')
  self.assertEqual(self.ui.wait_draft()['waits'],['2z'])
  hand.edit_buttons['apply'].click()
  self.assertFalse(hand.editing);self.assertEqual(hand.tiles[-1],'3z')
  self.assertTrue(self.ui.submit_wait_button.isEnabled())
  self.assertEqual(self.ui.wait_draft()['waits'],['3z']);self.assertEqual(self.ui.han.value(),6)
  self.assertIn(0,self.ui.inline_automation.manual_hands);self.assertEqual(self.model.state,before)
  # Leaving an unadopted edit cannot apply it to the newly selected player.
  QTest.mouseClick(hand,Qt.MouseButton.LeftButton,pos=hand.tile_rects()[-1].center().toPoint())
  self.ui.tile_buttons['4z'].click();self.ui.select(1)
  self.assertFalse(hand.editing);self.assertEqual(hand.tiles[-1],'3z')
  self.ui.inline_automation.receive_tiles(1,parse_tiles('123m123p123s1112z'),source='验收手牌')
  self.ui.select(0);other=self.ui.recognized_hands[1]
  QTest.mouseClick(other,Qt.MouseButton.LeftButton,pos=other.tile_rects()[0].center().toPoint())
  self.assertEqual(self.ui.selected,1);self.assertTrue(other.is_tile_editing);self.assertFalse(hand.editing)
  self.ui.tile_buttons['9m'].click();other.edit_buttons['cancel'].click()
  self.assertEqual(other.tiles[0],'1m');self.assertEqual(self.model.state,before)


if __name__=='__main__':unittest.main()
