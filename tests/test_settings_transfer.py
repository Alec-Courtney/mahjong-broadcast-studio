import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget
from event_store import EventStore
from event_workspace import EventWorkspaceDialog
from media_store import resolve_media_path
from model import Model
from settings_transfer import (
    export_event_settings, export_system_settings, import_event_settings, import_system_settings,
)

APP = QApplication.instance() or QApplication([])


class SettingsTransferTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='tmp-settings-transfer-', dir=ROOT/'tests')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = Model(self.root/'source')
        self.source_store = EventStore(self.source)
        self.source_store.create('设置来源', 'team_league')
        self.target = Model(self.root/'target')
        self.target_store = EventStore(self.target)
        self.target_store.create('目标赛事', 'team_league')

    def test_global_file_stays_global_and_preserves_the_target_live_animation(self):
        self.source.setting('console_dock_animations', True)
        self.source.setting('console_theme', 'light')
        self.source.setting('speed', 2.5)
        self.target.setting('speed', 1.25)
        filename = self.root/'global.json'
        export_system_settings(self.source, filename)
        self.assertNotIn('speed', json.loads(filename.read_text(encoding='utf-8'))['settings'])
        import_system_settings(self.target, filename)
        first = self.target.event['id']
        self.target_store.create('另一赛事', 'individual_league')
        self.assertTrue(self.target.settings['console_dock_animations'])
        self.assertEqual(self.target.settings['console_theme'], 'light')
        self.assertEqual(self.target.settings['speed'], 1.)
        self.target_store.open(first)
        self.assertEqual(self.target.settings['speed'], 1.25)
        self.assertTrue(Model(self.target.path).settings['console_dock_animations'])

    def test_event_file_brings_media_and_animation_to_new_device_without_copying_results(self):
        image = QImage(40, 30, QImage.Format.Format_ARGB32)
        image.fill(QColor('#318090'))
        artwork = self.root/'title.png'
        self.assertTrue(image.save(str(artwork)))
        self.source.configure_opening({'opening_title': str(artwork)})
        self.source.configure_postmatch({'postmatch_chart_hold': 23.})
        typography = {'commentary_title_font_family': 'Arial', 'commentary_body_font_size': 33,
                      'commentary_body_letter_spacing': 2.5}
        self.source.configure_commentary(typography)
        self.source.configure_broadcast({'start_points': 27000})
        self.source.setting('speed', 1.8)
        self.source.setting('console_theme', 'light')
        self.source.update(lambda state: state['players'][0].update(name='原赛事选手'))
        self.source.set_score(0, 35000)
        self.source.field('roundIndex', 7)
        self.source.field('dealer', 3)
        self.source.draw()
        self.source.matches.confirm_finished()
        filename = self.root/'event.json'
        export_event_settings(self.source, filename)

        self.target_store.create('文件创建的赛事', 'team_league', settings_file=filename)
        broadcast = self.target.state['broadcast']
        config = broadcast['config']
        self.assertEqual((config['postmatch_chart_hold'], self.target.settings['speed']), (23., 1.8))
        self.assertEqual({key: config[key] for key in typography}, typography)
        self.assertEqual(self.target.settings['console_theme'], 'dark')
        self.assertTrue(all(player['score']==27000 for player in self.target.state['players']))
        self.assertFalse(broadcast['matches'])
        self.assertFalse(broadcast['hands'])
        self.assertNotIn('原赛事选手', [player['name'] for player in broadcast['roster']])
        self.assertEqual(resolve_media_path(self.target.path, config['opening_title']).read_bytes(), artwork.read_bytes())

    def test_new_event_ui_can_choose_an_inactive_events_settings(self):
        first = self.source.event['id']
        self.source.setting('speed', 2.25)
        self.source.set_score(0, 30000)
        self.source_store.create('当前赛事', 'team_league')
        parent = QWidget()
        parent.model = self.source
        from types import SimpleNamespace
        parent.manager = SimpleNamespace(save_geometry=lambda: None)
        dialog = EventWorkspaceDialog(parent)
        try:
            dialog.settings_source.setCurrentIndex(dialog.settings_source.findData(first))
            dialog.new_name.setText('采用其他赛事设置')
            dialog.create_event()
            self.assertFalse(dialog._save_failed, dialog.status.text())
            self.assertEqual(self.source.settings['speed'], 2.25)
            self.assertEqual(self.source.state['players'][0]['score'], 25000)
        finally:
            dialog.deleteLater()
            parent.deleteLater()
            APP.processEvents()

    def test_event_import_preserves_current_ledger_and_validates_before_writing(self):
        self.source.configure_postmatch({'postmatch_chart_hold': 27.})
        self.target.configure_commentary({'commentary_body_font_size': 42})
        self.target.set_score(0, 35000)
        self.target.field('roundIndex', 7)
        self.target.field('dealer', 3)
        self.target.draw()
        self.target.matches.confirm_finished()
        original_event = self.target.event['id']
        ledger = copy.deepcopy(self.target.state['broadcast']['matches'])
        filename = self.root/'event.json'
        export_event_settings(self.source, filename)
        import_event_settings(self.target, filename)
        self.assertEqual(self.target.event['id'], original_event)
        self.assertEqual(self.target.state['broadcast']['matches'], ledger)
        self.assertEqual(self.target.state['broadcast']['config']['postmatch_chart_hold'], 27.)
        self.assertNotIn('commentary_body_font_size', self.target.state['broadcast']['config'])

        before = copy.deepcopy((self.target.state, self.target.event, self.target.settings))
        with patch.object(self.target.repository,'save',side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                import_event_settings(self.target, filename)
        self.assertEqual((self.target.state, self.target.event, self.target.settings), before)
        with self.assertRaises(ValueError):
            import_system_settings(self.target, filename)


if __name__ == '__main__':
    unittest.main()
