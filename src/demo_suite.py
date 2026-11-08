"""Build a complete, isolated and locally playable demonstration event library.

Only the new output directory is written. The normal data/ library is never opened.
All newly generated results pass through the real payment and archive engines.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import random
import tempfile
import uuid
from pathlib import Path

from app_paths import ROOT
from app_settings import default_settings
from model import Model
from event_store import new_event,validate_event,atomic_json
from demo_data import DEMO_SCENARIOS,DEMO_DESCRIPTIONS,load_demo_state,_resolve_assets,_settings,COLORS,NAMES
from complete_demo import mleague_roster
from media_store import manage_photos
import tournament
import event_schedule as schedule
import ranking_reports as reports

SUITE_VERSION=1
SEED=20261027
NAMESPACE=uuid.UUID('d122072b-56ba-42bc-9dd0-6e3a81913bea')
CORE_WINDOWS=['round','dora','player0','player1','player2','player3','yaku']
FULL_CASES=[
    ('team_live','团队联赛・40人完整数据','十队40人、60个完整半庄，所有人都有逐局统计；直播四队红色高亮。',['team','all_statistics','team_sidebar','team_bottom','portraits','planned_games']),
    ('team_final','团队联赛・半庄终局','终局已确认，展示最终点数/pt、队伍总分和赛后走势。',['team','finished','final_pt','chart','settlement']),
    ('individual_live','个人联赛・16人完整数据','16人32个完整半庄，显示已打/计划场次及ML完整统计。',['individual','all_statistics','individual_sidebar','individual_bottom']),
    ('large_individual','个人联赛・48人榜单','48人各两场，检查完整名次、翻页预览和单张完整榜单导出。',['individual','full_ranking_image','planned_games']),
    ('team_pages','团队联赛・13队分页','13队52人，队伍侧栏第二页，包含正分、负分、晋级线和计划场次。',['team','sidebar_pagination','qualification_line']),
    ('knockout_ready','淘汰赛・赛前编排','16人四进二，每桌两半庄，首轮已排好，可从第一桌实际开打。',['knockout','ready','four_to_two','manual_pairing']),
    ('knockout_progress','淘汰赛・晋级进行中','首轮全部晋级，半决赛一桌已打一场，当前为第二场进行中。',['knockout','four_to_two','live_node','multi_match_node','advancement']),
    ('knockout_final','淘汰赛・全程完成','16人到决赛共14个完整半庄，冠军已确认，完整晋级图可导出。',['knockout','finished','champion','bracket_export']),
    ('knockout_one','淘汰赛・四进一条件','16人四进一，三桌已晋级，第四桌进入南四，可测试最后一局晋级条件。',['knockout','four_to_one','qualification','south_four']),
    ('knockout_three','淘汰赛・四进三与直入','10人自定义赛程：两桌四进三，加两名直入选手，再晋级决赛。',['knockout','four_to_three','seeded_bye','manual_pairing']),
    ('wait_states','听牌・有役无役与振听','四席同时比较有役、无役、振听、山存0/未知，并包含全部副露和五宝牌。',['wait_yaku','wait_no_yaku','furiten','remaining_zero','remaining_unknown','melds','five_dora','deferred_riichi']),
    ('history_gaps','统计・缺失历史与补录','可靠历史基数、不完整顺位、只补最终点数及未出场选手，检查“—”和覆盖说明。',['history_baselines','missing_history','manual_results','unplayed','statuses']),
    ('custom_reports','报表・全部统计与自定义表头','所有可选个人统计列，重排列序，修改表头；计算保持一致。',['all_columns','custom_headers','column_order','xlsx','png']),
    ('japanese_layout','双语・字体与独立布局','日文播出界面、独立字体和窗口设置；切回其他赛事恢复各自配置。',['japanese','event_settings','fonts','layout']),
]


class MemoryModel(Model):
    def save(self):
        """Generation batches to disk only after the whole scene validates."""


class MemoryStore:
    def __init__(self,model):self.model=model
    def update(self,**patch):
        candidate=copy.deepcopy(self.model.event);candidate.update(patch);validate_event(candidate,self.model.state);self.model.event=candidate


def identity(key):return uuid.uuid5(NAMESPACE,key).hex


def restore_transition(model):
    """Rebuild only an in-memory replay frame; never add a recorded result."""
    result=model.state.get('lastResult')
    if not model.settings.get('local_demo_suite') or not result:return False
    after=copy.deepcopy(model.state);before=copy.deepcopy(after)
    before['phase']='playing';before['lastResult']=None;before.pop('finalRanking',None);before['pot']=result['potBefore']
    for player,score in zip(before['players'],result['beforeScores']):player['score']=score
    broadcast=before['broadcast'];broadcast['hands']=[h for h in broadcast['hands'] if h['id']!=broadcast['current_hand']]
    broadcast['matches']=[m for m in broadcast['matches'] if m['id']!=before['id']]
    model.engine.validate(before);model.last_transition=(before,after,result['kind']);return True


def synthetic_roster(count,teams=False):
    rows=[]
    for index in range(count):
        team=index//4 if teams else index%16
        rows.append(dict(id=f'full-demo-p{index+1:03d}',name=(NAMES[index%16] if index<16 else f'演示选手{index+1:02d}'),
            team=f'演示队伍{team+1:02d}' if teams else '',team_id=f'full-demo-team{team+1:02d}' if teams else '',
            photo=str(ROOT/'resources/demo/avatars'/f'{index%16+1}.png'),color=COLORS[team%16],
            base_points=0.,games=0,stats={},status='active'))
    return rows


def fresh_state(model,roster,seats,key,serial=0,node_id=''):
    config=copy.deepcopy(model.state['broadcast']['config']);rules=copy.deepcopy(model.state['rules'])
    rules.update(startPoints=config['start_points'],returnPoints=config['return_points'],uma=config['uma'][:],okaEnabled=config['oka_enabled'],tieBreak=config['tie_break'])
    state=model.engine.invoke('createState',{'players':[{'id':pid,'name':next(p['name'] for p in roster if p['id']==pid)} for pid in seats],
        'rules':rules,'title':model.state.get('title','完整演示'),'initialDealer':serial%4})
    state['id']=f'full-demo-{key}-{serial:03d}';state['manualDisplay']=True
    profiles={p['id']:p for p in roster}
    for player in state['players']:
        profile=profiles[player['id']];player.update(tenpai=False,waits=[],remaining=None,photo=profile.get('photo',''),
            color='#ffffff',team_color=profile.get('color','#377e72'),team=profile.get('team',''),team_id=profile.get('team_id',''))
    broadcast=tournament.ensure_broadcast(state);broadcast.update(roster=copy.deepcopy(roster),config=config,current_hand=state['id']+'-h00')
    broadcast['initial_players']=copy.deepcopy(state['players'])
    if node_id:broadcast['active_node_id']=node_id
    return state


def scene_model(scratch,key,title,roster,kind='individual_league'):
    model=MemoryModel(scratch/key);model.event=new_event('【演示】'+title,kind,identity(key))
    model.state['broadcast']['config'].update(start_points=25000,return_points=30000,uma=[50,10,-10,-30],oka_enabled=False,tie_break='share',
        event_mode='team' if kind=='team_league' else 'individual',title=model.event['name'],sidebar_mode='team' if kind=='team_league' else 'individual',
        standings_mode='team' if kind=='team_league' else 'individual',show_match_title=True,statistics_scope='all',chart_mode='scores',
        commentary_title='完整演示数据',commentary_body='姓名/照片仅作素材展示，比赛成绩和统计均为模拟。',commentary_builtin=False,commentary_duration=0)
    model.state['title']=model.event['name'];model.state=fresh_state(model,roster,[p['id'] for p in roster[:4]],key)
    model.settings=default_settings();model.settings.update(_settings(CORE_WINDOWS+['qualification','standings']))
    model.settings.update(show_match_title=True,show_yaku=False,player_group_linked=True,local_demo_suite=True)
    model.event['report_columns']={'individual':reports.preset_columns(kind,'ml')}
    model.event['planned_games']=12 if kind=='individual_league' else 48 if kind=='team_league' else 0
    if kind=='team_league':
        model.event['qualification_count']=6
        for player in roster:model.event['team_names'].setdefault(player['team_id'],['DEMO TEAM',player['team']])
    return model


def simulate(model,seats,key,serial,partial=None,node_id='',repeat=None):
    """A genuine half-game: riichi, open/dama wins, ron/tsumo and tenpai draws."""
    roster=model.state['broadcast']['roster'];state=fresh_state(model,roster,seats,key,serial,node_id)
    rng=random.Random(SEED+serial*97+sum(map(ord,key)));number=0;info=None
    repeats=serial%2==0 if repeat is None else repeat
    while state['phase']!='finished':
        dealer=state['dealer'];draw=number in (2,5) and (serial+number)%3!=1
        winner=dealer if number==0 and repeats else (dealer+1+rng.randrange(3))%4
        loser=(winner+1+rng.randrange(3))%4;opened=rng.random()<.34;riichi=not opened and rng.random()<.63
        state=model.engine.apply(state,{'type':'dora','tile':model.engine.invoke('indicatorFromTile',['5m','3p','6s','7z'][number%4])})
        if draw:
            tenpai=[i for i in range(4) if i!=dealer and rng.random()<.66] or [(dealer+1)%4]
            caller=(dealer+2)%4
            if caller in tenpai and len(tenpai)>1:caller=dealer
            state=model.engine.apply(state,{'type':'meld','player':caller,'meld':{'kind':'pon','tiles':['7s']*3,'from':(caller+1)%4,'calledIndex':0}})
            if serial%2:
                who=next((i for i in tenpai if i!=caller),None)
                if who is not None:state=model.engine.apply(state,{'type':'riichi','player':who})
            for i,p in enumerate(state['players']):p['tenpai']=i in tenpai
            event={'type':'draw','tenpai':tenpai};info=None
        else:
            if opened:state=model.engine.apply(state,{'type':'meld','player':winner,'meld':{'kind':'pon','tiles':['5p']*3,'from':(winner+1)%4,'calledIndex':0}})
            elif riichi:
                state=model.engine.apply(state,{'type':'riichi','player':winner});state['players'][winner]['tenpai']=True
            caller=(winner+2)%4
            if not draw and rng.random()<.64:state=model.engine.apply(state,{'type':'meld','player':caller,'meld':{'kind':'chi','tiles':['2m','3m','4m'],'from':(caller+3)%4,'calledIndex':0}})
            if loser not in (winner,caller) and serial%3==0:
                state=model.engine.apply(state,{'type':'riichi','player':loser});state['players'][loser]['tenpai']=True
            tsumo=rng.random()<.43;han=rng.choice([1,2,2,3,3,4,5]);fu=rng.choice([30,40])
            event=dict(type='tsumo' if tsumo else 'ron',winner=winner,han=han,fu=fu)
            if not tsumo:event['loser']=loser
            preview=model.engine.preview(state,event)
            yaku=(['断幺九'] if opened else ['立直'] if riichi else ['门前清自摸和'] if tsumo else ['白'])
            if han>1:yaku.append('宝牌 '+str(han-1))
            info=dict(name=state['players'][winner]['name'],winner=winner,kind=event['type'],color='#ffffff',han=han,fu=fu,yakuman=0,yaku=yaku,
                limit=preview['limit'],base_points=preview['basePoints'])
        before=state;state=model.engine.apply(state,event);tournament.on_update(before,state,event['type']);number+=1
        if state['phase']=='finished':break
        before=state;state=model.engine.apply(state,{'type':'next_hand'});tournament.on_update(before,state,'next_hand');state['broadcast']['current_hand']=state['id']+f'-h{number:02d}'
        if partial is not None and number>=partial:break
        if number>16:raise ValueError('模拟半庄未正确结束')
    model.engine.validate(state);tournament.validate_broadcast(state)
    record=tournament.archive_match(state,True) if state['phase']=='finished' else None
    return state,copy.deepcopy(record),info


def set_live(model,state,history,info=None):
    state['broadcast']['matches']=history;model.state=state;model.last_info=info if state['phase']=='finished' else None


def league(scratch,key,title,count,team=False,games_per_player=4,finish=False):
    roster=_resolve_assets(mleague_roster()) if count==40 else synthetic_roster(count,team)
    if not team:
        for player in roster:player.update(team='',team_id='')
    model=scene_model(scratch,key,title,roster,'team_league' if team else 'individual_league');history=[]
    rng=random.Random(SEED+count);seatings=[]
    if team:
        teams=list(dict.fromkeys(p['team_id'] for p in roster));members={tid:[p['id'] for p in roster if p['team_id']==tid] for tid in teams};used={tid:0 for tid in teams}
        stride=next(n for n in (3,5,1) if len({(j*n)%len(teams) for j in range(4)})==4)
        for game in range(count*games_per_player//4):
            ids=[]
            for j in range(4):
                tid=teams[(game+j*stride)%len(teams)];ids.append(members[tid][used[tid]%len(members[tid])]);used[tid]+=1
            seatings.append(ids)
    else:
        for cycle in range(games_per_player):
            ids=[p['id'] for p in roster];rng.shuffle(ids);seatings.extend(ids[i:i+4] for i in range(0,count,4))
    for number,seats in enumerate(seatings,1):
        state,record,info=simulate(model,seats,key,number);history.append(record)
    set_live(model,state,history,info)
    if not finish:
        if team:
            ranking=reports.team_rows(model.state,model.event);wanted=[ranking[i]['id'] for i in (2,4,5,7)];seats=[next(p['id'] for p in roster if p['team_id']==tid) for tid in wanted]
        else:seats=[p['id'] for p in roster[:4]]
        state,_,_=simulate(model,seats,key,1000,partial=3);set_live(model,state,history)
        for i,p in enumerate(model.state['players']):p.update(tenpai=True,waits=[['3m','6m'],['2p','5p','8p'],['1s','4s'],['5z']][i],remaining=[7,0,None,2][i])
    else:model.settings.update(final_points_mode='pt',broadcast_scene='review')
    if team:
        model.event['player_plans']={p['id']:games_per_player+2 for p in roster};model.event['team_plans']={p['team_id']:48 for p in roster}
    else:model.event['planned_games']=games_per_player+4
    return model


def knockout(scratch,key,title,stage='ready',advance=2,count=16,*,roster=None,games_per_table=None):
    roster=synthetic_roster(count) if roster is None else roster
    model=scene_model(scratch,key,title,roster,'individual_knockout')
    if advance==3:
        nodes=[]
        def node(name,round_number,sources,passed):
            n=dict(id=identity(key+name),name=name,round=round_number,sources=sources,planned_games=games_per_table or 1,advance_count=passed,status='pending',winners=[]);nodes.append(n);return n
        a=node('首轮 A桌',1,[{'player':p['id']} for p in roster[:4]],3);b=node('首轮 B桌',1,[{'player':p['id']} for p in roster[4:8]],3)
        c=node('半决赛 A桌',2,[{'node':a['id'],'place':i} for i in (1,2,3)]+[{'player':roster[8]['id']}],2)
        d=node('半决赛 B桌',2,[{'node':b['id'],'place':i} for i in (1,2,3)]+[{'player':roster[9]['id']}],2)
        node('决赛',3,[{'node':n['id'],'place':i} for n in (c,d) for i in (1,2)],1)
        bracket={'nodes':nodes,'active_node':''}
    else:
        generated=schedule.generate(roster,games_per_table or (2 if advance==2 else 1),advance);mapping={n['id']:identity(key+'node'+str(i)) for i,n in enumerate(generated['nodes'])}
        for i,n in enumerate(generated['nodes']):
            n['id']=mapping[n['id']]
            for source in n['sources']:
                if 'node' in source:source['node']=mapping[source['node']]
        bracket=generated;bracket['nodes'][-1]['advance_count']=1
    model.event['bracket']=bracket;history=[];store=MemoryStore(model);serial=0
    selected=None
    for node in bracket['nodes']:
        should_finish=(stage=='finished' or (stage=='progress' and node['round']==1) or (stage=='one' and node['round']==1 and sum(n['round']==1 for n in bracket['nodes'][:bracket['nodes'].index(node)])<3))
        if not should_finish:
            if selected is None:selected=node
            continue
        for _ in range(node['planned_games']):
            serial+=1;seats=schedule.resolve_sources(model.event,node);state,record,info=simulate(model,seats,key,serial,node_id=node['id']);history.append(record);set_live(model,state,history,info)
        schedule.confirm_advancement(store,node['id']);bracket=model.event['bracket']
    if stage in ('progress','one'):
        node=next(n for n in model.event['bracket']['nodes'] if n['id']==selected['id']);seats=schedule.resolve_sources(model.event,node)
        if node['planned_games']>1:
            serial+=1;state,record,info=simulate(model,seats,key,serial,node_id=node['id']);history.append(record);set_live(model,state,history,info)
        serial+=1;state,_,_=simulate(model,seats,key,serial,partial=8 if stage=='one' else 3,node_id=node['id'],repeat=True)
        set_live(model,state,history);node['status']='running';model.event['bracket']['active_node']=node['id']
    elif stage=='ready':
        model.state=fresh_state(model,roster,[p['id'] for p in roster[:4]],key);model.settings['visible']['standings']=False
    active=next((n for n in model.event['bracket']['nodes'] if n['id']==model.state['broadcast'].get('active_node_id')),None)
    model.state['broadcast']['config'].update(sidebar_mode='qualification',qualification_slots=active['advance_count'] if active else advance,qualification_final=stage=='one')
    if stage=='finished':model.settings['final_points_mode']='pt'
    validate_event(model.event,model.state);return model


def from_legacy(scratch,key,source,label):
    model=MemoryModel(scratch/key);payload=load_demo_state(source)
    model.state=model.migrate(payload['state'],6);model.settings=default_settings();model.settings.update(payload['settings']);model.last_info=copy.deepcopy(payload.get('last_info'))
    kind='team_league' if model.state['broadcast']['config']['event_mode']=='team' else 'individual_league'
    model.event=new_event('【演示】'+label,kind,identity(key));model.event['planned_games']=24
    model.settings['local_demo_suite']=True
    for k in ('demo_active','demo_id'):model.settings.pop(k,None)
    return model


def build_models(scratch):
    models={}
    models['team_live']=league(scratch,'team_live',FULL_CASES[0][1],40,True,6)
    yield 'team_live',models['team_live']
    model=copy_scene(models['team_live'],scratch,'team_final',FULL_CASES[1][1]);history=copy.deepcopy(model.state['broadcast']['matches']);state,record,info=simulate(model,[p['id'] for p in model.state['players']],'team_final',70);history.append(record);set_live(model,state,history,info);model.settings.update(final_points_mode='pt',broadcast_scene='review');yield 'team_final',model
    models['individual_live']=league(scratch,'individual_live',FULL_CASES[2][1],16,False,8);yield 'individual_live',models['individual_live']
    yield 'large_individual',league(scratch,'large_individual',FULL_CASES[3][1],48,False,2)
    model=league(scratch,'team_pages',FULL_CASES[4][1],52,True,2);model.state['broadcast']['config']['sidebar_page']=1;yield 'team_pages',model
    yield 'knockout_ready',knockout(scratch,'knockout_ready',FULL_CASES[5][1])
    yield 'knockout_progress',knockout(scratch,'knockout_progress',FULL_CASES[6][1],'progress')
    yield 'knockout_final',knockout(scratch,'knockout_final',FULL_CASES[7][1],'finished')
    yield 'knockout_one',knockout(scratch,'knockout_one',FULL_CASES[8][1],'one',1)
    yield 'knockout_three',knockout(scratch,'knockout_three',FULL_CASES[9][1],'progress',3,10)
    model=from_legacy(scratch,'wait_states','live_full',FULL_CASES[10][1]);model.settings['riichi_payment_mode']='deferred'
    for i,player in enumerate(model.state['players']):
        player.update(tenpai=True,waits=[['2m','5m','8m'],['3p','6p','9p'],['1s','4s','7s'],['5z']][i],
            waits_no_yaku=[[],['3p','6p'],['1s'],['5z']][i],furiten=i>=2,remaining=[7,0,4,None][i])
    yield 'wait_states',model
    model=scene_model(scratch,'history_gaps',FULL_CASES[11][1],synthetic_roster(12));roster=model.state['broadcast']['roster']
    for i,p in enumerate(roster[:8]):
        p.update(base_points=[120,80,20,-10,-40,-60,-80,-120][i],games=10+i,
            result_baseline={'counts':[2,1,1,0] if i%2 else [0,0,0,0],'max_score':47000 if i%3==0 else None})
        if i%2:p['stats']=dict(hands=120,calls=30,riichi=25,wins=24,deal_ins=12,win_points=156000,deal_in_points=66000)
    roster[6]['status']='qualified';roster[7]['status']='eliminated';roster[11]['status']='inactive'
    from event_results import prepare_result,record_result
    tournament.configure(model.state,{'roster':roster});store=MemoryStore(model)
    for i in (0,4):record_result(store,prepare_result(model,[p['id'] for p in roster[i:i+4]],[41000,31000,19000,9000],title='仅补录最终点数・DEMO'))
    model.event['report_columns']={'individual':reports.preset_columns('individual_league','technical')};yield 'history_gaps',model
    model=copy_scene(models['team_live'],scratch,'custom_reports',FULL_CASES[12][1]);base=reports.allowed_columns('team_league');model.event['report_columns']={'individual':['name','points','rank']+[k for k in base if k not in ('name','points','rank')],'team':['team','rank','points','games','leader_gap','cutoff_gap','first','second','third','fourth']}
    model.event['report_headers']={'individual':{'name':'参赛者昵称','points':'赛季总成绩','rank':'当前名次','win_rate':'胡牌率','deal_in_rate':'铳率','max_score':'单场最高持点','top_rate':'头名率'},'team':{'team':'俱乐部名称','points':'团队总成绩','games':'出场进度','cutoff_gap':'晋级线分差'}};yield 'custom_reports',model
    model=copy_scene(models['individual_live'],scratch,'japanese_layout',FULL_CASES[13][1]);model.settings.update(language='ja_JP',console_font_size=14,overlay_font_scale=.9,player_group_linked=False,capture_bg='blue');model.state['broadcast']['config'].update(commentary_title='押し引き',commentary_body='デモ用の架空対局データです。',commentary_builtin=False);yield 'japanese_layout',model
    for key,label in DEMO_SCENARIOS:yield 'legacy_'+key,from_legacy(scratch,'legacy_'+key,key,label)


def copy_scene(original,scratch,key,title):
    model=MemoryModel(scratch/key);model.state=copy.deepcopy(original.state);model.settings=copy.deepcopy(original.settings);model.last_info=copy.deepcopy(original.last_info);model.event=copy.deepcopy(original.event)
    model.path=original.path
    model.event.update(id=identity(key),name='【演示】'+title);model.state['title']=model.event['name'];model.state['broadcast']['config']['title']=model.event['name'];return model


def save_scene(model,root,key,number,description,features,asset_root=None):
    event_dir=root/'library/events'/model.event['id'];event_dir.mkdir(parents=True,exist_ok=True)
    model.event['demo_suite']=dict(version=SUITE_VERSION,key=key,description=description,features=features,synthetic_results=True)
    model.event['name']=f'{number:02d} '+model.event['name']
    model.settings['local_demo_suite']=True
    for k in ('demo_active','demo_id'):model.settings.pop(k,None)
    validate_event(model.event,model.state);tournament.validate_broadcast(model.state);model.engine.validate(model.state)
    from media_store import register_asset_root
    project_root=Path(asset_root or root).resolve()
    register_asset_root(event_dir,project_root)
    manage_photos(model.state,event_dir,source_base=model.path,strict=True);model.path=event_dir
    model.asset_root=project_root
    # Call the actual save implementation; generation's no-op avoids repeated I/O.
    Model.save(model)
    portable=root/'portable'/f'{number:02d}-{key}.json';portable.parent.mkdir(exist_ok=True);model.export_data(portable)
    rows=reports.individual_rows(model.state,model.event)
    return dict(key=key,id=model.event['id'],name=model.event['name'],kind=model.event['kind'],description=description,features=features,
        players=len(rows),confirmed_matches=len(reports.confirmed_matches(model.state)),recorded_hands=sum(len(m['hands']) for m in reports.confirmed_matches(model.state)),
        current_hands=len(model.state['broadcast']['hands']),phase=model.state['phase'],portable=portable.relative_to(root).as_posix(),
        event_file=(event_dir/'session.json').relative_to(root).as_posix())


def exports(model,root,key):
    folder=root/'exports'/key;folder.mkdir(parents=True,exist_ok=True)
    reports.export_xlsx(model.state,model.event,folder/'standings.xlsx')
    if key in ('team_live','individual_live','large_individual','history_gaps','custom_reports'):
        reports.export_png(reports.report(model.state,model.event),folder/'individual.png')
        if model.event['kind']=='team_league':reports.export_png(reports.report(model.state,model.event,'team'),folder/'team.png')
    if model.event['bracket']['nodes']:
        image=schedule.render_bracket(model.state,model.event)
        if not image.save(str(folder/'bracket.png'),'PNG'):raise OSError('晋级图保存失败')
        reports.export_schedule_xlsx(model.state,model.event,folder/'schedule.xlsx')
    if key in ('team_live','wait_states','legacy_live_full','legacy_match_finished','legacy_yakuman_single','legacy_yakuman_multiple'):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage,QPainter
        from artwork import Artwork,Animation,render_image
        from broadcast_artwork import EXTRA_SIZES,draw_extension
        art=Artwork(model,Animation())
        for kind in ('qualification','statistics','chart','commentary'):
            image=QImage(*EXTRA_SIZES[kind],QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent);p=QPainter(image);draw_extension(p,kind,model);p.end();image.save(str(folder/(kind+'.png')))
        for i in range(4):render_image(art,'player',i).save(str(folder/f'player-{i+1}.png'))
        render_image(art,'round').save(str(folder/'round.png'));render_image(art,'dora').save(str(folder/'dora.png'))
        if model.last_info:render_image(art,'yaku').save(str(folder/'yaku.png'))


def validate_suite(root,asset_root=None):
    root=Path(root);catalog=json.loads((root/'catalog.json').read_text(encoding='utf-8'));seen=set();populated=set();totals=dict(events=0,confirmed_matches=0,recorded_hands=0)
    for entry in catalog['scenarios']:
        path=root/entry['event_file'];model=Model(path.parent,asset_root=asset_root or ROOT)
        state,settings,info,assets,event=model.read_data(path)
        if event['id'] in seen:raise ValueError('演示赛事编号重复')
        seen.add(event['id']);validate_event(event,state);model.engine.validate(state)
        rows=reports.individual_rows(state,event)
        for key in reports.TECH:
            if any(row.get(key) is not None for row in rows):populated.add(key)
        for n in event['bracket']['nodes']:
            if n['status']=='complete':
                schedule.validate_sources(state,event,n)
                if n['result_signature']!=schedule.signature(state,n):raise ValueError('演示晋级结果过期')
        portable=root/entry['portable'];payload=model.read_data(portable)
        if not payload[3] and any(p.get('photo') for p in state['broadcast']['roster']):raise ValueError('完整JSON没有内嵌照片')
        if payload[0]!=state or payload[4]!=event:raise ValueError('演示导出往返不一致')
        totals['events']+=1;totals['confirmed_matches']+=len(reports.confirmed_matches(state));totals['recorded_hands']+=sum(len(m['hands']) for m in reports.confirmed_matches(state))
    if set(reports.TECH)-populated:raise ValueError('未覆盖的技术指标：'+','.join(set(reports.TECH)-populated))
    totals['technical_metrics']=len(populated);return totals


def generate(output=None,asset_root=None):
    output=Path(output or ROOT/'demo-suite').resolve()
    project_root=Path(asset_root or ROOT).resolve()
    if not output.is_relative_to(ROOT.resolve()):raise ValueError('演示输出必须位于项目目录内')
    if output.exists():raise ValueError('输出目录已存在；为保留已操作的演示进度，请指定另一个新目录')
    missing=[ROOT/'resources/demo/avatars'/f'{i}.png' for i in range(1,17) if not (ROOT/'resources/demo/avatars'/f'{i}.png').is_file()]
    if missing:
        from demo_data import _avatar_files
        with tempfile.TemporaryDirectory(prefix='.demo-avatars-',dir=ROOT) as folder:
            _avatar_files(Path(folder))
            for target in missing:target.write_bytes((Path(folder)/'avatars'/target.name).read_bytes())
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.demo-suite-build-',dir=output.parent) as temporary:
        stage=Path(temporary)/'suite';stage.mkdir();entries=[];lookup={k:(title,description,features) for k,title,description,features in FULL_CASES}
        with tempfile.TemporaryDirectory(prefix='scratch-',dir=temporary) as scratch:
            for number,(key,model) in enumerate(build_models(Path(scratch)),1):
                if key in lookup:_,description,features=lookup[key]
                else:
                    source=key.removeprefix('legacy_');description=DEMO_DESCRIPTIONS[source];features=['legacy_scenario',source]
                entries.append(save_scene(model,stage,key,number,description,features,asset_root=project_root));exports(model,stage,key)
                print(f'{number:02d} {key}: {entries[-1]["players"]} players, {entries[-1]["confirmed_matches"]} matches',flush=True)
        atomic_json(stage/'library/library.json',{'version':1,'active_id':entries[0]['id'],'events':[e['id'] for e in entries]})
        atomic_json(stage/'catalog.json',dict(version=SUITE_VERSION,seed=SEED,synthetic_results=True,scenarios=entries))
        totals=validate_suite(stage,asset_root=project_root);atomic_json(stage/'validation.json',dict(status='passed',**totals))
        lines=['# 完整本地演示赛事库','','双击项目根目录的 `START-Full-Demo.cmd`，然后从“赛事库”切换以下场景。','','这是独立赛事库，不读取或覆盖正式 `data/`。照片素材沿用项目已有本地素材；比赛成绩和统计均为模拟。','',
            f'共 {totals["events"]} 个场景、{totals["confirmed_matches"]} 份已确认半庄快照、{totals["recorded_hands"]} 条逐局记录，覆盖全部 {totals["technical_metrics"]} 个可选技术统计字段。部分场景复用同一联赛底稿，合计数不是互不重复的现实比赛。','',
            '| 场景 | 数据规模 | 演示内容 |','| --- | --- | --- |']
        for entry in entries:lines.append(f'| {entry["name"]} | {entry["players"]}人 / {entry["confirmed_matches"]}半庄 | {entry["description"]} |')
        lines += ['', '## 推荐顺序','','1. 01团队联赛：打开榜单与导出，切换个人/团队榜；在播出中查看红色四队侧榜及底部队伍积分。',
            '2. 03个人联赛：查看每人已打/总场次，应用ML完整数据和技术统计预设。',
            '3. 06—10淘汰赛：从未开赛、进行中到全程完成，比较四进一/二/三与种子直入。07可继续打当前半决赛的第二场；09可直接演示南四晋级条件。',
            '4. 11听牌对照：四席分别展示普通有役、混合无役、振听与无役振听，包含0和未填写山存。',
            '5. 12统计缺失：对照可靠基数、未知数据和只补录结果，未知比率显示“—”。',
            '6. 13报表：所有技术列、自定义表头、调序、Excel及完整PNG导出。',
            '7. 15—26原有场景：检查开局、各类副露、五宝牌、长名字、同分/负分、单倍/双倍役满及支付动画。',
            '', '## 文件与使用','','- `library/`：可直接启动并继续操作的独立赛事库。','- `portable/`：每场完整JSON，内嵌照片，可在正式软件中“导入为独立赛事”。',
            '- `exports/`：预先生成的彩色Excel、个人/团队PNG、晋级图和播出模块示例。','- `catalog.json`：场景和功能覆盖目录。','- `validation.json`：校验结果。',
            '', '## 操作提示','','- 本场未到终局前，正式榜单保持开场前已确认数据；终局点击“记录本场积分”才更新。',
            '- 已经结束的场景可切换最终点数/结算pt，重播图表；要重新看支付动画，可在内置演示页载入对应场景后点击重播。',
            '- 工作台“榜单与导出”的预设、列设置、表头改名会保存到当前演示赛事。',
            '- 多次操作不会改动 `portable/` 中的初始JSON，想恢复某个场景可将该JSON重新导入为独立赛事。',
            '- `legacy_m_tournament`为原有公开局面复现，其余比赛过程/结果都是模拟；真实姓名或照片不代表真实成绩。',
            '- 新生成的数据已经通过引擎守恒、归档、赛事元数据、晋级关系、内嵌照片和JSON往返校验。',
            '', '正式赛事仍通过原 `START-Windows.cmd` 打开。']
        (stage/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        manifest=[]
        for path in sorted(stage.rglob('*')):
            if path.is_file():manifest.append(dict(path=path.relative_to(stage).as_posix(),bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        atomic_json(stage/'manifest.json',manifest);stage.replace(output)
    return totals


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);parser.add_argument('--validate',type=Path);parser.add_argument('--asset-root',type=Path);args=parser.parse_args()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase,QFont
    app=QApplication.instance() or QApplication([])
    for font in (Path('C:/Windows/Fonts/msyh.ttc'),ROOT/'resources/NotoSansCJKjp-Regular.otf'):
        if font.exists():QFontDatabase.addApplicationFont(str(font))
    import artwork
    artwork.FONT=next((f for f in ('Microsoft YaHei','Noto Sans CJK JP') if f in QFontDatabase.families()),'Sans Serif');app.setFont(QFont(artwork.FONT,10))
    print(json.dumps(validate_suite(args.validate,args.asset_root) if args.validate else generate(args.output,args.asset_root),ensure_ascii=False))
