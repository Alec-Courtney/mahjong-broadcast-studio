"""Tournament invariants: replace/undo, exact payments, persistence and advancement."""
import copy
import json
import os
from pathlib import Path
from unittest.mock import patch
import sys
import tempfile
import unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtWidgets import QApplication
from model import Model
import tournament as t
APP=QApplication.instance() or QApplication([])

class TournamentModelTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.m=Model(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def stats(self,scope='all'):return {r['id']:r for r in t.statistics(self.m.state,scope)}
 def win(self,w=1,l=2,han=1):self.m.settle({'type':'ron','winner':w,'loser':l,'han':han,'fu':30},{'yaku':['立直']})
 def test_empty_history_is_unknown_not_zero_rate(self):
  for r in self.stats().values():
   self.assertEqual(r['hands'],0);self.assertIsNone(r['call_rate']);self.assertIsNone(r['average_deal_in'])
 def test_replace_settlement_undo_redo_reload(self):
  self.m.riichi(1);self.m.field('honba',2)
  self.m.meld(2,'pon',['5p']*3,3)
  self.win();r=self.stats();self.assertEqual(r['p2']['wins'],1);self.assertEqual(r['p3']['deal_in_points'],1600)
  self.assertEqual(r['p2']['win_points'],1600);self.assertEqual(r['p2']['riichi'],1);self.assertEqual(r['p3']['calls'],1)
  self.win(w=3,l=0,han=3);r=self.stats();self.assertEqual(r['p2']['wins'],0);self.assertEqual(r['p4']['wins'],1)
  self.assertEqual(r['p1']['deal_in_points'],4500);self.assertEqual(len(self.m.state['broadcast']['hands']),1)
  self.m.history('undo');self.assertEqual(self.stats()['p2']['wins'],1)
  self.m.history('redo');self.assertEqual(self.stats()['p4']['wins'],1)
  reloaded=Model(self.tmp.name);self.assertEqual(t.statistics(reloaded.state),t.statistics(self.m.state))
 def test_calls_count_hands_not_melds_and_ankan_excluded(self):
  self.m.meld(0,'ankan',['1m']*4,None)
  self.m.meld(1,'pon',['5p']*3,2);self.m.meld(1,'chi',['2s','3s','4s'],0)
  self.m.draw();r=self.stats();self.assertEqual(r['p1']['calls'],0);self.assertEqual(r['p2']['calls'],1)
  self.assertEqual(r['p2']['call_rate'],1);self.assertEqual(r['p2']['wins'],0)
 def test_next_hand_distinguishes_repeat_round(self):
  self.win(w=0);self.assertTrue(self.m.next_hand());self.win(w=0)
  self.assertEqual(self.stats()['p1']['hands'],2);self.assertEqual(self.stats()['p1']['wins'],2)
  chart=t.progression(self.m.state);self.assertEqual(len(chart['labels']),3);self.assertEqual(chart['hands'],2)
 def test_commit_is_idempotent_and_new_match_retains_history(self):
  self.win();self.m.commit_match();before=t.standings(self.m.state,live=False)
  self.m.commit_match();self.assertEqual(before,t.standings(self.m.state,live=False));self.assertEqual(self.stats()['p2']['hands'],1)
  self.m.start_match();self.assertEqual(self.stats()['p2']['hands'],1);self.assertEqual(self.stats('match')['p2']['hands'],0)
  rows={r['id']:r for r in t.standings(self.m.state,live=False)};self.assertEqual(rows['p2']['games'],1)
  self.m.history('undo');self.assertEqual(self.stats('match')['p2']['hands'],1)
 def test_uncommitted_restart_preserves_stats_without_booking_points(self):
  self.win();self.m.start_match();self.assertEqual(self.stats()['p2']['wins'],1)
  self.assertTrue(all(r['points']==0 and r['games']==0 for r in t.standings(self.m.state,live=False)))
 def test_baselines_and_stable_identity(self):
  roster=copy.deepcopy(self.m.state['broadcast']['roster']);roster[0]['stats']={'hands':20,'calls':4,'wins':5,'win_points':40000}
  self.m.configure_broadcast({'roster':roster});self.assertEqual(self.stats()['p1']['call_rate'],.2)
  self.m.update(lambda s:s['players'][0].update(name='新姓名'))
  self.assertEqual(self.stats()['p1']['name'],'新姓名');self.assertEqual(self.stats()['p1']['hands'],20)
  roster=copy.deepcopy(self.m.state['broadcast']['roster']);roster.append({'id':'new-id','name':'替补','seed':5})
  self.m.configure_broadcast({'roster':roster,'seats':['new-id','p2','p3','p4']})
  self.assertEqual(self.stats()['new-id']['hands'],0);self.assertEqual(self.stats()['p1']['hands'],20)
  self.win()
  with self.assertRaises(ValueError):self.m.configure_broadcast({'seats':['p1','p2','p3','p4']})
 def test_import_validation_is_transactional(self):
  self.win();target=Path(self.tmp.name)/'export.json';self.m.export_data(target)
  original=copy.deepcopy(self.m.state);bad=json.loads(target.read_text(encoding='utf-8'));bad['state']['broadcast']['roster'][0]['base_points']=float('nan')
  target.write_text(json.dumps(bad),encoding='utf-8')
  with self.assertRaises(ValueError):self.m.import_data(target)
  self.assertEqual(self.m.state,original)
  self.m.export_data(target);self.m.start_match();self.m.import_data(target);self.assertEqual(self.m.state,original)
 def test_legacy_session_does_not_invent_previous_statistics(self):
  self.win();s=copy.deepcopy(self.m.state);s.pop('broadcast');s['roundIndex']=7
  migrated=self.m.migrate(s,5);self.assertTrue(migrated['broadcast']['partial']);self.assertEqual(migrated['broadcast']['hands'],[])
  self.assertEqual(t.progression(migrated)['series'][0]['scores'],[s['players'][0]['score']])
 def test_four_to_one_two_three(self):
  for slots in (1,2,3):
   self.m.configure_broadcast({'qualification_slots':slots})
   q=t.qualification(self.m.state);self.assertEqual(len(q['rows']),4)
   self.assertEqual(sum(r['status']=='inside' for r in q['rows']),slots)
 def test_qualification_displays_only_the_lowest_successful_payment(self):
  # 2025-06-02 A group game 2: Miura passes on Ishii 2000, fails on 3900.
  def change(s):
   s['roundIndex']=7;s['dealer']=3
   for p,n,score in zip(s['players'],['岡田紗佳','二階堂瑠美','三浦智博','石井一馬'],[27200,15900,27000,29900]):p.update(name=n,score=score)
   s['accountingTotal']=100000
  self.m.update(change)
  roster=copy.deepcopy(self.m.state['broadcast']['roster'])
  for p,base in zip(roster,[1.4,67.7,-46.7,-22.4]):p['base_points']=base
  self.m.configure_broadcast({'roster':roster,'uma':[50,10,-10,-30],'return_points':30000,'tie_break':'share'})
  q=t.qualification(self.m.state);r=q['rows'][2]
  direct=[x for x in r['scenarios'] if x['type']=='ron' and x['from']==3]
  self.assertEqual([x['base'] for x in direct],[1600])
  self.assertEqual(r['minimum_tsumo']['text'],'500 / 1,000')
  self.assertEqual(len({(x['type'],x['from']) for x in r['scenarios']}),len(r['scenarios']))
  self.assertTrue(all(x['qualifies'] for x in r['scenarios']))
  self.assertEqual(r['ron'][2]['points'],[1600]);self.assertEqual(r['ron'][2]['text'],'1,600')
  self.assertNotIn('等',r['tsumo'])
  # Accurate arithmetic remains unchanged even though the presentation now
  # intentionally shows just the minimum successful tier per payment source.
  for payment,expected in [(2000,True),(3900,False)]:
   scores=[27200,15900,27000+payment,29900-payment]
   self.assertEqual(t._qualifies(t._projected_standings(self.m.state,scores=scores),'p3',2,False),expected)
 def test_manual_conditions_do_not_override_auto(self):
  self.m.configure_broadcast({'manual_conditions':{'p1':'指定条件'}})
  self.assertNotEqual(t.qualification(self.m.state)['rows'][0]['condition'],'指定条件')
  self.m.configure_broadcast({'qualification_calculation':'manual'})
  self.assertEqual(t.qualification(self.m.state)['rows'][0]['condition'],'指定条件')
 def test_clear_does_not_create_hand(self):
  self.m.clear();self.assertEqual(self.stats()['p1']['hands'],0)
  self.win();self.m.clear();self.assertEqual(self.stats()['p1']['hands'],1)

 def test_roster_switch_preserves_identity_photo(self):
  self.m.update(lambda s:s['players'][0].update(photo='profiles/alice.jpg'))
  self.m.configure_broadcast({'seats':['p2','p1','p3','p4']})
  self.assertEqual(self.m.state['players'][0]['photo'],'');self.assertEqual(self.m.state['players'][1]['photo'],'profiles/alice.jpg')
 def test_historical_team_points_stay_with_original_team(self):
  roster=copy.deepcopy(self.m.state['broadcast']['roster'])
  for p,team in zip(roster,['A','B','C','D']):p.update(team=team,team_id=team)
  self.m.configure_broadcast({'roster':roster});self.m.field('roundIndex',7);self.m.field('dealer',3);self.win();self.m.commit_match()
  original={r['id']:r for r in t.standings(self.m.state,'team',False)}
  self.m.start_match();roster=copy.deepcopy(self.m.state['broadcast']['roster']);roster[1].update(team='E',team_id='E');self.m.configure_broadcast({'roster':roster})
  changed={r['id']:r for r in t.standings(self.m.state,'team',False)}
  self.assertEqual(changed['B']['points'],original['B']['points']);self.assertEqual(changed['E']['points'],0)
  self.assertEqual(changed['B']['games'],1);self.assertEqual(changed['E']['games'],0)
 def test_manual_recording_start_and_first_hand_riichi(self):
  self.m.riichi(0);self.assertEqual(t.progression(self.m.state)['series'][0]['scores'],[30000])
  self.m.history('undo')
  def change(s):
   for p,score in zip(s['players'],[40000,30000,20000,30000]):p['score']=score
   s['roundIndex']=6
  self.m.update(change);self.win();chart=t.progression(self.m.state)
  self.assertEqual(chart['series'][0]['scores'][0],40000);self.assertTrue(chart['partial'])
 def test_settled_qualification_does_not_repeat_payment(self):
  self.win();q=t.qualification(self.m.state)
  self.assertTrue(all(not r['scenarios'] for r in q['rows']));self.assertIn('已结算',q['summary'])
  self.m.next_hand();self.assertTrue(t.qualification(self.m.state)['rows'][0]['scenarios'])
 def test_terminal_shared_rank_is_frozen_before_deposit_split(self):
  def change(s):
   s['roundIndex']=7;s['dealer']=3;s['pot']=1000
   for p,score in zip(s['players'],[30000,30000,30000,29000]):p['score']=score
   s['rules'].update(terminalPot='top',terminalPotTie='equal_100')
  self.m.update(change);self.m.draw()
  self.assertEqual(self.m.state['phase'],'finished')
  actual={r['id']:r for r in t.match_results(self.m.state)}
  engine={r['id']:r for r in self.m.engine.invoke('computeStandings',self.m.state)}
  for pid in actual:self.assertEqual(actual[pid]['rank'],engine[pid]['rank']);self.assertAlmostEqual(actual[pid]['points'],engine[pid]['total'])

 def test_new_match_start_points_and_scoring_rules(self):
  self.m.configure_broadcast({'start_points':25000,'return_points':30000,'uma':[50,10,-10,-30],'tie_break':'initial_seat'})
  self.assertEqual([p['score'] for p in self.m.state['players']],[30000]*4)
  self.assertEqual(self.m.state['rules']['startPoints'],30000)
  self.assertEqual(self.m.state['rules']['uma'],[50,10,-10,-30])
  self.assertEqual(self.m.state['rules']['tieBreak'],'initial_seat')
  self.m.start_match();self.assertEqual([p['score'] for p in self.m.state['players']],[25000]*4)
  self.assertEqual(self.m.state['rules']['startPoints'],25000)


 def setup_league_table(self):
  def change(s):
   for player,score in zip(s['players'],[31000,29000,22000,18000]):player['score']=score
   s['accountingTotal']=100000
  self.m.update(change)
  roster=copy.deepcopy(self.m.state['broadcast']['roster'])
  for player,team,points in zip(roster,['A','B','C','D'],[400,360,300,200]):
   player.update(name=team+'选手',team=team,team_id=team,base_points=points)
  for seed,(team,points) in enumerate(zip(['U','V','W','X','Y','Z'],[500,450,330,250,150,100]),5):
   roster.append(dict(id='extra-'+team,name=team+'选手',team=team,team_id=team,base_points=points,seed=seed))
  self.m.configure_broadcast(dict(roster=roster,uma=[50,10,-10,-30],return_points=30000,oka_enabled=False,tie_break='share',event_mode='team'))

 def test_table_standings_uses_global_ranks_and_keeps_seat_order(self):
  self.setup_league_table()
  before=copy.deepcopy(self.m.state)
  for mode in ('individual','team'):
   rows=t.table_standings(self.m.state,mode)
   full={r['id']:r for r in t.standings(self.m.state,mode)}
   self.assertEqual([r['seat'] for r in rows],[0,1,2,3])
   self.assertEqual([r['player_id'] for r in rows],['p1','p2','p3','p4'])
   self.assertEqual([r['rank'] for r in rows],[3,4,6,8])
   self.assertEqual([r['points'] for r in rows],[400.,360.,300.,200.])
   for row in rows:self.assertEqual(row['points'],full[row['id']]['points']);self.assertTrue(row['current_table'])
  self.assertEqual(self.m.state,before)
  roster=copy.deepcopy(self.m.state['broadcast']['roster']);roster[1].update(team='A',team_id='A')
  self.m.configure_broadcast({'roster':roster})
  rows=t.table_standings(self.m.state,'team')
  self.assertEqual(len(rows),4);self.assertEqual([r['id'] for r in rows],['A','A','C','D'])
  self.assertEqual(rows[0]['points'],rows[1]['points']);self.assertEqual(rows[0]['team_match_points'],0.)
  override=[27100,32900,22000,18000]
  full={r['id']:r for r in t.standings(self.m.state,'team',scores=override)}
  for row in t.table_standings(self.m.state,'team',scores=override):
   self.assertEqual(row['points'],full[row['id']]['points']);self.assertEqual(row['rank'],full[row['id']]['rank'])

 def test_settlement_summary_includes_rank_bonus_and_all_league_positions(self):
  self.setup_league_table();before=copy.deepcopy(self.m.state)
  self.m.settle(dict(type='ron',winner=1,loser=0,han=3,fu=30),{'yaku':[]})
  after=copy.deepcopy(self.m.state);summary=t.settlement_summary(before,after,'team')
  self.assertEqual([r['points_before'] for r in summary['players']],[51.,9.,-18.,-42.])
  self.assertEqual([r['points_after'] for r in summary['players']],[7.1,52.9,-18.,-42.])
  self.assertEqual([r['points_delta'] for r in summary['players']],[-43.9,43.9,0.,0.])
  self.assertEqual([r['score_delta'] for r in summary['players']],[-3900,3900,0,0])
  self.assertEqual([r['rank_before'] for r in summary['standings']],[2,4,6,8])
  self.assertEqual([r['rank_after'] for r in summary['standings']],[4,3,6,8])
  self.assertEqual([r['points_after'] for r in summary['standings']],[407.1,412.9,282.,158.])
  all_rows={r['id']:r for r in summary['all_standings']}
  self.assertEqual(all_rows['V']['points'],450.);self.assertEqual(all_rows['V']['rank'],2)
  self.assertFalse(all_rows['V']['current_table']);self.assertEqual(summary['highlight_ids'],['A','B','C','D'])
  self.assertEqual(before['broadcast']['hands'],[]);self.assertEqual(len(after['broadcast']['hands']),1)
  self.assertEqual(self.m.state,after)
  personal=t.settlement_summary(before,after,'individual')
  self.assertEqual(personal['highlight_ids'],['p1','p2','p3','p4'])
  self.assertEqual(sum(r['current_table'] for r in personal['all_standings']),4)

 def test_settlement_summary_does_not_rebook_committed_or_replaced_hands(self):
  self.setup_league_table();before=copy.deepcopy(self.m.state)
  self.win(w=1,l=0,han=3);after=copy.deepcopy(self.m.state)
  expected=t.settlement_summary(before,after,'team')
  self.m.commit_match();self.m.commit_match()
  committed=copy.deepcopy(self.m.state)
  actual=t.settlement_summary(before,committed,'team')
  for key in ('players','standings'):
   for first,second in zip(expected[key],actual[key]):
    for field in ('points_before','points_after','points_delta','rank_before','rank_after'):
     self.assertEqual(first[field],second[field])
  # A replacement still compares against this hand's start; neither the
  # replaced one-hand history nor its committed snapshot is another payment.
  self.win(w=2,l=0,han=2)
  revised=copy.deepcopy(self.m.state)
  correction=t.settlement_summary(committed,revised,'team')
  prior={r['id']:r for r in t._projected_standings(before,'team')}
  for row in correction['standings']:
   self.assertEqual(row['points_before'],prior[row['id']]['points'])
  self.assertEqual(len(revised['broadcast']['hands']),1)
  self.assertEqual(self.m.state,revised)
  self.assertEqual(sum(r['hands'] for r in t.statistics(revised,'match')),4)

 def test_read_helpers_support_legacy_without_mutation_and_validate_flow_settings(self):
  legacy=copy.deepcopy(self.m.state);legacy.pop('broadcast');original=copy.deepcopy(legacy)
  self.assertEqual(len(t.table_standings(legacy)),4)
  self.assertEqual([r['delta'] for r in t.settlement_summary(legacy,legacy)['players']],[0.]*4)
  self.assertEqual(legacy,original)
  old=copy.deepcopy(self.m.state);old['broadcast']['config'].pop('event_mode');old['broadcast']['config']['standings_mode']='team'
  self.assertEqual(t.ensure_broadcast(old)['config']['event_mode'],'individual')
  self.m.configure_broadcast(dict(event_mode='team',settlement_enabled=False,settlement_hold=1.5,settlement_total_hold=30.,sidebar_mode='individual'))
  before=copy.deepcopy(self.m.state)
  for patch in ({'event_mode':'invalid'},{'settlement_enabled':1},{'settlement_hold':0},{'settlement_total_hold':31}):
   with self.assertRaises(ValueError):self.m.configure_broadcast(patch)
   self.assertEqual(self.m.state,before)


 def test_hand_summary_includes_nonwinning_riichi_and_carried_sticks(self):
  self.setup_league_table();self.m.field('pot',2000)
  start=copy.deepcopy(self.m.state)
  self.m.riichi(2);before=copy.deepcopy(self.m.state)
  self.win(w=1,l=0,han=3);after=copy.deepcopy(self.m.state)
  summary=t.settlement_summary(before,after,'team')
  self.assertEqual(summary['hand_start_scores'],[31000,29000,22000,18000])
  self.assertEqual((summary['hand_start_pot'],summary['hand_end_pot']),(2000,0))
  self.assertEqual([r['points_delta'] for r in summary['players']],[-43.9,46.9,-1.,0.])
  old={r['id']:r for r in t._projected_standings(start,'team')}
  for row in summary['standings']:self.assertEqual(row['points_before'],old[row['id']]['points'])
  self.assertEqual(before['players'][2]['score'],21000);self.assertEqual(self.m.state,after)

 def test_hand_summary_does_not_count_returned_winners_own_stick_as_income(self):
  self.setup_league_table();self.m.field('pot',2000);self.m.riichi(1)
  before=copy.deepcopy(self.m.state);self.win(w=1,l=0,han=3)
  summary=t.settlement_summary(before,self.m.state,'individual')
  self.assertEqual(summary['players'][1]['score_before'],29000)
  self.assertEqual(summary['players'][1]['score_after'],34900)
  self.assertEqual(summary['players'][1]['score_delta'],5900)
  self.assertEqual(summary['players'][1]['points_delta'],45.9)
  self.assertEqual(summary['hand_start_pot'],2000)
  self.assertEqual(sum(r['score_delta'] for r in summary['players']),2000)

 def test_draw_summary_retains_new_deposit_and_next_hand_uses_previous_result(self):
  self.setup_league_table();self.m.field('pot',2000);self.m.riichi(2)
  for i in range(4):self.m.set_tenpai(i,True)
  before=copy.deepcopy(self.m.state);self.m.draw();paid=copy.deepcopy(self.m.state)
  summary=t.settlement_summary(before,paid,'team')
  self.assertEqual([r['points_delta'] for r in summary['players']],[0.,0.,-1.,0.])
  self.assertEqual((summary['hand_start_pot'],summary['hand_end_pot']),(2000,3000))
  self.assertTrue(self.m.next_hand());self.m.riichi(3)
  next_before=copy.deepcopy(self.m.state);self.win(w=1,l=0,han=3)
  next_summary=t.settlement_summary(next_before,self.m.state,'team')
  self.assertEqual(next_summary['hand_start_scores'],[p['score'] for p in paid['players']])
  self.assertEqual(next_summary['hand_start_pot'],3000)
  self.assertEqual(next_summary['hand_start_source'],'previous_hand')
  self.assertEqual(next_summary['players'][2]['score_delta'],0)
  self.assertEqual(next_summary['players'][3]['score_delta'],-1000)
  self.assertEqual(len(self.m.state['broadcast']['hands']),2)


 def test_ordinary_standings_ignore_riichi_hands_and_early_current_booking(self):
  self.setup_league_table()
  baseline={mode:t.standings(self.m.state,mode) for mode in ('individual','team')}
  cards={mode:t.table_standings(self.m.state,mode) for mode in baseline}
  def unchanged():
   original=copy.deepcopy(self.m.state)
   # Guard against future regressions that silently reintroduce simulations.
   with patch.object(t,'_match_results',side_effect=AssertionError('ordinary standings must not project scores')):
    for mode in baseline:
     for live in (True,False):
      self.assertEqual(t.standings(self.m.state,mode,live,scores=[90000,0,0,10000]),baseline[mode])
      self.assertEqual(t.table_standings(self.m.state,mode,live,scores=[90000,0,0,10000]),cards[mode])
   self.assertEqual(self.m.state,original)
  unchanged();self.m.riichi(2);unchanged()
  self.m.draw();unchanged();self.m.commit_match();unchanged()
  self.assertTrue(self.m.next_hand());self.m.riichi(3);unchanged()
  self.win(w=1,l=0,han=3);unchanged()
  self.assertIsNone(t.final_settlement_summary(self.m.state,'team'))
  # Earlier manual commitments remain valid historical bookings when the
  # operator intentionally starts the next half-game; never double-book them.
  archived=copy.deepcopy(self.m.state['broadcast']['matches'][0]['results'])
  self.m.start_match()
  expected={r['id']:r['points'] for r in archived}
  rows={r['player_id']:r for r in t.table_standings(self.m.state)}
  for pid,base in zip(['p1','p2','p3','p4'],[400,360,300,200]):
   self.assertAlmostEqual(rows[pid]['points'],base+expected[pid])

 def test_final_summary_is_complete_half_game_and_requires_final_confirmation(self):
  self.setup_league_table();baseline=t.standings(self.m.state,'team')
  self.assertIsNone(t.final_settlement_summary(self.m.state,'team'))
  self.m.field('roundIndex',7);self.m.field('dealer',3)
  self.win(w=1,l=0,han=3)
  self.assertEqual(self.m.state['phase'],'finished')
  self.assertEqual(t.standings(self.m.state,'team'),baseline)
  self.assertIsNone(t.final_settlement_summary(self.m.state,'team'))
  self.m.commit_match();saved=copy.deepcopy(self.m.state)
  with patch.object(t,'_match_results',side_effect=AssertionError('booked final views must use archive')):
   summary=t.final_settlement_summary(self.m.state,'team')
   self.assertEqual([r['points_before'] for r in summary['players']],[0.]*4)
   self.assertEqual([r['points_after'] for r in summary['players']],[7.1,52.9,-18.,-42.])
   self.assertEqual([r['points_before'] for r in summary['standings']],[400.,360.,300.,200.])
   self.assertEqual([r['points_after'] for r in summary['standings']],[407.1,412.9,282.,158.])
   self.assertEqual([r['rank_before'] for r in summary['standings']],[3,4,6,8])
   self.assertEqual([r['rank_after'] for r in summary['standings']],[4,3,6,8])
   self.assertEqual(summary['all_standings'],[dict(row,current_table=row['id'] in summary['highlight_ids']) for row in t.standings(self.m.state,'team')])
  self.assertEqual(self.m.state,saved)
  self.m.commit_match();self.assertEqual(t.final_settlement_summary(self.m.state,'team'),summary)

 def test_early_booked_current_half_only_enters_board_when_finished(self):
  self.setup_league_table();baseline=t.standings(self.m.state,'team')
  self.win();self.m.commit_match();self.assertEqual(t.standings(self.m.state,'team'),baseline)
  self.assertTrue(self.m.next_hand());self.m.riichi(1)
  self.assertEqual(t.standings(self.m.state,'team'),baseline)
  self.m.field('roundIndex',7);self.m.field('dealer',3);self.win(w=1,l=0,han=3)
  self.assertEqual(self.m.state['phase'],'finished')
  self.assertNotEqual(t.standings(self.m.state,'team'),baseline)
  self.assertIsNotNone(t.final_settlement_summary(self.m.state,'team'))

if __name__=='__main__':unittest.main()
