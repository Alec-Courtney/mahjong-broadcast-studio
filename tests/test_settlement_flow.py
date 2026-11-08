"""Only explicitly archived half-games show final pt; small hands never project totals."""
import copy,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QRect,Qt
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QApplication
import overlays
from model import Model
from overlays import OverlayManager

class SettlementFlowTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
 def setUp(self):
  self.now=100.;self.clock=patch('overlays.time.monotonic',side_effect=lambda:self.now);self.clock.start()
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests',prefix='tmp-settlement-')
  self.model=Model(self.tmp.name);self.manager=OverlayManager(self.model)
  self.model.configure_broadcast({'settlement_hold':1.,'settlement_total_hold':1.})
  self.manager.on_display()
 def tearDown(self):self.manager.close();self.tmp.cleanup();self.clock.stop()
 def advance(self,seconds):self.now+=seconds/self.manager.speed;self.manager.tick()
 def ron(self):
  self.model.settle({'type':'ron','winner':1,'loser':0,'han':3,'fu':40},
                    {'winner':1,'name':self.model.state['players'][1]['name'],'han':3,'fu':40,'yaku':['立直'],'base_points':5200})
 def final_ron(self):
  self.model.field('roundIndex',7);self.model.field('dealer',3)
  self.ron();self.model.commit_match()
 def to_hand(self):
  self.advance(self.manager.animation.duration_ms/1000+.01)
  if self.manager.stage=='players_out':
   self.advance(.46);self.advance(self.manager.art.win_content_duration()+self.model.settings.get('win_hold',4.)+.01);self.advance(.46)
  else:self.advance(self.model.settings.get('win_hold',4.)+.01)
  self.assertEqual(self.manager.stage,'players_in')
  self.manager.set_bottom_mode('match_result')
  self.assertEqual(self.manager.stage,'settlement_hand')
 def test_individual_forces_two_views_then_restores_visibility_and_positions(self):
  self.manager.set_visible('player2',False)
  self.manager.set_extension_visible('qualification',False);self.manager.set_extension_visible('standings',False)
  original={key:window.geometry() for key,window in self.manager.windows.items()}
  visible=copy.deepcopy(self.model.settings['visible'])
  self.final_ron();self.to_hand();paid=copy.deepcopy(self.model.state)
  self.assertTrue(self.manager.content_visible('standings'))
  self.assertFalse(any(self.manager.content_visible(f'player{i}') for i in range(4)))
  self.assertFalse(self.manager.content_visible('yaku'))
  self.assertEqual(self.manager.window_label('standings'),'半庄最终积分')
  self.manager.set_extension_visible('standings',False)
  self.assertTrue(self.manager.content_visible('standings'))
  self.advance(1.01)
  self.assertEqual(self.manager.stage,'settlement_hand')
  self.manager.set_bottom_mode('total_result')
  self.assertEqual(self.manager.stage,'settlement_total');self.assertEqual(self.manager.settlement_window(),'qualification')
  self.assertEqual(self.manager.settlement_progress(),1.)
  self.assertTrue(self.manager.content_visible('qualification'));self.assertFalse(self.manager.content_visible('standings'))
  self.assertEqual(self.model.state,paid)
  self.advance(.99);self.assertEqual(self.model.state['phase'],'finished')
  self.advance(.02);self.assertEqual(self.manager.stage,'settlement_total')
  self.model.start_match();self.assertEqual(self.model.state['phase'],'playing')
  self.assertEqual(self.model.settings['visible'],visible)
  self.advance(.46)
  self.assertFalse(self.manager.content_visible('player2'))
  self.assertFalse(self.manager.content_visible('qualification'))
  for key,geometry in original.items():self.assertEqual(self.manager.windows[key].geometry(),geometry,key)
 def test_team_uses_four_slots_and_total_animation_without_changing_ledger(self):
  capture_windows={key:int(window.winId()) for key,window in self.manager.windows.items()}
  self.model.configure_broadcast({'event_mode':'team'})
  self.final_ron();self.to_hand();paid=copy.deepcopy(self.model.state)
  self.assertEqual(len(self.manager.settlement_data['players']),4)
  self.assertEqual(len(self.manager.settlement_data['standings']),4)
  self.assertEqual([r['seat'] for r in self.manager.settlement_data['standings']],[0,1,2,3])
  self.advance(1.01);self.assertEqual(self.manager.stage,'settlement_hand')
  self.manager.set_bottom_mode('total_result');self.assertEqual(self.manager.settlement_window(),'standings')
  self.assertEqual(self.manager.window_label('standings'),'队伍总积分变动')
  self.assertEqual(self.manager.settlement_progress(),0.)
  self.advance(.45);self.assertAlmostEqual(self.manager.settlement_progress(),.5)
  svg=Path(self.tmp.name)/'settlement.svg';self.manager.export_svg('standings',svg)
  self.assertIn('队伍总积分变动',svg.read_text(encoding='utf-8'))
  self.assertEqual(self.model.state,paid)
  self.assertEqual({key:int(window.winId()) for key,window in self.manager.windows.items()},capture_windows)
 def test_replay_during_new_stage_replays_win_and_never_pays_or_records_twice(self):
  self.final_ron();self.to_hand();paid=copy.deepcopy(self.model.state);summary=copy.deepcopy(self.manager.settlement_data)
  self.manager.replay()
  self.assertEqual(self.model.state,paid);self.assertTrue(self.model.settings['show_yaku'])
  self.assertEqual(self.manager.stage,'table');self.advance(self.manager.animation.duration_ms/1000+.01);self.advance(.46)
  self.assertTrue(self.manager.content_visible('yaku'))
  self.advance(self.manager.art.win_content_duration()+4.01);self.advance(.46)
  self.assertEqual(self.manager.stage,'players_in');self.manager.set_bottom_mode('match_result')
  self.assertEqual(self.manager.stage,'settlement_hand')
  self.assertEqual(self.manager.settlement_data,summary)
  self.assertEqual(self.model.state,paid)
 def test_small_draws_never_project_pt_even_with_final_results_enabled(self):
  with patch('overlays.final_settlement_summary',wraps=overlays.final_settlement_summary) as summary:
   self.model.riichi(2);self.model.draw();self.advance(self.manager.animation.duration_ms/1000+.01)
   self.assertEqual(self.manager.stage,'draw_hold');self.assertIsNone(self.manager.settlement_data)
   self.advance(4.01);self.assertEqual(self.model.state['phase'],'playing')
   self.model.configure_broadcast({'settlement_enabled':False})
   self.model.draw();self.advance(self.manager.animation.duration_ms/1000+.01);self.advance(4.01)
   self.assertEqual(self.manager.stage,'players_in');self.assertEqual(self.model.state['phase'],'playing')
   summary.assert_not_called()
 def test_small_win_replay_import_restart_and_next_hand_never_calculate_pt(self):
  with patch('overlays.final_settlement_summary',wraps=overlays.final_settlement_summary) as summary:
   self.ron();paid=copy.deepcopy(self.model.state);self.manager.replay()
   self.assertEqual(self.model.state,paid);self.assertIsNone(self.manager.settlement_data)
   export=Path(self.tmp.name)/'small-result.json';self.model.export_data(export)
   self.advance(self.manager.animation.duration_ms/1000+.01);self.advance(.46);self.advance(self.manager.art.win_content_duration()+4.01);self.advance(.46)
   self.assertEqual(self.manager.stage,'players_in');self.assertEqual(self.model.state['phase'],'playing')
   self.model.import_data(export);self.assertIsNone(self.manager.settlement_data)
   self.manager.close();self.model=Model(self.tmp.name);self.manager=OverlayManager(self.model);self.manager.on_display()
   self.assertIsNone(self.manager.settlement_data);self.model.next_hand()
   self.assertEqual(self.model.state['phase'],'playing');summary.assert_not_called()
 def test_terminal_requires_explicit_archive_and_identical_commit_does_not_replay(self):
  self.model.field('roundIndex',7);self.model.field('dealer',3)
  with patch('overlays.final_settlement_summary',wraps=overlays.final_settlement_summary) as summary:
   self.ron();self.advance(self.manager.animation.duration_ms/1000+.01);self.advance(.46);self.advance(self.manager.art.win_content_duration()+4.01)
   self.assertEqual(self.manager.stage,'win_out');self.assertIsNone(self.manager.settlement_data);summary.assert_not_called()
   self.model.commit_match();self.assertEqual(summary.call_count,1)
   self.assertEqual(self.manager.stage,'win_out');self.advance(.46)
   self.assertEqual(self.manager.stage,'players_in');self.manager.set_bottom_mode('match_result')
   self.assertEqual(self.manager.stage,'settlement_hand');started=self.manager.stage_started
   archived=copy.deepcopy(self.model.state['broadcast']['matches'])
   self.model.commit_match();self.assertEqual(summary.call_count,1);self.assertEqual(self.manager.stage_started,started)
   self.manager.set_bottom_mode('total_result');self.advance(1.01);self.advance(1.01);started=self.manager.stage_started
   self.model.commit_match();self.assertEqual(self.manager.stage,'settlement_total');self.assertEqual(self.manager.stage_started,started)
   self.assertEqual(self.model.state['broadcast']['matches'],archived)
   self.model.update(lambda state:(state['players'][1].update(score=state['players'][1]['score']+1000),state['players'][0].update(score=state['players'][0]['score']-1000)))
   self.assertEqual(summary.call_count,1)
   self.model.commit_match()
   self.assertEqual(summary.call_count,2);self.assertEqual(self.manager.stage,'settlement_total')
 def test_disabling_final_results_restores_previous_bottom_geometry_and_visibility(self):
  self.manager.set_bottom_mode('team');self.advance(.4)
  original=QRect(90,190,1030,120);self.manager.windows['standings'].setGeometry(original);self.manager.save_geometry()
  visible=copy.deepcopy(self.model.settings['visible'])
  self.final_ron();self.to_hand();self.manager.set_bottom_mode('total_result');self.advance(2.01)
  self.assertEqual(self.manager.stage,'settlement_total')
  self.model.configure_broadcast({'settlement_enabled':False})
  self.assertIsNone(self.manager.settlement_window());self.assertIsNone(self.manager.settlement_data)
  self.assertEqual(self.manager.stage,'table');self.assertEqual(self.model.settings['visible'],visible)
  self.assertEqual(self.manager.windows['standings'].geometry(),original)
  self.assertFalse(self.manager.content_visible('standings'));self.assertFalse(self.manager.content_visible('qualification'))
  self.assertTrue(all(self.manager.content_visible(f'player{i}') for i in range(4)))
  self.model.commit_match();self.assertIsNone(self.manager.settlement_window())
 def test_event_mode_change_updates_active_final_snapshot_without_restart(self):
  self.final_ron();self.to_hand();self.manager.set_bottom_mode('total_result');self.advance(2.01)
  started=self.manager.stage_started;self.assertEqual(self.manager.settlement_window(),'qualification')
  self.model.configure_broadcast({'event_mode':'team'})
  self.assertEqual(self.manager.settlement_data['mode'],'team');self.assertEqual(self.manager.settlement_window(),'standings')
  self.assertTrue(self.manager.content_visible('standings'));self.assertFalse(self.manager.content_visible('qualification'))
  self.assertEqual(self.manager.stage_started,started)
  self.model.configure_broadcast({'event_mode':'individual'})
  self.assertEqual(self.manager.settlement_data['mode'],'individual');self.assertEqual(self.manager.settlement_window(),'qualification')
 def test_display_only_changes_do_not_restart_identical_final_commit(self):
  self.final_ron();self.to_hand();self.manager.set_bottom_mode('total_result');self.advance(2.01)
  started=self.manager.stage_started;signature=self.manager._last_final_archive
  for config in ({'commentary_duration':10},{'sidebar_page':1},{'statistics_view':'result'}):
   self.model.configure_broadcast(config);self.model.commit_match()
   self.assertEqual(self.manager.stage,'settlement_total');self.assertEqual(self.manager.stage_started,started)
   self.assertEqual(self.manager._last_final_archive,signature)
 def test_restart_during_settlement_recovers_final_cards_and_original_window_geometry(self):
  self.manager.windows['standings'].setGeometry(90,200,1000,120)
  original=self.manager.windows['standings'].geometry()
  self.final_ron();self.to_hand();expected=copy.deepcopy(self.manager.settlement_data);scores=[p['score'] for p in self.model.state['players']]
  self.manager.close();self.model=Model(self.tmp.name);self.manager=OverlayManager(self.model);self.manager.on_display()
  self.assertEqual(self.manager.stage,'table')
  self.assertEqual(self.manager.settlement_data,expected)
  self.advance(1.01);self.advance(1.01);self.advance(.46)
  self.assertEqual(self.manager.stage,'table')
  self.assertFalse(self.manager.content_visible('yaku'))
  self.assertTrue(all(self.manager.content_visible(f'player{i}') for i in range(4)))
  self.assertEqual([p['score'] for p in self.model.state['players']],scores)
  self.model.start_match()
  self.assertEqual(self.manager.windows['standings'].geometry(),original)
 def test_terminal_total_stops_timer_and_import_cancels_forced_views(self):
  export=Path(self.tmp.name)/'original.json';self.model.export_data(export)
  self.model.field('roundIndex',7);self.model.field('dealer',3)
  self.final_ron();self.to_hand();self.manager.set_bottom_mode('total_result');self.advance(2.01)
  self.assertEqual(self.manager.stage,'settlement_total');self.assertEqual(self.manager.settlement_progress(),1.)
  self.assertFalse(self.manager.timer.isActive())
  paid=copy.deepcopy(self.model.state);self.advance(50.);self.assertEqual(self.model.state,paid)
  self.model.import_data(export)
  self.assertEqual(self.manager.stage,'table');self.assertIsNone(self.manager.settlement_data)
  self.assertFalse(self.manager.content_visible('standings'));self.assertFalse(self.manager.content_visible('qualification'))
 def test_chart_show_replay_progress_speed_and_static_export(self):
  self.manager.set_extension_visible('chart',True)
  self.assertEqual(self.manager.chart_progress(),0.)
  self.advance(.9);self.assertAlmostEqual(self.manager.chart_progress(),.5)
  self.model.setting('speed',2.)
  self.assertAlmostEqual(self.manager.chart_progress(),.5)
  image=QImage(1600,900,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent);painter=QPainter(image)
  try:
   with patch('overlays.draw_extension',wraps=overlays.draw_extension) as draw:
    self.manager.draw_content(painter,'chart')
    self.assertAlmostEqual(draw.call_args.kwargs['progress'],.5)
   with patch('overlays.draw_extension',wraps=overlays.draw_extension) as draw:
    self.manager.draw_content(painter,'chart',static=True)
    self.assertEqual(draw.call_args.kwargs.get('progress',1.),1.)
    self.assertEqual(draw.call_args.kwargs.get('flow_time',0.),0.)
  finally:painter.end()
  self.advance(.91);self.assertIsNone(self.manager.chart_started);self.assertEqual(self.manager.chart_progress(),1.)
  self.assertTrue(self.manager.timer.isActive())  # Colour drift outlives the drawing reveal.
  self.manager.replay_chart();self.assertEqual(self.manager.chart_progress(),0.);self.assertTrue(self.manager.timer.isActive())
  self.manager.set_extension_visible('chart',False);self.advance(.65);self.assertIsNone(self.manager.chart_started)
  self.assertFalse(self.manager.timer.isActive())
 def test_old_proportional_custom_caption_only_migrates_height(self):
  self.model.settings.update(commentary_layout_version=2,commentary_auto=False)
  self.model.settings['windows']['commentary']=[212,86,770,99]
  self.manager.restore_geometry()
  self.assertEqual(self.manager.windows['commentary'].geometry(),QRect(212,86,770,132))
  self.assertFalse(self.model.settings['commentary_auto'])
  self.manager.save_geometry();self.manager.restore_geometry()
  self.assertEqual(self.manager.windows['commentary'].geometry(),QRect(212,86,770,132))
 def test_two_line_caption_migrates_old_layout_but_retains_custom_size(self):
  self.model.settings['windows']['commentary']=[410,137,1100,142]
  self.model.settings['commentary_auto_geometry']=[410,137,1100,142];self.model.settings['commentary_auto']=True
  self.manager.restore_geometry();self.manager.center_commentary(QRect(0,0,1920,1080))
  self.assertEqual(self.manager.windows['commentary'].base,(900,132))
  self.assertEqual(self.manager.windows['commentary'].width(),900);self.assertEqual(self.manager.windows['commentary'].height(),132)
  custom=QRect(210,82,660,160);self.manager.windows['commentary'].setGeometry(custom);self.manager.save_geometry();self.manager.restore_geometry()
  self.assertEqual(self.manager.windows['commentary'].geometry(),custom)
  self.assertFalse(self.model.settings['commentary_auto'])

 def test_commentary_has_fixed_padding_and_keeps_native_font_for_complete_copy(self):
  from broadcast_artwork import commentary_layout
  self.model.state['broadcast']['config'].update(commentary_title='振听',commentary_builtin=False)
  bodies=('曾经舍弃过自己正在等待的牌。',
          '曾经舍弃过自己正在等待的牌，或者错过了可以荣和的机会，此时不能荣和他人的舍牌，但仍然可以通过自摸和牌。',
          '不能荣和他人舍牌，但可以自摸和牌。'*12, '牌'*10000)
  layouts=[]
  for body in bodies:
   self.model.state['broadcast']['config']['commentary_body']=body
   layout=commentary_layout(self.model);layouts.append(layout)
   self.assertEqual(layout['body_font'].pixelSize(),24)
   self.assertLessEqual(layout['body_height'],layout['body_rect'].height())
   lines=layout['body_layout']
   self.assertEqual(sum(lines.lineAt(i).textLength() for i in range(lines.lineCount())),len(body))
   self.assertGreaterEqual(layout['body_rect'].y()-layout['top'],20)
  self.assertEqual(layouts[0]['body_lines'],1);self.assertEqual(layouts[1]['body_lines'],2)
  self.assertTrue(all(layout['body_rect'].width()==layouts[0]['body_rect'].width() for layout in layouts))
  self.assertGreater(layouts[-1]['body_lines'],2)
  self.assertGreater(layouts[-1]['height'],layouts[0]['height'])

if __name__=='__main__':unittest.main()
