"""Final meld orientation and independent operator tile sizes stay consistent."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QLabel
from console import Console
from event_store import EventStore
from model import Model
from overlays import OverlayManager
from system_settings import SystemSettingsDialog
try:
    from .ui_test_support import reveal,settle
except ImportError:
    from ui_test_support import reveal,settle

APP=QApplication.instance() or QApplication([])


class DeskMeldPreviewTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.cache').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='desk-meld-',dir=ROOT/'.cache')
        self.model=Model(self.temp.name)
        self.model.settings.update(show_overlays=False,topmost=False)
        self.ui=Console(self.model,OverlayManager(self.model));self.ui.resize(1460,1000);self.ui.show();settle(APP)
        self.dialog=None

    def tearDown(self):
        if self.dialog:self.dialog.close();self.dialog.deleteLater()
        self.ui.close();self.ui.deleteLater();APP.processEvents();self.temp.cleanup()

    def draft(self,kind,tiles,called=0,relation=3):
        self.ui.meld_kind.setCurrentIndex(self.ui.meld_kind.findData(kind))
        self.ui.meld_from.setCurrentIndex(self.ui.meld_from.findData(relation))
        self.ui.draft=list(tiles);self.ui.choose_called(called)

    def test_each_complete_preview_matches_the_group_committed_to_the_player(self):
        for kind,tiles,called,relation in (
            ('chi',['6m','4m','5m'],2,3), ('chi',['6m','4m','5m'],0,2),
            ('pon',['0p','5p','5p'],0,1), ('daiminkan',['7s']*4,1,1),
            ('ankan',['0m','5m','5m','5m'],0,3), ('kakan',['3p']*4,0,2)):
            with self.subTest(kind=kind,relation=relation):
                self.model.clear();self.draft(kind,tiles,called,relation)
                preview=copy.deepcopy(self.ui.meld_draft_preview.group)
                before=copy.deepcopy(self.model.state);history=len(self.model.undo)
                self.ui.refresh_draft()
                self.assertEqual(self.model.state,before);self.assertEqual(len(self.model.undo),history)
                self.ui.add_meld_button.click()
                self.assertEqual(self.model.state['players'][0]['melds'][-1],preview)
        self.assertFalse(any('鸣入牌：第' in label.text() for label in self.ui.findChildren(QLabel)))

    def test_selecting_chi_defaults_to_upper_seat_and_still_allows_overrides(self):
        self.ui.meld_from.setCurrentIndex(self.ui.meld_from.findData(1))
        self.ui.meld_kind.buttons[self.ui.meld_kind.findData('chi')].click()
        self.assertEqual(self.ui.meld_from.currentData(),3)
        self.assertTrue(all(button.isEnabled() for button in self.ui.meld_from.buttons))
        for code in ('3m','4m','5m'):self.ui.add_draft(code)
        self.ui.meld_from.buttons[self.ui.meld_from.findData(2)].click()
        self.assertEqual(self.ui.meld_draft_preview.group['calledIndex'],1)
        self.ui.add_meld_button.click()
        self.assertEqual(self.model.state['players'][0]['melds'][0]['from'],2)

    def test_clicking_a_red_tile_uses_its_actual_identity_after_source_reordering(self):
        self.draft('pon',['0p','5p','5p'],called=2,relation=1)
        preview=self.ui.meld_draft_preview;reveal(preview,APP)
        area=next(area for index,area in preview.tile_rects() if index==0)
        QTest.mouseClick(preview,Qt.MouseButton.LeftButton,pos=area.center().toPoint())
        self.assertEqual(self.ui.called_index,0)
        group=preview.group;self.assertEqual(group['tiles'][group['calledIndex']],'0p')
        self.assertEqual(group['calledIndex'],2)
        self.ui.add_meld_button.click()
        self.assertEqual(self.model.state['players'][0]['melds'][0],group)

    def test_added_kan_preview_keeps_the_original_pon_source_and_stacks_the_new_red_tile(self):
        self.model.meld(0,'pon',['5p']*3,1,0)
        self.ui.meld_kind.setCurrentIndex(self.ui.meld_kind.findData('kakan'));self.ui.add_draft('0p')
        group=copy.deepcopy(self.ui.meld_draft_preview.group)
        self.assertEqual((group['from'],group['calledIndex'],group['tiles'][3]),(1,2,'0p'))
        self.ui.add_meld_button.click()
        self.assertEqual(len(self.model.state['players'][0]['melds']),1)
        self.assertEqual(self.model.state['players'][0]['melds'][0],group)

    def test_right_click_removes_the_visible_red_ankan_tile(self):
        self.draft('ankan',['0p','5p','5p','5p'])
        preview=self.ui.meld_draft_preview;reveal(preview,APP)
        area=next(area for index,area in preview.tile_rects() if index==0)
        QTest.mouseClick(preview,Qt.MouseButton.RightButton,pos=area.center().toPoint())
        self.assertEqual(self.ui.draft,['5p']*3)
        self.assertFalse(self.ui.add_meld_button.isEnabled())

    def test_tile_sizes_save_immediately_preserve_drafts_and_do_not_resize_broadcast(self):
        self.model.submit_waits(0,['3m'],4);self.ui.add_wait_draft('6m');self.ui.add_draft('5p')
        before=copy.deepcopy((self.model.state,self.model.undo));draft=self.ui.wait_draft().copy();meld=self.ui.draft.copy()
        output=self.ui.manager.windows['player0'].size()
        self.dialog=SystemSettingsDialog(self.ui)
        self.assertEqual(self.dialog.console_meld_tile_scale.value(),100)
        self.assertEqual(self.dialog.console_wait_tile_scale.value(),100)
        self.assertEqual(self.ui.player_wait_widgets[0][0].height(),45)
        self.dialog.console_wait_tile_scale.setValue(80)
        self.assertEqual(self.ui.player_wait_widgets[0][0].height(),36)
        self.assertEqual(self.ui.wait_row.itemAt(0).widget().size().toTuple(),(25,36))
        self.assertEqual(self.ui.meld_draft_preview.scale,1.25)
        self.dialog.console_meld_tile_scale.setValue(80)
        self.assertEqual(self.ui.meld_draft_preview.scale,1.)
        self.dialog.console_wait_tile_scale.setValue(100)
        self.assertEqual(self.ui.wait_row.itemAt(0).widget().size().toTuple(),(31,45))
        self.assertEqual(self.ui.wait_draft(),draft);self.assertEqual(self.ui.draft,meld)
        self.assertEqual((self.model.state,self.model.undo),before)
        self.assertEqual(self.ui.manager.windows['player0'].size(),output)
        restarted=Model(self.temp.name)
        self.assertEqual((restarted.settings['console_meld_tile_scale'],restarted.settings['console_wait_tile_scale']),(80,100))

    def test_sizes_are_global_across_events_and_invalid_values_are_rejected(self):
        store=EventStore(self.model);first=self.model.event['id']
        self.model.setting('console_meld_tile_scale',80);self.model.setting('console_wait_tile_scale',140)
        store.create('另一场赛事','individual_league');store.open(first)
        self.assertEqual((self.model.settings['console_meld_tile_scale'],self.model.settings['console_wait_tile_scale']),(80,140))
        before=copy.deepcopy(self.model.settings)
        for value in (39,201,True,100.5):
            with self.assertRaises(ValueError):self.model.setting('console_wait_tile_scale',value)
        self.assertEqual(self.model.settings,before)

    def test_failed_size_save_restores_the_control_and_previous_display(self):
        self.dialog=SystemSettingsDialog(self.ui)
        with patch.object(self.model.repository,'save',side_effect=OSError('写入失败')):
            self.dialog.console_wait_tile_scale.setValue(80)
        self.assertEqual(self.dialog.console_wait_tile_scale.value(),100)
        self.assertEqual(self.ui.player_wait_widgets[0][0].height(),45)
        self.assertIn('写入失败',self.dialog.error.text())

    def test_large_wait_tiles_wrap_in_a_narrow_dock(self):
        self.model.submit_waits(0,[f'{number}m' for number in range(1,10)]+['1z','2z','3z','4z'],13)
        self.model.setting('console_wait_tile_scale',200);self.ui.resize(960,620);settle(APP)
        scroll=self.ui.workspace.scrolls['waits']
        self.assertEqual(scroll.horizontalScrollBar().maximum(),0)
        self.assertEqual(self.ui.wait_row.count(),13)
        for index in range(13):self.assertEqual(self.ui.wait_row.itemAt(index).widget().height(),90)


if __name__=='__main__':unittest.main()
