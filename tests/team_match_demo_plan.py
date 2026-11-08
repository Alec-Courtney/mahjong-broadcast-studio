"""Legal, reproducible full-hanchan actions for the isolated team broadcast demo.

Times are relative to each live hand's entrance. The renderer owns waiting for
payments, win details and next-hand entrance; this module never edits scores.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]


def action(at,method,*args,**kwargs):
    return dict(at=at,method=method,args=list(args),kwargs=kwargs)


HANDS=(
    dict(id='east-1',title='东1局',round=0,dealer=0,honba=0,pot=0,settle_at=13.,
         actions=(action(.6,'dora','4m'),action(2.,'meld',2,'pon',['7s']*3,3),
                  action(4.,'riichi',1),action(4.4,'riichi',0),
                  action(7.,'submit_waits',1,['3p','6p'],5),
                  action(9.3,'submit_waits',0,['2s','5s'],4)),
         event=dict(type='ron',winner=1,loser=3,han=2,fu=30),yaku=['立直','平和']),
    dict(id='east-2-draw',title='东2局',round=1,dealer=1,honba=0,pot=0,settle_at=14.,
         actions=(action(.6,'dora','6p'),action(2.,'meld',0,'chi',['2m','3m','4m'],3),
                  action(4.,'riichi',1),action(4.4,'riichi',2),
                  action(9.3,'submit_waits',1,['2p','5p'],6),action(9.3,'submit_waits',2,['4s','7s'],3)),
         event=dict(type='draw'),yaku=[]),
    dict(id='east-2-repeat',title='东2局 一本场',round=1,dealer=1,honba=1,pot=2000,settle_at=12.,
         actions=(action(.6,'dora','5s'),action(3.,'riichi',0),
                  action(6.,'submit_waits',0,['2m','5m'],4)),
         event=dict(type='tsumo',winner=0,han=3,fu=30),yaku=['立直','门前清自摸和','宝牌1']),
    dict(id='east-3',title='东3局',round=2,dealer=2,honba=0,pot=0,settle_at=11.,
         actions=(action(.6,'dora','7p'),action(2.,'meld',3,'pon',['5z']*3,1),
                  action(3.5,'meld',0,'daiminkan',['8s']*4,2),action(5.,'dora','3m'),
                  action(6.,'submit_waits',3,['4m','7m'],4)),
         event=dict(type='ron',winner=3,loser=1,han=1,fu=30),yaku=['役牌 白']),
    dict(id='east-4',title='东4局',round=3,dealer=3,honba=0,pot=0,settle_at=11.,
         actions=(action(.6,'dora','2s'),
                  action(2.5,'submit_waits',0,['3m','6m'],2,no_yaku=['6m'],furiten=True),
                  action(4.,'submit_waits',2,['1p','4p'],0),
                  action(6.,'submit_waits',1,['2m','5m'],4)),
         event=dict(type='tsumo',winner=1,han=2,fu=40),yaku=['门前清自摸和','役牌 白']),
    dict(id='south-1',title='南1局',round=4,dealer=0,honba=0,pot=0,settle_at=13.,
         actions=(action(.6,'dora','3s'),action(2.,'meld',2,'ankan',['2m']*4,None),
                  action(4.,'dora','8p'),action(6.,'riichi',2),
                  action(9.,'submit_waits',2,['3p','6p'],4)),
         event=dict(type='ron',winner=2,loser=1,han=3,fu=40),yaku=['立直','一气通贯']),
    dict(id='south-2',title='南2局',round=5,dealer=1,honba=0,pot=0,settle_at=12.,
         actions=(action(.6,'dora','6m'),action(2.,'meld',0,'pon',['0p','5p','5p'],2),
                  action(4.,'meld',0,'kakan',['5p'],None),action(6.,'dora','9s'),
                  action(8.,'submit_waits',0,['6s','9s'],3)),
         event=dict(type='ron',winner=0,loser=2,han=3,fu=40),yaku=['对对和','赤宝牌1']),
    dict(id='south-3',title='南3局',round=6,dealer=2,honba=0,pot=0,settle_at=12.,
         actions=(action(.6,'dora','6s'),action(3.,'riichi',3),
                  action(6.,'submit_waits',3,['3m','6m'],3)),
         event=dict(type='tsumo',winner=3,han=3,fu=30),yaku=['立直','门前清自摸和','宝牌1']),
    dict(id='south-4',title='南4局',round=7,dealer=3,honba=0,pot=0,settle_at=13.,
         actions=(action(.6,'dora','7z'),action(3.,'riichi',1),
                  action(6.,'submit_waits',1,['2m','5m'],4)),
         event=dict(type='ron',winner=1,loser=0,han=4,fu=30),yaku=['立直','平和','断幺九','宝牌1']),
)


def extend_demo_roster(model):
    """Give the four teams 16 real demo rows so personal paging has two pages."""
    roster=copy.deepcopy(model.state['broadcast']['roster'])
    if len(roster)!=4:raise ValueError('The team demo expects exactly four source profiles')
    additions=[]
    for profile in roster:
        for number in range(2,5):
            member=copy.deepcopy(profile)
            member.update(id=profile['id']+'-reserve-'+str(number),name=profile['team']+'选手'+str(number),
                          base_points=0.,games=0,seed=len(roster)+len(additions)+1,stats={})
            additions.append(member)
    model.set_roster(roster+additions)
    return model.state['broadcast']['roster']


def apply_action(model,item):
    return getattr(model,item['method'])(*copy.deepcopy(item['args']),**copy.deepcopy(item['kwargs']))


def settle_hand(model,hand):
    event=copy.deepcopy(hand['event'])
    if event['type']=='draw':return model.draw()
    winner=event['winner']
    info=dict(winner=winner,name=model.state['players'][winner]['name'],
              han=event['han'],fu=event['fu'],yaku=hand['yaku'][:])
    return model.settle(event,info)


def assert_start(model,hand):
    actual={key:model.state[key] for key in ('roundIndex','dealer','honba','pot')}
    expected=dict(roundIndex=hand['round'],dealer=hand['dealer'],honba=hand['honba'],pot=hand['pot'])
    if actual!=expected:raise AssertionError((hand['id'],actual,expected))
    if model.state['phase']!='playing':raise AssertionError('A new hand must be playing')


def assert_strict_state(model):
    state=copy.deepcopy(model.state);state['manualDisplay']=False
    result=model.engine.invoke('validateState',state)
    if not result.get('valid'):raise AssertionError(result)
    if sum(player['score'] for player in state['players'])+state['pot']!=120000:
        raise AssertionError('Account total changed')


def dry_run():
    from PySide6.QtWidgets import QApplication
    from model import payment_lines
    from broadcast_test_support import initialize_fonts,review_model
    from tournament import match_results,standings,statistics
    app=QApplication.instance() or QApplication([]);family=initialize_fonts(app)
    with tempfile.TemporaryDirectory(prefix='team-plan-',dir=ROOT/'tests') as folder:
        model=review_model(folder,family);extend_demo_roster(model)
        model.settings['riichi_payment_mode']='deferred';rows=[]
        for index,hand in enumerate(HANDS):
            assert_start(model,hand);assert_strict_state(model)
            for item in hand['actions']:
                apply_action(model,item);assert_strict_state(model)
            before=copy.deepcopy(model.state);settle_hand(model,hand);assert_strict_state(model)
            lines=payment_lines(before,model.state,'deferred')
            rows.append(dict(id=hand['id'],round=hand['round'],dealer=hand['dealer'],honba=hand['honba'],
                             scores=[p['score'] for p in model.state['players']],pot=model.state['pot'],
                             kind=model.state['lastResult']['kind'],payment_lines=lines,
                             deposit_only_seats=[i for i,(ordinary,deposit) in enumerate(lines) if not ordinary and deposit]))
            if index<len(HANDS)-1:
                if not model.next_hand():raise AssertionError('Match finished prematurely')
        if model.state['phase']!='finished':raise AssertionError('South four did not finish')
        if rows[0]['payment_lines']!=[(0,-1000),(2000,1000),(0,0),(-2000,0)]:
            raise AssertionError('East one must show deposit-only, income-plus-deposit, and ordinary-loss seats together')
        model.commit_match();assert_strict_state(model)
        return dict(hands=rows,hand_count=len(model.state['broadcast']['hands']),
                    final_results=match_results(model.state),standings=standings(model.state,'team'),
                    committed_matches=len([m for m in model.state['broadcast']['matches'] if m['committed']]),
                    statistics=statistics(model.state,'match'),roster_count=len(model.state['broadcast']['roster']),
                    progression_points=len(model.state['broadcast']['hands'])+1,
                    coverage=dict(full_hanchan=True,renchan_draw=True,carried_pot=2000,
                        chi=True,pon=True,daiminkan=True,ankan=True,kakan=True,red_called_tile=True,
                        kan_dora=True,riichi_all_four_teams=True,riichi_fifo_pair=True,
                        deposit_only_with_blank_ordinary_row=True,
                        ordinary_tenpai=True,no_yaku_wait=True,furiten=True,known_remaining_zero=True))


if __name__=='__main__':print(json.dumps(dry_run(),ensure_ascii=False,indent=2))
