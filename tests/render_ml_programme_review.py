"""A local-ML opening-to-ending programme, with representative live hands."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
from PySide6.QtCore import QRectF,Qt
from PySide6.QtGui import QColor,QPainter
from PySide6.QtWidgets import QApplication
from demo_data import load_into
from glossary import get_term
from match_opening import OPENING_DURATION,draw_opening,prepare_opening
from model import Model,payment_lines
from overlays import OverlayManager
from postmatch_artwork import draw_postmatch,prepare_postmatch,prepare_postmatch_model
from postmatch_sequence import duration as postmatch_duration,timeline as postmatch_timeline
from broadcast_test_support import background,formal_inventory
from broadcast_test_support import initialize_fonts
from sidebar_paging import sidebar_page_data
from team_match_demo_plan import HANDS,apply_action,assert_start,settle_hand
import tournament

FPS=60
SIZE=(1920,1080)
VIDEO_NAME='ML_开场至结束_精简对局演示.mp4'
SILENT_NAME='.ml-programme-silent.mp4'
VISIBLE_HANDS=(0,1,8)
SKIP_AT=77.
FINAL_AT=79.
FINAL_SETTLE=92.
POSTMATCH_AT=112.
DURATION=math.ceil((POSTMATCH_AT+postmatch_duration()+.5)*FPS)/FPS


def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


class NullAudio:
    """Offline rendering records cues for the later mix, never an audio device."""
    def play(self,*_):pass
    def stop(self):pass
    def close(self):pass


def validate_model(model):
    """Validate the actual supplied rules; their accounting total is 100000."""
    state=copy.deepcopy(model.state);state['manualDisplay']=False
    result=model.engine.invoke('validateState',state)
    if not result.get('valid'):raise AssertionError(result)
    if sum(player['score'] for player in state['players'])+state['pot']!=state['accountingTotal']:
        raise AssertionError('Points plus table deposits are not conserved')


def programme_model(directory,family):
    directory=Path(directory)
    model=Model(directory/'data',asset_root=directory)
    load_into(model,'team_complete');model.start_match()
    model.field('title','M.League 名册 · 动画流程演示')
    model.configure_broadcast(dict(title='M.League 名册 · 动画流程演示',postmatch_chart_hold=15.,postmatch_stats_hold=15.,
        settlement_enabled=False,sidebar_auto_page=False,sidebar_page_size=4))
    model.configure_opening({'opening_title_text_only':True})
    model.configure_corner_header(dict(corner_logo=str(ROOT/'resources/demo_team_logos/demo_wave_teal_v1.png'),
        corner_top_text='S2赛季',corner_bottom_text='Nov. 03',corner_match='1'))
    model.configure_sidebar(dict(sidebar_mode='individual',sidebar_scope='current'))
    model.settings.update(theme='mleague',overlay_font_family=family,broadcast_scene='clean',show_overlays=True,
        topmost=False,show_yaku=False,riichi_payment_mode='deferred',speed=1.,win_hold=2.,final_scores_hold=3.,
        show_melds=True)
    model.settings['visible']={key:False for key in model.settings.get('visible',{})}
    model.save();validate_model(model)
    return model


class Programme:
    def __init__(self,model,output):
        self.model=model;self.output=Path(output);self.now=0.;self.revision=0
        self.accounting_total=model.state['accountingTotal'];self.source_match_id=model.state['id']
        self.background=background();self.opening=prepare_opening(model)
        self.events=[];self.event_map={};self.chapters=[];self.samples={};self.phase_log=[];self.last_signature=None
        self.payment_records=[];self.hidden_hands=[];self.riichi_starts=[];self.last_active=None;self.manager_generation=0
        self.final_pt_complete=None;self.final_return_started=None;self.commit_events=[];self.bottom_events=[]
        self.frozen=None;self.end_state=None;self.last_live=None;self.cut_image=None
        model.changed.connect(self.changed);model.display_changed.connect(self.invalidate)
        self.create_manager();self.plan()

    def invalidate(self,*_):self.revision+=1

    def changed(self,*_):self.invalidate()

    def create_manager(self):
        self.manager=OverlayManager(self.model,riichi_audio=NullAudio());self.manager_generation+=1;self.last_active=None
        for seat in range(4):self.manager.windows[f'player{seat}'].setGeometry(63+seat*452,740,438,340)
        self.manager._sync_bottom_geometry(force=True);self.manager.on_display()
        for window in self.manager.windows.values():window.hide()

    def close_manager(self):
        manager=self.manager
        for signal,slot in ((self.model.changed,manager.on_change),(self.model.display_changed,manager.on_display),
                            (self.model.changed,manager.postmatch.on_model_changed),(self.model.changed,manager.opening.on_model_changed)):
            try:signal.disconnect(slot)
            except (RuntimeError,TypeError):pass
        manager.close()

    def event(self,time,label,callback):
        self.events.append((round(time*FPS),label,callback))

    def chapter(self,identity,time,title,description,features,capture,keyframe=None):
        entry=dict(id=identity,start=time,title=title,description=description,features=features,
                   keyframe=f'关键帧/{keyframe or identity}.png')
        self.chapters.append(entry);self.samples[round(capture*FPS)]=entry['keyframe']

    def open_live(self):
        self.manager.apply_scene('table');self.manager.set_extension_visible('corner_header',True)
        self.manager.replay_players('entry')

    def action(self,item):
        apply_action(self.model,item);validate_model(self.model)

    def settle(self,index,visible=True):
        if visible and self.manager.riichi_queue.busy:raise AssertionError('Riichi effect was cut by a settlement')
        hand=HANDS[index];before=copy.deepcopy(self.model.state);settle_hand(self.model,hand);validate_model(self.model)
        self.payment_records.append(dict(hand_id=hand['id'],visible=visible,time_s=self.now,
            before_scores=[p['score'] for p in before['players']],after_scores=[p['score'] for p in self.model.state['players']],
            pot_before=before['pot'],pot_after=self.model.state['pot'],payment_lines=payment_lines(before,self.model.state,'deferred')))

    def next_hand(self):
        if not self.model.next_hand():raise AssertionError('The hanchan unexpectedly ended before south four')
        validate_model(self.model)

    def begin_visible_hand(self,index):
        # The production manager already advances after the settlement exit.
        if self.model.state['phase']=='result':self.next_hand()
        assert_start(self.model,HANDS[index]);validate_model(self.model)

    def sidebar(self,mode,scope,page=0):
        self.model.configure_sidebar(dict(sidebar_mode=mode,sidebar_scope=scope,sidebar_page=page,sidebar_page_size=4,sidebar_auto_page=False))
        self.manager.set_extension_visible('qualification',True)

    def first_active_page(self):
        current={player['id'] for player in self.model.state['players']}
        rows=tournament.standings(self.model.state,'individual',live=False)
        return next(index//4 for index,row in enumerate(rows) if row['id'] in current)

    def publish(self,identity):
        term=get_term(identity)
        self.model.configure_commentary(dict(commentary_title=term['zh_title'],commentary_body=term['zh_body'],
            commentary_builtin=True,commentary_duration=2.))
        self.manager.set_extension_visible('commentary',True)

    def bottom(self,mode):
        if self.model.state['phase']!='playing':raise AssertionError('Manual team scores belong in the live match')
        before=copy.deepcopy(self.model.state);self.manager.set_bottom_mode(mode)
        if self.model.state!=before:raise AssertionError('Display toggle changed gameplay')
        self.bottom_events.append(dict(time_s=self.now,mode=mode,phase='playing',manual=True))

    def final_bottom(self,mode):
        if self.model.state['phase']!='finished':raise AssertionError('Final cards need a finished match')
        before=copy.deepcopy(self.model.state);self.manager.set_bottom_mode(mode)
        if self.model.state!=before:raise AssertionError('Final display toggle changed gameplay')
        self.bottom_events.append(dict(time_s=self.now,mode=mode,phase='finished',manual=True))

    def skip_middle(self):
        """Calculate six unshown legal hands without emitting broadcast events."""
        self.cut_image=self.paint_live();self.close_manager()
        was_blocked=self.model.blockSignals(True)
        try:
            if self.model.state['phase']=='result':self.next_hand()
            assert_start(self.model,HANDS[2])
            for index in range(2,8):
                hand=HANDS[index];assert_start(self.model,hand)
                for item in hand['actions']:self.action(item)
                self.settle(index,visible=False)
                self.hidden_hands.append(dict(id=hand['id'],round_index=hand['round'],honba=hand['honba'],
                    hand_number=index+1,visual_omitted=True,actions_executed=len(hand['actions']),
                    phase_after=self.model.state['phase'],scores=[p['score'] for p in self.model.state['players']]))
                self.next_hand()
        finally:self.model.blockSignals(was_blocked)
        assert_start(self.model,HANDS[8]);validate_model(self.model)
        self.model.settings['show_yaku']=False;self.model.settings['broadcast_scene']='clean'
        self.create_manager();self.invalidate()

    def commit(self):
        if self.final_pt_complete is None:raise AssertionError('Final pt has not completed')
        if self.model.state['phase']!='finished':raise AssertionError('Cannot archive before completion')
        before=(self.manager.stage,self.manager.bottom_mode());self.model.commit_match();validate_model(self.model)
        current=[row for row in self.model.state['broadcast']['matches'] if row.get('committed') and row['id']==self.source_match_id]
        if len(current)!=1:raise AssertionError('Current match must be committed exactly once')
        if (self.manager.stage,self.manager.bottom_mode())!=before or self.manager.settlement_window() is not None:
            raise AssertionError('Archiving restarted the final card presentation')
        self.commit_events.append(dict(time_s=self.now,current_match_count=1,stage=before[0],bottom_mode=before[1]))

    def freeze_postmatch(self):
        if self.final_pt_complete is None or self.now-self.final_pt_complete<3:
            raise AssertionError('Completed pt requires a stable hold')
        self.last_live=self.paint_live();self.end_state=copy.deepcopy(self.model.state)
        self.model.export_data(self.output/'ML_演示数据.json')
        self.frozen=SimpleNamespace(state=copy.deepcopy(self.model.state),settings=copy.deepcopy(self.model.settings),
            path=self.model.path,event=copy.deepcopy(self.model.event))
        self.postmatch_data=prepare_postmatch(self.frozen);prepare_postmatch_model(self.frozen)
        if self.postmatch_data['hands']!=9 or self.postmatch_data['draws']!=1:raise AssertionError('Post-match data does not cover all nine legal hands')
        write_json(self.output/'final-state.json',self.end_state);write_json(self.output/'postmatch-data.json',self.postmatch_data)

    def plan(self):
        self.chapter('opening-title',0.,'赛季标题卡','显示模拟赛事名称。',['赛事标题'],2.)
        self.chapter('opening-rosters',4.,'四队全部选手名单','当前四队共16名演示选手，突出本场出战者。',['完整队伍名册','出战高亮'],8.)
        self.chapter('opening-starters',11.,'本场四席出战','肖像、队名和队色来自当前四席。',['原创演示肖像','队色跟随'],14.)
        self.chapter('live-entry',18.,'四席入场与局况','正式对局阶段停止背景音乐。',['选手入场','宝牌'],20.)
        self.chapter('current-people',21.,'本桌四人积分','仅本桌四人，短底板、无红色。',['精简个人侧栏'],23.)
        self.chapter('riichi',26.,'立直声明与顺序播放','音效跟随实际声明开始，声明结束才改变选手状态。',['立直队色细线','FIFO'],27.)
        self.chapter('remaining',29.,'一次听牌余枚减少','余枚从5减至4，播放一次0.38秒过渡。',['一次余枚递减'],29.8,'remaining-transition')
        self.chapter('current-teams',25.,'本桌四队积分','保持队伍全局排名，四队短底板、无红色。',['精简队伍侧栏'],27.5)
        self.chapter('paged-standings',35.,'完整名册分页','每页四行仍保留原高度与本桌红色标识。',['真实分页'],36.5)
        self.chapter('glossary',39.5,'术语解释','中心白线展开，文字、底板分层进出。',['术语白线进退'],41.)
        self.chapter('manual-team-points',41.,'比赛中手动队伍积分','比赛中显示队伍积分后返回四席。',['手动队伍积分'],43.)
        self.chapter('first-settlement',46.,'收支与胡牌详情','两行收支垂直等尺寸汇入，接续胡牌详情。',['垂直等尺寸收支','胡牌详情'],48.)
        self.chapter('draw-hand',56.,'流局、本场与场供','代表性流局完整显示付款；随后省略中段对局画面。',['流局','本场供托'],74.2)
        self.chapter('south-four',79.,'南4局与完场','中段已通过计分引擎逐局计算，本段展示最后一局。',['终局荣和'],86.)
        self.chapter('final-points',FINAL_SETTLE,'最终点数转pt','身份保持原位，仅数字层切换，pt完整后稳定停留。',['点数转pt','稳定pt'],105.)
        self.chapter('postmatch-chart',POSTMATCH_AT,'赛后折线图','同一完整半庄的10个折线节点，完整停留15秒。',['曲线渐变','完整15秒'],POSTMATCH_AT+8.)
        self.chapter('postmatch-statistics',POSTMATCH_AT+20.967,'简易技术统计','与同一场点数及统计一致，完整停留15秒。',['简易技术统计','完整15秒'],POSTMATCH_AT+26.)
        self.event(18.,'四席入场',self.open_live)
        self.event(21.,'本桌四人积分',lambda:self.sidebar('individual','current'))
        self.event(24.6,'收起个人侧栏',lambda:self.manager.set_extension_visible('qualification',False))
        for item in HANDS[0]['actions']:
            action=copy.deepcopy(item)
            self.event(22.+item['at'],item['method'],lambda action=action:self.action(action))
        self.event(25.,'本桌四队积分',lambda:self.sidebar('team','current'))
        self.event(28.6,'收起队伍侧栏',lambda:self.manager.set_extension_visible('qualification',False))
        self.event(29.6,'余枚一次5减至4',lambda:self.model.set_remaining(1,4))
        for at,name in ((29.5,'remaining-before'),(30.2,'remaining-after')):
            self.samples[round(at*FPS)]=f'关键帧/{name}.png'
        self.event(35.,'真实四行分页',lambda:self.sidebar('individual','all',self.first_active_page()))
        self.event(39.,'收起分页侧栏',lambda:self.manager.set_extension_visible('qualification',False))
        self.event(39.5,'术语现物',lambda:self.publish('genbutsu'))
        self.event(41.,'手动显示队伍积分',lambda:self.bottom('team'))
        self.event(44.5,'手动返回选手信息',lambda:self.bottom('players'))
        self.event(45.,'收起术语',lambda:self.manager.set_extension_visible('commentary',False))
        self.event(46.,'东1局结算',lambda:self.settle(0))
        self.samples[round(51.*FPS)]='关键帧/first-win-details.png'
        self.event(56.,'开始展示东2局',lambda:self.begin_visible_hand(1))
        for item in HANDS[1]['actions']:self.event(56.+item['at'],item['method'],lambda item=item:self.action(item))
        self.event(70.,'东2局流局',lambda:self.settle(1))
        self.event(SKIP_AT,'离屏计算中段六局',self.skip_middle)
        self.event(FINAL_AT,'南4局入场',self.open_live)
        for item in HANDS[8]['actions']:self.event(FINAL_AT+item['at'],item['method'],lambda item=item:self.action(item))
        self.event(FINAL_SETTLE,'南4局结算',lambda:self.settle(8))
        self.event(103.2,'手动显示最终点数与名次',lambda:self.final_bottom('final_scores'))
        self.event(103.95,'手动切换终局pt',lambda:self.final_bottom('final_pt'))
        self.event(107.,'归档当前半庄一次',self.commit)
        self.event(POSTMATCH_AT,'准备赛后展示',self.freeze_postmatch)
        self.events.sort(key=lambda item:item[0])
        for frame,label,callback in self.events:self.event_map.setdefault(frame,[]).append((label,callback))
        self.chapters.sort(key=lambda chapter:chapter['start'])

    def paint_live(self):
        image=self.background.copy();p=QPainter(image)
        for hint in (QPainter.RenderHint.Antialiasing,QPainter.RenderHint.TextAntialiasing,QPainter.RenderHint.SmoothPixmapTransform):p.setRenderHint(hint)
        def draw(key,kind,index,x,y,width=None,height=None):
            if not self.manager.content_visible(key):return
            base=self.manager.base_size(kind);width=base[0] if width is None else width;height=base[1]*width/base[0] if height is None else height
            p.save();p.translate(x,y);p.scale(width/base[0],height/base[1]);self.manager.paint_content(p,kind,index);p.restore()
        try:
            draw('round','round',0,38,20,624)
            draw('commentary','commentary',0,684,17,800)
            draw('corner_header','corner_header',0,1524,4,360)
            draw('qualification','qualification',0,32,162)
            for seat in range(4):draw(f'player{seat}','player',seat,63+452*seat,740)
            w=self.manager.windows['standings'];draw('standings','standings',0,w.x(),w.y(),w.width(),w.height())
            draw('yaku','yaku',0,0,840,1920)
            draw('riichi_effect','riichi_effect',0,0,0)
        finally:p.end()
        return image

    def paint(self):
        if self.now<OPENING_DURATION:
            image=self.background.copy();p=QPainter(image)
            try:draw_opening(p,self.opening,self.now)
            finally:p.end()
            return image
        if self.now>=POSTMATCH_AT:
            image=self.last_live.copy();p=QPainter(image)
            try:draw_postmatch(p,self.frozen,self.now-POSTMATCH_AT)
            finally:p.end()
            return image
        if SKIP_AT<=self.now<FINAL_AT:
            image=self.cut_image.copy() if self.now<SKIP_AT+.45 else self.background.copy()
            opacity=min(1.,max(0.,(self.now-SKIP_AT)/.45)) if self.now<SKIP_AT+.45 else max(0.,1-(self.now-(FINAL_AT-.45))/.45)
            p=QPainter(image);p.fillRect(image.rect(),QColor(0,0,0,round(255*opacity)));p.end();return image
        return self.paint_live()

    def record_phase(self):
        active=self.manager.riichi_queue.active
        signature=(self.manager.stage,self.model.state['phase'],self.model.state['roundIndex'],self.model.state['honba'],
            self.model.settings.get('final_points_mode'),active.sequence_id if active else None,self.manager_generation,
            self.manager.bottom_mode(),self.manager._final_card_change is not None)
        if signature!=self.last_signature:
            self.phase_log.append(dict(time_s=self.now,stage=signature[0],phase=signature[1],round_index=signature[2],honba=signature[3],
                final_points_mode=signature[4],active_queue_sequence=signature[5],manager_generation=signature[6],
                bottom_mode=signature[7],numeric_transition_active=signature[8]))
            self.last_signature=signature
        active_key=(self.manager_generation,active.sequence_id) if active else None
        if active_key is not None and active_key!=self.last_active:
            self.riichi_starts.append(dict(sequence_id=len(self.riichi_starts)+1,queue_sequence_id=active.sequence_id,
                manager_generation=self.manager_generation,time_s=self.now,player_id=active.player_id,
                player_name=active.player_name,team_id=active.team_id,team_color=active.team_color,duration_seconds=2.5))
        self.last_active=active_key
        if signature[1]=='finished':
            if signature[0]=='players_in' and self.final_return_started is None:self.final_return_started=self.now
            if signature[0]=='table' and signature[4]=='pt' and not signature[8] and self.final_pt_complete is None:
                self.final_pt_complete=self.now;self.samples[round(self.now*FPS)]='关键帧/final-points-complete.png'


def run(output,dry_run=False):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    (output/'关键帧').mkdir(exist_ok=True);(output/'analysis').mkdir(exist_ok=True)
    app=QApplication.instance() or QApplication([]);family=initialize_fonts(app);protected=formal_inventory()
    source=ROOT/'resources/demo/team_complete.json';source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    process=None;frame_cache=None;raw_cache=None;rendered=reused=0;trace=[]
    with tempfile.TemporaryDirectory(prefix='tmp-ml-programme-',dir=ROOT/'tests') as temporary:
        model=programme_model(temporary,family)
        with patch('overlays.time.monotonic',return_value=1000.) as clock:
            demo=Programme(model,output)
            if not dry_run:
                encoder=shutil.which('ffmpeg')
                if not encoder:raise RuntimeError('FFmpeg is required')
                process=subprocess.Popen([encoder,'-nostdin','-v','error','-y','-f','rawvideo','-pix_fmt','bgra','-s','1920x1080','-r',str(FPS),
                    '-i','-','-an','-c:v','libx264','-crf','18','-preset','veryfast','-pix_fmt','yuv420p','-movflags','+faststart',str(output/SILENT_NAME)],
                    stdin=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            previous_post_phase=None
            try:
                for frame in range(round(DURATION*FPS)):
                    demo.now=frame/FPS;clock.return_value=1000.+demo.now
                    moving=demo.manager.timer.isActive();revision=demo.revision
                    for label,callback in demo.event_map.get(frame,[]):callback();trace.append(dict(time_s=demo.now,label=label));demo.invalidate()
                    demo.manager.tick();demo.record_phase()
                    if sum(p['score'] for p in model.state['players'])+model.state['pot']!=demo.accounting_total:raise AssertionError('Rendered ledger drift')
                    if demo.end_state is not None and model.state!=demo.end_state:raise AssertionError('Rendering mutated the frozen result')
                    sample=demo.samples.get(frame)
                    if demo.now<OPENING_DURATION or SKIP_AT<=demo.now<FINAL_AT:moving=True
                    elif demo.now>=POSTMATCH_AT:
                        phase=postmatch_timeline(demo.now-POSTMATCH_AT)['phase']
                        moving=phase!=previous_post_phase or phase not in ('stats_hold','complete') or demo.now-POSTMATCH_AT>postmatch_duration()
                        previous_post_phase=phase
                    else:moving=moving or demo.manager.timer.isActive() or revision!=demo.revision
                    if sample or (not dry_run and (frame_cache is None or moving)):
                        frame_cache=demo.paint();raw_cache=None;rendered+=1
                    else:reused+=1
                    if sample:frame_cache.save(str(output/sample))
                    if process:
                        if raw_cache is None:raw_cache=bytes(frame_cache.constBits())
                        process.stdin.write(raw_cache)
                    if frame%(FPS*10)==0:print(f'{"Simulated" if dry_run else "Rendered"} {demo.now:.0f}/{DURATION:.3f}s',flush=True)
                if process:
                    process.stdin.close();error=process.stderr.read();code=process.wait()
                    if code:raise RuntimeError(error.decode('utf8',errors='replace'))
                validate_model(model)
                if len(model.state['broadcast']['hands'])!=9:raise AssertionError('All nine hands must be calculated')
                report=dict(status='PASS',video=VIDEO_NAME,silent_video=SILENT_NAME,fps=FPS,resolution=SIZE,duration_seconds=DURATION,
                    video_rendered=not dry_run,source_data=str(source.relative_to(ROOT)),source_sha256=source_hash,
                    real_ml_identity_data=True,simulated_match_actions_and_results=True,playback_speed=1.,
                    opening_duration_seconds=OPENING_DURATION,live_interval=[OPENING_DURATION,POSTMATCH_AT],
                    music_intervals=[[0.,OPENING_DURATION],[POSTMATCH_AT,DURATION]],no_music_in_live_match=True,
                    riichi_effect_starts=demo.riichi_starts,visible_hands=[HANDS[index]['id'] for index in VISIBLE_HANDS],
                    hidden_hands=demo.hidden_hands,skipped_visual_interval=[SKIP_AT,FINAL_AT],hidden_actions_emit_no_broadcast_signals=True,
                    postmatch_start_seconds=POSTMATCH_AT,chart_hold=15.,stats_hold=15.,
                    complete_chart_interval=[POSTMATCH_AT+5.967,POSTMATCH_AT+20.967],
                    complete_statistics_interval=[POSTMATCH_AT+23.967,POSTMATCH_AT+38.967],
                    match_summary=dict(completed_hands=9,draws=1,roster_count=len(model.state['broadcast']['roster']),
                        accounting_total=demo.accounting_total,committed_current_match_count=1,
                        historical_committed_matches=len([row for row in model.state['broadcast']['matches'] if row.get('committed')])-1,
                        final_scores=[p['score'] for p in model.state['players']],final_results=tournament.match_results(model.state),
                        statistics=tournament.statistics(model.state,'match')),
                    opening_teams=[dict(id=team.team_id,name=team.name,color=team.color,members=len(team.members)) for team in demo.opening.teams],
                    active_players=[dict(id=p.player_id,name=p.name,team=p.team_name,color=p.team_color) for p in demo.opening.players],
                    hand_payments=demo.payment_records,phase_log=demo.phase_log,commands=trace,manual_bottom_mode_events=demo.bottom_events,
                    archive_events=demo.commit_events,final_cards_return_time=demo.final_return_started,final_pt_complete_time=demo.final_pt_complete,
                    stable_final_pt_seconds=POSTMATCH_AT-demo.final_pt_complete,visual_frames_rendered=rendered,visual_frames_reused=reused,
                    formal_data_changed=formal_inventory()!=protected,temporary_project_removed=False)
                if report['formal_data_changed']:raise AssertionError('Formal files changed')
                chapters=dict(title='M.League 名册演示 · 开场至结束（中段节选）',video=VIDEO_NAME,fps=FPS,duration_seconds=DURATION,
                    postmatch_start_seconds=POSTMATCH_AT,chart_hold=15.,stats_hold=15.,chapters=demo.chapters,
                    music_intervals=report['music_intervals'],riichi_effect_starts=demo.riichi_starts)
                write_json(output/'chapters.json',chapters);write_json(output/'verification.json',report)
            finally:
                if process and process.poll() is None:process.kill();process.wait()
                demo.close_manager();app.processEvents()
    if formal_inventory()!=protected or hashlib.sha256(source.read_bytes()).hexdigest()!=source_hash:raise AssertionError('Protected source data changed')
    report['temporary_project_removed']=not Path(temporary).exists();write_json(output/'verification.json',report)
    print(json.dumps(dict(status='PASS',duration_seconds=DURATION,postmatch_start_seconds=POSTMATCH_AT,
        completed_hands=9,visible_hands=len(VISIBLE_HANDS),riichi_effect_starts=len(report['riichi_effect_starts']),dry_run=dry_run),ensure_ascii=True),flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'previews/latest/programme')
    parser.add_argument('--dry-run',action='store_true');args=parser.parse_args()
    run(args.output,args.dry_run)
