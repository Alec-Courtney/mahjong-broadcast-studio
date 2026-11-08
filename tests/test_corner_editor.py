"""Two editable text lines, portable logos and the live-desk entry points."""
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
from PySide6.QtGui import QFont,QFontDatabase,QImage,QPainter
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from console import Console
from corner_header import CORNER_SIZE,corner_logo,corner_texts,draw_corner_header
from event_store import EventStore,resolve_active_directory
from media_store import resolve_media_path
from model import Model
from overlays import OverlayManager
try:
    from .ui_test_support import reveal,settle,visibility
except ImportError:
    from ui_test_support import reveal,settle,visibility

APP=QApplication.instance() or QApplication([])
for filename in ('msyh.ttc','msyhbd.ttc','arial.ttf'):
    path=Path('C:/Windows/Fonts')/filename
    if path.exists():QFontDatabase.addApplicationFont(str(path))


class CornerEditorTests(unittest.TestCase):
    def setUp(self):
        self.close_answer = patch('settings_window.SettingsWindow.ask_save_changes', return_value=QMessageBox.StandardButton.No)
        self.close_answer.start();self.addCleanup(self.close_answer.stop)
        self.old_font=APP.font();APP.setFont(QFont('Microsoft YaHei',10))
        self.temp=tempfile.TemporaryDirectory(prefix='corner-editor-',dir=ROOT/'tests')
        self.model=Model(self.temp.name);self.store=EventStore(self.model)
        self.model.settings.update(show_overlays=False,topmost=False,console_font_family='Microsoft YaHei')
        self.manager=OverlayManager(self.model);self.ui=Console(self.model,self.manager)
        self.ui.resize(1460,1000);self.ui.show();settle(APP)

    def tearDown(self):
        self.ui.close();self.ui.deleteLater();APP.processEvents();APP.setFont(self.old_font);self.temp.cleanup()

    def test_dedicated_editor_imports_logo_and_preserves_each_line_typography_across_export(self):
        dialog=self.ui.open_corner_header();before=copy.deepcopy(self.model.state)
        logo=Path(self.temp.name)/'external-logo.png'
        logo.write_bytes((ROOT/'resources/demo_team_logos/demo_wave_teal_v1.png').read_bytes())
        dialog.top_text.setText('FINALS 27')
        with patch('corner_editor.QFileDialog.getOpenFileName',return_value=(str(logo),'PNG')):dialog.choose_logo_button.click()
        self.assertTrue(dialog.logo_reference.startswith('assets/imported/logos/'))
        self.assertTrue(resolve_media_path(self.model.path,dialog.logo_reference).is_relative_to(Path(self.temp.name)))
        before['broadcast']['config']['corner_logo']=dialog.logo_reference
        self.assertEqual(dialog.top_text.text(),'FINALS 27')
        self.assertEqual(dialog.dirty_sections(),['corner'])
        logo.unlink()
        restarted=Model(resolve_active_directory(self.store.root),asset_root=self.model.asset_root)
        self.assertEqual(restarted.state,before)
        self.assertIsNotNone(corner_logo(restarted,dialog.logo_reference))
        dialog.bottom_text.setText('Nov. 03');dialog.match.setText('12')
        dialog.top_size.setValue(87);dialog.top_tracking.setValue(3.2)
        dialog.bottom_size.setValue(39);dialog.bottom_tracking.setValue(1.8)
        self.assertEqual(self.model.state,before)
        self.assertEqual(dialog.preview.values['corner_top_size'],87)
        history=(len(self.model.undo),len(self.model.redo));dialog.apply(show=True)
        self.assertEqual((len(self.model.undo),len(self.model.redo)),history)
        config=self.model.state['broadcast']['config']
        self.assertEqual((config['corner_top_size'],config['corner_bottom_size']),(87,39))
        self.assertEqual((config['corner_top_tracking'],config['corner_bottom_tracking']),(3.2,1.8))
        self.assertTrue(self.manager.enabled['corner_header'])
        transfer=Path(self.temp.name)/'transfer.json';self.model.export_data(transfer)
        target=Model(Path(self.temp.name)/'imported');target.import_data(transfer)
        self.assertEqual(corner_texts(target.state['broadcast']['config']),('FINALS 27','Nov. 03'))
        self.assertEqual(target.state['broadcast']['config']['corner_match'],'12')
        self.assertEqual(target.state['broadcast']['config']['corner_top_tracking'],3.2)

    def test_clear_logo_saves_immediately_and_reports_write_failure(self):
        logo=ROOT/'resources/demo_team_logos/demo_wave_teal_v1.png'
        self.model.configure_corner_header({'corner_logo':str(logo)})
        dialog=self.ui.open_corner_header();saved=dialog.logo_reference
        with patch.object(self.model.repository,'save',side_effect=OSError('disk unavailable')):
            dialog.clear_logo_button.click()
        self.assertIn('disk unavailable',dialog.status.text())
        self.assertEqual(dialog.logo_reference,saved)
        self.assertEqual(dialog.dirty_sections(),[])
        dialog.clear_logo_button.click()
        self.assertEqual(dialog.logo_reference,'')
        self.assertEqual(dialog.dirty_sections(),[])
        restarted=Model(resolve_active_directory(self.store.root),asset_root=self.model.asset_root)
        self.assertEqual(restarted.state['broadcast']['config']['corner_logo'],'')

    def test_natural_upright_glyphs_keep_the_separate_large_match_and_short_horizontal_rule(self):
        import corner_header
        original=corner_header.draw_single_line;calls=[]
        def record(painter,value,box,font,*args,**kwargs):
            calls.append((value,box.width(),font.stretch(),font.italic(),font.letterSpacing(),font.pixelSize()))
            return original(painter,value,box,font,*args,**kwargs)
        def render():
            image=QImage(*CORNER_SIZE,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
            painter=QPainter(image)
            with patch('corner_header.draw_single_line',side_effect=record):draw_corner_header(painter,self.model)
            painter.end();return image
        image=render()
        self.assertEqual([call[0] for call in calls],['26–27','Oct. 29','#','1'])
        self.assertTrue(all(call[2:4]==(100,False) for call in calls))
        self.assertGreater(calls[3][5],calls[1][5])
        self.assertTrue(all(image.pixelColor(x,202).alpha()>180 for x in range(207,418)))
        self.assertEqual(image.pixelColor(470,202).alpha(),0)
        self.model.configure_corner_header({'corner_match':''});calls.clear();render()
        self.assertEqual([call[0] for call in calls],['26–27','Oct. 29'])
        self.assertGreater(calls[1][1],214)

    def test_editor_event_switch_discards_old_drafts_and_restores_the_events_own_values(self):
        self.model.configure_corner_header({'corner_top_text':'First event','corner_bottom_text':'Day one','corner_top_size':88,'corner_bottom_tracking':2.5})
        first=self.model.event['id'];dialog=self.ui.open_corner_header();dialog.top_text.setText('Unsaved first draft')
        self.store.create('Second event','individual_league',False)
        self.assertEqual(dialog.top_text.text(),'26–27')
        self.assertEqual(dialog.logo_reference,'')
        self.store.open(first)
        self.assertEqual(dialog.top_text.text(),'First event');self.assertEqual(dialog.top_size.value(),88)
        self.assertEqual(dialog.bottom_tracking.value(),2.5)

    def test_unedited_fields_keep_live_changes_and_same_field_conflicts_require_reload(self):
        dialog=self.ui.open_corner_header();dialog.top_size.setValue(90)
        self.ui.corner_bottom_text.setText('Live date');self.ui.corner_apply_button.click();dialog.apply()
        self.assertEqual(self.model.state['broadcast']['config']['corner_bottom_text'],'Live date')
        self.assertEqual(self.model.state['broadcast']['config']['corner_top_size'],90)
        dialog.top_text.setText('Editor draft');self.model.configure_corner_header({'corner_top_text':'External'})
        dialog.apply();self.assertIn('重新读取',dialog.status.text())
        self.assertEqual(self.model.state['broadcast']['config']['corner_top_text'],'External')

    def test_separate_main_rail_entries_font_shortcuts_animation_menu_and_native_viewports(self):
        self.assertTrue(self.ui.corner_group.isAncestorOf(self.ui.quick_corner))
        self.assertFalse(self.ui.presentation_group.isAncestorOf(self.ui.quick_corner))
        before=copy.deepcopy(self.model.state);history=(len(self.model.undo),len(self.model.redo))
        self.ui.font_larger_button.click();self.assertEqual(self.model.settings['console_font_size'],15)
        self.ui.font_smaller_button.click();self.assertEqual(self.model.settings['console_font_size'],14)
        self.assertEqual(self.model.state,before);self.assertEqual((len(self.model.undo),len(self.model.redo)),history)
        actions=self.ui.player_animation_menu.actions()
        with patch.object(self.manager,'replay_players',create=True) as replay_players:
            actions[0].trigger();actions[1].trigger()
            self.assertEqual([call.args for call in replay_players.call_args_list],[('entry',),('cycle',)])
        with patch.object(self.manager,'replay') as replay,patch.object(self.manager,'finish') as finish:
            actions[2].trigger();actions[3].trigger();replay.assert_called_once();finish.assert_called_once()
        self.model.configure_corner_header({'corner_match':'12'})
        self.assertFalse(hasattr(self.ui,'corner_top_text'))
        editor=self.ui.open_corner_header();editor.top_text.setText('NEW SEASON');editor.apply();editor.close()
        self.ui.corner_bottom_text.setText('NEW DATE');self.ui.corner_apply_button.click()
        self.assertEqual(self.model.state['broadcast']['config']['corner_top_text'],'NEW SEASON')
        self.assertEqual(self.model.state['broadcast']['config']['corner_match'],'12')
        for width,height in ((960,620),(1460,1000)):
            self.ui.resize(width,height);settle(APP)
            nav=self.ui.navigation_scroll;self.assertEqual(nav.horizontalScrollBar().maximum(),0)
            for widget in (self.ui.font_smaller_button,self.ui.font_larger_button,self.ui.player_animation_button,
                           self.ui.quick_corner,self.ui.corner_bottom_text,
                           self.ui.corner_apply_button,self.ui.corner_logo_button):
                reveal(widget,APP);self.assertTrue(visibility(widget,self.ui)['visible'],widget.objectName())
        editor=self.ui.open_corner_header()
        self.assertIs(self.ui.broadcast_panel().open_corner_editor(),editor)


if __name__=='__main__':unittest.main()
