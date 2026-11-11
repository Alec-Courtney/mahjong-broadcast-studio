"""Native application model and transaction boundary for table commands."""
from __future__ import annotations
import copy, json
from pathlib import Path
from PySide6.QtCore import QObject, Signal
from PySide6.QtQml import QJSEngine
import tournament
from app_paths import ROOT, APP_DIR
from mahjong_values import (WINDS, COLORS, TEAM_COLORS, TILES, YAKU, YAKU_ALIASES,
    DEFAULT_NAMES, LEGACY_DEFAULT_NAMES, simplified, normalize_waits, tile_sort_key,
    display_score, payment_lines, tile_name, parse_tiles, compact, yaku_total)
from app_settings import (default_settings, validate_console_theme,
                          validate_player_style, normalize_player_style)
from session_repository import SessionRepository
from match_service import MatchService
from event_record_service import EventRecordService
from state_snapshots import copy_state, seal_archives, mutable_copy

_UNCHANGED = object()

class Engine:
 def __init__(self):
  self.js=QJSEngine()
  r=self.js.evaluate((ROOT/'resources/engine.js').read_text(encoding='utf-8'))
  if r.isError():raise RuntimeError(r.toString())
  self.call=self.js.evaluate('(function(name,a,b){var E=MahjongEngine;var r=b===undefined?E[name](JSON.parse(a)):E[name](JSON.parse(a),JSON.parse(b));return JSON.stringify(r);})')
 def invoke(self,name,a,b=None):
  from PySide6.QtQml import QJSValue
  broadcast=a.get('broadcast',_UNCHANGED) if isinstance(a,dict) else _UNCHANGED
  # The payment engine owns only the live table. Sending complete historical
  # archives to JavaScript made every preview/validation copy the whole event.
  if broadcast is not _UNCHANGED and name in ('applyEvent','previewSettlement','reviseSettlement','validateState','computeStandings'):
   a={key:value for key,value in a.items() if key!='broadcast'}
  args=[QJSValue(name),QJSValue(json.dumps(a,ensure_ascii=False))]
  if b is not None:args.append(QJSValue(json.dumps(b,ensure_ascii=False)))
  r=self.call.call(args)
  if r.isError():raise ValueError(r.toString().split(': ',1)[-1])
  result=json.loads(r.toString())
  if name=='applyEvent' and broadcast is not _UNCHANGED:result['broadcast']=copy.deepcopy(broadcast)
  return result
 def apply(self,state,event):return self.invoke('applyEvent',state,event)
 def preview(self,state,event):return self.invoke('previewSettlement',state,event)
 def validate(self,state):
  result=self.invoke('validateState',state)
  if not result['valid']:raise ValueError(result['errors'][0])

class Model(QObject):
 changed=Signal(object,object,str)
 display_changed=Signal()
 def __init__(self,data_dir=None,asset_root=None,preferences_root=None):
  super().__init__();self.engine=Engine();self.repository=SessionRepository(self.engine,self.migrate);self.undo=[];self.redo=[];self.last_transition=None
  self.changed.connect(self._seal_transition)
  self.path=Path(data_dir or APP_DIR/'data');self.path.mkdir(parents=True,exist_ok=True)
  from application_preferences import ApplicationPreferences,infer_preferences_root
  self.preferences=ApplicationPreferences(preferences_root or infer_preferences_root(self.path))
  self._explicit_preferences_root=preferences_root is not None
  from media_store import infer_asset_root,register_asset_root
  self.asset_root=Path(asset_root or (APP_DIR if data_dir is None else infer_asset_root(self.path))).resolve()
  register_asset_root(self.path,self.asset_root)
  self.settings=default_settings();self.event=None;self.event_store=None
  self.matches=MatchService(self);self.records=EventRecordService(self)
  self.state=self.fresh();self.last_info=None
  session=self.path/'session.json'
  self.repository.observe(session)
  try:
   encoded=session.read_bytes()
   from session_repository import reject_constant,MAX_LOCAL_SESSION_BYTES
   if len(encoded)>MAX_LOCAL_SESSION_BYTES:raise ValueError('本地工作区数据超过保存上限')
   saved=json.loads(encoded.decode('utf-8-sig'),parse_constant=reject_constant)
   if not isinstance(saved,dict) or isinstance(saved.get('version',0),bool) or saved.get('version',0) not in range(7):
    raise ValueError('不支持的工作区数据版本')
   restored=self.migrate(saved['state'],saved.get('version',0))
   self.engine.validate(restored)
   from media_store import manage_photos
   try:manage_photos(restored,self.path)
   except (OSError,ValueError):pass
   saved_settings=copy.deepcopy(saved.get('settings',{}));normalize_player_style(saved_settings)
   from session_repository import validate_settings
   validate_settings(saved_settings)
   if saved.get('event') is not None:
    from event_metadata import validate_event
    validate_event(saved['event'],restored)
   self.state=restored;self.settings.update(saved_settings);self.last_info=saved.get('last_info');self.event=saved.get('event')
   if self.last_info:
    self.last_info['yaku']=[simplified(n) for n in self.last_info.get('yaku',[])]
    self.last_info['limit']=simplified(self.last_info.get('limit',''))
   self.repository.observe(session,encoded)
  except FileNotFoundError:pass
  except (OSError,ValueError,KeyError,TypeError,AttributeError,IndexError,UnicodeError) as error:
   raise ValueError('无法读取已保存的工作区，原文件已保留，请从备份恢复：'+str(session)) from error
  normalize_player_style(self.settings)
  if self.settings.get('console_theme') not in ('dark','light'):self.settings['console_theme']='dark'
  if self.settings.get('final_points_mode') not in ('scores','pt'):self.settings['final_points_mode']='scores'
  if self.settings.get('riichi_payment_mode') not in ('immediate','deferred'):self.settings['riichi_payment_mode']='immediate'
  from card_style import normalize_card_style,default_card_style
  for key,theme in (('player_card_style','mleague'),('saikouisen_card_style','saikouisen')):
   try:self.settings[key]=normalize_card_style(self.settings.get(key),theme)
   except ValueError:self.settings[key]=default_card_style(theme)
  from typography import validate
  try:validate(self.settings)
  except ValueError:
   for key in ('console_font_family','overlay_font_family','console_font_size','settings_font_size','overlay_font_scale'):self.settings.pop(key,None)
  if not isinstance(self.settings.get('player_group_linked',False),bool):self.settings['player_group_linked']=False
  self.preferences.apply(self.settings)
  seal_archives(self.state)
 def _seal_transition(self,before,after,kind):
  seal_archives(after)
 def migrate(self,state,version=0):
  """Keep persisted indicator codes intact; only the presentation is converted."""
  state=copy.deepcopy(state);state['manualDisplay']=True
  for i,p in enumerate(state['players']):
   if version<5 and p['name']==LEGACY_DEFAULT_NAMES[i]:p['name']=DEFAULT_NAMES[i]
   p.setdefault('photo','');p.setdefault('color',COLORS[i]);p.setdefault('team_color',TEAM_COLORS[i]);p.setdefault('team','')
   p['waits']=normalize_waits(p.get('waits',[]))
   # v0.4 accepted waits without ever switching the display to tenpai.
   p['tenpai']=bool(p.get('tenpai') or (version<5 and (p['riichi'] or p['waits'])))
   remaining=p.get('remaining')
   p['remaining']=remaining if isinstance(remaining,int) and not isinstance(remaining,bool) and 0<=remaining<=136 else None
   no_yaku=p.get('waits_no_yaku',[])
   if not isinstance(no_yaku,list) or any(t not in TILES for t in no_yaku):raise ValueError('无役待牌格式无效')
   if 'waits_no_yaku' in p:p['waits_no_yaku']=[t for t in normalize_waits(no_yaku) if t in p['waits']]
   if 'furiten' in p and not isinstance(p['furiten'],bool):raise ValueError('振听设置无效')
  tournament.validate_broadcast(state)
  return state
 @property
 def dora_tiles(self):
  return [self.engine.invoke('tileFromIndicator',code) for code in self.state['doraIndicators']]
 def fresh(self, *, start_points=None):
  rules={'kiriageMangan':True,'kazoeYakuman':False}
  if start_points is not None:rules['startPoints']=start_points
  s=self.engine.invoke('createState',{'names':DEFAULT_NAMES,'rules':rules})
  s['title']='A1联赛 第1节';s['manualDisplay']=True
  for i,p in enumerate(s['players']):p.update(tenpai=False,waits=[],remaining=None,photo='',color=COLORS[i],team_color=TEAM_COLORS[i],team='')
  tournament.ensure_broadcast(s)
  return s
 def save(self):
  from application_preferences import capture_preferences
  previous=capture_preferences(self.settings)
  self._save_snapshot(self.state,self.settings,self.last_info,self.event)
  seal_archives(self.state)
  if capture_preferences(self.settings)!=previous:self.display_changed.emit()
 def _save_snapshot(self,state,settings,info,event,application_keys=()):
  with self.preferences.transaction():
   return self._save_snapshot_locked(state,settings,info,event,application_keys)
 def _save_snapshot_locked(self,state,settings,info,event,application_keys=()):
  """Keep the event snapshot consistent if a shared-preference write fails."""
  values=self.preferences.merge(settings,application_keys)
  candidate=copy.deepcopy(settings);candidate.update(values)
  if values==self.preferences.read():
   self.repository.save(self.path,state,candidate,info,event)
   self.preferences.baseline=copy.deepcopy(values);settings.update(values);return
  session=self.path/'session.json'
  previous_baseline=copy.deepcopy(self.preferences.baseline)
  from file_transaction import atomic_writes
  try:
   with atomic_writes(self.preferences.root):
    self.repository.save(self.path,state,candidate,info,event)
    self.preferences.save(candidate,prepared=values)
  except Exception:
   self.preferences.baseline=previous_baseline
   self.repository.observe(session)
   raise
  self.repository.observe(session)
  settings.update(values)
 def save_preferences(self,settings,application_keys=None):
  """Persist display preferences atomically without consuming gameplay undo."""
  from session_repository import validate_settings
  candidate=copy.deepcopy(settings);validate_settings(candidate)
  if application_keys is None:
   from application_preferences import capture_preferences
   previous=capture_preferences(self.settings);current=capture_preferences(candidate)
   application_keys={key for key in current if current[key]!=previous[key]}
  state=copy_state(self.state)
  self._save_snapshot(state,candidate,self.last_info,self.event,application_keys)
  self.state=state;self.settings=candidate;self.display_changed.emit()
 def save_win_info(self,info,show=True):
  """Save a replacement win caption and its visibility as one display update."""
  if info is not None and (not isinstance(info,dict) or not isinstance(info.get('yaku',[]),list)
      or any(not isinstance(value,str) for value in info.get('yaku',[]))):raise ValueError('和牌展示数据格式无效')
  if not isinstance(show,bool):raise ValueError('和牌展示开关格式无效')
  candidate=copy.deepcopy(self.settings);candidate['show_yaku']=show
  details=copy.deepcopy(info)
  self._save_snapshot(self.state,candidate,details,self.event)
  self.settings=candidate;self.last_info=details;self.display_changed.emit()
 def _publish(self,before,after,settings,info,undo,redo,transition,kind,*,event=_UNCHANGED,display=False):
  """Persist a complete candidate before publishing any observer notification."""
  previous=(self.state,self.settings,self.last_info,self.undo,self.redo,self.last_transition,self.event)
  self.state,self.settings,self.last_info=after,settings,info
  self.undo,self.redo,self.last_transition=undo,redo,transition
  if event is not _UNCHANGED:self.event=event
  try:self.save()
  except Exception:
   (self.state,self.settings,self.last_info,self.undo,self.redo,self.last_transition,self.event)=previous
   raise
  self.changed.emit(before,self.state,kind)
  if display:self.display_changed.emit()
 def update(self,fn,kind='update',*,info=_UNCHANGED,settings_patch=None,reset_transition=False,display=False):
  before=copy_state(self.state);after=copy_state(before);fn(after)
  if self.event_store and before['broadcast']['hands'] and before['phase']!='finished' and kind not in ('new_match','start_match'):
   if any(before['rules'].get(key)!=after['rules'].get(key) for key in ('returnPoints','uma','okaEnabled','tieBreak','kiriageMangan','kazoeYakuman')):
    raise ValueError('当前半庄进行中，请完成并确认后再修改计分规则')
  # Terminal pot awards freeze tied ranks. Only an explicit later score edit
  # invalidates that snapshot; a replay, rename or display edit must preserve it.
  if before.get('phase')==after.get('phase')=='finished' and kind not in ('ron','tsumo','draw','hand_correction') and any(p['score']!=q['score'] for p,q in zip(before['players'],after['players'])):
   after['finalRanking']=None
  tournament.on_update(before,after,kind);self.engine.validate(after)
  from media_store import manage_photos
  manage_photos(after,self.path)
  from transition_settings import settings_for_transition
  settings=settings_for_transition(self.settings,before,after,kind)
  if settings_patch:settings.update(copy.deepcopy(settings_patch))
  undo=(self.undo+[(before,copy.deepcopy(self.last_info))])[-60:]
  transition=self.last_transition
  if kind in ('ron','tsumo','draw','riichi'):transition=(before,copy.deepcopy(after),kind)
  elif kind in ('clear','next_hand') or before['pot']!=after['pot'] or any(a['score']!=b['score'] for a,b in zip(before['players'],after['players'])):transition=None
  if reset_transition:transition=None
  candidate_info=copy.deepcopy(self.last_info if info is _UNCHANGED else info)
  self._publish(before,after,settings,candidate_info,undo,[],transition,kind,display=display)
 def setting(self,key,value):
  candidate=copy.deepcopy(self.settings)
  if key=='console_theme':validate_console_theme(value)
  if key=='theme':validate_player_style(value);candidate['player_style_version']=1
  if key in ('player_card_style','saikouisen_card_style'):
   from card_style import normalize_card_style
   value=normalize_card_style(value,'saikouisen' if key=='saikouisen_card_style' else 'mleague')
  candidate[key]=value;self.save_preferences(candidate,application_keys={key})
 def history(self,direction):
  source,target=(self.undo,self.redo) if direction=='undo' else (self.redo,self.undo)
  if not source:return
  before=copy.deepcopy(self.state)
  source=source[:-1];target=target+[(before,copy.deepcopy(self.last_info))]
  original=self.undo if direction=='undo' else self.redo
  state,info=copy.deepcopy(original[-1])
  if self.event:
   config=state['broadcast']['config'];config['event_mode']='team' if self.event['kind']=='team_league' else 'individual'
   if self.event['kind']!='individual_knockout':
    config['qualification_mode']=config['event_mode']
    if self.event.get('qualification_count',0)>0:config['qualification_slots']=self.event['qualification_count']
   if self.event.get('team_baselines'):config['team_baselines']=copy.deepcopy(self.event['team_baselines'])
   else:config.pop('team_baselines',None)
  from transition_settings import settings_for_transition
  settings=settings_for_transition(self.settings,before,state,'restore')
  if state.get('phase')!='finished':settings['final_points_mode']='scores'
  self.engine.validate(state);tournament.validate_broadcast(state)
  undo,redo=(source,target) if direction=='undo' else (target,source)
  self._publish(before,state,settings,info,undo,redo,None,'restore')
 def playing(self):
  """Operator inputs remain editable before and after payment confirmation."""
  return True
 def riichi(self,i):
  self.playing()
  def change(s):
   p=s['players'][i]
   if s['phase']!='playing':p['riichi']=not p['riichi']
   elif p['riichi']:
    p['riichi']=False
    if s['pot']>=1000:p['score']+=1000;s['pot']-=1000
   else:s.update(self.engine.apply(s,{'type':'riichi','player':i}));s['players'][i]['tenpai']=True
  self.update(change,'riichi')
 def tenpai(self,i):
  self.set_tenpai(i,not self.state['players'][i]['tenpai'])
 def set_tenpai(self,i,value):
  self.playing()
  self.update(lambda s:s['players'][i].update(tenpai=bool(value)))
 def set_waits(self,i,waits):
  self.playing();waits=normalize_waits(waits)
  def change(s):
   p=s['players'][i];p['waits']=waits
   if waits:p['tenpai']=True
  self.update(change,'waits')
 def set_remaining(self,i,value):
  if value is not None and (not isinstance(value,int) or isinstance(value,bool) or not 0<=value<=136):
   raise ValueError('山存张数必须是0到136之间的整数')
  self.update(lambda s:s['players'][i].update(remaining=value),'waits')
 def submit_waits(self,i,waits,remaining,no_yaku=None,furiten=None):
  waits=normalize_waits(waits)
  if no_yaku is not None:
   no_yaku=normalize_waits(no_yaku)
   if not set(no_yaku).issubset(waits):raise ValueError('无役待牌必须在待牌列表中')
  if furiten is not None and not isinstance(furiten,bool):raise ValueError('振听设置无效')
  if remaining is not None and (not isinstance(remaining,int) or isinstance(remaining,bool) or not 0<=remaining<=136):raise ValueError('山存量必须是0到136之间的整数')
  def change(s):
   p=s['players'][i];p.update(waits=waits,remaining=remaining)
   if no_yaku is not None:p['waits_no_yaku']=no_yaku
   if furiten is not None:p['furiten']=furiten
   if waits:p['tenpai']=True
  self.update(change,'waits')
 def set_score(self,i,value):
  if not isinstance(value,int) or isinstance(value,bool) or value%100:raise ValueError('点数须为百点整数')
  def change(s):
   credit=display_score(s,i,self.settings.get('riichi_payment_mode'))-s['players'][i]['score']
   s['players'][i]['score']=value-credit
   total=sum(p['score'] for p in s['players'])+s['pot']
   s['accountingAdjustment']=s.get('accountingAdjustment',0)+total-s['accountingTotal'];s['accountingTotal']=total
  self.update(change,'score')
 def field(self,key,value):
  self.playing()
  def change(s):
   s[key]=value
   if key=='pot':s['accountingTotal']=sum(p['score'] for p in s['players'])+s['pot']
  self.update(change)
 def clear(self):
  def change(s):
   s.update(phase='playing',lastResult=None,finalRanking=None,doraIndicators=[])
   for p in s['players']:
    p.update(riichi=False,tenpai=False,waits=[],remaining=None,melds=[])
    p.pop('waits_no_yaku',None);p.pop('furiten',None)
  self.update(change,'clear',info=None)
 def settle(self,event,info):
  def change(s):
   from hand_history import settlement_context
   context=settlement_context(s)
   s.update(self.engine.apply(s,event))
   tournament.record_hand(s,context=context,event=event,win_info=info)
  self.update(change,event['type'],info=info,display=True)
 def preview_hand_correction(self,hand_id,patch,expected_state=None):
  if expected_state is not None and self.state is not expected_state:
   raise ValueError('对局记录已变化，请关闭详情后重新核对')
  from hand_history import revise
  candidate=copy_state(self.state)
  info=revise(candidate,self.engine,hand_id,patch)
  tournament.on_update(self.state,candidate,'hand_correction')
  self.engine.validate(candidate);tournament.validate_broadcast(candidate)
  return candidate,info
 def correct_hand(self,hand_id,patch,expected_state=None):
  candidate,info=self.preview_hand_correction(hand_id,patch,expected_state)
  self.update(lambda s:(s.clear(),s.update(candidate)),'hand_correction',info=info,
              reset_transition=True,display=True)
 def preview(self,event):
  """Preview a new payment, or the replacement of this hand's last payment."""
  result=self.engine.preview(self.state,event)
  if self.settings.get('riichi_payment_mode')=='deferred':
   after=copy.deepcopy(self.state);after.update(phase='finished' if result['next']['finished'] else 'result',lastResult=result,pot=result['potAfter'])
   for p,score in zip(after['players'],result['afterScores']):p['score']=score
   result['paymentLines']=payment_lines(self.state,after,'deferred');result['displayDeltas']=[sum(line) for line in result['paymentLines']]
  return result
 def next_hand(self):
  if self.state['phase']!='result' or not self.state.get('lastResult') or self.state['lastResult']['next']['finished']:return False
  def advance(s):
   s.update(self.engine.apply(s,{'type':'next_hand'}))
   for p in s['players']:p.pop('waits_no_yaku',None);p.pop('furiten',None)
  self.update(advance,'next_hand',info=None,reset_transition=True,display=True);return True
 def draw(self,declared_tenpai=None):
  """Settle with the table's declaration, defaulting to recorded tenpai."""
  self.playing()
  def change(s):
   tenpai=declared_tenpai if declared_tenpai is not None else [i for i,p in enumerate(s['players']) if p['tenpai']]
   from hand_history import settlement_context
   context=settlement_context(s);event={'type':'draw','tenpai':tenpai}
   s.update(self.engine.apply(s,event))
   tournament.record_hand(s,context=context,event=event)
  self.update(change,'draw',info=None)
 def meld(self,player,kind,tiles,source,called=0):
  self.playing();self.update(lambda s:s.update(self.engine.apply(s,{'type':'meld','player':player,'meld':{'kind':kind,'tiles':tiles,'from':source,'calledIndex':called}})))
 def remove_meld(self,player,index):
  self.playing()
  def change(s):
   s['players'][player]['melds'].pop(index)
  self.update(change)
 def dora(self,tile=None,index=None):
  self.playing()
  def change(s):
   if index is not None:s['doraIndicators'].pop(index)
   else:s.update(self.engine.apply(s,{'type':'dora','tile':self.engine.invoke('indicatorFromTile',tile)}))
  self.update(change,'dora')

 def configure_broadcast(self,config):
  self.update(lambda s:tournament.configure(s,config),'broadcast')
  self.display_changed.emit()
 def configure_presentation(self,values,settings=None,kind='presentation',emit=True):
  """Commit display config/settings together without consuming table history."""
  from commentary_style import COMMENTARY_STYLE_KEYS
  from sidebar_presets import SIDEBAR_SETTING_KEYS
  from postmatch_config import POSTMATCH_SETTING_KEYS
  from corner_header import CORNER_SETTING_KEYS
  from opening_config import OPENING_SETTING_KEYS
  from session_repository import validate_settings
  allowed=(SIDEBAR_SETTING_KEYS|POSTMATCH_SETTING_KEYS|CORNER_SETTING_KEYS|OPENING_SETTING_KEYS|COMMENTARY_STYLE_KEYS|
   {'sidebar_presets','sidebar_duration','standings_mode','standings_page','standings_page_size','standings_live',
    'table_score_mode','table_score_layout','show_match_title','chart_mode','statistics_scope','statistics_view','statistics_page',
    'settlement_enabled','settlement_hold','settlement_total_hold','hud_opacity','riichi_effect_enabled',
    'commentary_title','commentary_body','commentary_builtin','commentary_duration','custom_terms',
    'term_favorites','term_buttons','term_button_columns'})
  if not isinstance(values,dict) or not set(values)<=allowed:raise ValueError('播出配置只能修改展示字段')
  before=copy_state(self.state);after=copy_state(before)
  after['broadcast']['config'].update(copy.deepcopy(values));tournament.validate_broadcast(after)
  from media_store import manage_photos
  presentation={key:copy.deepcopy(after['broadcast']['config'][key]) for key in values}
  manage_photos(presentation,self.path,strict=True);after['broadcast']['config'].update(presentation)
  candidate=copy.deepcopy(self.settings if settings is None else settings)
  if set(values)&CORNER_SETTING_KEYS:
   from corner_header import corner_canvas_size
   old_size=corner_canvas_size(before['broadcast']['config']);new_size=corner_canvas_size(after['broadcast']['config'])
   geometry=candidate.get('windows',{}).get('corner_header')
   if geometry and old_size!=new_size:
    factor=geometry[2]/old_size[0]
    candidate['windows']['corner_header']=[*geometry[:2],max(1,round(new_size[0]*factor)),max(1,round(new_size[1]*factor))]
  validate_settings(candidate)
  # Copy the editable table/config part only. Immutable archived results remain
  # shared across all 60 undo entries rather than copied 60 more times.
  undo,redo=copy.deepcopy((self.undo,self.redo))
  for state,_ in undo+redo:state['broadcast']['config'].update(copy.deepcopy(presentation))
  self._save_snapshot(after,candidate,self.last_info,self.event)
  self.state,self.settings,self.undo,self.redo=after,candidate,undo,redo
  seal_archives(self.state)
  if emit:self.changed.emit(before,after,kind);self.display_changed.emit()
 def _configure_display_group(self,values,allowed,label,kind):
  if not isinstance(values,dict) or not set(values)<=allowed:raise ValueError(label+'设置字段无效')
  complete={key:copy.deepcopy(self.state['broadcast']['config'][key]) for key in allowed}
  complete.update(copy.deepcopy(values))
  self.configure_presentation(complete,kind=kind)
 def configure_sidebar(self,values):
  from sidebar_presets import SIDEBAR_SETTING_KEYS
  self._configure_display_group(values,SIDEBAR_SETTING_KEYS|{'sidebar_presets','sidebar_duration'},'侧栏显示','sidebar')
 def set_roster(self,roster):self.configure_broadcast({'roster':roster})
 def configure_postmatch(self,values):
  from postmatch_config import POSTMATCH_SETTING_KEYS
  self._configure_display_group(values,POSTMATCH_SETTING_KEYS,'赛后连播','postmatch')
 def configure_corner_header(self,values):
  from corner_header import CORNER_SETTING_KEYS
  self._configure_display_group(values,CORNER_SETTING_KEYS,'右上角标识','corner_header')
 def configure_opening(self,values):
  from opening_config import OPENING_SETTING_KEYS
  if not isinstance(values,dict) or not set(values)<=OPENING_SETTING_KEYS:raise ValueError('赛前开场设置字段无效')
  values=copy.deepcopy(values)
  if isinstance(values.get('opening_title'),Path):values['opening_title']=str(values['opening_title'])
  if 'opening_title' in values:values.setdefault('opening_title_text_only',False)
  self._configure_display_group(values,OPENING_SETTING_KEYS,'赛前开场','opening')
 def set_riichi_effect_enabled(self,enabled):
  if not isinstance(enabled,bool):raise ValueError('立直特效开关格式无效')
  self.configure_presentation({'riichi_effect_enabled':enabled},kind='riichi_effect')
 def configure_commentary(self,values):
  from commentary_style import COMMENTARY_STYLE_KEYS
  allowed={'commentary_title','commentary_body','commentary_builtin','commentary_duration',
           'custom_terms','term_favorites','term_buttons','term_button_columns'}|COMMENTARY_STYLE_KEYS
  if not isinstance(values,dict) or not set(values)<=allowed:raise ValueError('术语设置字段无效')
  self.configure_presentation(values,kind='commentary')
 def assign_player(self,seat,player_id):
  if not isinstance(seat,int) or not 0<=seat<4:raise ValueError('座位编号无效')
  seats=[p['id'] for p in self.state['players']];seats[seat]=player_id
  self.configure_broadcast({'seats':seats})
 def commit_match(self):
  self.matches.commit()
 def start_match(self):
  self.matches.start()
 def export_data(self,path):
  self.repository.export(path,self.path,self.state,self.settings,self.last_info,self.event)
 def read_data(self,path,local=False):
  return self.repository.read(path,local=local)
 def reload_current(self):
  """Explicitly discard the local snapshot and adopt the latest durable one."""
  identity=self.event.get('id') if self.event_store and self.event else None
  if identity and self.event_store.directory(identity).resolve()!=self.path.resolve():
   raise ValueError('已保存的赛事编号与当前工作区不一致，原文件已保留')
  restored,settings,info,embedded,event=self.repository.read(self.path/'session.json',expected_event_id=identity,local=True)
  candidate=default_settings();candidate.update(settings)
  self.preferences.apply(candidate)
  before=copy.deepcopy(self.state)
  self.state,self.settings,self.last_info,self.event=restored,candidate,info,event
  self.undo=[];self.redo=[];self.last_transition=None
  self.changed.emit(before,self.state,'import');self.display_changed.emit()
 def import_data(self,path,replace_settings=False,clear_history=False,emit=True,allow_event_kind_change=False):
  source=Path(path);restored,settings,info,embedded,event=self.repository.read(source,observe=False)
  if self.event_store and self.event and not allow_event_kind_change:
   target_kind=event.get('kind') if event else ('team_league' if restored['broadcast']['config'].get('event_mode')=='team' else 'individual_league')
   if target_kind!=self.event['kind']:
    raise ValueError('导入文件的比赛类型与当前赛事不同，请从赛事库导入为独立赛事')
  from media_store import manage_photos,archive_import_file
  manage_photos(restored,self.path,source_base=source.parent,embedded=embedded,strict=True)
  archive_import_file(self.path,source,event_owned=self.event_store is not None)
  previous=copy.deepcopy(self.state);undo=(self.undo+[(previous,copy.deepcopy(self.last_info))])[-60:]
  candidate_settings=default_settings() if replace_settings else copy.deepcopy(self.settings)
  candidate_settings.update(settings)
  candidate_event=self.event
  if event is not None:
   event=copy.deepcopy(event)
   if self.event:event['id']=self.event['id']
   candidate_event=event
  from application_preferences import APPLICATION_KEYS
  self._save_snapshot(restored,candidate_settings,info,candidate_event,set(settings)&set(APPLICATION_KEYS))
  self.state=restored;self.settings=candidate_settings;self.last_info=info;self.last_transition=None
  self.event=candidate_event;self.undo=[] if clear_history else undo;self.redo=[]
  if emit:self.changed.emit(previous,self.state,'import');self.display_changed.emit()
