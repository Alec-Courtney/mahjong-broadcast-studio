"""The shortcut rail can force a smooth sidebar exit from every live state."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage,QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from console import Console
from model import Model
from overlays import OverlayManager
from sidebar_presets import default_sidebar_presets

APP=QApplication.instance() or QApplication([])


def pixels(image):return bytes(image.constBits())


class SidebarQuickCloseTests(unittest.TestCase):
    def setUp(self):
        self.now=1000.
        self.clock=patch('overlays.time.monotonic',side_effect=lambda:self.now);self.clock.start()
        (ROOT/'.cache').mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='sidebar-quick-close-',dir=ROOT/'.cache')
        self.model=Model(self.tmp.name)
        roster=copy.deepcopy(self.model.state['broadcast']['roster'])
        for index in range(len(roster),24):
            roster.append(dict(id=f'close-player-{index}',name=f'分页选手{index}',seed=index+1))
        self.model.configure_broadcast(dict(roster=roster,sidebar_mode='individual',sidebar_scope='all',
            sidebar_auto_page=True,sidebar_page_size=10,sidebar_page_interval=5,sidebar_duration=15))
        self.model.settings.update(show_overlays=True,topmost=False)
        self.manager=OverlayManager(self.model);self.manager.set_sidebar_mode('individual',True)
        self.main=None

    def tearDown(self):
        if self.main is not None:
            self.main.close();self.main.deleteLater()
        else:self.manager.close()
        APP.processEvents();self.tmp.cleanup();self.clock.stop()

    def at(self,seconds):
        self.now=1000.+seconds;self.manager.tick()

    def frame(self):
        image=QImage(*self.manager.base_size('qualification'),QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent);painter=QPainter(image)
        try:self.manager.paint_content(painter,'qualification')
        finally:painter.end()
        return image

    def assert_no_pending_work(self):
        self.assertFalse(self.manager.sidebar_timer.isActive())
        self.assertFalse(self.manager.sidebar_hide_timer.isActive())
        self.assertFalse(self.manager._sidebar_auto_active)
        self.assertIsNone(self.manager._sidebar_transition)
        self.assertIsNone(self.manager._sidebar_deadline)
        self.assertIsNone(self.manager.sidebar_hide_deadline)

    def test_top_shortcut_button_plays_exit_and_stays_closed_after_refresh(self):
        self.main=Console(self.model,self.manager);self.main.resize(960,620);self.main.show()
        self.at(1.)
        control=self.main.sidebar_close_button
        self.assertIs(self.main.sidebar_presets_layout.itemAt(0).widget(),control)
        self.assertEqual(control.text(),'关闭侧栏');self.assertTrue(control.isEnabled())
        self.main.navigation_scroll.ensureWidgetVisible(control);APP.processEvents()
        before=self.frame();state=copy.deepcopy((self.model.state,self.model.undo,self.model.redo))
        QTest.mouseClick(control,Qt.MouseButton.LeftButton)
        self.assertFalse(self.manager.enabled['qualification'])
        self.assertFalse(self.manager._sidebar_motion.target)
        self.assertTrue(self.manager.content_visible('qualification'))
        self.assertEqual(pixels(self.frame()),pixels(before))
        self.assertFalse(control.isEnabled());self.assert_no_pending_work()
        self.assertEqual((self.model.state,self.model.undo,self.model.redo),state)
        self.at(1.1);self.assertTrue(self.manager.content_visible('qualification'))
        self.assertGreater(self.manager._sidebar_motion.frame(self.now).opacity,0.)
        self.assertLess(self.manager._sidebar_motion.frame(self.now).opacity,1.)
        self.at(1.25);self.assertFalse(self.manager.content_visible('qualification'))
        self.model.field('honba',1);self.manager.on_display()
        self.assertFalse(self.manager.enabled['qualification'])
        self.assertFalse(self.manager.content_visible('qualification'))
        self.assertFalse(Model(self.tmp.name).settings['visible']['qualification'])

    def test_page_change_is_frozen_and_pending_page_does_not_resume(self):
        self.at(5.);self.at(5.125)
        self.assertIsNotNone(self.manager._sidebar_transition)
        before=self.frame();page_frame=self.manager._sidebar_page_frame(self.now)
        self.assertGreater(page_frame[1],0.);self.assertLess(page_frame[1],1.)
        self.manager.force_sidebar_exit()
        self.assertEqual(self.manager._sidebar_exit_page,page_frame)
        self.assertEqual(pixels(self.frame()),pixels(before));self.assert_no_pending_work()
        self.at(5.225);self.assertTrue(self.manager.content_visible('qualification'))
        self.assertIsNone(self.manager._sidebar_transition)
        self.at(5.4);self.manager.on_display();self.assertFalse(self.manager.content_visible('qualification'))
        self.manager.apply_sidebar_preset('personal-auto');self.at(6.5)
        self.assertTrue(self.manager._sidebar_motion.target)
        self.assertIsNone(getattr(self.manager,'_sidebar_exit_page',None))
        self.assertTrue(self.manager.sidebar_timer.isActive())

    def test_close_wins_over_an_imminent_timeout_and_paging_wakeup(self):
        self.model.configure_sidebar({'sidebar_duration':5})
        self.at(4.999);self.manager.force_sidebar_exit();self.assert_no_pending_work()
        self.now=1005.;self.manager.sidebar_hide_timer.timeout.emit();self.manager.sidebar_timer.timeout.emit()
        self.assertFalse(self.manager.enabled['qualification'])
        self.assertFalse(self.manager._sidebar_motion.target)
        self.assertTrue(self.manager.content_visible('qualification'))
        self.at(5.25);self.assertFalse(self.manager.content_visible('qualification'))

    def test_failed_save_preserves_entry_and_timers_for_retry(self):
        self.at(.3);before=copy.deepcopy((self.model.state,self.model.settings))
        frame=self.manager._sidebar_motion.frame(self.now);deadline=self.manager.sidebar_hide_deadline
        with patch.object(self.model.repository,'save',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):self.manager.force_sidebar_exit()
        self.assertEqual((self.model.state,self.model.settings),before)
        self.assertTrue(self.manager._sidebar_motion.target)
        self.assertEqual(self.manager._sidebar_motion.frame(self.now),frame)
        self.assertEqual(self.manager.sidebar_hide_deadline,deadline)
        self.assertTrue(self.manager.sidebar_hide_timer.isActive())
        self.assertTrue(self.manager.force_sidebar_exit())

    def test_close_button_remains_when_shortcut_list_is_empty_and_hidden_is_harmless(self):
        self.main=Console(self.model,self.manager)
        self.manager.save_sidebar_presets([]);self.main.refresh_sidebar_presets()
        self.assertEqual(self.main.sidebar_preset_buttons,{})
        self.assertIs(self.main.sidebar_presets_layout.itemAt(0).widget(),self.main.sidebar_close_button)
        self.at(1.);self.main.sidebar_close_button.click();self.at(1.3)
        self.assertFalse(self.main.sidebar_close_button.isEnabled())
        with patch.object(self.model.repository,'save') as save:
            self.assertFalse(self.manager.force_sidebar_exit());save.assert_not_called()

    def test_conditions_shortcut_exits_its_target_without_stopping_other_panels(self):
        self.model.setting('split_fullscreen_windows',True)
        presets=default_sidebar_presets();conditions=copy.deepcopy(presets[0])
        conditions.update(id='four-conditions-shortcut',title='四人条件')
        conditions['settings'].update(sidebar_mode='qualification',qualification_layout='four')
        self.manager.save_sidebar_presets(presets+[conditions])
        self.manager.apply_sidebar_preset(conditions['id'])
        self.manager.set_extension_visible('chart',True);self.at(1.)
        self.assertTrue(self.manager.enabled['four_conditions']);self.assertTrue(self.manager.enabled['chart'])
        self.manager.force_sidebar_exit()
        self.assertFalse(self.manager.enabled['four_conditions']);self.assertTrue(self.manager.enabled['chart'])
        self.assertTrue(self.manager.content_visible('four_conditions'))
        self.at(1.4);self.assertFalse(self.manager.content_visible('four_conditions'))
        self.assertTrue(self.manager.enabled['chart'])

    def test_paused_conditions_shortcut_still_plays_its_exit(self):
        conditions=copy.deepcopy(default_sidebar_presets()[0])
        conditions.update(id='four-conditions-shortcut',title='四人条件')
        conditions['settings'].update(sidebar_mode='qualification',qualification_layout='four')
        self.manager.save_sidebar_presets([conditions])
        self.manager.apply_sidebar_preset(conditions['id']);self.at(1.)
        self.manager.programme.pause_resume()
        self.assertTrue(self.manager.programme.paused)
        self.assertEqual(self.manager.programme.frozen_kind,'four_conditions')
        self.manager.force_sidebar_exit()
        self.assertFalse(self.manager.programme.paused)
        self.assertIsNone(self.manager.programme.frozen_frame)
        self.assertTrue(self.manager.content_visible('four_conditions'))
        self.at(1.1);self.assertGreater(self.manager.extension_opacity('four_conditions'),0.)
        self.assertLess(self.manager.extension_opacity('four_conditions'),1.)
        self.at(1.4);self.assertFalse(self.manager.content_visible('four_conditions'))


if __name__=='__main__':unittest.main()
