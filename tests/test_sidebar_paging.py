"""Real clock-driven pagination without sleeps, score changes or page writes."""
import copy
import hashlib
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
from PySide6.QtGui import QImage,QPainter,QFontDatabase
from PySide6.QtWidgets import QApplication
from event_store import EventStore
from model import Model
from overlays import OverlayManager
from sidebar_paging import sidebar_page_data,sidebar_render_model,is_team_event

APP=QApplication.instance() or QApplication([])
for font in (Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/msyh.ttc',ROOT/'resources/NotoSansCJKsc-Regular.otf'):
    if font.is_file():QFontDatabase.addApplicationFont(str(font))


def digest(image):return hashlib.sha256(bytes(image.constBits())).hexdigest()


class SidebarPagingTests(unittest.TestCase):
    def setUp(self):
        self.now=1000.
        self.clock=patch('overlays.time.monotonic',side_effect=lambda:self.now);self.clock.start()
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests')
        self.model=Model(self.tmp.name);self.store=EventStore(self.model)
        roster=copy.deepcopy(self.model.state['broadcast']['roster'])
        for index in range(4,24):roster.append(dict(id=f'player-{index+1:02}',name=f'分页选手{index+1:02}',seed=index+1))
        for index,row in enumerate(roster):
            row.update(name=f'分页选手{index+1:02}',base_points=240-index*10,team=f'队伍{index%12+1:02}',team_id=f'team-{index%12+1:02}')
        self.model.configure_broadcast(dict(roster=roster,sidebar_mode='individual',sidebar_scope='all',sidebar_auto_page=True,sidebar_page_size=10,sidebar_page_interval=5))
        self.manager=OverlayManager(self.model);self.manager.apply_scene('qualification')

    def tearDown(self):
        self.manager.close();APP.processEvents();self.tmp.cleanup();self.clock.stop()

    def frame(self,static=False):
        image=QImage(400,650,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
        painter=QPainter(image)
        try:self.manager.draw_content(painter,'qualification',static=static)
        finally:painter.end()
        return image

    def at(self,seconds):
        self.now=1000.+seconds;self.manager.tick()

    def test_scope_keeps_real_global_ranks_and_current_four_ignores_page_size(self):
        data=sidebar_page_data(self.model)
        self.assertEqual((data['page'],data['pages'],len(data['rows'])),(0,3,10))
        self.assertEqual([row['rank'] for row in data['rows']],list(range(1,11)))
        self.assertEqual([row['rank'] for row in sidebar_page_data(self.model,page=2)['rows']],[21,22,23,24])
        selected=[self.model.state['broadcast']['roster'][index]['id'] for index in (2,9,17,23)]
        self.model.configure_broadcast(dict(seats=selected,sidebar_scope='current',sidebar_page_size=2))
        current=sidebar_page_data(self.model)
        self.assertEqual((current['pages'],len(current['rows'])),(1,4))
        self.assertEqual([row['rank'] for row in current['rows']],[3,10,18,24])

    def test_half_second_transition_renders_one_page_and_never_writes_data(self):
        before=copy.deepcopy(self.model.state);initial=digest(self.frame())
        with patch.object(self.model,'save') as save,patch.object(self.model.repository,'save') as repository_save:
            self.at(4.99);self.assertEqual(self.manager.current_sidebar_page(),0);self.assertIsNone(self.manager._sidebar_transition)
            self.at(5.)
            self.assertIsNotNone(self.manager._sidebar_transition)
            self.at(5.125)
            with patch.object(self.manager,'_sidebar_picture',wraps=self.manager._sidebar_picture) as picture:
                self.frame();self.assertEqual([call.args[0] for call in picture.call_args_list],[0])
            self.at(5.375)
            with patch.object(self.manager,'_sidebar_picture',wraps=self.manager._sidebar_picture) as picture:
                self.frame();self.assertEqual([call.args[0] for call in picture.call_args_list],[1])
            self.at(5.51)
            self.assertIsNone(self.manager._sidebar_transition);self.assertEqual(self.manager.current_sidebar_page(),1)
            self.assertNotEqual(initial,digest(self.frame()))
            self.assertFalse(self.manager.timer.isActive());self.assertTrue(self.manager.sidebar_timer.isActive())
            save.assert_not_called();repository_save.assert_not_called()
        self.assertEqual(self.model.state,before)

    def test_last_page_and_repeat_keep_the_same_window_geometry(self):
        geometry=self.manager.windows['qualification'].geometry()
        for begin,end,index in ((5.,5.51,1),(10.51,11.02,2),(16.02,16.53,0)):
            self.at(begin);self.at(end)
            self.assertEqual(self.manager.current_sidebar_page(),index)
            self.assertEqual(self.manager.windows['qualification'].geometry(),geometry)
            self.assertEqual(self.frame().size().width(),400)
            self.assertEqual(self.frame().size().height(),650)

    def test_manual_static_export_uses_requested_page_and_proxy_is_read_only(self):
        self.model.configure_broadcast(dict(sidebar_auto_page=False,sidebar_page=2))
        self.assertEqual(self.manager.current_sidebar_page(),2)
        before=copy.deepcopy(self.model.state)
        proxy=sidebar_render_model(self.model,1)
        self.assertEqual(sidebar_page_data(proxy)['page'],1)
        self.assertEqual(self.model.state,before)
        self.at(200.);self.assertEqual(self.manager.current_sidebar_page(),2)
        target=Path(self.tmp.name)/'current-page.svg';self.manager.export_svg('qualification',target)
        svg=target.read_text(encoding='utf-8')
        self.assertIn('分页选手21',svg);self.assertNotIn('分页选手01',svg)
        self.assertEqual(self.model.state,before)

    def test_scope_content_roster_and_event_changes_restart_at_first_page(self):
        self.at(5.);self.at(5.51);self.assertEqual(self.manager.current_sidebar_page(),1)
        self.model.configure_broadcast(dict(sidebar_scope='current'))
        self.assertEqual(self.manager.current_sidebar_page(),0);self.assertFalse(self.manager.sidebar_timer.isActive())
        self.model.configure_broadcast(dict(sidebar_scope='all'))
        self.assertEqual(self.manager.current_sidebar_page(),0)
        self.at(10.51);self.at(11.02);self.assertEqual(self.manager.current_sidebar_page(),1)
        roster=copy.deepcopy(self.model.state['broadcast']['roster']);roster[-1]['name']='新名册末席'
        self.model.set_roster(roster);self.assertEqual(self.manager.current_sidebar_page(),0)
        self.manager.set_sidebar_mode('team');self.assertEqual(self.manager.current_sidebar_page(),0)
        self.assertEqual(self.manager._sidebar_pages,2)
        self.store.create('另一个赛事','team_league')
        self.assertEqual(self.manager.current_sidebar_page(),0);self.assertIsNone(self.manager._sidebar_transition)

    def test_team_hides_top_strip_preserving_individual_switch_and_export_policy(self):
        self.manager.set_extension_visible('table_scores',True)
        self.assertTrue(self.manager.content_visible('table_scores'))
        original=self.model.event['id'];self.store.create('团队赛','team_league',copy_config=True)
        self.assertTrue(is_team_event(self.model));self.assertFalse(self.manager.content_visible('table_scores'))
        self.assertTrue(self.manager.enabled['table_scores'])
        image=QImage(820,120,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
        painter=QPainter(image)
        try:self.manager.draw_content(painter,'table_scores',static=True)
        finally:painter.end()
        self.assertEqual(bytes(image.constBits()),bytes(image.sizeInBytes()))
        with self.assertRaises(ValueError):self.manager.export_svg('table_scores',Path(self.tmp.name)/'team-strip.svg')
        self.store.open(original)
        self.model.state['broadcast']['config']['event_mode']='team'
        self.assertFalse(is_team_event(self.model));self.assertTrue(self.manager.content_visible('table_scores'))
        self.assertTrue(self.manager.enabled['table_scores'])

    def test_hiding_stops_page_work_and_reopening_starts_cleanly(self):
        self.at(5.);self.at(5.125);self.assertIsNotNone(self.manager._sidebar_transition)
        self.manager.apply_scene('clean')
        self.assertIsNone(self.manager._sidebar_transition);self.assertFalse(self.manager.sidebar_timer.isActive())
        self.at(50.);self.assertEqual(self.manager.current_sidebar_page(),0)
        self.manager.apply_scene('qualification');self.assertEqual(self.manager.current_sidebar_page(),0)
        self.assertTrue(self.manager.sidebar_timer.isActive())

    def test_an_early_native_wakeup_rearms_the_remaining_hold(self):
        self.now=1004.8;self.manager._wake_sidebar_page()
        self.assertIsNone(self.manager._sidebar_transition)
        self.assertTrue(self.manager.sidebar_timer.isActive())
        self.now=1005.;self.manager._wake_sidebar_page()
        self.assertIsNotNone(self.manager._sidebar_transition)
        self.assertTrue(self.manager.timer.isActive())

    def test_invalid_paging_configuration_is_transactional(self):
        before=copy.deepcopy(self.model.state)
        for patch_value in (dict(sidebar_scope='missing'),dict(sidebar_auto_page=1),dict(sidebar_page_interval=4),dict(sidebar_page_interval=61)):
            with self.subTest(patch=patch_value):
                with self.assertRaises(ValueError):self.model.configure_broadcast(patch_value)
                self.assertEqual(self.model.state,before)


if __name__=='__main__':unittest.main()
