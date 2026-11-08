"""Visible sidebars can always be dismissed and expire independently of paging."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtWidgets import QApplication,QWidget
from broadcast_console import BroadcastDialog
from model import Model
from overlays import OverlayManager

APP=QApplication.instance() or QApplication([])


class SidebarDismissalTests(unittest.TestCase):
    def setUp(self):
        self.now=1000.
        self.clock=patch('overlays.time.monotonic',side_effect=lambda:self.now);self.clock.start()
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.model=Model(self.tmp.name)
        self.model.configure_sidebar({'sidebar_mode':'individual','sidebar_duration':15})
        self.manager=OverlayManager(self.model)
        self.manager.set_sidebar_mode('individual',True)

    def tearDown(self):
        self.manager.close();APP.processEvents();self.tmp.cleanup();self.clock.stop()

    def test_model_refresh_does_not_extend_timeout_and_expiry_stays_closed(self):
        deadline=self.manager.sidebar_hide_deadline
        self.assertEqual(deadline,1015.)
        for second in (1005.,1010.,1014.):
            self.now=second;self.model.field('honba',int(second-1000));self.manager.on_display()
            self.assertEqual(self.manager.sidebar_hide_deadline,deadline)
        before=copy.deepcopy((self.model.state,self.model.undo,self.model.redo))
        self.now=deadline;self.manager.sidebar_hide_timer.timeout.emit()
        self.assertFalse(self.manager.enabled['qualification'])
        self.assertIsNone(self.manager.sidebar_hide_deadline)
        self.assertFalse(self.manager.sidebar_timer.isActive())
        self.now+=.25;self.manager.tick();self.manager.on_display()
        self.assertFalse(self.manager.content_visible('qualification'))
        self.assertEqual((self.model.state,self.model.undo,self.model.redo),before)
        self.assertFalse(Model(self.tmp.name).settings['visible']['qualification'])

    def test_zero_duration_persists_and_duration_change_rearms_existing_timer(self):
        self.now=1003.;self.model.configure_sidebar({'sidebar_duration':25})
        self.assertEqual(self.manager.sidebar_hide_deadline,1028.)
        self.assertTrue(self.manager.sidebar_hide_timer.isActive())
        self.model.configure_sidebar({'sidebar_duration':0})
        self.assertIsNone(self.manager.sidebar_hide_deadline)
        self.assertFalse(self.manager.sidebar_hide_timer.isActive())
        self.now+=1000.;self.manager._expire_sidebar_timeout()
        self.assertTrue(self.manager.enabled['qualification'])
        self.assertEqual(Model(self.tmp.name).state['broadcast']['config']['sidebar_duration'],0)

    def test_same_visible_shortcut_closes_without_runtime_preset_identity(self):
        self.manager.apply_sidebar_preset('personal-home')
        self.manager._active_sidebar_preset_id=None
        self.manager._active_sidebar_preset_event='previous-event'
        self.manager.toggle_sidebar_preset('personal-home')
        self.assertFalse(self.manager.enabled['qualification'])
        self.now+=.25;self.manager.tick();self.manager.on_display()
        self.assertFalse(self.manager.content_visible('qualification'))
        self.manager.toggle_sidebar_preset('personal-home')
        self.assertTrue(self.manager.enabled['qualification'])
        self.assertEqual(self.manager.sidebar_hide_deadline,self.now+15)

    def test_failed_timeout_save_keeps_visible_view_and_can_retry(self):
        self.now=self.manager.sidebar_hide_deadline
        before=copy.deepcopy((self.model.state,self.model.settings,self.model.undo,self.model.redo))
        with patch.object(self.model.repository,'save',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):self.manager._expire_sidebar_timeout()
        self.assertTrue(self.manager.enabled['qualification'])
        self.assertEqual((self.model.state,self.model.settings,self.model.undo,self.model.redo),before)
        self.assertEqual(self.manager.sidebar_hide_deadline,self.now)
        self.assertTrue(self.manager.sidebar_hide_timer.isActive())
        self.manager._expire_sidebar_timeout()
        self.assertFalse(self.manager.enabled['qualification'])

    def test_settings_offer_explicit_close_and_continuous_display(self):
        parent=QWidget();parent.model=self.model;parent.manager=self.manager
        dialog=BroadcastDialog(parent)
        try:
            self.assertEqual(dialog.sidebar_duration.value(),15)
            dialog.sidebar_duration.setValue(0)
            self.assertEqual(self.model.state['broadcast']['config']['sidebar_duration'],0)
            self.assertTrue(dialog.sidebar_close_button.isEnabled())
            dialog.sidebar_close_button.click()
            self.assertFalse(self.manager.enabled['qualification'])
            dialog.refresh_live()
            self.assertFalse(dialog.sidebar_close_button.isEnabled())
        finally:
            dialog.hide();dialog.deleteLater();parent.deleteLater();APP.processEvents()

    def test_closing_manager_stops_sidebar_timeout(self):
        self.assertTrue(self.manager.sidebar_hide_timer.isActive())
        self.manager.close()
        self.assertFalse(self.manager.sidebar_hide_timer.isActive())
        self.assertIsNone(self.manager.sidebar_hide_deadline)


if __name__=='__main__':unittest.main()
