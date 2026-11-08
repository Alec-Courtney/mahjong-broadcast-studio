"""Final-score entry for other tables without inventing hand statistics."""
import copy
import uuid
import tournament


def prepare_result(model, seats, scores, node_id='', match_id=None, title='补录半庄',initial_dealer=0):
    if not isinstance(seats,list) or len(seats)!=4 or len(set(seats))!=4:raise ValueError('请选择四名不同选手')
    roster={p['id']:p for p in model.state['broadcast']['roster']}
    if any(pid not in roster for pid in seats):raise ValueError('参赛人不在赛事名册中')
    if not isinstance(scores,list) or len(scores)!=4:raise ValueError('请输入四人的最终持点')
    for value in scores:
        tournament._integer(value,'最终持点',-1000000000)
        if value%100:raise ValueError('最终持点须为百点整数')
    tournament._integer(initial_dealer,'起家席位',0,3)
    prior=next((m for m in model.state['broadcast']['matches'] if m['id']==match_id),None)
    if match_id and not prior:raise ValueError('待更正成绩不存在')
    if prior and (prior['id']==model.state['id'] or prior.get('source')!='manual'):
        raise ValueError('直播成绩请在导播台更正；此处仅更正补录成绩')
    if prior and prior.get('seats') and seats != prior['seats']:
        raise ValueError('更正只调整最终持点，参赛人和席位保留')
    if not prior:
        from event_roster import require_visible_seats
        require_visible_seats(model.state, seats)
    if prior:node_id=prior.get('bracket_node_id','')
    if node_id:
        from event_schedule import node_matches,resolve_sources,validate_sources
        node=next((n for n in model.event['bracket']['nodes'] if n['id']==node_id),None)
        if not node:raise ValueError('淘汰桌不存在')
        validate_sources(model.state,model.event,node)
        if model.state['broadcast'].get('active_node_id')==node_id and model.state['phase']!='finished':raise ValueError('这桌正在导播台进行，请在导播台完成并确认当前半庄')
        if seats!=resolve_sources(model.event,node):raise ValueError('请使用该淘汰桌的四个席位')
        if not prior and len(node_matches(model.state,node_id))>=node['planned_games']:raise ValueError('本桌计划半庄已全部录入')
        title=node['name']
    state=copy.deepcopy(model.state);state['initialDealer']=initial_dealer;state['phase']='finished';state.pop('finalRanking',None)
    if prior:state['broadcast']['config']=copy.deepcopy(prior['config'])
    state['rules']['startPoints']=state['broadcast']['config']['start_points']
    state['players']=[dict(id=pid,name=roster[pid]['name'],score=score,photo=roster[pid].get('photo',''),team_logo=roster[pid].get('team_logo','')) for pid,score in zip(seats,scores)]
    results=tournament._match_results(state,scores=scores)
    if prior:
        if set(seats)!={r['id'] for r in prior['results']}:raise ValueError('更正只调整最终持点，参赛人保留')
        profiles={r['id']:r for r in prior['results']}
        for result in results:
            for key in ('name','team','team_id','color','photo'):result[key]=profiles[result['id']].get(key,result.get(key,''))
            if 'team_logo' in profiles[result['id']]:result['team_logo']=profiles[result['id']]['team_logo']
            else:result.pop('team_logo',None)
    record=dict(id=match_id or uuid.uuid4().hex,title=title,committed=True,completed=True,source='manual',seats=seats[:],initial_dealer=initial_dealer,hands=[],
        results=results,initial_scores=[state['rules']['startPoints']]*4,initial_players=[],partial=False,
        config=copy.deepcopy(state['broadcast']['config']))
    if node_id:record['bracket_node_id']=node_id
    return record


def record_result(store, record):
    model=store.model;record=copy.deepcopy(record)
    # Re-validate against the current roster and schedule, after the preview.
    by_id={r['id']:r for r in record['results']};ordered=[by_id[pid] for pid in record['seats']];prior=next((m for m in model.state['broadcast']['matches'] if m['id']==record['id']),None)
    checked=prepare_result(model,[r['id'] for r in ordered],[r['score'] for r in ordered],
        node_id=record.get('bracket_node_id',''),match_id=record['id'] if prior else None,title=record['title'],initial_dealer=record.get('initial_dealer',0))
    # Compare the whole result snapshot, including team identity and presentation
    # metadata. Equal points/ranks alone can silently book a new result against
    # a team that was changed in another editor after this preview was opened.
    # Corrections retain their original historical profiles in prepare_result.
    if sorted(checked['results'],key=lambda r:r['id'])!=sorted(record['results'],key=lambda r:r['id']):
        raise ValueError('计分规则或名册在预览后已改变，请重新预览')
    return model.records.write_manual(record)
