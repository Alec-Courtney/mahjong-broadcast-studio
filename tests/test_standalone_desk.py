"""The UI-only entry point keeps its match state apart from the event library."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from console import Console
from overlays import OverlayManager
from run import create_model

APP = QApplication.instance() or QApplication([])


class StandaloneDeskTests(unittest.TestCase):
    def test_managed_startup_reads_active_event_instead_of_an_obsolete_root_session(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
            root = Path(folder)
            model = create_model(root)
            model.set_score(0, 41000)
            identity = model.event['id']
            (root/'session.json').write_text('broken obsolete session', encoding='utf-8')
            reopened = create_model(root)
            self.assertEqual(reopened.event['id'], identity)
            self.assertEqual(reopened.state['players'][0]['score'], 41000)
            self.assertEqual((root/'session.json').read_text(encoding='utf-8'), 'broken obsolete session')

    def test_ui_only_starts_without_touching_even_a_broken_library(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
            root = Path(folder)
            (root/'library.json').write_text('broken library', encoding='utf-8')
            model = create_model(root, ui_only=True)
            self.assertTrue(model.ui_only)
            self.assertIsNone(model.event_store)
            self.assertIsNone(model.event)
            model.set_score(0, 42000)
            model.setting('console_theme', 'light')
            model.save()
            restarted = create_model(root, ui_only=True)
            self.assertEqual(restarted.state['players'][0]['score'], 42000)
            self.assertEqual(restarted.settings['console_theme'], 'light')
            self.assertEqual((root/'library.json').read_text(encoding='utf-8'), 'broken library')
            self.assertFalse((root/'events').exists())

    def test_ui_only_shows_table_controls_without_event_navigation(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
            model = create_model(Path(folder), ui_only=True)
            model.settings['show_overlays'] = False
            manager = OverlayManager(model)
            main = Console(model, manager)
            try:
                for key in ('events', 'reports', 'event_settings', 'commit'):
                    self.assertTrue(main.navigation_buttons[key].isHidden())
                self.assertFalse(main.navigation_buttons['new_match'].isHidden())
                self.assertIn('独立导播', main.event_label.text())
                main.new_event_match()
                self.assertIsNone(model.event_store)
                self.assertFalse((Path(folder)/'library.json').exists())
            finally:
                main.workspace.dispose(); main.shortcuts.dispose(); main.language_controller.dispose()
                APP.removeEventFilter(main)
                main.hide(); main.deleteLater(); manager.close()
                APP.sendPostedEvents(None, QEvent.Type.DeferredDelete); APP.processEvents()


if __name__ == '__main__':
    unittest.main()
