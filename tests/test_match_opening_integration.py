"""The real opening button uses portable event data and returns to the table."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor,QImage
from PySide6.QtWidgets import QApplication,QWidget
from broadcast_console import BroadcastDialog
from event_store import EventStore
from media_store import resolve_media_path
from model import Model
from overlays import OverlayManager
from broadcast_test_support import initialize_fonts,review_model
from team_match_demo_plan import extend_demo_roster

APP=QApplication.instance() or QApplication([])
FAMILY=initialize_fonts(APP)


class MatchOpeningIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='tmp-opening-',dir=ROOT/'tests')
        self.root=Path(self.tmp.name)
        self.model=review_model(self.root/'first',FAMILY);extend_demo_roster(self.model)
        self.model.settings['broadcast_scene']='table'
        self.model.settings['visible'].update({f'player{i}':True for i in range(4)})
        self.manager=None

    def tearDown(self):
        if self.manager:self.manager.close()
        APP.processEvents();self.tmp.cleanup()

    def title(self):
        path=self.root/'local-title.png'
        image=QImage(320,180,QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor('#e91d46'));image.save(str(path));return path

    def playback(self):
        self.manager=OverlayManager(self.model);return self.manager.opening

    def test_local_title_is_copied_and_exported_without_original_path(self):
        path=self.title();self.model.configure_opening({'opening_title':str(path)})
        reference=self.model.state['broadcast']['config']['opening_title']
        self.assertTrue(reference.startswith('assets/imported/backgrounds/'))
        self.assertEqual(resolve_media_path(self.model.path,reference).read_bytes(),path.read_bytes())
        transfer=self.root/'portable.json';self.model.export_data(transfer);path.unlink()
        target=Model(self.root/'second');target.import_data(transfer)
        self.assertTrue(resolve_media_path(target.path,reference).is_file())
        from match_opening import prepare_opening
        snapshot=prepare_opening(target)
        self.assertTrue(snapshot.title_image.png)
        self.assertEqual([len(team.members) for team in snapshot.teams],[4]*4)

    def test_setting_preserves_undo_redo_and_failed_save_is_atomic(self):
        self.model.set_score(0,31000);self.model.history('undo')
        lengths=(len(self.model.undo),len(self.model.redo))
        transition=copy.deepcopy(self.model.last_transition)
        self.model.configure_opening({'opening_title':str(self.title())})
        reference=self.model.state['broadcast']['config']['opening_title']
        self.assertEqual((len(self.model.undo),len(self.model.redo)),lengths)
        self.assertEqual(self.model.last_transition,transition)
        self.model.history('redo');self.assertEqual(self.model.state['broadcast']['config']['opening_title'],reference)
        before=copy.deepcopy((self.model.state,self.model.undo,self.model.redo))
        with patch.object(self.model.repository,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.model.configure_opening({'opening_title':''})
        self.assertEqual((self.model.state,self.model.undo,self.model.redo),before)
        for value in (None,12,'bad\npath'):
            with self.assertRaises(ValueError):self.model.configure_opening({'opening_title':value})

    def test_event_switch_restores_its_own_title(self):
        first=self.model.event['id'];self.model.configure_opening({'opening_title':str(self.title())})
        reference=self.model.state['broadcast']['config']['opening_title']
        store=self.model.event_store;second=store.create('另一场赛事','team_league')
        self.assertEqual(self.model.state['broadcast']['config']['opening_title'],'')
        store.open(first);self.assertEqual(self.model.state['broadcast']['config']['opening_title'],reference)

    def test_default_cover_reads_the_supplied_root_png_without_mutating_event_data(self):
        from match_opening import prepare_opening
        from opening_config import DEFAULT_TITLE_FILE
        cover=self.root/DEFAULT_TITLE_FILE;cover.write_bytes(self.title().read_bytes())
        model=Model(self.root/'default-event',asset_root=self.root);before=copy.deepcopy(model.state)
        snapshot=prepare_opening(model,require_distinct_teams=False)
        self.assertEqual(snapshot.title_reference,str(cover))
        self.assertTrue(snapshot.title_image.png)
        self.assertFalse(QImage.fromData(snapshot.title_image.png).isNull())
        self.assertEqual(model.state,before)

    def test_text_choice_is_saved_and_selecting_an_image_or_default_clears_it(self):
        self.model.configure_opening({'opening_title':'','opening_title_text_only':True})
        self.assertTrue(Model(self.model.path).state['broadcast']['config']['opening_title_text_only'])
        self.model.configure_opening({'opening_title':self.title()})
        self.assertFalse(self.model.state['broadcast']['config']['opening_title_text_only'])
        self.model.configure_opening({'opening_title':'','opening_title_text_only':True})
        self.model.configure_opening({'opening_title':''})
        self.assertFalse(self.model.state['broadcast']['config']['opening_title_text_only'])
        with self.assertRaises(ValueError):self.model.configure_opening({'opening_title_text_only':1})

    def test_playback_hides_live_windows_then_enters_four_players_without_ledger_changes(self):
        player=self.playback();before=copy.deepcopy((self.model.state,self.model.undo,self.manager.enabled))
        visible={key:self.manager.content_visible(key) for key in self.manager.windows}
        with patch('match_opening_player.time.monotonic',return_value=100.):player.start()
        self.assertTrue(player.active);self.assertFalse(any(self.manager.content_visible(key) for key in self.manager.windows))
        self.assertEqual([len(team.members) for team in player.snapshot.teams],[4]*4)
        with patch('match_opening_player.time.monotonic',return_value=108.):
            self.assertEqual(player.frame().size().width(),1920)
        with patch('match_opening_player.time.monotonic',return_value=118.01):player._tick()
        self.assertFalse(player.active);self.assertEqual(self.manager.stage,'players_demo_in')
        self.assertEqual(visible,{key:self.manager.content_visible(key) for key in self.manager.windows})
        self.assertEqual((self.model.state,self.model.undo,self.manager.enabled),before)

    def test_live_riichi_interrupts_opening_before_normal_declaration_sequence(self):
        opening_score=self.model.state['players'][0]['score']
        player=self.playback();player.start();self.model.riichi(0)
        self.assertFalse(player.active);self.assertTrue(self.manager.riichi_queue.busy)
        self.assertEqual(self.model.state['pot'],1000)
        self.assertEqual(self.model.state['players'][0]['score'],opening_score-1000)
        self.assertFalse(self.manager.art.model.state['players'][0]['riichi'])
        with self.assertRaises(ValueError):player.start()

    def test_global_hide_and_stop_restore_visibility_preferences(self):
        player=self.playback();enabled=copy.deepcopy(self.manager.enabled);player.start()
        self.manager.set_overlays_visible(False)
        self.assertFalse(player.active);self.assertEqual(self.manager.enabled,enabled)
        self.manager.set_overlays_visible(True);player.start();player.stop()
        self.assertFalse(player.active);self.assertEqual(self.manager.enabled,enabled)
        self.model.settings['windows']['opening']=[20,30,800,450]
        self.manager.save_geometry();self.assertEqual(self.model.settings['windows']['opening'],[20,30,800,450])

    def test_real_broadcast_controls_import_and_play_the_current_lineup(self):
        player=self.playback();parent=QWidget();parent.model=self.model;parent.manager=self.manager
        dialog=BroadcastDialog(parent)
        try:
            with patch('broadcast_console.QFileDialog.getOpenFileName',return_value=(str(self.title()),'PNG')):
                dialog.choose_opening_title()
            self.assertIn('已保存到项目',dialog.opening_title_label.text())
            dialog.opening_play.click();self.assertTrue(player.active)
            self.assertFalse(dialog.opening_play.isEnabled());self.assertTrue(dialog.opening_stop.isEnabled())
            dialog.opening_stop.click();self.assertFalse(player.active)
            self.assertEqual(sum(member.selected for team in player.snapshot.teams for member in team.members),4)
        finally:
            dialog.hide();dialog.deleteLater();parent.deleteLater();APP.processEvents()


if __name__=='__main__':unittest.main()
