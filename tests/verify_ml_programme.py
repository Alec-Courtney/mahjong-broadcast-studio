"""Independent arithmetic and portable-media audit of the condensed ML show.

The ledger verifier uses standard-library arithmetic, not the application's
scoring engine, statistics, renderer or demo action helpers. A separate import
check restores only one exported JSON in a disposable project directory.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'previews/latest/programme'
FIELDS=('hands','calls','riichi','wins','deal_ins','win_points','deal_in_points')


def check(condition,message):
    if not condition:raise ValueError(message)


def same(actual,expected):
    return isinstance(actual,(int,float)) and math.isfinite(actual) and abs(actual-expected)<1e-8


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def scrub_media(value):
    if isinstance(value,dict):return {key:scrub_media(child) for key,child in value.items() if key not in ('photo','team_logo')}
    if isinstance(value,list):return [scrub_media(child) for child in value]
    return value


def audit_ledger(state,postmatch,verification,source):
    broadcast=state['broadcast'];rules=state['rules'];players=state['players'];hands=broadcast['hands']
    source_state=source['state'];source_broadcast=source_state['broadcast']
    check(rules==source_state['rules'],'The local ML rules were changed for the review')
    check(rules['startPoints']==25000 and state['accountingTotal']==100000,'The actual ML start/accounting total was not retained')
    check(state['phase']=='finished' and len(players)==4,'The displayed match is not finished')
    check(not state.get('accountingAdjustment',0),'The demonstration changed scores manually')
    check(not broadcast['partial'],'The current ledger is only a partial match')
    check(len(hands)==9 and len({hand['id'] for hand in hands})==9,'Expected nine unique complete hands')
    check([hand['round_index'] for hand in hands]==[0,1,1,2,3,4,5,6,7],'The east/south hand progression is incomplete')
    check([hand['honba'] for hand in hands]==[0,0,1,0,0,0,0,0,0],'The repeat hand or honba progression is wrong')
    identities=[player['id'] for player in players]
    check(len(set(identities))==4 and len({p['team_id'] for p in players})==4,'Four distinct people and teams are required')
    check(broadcast['initial_scores']==[rules['startPoints']]*4,'The current match did not start at the local rule points')
    scores=broadcast['initial_scores'][:];pot=0;conserved=sum(scores);trail=[scores[:]]
    counts=[dict.fromkeys(FIELDS,0) for _ in players];records=[]
    expected_calls=((0,0,1,0),(1,0,0,0),(0,0,0,0),(1,0,0,1),(0,0,0,0),
                    (0,0,0,0),(1,0,0,0),(0,0,0,0),(0,0,0,0))
    ceil100=lambda amount:math.ceil(amount/100)*100
    for index,hand in enumerate(hands):
        result=hand['result'];profiles=hand['players'];label=hand['label']
        check([profile['id'] for profile in profiles]==identities,label+': changed table identities')
        declarations=[int(profile['riichi']) for profile in profiles]
        check(all(value in (0,1) for value in declarations),label+': invalid declaration count')
        before=[score-1000*declared for score,declared in zip(scores,declarations)];pot+=1000*sum(declarations)
        check(before==hand['before_scores']==result['beforeScores'],label+': score changed outside a legitimate declaration/settlement')
        check(pot==result['potBefore'],label+': incorrect deposit carry')
        payments=[];awards=[];deltas=[0]*4
        if result['kind']=='draw':
            tenpai=result['tenpai'];number=len(tenpai)
            check(len(set(tenpai))==number and all(seat in range(4) for seat in tenpai),label+': invalid tenpai list')
            if 0<number<4:
                deltas=[rules['notenPayment']//number if seat in tenpai else -rules['notenPayment']//(4-number) for seat in range(4)]
        else:
            han,fu=result['han'],result['fu']
            check(1<=han<=4 and not result['yakuman'],label+': result is outside this demonstration plan')
            base=fu*2**(han+2)
            if base>=2000 or rules['kiriageMangan'] and base==1920:base=2000
            winner,dealer,honba=result['winner'],result['dealer'],result['honba']
            if result['kind']=='ron':
                amount=ceil100(base*(6 if winner==dealer else 4))+honba*rules['honbaRon']
                payments.append(dict(from_seat=result['loser'],to_seat=winner,amount=amount))
            else:
                check(result['kind']=='tsumo',label+': unexpected result kind')
                for seat in range(4):
                    if seat!=winner:
                        amount=ceil100(base*(2 if winner==dealer or seat==dealer else 1))+honba*rules['honbaTsumoEach']
                        payments.append(dict(from_seat=seat,to_seat=winner,amount=amount))
            for payment in payments:
                deltas[payment['from_seat']]-=payment['amount'];deltas[payment['to_seat']]+=payment['amount']
            if pot:awards=[dict(player=winner,amount=pot)];deltas[winner]+=pot;pot=0
        observed=[dict(from_seat=p['from'],to_seat=p['to'],amount=p['amount']) for p in result['payments']]
        check(observed==payments,label+': payment amount differs from independent fu/han/honba arithmetic')
        check(result['potAwards']==awards and result['deltas']==deltas,label+': deposit award or delta mismatch')
        scores=[score+delta for score,delta in zip(before,deltas)]
        check(scores==hand['after_scores']==result['afterScores'],label+': final scores mismatch')
        check(result['potAfter']==pot and sum(scores)+pot==conserved,label+': point/deposit conservation failed')
        trail.append(scores[:])
        for seat,profile in enumerate(profiles):
            won=result['winner']==seat;lost=result['kind']=='ron' and result['loser']==seat
            values=dict(hands=1,calls=expected_calls[index][seat],riichi=declarations[seat],wins=int(won),deal_ins=int(lost),
                win_points=sum(p['amount'] for p in payments if p['to_seat']==seat) if won else 0,
                deal_in_points=sum(p['amount'] for p in payments if p['from_seat']==seat) if lost else 0)
            for field,value in values.items():
                check(profile[field]==value,label+': per-hand '+field+' mismatch')
                counts[seat][field]+=value
        records.append(dict(id=hand['id'],label=label,before_scores=before,after_scores=scores[:],pot_after=pot,payments=payments,pot_awards=awards))
    check(scores==[p['score'] for p in players] and pot==state['pot'],'The exported final scores differ from the independent ledger')
    committed=[match for match in broadcast['matches'] if match.get('committed')]
    current=[match for match in committed if match['id']==state['id']]
    historic={match['id']:match for match in committed if match['id']!=state['id']}
    original={match['id']:match for match in source_broadcast['matches'] if match.get('committed')}
    check(len(current)==1 and current[0]['hands']==hands,'The current match is missing, duplicated or has different recorded hands')
    check(original and set(historic)==set(original),'The local historical matches were changed or double-counted')
    for identity,match in historic.items():
        check(scrub_media(match)==scrub_media(original[identity]),'A historical match was rewritten: '+identity)
    ranks=[1+sum(other>score for other in scores) for score in scores]
    check(len(set(ranks))==4 and not rules['okaEnabled'],'Unexpected tie/oka outside this local ML review')
    points=[(score-rules['returnPoints'])/1000+rules['uma'][rank-1] for score,rank in zip(scores,ranks)]
    check(same(sum(points),0.),'Final pt does not balance under the supplied ML rules')
    check(postmatch['hands']==9 and postmatch['draws']==1 and len(postmatch['labels'])==10,'Post-match hand counts/graph labels mismatch')
    rows={row['id']:row for row in postmatch['rows']}
    check(set(rows)==set(identities),'The post-match display contains bench players')
    results={row['id']:row for row in verification['match_summary']['final_results']}
    statistics={row['id']:row for row in verification['match_summary']['statistics']}
    for seat,identity in enumerate(identities):
        row=rows[identity]
        check(row['score']==scores[seat] and row['rank']==ranks[seat] and same(row['points'],points[seat]),'Post-match result differs from ledger: '+identity)
        check(row['scores']==[step[seat] for step in trail],'A graph node is missing or fabricated: '+identity)
        for field in ('riichi','wins','deal_ins'):check(row[field]==counts[seat][field],'Post-match count mismatch: '+identity+'/'+field)
        check(results[identity]['score']==scores[seat] and results[identity]['rank']==ranks[seat] and same(results[identity]['points'],points[seat]),'Renderer final result mismatch')
        for field in FIELDS:check(statistics[identity][field]==counts[seat][field],'Renderer statistic mismatch: '+identity+'/'+field)
        for rate,field in (('call_rate','calls'),('riichi_rate','riichi'),('win_rate','wins'),('deal_in_rate','deal_ins')):
            check(same(statistics[identity][rate],counts[seat][field]/9),'Renderer rate mismatch: '+identity+'/'+rate)
    summary=verification['match_summary']
    check(summary['completed_hands']==9 and summary['draws']==1 and summary['roster_count']==40,'Renderer summary count mismatch')
    check(summary['committed_current_match_count']==1 and summary['historical_committed_matches']==len(original),'Renderer summary archive count mismatch')
    check(summary['final_scores']==scores and summary['accounting_total']==conserved,'Renderer summary point total mismatch')
    return dict(status='PASS',starting_points=rules['startPoints'],accounting_total=conserved,current_committed_matches=1,
        historical_matches_preserved=len(original),completed_hands=9,draws=1,final_scores=scores,final_pt=points,
        progression_points_per_player=len(trail),statistics=[dict(id=identity,**counts[seat]) for seat,identity in enumerate(identities)],
        all_hands_conserve_points_and_pot=True,no_manual_score_edit=True,no_historical_double_count=True,hands=records)


def audit_identity_and_timeline(state,verification,source):
    source_roster={p['id']:p for p in source['state']['broadcast']['roster']};roster=state['broadcast']['roster']
    check(len(roster)==40 and {p['id'] for p in roster}==set(source_roster),'The complete local 40-person roster was changed')
    for profile in roster:
        original=source_roster[profile['id']]
        for field in ('name','team','team_id','color'):check(profile[field]==original[field],'A demo identity/colour changed: '+profile['id'])
    active=[p['id'] for p in source['state']['players']]
    check([p['id'] for p in state['players']]==active,'The local selected players were replaced')
    openings=verification['opening_teams'];check(len(openings)==4,'Opening lacks four team columns')
    for entry in openings:
        members=[p for p in roster if p['team_id']==entry['id']]
        check(entry['members']==len(members)==4,'Opening roster is incomplete')
        check(entry['name']==members[0]['team'] and entry['color']==members[0]['color'],'Opening team identity/colour mismatch')
    for entry,identity in zip(verification['active_players'],active):
        original=source_roster[identity]
        check(entry['id']==identity and entry['name']==original['name'] and entry['team']==original['team'] and entry['color']==original['color'],'Opening starting-player identity mismatch')
    check(verification['visible_hands']==['east-1','east-2-draw','south-4'],'Representative live-hand selection changed')
    hidden=verification['hidden_hands']
    check([h['id'] for h in hidden]==['east-2-repeat','east-3','east-4','south-1','south-2','south-3'],'Hidden middle hands are missing')
    check(all(h['visual_omitted'] for h in hidden) and verification['hidden_actions_emit_no_broadcast_signals'],'Off-screen hands leaked declaration animations')
    cues=verification['riichi_effect_starts'];check(len(cues)==5,'Expected exactly five visible declarations')
    check([cue['time_s'] for cue in cues]==[26.,28.5,60.,62.5,82.],'Sound declarations are not actual FIFO starts')
    check([cue['sequence_id'] for cue in cues]==[1,2,3,4,5],'Programme sound cue IDs are not unique')
    check([(cue['manager_generation'],cue['queue_sequence_id']) for cue in cues]==[(1,1),(1,2),(1,3),(1,4),(2,1)],'Manager recreation duplicated or lost a declaration cue')
    for cue in cues:
        original=source_roster[cue['player_id']]
        check(cue['team_id']==original['team_id'] and cue['team_color']==original['color'],'Queued declaration belongs to another team')
        check(cue['duration_seconds']==2.5,'A declaration has been accelerated')
    decrement=[event for event in verification['commands'] if '余枚' in event['label']]
    check(decrement==[dict(time_s=29.6,label='余枚一次5减至4')],'Remaining-tile decrement repeats or reaches zero')
    check(verification['opening_duration_seconds']==18. and verification['playback_speed']==1.,'Opening/motion timing has been accelerated')
    start=verification['postmatch_start_seconds'];duration=verification['duration_seconds']
    check(verification['music_intervals']==[[0.,18.],[start,duration]],'Music is scheduled inside the live match')
    check(verification['live_interval']==[18.,start] and verification['no_music_in_live_match'],'Live stage declares background music')
    check(verification['stable_final_pt_seconds']>=3.,'Final pt does not remain stable before post-match')
    for field in ('complete_chart_interval','complete_statistics_interval'):
        interval=verification[field];check(same(interval[1]-interval[0],15.),'A complete post-match page is not held for 15 seconds')
    check(not verification['formal_data_changed'] and verification['temporary_project_removed'],'Render touched formal data or left its temporary project')
    return dict(status='PASS',source_roster=40,opening_teams=4,opening_members_per_team=4,visible_hands=3,offscreen_hands=6,
        declaration_starts=[cue['time_s'] for cue in cues],unique_programme_sequence_ids=True,
        remaining_decrement=dict(time_s=29.6,from_value=5,to_value=4,count=1),music_intervals=verification['music_intervals'],
        chart_complete_hold_seconds=15.,statistics_complete_hold_seconds=15.,stable_final_pt_seconds=verification['stable_final_pt_seconds'])


def audit_portable(export_path,source,expected):
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen');sys.path.insert(0,str(ROOT/'src'))
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from identity_media import load_identity_image
    from media_store import resolve_media_path
    from model import Model
    from postmatch_artwork import prepare_postmatch
    document=json.loads(export_path.read_text(encoding='utf8'));assets=document['assets'];source_hash=sha(export_path)
    opening_reference=document['state']['broadcast']['config'].get('opening_title','')
    check(len(assets)==41+bool(opening_reference),'Expected 40 portraits, a corner logo and any selected title image')
    decoded={}
    for key,record in assets.items():
        raw=base64.b64decode(record['data'],validate=True)
        check(hashlib.sha256(raw).hexdigest()==record['sha256'],'Embedded asset SHA mismatch: '+key);decoded[key]=raw
    app=QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix='tmp-ml-portable-',dir=ROOT/'tests') as temporary:
        root=Path(temporary);transfer=root/'transfer';transfer.mkdir();detached=transfer/'only.json';detached.write_bytes(export_path.read_bytes())
        project=root/'p';model=Model(project/'data',asset_root=project);model.import_data(detached,replace_settings=True,clear_history=True)
        check(list(transfer.iterdir())==[detached],'The transfer unexpectedly has sibling source media')
        for reference,raw in decoded.items():
            path=resolve_media_path(model.path,reference).resolve()
            check(path.is_relative_to(project) and path.is_file() and path.read_bytes()==raw,'Imported media still depends on the original project')
            check(not QImage(str(path)).isNull(),'An embedded image cannot be decoded')
        source_roster={p['id']:p for p in source['state']['broadcast']['roster']}
        for profile in model.state['broadcast']['roster']:
            original=source_roster[profile['id']];reference=original['photo']
            check(reference.startswith('demo://'),'The source portrait is not a bundled local asset')
            path=(ROOT/'resources/demo'/reference[len('demo://'):]).resolve()
            check(hashlib.sha256(decoded[profile['photo']]).hexdigest()==sha(path),'A demo portrait was replaced')
        opening=model.state['broadcast']['config']['opening_title'];title_size=None
        if opening_reference:
            image=load_identity_image(model,opening)
            check(opening==opening_reference and opening in decoded and image is not None,
                  'The selected title did not survive the detached import')
            title_size=[image.width(),image.height()]
        else:
            check(model.state['broadcast']['config']['opening_title_text_only'],
                  'The demo text title did not survive the detached import')
        check(len(model.state['broadcast']['roster'])==40,'The restored roster is incomplete')
        model.save();reopened=Model(project/'data',asset_root=project);postmatch=prepare_postmatch(reopened)
        check([p['score'] for p in reopened.state['players']]==expected['final_scores'],'A restart changed the final scores')
        results={row['id']:row for row in postmatch['rows']}
        for seat,player in enumerate(reopened.state['players']):
            check(same(results[player['id']]['points'],expected['final_pt'][seat]),'A detached import/restart changed pt')
            check(len(results[player['id']]['scores'])==10,'A detached import/restart lost graph nodes')
        del model,reopened;app.processEvents()
    check(sha(export_path)==source_hash,'The export was changed during verification')
    return dict(status='PASS',embedded_assets=len(assets),portrait_assets=40,all_asset_sha_and_image_decodes_passed=True,
        original_ml_portrait_sha_verified=True,only_json_transferred=True,source_directory_required=False,
        opening_title_native_size=title_size,restart_preserves_scores_pt_and_graphs=True,temporary_project_removed=not Path(temporary).exists(),
        formal_data_read_or_written=False,export_sha256=source_hash)


def audit_silent_video(folder,verification):
    """Verify encoded frames even after a delivered movie's intermediate is removed."""
    movie=folder/verification['silent_video'];intermediate=movie.is_file()
    if not intermediate:movie=folder/verification['video']
    check(movie.is_file(),'Neither the render intermediate nor the delivered video exists')
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries',
        'format=duration:stream=codec_type,width,height,avg_frame_rate,nb_frames','-of','json',str(movie)]))
    streams=probe['streams'];videos=[stream for stream in streams if stream['codec_type']=='video']
    check(len(videos)==1,'Expected exactly one encoded video stream')
    if intermediate:check(len(streams)==1,'The offline render unexpectedly contains audio or extra streams')
    video=videos[0]
    check((video['width'],video['height'],video['avg_frame_rate'])==(1920,1080,'60/1'),'Wrong silent video size/frame rate')
    frames=round(verification['duration_seconds']*60)
    check(int(video['nb_frames'])==frames,'The silent video lost or duplicated frames')
    check(abs(float(probe['format']['duration'])-frames/60)<1/60,'Silent container duration differs from its frame timeline')
    decoded=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-v','error','-i',str(movie),
        '-map','0:v:0','-f','null','-'],capture_output=True,timeout=180)
    check(decoded.returncode==0 and not decoded.stderr,'Full silent-video decode failed: '+decoded.stderr.decode('utf8',errors='replace')[-2000:])
    return dict(status='PASS',file=movie.name,sha256=sha(movie),frames=frames,
        full_video_decode_passed=True,intermediate_retained=intermediate,
        audio_streams=sum(stream['codec_type']=='audio' for stream in streams),probe=probe)


def audit_chapters(folder,verification):
    document=json.loads((folder/'chapters.json').read_text(encoding='utf8'));chapters=document['chapters']
    check(document['video']==verification['video'] and same(document['duration_seconds'],verification['duration_seconds']),'Chapter file refers to another programme')
    check(len(chapters)==17 and len({row['id'] for row in chapters})==17,'The complete programme chapters are missing or duplicated')
    starts=[row['start'] for row in chapters]
    check(starts==sorted(starts) and starts[0]==0 and starts[-1]<verification['duration_seconds'],'Chapter order is invalid')
    images=[]
    from PySide6.QtGui import QImage
    for row in chapters:
        image=(folder/row['keyframe']).resolve()
        check(image.is_relative_to(folder) and image.is_file(),'Missing chapter keyframe: '+row['id'])
        loaded=QImage(str(image));check((loaded.width(),loaded.height())==(1920,1080),'Invalid chapter keyframe image: '+row['id'])
        images.append(dict(id=row['id'],start=row['start'],file=row['keyframe'],sha256=sha(image)))
    return dict(status='PASS',chapters=17,keyframes=images)


def verify(folder,output=None,require_rendered=False):
    folder=Path(folder).resolve();output=Path(output).resolve() if output else folder/'analysis/independent-programme-audit.json'
    paths={name:folder/name for name in ('final-state.json','postmatch-data.json','verification.json','ML_演示数据.json','chapters.json')}
    inputs={name:dict(bytes=path.stat().st_size,sha256=sha(path)) for name,path in paths.items()}
    state=json.loads(paths['final-state.json'].read_text(encoding='utf8'))
    postmatch=json.loads(paths['postmatch-data.json'].read_text(encoding='utf8'))
    verification=json.loads(paths['verification.json'].read_text(encoding='utf8'))
    source_path=ROOT/verification['source_data'];source=json.loads(source_path.read_text(encoding='utf8'))
    check(sha(source_path)==verification['source_sha256'],'The renderer used a different local source dataset')
    ledger=audit_ledger(state,postmatch,verification,source);timeline=audit_identity_and_timeline(state,verification,source)
    portable=audit_portable(paths['ML_演示数据.json'],source,ledger)
    chapters=audit_chapters(folder,verification)
    silent=audit_silent_video(folder,verification) if verification['video_rendered'] else dict(status='NOT_RENDERED')
    media=dict(status='NOT_ENCODED',actual_encoded_audio_not_yet_signed=True)
    if require_rendered:
        check(verification['video_rendered'],'A dry-run cannot be signed as a rendered programme')
        movie=folder/verification['video'];check(movie.is_file(),'The final video is missing')
        probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type,width,height,avg_frame_rate','-of','json',str(movie)]))
        video=next(stream for stream in probe['streams'] if stream['codec_type']=='video')
        check((video['width'],video['height'],video['avg_frame_rate'])==(1920,1080,'60/1'),'Wrong encoded video size/frame rate')
        check(any(stream['codec_type']=='audio' for stream in probe['streams']),'The final programme has no audio track')
        check(abs(float(probe['format']['duration'])-verification['duration_seconds'])<1/60,'Encoded duration differs from the programme')
        media=dict(status='PASS',final_video_sha256=sha(movie),probe=probe,
            phase_audio_sample_content_audit='separate audio mixer proof; outside this script',actual_encoded_audio_not_yet_signed=False)
    for name,path in paths.items():check(sha(path)==inputs[name]['sha256'],'Input changed during the independent audit: '+name)
    scope='rendered programme and detached export' if require_rendered else ('rendered silent video, final data and detached export; final audio pending' if verification['video_rendered'] else 'dry-run data and detached export only')
    report=dict(status='PASS',scope=scope,
        finished_utc=datetime.now(timezone.utc).isoformat(),inputs=inputs,source_data=str(source_path.relative_to(ROOT)),source_sha256=sha(source_path),
        ledger=ledger,timeline_and_identity=timeline,portable=portable,chapters=chapters,silent_video=silent,media=media,auditor_source_sha256=sha(Path(__file__)))
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--folder',type=Path,default=DEFAULT)
    parser.add_argument('--output',type=Path);parser.add_argument('--require-rendered',action='store_true')
    args=parser.parse_args();result=verify(args.folder,args.output,args.require_rendered)
    print(json.dumps(dict(status=result['status'],scope=result['scope'],hands=result['ledger']['completed_hands'],
        current_committed=1,historic_preserved=result['ledger']['historical_matches_preserved'],assets=result['portable']['embedded_assets'],final_scores=result['ledger']['final_scores']),ensure_ascii=True))
