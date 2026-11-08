"""Native shortcut rail and nonmodal event preset editor acceptance."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from console import Console
from event_store import EventStore
from model import Model
from overlays import OverlayManager
from sidebar_presets import sidebar_presets
from sidebar_presets_dialog import WrappingPresetButton
from i18n import set_language,localize_widgets
import tournament

APP=QApplication.instance() or QApplication([])


class SidebarPresetUITests(unittest.TestCase):
    def setUp(self):
        self.close_answer = patch('settings_window.SettingsWindow.ask_save_changes', return_value=QMessageBox.StandardButton.No)
        self.close_answer.start();self.addCleanup(self.close_answer.stop)
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.root=Path(self.tmp.name)
        self.model=Model(self.root);self.store=EventStore(self.model);self.model.settings['show_overlays']=False
        self.manager=OverlayManager(self.model);self.main=Console(self.model,self.manager)
        self.main.resize(1080,700);self.main.show();APP.processEvents()

    def tearDown(self):
        self.main.close();self.main.deleteLater();APP.processEvents();set_language('zh_CN');self.tmp.cleanup()

    def editor(self):
        self.main.open_sidebar_presets();APP.processEvents();return self.main.sidebar_presets_dialog

    def test_default_buttons_team_gate_and_small_screen_scroll(self):
        self.assertEqual(list(self.main.sidebar_preset_buttons),['personal-home','personal-auto','current-four','team-ranking'])
        self.assertFalse(self.main.sidebar_preset_buttons['team-ranking'].isEnabled())
        self.main.resize(960,620);APP.processEvents()
        self.assertGreater(self.main.navigation_scroll.verticalScrollBar().maximum(),0)
        self.assertTrue(self.main.sidebar_presets_manage_button.isHidden())
        self.assertFalse(self.main.navigation_buttons['settings'].isHidden())
        last=self.main.sidebar_preset_buttons['team-ranking']
        self.main.navigation_scroll.ensureWidgetVisible(last);APP.processEvents()
        position=last.mapTo(self.main.navigation_scroll.viewport(),last.rect().center())
        self.assertTrue(self.main.navigation_scroll.viewport().rect().contains(position))
        self.store.create('团队赛','team_league',copy_config=True);self.assertTrue(self.main.sidebar_preset_buttons['team-ranking'].isEnabled())
        # A carried-over visible strip can still be switched off in a team event.
        self.assertEqual(self.main.quick_table_scores.isEnabled(), self.main.quick_table_scores.isChecked())

    def test_capture_edit_apply_reorder_delete_and_cold_restart(self):
        self.manager.set_sidebar_mode('individual')
        dialog=self.editor();window=self.manager.windows['qualification'];window.setGeometry(35,40,410,660)
        identity=dialog.add_current('我的个人积分');self.assertIn(identity,self.main.sidebar_preset_buttons)
        saved=next(entry for entry in sidebar_presets(self.model) if entry['id']==identity)
        self.assertEqual(saved['geometry'],[window.x(),window.y(),window.width(),window.height()])
        dialog.title.setText('半决赛积分');dialog.page_size.setValue(4);dialog.auto.setChecked(False);dialog.page.setValue(2);dialog.save_selected()
        self.assertEqual(self.main.sidebar_preset_buttons[identity].text(),'半决赛积分')
        before=copy.deepcopy(self.model.state['players']);hands=copy.deepcopy(self.model.state['broadcast']['hands']);matches=copy.deepcopy(self.model.state['broadcast']['matches']);standings=tournament.standings(self.model.state)
        history_lengths=(len(self.model.undo),len(self.model.redo))
        dialog.apply_selected();config=self.model.state['broadcast']['config']
        self.assertEqual((len(self.model.undo),len(self.model.redo)),history_lengths)
        self.assertEqual((config['sidebar_page_size'],config['sidebar_page'],config['sidebar_auto_page']),(4,1,False))
        self.assertEqual(self.model.state['players'],before);self.assertEqual(self.model.state['broadcast']['hands'],hands);self.assertEqual(self.model.state['broadcast']['matches'],matches);self.assertEqual(tournament.standings(self.model.state),standings)
        dialog.move_selected(-1);self.assertEqual(list(self.main.sidebar_preset_buttons)[3],identity)
        restarted=Model(self.root);EventStore(restarted);self.assertEqual(sidebar_presets(restarted)[3]['title'],'半决赛积分')
        dialog.delete_selected();self.assertNotIn(identity,self.main.sidebar_preset_buttons)

    def test_custom_title_wraps_fully_and_is_literal_after_language_change(self):
        dialog=self.editor();title='半决赛个人累计积分第一页专用侧栏完整保留这个按钮的自定义名称'
        identity=dialog.add_current(title);control=self.main.sidebar_preset_buttons[identity]
        self.assertEqual(control.text(),title);self.assertGreater(control.sizeHint().height(),34)
        set_language('ja_JP');localize_widgets(self.main);localize_widgets(dialog)
        self.assertEqual(control.text(),title);self.assertEqual(dialog.title.text(),title)

    def test_eighty_ascii_characters_wrap_without_losing_visible_rows_or_margins(self):
        control=WrappingPresetButton('W'*80);control.setFont(QFont('Arial',12));control.setStyleSheet('QPushButton{background:#000000;color:#ffffff;border:0}')
        control.resize(146,control.heightForWidth(146));control.show();APP.processEvents()
        try:
            self.assertGreater(control.height(),60);image=control.grab().toImage();points=[];rows=[]
            for y in range(image.height()):
                row=[]
                for x in range(image.width()):
                    pixel=image.pixelColor(x,y)
                    if min(pixel.red(),pixel.green(),pixel.blue())>220:points.append((x,y));row.append(x)
                if row:rows.append(y)
            self.assertTrue(points);self.assertGreaterEqual(min(x for x,y in points),7);self.assertLessEqual(max(x for x,y in points),image.width()-8)
            self.assertGreaterEqual(min(rows),5);self.assertLessEqual(max(rows),image.height()-6)
            groups=1+sum(b>a+1 for a,b in zip(rows,rows[1:]));self.assertGreaterEqual(groups,5)
            bounds=control.fontMetrics().boundingRect(0,0,132,20000,control.TEXT_FLAGS,control.text())
            self.assertLessEqual(bounds.height(),control.height()-10);self.assertEqual(control.text(),'W'*80)
        finally:control.close();control.deleteLater();APP.processEvents()

    def test_open_editor_reload_and_guard_prevent_stale_cross_event_save(self):
        dialog=self.editor();original=self.model.event['id'];dialog.title.setText('旧赛事未保存草稿')
        self.store.create('新赛事','individual_league',False)
        self.assertEqual(dialog.loaded_event_id,self.model.event['id']);self.assertIn('重新读取',dialog.status.text())
        dialog.save_selected();self.assertNotIn('旧赛事未保存草稿',[entry['title'] for entry in sidebar_presets(self.model)])
        new=self.model.event['id'];self.store.open(original);identity=dialog.add_current('旧赛事专属')
        self.store.open(new)
        # Also exercise the loaded-ID guard if an external integration replaces
        # the event without delivering the normal import/reload signal.
        dialog.loaded_event_id=original;dialog.title.setText('不能写进新赛事');before=copy.deepcopy(sidebar_presets(self.model));dialog.save_selected()
        self.assertEqual(sidebar_presets(self.model),before);self.assertIn('赛事已切换',dialog.status.text());self.assertNotIn(identity,self.main.sidebar_preset_buttons)

    def test_copy_current_presets_are_independent_and_empty_list_stays_editable(self):
        dialog=self.editor();original=self.model.event['id'];identity=dialog.add_current('原赛事的条件窗口')
        self.store.create('沿用设置','individual_league',True)
        self.assertIn(identity,self.main.sidebar_preset_buttons);dialog.title.setText('复制赛事的新名称');dialog.save_selected()
        self.store.open(original);self.assertEqual(next(entry['title'] for entry in sidebar_presets(self.model) if entry['id']==identity),'原赛事的条件窗口')
        self.manager.save_sidebar_presets([]);dialog.reload();self.assertFalse(dialog.save_button.isEnabled());self.assertFalse(dialog.apply_button.isEnabled());self.assertEqual(self.main.sidebar_preset_buttons,{})
        identity=dialog.add_current('重新添加个人展示');self.assertIn(identity,self.main.sidebar_preset_buttons)


if __name__=='__main__':unittest.main()
