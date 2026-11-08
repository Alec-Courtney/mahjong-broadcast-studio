"""Live controls remain reachable and still operate the intended table inputs."""
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
from PySide6.QtGui import QFont,QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QStyle,QStyleOptionSpinBox
from console import Console
from model import Model,YAKU
from overlays import OverlayManager
try:
    from .ui_test_support import controls,reveal,settle,visibility
except ImportError:
    from ui_test_support import controls,reveal,settle,visibility

APP=QApplication.instance() or QApplication([])
for filename in ('msyh.ttc','msyhbd.ttc','seguisym.ttf'):
    path=Path('C:/Windows/Fonts')/filename
    if path.exists():QFontDatabase.addApplicationFont(str(path))


class LiveConsoleAccessibilityTests(unittest.TestCase):
    def setUp(self):
        self.previous_font=APP.font();APP.setFont(QFont('Microsoft YaHei',10))
        self.tmp=tempfile.TemporaryDirectory(prefix='live-control-',dir=ROOT/'tests')
        self.model=Model(self.tmp.name)
        self.model.settings.update(show_overlays=False,console_font_family='Microsoft YaHei',topmost=False)
        self.manager=OverlayManager(self.model);self.ui=Console(self.model,self.manager)
        self.ui.resize(1460,1000);self.ui.show();settle(APP)

    def tearDown(self):
        self.ui.close();APP.processEvents();APP.setFont(self.previous_font);self.tmp.cleanup()

    def assert_accessible(self,width,height):
        self.model.dora('3m');self.ui.resize(width,height);settle(APP)
        for bonus in self.ui.bonuses:self.assertTrue(visibility(bonus,self.ui)['visible'],'Bonus count should be discoverable on first view')
        for key in ('players','round','dora','waits','score','meld','yaku'):
            self.assertFalse(self.ui.workspace.docks[key].isHidden(),key)
            self.assertEqual(self.ui.tabifiedDockWidgets(self.ui.workspace.docks[key]),[],key)
            self.assertEqual(self.ui.workspace.scrolls[key].horizontalScrollBar().maximum(),0,key)
        for name,widget in controls(self.ui).items():
            with self.subTest(control=name,size=(width,height)):
                self.assertTrue(widget.isVisible(),name)
                reveal(widget,APP)
                self.assertTrue(visibility(widget,self.ui)['visible'],name)

    def test_large_desk_keeps_all_live_controls_accessible_without_hidden_tabs(self):
        self.assert_accessible(1460,1000)
        palette=self.ui.workspace.docks['tiles']
        self.assertLess(palette.width(),self.ui.width()*.6)
        self.assertEqual(palette.x(),self.ui.workspace.docks['yaku'].x())
        quick=self.ui.workspace.docks['quick']
        self.assertLessEqual(self.ui.workspace.docks['players'].y()-quick.geometry().bottom(),12)

    def test_small_desk_scrolls_vertically_to_every_live_control_without_side_clipping(self):
        self.assert_accessible(960,620)

    def test_four_score_editors_update_the_corresponding_seats(self):
        for index,row in enumerate(self.ui.player_widgets):
            row[2].setValue(30100+index*100);row[2].editingFinished.emit()
            self.assertEqual(self.model.state['players'][index]['score'],30100+index*100)
            for upward,expected in ((True,30200+index*100),(False,30100+index*100)):
                reveal(row[2],APP);option=QStyleOptionSpinBox();row[2].initStyleOption(option)
                control=QStyle.SubControl.SC_SpinBoxUp if upward else QStyle.SubControl.SC_SpinBoxDown
                rect=row[2].style().subControlRect(QStyle.ComplexControl.CC_SpinBox,option,control,row[2])
                QTest.mouseClick(row[2],Qt.MouseButton.LeftButton,pos=rect.center())
                self.assertEqual(self.model.state['players'][index]['score'],expected)

    def test_round_honba_and_pot_inputs_update_the_live_state(self):
        self.ui.round.setCurrentIndex(self.ui.round.findData(4))
        self.ui.honba.setValue(3);self.ui.pot.setValue(2)
        self.assertEqual((self.model.state['roundIndex'],self.model.state['honba'],self.model.state['pot']),(4,3,2000))

    def test_dora_target_and_tile_button_add_the_displayed_bonus_tile(self):
        count=len(self.model.dora_tiles);self.ui.focus_tile_target('dora')
        self.ui.tile_buttons['4m'].click()
        self.assertEqual(len(self.model.dora_tiles),count+1);self.assertEqual(self.model.dora_tiles[-1],'4m')
        self.assertTrue(self.ui.target_buttons['dora'].isChecked())

    def test_wait_editor_submits_only_to_its_selected_player(self):
        self.ui.select(2);self.ui.focus_tile_target('waits');self.ui.tile_buttons['3m'].click()
        self.ui.remaining.setValue(0)
        self.assertIn('待提交',self.ui.wait_preview_label.text())
        self.assertIn('山存 0',self.ui.wait_preview_label.text())
        self.assertEqual(self.model.state['players'][2]['waits'],[])
        self.ui.submit_wait_button.click()
        self.assertEqual(self.model.state['players'][2]['waits'],['3m'])
        self.assertEqual(self.model.state['players'][2]['remaining'],0)
        self.assertTrue(self.model.state['players'][2]['tenpai'])
        self.assertEqual(self.model.state['players'][0]['waits'],[])
        self.assertIn('已播出',self.ui.wait_preview_label.text())

    def test_published_remaining_updates_immediately_and_survives_reload(self):
        self.ui.select(2);self.ui.focus_tile_target('waits');self.ui.tile_buttons['3m'].click()
        self.ui.remaining.setValue(4);self.ui.submit_wait_button.click()
        before=copy.deepcopy(self.model.state['players']);self.ui.remaining.setValue(0)
        self.assertEqual(self.model.state['players'][2]['remaining'],0)
        self.assertEqual(Model(self.tmp.name).state['players'][2]['remaining'],0)
        self.assertNotIn(2,self.ui.wait_dirty);self.assertIn('已播出',self.ui.wait_preview_label.text())
        for seat in (0,1,3):self.assertEqual(self.model.state['players'][seat],before[seat])
        self.model.history('undo');self.assertEqual(self.ui.remaining.value(),4)
        self.ui.clear_remaining_button.click()
        self.assertIsNone(self.model.state['players'][2]['remaining'])
        self.assertNotIn(2,self.ui.wait_dirty)

    def test_live_remaining_does_not_submit_other_pending_wait_changes(self):
        self.ui.focus_tile_target('waits');self.ui.tile_buttons['3m'].click()
        self.ui.remaining.setValue(4);self.ui.submit_wait_button.click()
        self.ui.wait_no_yaku.click();self.ui.wait_row.itemAt(0).widget().click()
        self.ui.furiten.click();self.ui.tile_buttons['6m'].click();self.ui.remaining.setValue(2)
        player=self.model.state['players'][0]
        self.assertEqual(player['remaining'],2);self.assertEqual(player['waits'],['3m'])
        self.assertEqual(player['waits_no_yaku'],[]);self.assertFalse(player['furiten'])
        self.assertEqual(self.ui.wait_draft()['waits'],['3m','6m'])
        self.assertEqual(self.ui.wait_draft()['no_yaku'],['3m','6m']);self.assertTrue(self.ui.wait_draft()['furiten'])
        self.assertIn(0,self.ui.wait_dirty);self.ui.submit_wait_button.click()
        self.assertEqual(self.model.state['players'][0]['remaining'],2)
        self.assertEqual(self.model.state['players'][0]['waits'],['3m','6m'])

    def test_failed_live_remaining_change_restores_the_visible_count(self):
        self.ui.focus_tile_target('waits');self.ui.tile_buttons['3m'].click()
        self.ui.remaining.setValue(4);self.ui.submit_wait_button.click()
        with patch.object(self.model,'set_remaining',side_effect=ValueError('无法更新山存')):
            self.ui.remaining.setValue(2)
        self.assertEqual(self.ui.remaining.value(),4)
        self.assertEqual(self.model.state['players'][0]['remaining'],4)
        self.assertNotIn(0,self.ui.wait_dirty);self.assertIn('无法更新山存',self.ui.status.text())

    def test_meld_editor_keeps_the_selected_owner_and_called_source(self):
        self.ui.select(1);self.ui.focus_tile_target('meld')
        self.ui.meld_kind.setCurrentIndex(self.ui.meld_kind.findData('pon'))
        self.ui.tile_buttons['5p'].click();self.ui.add_meld_button.click()
        meld=self.model.state['players'][1]['melds'][0]
        self.assertEqual(meld['kind'],'pon');self.assertEqual(meld['tiles'],['5p']*3)
        self.assertEqual(meld['from'],0);self.assertEqual(self.model.state['players'][0]['melds'],[])

    def test_meld_type_and_source_buttons_select_and_submit_with_one_click(self):
        self.ui.select(1);self.ui.focus_tile_target('meld')
        reveal(self.ui.meld_kind,APP);settle(APP)
        self.assertEqual(len(self.ui.meld_kind.buttons),5);self.assertEqual(len(self.ui.meld_from.buttons),3)
        for selector in (self.ui.meld_kind,self.ui.meld_from):
            self.assertEqual(len({control.geometry().center().y() for control in selector.buttons}),1)
            for index,control in enumerate(selector.buttons):
                control.click();self.assertEqual(selector.currentIndex(),index)
                self.assertEqual(sum(button.isChecked() for button in selector.buttons),1)
        self.ui.meld_kind.buttons[self.ui.meld_kind.findData('pon')].click()
        self.ui.meld_from.buttons[self.ui.meld_from.findData(1)].click()
        self.ui.tile_buttons['5p'].click();self.ui.add_meld_button.click()
        self.assertEqual(self.model.state['players'][1]['melds'][0]['from'],2)
        self.ui.meld_kind.buttons[self.ui.meld_kind.findData('ankan')].click()
        self.assertFalse(self.ui.meld_from.isEnabled())
        self.ui.tile_buttons['7s'].click();self.ui.add_meld_button.click()
        self.assertEqual(self.model.state['players'][1]['melds'][1]['kind'],'ankan')
        self.ui.meld_kind.buttons[self.ui.meld_kind.findData('chi')].click()
        self.assertTrue(self.ui.meld_from.isEnabled())

    def test_no_yaku_button_edits_existing_waits_and_marks_new_waits(self):
        self.ui.select(2);self.ui.focus_tile_target('waits')
        for code in ('3m','6m'):self.ui.tile_buttons[code].click()
        before=copy.deepcopy(self.model.state)
        self.ui.wait_no_yaku.click();self.assertTrue(self.ui.wait_no_yaku.isChecked())
        self.ui.wait_row.itemAt(0).widget().click()
        self.assertEqual(self.ui.wait_draft()['waits'],['3m','6m'])
        self.assertEqual(self.ui.wait_draft()['no_yaku'],['3m'])
        self.ui.tile_buttons['9m'].click()
        self.assertEqual(self.ui.wait_draft()['no_yaku'],['3m','9m'])
        self.ui.wait_row.itemAt(0).widget().click()
        self.assertEqual(self.ui.wait_draft()['no_yaku'],['9m'])
        self.assertEqual(self.model.state,before)
        self.ui.wait_no_yaku.click();self.assertFalse(self.ui.wait_no_yaku.isChecked())
        self.ui.wait_row.itemAt(1).widget().click()
        self.assertEqual(self.ui.wait_draft()['waits'],['3m','9m'])
        self.ui.submit_wait_button.click()
        self.assertEqual(self.model.state['players'][2]['waits_no_yaku'],['9m'])
        self.assertEqual(self.model.state['players'][0]['waits'],[])

    def test_furiten_button_toggles_and_restores_each_players_draft(self):
        self.ui.focus_tile_target('waits');self.ui.tile_buttons['3m'].click()
        self.assertFalse(self.ui.furiten.isChecked());self.ui.furiten.click()
        self.assertTrue(self.ui.furiten.isChecked());self.assertTrue(self.ui.wait_draft()['furiten'])
        self.assertFalse(self.model.state['players'][0].get('furiten',False))
        self.ui.select(1);self.assertFalse(self.ui.furiten.isChecked())
        self.ui.select(0);self.assertTrue(self.ui.furiten.isChecked())
        self.ui.submit_wait_button.click();self.assertTrue(self.model.state['players'][0]['furiten'])
        self.ui.furiten.click();self.assertFalse(self.ui.furiten.isChecked())
        self.ui.submit_wait_button.click();self.assertFalse(self.model.state['players'][0]['furiten'])

    def assert_settled_player_inputs_blocked(self,final):
        if final:
            self.model.field('roundIndex',7);self.model.field('dealer',3)
        self.ui.focus_tile_target('meld');self.ui.tile_buttons['5p'].click()
        self.ui.focus_tile_target('waits');self.ui.tile_buttons['3m'].click()
        self.model.settle(dict(type='ron',winner=1,loser=0,han=3,fu=40),
                          dict(winner=1,name='选手二',han=3,fu=40,yaku=['立直'],base_points=5200))
        self.assertEqual(self.model.state['phase'],'finished' if final else 'result')
        self.assertFalse(self.ui.add_meld_button.isEnabled())
        self.assertFalse(self.ui.submit_wait_button.isEnabled())
        self.assertIn('新半庄' if final else '下一局',self.ui.wait_preview_label.text())
        self.assertIn('新半庄' if final else '下一局',self.ui.add_meld_button.toolTip())
        before=copy.deepcopy(self.model.state);history=len(self.model.undo)
        self.ui.add_meld_button.click();self.ui.submit_wait_button.click()
        # Direct callbacks must respect the same phase guard as disabled buttons.
        self.ui.add_meld();self.ui.submit_waits()
        self.assertEqual(self.model.state,before);self.assertEqual(len(self.model.undo),history)
        if final:self.model.start_match()
        else:self.model.next_hand()
        self.assertTrue(self.ui.submit_wait_button.isEnabled())
        self.ui.focus_tile_target('meld');self.ui.tile_buttons['5p'].click()
        self.assertTrue(self.ui.add_meld_button.isEnabled());self.ui.add_meld_button.click()
        self.ui.focus_tile_target('waits');self.ui.tile_buttons['3m'].click();self.ui.submit_wait_button.click()
        self.assertEqual(self.model.state['players'][0]['melds'][0]['tiles'],['5p']*3)
        self.assertEqual(self.model.state['players'][0]['waits'],['3m'])

    def test_settled_hand_blocks_player_submissions_until_next_hand(self):
        self.assert_settled_player_inputs_blocked(False)

    def test_finished_match_blocks_player_submissions_until_new_match(self):
        self.assert_settled_player_inputs_blocked(True)

    def test_ordinary_yaku_and_three_bonus_counts_drive_the_live_han_preview(self):
        self.ui.clear_yaku();self.ui.yaku_buttons[YAKU[0][0]].click()
        for value,widget in zip((1,2,3),self.ui.bonuses):widget.setValue(value)
        event,_=self.ui.win_event()
        self.assertEqual(event['han'],7);self.assertEqual(self.ui.han.value(),7)

    def test_fu_shortcuts_and_other_fu_can_be_entered_directly(self):
        for value in (30,40,50):
            self.ui.fu_buttons[value].click()
            self.assertEqual(self.ui.win_event()[0]['fu'],value)
            self.assertEqual([number for number,button in self.ui.fu_buttons.items() if button.isChecked()],[value])
            self.ui.fu_buttons[value].click();self.assertTrue(self.ui.fu_buttons[value].isChecked())
        reveal(self.ui.fu,APP);self.ui.fu.setFocus();self.ui.fu.selectAll()
        QTest.keyClicks(self.ui.fu,'70');QTest.keyClick(self.ui.fu,Qt.Key.Key_Return)
        self.assertEqual(self.ui.win_event()[0]['fu'],70)
        self.assertFalse(any(button.isChecked() for button in self.ui.fu_buttons.values()))

    def test_fixed_fu_tracks_pinfu_win_method_and_seven_pairs(self):
        before=copy.deepcopy(self.model.state);self.ui.fu_buttons[50].click()
        self.ui.yaku_buttons['平和'].click()
        self.assertEqual(self.ui.win_event()[0]['fu'],30);self.assertTrue(self.ui.fu.isReadOnly())
        self.assertTrue(all(not button.isEnabled() for button in self.ui.fu_buttons.values()))
        self.ui.tsumo.click();event,names=self.ui.win_event()
        self.assertEqual((event['type'],event['fu'],event['han']),('tsumo',20,2))
        self.assertIn('门前清自摸和',names);self.assertIn('20 符',self.ui.fu_hint.text())
        self.ui.ron.click();event,names=self.ui.win_event()
        self.assertEqual((event['type'],event['fu'],event['han']),('ron',30,1))
        self.assertNotIn('门前清自摸和',names)
        self.ui.yaku_buttons['门前清自摸和'].click()
        self.assertEqual((self.ui.kind,self.ui.fu.value()),('tsumo',20))
        self.ui.yaku_buttons['门前清自摸和'].click()
        self.assertEqual((self.ui.kind,self.ui.fu.value()),('ron',30))
        self.ui.yaku_buttons['平和'].click();self.assertFalse(self.ui.fu.isReadOnly())
        self.ui.fu_buttons[40].click();self.assertEqual(self.ui.fu.value(),40)
        self.ui.yaku_buttons['七对子'].click();self.assertEqual(self.ui.fu.value(),25)
        self.ui.tsumo.click();self.assertEqual(self.ui.fu.value(),25)
        self.assertTrue(self.ui.fu.isReadOnly());self.assertEqual(self.ui.win_event()[0]['han'],3)
        self.ui.ron.click();self.assertEqual(self.ui.fu.value(),25)
        self.ui.yaku_buttons['七对子'].click();self.assertFalse(self.ui.fu.isReadOnly())
        self.assertEqual(self.model.state,before)

    def test_unframed_meld_candidates_keep_called_tile_selection_and_removal(self):
        self.ui.focus_tile_target('meld');self.ui.tile_buttons['5p'].click()
        self.ui.draft_row.itemAt(2).widget().click()
        self.assertEqual(self.ui.called_index,2);self.assertIn('第3张',self.ui.called_tile_hint.text())
        control=self.ui.draft_row.itemAt(0).widget()
        control.customContextMenuRequested.emit(control.rect().center())
        self.assertEqual(self.ui.draft,['5p','5p']);self.assertFalse(self.ui.add_meld_button.isEnabled())
        self.ui.clear_draft();self.assertTrue(self.ui.called_tile_hint.isHidden())

    def test_ron_controls_settle_the_selected_winner_and_loser(self):
        self.ui.winner.setCurrentIndex(2);self.ui.loser.setCurrentIndex(3)
        self.ui.han.setValue(3);self.ui.ron.click()
        self.assertIn(self.model.state['players'][2]['name'],self.ui.win_preview_label.text())
        self.assertIn(self.model.state['players'][3]['name'],self.ui.win_preview_label.text())
        self.assertEqual(self.model.state['phase'],'playing')
        self.ui.settle_button.click()
        self.assertEqual(self.model.state['phase'],'result')
        self.assertEqual(self.model.state['lastResult']['winner'],2)
        self.assertEqual(self.model.state['lastResult']['loser'],3)


if __name__=='__main__':unittest.main()
