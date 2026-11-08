from __future__ import annotations
import sys,time,uuid,math,json,copy
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import Qt,QPoint,QTimer,QRectF,QSize,QSignalBlocker
from PySide6.QtGui import QPainter,QPen,QColor,QPicture,QImage,QCursor
from PySide6.QtWidgets import QWidget,QApplication
from PySide6.QtSvg import QSvgGenerator
from artwork import Artwork,Animation,SIZES,ease,ROUND_HEADER
from broadcast_artwork import EXTRA_SIZES,draw_extension
from i18n import tr,set_language
from tournament import final_settlement_summary
from mahjong_values import display_score
from player_layout import PLAYER_LAYOUT,PLAYER_CAPTURE_SIZE,PLAYER_CAPTURE_BOTTOM,restore_player_geometries
from player_status_frame import status_mode, frame_phase
from sidebar_paging import sidebar_page_data, sidebar_render_model, is_team_event
from riichi_effect import DESIGN_SIZE as RIICHI_SIZE, RiichiQueue, snapshot_riichi, draw_riichi
from capture_surface import clear_capture,draw_locator,reference_canvas,reference_width,CaptureDpiMonitor,dpi_scaled_geometry,screen_pixel_ratio

WINDOW_LABELS={'fullscreen':'全屏节目','players':'四家选手','opening':'赛前开场','postmatch':'赛后连播','four_conditions':'四人并排条件','round':'局况','dora':'宝牌','yaku':'和牌','qualification':'侧边展示栏',
               'table_scores':'上方四人积分','commentary':'术语解释','standings':'赛事排名','statistics':'技术统计','chart':'赛后走势','corner_header':'右上角赛事标识','riichi_effect':'立直特效'}
FULLSCREEN_PARTS=('opening','postmatch','chart','statistics','four_conditions','riichi_effect')
FULLSCREEN_PANELS=('chart','statistics','four_conditions')

class _RiichiDisplayModel:
 """Live artwork sees only completed declarations; the model stays immediate."""
 def __init__(self,manager):self.manager=manager
 @property
 def state(self):return self.manager.riichi_display_state()
 def __getattr__(self,name):return getattr(self.manager.model,name)

class OverlayWindow(QWidget):
 def __init__(self,manager,key,kind,index=0):
  flags=Qt.WindowType.Window|Qt.WindowType.FramelessWindowHint
  if kind=='riichi_effect':flags|=Qt.WindowType.WindowDoesNotAcceptFocus
  if manager.model.settings.get('topmost',False):flags|=Qt.WindowType.WindowStaysOnTopHint
  super().__init__(None,flags)
  self.manager=manager;self.key=key;self.kind=kind;self.index=index;self.base=manager.base_size(kind);self.drag=None;self._input_locked=None;self.is_topmost=manager.model.settings.get('topmost',False)
  self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground);self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
  self.setProperty('i18nLiteral',True)
  self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
  self.setWindowTitle(manager.capture_title(kind,index))
  self.resize(*self.base);self.setMinimumSize(1,1) if kind=='commentary' else self.setMinimumSize(100,30);self.setMouseTracking(True)
 def paintEvent(self,event):
  p=QPainter(self)
  try:
   preview=self.manager.capture_previewing(self.key)
   shown=not self.manager.capture_suppressed(self.key) and (preview or self.manager.content_visible(self.key))
   clear_capture(p,QRectF(self.rect()),self.manager.model.settings if shown else {})
   if not shown:return
   p.setRenderHint(QPainter.RenderHint.Antialiasing);p.scale(self.width()/self.base[0],self.height()/self.base[1])
   if preview:self.manager.draw_content(p,self.kind,self.index,static=True)
   else:self.manager.paint_content(p,self.kind,self.index)
   p.resetTransform()
   if preview:draw_locator(p,QRectF(self.rect()),self.manager.window_label(self.kind,self.index))
  finally:p.end()
 def mousePressEvent(self,event):
  if self._input_locked or event.button()!=Qt.MouseButton.LeftButton:return
  self.drag=(event.globalPosition().toPoint(),self.pos(),self.size());self.group_drag_snapshot=self.manager.player_group_snapshot() if self.kind in ('player','standings') and self.manager.model.settings.get('player_group_linked',False) else None;event.accept()
 def mouseMoveEvent(self,event):
  if not self.drag:return
  start,pos,size=self.drag;delta=event.globalPosition().toPoint()-start
  if self.group_drag_snapshot:self.manager.translate_player_group(self.group_drag_snapshot,delta)
  else:self.move(pos+delta)
 def mouseReleaseEvent(self,event):
  if self.drag:self.drag=None;self.manager.display_configuration.run_native(self.manager.save_geometry)
 def wheelEvent(self,event):
  if self._input_locked or not event.modifiers() & Qt.KeyboardModifier.ControlModifier:event.ignore();return
  factor=1.06 if event.angleDelta().y()>0 else 1/1.06
  if self.kind in ('player','standings') and self.manager.model.settings.get('player_group_linked',False):self.manager.display_configuration.run_native(lambda:self.manager.scale_player_group(factor));return
  w=max(120,min(2400,round(self.width()*factor)));self.resize(w,round(w*self.base[1]/self.base[0]));self.manager.display_configuration.run_native(self.manager.save_geometry)
 def closeEvent(self,event):
  if self.manager.closing:event.accept();return
  event.ignore();self.manager.display_configuration.run_native(lambda:self.manager.set_visible(self.key,False))
 def input_lock(self,locked):
  if self._input_locked==locked:return
  self._input_locked=locked
  self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,locked)
  if sys.platform=='win32':
   import ctypes
   lib=ctypes.windll.user32;get=lib.GetWindowLongPtrW;set_=lib.SetWindowLongPtrW;get.restype=ctypes.c_ssize_t;set_.restype=ctypes.c_ssize_t;get.argtypes=[ctypes.c_void_p,ctypes.c_int];set_.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_ssize_t]
   handle=int(self.winId());style=get(handle,-20);set_(handle,-20,(style|0x20) if locked else (style&~0x20))
  self.update()
 def apply_topmost(self,value):
  if self.is_topmost==value:return
  self.is_topmost=value
  if sys.platform=='win32':
   import ctypes
   fn=ctypes.windll.user32.SetWindowPos;fn.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint]
   fn(int(self.winId()),ctypes.c_void_p(-1 if value else -2),0,0,0,0,0x1|0x2|0x10)
  else:self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,value)


class HUDCaptureWindow(OverlayWindow):
 """Draw vector HUD at twice the layout resolution for flexible OBS sizing.

 The hidden round window owns layout/save coordinates. This surface only owns
 capture pixels; increasing their density never rewrites the saved footprint.
 """
 capture_pixel_ratio=2
 def __init__(self,manager):
  self._synchronizing=True
  super().__init__(manager,'round','round')
  self.sync_geometry();self._synchronizing=False
 def sync_geometry(self):
  layout=self.manager.windows['round'];self.base=layout.base
  self._synchronizing=True
  try:
   geometry=(layout.x(),layout.y(),layout.width()*self.capture_pixel_ratio,layout.height()*self.capture_pixel_ratio)
   if self.geometry().getRect()!=geometry:self.setGeometry(*geometry)
  finally:self._synchronizing=False
 def moveEvent(self,event):
  super().moveEvent(event)
  if not self._synchronizing:self.manager.windows['round'].move(self.pos())
 def wheelEvent(self,event):
  if self._input_locked or not event.modifiers() & Qt.KeyboardModifier.ControlModifier:event.ignore();return
  layout=self.manager.windows['round'];factor=1.06 if event.angleDelta().y()>0 else 1/1.06
  width=max(120,min(2400,round(layout.width()*factor)))
  layout.resize(width,round(width*layout.base[1]/layout.base[0]));self.sync_geometry();self.manager.display_configuration.run_native(self.manager.save_geometry)


class PlayersCaptureWindow(OverlayWindow):
 """One native capture window, using the unchanged four card renderers/layouts."""
 def __init__(self,manager):
  self._synchronizing=True
  super().__init__(manager,'players','players')
  self.sync_geometry()
  self._synchronizing=False
 def sync_geometry(self):
  bounds=self.manager.player_capture_bounds().toAlignedRect();self.base=self.manager.base_size('players')
  was_syncing=self._synchronizing;self._synchronizing=True
  try:
   if self.geometry()!=bounds:self.setGeometry(bounds)
  finally:self._synchronizing=was_syncing
 def moveEvent(self,event):
  super().moveEvent(event)
  if self._synchronizing:return
  bounds=self.manager.player_capture_bounds().toAlignedRect()
  delta=self.pos()-bounds.topLeft()
  if not delta.isNull():self.manager.translate_player_group(self.manager.player_group_snapshot(),delta)
 def paintEvent(self,event):
  p=QPainter(self)
  try:
   preview=self.manager.capture_previewing('players')
   shown=not self.manager.capture_suppressed('players') and (preview or self.manager.player_capture_visible())
   clear_capture(p,QRectF(self.rect()),self.manager.model.settings if shown else {})
   if not shown:return
   p.save()
   for index in range(4):
    key=f'player{index}';window=self.manager.windows[key]
    if not preview and not self.manager.content_visible(key):continue
    p.save();p.translate(window.x()-self.x(),window.y()-self.y());p.scale(window.width()/window.base[0],window.height()/window.base[1])
    p.setClipRect(QRectF(0,0,*window.base));p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if preview:self.manager.draw_content(p,'player',index,static=True)
    else:self.manager.paint_content(p,'player',index)
    p.restore()
   if not preview and self.manager.content_visible('standings'):
    window=self.manager.windows['standings']
    p.save();p.translate(window.x()-self.x(),window.y()-self.y());p.scale(window.width()/window.base[0],window.height()/window.base[1])
    p.setClipRect(QRectF(0,0,*window.base));p.setRenderHint(QPainter.RenderHint.Antialiasing)
    self.manager.paint_content(p,'standings');p.restore()
   p.restore()
   if preview:draw_locator(p,QRectF(self.rect()),'四家选手')
  finally:p.end()
 def mousePressEvent(self,event):
  if self._input_locked or event.button()!=Qt.MouseButton.LeftButton:return
  self.drag=(event.globalPosition().toPoint(),self.manager.player_group_snapshot())
 def mouseMoveEvent(self,event):
  if self.drag:self.manager.translate_player_group(self.drag[1],event.globalPosition().toPoint()-self.drag[0])
 def mouseReleaseEvent(self,event):
  if self.drag:self.drag=None;self.manager.display_configuration.run_native(self.manager.save_geometry)
 def wheelEvent(self,event):
  if self._input_locked or not event.modifiers() & Qt.KeyboardModifier.ControlModifier:event.ignore();return
  self.manager.display_configuration.run_native(lambda:self.manager.scale_player_group(1.06 if event.angleDelta().y()>0 else 1/1.06))

class OverlayManager:
 def __init__(self,model,riichi_audio=None):
  self._capture_previews={}
  self.model=model;self._riichi_holds={};self._riichi_payment_active=False;self.animation=Animation();self.art=Artwork(_RiichiDisplayModel(self),self.animation);self.closing=False;self.enabled=model.settings.get('visible',{}).copy();self.windows={};self.yaku_started=0.;self.speed=max(.1,model.settings.get('speed',1.));self.stage='table';self.stage_started=0.;self.pending_win=False;self.pending_next=model.state['phase'] in ('result','finished');self.info_signature=None;self.final_pt_deadline=None;self._auto_final_pending=False;self._final_target='pt'
  self._font_signature=None
  self.language=model.settings.get('language','zh_CN');set_language(self.language)
  self.extension_transitions={};self.extension_cache={};self.commentary_deadline=None
  self._bottom_player_signature=None;self._bottom_was_enabled=False;self._standings_change=None;self._final_card_change=None
  self._commentary_auto=False;self._commentary_auto_geometry=None;self._commentary_reference=None
  self.settlement_data=None;self._settlement_geometry=None;self._last_final_archive=None;self.chart_started=None
  self.chart_flow_started=time.monotonic()
  self.riichi_queue=RiichiQueue()
  if riichi_audio is None:
   from riichi_audio import RiichiAudio
   riichi_audio=RiichiAudio()
  self.riichi_audio=riichi_audio;self._riichi_sound_sequence=None
  self._sidebar_signature=None;self._sidebar_page=0;self._sidebar_pages=1;self._sidebar_transition=None;self._sidebar_deadline=None;self._sidebar_auto_active=False;self._sidebar_interval=10.
  for key in EXTRA_SIZES:self.enabled.setdefault(key,key=='table_scores' and model.settings.get('broadcast_scene','table') in ('table','qualification'))
  # Replays are explicitly cued; opening an application/session starts idle.
  for key in FULLSCREEN_PARTS:self.enabled[key]=False
  self._programme_activity={'opening':False,'postmatch':False};self._effect_active=False
  model.settings['visible']=self.enabled.copy()
  self.timer=QTimer();self.timer.setInterval(16);self.timer.timeout.connect(self.tick)
  self.sidebar_timer=QTimer();self.sidebar_timer.setSingleShot(True);self.sidebar_timer.setTimerType(Qt.TimerType.PreciseTimer);self.sidebar_timer.timeout.connect(self._wake_sidebar_page)
  self.sidebar_hide_deadline=None;self._sidebar_timeout_signature=None
  self.sidebar_hide_timer=QTimer();self.sidebar_hide_timer.setSingleShot(True);self.sidebar_hide_timer.setTimerType(Qt.TimerType.PreciseTimer);self.sidebar_hide_timer.timeout.connect(lambda:self.display_configuration.run_native(self._expire_sidebar_timeout))
  self.status_frame_timer=QTimer();self.status_frame_timer.setSingleShot(True);self.status_frame_timer.timeout.connect(self._wake_status_frames)
  self.commentary_timer=QTimer();self.commentary_timer.setSingleShot(True);self.commentary_timer.timeout.connect(lambda:self.display_configuration.run_native(lambda:self.set_extension_visible('commentary',False)))
  version=model.settings.get('layout_version',0)
  if version<3:
   for key in ('yaku','round','dora'):model.settings['windows'].pop(key,None)
  if version<7:
   saved=model.settings['windows'];round_geometry=saved.get('round')
   if round_geometry:
    x,y,w,h=round_geometry;scale=w/348
    rw,rh=[round(v*scale) for v in SIZES['round']]
    saved['round']=[x,y,rw,rh]
    saved['dora']=[x+rw,y,round(SIZES['dora'][0]*scale),rh]
   else:saved.pop('dora',None)
   if 'yaku' in saved:
    x,y,w,h=saved['yaku'];height=round(w*SIZES['yaku'][1]/SIZES['yaku'][0]);saved['yaku']=[x,y+h-height,w,height]
   model.settings['layout_version']=7
  for key,kind,index in [('round','round',0)]+[(f'player{i}','player',i) for i in range(4)]+[('yaku','yaku',0)]:self.windows[key]=OverlayWindow(self,key,kind,index)
  for key in EXTRA_SIZES:self.windows[key]=OverlayWindow(self,key,key)
  self.recover_empty_visibility()
  if model.settings.get('player_capture_version')!=1:
   group_visible=any(self.enabled.get(f'player{i}',True) for i in range(4))
   for index in range(4):self.enabled[f'player{index}']=group_visible
   model.settings['visible']=self.enabled.copy();model.settings['player_capture_version']=1
  self.windows['riichi_effect']=OverlayWindow(self,'riichi_effect','riichi_effect')
  self.restore_geometry();self.model.changed.connect(self.on_change);self.model.display_changed.connect(self.on_display)
  self.players_capture=PlayersCaptureWindow(self)
  self.hud_capture=HUDCaptureWindow(self)
  self._restore_settlement_data()
  # Saved terminal hands resume on final cards, not a stale win banner.
  if self.model.state.get('phase')=='finished':
   self.pending_next=False;self.model.settings['show_yaku']=False
  if self.enabled.get('chart'):self.replay_chart(show=False)
  if self.enabled.get('commentary'):self.reset_commentary_timer()
  self._sync_sidebar_paging(time.monotonic())
  from postmatch_player import PostMatchPlayback,PostMatchWindow
  self.postmatch=PostMatchPlayback(model)
  self.postmatch.changed.connect(self._postmatch_changed)
  self.model.changed.connect(self.postmatch.on_model_changed)
  from match_opening_player import MatchOpeningPlayback,MatchOpeningWindow
  self.opening=MatchOpeningPlayback(model,self._opening_guard)
  self.opening.changed.connect(self._postmatch_changed)
  self.opening.finished.connect(self._opening_finished)
  self.model.changed.connect(self.opening.on_model_changed)
  from fullscreen_capture import FullscreenCaptureWindow
  self.fullscreen_capture=FullscreenCaptureWindow(self)
  self.split_programme_windows={'opening':MatchOpeningWindow(self.opening),'postmatch':PostMatchWindow(self.postmatch)}
  for window in self.split_programme_windows.values():window._geometry_manager=self
  self._bind_fullscreen_windows()
  from programme import ProgrammeController
  self.programme=ProgrammeController(self)
  from display_configuration import DisplayConfigurationService
  self.display_configuration=DisplayConfigurationService(self)
  self.on_display()
  self.accept_geometry()
  captures=[self.players_capture,self.hud_capture,self.fullscreen_capture,*self.split_programme_windows.values(),*[window for key,window in self.windows.items() if not key.startswith('player') and key not in ('round','standings')]]
  for window in captures:window.output_physical_size=lambda window=window:self.capture_output_size(window)
  self._capture_dpi_monitors=[CaptureDpiMonitor(window,self._capture_dpi_changed) for window in dict.fromkeys(captures)]
 @staticmethod
 def capture_title(kind,index=0):
  label='四家选手' if kind=='players' else '选手 '+str(index+1) if kind=='player' else '局况 · 宝牌' if kind=='round' else WINDOW_LABELS.get(kind,kind)
  return '麻将UI · '+label
 @property
 def layout_windows(self):
  values={'round':self.windows['round'],'players':self.players_capture}
  values.update({key:window for key,window in self.windows.items() if key not in ('round','standings') and not key.startswith('player')})
  values.update(self.split_programme_windows)
  values['fullscreen']=self.fullscreen_capture
  return values
 @property
 def capture_windows(self):
  split=self.model.settings.get('split_fullscreen_windows',False)
  return {key:self.hud_capture if key=='round' else window for key,window in self.layout_windows.items() if (key!='fullscreen' if split else key not in FULLSCREEN_PARTS)}
 def _bind_fullscreen_windows(self):
  split=self.model.settings.get('split_fullscreen_windows',False)
  for key in ('opening','postmatch'):
   getattr(self,key).bind_capture_window(self.split_programme_windows[key] if split else self.fullscreen_capture)
 def _sync_programme_visibility(self):
  changed=False
  for key in ('opening','postmatch'):
   active=bool(getattr(self,key).active)
   if active!=self._programme_activity[key]:
    self.enabled[key]=active;self._programme_activity[key]=active;changed=True
  active=bool(self._riichi_allowed() and self.riichi_queue.busy)
  if active!=self._effect_active:
   self.enabled['riichi_effect']=active;self._effect_active=active;changed=True
  if changed:self.model.settings['visible']=self.enabled.copy()
 def normalize_fullscreen_selection(self):
  if self.model.settings.get('split_fullscreen_windows',False):return
  active=[key for key in FULLSCREEN_PANELS if self.enabled.get(key,False)]
  if len(active)>1:
   selected=self.model.settings.get('fullscreen_panel','chart')
   if selected not in active:selected=active[0]
   for key in active:
    if key!=selected:self.enabled[key]=False;self.extension_transitions.pop(key,None)
   self.model.settings['visible']=self.enabled.copy()
 def fullscreen_panel(self):
  selected=self.model.settings.get('fullscreen_panel')
  active=[key for key in FULLSCREEN_PANELS if self.content_visible(key)]
  return selected if selected in active else next(iter(active),None)
 def player_capture_visible(self):
  return self.capture_enabled('players') and (any(self.content_visible(f'player{i}') for i in range(4)) or self.content_visible('standings'))
 def capture_enabled(self,key):
  if key=='players':return self.enabled.get('players',any(self.enabled.get(f'player{i}',True) for i in range(4)) or self.enabled.get('standings',False))
  if key in ('opening','postmatch'):return self.model.settings.get('visible',{}).get(key,True)
  return self.enabled.get(key,True)
 def reference_width(self,key):
  if key=='commentary':return max(1,round(900/screen_pixel_ratio(self.windows[key])))
  return reference_width(self.layout_windows[key])
 def draft_capture_visibility(self,key,value):
  for target in ([f'player{i}' for i in range(4)] if key=='players' else [key]):self.enabled[target]=value
  if key=='players':self.enabled['players']=value
 def capture_preview_mode(self,key):
  for keys in reversed(list(self._capture_previews.values())):
   if key in keys:return keys[key]
  return None
 def capture_previewing(self,key):return self.capture_preview_mode(key) is True
 def capture_suppressed(self,key):return self.capture_preview_mode(key) is False
 def preview_capture(self,owner,key,visible=True):
  keys=self._capture_previews.setdefault(owner,{})
  keys[key]=bool(visible)
  self.on_display()
  if visible:self.capture_windows.get(key,self.layout_windows[key]).raise_()
 def clear_capture_preview(self,owner):
  self._capture_previews.pop(owner,None)
  if not self.closing:self.on_display()
 def player_capture_bounds(self):
  bounds=QRectF(self.windows['player0'].geometry())
  for index in range(1,4):bounds=bounds.united(QRectF(self.windows[f'player{index}'].geometry()))
  # Whitespace belongs to the capture canvas. Scaling all artwork to create
  # margins made every card smaller again after its physical size was chosen.
  body=PLAYER_LAYOUT.body_bounds()
  left=min(self.windows[f'player{i}'].x()+body.left()*self.windows[f'player{i}'].width()/438 for i in range(4))
  right=max(self.windows[f'player{i}'].x()+body.right()*self.windows[f'player{i}'].width()/438 for i in range(4))
  center=(left+right)/2
  scale=sum(self.windows[f'player{i}'].width() for i in range(4))/(4*438)
  ratio=screen_pixel_ratio(getattr(self,'players_capture',self.windows['player0']))
  if abs(scale*ratio-1)<2/438:scale=1/ratio
  padding=max(0.,min(15.,self.model.settings.get('player_side_padding',2.3)))/100
  native_width=round(PLAYER_CAPTURE_SIZE[0]*scale)
  width=round(max(native_width,2*max(center-bounds.left(),bounds.right()-center),(right-left)/(1-2*padding)))
  if abs(width-native_width)<=2:width=native_width
  return QRectF(round(center-width/2),bounds.y(),width,bounds.height()+round(PLAYER_CAPTURE_BOTTOM*scale))
 def capture_output_size(self,window):
  """Integer broadcast pixels; compensate Qt's fractional-DPI rounding in OBS."""
  ratio=screen_pixel_ratio(window)
  if window is self.players_capture:
   width=round(window.width()*ratio)
   if abs(width-1920)<=3:width=1920
   card=self.windows['player0'];card_width=round(card.width()*ratio)
   if abs(card_width-438)<=2:card_width=438
   height=round(card_width*PLAYER_CAPTURE_SIZE[1]/438)
   # A custom multi-row layout retains its complete captured height.
   bottom=round(PLAYER_CAPTURE_BOTTOM*card.width()/438)
   if abs(window.height()-card.height()-bottom)>2:height=round(window.height()*ratio)
   return width,height
  layout=self.windows['round'] if window is self.hud_capture else window
  width=round(layout.width()*ratio)
  if abs(width-layout.base[0])<=2:width=layout.base[0]
  if window is self.windows.get('commentary'):return width,round(layout.height()*ratio)
  return width,max(1,round(width*layout.base[1]/layout.base[0]))
 def _capture_dpi_changed(self,window,old_ratio,new_ratio):
  """Keep the chosen physical broadcast size while dragging to another screen."""
  if self.closing:return
  def resize():
   if window is self.players_capture:
    factor=old_ratio/new_ratio;origin=window.pos()
    for key,(x,y,width,height) in self.player_group_snapshot().items():
     self.windows[key].setGeometry(round(origin.x()+(x-origin.x())*factor),round(origin.y()+(y-origin.y())*factor),round(width*factor),round(height*factor))
    self.players_capture.sync_geometry()
   elif window is self.hud_capture:
    layout=self.windows['round'];layout.setGeometry(dpi_scaled_geometry(layout.geometry(),old_ratio,new_ratio));self.hud_capture.sync_geometry()
   else:window.setGeometry(dpi_scaled_geometry(window.geometry(),old_ratio,new_ratio))
  result=self.display_configuration.run_native(lambda:self.display_configuration.geometry_edit(resize))
  if result is not False and window is self.players_capture and window.drag:
   # Continue a held drag from the resized group, not its pre-DPI dimensions.
   window.drag=(QCursor.pos(),self.player_group_snapshot())
  for monitor in self._capture_dpi_monitors:monitor.rebase()
 def resize_player_capture(self,x,y,width):
  bounds=self.player_capture_bounds();factor=width/max(1.,bounds.width())
  widths=[self.windows[f'player{i}'].width() for i in range(4)]
  factor=max(120/min(widths),min(2400/max(widths),factor))
  for key,(old_x,old_y,w,h) in self.player_group_snapshot().items():
   self.windows[key].setGeometry(round(x+(old_x-bounds.x())*factor),round(y+(old_y-bounds.y())*factor),round(w*factor),round(h*factor))
  self.players_capture.sync_geometry()
 def _opening_active(self):
  return bool(getattr(self,'opening',None) and self.opening.active)
 def _opening_guard(self):
  if self.model.state.get('phase')!='playing':raise ValueError('请先准备好本场四席，再播放赛前开场')
  if not self.model.settings.get('show_overlays',True) or self.model.settings.get('broadcast_scene')=='clean':raise ValueError('请先显示直播窗口，再播放赛前开场')
  if self.riichi_queue.busy or self.animation.active or self._final_card_change is not None or self.stage not in ('table','players_demo_in') or self.pending_win or self.pending_next or self.postmatch.active or self.postmatch.preparing:
   raise ValueError('请等待当前播出动画结束后再播放赛前开场')
 def _opening_finished(self):
  if not self.closing and self.model.state.get('phase')=='playing':self.set_stage('players_demo_in')
 def _postmatch_changed(self):
  if not self.closing:self.on_display()
 def recover_empty_visibility(self,explicit=False):
  """Recover an empty saved scene; keep deliberate clean/hidden sessions intact."""
  if not explicit and (not self.model.settings.get('show_overlays',True) or self.model.settings.get('broadcast_scene')=='clean'):return False
  if any(self.enabled.get(key,key not in EXTRA_SIZES) for key in self.windows if key!='riichi_effect'):return False
  for key in ('round','yaku',*[f'player{i}' for i in range(4)]):self.enabled[key]=True
  self.model.settings.update(visible=self.enabled.copy(),broadcast_scene='table')
  return True
 def set_overlays_visible(self,visible):
  enabled=self.enabled.copy();patch={'show_overlays':bool(visible)}
  if visible and not any(enabled.get(key,key not in EXTRA_SIZES) for key in self.windows if key!='riichi_effect'):
   for key in ('round','yaku',*[f'player{i}' for i in range(4)]):enabled[key]=True
   patch.update(visible=enabled.copy(),broadcast_scene='table')
  self._save_display_settings(**patch);self.enabled=enabled
  self.on_display();self.model.display_changed.emit()
 def window_label(self,kind,index=0):
  if kind==self.settlement_window():
   if self.stage=='settlement_hand':return '半庄最终积分'
   return '队伍总积分变动' if self.settlement_data['mode']=='team' else '个人总积分'
  if kind=='qualification' and self.sidebar_mode()=='team':return '队伍排名'
  if kind=='qualification' and self.sidebar_mode()=='individual':return '个人排名'
  return '局况 · 宝牌' if kind=='round' else WINDOW_LABELS.get(kind,'选手 '+str(index+1))
 def sidebar_mode(self):
  return self.model.state.get('broadcast',{}).get('config',{}).get('sidebar_mode','qualification')
 def set_sidebar_mode(self,mode,visible=True):
  if mode not in ('qualification','team','individual'):raise ValueError('侧栏显示模式无效')
  enabled=self.enabled.copy()
  if visible is not None:enabled['qualification']=bool(visible)
  before=self.display_configuration.commit(config={'sidebar_mode':mode},patch={'visible':enabled.copy()},kind='sidebar')
  if visible is not None:self.extension_transitions['qualification']=(time.monotonic(),self.extension_opacity('qualification'),float(bool(visible)))
  self.enabled=enabled;self.display_configuration.publish(before,'sidebar')
  if visible:self._restart_sidebar_timeout()
 def show_conditions(self,layout=None):
  layout=layout or self.model.state['broadcast']['config'].get('qualification_layout','four')
  enabled=self.enabled.copy();config={};patch={}
  if layout=='four':
   if self.sidebar_mode()=='qualification':enabled['qualification']=False
   enabled['four_conditions']=True;patch['fullscreen_panel']='four_conditions'
   if not self.model.settings.get('split_fullscreen_windows',False):
    for key in FULLSCREEN_PANELS:
     if key!='four_conditions':enabled[key]=False
  else:
   enabled['four_conditions']=False;enabled['qualification']=True;config['sidebar_mode']='qualification'
  before=self.display_configuration.commit(config=config,patch=dict(patch,visible=enabled.copy()),kind='sidebar')
  if layout=='four' and hasattr(self,'programme'):self.programme.before_panel('four_conditions')
  now=time.monotonic()
  for key in ('qualification','four_conditions'):
   if enabled.get(key,False)!=self.enabled.get(key,False):self.extension_transitions[key]=(now,self.extension_opacity(key),float(enabled.get(key,False)))
  self.enabled=enabled;self.model.settings['visible']=enabled.copy();self.timer.start();self.display_configuration.publish(before,'sidebar')
 def hide_conditions(self):
  enabled=self.enabled.copy();enabled['four_conditions']=False
  if self.sidebar_mode()=='qualification':enabled['qualification']=False
  self._save_display_settings(visible=enabled.copy());now=time.monotonic()
  for key in ('qualification','four_conditions'):
   if enabled.get(key,False)!=self.enabled.get(key,False):self.extension_transitions[key]=(now,self.extension_opacity(key),float(enabled.get(key,False)))
  self.enabled=enabled;self.timer.start();self.on_display();self.model.display_changed.emit()
 def conditions_visible(self):
  return self.enabled.get('four_conditions',False) or (self.enabled.get('qualification',False) and self.sidebar_mode()=='qualification')
 def stop_fullscreen_panels(self):
  enabled=self.enabled.copy();enabled.update({key:False for key in FULLSCREEN_PANELS})
  self._save_display_settings(visible=enabled.copy());now=time.monotonic()
  for key in FULLSCREEN_PANELS:self.extension_transitions[key]=(now,self.extension_opacity(key),0.)
  self.enabled=enabled;self.timer.start();self.on_display();self.model.display_changed.emit()
 def save_sidebar_presets(self,presets):
  from sidebar_presets import validate_sidebar_presets
  values=validate_sidebar_presets(presets)
  before=self.display_configuration.commit(config={'sidebar_presets':values},kind='sidebar')
  if getattr(self,'_active_sidebar_preset_id',None) not in {value['id'] for value in values}:self._active_sidebar_preset_id=None
  self.display_configuration.publish(before,'sidebar')
  return values
 def apply_sidebar_preset(self,identity):
  from sidebar_presets import sidebar_presets,validate_sidebar_presets,preset_available,preset_target,preset_settings
  preset=next((value for value in sidebar_presets(self.model) if value['id']==identity),None)
  if preset is None:raise ValueError('侧栏快捷方案不存在')
  validate_sidebar_presets([preset])
  if not preset_available(self.model,preset):raise ValueError('个人赛事不能使用团队排名方案')
  target=preset_target(preset);layout_before=self.display_configuration.snapshot()
  enabled=self.enabled.copy();settings=self._collect_geometry_settings();config=preset_settings(preset);geometry=None
  if target=='four_conditions':
   if self.sidebar_mode()=='qualification':enabled['qualification']=False
   enabled['four_conditions']=True;settings['fullscreen_panel']='four_conditions'
   if not settings.get('split_fullscreen_windows',False):
    for key in FULLSCREEN_PANELS:
     if key!='four_conditions':enabled[key]=False
  elif 'geometry' in preset:
   geometry=[round(value) for value in preset['geometry']]
   if not any(screen.availableGeometry().intersects(QRectF(*geometry).toRect()) for screen in QApplication.screens()):
    bounds=QApplication.primaryScreen().availableGeometry();geometry[0]=bounds.x();geometry[1]=bounds.y()
   geometry[2:]=[self.windows['qualification'].width(),self.windows['qualification'].height()]
   settings['windows']['qualification']=geometry[:]
   settings.setdefault('qualification_geometries',{})['sidebar']=geometry[:]
  if target!='four_conditions':
   enabled['qualification']=True
   if preset['settings']['sidebar_mode']=='qualification':enabled['four_conditions']=False
  settings['visible']=enabled.copy()
  try:before=self.display_configuration.commit(settings=settings,config=config,kind='sidebar')
  except Exception:
   self.display_configuration.restore(layout_before);raise
  self._sidebar_transition=None;self.sidebar_timer.stop()
  if geometry:self.windows['qualification'].move(*geometry[:2])
  now=time.monotonic()
  for key in ('qualification','four_conditions'):
   if enabled.get(key,False)!=self.enabled.get(key,False):self.extension_transitions[key]=(now,self.extension_opacity(key),float(enabled.get(key,False)))
  if target=='four_conditions' and hasattr(self,'programme'):self.programme.before_panel('four_conditions')
  self.enabled=enabled;self.model.settings['visible']=enabled.copy()
  self.display_configuration.publish(before,'sidebar')
  self._sidebar_page=int(preset['settings']['sidebar_page'])%self._sidebar_pages
  self._sidebar_transition=None
  if self._sidebar_auto_active:
   self._sidebar_deadline=time.monotonic()+self._sidebar_interval;self.sidebar_timer.start(max(1,round(self._sidebar_interval*1000)))
  self._active_sidebar_preset_id=identity;self._active_sidebar_preset_event=(getattr(self.model,'event',None) or {}).get('id');self._restart_sidebar_entry();self._restart_sidebar_timeout();self.on_display();self.accept_geometry()
  return preset
 def toggle_sidebar_preset(self,identity):
  """A live shortcut dismisses its own visible view; applying stays explicit."""
  from sidebar_presets import sidebar_presets,preset_available,preset_target,preset_settings
  preset=next((value for value in sidebar_presets(self.model) if value['id']==identity),None)
  if preset is None:raise ValueError('侧栏快捷方案不存在')
  if not preset_available(self.model,preset):raise ValueError('个人赛事不能使用团队排名方案')
  config=self.model.state.get('broadcast',{}).get('config',{})
  same_view=all(config.get(key)==value for key,value in preset_settings(preset).items())
  target=preset_target(preset)
  if self.enabled.get(target,False) and same_view:
   self.set_extension_visible(target,False)
   return preset
  return self.apply_sidebar_preset(identity)
 def _sidebar_quick_close_targets(self):
  from sidebar_presets import sidebar_presets,preset_available,preset_target
  targets={'qualification'}
  if any(preset_available(self.model,preset) and preset_target(preset)=='four_conditions' for preset in sidebar_presets(self.model)):
   targets.add('four_conditions')
  return targets
 def sidebar_quick_close_available(self):
  motion=getattr(self,'_sidebar_motion',None)
  return any(self.enabled.get(key,False) for key in self._sidebar_quick_close_targets()) or bool(motion and motion.target)
 def force_sidebar_exit(self):
  """Cancel pending sidebar work and exit from its currently visible frame."""
  if not self.sidebar_quick_close_available():return False
  now=time.monotonic();targets=self._sidebar_quick_close_targets();enabled=self.enabled.copy()
  old={key:self.extension_opacity(key,now) for key in targets}
  frozen=self._sidebar_page_frame(now) if getattr(self,'_sidebar_motion_context',False) else None
  for key in targets:enabled[key]=False
  # Failed persistence leaves the current animation and timer requests intact.
  before=self.display_configuration.commit(patch={'visible':enabled.copy()},kind='sidebar')
  self.enabled=enabled;self.model.settings['visible']=enabled.copy()
  self._sidebar_exit_page=frozen
  if frozen is not None:self._sidebar_page=frozen[0]
  self._sidebar_transition=None;self._sidebar_deadline=None;self._sidebar_paused_remaining=None;self._sidebar_auto_active=False;self.sidebar_timer.stop()
  self.sidebar_hide_deadline=None;self._sidebar_timeout_signature=None;self.sidebar_hide_timer.stop()
  self._active_sidebar_preset_id=None;self._active_sidebar_preset_event=None
  for key in targets:
   if old[key]>0:self.extension_transitions[key]=(now,old[key],0.)
  programme=getattr(self,'programme',None)
  if 'four_conditions' in targets and programme and programme.frozen_kind=='four_conditions':
   # A paused conditions preset must still be able to play its forced exit.
   programme._static_paused=False;programme.frozen_kind=programme.frozen_frame=None
  self.timer.start();self.display_configuration.publish(before,'sidebar')
  return True
 def base_size(self,kind):
  if kind=='players':
   return PLAYER_CAPTURE_SIZE
  if kind=='riichi_effect':return RIICHI_SIZE
  if kind=='commentary':return self._commentary_canvas_size()
  if kind=='qualification':
   from qualification_presentation import qualification_panel_size
   return (400,650) if self.settlement_window()=='qualification' else qualification_panel_size(self.model)
  size=(SIZES['round'][0]+SIZES['dora'][0],SIZES['round'][1]) if kind=='round' else EXTRA_SIZES[kind] if kind in EXTRA_SIZES else SIZES[kind]
  if kind in ('round','dora') and not self.model.state.get('broadcast',{}).get('config',{}).get('show_match_title',True):return size[0],size[1]-ROUND_HEADER
  return size
 def standings_layout(self):
  """Four real card slots mapped to the canonical ranking canvas."""
  bounds=QRectF(self.bottom_bounds().toAlignedRect());width,height=EXTRA_SIZES['standings']
  if bounds.isEmpty():return None
  sx,sy=width/bounds.width(),height/bounds.height()
  return [QRectF((area.x()-bounds.x())*sx,(area.y()-bounds.y())*sy,area.width()*sx,area.height()*sy)
          for area in (self.player_card_bounds(i) for i in range(4))]
 def _extension_picture(self,kind,model=None):
  layout=self.standings_layout() if kind=='standings' else None
  signature=tuple((r.x(),r.y(),r.width(),r.height()) for r in layout) if layout else None
  key=(kind,signature) if layout else kind
  if model is None and key in self.extension_cache:return self.extension_cache[key]
  picture=QPicture();record=QPainter(picture)
  try:draw_extension(record,kind,model or self.model,layout=layout)
  finally:record.end()
  if model is None:self.extension_cache[key]=picture
  return picture
 def _commentary_wanted(self):
  return bool(not self.closing and self.enabled.get('commentary',False) and self.model.settings.get('show_overlays',True) and not (getattr(self,'postmatch',None) and self.postmatch.active))
 def _commentary_canvas_size(self):
  from capture_surface import screen_pixel_ratio
  window=self.windows.get('commentary')
  if window is None:return EXTRA_SIZES['commentary']
  ratio=screen_pixel_ratio(window)
  return max(1,round(window.width()*ratio)),max(1,round(window.height()*ratio))
 def _commentary_native_height(self,width):
  from broadcast_artwork import commentary_layout
  from typography import overlay_scope
  with overlay_scope(self.model.settings):return commentary_layout(self.model,width)['height']
 def _commentary_content_signature(self):
  from commentary_style import commentary_style_options
  config=self.model.state.get('broadcast',{}).get('config',{})
  style=tuple(commentary_style_options(config).values())
  return tuple(config.get(key,default) for key,default in (('commentary_title',''),('commentary_body',''),('commentary_builtin',False),('show_match_title',True)))+(self.language,self.model.settings.get('overlay_font_family',''),self.model.settings.get('overlay_font_scale',1.),self.base_size('commentary')[0],style)
 def _capture_commentary(self):
  from broadcast_artwork import draw_commentary,commentary_layout
  from commentary_style import commentary_style_options
  from typography import overlay_scope
  config=self.model.state.get('broadcast',{}).get('config',{})
  frozen={key:config.get(key,default) for key,default in (('commentary_title',''),('commentary_body',''),('show_match_title',True))}
  for key in ('commentary_title','commentary_body'):frozen[key]=tr(str(frozen[key]),literal=not config.get('commentary_builtin',False))
  frozen['commentary_builtin']=False
  frozen.update(commentary_style_options(config))
  proxy=SimpleNamespace(state={'broadcast':{'config':frozen}},settings={key:self.model.settings.get(key,default) for key,default in (('overlay_font_family',''),('overlay_font_scale',1.))})
  size=self.base_size('commentary');picture=QPicture();record=QPainter(picture)
  try:
   with overlay_scope(proxy.settings):
    layout=commentary_layout(proxy,size[0]);draw_commentary(record,proxy,layout=layout)
  finally:record.end()
  return dict(signature=self._commentary_content_signature(),model=proxy,picture=picture,size=size,height=layout['height'],layout=layout)
 def _commentary_visibility_active(self,now=None):
  motion=getattr(self,'_commentary_motion',None)
  return bool(motion and motion.active(time.monotonic() if now is None else now))
 def _commentary_visibility_visible(self,now=None):
  if self.closing:return False
  motion=getattr(self,'_commentary_motion',None)
  if motion is None:return False
  return motion.target or motion.frame(time.monotonic() if now is None else now).width>1e-9
 def _commentary_begin_entry(self,motion,now):
  motion.cue(True,now);self._commentary_motion_running=True;self._commentary_hold_pending=True
  self.commentary_deadline=None;self.commentary_timer.stop();self.timer.start()
 def _sync_commentary_visibility(self,now=None):
  from commentary_motion import CommentaryTransition
  self._sync_commentary_geometry()
  now=time.monotonic() if now is None else now
  motion=getattr(self,'_commentary_motion',None)
  if motion is None:self._commentary_motion=motion=CommentaryTransition()
  if not self._commentary_wanted():
   self._commentary_pending=None;self._commentary_hold_pending=False
   self.commentary_deadline=None;self.commentary_timer.stop()
   if motion.cue(False,now):self._commentary_motion_running=True;self.timer.start()
   return
  data=getattr(self,'_commentary_data',None);signature=self._commentary_content_signature()
  if data is None or (not motion.target and motion.frame(now).width<=1e-9):
   self._commentary_data=self._capture_commentary();self._commentary_pending=None;self._commentary_begin_entry(motion,now)
  elif signature!=data['signature']:
   pending=getattr(self,'_commentary_pending',None)
   if pending is None or pending['signature']!=signature:self._commentary_pending=self._capture_commentary()
   self._commentary_hold_pending=True;self.commentary_deadline=None;self.commentary_timer.stop()
   if motion.cue(False,now):self._commentary_motion_running=True;self.timer.start()
  elif not motion.target:
   self._commentary_pending=None;self._commentary_begin_entry(motion,now)
 def _advance_commentary_visibility(self,now):
  motion=getattr(self,'_commentary_motion',None)
  if motion is None or not getattr(self,'_commentary_motion_running',False) or motion.active(now):return
  pending=getattr(self,'_commentary_pending',None)
  if not motion.target and pending is not None and self._commentary_wanted():
   self._commentary_data=pending;self._commentary_pending=None;self._commentary_begin_entry(motion,now)
  else:
   self._commentary_motion_running=False
   if motion.target and getattr(self,'_commentary_hold_pending',False):
    self._commentary_hold_pending=False;self.reset_commentary_timer()
  self.on_display()
 def _paint_commentary(self,p,now=None):
  from broadcast_artwork import draw_commentary
  from commentary_motion import SHOWN
  motion=getattr(self,'_commentary_motion',None);data=getattr(self,'_commentary_data',None)
  if motion is None or data is None:return
  frame=motion.frame(time.monotonic() if now is None else now)
  if frame==SHOWN:p.drawPicture(0,0,data['picture'])
  else:draw_commentary(p,data['model'],frame=frame,layout=data['layout'])
 def _sidebar_visibility_active(self,now=None):
  motion=getattr(self,'_sidebar_motion',None)
  return bool(motion and motion.active(time.monotonic() if now is None else now))
 def _sync_sidebar_timeout(self,now=None):
  """Only a new visible sidebar or changed duration starts a fresh hold."""
  now=time.monotonic() if now is None else now
  seconds=self.model.state.get('broadcast',{}).get('config',{}).get('sidebar_duration',15)
  configured=self.model.settings.get('visible',{})
  wanted=bool(not self.closing and self.model.settings.get('show_overlays',True) and configured.get('qualification',self.enabled.get('qualification',False)) and self.settlement_window()!='qualification' and not (getattr(self,'postmatch',None) and self.postmatch.active))
  signature=((getattr(self.model,'event',None) or {}).get('id'),self.sidebar_mode(),seconds) if wanted else None
  if signature!=self._sidebar_timeout_signature:
   self.sidebar_hide_timer.stop()
   self._sidebar_timeout_signature=signature
   self.sidebar_hide_deadline=now+seconds if wanted and seconds else None
  if self.sidebar_hide_deadline is None:self.sidebar_hide_timer.stop()
  elif not self.sidebar_hide_timer.isActive():self.sidebar_hide_timer.start(max(1,math.ceil((self.sidebar_hide_deadline-now)*1000)))
 def _restart_sidebar_timeout(self,now=None):
  self._sidebar_timeout_signature=None;self.sidebar_hide_timer.stop();self._sync_sidebar_timeout(now)
 def _expire_sidebar_timeout(self):
  if self.closing or self.sidebar_hide_deadline is None:return
  if time.monotonic()<self.sidebar_hide_deadline:
   self.sidebar_hide_timer.start(max(1,math.ceil((self.sidebar_hide_deadline-time.monotonic())*1000)));return
  try:self.set_extension_visible('qualification',False)
  except Exception:
   # A failed durable hide keeps the current live view and can be retried.
   self.sidebar_hide_timer.start(1000);raise
 def _sync_sidebar_visibility(self,now=None):
  from sidebar_animation import SidebarTransition
  now=time.monotonic() if now is None else now
  self._sync_sidebar_timeout(now)
  context=self.sidebar_mode() in ('team','individual') and self.settlement_window()!='qualification'
  self._sidebar_motion_context=context
  if not context:
   self._sidebar_motion=None;self._sidebar_motion_running=False;self._sidebar_exit_page=None;return
  motion=getattr(self,'_sidebar_motion',None)
  if motion is None:self._sidebar_motion=motion=SidebarTransition()
  configured=self.model.settings.get('visible',{})
  wanted=bool(not self.closing and self.model.settings.get('show_overlays',True) and configured.get('qualification',self.enabled.get('qualification',False)) and not (getattr(self,'postmatch',None) and self.postmatch.active))
  if wanted:self._sidebar_exit_page=None
  identity=((getattr(self.model,'event',None) or {}).get('id'),self.sidebar_mode())
  changed=identity!=getattr(self,'_sidebar_motion_identity',identity);self._sidebar_motion_identity=identity
  if motion.cue(wanted,now):self._sidebar_motion_running=True;self.timer.start()
  elif wanted and changed and not motion.active(now):
   motion.cue(True,now,restart=True);self._sidebar_motion_running=True;self.timer.start()
 def _sidebar_visibility_visible(self,now=None):
  if self.closing:return False
  motion=getattr(self,'_sidebar_motion',None)
  if motion is None:return False
  frame=motion.frame(time.monotonic() if now is None else now)
  return motion.target or (frame.width>1e-6 and frame.opacity>1e-6)
 def _advance_sidebar_visibility(self,now):
  if getattr(self,'_sidebar_motion_running',False) and not self._sidebar_visibility_active(now):
   self._sidebar_motion_running=False
   if not self._sidebar_visibility_visible(now):self._sidebar_exit_page=None
   self.on_display()
 def _restart_sidebar_entry(self,now=None):
  now=time.monotonic() if now is None else now
  self._sync_sidebar_visibility(now)
  motion=getattr(self,'_sidebar_motion',None)
  if motion and motion.target and not motion.active(now):
   motion.cue(True,now,restart=True);self._sidebar_motion_running=True;self.timer.start()
 def _sidebar_picture(self,page):
  from broadcast_artwork import draw_team_sidebar
  from typography import overlay_scope
  key=('qualification','sidebar_page',page)
  if key not in self.extension_cache:self.extension_cache[key]=self._extension_picture('qualification',sidebar_render_model(self.model,page))
  layers_key=('qualification','sidebar_layers',page)
  if layers_key not in self.extension_cache:
   proxy=sidebar_render_model(self.model,page);layers={}
   for layer in ('all','background','highlight','foreground'):
    image=QImage(*self.base_size('qualification'),QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
    painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing);painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    try:
     with overlay_scope(self.model.settings):draw_team_sidebar(painter,proxy,mode=self.sidebar_mode(),layer=layer)
    finally:painter.end()
    layers[layer]=image
   self.extension_cache[layers_key]=layers
  return self.extension_cache[key]
 def current_sidebar_page(self,now=None):
  """A static export chooses one whole current page, never a blended frame."""
  if self._sidebar_transition:
   started,old,new=self._sidebar_transition
   return old if ((time.monotonic() if now is None else now)-started)<.25 else new
  return self._sidebar_page
 def _compose_sidebar_layers(self,layers,foreground,highlight):
  from PySide6.QtGui import QPainterPath
  image=QImage(layers['background'].size(),QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
  p=QPainter(image)
  try:
   p.drawImage(0,0,layers['background'])
   if highlight>0:
    mask=QPainterPath();x,width,height=(0.,400.,52.) if self.sidebar_mode()=='team' else (8.,384.,49.)
    skew=height*.45;edge=-skew+(width+skew)*highlight
    for row in range(10):
     y=86.+row*52.;mask.moveTo(x,y);mask.lineTo(x+max(0.,edge+skew),y);mask.lineTo(x+max(0.,edge),y+height);mask.lineTo(x,y+height);mask.closeSubpath()
    p.save();p.setClipPath(mask,Qt.ClipOperation.IntersectClip);p.drawImage(0,0,layers['highlight']);p.restore()
   if foreground>0:
    p.setOpacity(foreground);p.drawImage(0,0,layers['foreground'])
  finally:p.end()
  return image
 def _sidebar_page_frame(self,now):
  transition=self._sidebar_transition
  if transition is None:page=self._sidebar_page;alpha=1.;offset=0.
  else:
   started,old,new=transition;amount=max(0.,min(1.,(now-started)/.5))
   # Retain the existing single-page change; entry/exit does not reset its data.
   if amount<.5:page=old;alpha=1-ease(amount*2);offset=-10*(1-alpha)
   else:page=new;alpha=ease((amount-.5)*2);offset=10*(1-alpha)
  return page,alpha,offset
 def _paint_sidebar_pages(self,p,now=None):
  now=time.monotonic() if now is None else now
  from sidebar_animation import SHOWN
  motion=getattr(self,'_sidebar_motion',None);frame=motion.frame(now) if motion else SHOWN
  frozen=getattr(self,'_sidebar_exit_page',None)
  page,alpha,offset=frozen if frozen is not None and motion and not motion.target else self._sidebar_page_frame(now)
  picture=self._sidebar_picture(page)
  if frame.width<=0 or frame.opacity<=0:return
  p.save();p.setClipRect(QRectF(0,0,400*frame.width,self.base_size('qualification')[1]),Qt.ClipOperation.IntersectClip);p.setOpacity(p.opacity()*alpha*frame.opacity);p.translate(0,offset)
  if frame==SHOWN:p.drawPicture(0,0,picture)
  else:
   layers=self.extension_cache[('qualification','sidebar_layers',page)]
   if motion and not motion.target:
    # Fade a single composed frame so rows, text and their translucent bases
    # retain their original relative contrast during the short exit.
    if frame.foreground==1. and frame.highlight==1.:image=layers['all']
    else:
     key=('qualification','sidebar_exit',page,frame.foreground,frame.highlight)
     if key not in self.extension_cache:self.extension_cache[key]=self._compose_sidebar_layers(layers,frame.foreground,frame.highlight)
     image=self.extension_cache[key]
   else:
    image=self._compose_sidebar_layers(layers,frame.foreground,frame.highlight)
   p.drawImage(0,0,image)
  p.restore()
 def _sync_sidebar_paging(self,now=None):
  now=time.monotonic() if now is None else now
  config=self.model.state.get('broadcast',{}).get('config',{});mode=self.sidebar_mode();event=getattr(self.model,'event',None) or {}
  roster=self.model.state.get('broadcast',{}).get('roster',[])
  signature=(event.get('id'),event.get('kind'),mode,config.get('sidebar_scope','all'),config.get('sidebar_auto_page',True),config.get('sidebar_page_size',10),config.get('sidebar_page_interval',10),config.get('sidebar_page',0),
             tuple((row.get('id'),row.get('name'),row.get('team_id'),row.get('team')) for row in roster),tuple(row.get('id') for row in self.model.state.get('players',[])))
  data=sidebar_page_data(self.model,mode=mode) if mode in ('team','individual') else dict(pages=1,scope='all',page=0)
  was_active=self._sidebar_auto_active;changed=signature!=self._sidebar_signature
  self._sidebar_pages=data['pages'];self._sidebar_interval=float(config.get('sidebar_page_interval',10))
  active=bool(not self.closing and self.model.settings.get('show_overlays',True) and self.enabled.get('qualification',False) and mode in ('team','individual') and self.settlement_window()!='qualification' and data['scope']=='all' and config.get('sidebar_auto_page',True) and self._sidebar_pages>1)
  if changed:
   self._sidebar_page=0 if config.get('sidebar_auto_page',True) else data['page'];self._sidebar_transition=None;self._sidebar_deadline=None;self._sidebar_signature=signature;self._sidebar_paused_remaining=None
  self._sidebar_page%=self._sidebar_pages;self._sidebar_auto_active=active
  if not active:
   if was_active:
    self._sidebar_page=self.current_sidebar_page(now)
    self._sidebar_paused_remaining=max(0.,self._sidebar_deadline-now) if self._sidebar_deadline is not None else self._sidebar_interval
   self._sidebar_transition=None;self._sidebar_deadline=None;self.sidebar_timer.stop();return
  if changed or not was_active:
   remaining=getattr(self,'_sidebar_paused_remaining',None)
   self._sidebar_transition=None;self._sidebar_deadline=now+(self._sidebar_interval if changed or remaining is None else remaining);self._sidebar_paused_remaining=None
  if self._sidebar_transition is None:
   if self._sidebar_deadline is None:self._sidebar_deadline=now+self._sidebar_interval
   self.sidebar_timer.start(max(1,math.ceil((self._sidebar_deadline-now)*1000)))
 def _advance_sidebar_paging(self,now):
  if not self._sidebar_auto_active:return
  if self._sidebar_transition:
   started,old,new=self._sidebar_transition
   if now-started>=.5:
    self._sidebar_page=new;self._sidebar_transition=None;self._sidebar_deadline=now+self._sidebar_interval
    self.sidebar_timer.start(max(1,round(self._sidebar_interval*1000)))
  elif self._sidebar_deadline is not None and now>=self._sidebar_deadline:
   self._sidebar_transition=(now,self._sidebar_page,(self._sidebar_page+1)%self._sidebar_pages);self._sidebar_deadline=None;self.sidebar_timer.stop();self.timer.start()
 def _wake_sidebar_page(self):
  if self.closing:return
  now=time.monotonic()
  if self._sidebar_auto_active and self._sidebar_deadline is not None and now<self._sidebar_deadline:
   self.sidebar_timer.start(max(1,math.ceil((self._sidebar_deadline-now)*1000)));return
  self.tick()
 def _paint_standings_picture(self,p,picture,now=None):
  transition=self._standings_change
  progress=1. if transition is None else ease(((time.monotonic() if now is None else now)-transition[0])/.28)
  if transition and progress<1:
   # Premultiplied addition keeps shared card backgrounds solid during a fade.
   frame=QImage(*EXTRA_SIZES['standings'],QImage.Format.Format_ARGB32_Premultiplied);frame.fill(Qt.GlobalColor.transparent)
   blend=QPainter(frame)
   try:
    weight=round(progress*255)
    # Qt's raster opacity is 8-bit fixed point. Complementary integer weights
    # avoid a two-unit alpha dip from independently rounded halves.
    blend.setOpacity((256-weight)/256);blend.drawImage(0,0,transition[1])
    blend.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    blend.setOpacity((weight+1)/256);blend.drawPicture(0,0,picture)
   finally:blend.end()
   p.drawImage(0,0,frame)
  else:p.drawPicture(0,0,picture)
 def _start_standings_change(self,before):
  proxy=SimpleNamespace(state=before,settings=self.model.settings,path=self.model.path)
  snapshot=QImage(*EXTRA_SIZES['standings'],QImage.Format.Format_ARGB32_Premultiplied);snapshot.fill(Qt.GlobalColor.transparent)
  record=QPainter(snapshot)
  try:self._paint_standings_picture(record,self._extension_picture('standings',proxy))
  finally:record.end()
  self._standings_change=(time.monotonic(),snapshot);self.timer.start()
 def paint_content(self,p,kind,index=0):
  """Native and exported-frame animation policy, after window/base scaling."""
  programme=getattr(self,'programme',None)
  if programme is not None and programme.paused and programme.frozen_kind==kind and programme.frozen_frame is not None:
   p.drawImage(0,0,programme.frozen_frame);return
  p.save()
  if kind==self.settlement_window():
   alpha=1. if self.stage=='settlement_final' else ease(self.age()/.25)
   p.setOpacity(p.opacity()*alpha)
  elif kind=='yaku':
   alpha=self.yaku_opacity();p.setOpacity(p.opacity()*alpha);p.translate(0,24*(1-alpha))
  elif kind=='player':
   alpha=self.player_opacity();p.setOpacity(p.opacity()*alpha*self.bottom_player_opacity());p.translate(0,28*(1-alpha))
  elif kind=='standings':
   # Mode changes only crossfade. Win/return stages keep their existing timing.
   alpha=self.player_opacity();p.setOpacity(p.opacity()*alpha*self.extension_opacity('standings'));p.translate(0,28*(1-alpha))
  elif kind=='qualification' and self.sidebar_mode() in ('team','individual'):
   # Ranking sidebars own their fixed-position, layered reference transition.
   pass
  elif kind=='commentary':pass
  elif kind in EXTRA_SIZES:
   alpha=self.extension_opacity(kind);p.setOpacity(p.opacity()*alpha);p.translate(0,8*(1-alpha))
  if kind=='player' and self._final_card_change and self.stage=='table':self._paint_final_card_change(p,index)
  else:self.draw_content(p,kind,index)
  p.restore()
 def draw_content(self,p,kind,index=0,static=False):
  if kind=='riichi_effect':
   if self.riichi_queue.active:draw_riichi(p,self.riichi_queue.active,self.riichi_queue.elapsed())
   return
  if kind=='table_scores' and is_team_event(self.model):return
  if kind in EXTRA_SIZES:
   layout=self.standings_layout() if kind=='standings' else None
   if kind==self.settlement_window():
    draw_extension(p,kind,self.model,layout=layout,progress=1. if static else self.settlement_progress(),settlement=self.settlement_data,settlement_stage='hand' if self.stage=='settlement_hand' else 'total')
   elif kind=='qualification' and self.sidebar_mode() in ('team','individual'):
    if static:draw_extension(p,kind,sidebar_render_model(self.model,self.current_sidebar_page()))
    else:self._paint_sidebar_pages(p)
   elif kind=='commentary':
    if static:draw_extension(p,kind,self.model,canvas_size=self.base_size(kind))
    else:self._paint_commentary(p)
   elif static:draw_extension(p,kind,self.model,layout=layout)
   elif kind=='chart':
    draw_extension(p,kind,self.model,progress=self.chart_progress(),flow_time=max(0.,time.monotonic()-self.chart_flow_started))
   else:
    picture=self._extension_picture(kind)
    if kind=='standings':self._paint_standings_picture(p,picture)
    else:p.drawPicture(0,0,picture)
  else:
   p.save();p.setClipRect(QRectF(0,0,*self.base_size(kind)))
   if kind in ('round','dora') and not self.model.state.get('broadcast',{}).get('config',{}).get('show_match_title',True):p.translate(0,-ROUND_HEADER)
   art=Artwork(self.model,Animation()) if static else self.art;art.draw(p,kind,index,show_dora=self.dora_visible())
   if kind=='round' and self.dora_visible():
    p.translate(SIZES['round'][0],0);art.draw(p,'dora')
   p.restore()
 def _migrate_hud_defaults(self,screen):
  """Shrink exact old paired defaults; a customized member preserves the pair."""
  saved=self.model.settings['windows'];pair=(saved.get('round'),saved.get('dora'))
  scale=min(1.,screen.width()/1920)
  candidates=[]
  for height in (SIZES['round'][1],SIZES['round'][1]-ROUND_HEADER):
   rw=round(SIZES['round'][0]*scale);rh=round(height*scale)
   candidates.append(([screen.x()+round(40*scale),screen.y()+8,rw,rh],
                      [screen.x()+round(40*scale)+rw,screen.y()+8,round(SIZES['dora'][0]*scale),rh]))
   candidates.append(([40,8,384,height],[424,8,344,height]))
   candidates.append(([20,12,240,round(height*.625)],[260,12,215,round(height*.625)]))
  for old_round,old_dora in candidates:
   if self._same_geometry(pair[0],old_round) and self._same_geometry(pair[1],old_dora):
    x,y,w,h=old_round;new_width=round(w*.85);new_height=round(h*.85)
    saved['round']=[x,y,new_width,new_height]
    saved['dora']=[x+new_width,y,round(old_dora[2]*.85),new_height]
    break
  self.model.settings['hud_layout_version']=1
 def restore_geometry(self):
  screen=QApplication.primaryScreen().availableGeometry();width=min(438,max(220,int((screen.width()-70)/4)));height=round(width*self.base_size('player')[1]/438);win_width=max(400,screen.width()-80);win_height=round(win_width*SIZES['yaku'][1]/SIZES['yaku'][0]);hud_scale=min(1.,screen.width()/1920)
  self._migrate_hud_defaults(screen)
  saved=self.model.settings['windows']
  if self.model.settings.get('hud_group_version',0)<1 or 'dora' in saved:
   old=saved.get('round')
   if old:
    x,y,w,h=old;saved['round']=[x,y,round(w*(SIZES['round'][0]+SIZES['dora'][0])/SIZES['round'][0]),h]
   saved.pop('dora',None);self.model.settings['hud_group_version']=1
  hud_scale*=.85
  defaults={'round':(screen.x()+round(40*min(1.,screen.width()/1920)),screen.y()+8,round(self.base_size('round')[0]*hud_scale),round(self.base_size('round')[1]*hud_scale)),'yaku':(screen.x()+40,screen.bottom()-win_height-20,win_width,win_height)}
  defaults.update({f'player{i}':(screen.x()+15+i*(width+10),screen.bottom()-height-30,width,height) for i in range(4)})
  # New windows share a 1920x1080 reference canvas. Preserve custom positions.
  factor=min(screen.width()/1920,screen.height()/1080)
  places={'standings':(40,900),
          'statistics':(160,140),'chart':(160,90)}
  for key,(x,y) in places.items():
   bw,bh=EXTRA_SIZES[key];size_factor=factor*(.8 if key=='qualification' else 1.);defaults[key]=(screen.x()+x*factor,screen.y()+y*factor,bw*size_factor,bh*size_factor)
  defaults['qualification']=self._qualification_default(screen)
  layouts=self.model.settings.setdefault('qualification_geometries',{})
  defaults['table_scores']=self._table_scores_default(screen,defaults['round'])
  cw=round(screen.width()*900/1920);ch=round(cw*EXTRA_SIZES['commentary'][1]/EXTRA_SIZES['commentary'][0])
  defaults['commentary']=(screen.x()+(screen.width()-cw)/2,screen.y()+24*screen.width()/1920,cw,ch)
  bw,bh=EXTRA_SIZES['corner_header'];defaults['corner_header']=(screen.right()-(bw+30)*factor,screen.y()+24*factor,bw*factor,bh*factor)
  defaults['riichi_effect']=(screen.x(),screen.y(),RIICHI_SIZE[0]*factor,RIICHI_SIZE[1]*factor)
  legacy_defaults=defaults;defaults=self._canvas_defaults()
  version=self.model.settings.get('broadcast_canvas_version',0)
  player_keys=[f'player{i}' for i in range(4)]
  players=restore_player_geometries(saved,legacy_defaults,defaults,version)
  # Upgrade only the untouched old 494px row. A moved/resized group is an
  # explicit layout and keeps its four saved coordinates together.
  if version==2:
   canvas=reference_canvas();factor=canvas.width()/1920
   old={f'player{i}':[round(canvas.x()+i*494*factor),round(canvas.y()+710*factor),round(438*factor),round(340*factor)] for i in range(4)}
   if all(self._same_geometry(players[key],old[key]) for key in player_keys):
    players={key:[round(value) for value in defaults[key]] for key in player_keys}
  if version<4:
   canvas=reference_canvas();factor=canvas.width()/1920
   previous={f'player{i}':[round(canvas.x()+(101+i*434)*factor),round(canvas.y()+710*factor),round(438*factor),round(340*factor)] for i in range(4)}
   if all(self._same_geometry(players[key],previous[key]) for key in player_keys):
    players={key:[round(value) for value in defaults[key]] for key in player_keys}
   for key in player_keys:
    geometry=players[key]
    if abs(geometry[3]-round(geometry[2]*340/438))<=1:
     geometry[3]=round(geometry[2]*self.base_size('player')[1]/438)
  if version==4:
   canvas=reference_canvas();factor=canvas.width()/1920
   previous={f'player{i}':[round(canvas.x()+(140+i*408)*factor),round(canvas.y()+710*factor),round(438*factor),round(348*factor)] for i in range(4)}
   if all(self._same_geometry(players[key],previous[key]) for key in player_keys):
    players={key:[round(value) for value in defaults[key]] for key in player_keys}
  if 5<=version<8:
   canvas=reference_canvas();factor=canvas.width()/1920
   previous={f'player{i}':[round(canvas.x()+(104+i*432)*factor),round(canvas.y()+710*factor),round(438*factor),round(348*factor)] for i in range(4)}
   if all(self._same_geometry(players[key],previous[key]) for key in player_keys):
    players={key:[round(value) for value in defaults[key]] for key in player_keys}
  if version<7:
   canvas=reference_canvas();factor=canvas.width()/1920
   previous={f'player{i}':[round(canvas.x()+(32+i*468)*factor),round(canvas.y()+710*factor),round(438*factor),round(348*factor)] for i in range(4)}
   if all(self._same_geometry(players[key],previous[key]) for key in player_keys):
    players={key:[round(value) for value in defaults[key]] for key in player_keys}
  if version<8:
   # Bundled demos used a smaller row, with both the old and corrected seat heights.
   height=players['player0'][3]
   if height in (237,242) and all(self._same_geometry(players[key],(10+317*i,460,305,height))
                                for i,key in enumerate(player_keys)):
    players={key:[round(value) for value in defaults[key]] for key in player_keys}
  if version<6 and all(self._same_geometry(players[key],defaults[key]) for key in player_keys):
   if self.model.settings.get('player_side_padding',6.)==6.:self.model.settings['player_side_padding']=2.3
  if version<1:
   for key,geometry in list(saved.items()):
    if key not in player_keys and key in legacy_defaults and self._same_geometry(geometry,[round(value) for value in legacy_defaults[key]]):saved.pop(key,None)
  saved.update(players)
  self.model.settings['broadcast_canvas_version']=max(8,version)
  from qualification_presentation import fixed_sidebar_geometry
  saved['qualification']=fixed_sidebar_geometry(self.model.settings,defaults['qualification'])
  config=self.model.state.get('broadcast',{}).get('config',{})
  if self.model.settings.get('sidebar_fixed_canvas_version',0)<1 and config.get('sidebar_mode','qualification')=='qualification' and config.get('qualification_layout','four')=='four':
   self.enabled['qualification']=False;self.model.settings.setdefault('visible',{})['qualification']=False
  self.model.settings['sidebar_fixed_canvas_version']=1
  from win_layout import restore_win_geometry
  canvas=reference_canvas();factor=canvas.width()/1920
  legacy_win=(canvas.x()+40*factor,canvas.y()+840*factor,1840*factor,220*factor)
  saved['yaku']=restore_win_geometry(saved.get('yaku'),defaults['yaku'],legacy_win,self.model.settings.get('win_banner_canvas_version',0))
  self.model.settings['win_banner_canvas_version']=2
  saved_commentary=self.model.settings['windows'].get('commentary')
  for key,w in self.windows.items():
   w.base=self.base_size(w.kind)
   w.apply_topmost(self.model.settings.get('topmost',False));x,y,ww,hh=self.model.settings['windows'].get(key,defaults[key])
   min_width,min_height=(1,1) if key=='commentary' else (120,30)
   ww=max(min_width,min(4000,ww));hh=max(min_height,min(3000,hh))
   if key not in player_keys and key in self.model.settings['windows'] and not any(s.availableGeometry().intersects(QRectF(x,y,ww,hh).toRect()) for s in QApplication.screens()):
    x,y=defaults[key][:2]
   w.setGeometry(round(x),round(y),round(ww),round(hh))
  bounds=self.player_capture_bounds().toAlignedRect()
  if not any(screen.availableGeometry().intersects(bounds) for screen in QApplication.screens()):
   dx,dy=round(defaults['player0'][0])-bounds.x(),round(defaults['player0'][1])-bounds.y()
   for key in player_keys:self.windows[key].move(self.windows[key].x()+dx,self.windows[key].y()+dy)
  saved.update({key:self._geometry_list(self.windows[key]) for key in player_keys})
  self._restore_commentary_layout(saved_commentary,reference_canvas())
  layouts['sidebar']=self._geometry_list(self.windows['qualification'])
  self.model.settings['windows']['qualification']=layouts['sidebar'][:]
  self.model.settings['qualification_sidebar_base_height']=self.base_size('qualification')[1]
  self.model.settings['qualification_geometry_mode']='sidebar'
  self._sync_bottom_geometry(force=True)
  if hasattr(self,'fullscreen_capture'):self.fullscreen_capture.restore_geometry()
  for window in getattr(self,'split_programme_windows',{}).values():window.restore_geometry()
  for monitor in getattr(self,'_capture_dpi_monitors',[]):monitor.rebase()
 def _canvas_defaults(self):
  canvas=reference_canvas();factor=canvas.width()/1920
  x,y=canvas.x(),canvas.y()
  values={'round':(x+40*factor,y+8*factor,self.base_size('round')[0]*.85*factor,self.base_size('round')[1]*.85*factor)}
  player_width=438*factor;player_height=self.base_size('player')[1]*factor
  step=468
  origin=(PLAYER_CAPTURE_SIZE[0]-3*step)/2-PLAYER_LAYOUT.body_bounds().center().x()
  values.update({f'player{i}':(x+(origin+i*step)*factor,y+710*factor,player_width,player_height) for i in range(4)})
  width=1920*factor
  values['yaku']=(x,y+(1060-1920*SIZES['yaku'][1]/SIZES['yaku'][0])*factor,width,width*SIZES['yaku'][1]/SIZES['yaku'][0])
  for key,(left,top) in {'standings':(40,900),'statistics':(160,140),'chart':(160,90)}.items():
   width,height=EXTRA_SIZES[key];values[key]=(x+left*factor,y+top*factor,width*factor,height*factor)
  values['qualification']=self._qualification_default(canvas)
  values['table_scores']=self._table_scores_default(canvas,values['round'])
  values['commentary']=(x+510*factor,y+24*factor,900*factor,900*factor*EXTRA_SIZES['commentary'][1]/EXTRA_SIZES['commentary'][0])
  width,height=EXTRA_SIZES['corner_header'];values['corner_header']=(x+(1920-width-30)*factor,y+24*factor,width*factor,height*factor)
  values['riichi_effect']=canvas.getRect()
  values['four_conditions']=canvas.getRect()
  return values
 def _qualification_default(self,screen):
  factor=min(screen.width()/1920,screen.height()/1080)
  height=self.base_size('qualification')[1]
  return [round(screen.x()+40*factor),round(screen.y()+190*factor),round(320*factor),round(height*.8*factor)]
 def _sync_qualification_geometry(self):
  self.windows['qualification'].base=self.base_size('qualification')
 def _table_scores_default(self,screen,hud_geometry=None):
  hud=QRectF(*(hud_geometry or self._geometry_list(self.windows['round'])))
  scale=min(screen.width()/1920,screen.height()/1080);gap=max(4,round(12*scale))
  width=round(640*scale);height=round(width*120/820)
  x=round(hud.right()+gap);right=screen.right()-round(24*scale)
  if x+width>right:x=max(screen.left(),right-width)
  return [round(x),round(hud.y()),width,height]
 def place_table_scores(self):
  screen=QApplication.screenAt(self.windows['round'].geometry().center()) or QApplication.primaryScreen()
  self.display_configuration.geometry_edit(lambda:self.windows['table_scores'].setGeometry(*self._table_scores_default(screen.availableGeometry())))
  return self.windows['table_scores'].geometry()
 def player_group_geometry(self):
  windows=[self.windows[f'player{i}'] for i in range(4)]
  gap=round(sum(windows[i+1].x()-windows[i].x()-windows[i].width() for i in range(3))/3)
  first=windows[0]
  return first.x(),first.y(),first.width(),gap
 def player_group_snapshot(self):
  return {f'player{i}':self._geometry_list(self.windows[f'player{i}']) for i in range(4)}
 def translate_player_group(self,snapshot,delta):
  for key,(x,y,width,height) in snapshot.items():self.windows[key].move(x+delta.x(),y+delta.y())
  self._sync_bottom_geometry(force=True)
  if hasattr(self,'players_capture'):self.players_capture.sync_geometry();self.players_capture.update()
 def set_player_group(self,x,y,width,gap,settings_patch=None):
  width=max(120,min(2400,round(width)));gap=max(-round(width*.15),min(1000,round(gap)))
  def arrange():
   for index in range(4):
    window=self.windows[f'player{index}'];window.setGeometry(round(x+index*(width+gap)),round(y),width,round(width*window.base[1]/window.base[0]))
  self.display_configuration.geometry_edit(arrange,settings_patch=settings_patch)
  self.extension_cache.clear()
 def scale_player_group(self,factor):
  snapshot=self.player_group_snapshot();anchor=snapshot['player0']
  smallest=min(g[2] for g in snapshot.values());largest=max(g[2] for g in snapshot.values())
  factor=max(120/smallest,min(2400/largest,factor))
  def scale():
   for key,(x,y,width,height) in snapshot.items():
    self.windows[key].setGeometry(round(anchor[0]+(x-anchor[0])*factor),round(anchor[1]+(y-anchor[1])*factor),round(width*factor),round(height*factor))
  self.display_configuration.geometry_edit(scale,notify=False);self.extension_cache.clear()
 def reset_geometry(self,persist=True):
  def reset():
   self.model.settings['windows']={};self.model.settings.pop('qualification_geometries',None);self.model.settings.pop('qualification_geometry_mode',None);self.restore_geometry()
  if persist:self.display_configuration.geometry_edit(reset)
  else:reset();self.save_geometry(persist=False)
 def accept_geometry(self):
  self.display_configuration.accept_geometry()
 def save_geometry(self,persist=True,geometry_overrides=None):
  return self.display_configuration.save_geometry(persist,geometry_overrides)
 def _collect_geometry_settings(self):
  original=self.model.settings;self.model.settings=copy.deepcopy(original)
  try:return self._collect_geometry_settings_draft()
  finally:self.model.settings=original
 def _collect_geometry_settings_draft(self):
  self._sync_commentary_geometry()
  self._sync_bottom_geometry()
  programme_geometries={key:self.model.settings['windows'][key] for key in ('postmatch','opening') if key in self.model.settings['windows']}
  self.model.settings['windows']={k:[w.x(),w.y(),w.width(),w.height()] for k,w in self.windows.items()}
  self.model.settings['windows'].update(programme_geometries)
  if hasattr(self,'fullscreen_capture'):self.model.settings['windows']['fullscreen']=self._geometry_list(self.fullscreen_capture)
  self.model.settings.setdefault('qualification_geometries',{})['sidebar']=self._geometry_list(self.windows['qualification'])
  self.model.settings['qualification_geometry_mode']='sidebar'
  self.model.settings['qualification_sidebar_base_height']=self.windows['qualification'].base[1]
  if self._settlement_geometry is not None:self.model.settings['windows']['standings']=list(self._settlement_geometry)
  return self.model.settings
 def _commentary_screen(self):
  screen=QApplication.screenAt(self.windows['round'].geometry().center()) or QApplication.primaryScreen()
  return QRectF(reference_canvas(screen))
 def commentary_geometry(self,reference_rect=None):
  """Center native-size text in the space between the HUD and visible logo."""
  from capture_surface import screen_pixel_ratio
  screen=QRectF(reference_rect) if reference_rect is not None else self._commentary_screen()
  scale=screen.width()/1920;gap=max(4.,12*scale);margin=max(4.,24*scale)
  preferred=max(1,round(900*scale));left=math.ceil(screen.left()+margin);right=math.floor(screen.right()-margin)
  hud=QRectF(self.windows['round'].geometry())
  logo=self.windows.get('corner_header')
  if logo is not None and self.enabled.get('corner_header',False) and logo.geometry().left()>hud.right():
   right=min(right,math.floor(logo.geometry().left()-gap))
  start=math.ceil(max(hud.right()+gap,left));available=right-start
  if available<40 and hud.left()-gap-left>=40:
   # A manually moved HUD can leave its usable space to the left instead.
   edge=math.floor(min(right,hud.left()-gap));width=min(preferred,edge-left)
   x=left+(edge-left-width)//2
  else:
   width=max(1,min(preferred,available))
   x=max(math.ceil(screen.left()),min(start+max(0,(available-width)//2),math.floor(screen.right())-width))
  ratio=screen_pixel_ratio(self.windows.get('commentary',self.windows['round']))
  native_height=self._commentary_native_height(round(width*ratio))
  data=getattr(self,'_commentary_data',None)
  if data is not None and self._commentary_visibility_visible():native_height=max(native_height,data['height'])
  height=max(1,math.ceil(native_height/ratio))
  y=round(max(screen.top(),min(hud.top(),screen.bottom()-height)))
  if self.enabled.get('table_scores',False) and not is_team_event(self.model):
   scores=QRectF(self.windows['table_scores'].geometry())
   if scores.intersects(QRectF(x,y,width,height)):
    y=round(max(hud.bottom(),scores.bottom())+gap)
    y=round(min(y,screen.bottom()-height))
  return QRectF(x,y,width,height).toRect()
 @staticmethod
 def _same_geometry(first,second):
  try:
   if first is None or second is None or len(first)!=4 or len(second)!=4:return False
   return all(round(float(a))==round(float(b)) for a,b in zip(first,second))
  except (TypeError,ValueError,OverflowError):return False
 @staticmethod
 def _geometry_list(window):
  return [window.x(),window.y(),window.width(),window.height()]
 def _restore_commentary_layout(self,saved,screen):
  factor=min(screen.width()/1920,screen.height()/1080)
  old_default=[screen.x()+790*factor,screen.y()+8*factor,1100*factor,142*factor]
  old_positions=[old_default,[790,8,1100,142],[490,12,770,99]]
  if self.model.settings.get('commentary_layout_version',0)<6:old_positions.append([screen.x()+510*factor,screen.y()+24*factor,900*factor,108*factor])
  marker=self.model.settings.get('commentary_auto_geometry')
  auto=saved is None or any(self._same_geometry(saved,value) for value in old_positions)
  auto=auto or (self.model.settings.get('commentary_auto',False) and self._same_geometry(saved,marker))
  self._commentary_reference=None;self._commentary_auto=bool(auto)
  self.model.settings['commentary_auto']=bool(auto)
  self.model.settings['commentary_layout_version']=6
  self._commentary_auto_geometry=None
  self._sync_commentary_geometry(force=True)
 def _sync_commentary_geometry(self,force=False):
  from capture_surface import screen_pixel_ratio
  window=self.windows['commentary']
  current=self._geometry_list(window)
  if self._commentary_auto and not force and self._commentary_auto_geometry is not None and current!=self._commentary_auto_geometry:
   # A direct drag, wheel resize or layout-editor change opts out of auto placement.
   self._commentary_auto=False;self.model.settings['commentary_auto']=False
  if self._commentary_auto:
   geometry=self.commentary_geometry(self._commentary_reference)
   if window.geometry()!=geometry:window.setGeometry(geometry)
   self._commentary_auto_geometry=self._geometry_list(window)
   self.model.settings['commentary_auto']=True
   self.model.settings['commentary_auto_geometry']=self._commentary_auto_geometry[:]
   self.model.settings.setdefault('windows',{})['commentary']=self._commentary_auto_geometry[:]
  else:
   ratio=screen_pixel_ratio(window);height=math.ceil(self._commentary_native_height(round(window.width()*ratio))/ratio)
   if window.height()<height:window.resize(window.width(),height)
  window.base=self.base_size('commentary')
 def center_commentary(self,reference_rect=None):
  """Explicitly place beside the HUD; optional canvas for reproducible previews."""
  def center():
   self._commentary_reference=QRectF(reference_rect) if reference_rect is not None else None
   self._commentary_auto=True;self._sync_commentary_geometry(force=True)
  self.display_configuration.geometry_edit(center)
  return self.windows['commentary'].geometry()
 def player_card_bounds(self,index):
  """Stable visible card envelope, excluding the upper transparent/meld area."""
  window=self.windows[f'player{index}']
  width,height=window.base
  theme=self.model.settings.get('theme','mleague');envelope=PLAYER_LAYOUT.envelope(theme)
  if theme!='saikouisen':envelope=PLAYER_LAYOUT.body_transform().mapRect(envelope)
  sx,sy=window.width()/width,window.height()/height
  return QRectF(window.x()+envelope.x()*sx,window.y()+envelope.y()*sy,envelope.width()*sx,envelope.height()*sy)
 def bottom_bounds(self):
  bounds=self.player_card_bounds(0)
  for index in range(1,4):bounds=bounds.united(self.player_card_bounds(index))
  return bounds
 def _sync_bottom_geometry(self,force=False):
  if 'standings' not in self.windows:return
  players=(self.windows[f'player{i}'] for i in range(4))
  signature=(self.model.settings.get('theme'),tuple((w.x(),w.y(),w.width(),w.height(),w.base) for w in players))
  enabled=self.enabled.get('standings',False) or self.settlement_window()=='standings'
  if enabled and (force or not self._bottom_was_enabled or signature!=self._bottom_player_signature):
   self.windows['standings'].setGeometry(self.bottom_bounds().toAlignedRect())
  self._bottom_player_signature=signature;self._bottom_was_enabled=enabled
 def bottom_mode(self):
  if self.stage in ('settlement_hand','settlement_total','settlement_final'):return 'match_result' if self.stage=='settlement_hand' else 'total_result'
  if self.model.state.get('phase')=='finished':return ('final_pt' if self.model.settings.get('final_points_mode')=='pt' else 'final_scores') if self.model.settings.get('final_cards_visible',False) else 'players'
  if not self.enabled.get('standings',False):return 'players'
  return self.model.state.get('broadcast',{}).get('config',{}).get('standings_mode','individual')
 def set_bottom_mode(self,mode):
  if mode in ('final_scores','final_pt'):return self.set_final_points_mode('pt' if mode=='final_pt' else 'scores')
  if mode in ('match_result','total_result'):
   self._restore_settlement_data()
   if not self._can_settle():raise ValueError('请先记录本场积分，再手动展示赛后结算')
   self._save_display_settings(show_yaku=False)
   self.pending_win=False;self.pending_next=False;self.animation.finish()
   self._begin_settlement(time.monotonic())
   if mode=='total_result':self.set_stage('settlement_total')
   self.extra_until=time.monotonic()+2/self.speed;self.on_display();self.model.display_changed.emit();return True
  if mode=='players' and self.model.state.get('phase')=='finished':
   self._save_display_settings(final_cards_visible=False,show_yaku=False)
   self._final_card_change=None;self.pending_win=False;self.pending_next=False
   self.animation.finish();self.animation.clear_presentation();self.set_stage('table')
   self.on_display();self.model.display_changed.emit();return True
  if mode not in ('players','individual','team'):raise ValueError('底部显示模式无效')
  enabled=self.enabled.copy();enabled['standings']=mode!='players';old=self.extension_opacity('standings');was_enabled=self.enabled.get('standings',False)
  before=self.display_configuration.commit(config={'standings_mode':mode} if mode!='players' else {},patch={'visible':enabled.copy()})
  self.enabled=enabled;self.extension_transitions['standings']=(time.monotonic(),old,float(mode!='players'))
  if mode!='players' and not was_enabled:self._standings_change=None
  self.timer.start();self.display_configuration.publish(before)
 def dora_visible(self):
  return (self.model.state.get('phase')!='finished' or self.animation.active or self.stage in ('players_out','win','win_out','final'))
 def set_final_points_mode(self,mode):
  """Show final score/pt cards without changing payments or event standings."""
  if mode not in ('scores','pt'):raise ValueError('终局显示模式无效')
  if self.model.state.get('phase')!='finished':return False
  old=self.model.settings.get('final_points_mode','scores') if self.model.settings.get('final_cards_visible',False) else 'scores'
  self._save_display_settings(final_points_mode=mode,final_cards_visible=True,show_yaku=False)
  self.animation.finish();self.animation.clear_presentation();self.pending_win=False;self.pending_next=False;self.final_pt_deadline=None;self._auto_final_pending=False
  self.art.win_started=None
  self._clear_settlement();self.extension_cache.clear();self.set_stage('table')
  now=time.monotonic();previous=self._final_card_change
  if old!=mode:
   if self.model.settings.get('theme')=='mleague':
    from final_points_motion import FinalPointsChange
    self._final_card_change=FinalPointsChange(self.art,old,mode,now,self.speed,previous)
   else:self._final_card_change=None
  self.on_display();self.model.display_changed.emit()
  return True
 def _paint_final_card_change(self,p,index):
  self._final_card_change.paint(p,index,time.monotonic())
 def bottom_player_opacity(self,now=None):
  if self.settlement_window():return 0.
  if self.model.state.get('phase')=='finished':return 1.
  transition=self.extension_transitions.get('standings')
  # The opaque original card is the lower layer of a dissolve. Fading both
  # independent windows would darken their shared white panel at the midpoint.
  if transition and transition[1]!=transition[2]:return 1.
  return 0. if self.enabled.get('standings',False) else 1.
 def standings_replacing_players(self):
  return self.bottom_player_opacity()<=.001
 def content_visible(self,key):
  """The same visibility policy is used by native windows and preview tooling."""
  if self._opening_active():return False
  if key=='riichi_effect':return self._riichi_allowed() and self.riichi_queue.busy
  if key=='qualification' and getattr(self,'_sidebar_motion_context',False):
   return self._sidebar_visibility_visible()
  if key=='commentary':return self._commentary_visibility_visible()
  if not self.model.settings.get('show_overlays',True):return False
  if getattr(self,'postmatch',None) and self.postmatch.active:return False
  if key=='table_scores' and is_team_event(self.model):return False
  settlement_window=self.settlement_window()
  if settlement_window:
   if key==settlement_window:return True
   if key.startswith('player') or key in ('standings','yaku'):return False
  if key in EXTRA_SIZES:
   show=self.enabled.get(key,False) or self.extension_opacity(key)>.001
  else:show=self.enabled.get(key,True)
  if key=='yaku':
   return show and bool(self.model.last_info) and self.model.settings['show_yaku'] and self.stage in ('win','win_out','final')
  if key.startswith('player'):
   return show and not self.standings_replacing_players() and self.stage not in ('win','win_out','final')
  if key=='standings':
   if self.model.state.get('phase')=='finished':return False
   return show and self.stage not in ('win','win_out','final')
  return show
 def settlement_window(self):
  if not self._can_settle() or self.stage not in ('settlement_hand','settlement_total','settlement_final'):return None
  return 'standings' if self.stage=='settlement_hand' or self.settlement_data['mode']=='team' else 'qualification'
 def settlement_progress(self,now=None):
  if self.stage=='settlement_final':return 1.
  if self.stage=='settlement_total' and self.settlement_data and self.settlement_data['mode']=='individual':return 1.
  return ease(self.age(now)/.9)
 @staticmethod
 def _committed_match(state):
  return next((match for match in state.get('broadcast',{}).get('matches',[]) if match.get('id')==state.get('id') and match.get('committed')),None)
 @staticmethod
 def _final_archive_signature(archive):
  if archive is None:return None
  fields=('id','name','team','team_id','rank','score','points')
  rows=sorted(archive.get('results',[]),key=lambda row:str(row.get('id','')))
  return archive.get('id'),tuple(tuple(row.get(key) for key in fields) for row in rows)
 def _capture_settlement(self,before,after):
  # Small hands never calculate temporary pt or projected league standings.
  if after.get('phase')!='finished' or self._committed_match(after) is None:return
  mode=after.get('broadcast',{}).get('config',{}).get('event_mode','individual')
  self.settlement_data=final_settlement_summary(after,mode=mode)
  self._last_final_archive=self._final_archive_signature(self._committed_match(after))
 def _restore_settlement_data(self):
  if self.settlement_data is None:self._capture_settlement(self.model.state,self.model.state)
 def _can_settle(self):
  return bool(self.can_show_settlement() and self.settlement_data)
 def can_show_settlement(self):
  return bool(self.model.state.get('phase')=='finished' and self._committed_match(self.model.state) is not None and self.model.state.get('broadcast',{}).get('config',{}).get('settlement_enabled',True))
 def _clear_settlement(self,restore_geometry=True):
  if restore_geometry and self._settlement_geometry is not None:
   self.windows['standings'].setGeometry(*self._settlement_geometry)
  self._settlement_geometry=None;self.settlement_data=None
 def _begin_settlement(self,now):
  self.art.win_started=None
  if self._settlement_geometry is None:self._settlement_geometry=self._geometry_list(self.windows['standings'])
  self._standings_change=None;self.set_stage('settlement_hand',now)
  self._sync_bottom_geometry(force=True);self.on_display()
 def _advance_after_settlement(self,now):
  advance=self.pending_next
  if advance and self.model.state.get('phase')=='result' and self.model.state.get('lastResult') and not self.model.state['lastResult']['next']['finished']:
   if self.model.next_hand():return
  if self.model.settings.get('show_yaku'):self._save_display_settings(show_yaku=False)
  self.pending_next=False
  summary=self.settlement_data if self.model.state.get('phase')=='finished' else None
  self._clear_settlement();self.settlement_data=summary
  self.set_stage('players_in',now);self.on_display()
 def chart_progress(self,now=None):
  if self.chart_started is None:return 1.
  return max(0.,min(1.,((time.monotonic() if now is None else now)-self.chart_started)*self.speed/1.8))
 def chart_flow_active(self):
  return not self.closing and self.content_visible('chart')
 def replay_chart(self,show=True):
  if show and not self.enabled.get('chart',False):self.set_extension_visible('chart',True);return
  self.chart_started=time.monotonic();self.chart_flow_started=self.chart_started;self.timer.start();self.windows['chart'].update()
 def signature(self):
  info=self.model.last_info or {}
  return (info.get('name'),info.get('han'),info.get('fu'),info.get('yakuman'),tuple(info.get('yaku',[])),info.get('base_points'))
 def set_stage(self,stage,now=None):
  now=time.monotonic() if now is None else now;self.stage=stage;self.stage_started=now
  if stage in ('players_in','players_demo_in'):self.animation.clear_presentation()
  if stage=='win':self.yaku_started=now;self.art.win_started=now;self.info_signature=self.signature()
  self.timer.start()
 def age(self,now=None):return ((time.monotonic() if now is None else now)-self.stage_started)*self.speed
 def replay_players(self,mode='entry'):
  """Preview existing card motion without creating a gameplay transition."""
  if mode not in ('entry','cycle'):raise ValueError('选手动画类型无效')
  if self._opening_active() or self.riichi_queue.busy or self.animation.active or self._final_card_change is not None or self.stage not in ('table','players_demo_out','players_demo_in') or self.pending_win or self.pending_next or self.postmatch.active or self.postmatch.preparing:
   raise ValueError('请等待当前播出动画结束后再展示选手动画')
  self.set_stage('players_demo_out' if mode=='cycle' else 'players_demo_in')
  self.on_display();self.model.display_changed.emit()
  return True
 def on_change(self,before,after,kind):
  if self.closing:return
  if self._opening_active():self.opening.on_model_changed(before,after,kind)
  if self.stage in ('players_demo_out','players_demo_in'):self.set_stage('table')
  display_before=self.riichi_display_state(before)
  had_riichi_holds=bool(self._riichi_holds)
  self._update_riichi_queue(before,after,kind)
  display_after=self.riichi_display_state(after)
  if kind in ('import','event_settings'):self._sidebar_signature=None;self._active_sidebar_preset_id=None
  self._final_card_change=None
  archived=self._committed_match(after)
  signature=self._final_archive_signature(archived)
  committed_final=kind=='commit_match' and after.get('phase')=='finished' and archived is not None and (self._final_archive_signature(self._committed_match(before))!=signature or self._last_final_archive!=signature)
  if committed_final:
   self._clear_settlement();self._capture_settlement(before,after)
  elif kind in ('ron','tsumo','draw'):
   self._clear_settlement();self._capture_settlement(before,after)
  elif kind in ('match_penalty','roster_management'):
   self._capture_settlement(before,after)
  elif kind in ('clear','restore','next_hand','start_match','new_match','import'):self._clear_settlement(restore_geometry=kind!='import')
  previous=before.get('broadcast',{}).get('config',{});current=after.get('broadcast',{}).get('config',{})
  if self.settlement_data and not current.get('settlement_enabled',True):
   active=self.stage in ('settlement_hand','settlement_total','settlement_final')
   self._clear_settlement()
   if active:
    self.pending_win=False;self.pending_next=False;self.model.settings['show_yaku']=False;self.set_stage('table')
  elif self.settlement_data and previous.get('event_mode')!=current.get('event_mode'):
   self._capture_settlement(before,after)
  if kind!='import' and self.enabled.get('standings',False) and any(previous.get(key)!=current.get(key) for key in ('standings_mode',)):
   self._start_standings_change(before)
  self.extension_cache.clear()
  if kind=='import':
   self._last_final_archive=signature
   self.enabled=self.model.settings.get('visible',{}).copy()
   for key in FULLSCREEN_PARTS:self.enabled[key]=False
   self.model.settings['visible']=self.enabled.copy()
   for key in EXTRA_SIZES:self.enabled.setdefault(key,key=='table_scores' and self.model.settings.get('broadcast_scene','table') in ('table','qualification'))
   self.extension_transitions.clear();self._standings_change=None;self.commentary_deadline=None;self.commentary_timer.stop()
   if getattr(self,'_commentary_motion',None):self._commentary_motion.reset()
   self._commentary_data=None;self._commentary_pending=None;self._commentary_motion_running=False
   if self.enabled.get('commentary'):self.reset_commentary_timer()
   self.restore_geometry()
   if self.enabled.get('chart'):self.replay_chart(show=False)
   else:self.chart_started=None
  elif kind=='event_settings':
   self.restore_geometry()
  self.art.update(display_before,display_after)
  if kind in ('ron','tsumo'):
   self.model.settings['final_cards_visible']=False
   self._riichi_payment_active=False;self.final_pt_deadline=None;self._auto_final_pending=False;self.stage='table';self.pending_win=True;self.pending_next=True;self.model.settings['show_yaku']=True;self.animation.begin(before,after,self.speed,self.model.settings.get('riichi_payment_mode','immediate'),theme=self.model.settings.get('theme','mleague'));self.timer.start()
  elif kind=='draw':
   self.model.settings['final_cards_visible']=False
   self._riichi_payment_active=False;self.final_pt_deadline=None;self._auto_final_pending=False;self.stage='table';self.pending_win=False;self.pending_next=True;self.animation.begin(before,after,self.speed,self.model.settings.get('riichi_payment_mode','immediate'),theme=self.model.settings.get('theme','mleague'));self.timer.start()
  elif kind=='riichi' and any(display_score(display_after,i,self.model.settings.get('riichi_payment_mode'))!=display_score(display_before,i,self.model.settings.get('riichi_payment_mode')) for i in range(4)):
   self.animation.begin(display_before,display_after,self.speed,self.model.settings.get('riichi_payment_mode','immediate'),preserve_other_tracks=True,theme=self.model.settings.get('theme','mleague'));self._riichi_payment_active=had_riichi_holds;self.timer.start()
  elif kind in ('clear','restore','next_hand','start_match','new_match','import'):
   if kind in ('clear','restore','next_hand','start_match','new_match'):self.model.settings['final_cards_visible']=False
   self._riichi_payment_active=False;self.final_pt_deadline=None;self._auto_final_pending=False;self._final_card_change=None;self.animation.finish();self.animation.clear_presentation();self.pending_win=False;self.pending_next=False;self.model.settings['show_yaku']=False;self.art.win_started=None;self.set_stage('players_in' if kind=='next_hand' else 'table')
   if kind=='restore':self._restore_settlement_data()
  elif any(p['score']!=display_before['players'][i]['score'] for i,p in enumerate(display_after['players'])):self.animation.finish();self._riichi_payment_active=False
  elif self.animation.active:self.animation.after=display_after
  self.extra_until=max(getattr(self,'extra_until',0),time.monotonic()+.85/self.speed);self.timer.start();self.on_display()
  if kind=='import':self.accept_geometry()
 def _riichi_allowed(self):
  return bool(not self.closing and self.model.settings.get('show_overlays',True) and self.model.settings.get('broadcast_scene','table')!='clean' and self.model.state.get('broadcast',{}).get('config',{}).get('riichi_effect_enabled',True) and not (getattr(self,'postmatch',None) and self.postmatch.active))
 def riichi_display_state(self,state=None):
  """Mask only each queued declaration, without freezing unrelated live edits."""
  state=self.model.state if state is None else state
  if not self._riichi_holds:return state
  players=list(state.get('players',[]));deposit=0
  for index,player in enumerate(players):
   hold=self._riichi_holds.get(str(player.get('id') or index))
   if hold is None or not player.get('riichi'):continue
   shown=dict(player);shown['riichi']=hold['riichi'];shown['tenpai']=hold['tenpai'];shown['score']=player['score']+hold['deposit'];players[index]=shown;deposit+=hold['deposit']
  result=dict(state);result['players']=players;result['pot']=max(0,state.get('pot',0)-deposit)
  return result
 def _release_riichi_item(self,item):
  if item is None:return False
  hold=self._riichi_holds.get(item.player_id)
  if hold is None or hold['sequence']!=item.sequence_id:return False
  self._riichi_holds.pop(item.player_id,None);return True
 def _advance_riichi_presentation(self,now):
  before=self.riichi_display_state();completed=self.riichi_queue.completed_count
  changed=self.riichi_queue.advance(now)
  if self.riichi_queue.completed_count!=completed and self._release_riichi_item(self.riichi_queue.last_completed):
   after=self.riichi_display_state();self.art.update(before,after)
   mode=self.model.settings.get('riichi_payment_mode','immediate')
   if any(display_score(after,i,mode)!=display_score(before,i,mode) for i in range(4)):
    self.animation.begin(before,after,self.speed,mode,preserve_other_tracks=True,theme=self.model.settings.get('theme','mleague'));self._riichi_payment_active=True;self.timer.start()
  self._sync_riichi_sound()
  return changed
 def _sync_riichi_sound(self):
  active=self.riichi_queue.active if self._riichi_allowed() else None
  sequence=active.sequence_id if active else None
  if sequence==self._riichi_sound_sequence:return
  self._riichi_sound_sequence=sequence
  if sequence is None:self.riichi_audio.stop()
  else:self.riichi_audio.play(sequence)
 def _clear_riichi_presentation(self,update_art=True):
  before=self.riichi_display_state();had_holds=bool(self._riichi_holds)
  self.riichi_queue.clear();self._riichi_holds.clear()
  self.riichi_audio.stop();self._riichi_sound_sequence=None
  if update_art and had_holds:self.art.update(before,self.model.state)
  if self._riichi_payment_active:self.animation.finish();self._riichi_payment_active=False
 def _update_riichi_queue(self,before,after,kind):
  changed_ids=[p.get('id') for p in before.get('players',[])]!=[p.get('id') for p in after.get('players',[])]
  if changed_ids or kind in ('clear','restore','next_hand','start_match','new_match','import','ron','tsumo','draw'):
   self._clear_riichi_presentation(update_art=False);return
  if not self._riichi_allowed():self._clear_riichi_presentation(update_art=False);return
  if kind=='riichi':
   now=time.monotonic()
   for index,(old,new) in enumerate(zip(before.get('players',[]),after.get('players',[]))):
    identity=str(new.get('id') or index)
    if old.get('riichi') and not new.get('riichi'):
     self.riichi_queue.cancel(identity,now);self._riichi_holds.pop(identity,None)
    elif not old.get('riichi') and new.get('riichi') and after.get('phase')=='playing':
     completed=self.riichi_queue.completed_count;item=self.riichi_queue.enqueue(snapshot_riichi(self.model,index),now)
     self._riichi_holds[identity]=dict(sequence=item.sequence_id,riichi=bool(old.get('riichi')),tenpai=bool(old.get('tenpai')),deposit=max(0,old['score']-new['score']))
     if self.riichi_queue.completed_count!=completed:self._release_riichi_item(self.riichi_queue.last_completed)
   self._sync_riichi_sound()
   if self.riichi_queue.busy:self.timer.start()
  else:
   for index,(old,new) in enumerate(zip(before.get('players',[]),after.get('players',[]))):
    hold=self._riichi_holds.get(str(new.get('id') or index))
    if hold and any(old.get(key)!=new.get(key) for key in ('tenpai','waits','remaining','waits_no_yaku','furiten')):hold['tenpai']=bool(new.get('tenpai'))
 def clear_riichi_effects(self):
  self._clear_riichi_presentation();self.model.display_changed.emit()
 def riichi_status_text(self):
  active=self.riichi_queue.active
  if active is None:return '立直特效：等待立直'
  waiting=' → '.join(item.player_name for item in self.riichi_queue.pending)
  return '播放中：'+active.player_name+('　待播：'+waiting if waiting else '　待播：0')
 def on_display(self):
  if self.closing:return
  if hasattr(self,'fullscreen_capture'):self._sync_programme_visibility()
  if hasattr(self,'fullscreen_capture'):self._bind_fullscreen_windows();self.normalize_fullscreen_selection()
  if not self._riichi_allowed():self._clear_riichi_presentation()
  self._sync_qualification_geometry()
  event=getattr(self.model,'event',None) or {}
  event_signature=repr((event.get('name'),event.get('team_names'),event.get('team_baselines'),event.get('bracket')))
  if event_signature!=getattr(self,'_event_signature',None):self.extension_cache.clear();self._event_signature=event_signature
  font_signature=(self.model.settings.get('overlay_font_family',''),self.model.settings.get('overlay_font_scale',1.),self.model.settings.get('theme'),
                  json.dumps([self.model.settings.get('player_card_style'),self.model.settings.get('saikouisen_card_style')],sort_keys=True))
  if font_signature!=self._font_signature:
   self._font_signature=font_signature;self.extension_cache.clear();self._final_card_change=None
  configured=self.model.settings.get('visible',{})
  desired={key:configured.get(key,key not in EXTRA_SIZES or (key=='table_scores' and self.model.settings.get('broadcast_scene','table') in ('table','qualification'))) for key in self.windows}
  if any(self.enabled.get(key,key not in EXTRA_SIZES)!=value for key,value in desired.items()):
   chart_was_visible=self.enabled.get('chart',False)
   self.enabled=desired;self.extension_transitions.clear()
   if desired.get('chart') and not chart_was_visible:self.replay_chart(show=False)
   elif not desired.get('chart'):self.chart_started=None
   if self.enabled.get('commentary'):self.reset_commentary_timer()
   else:self.commentary_deadline=None;self.commentary_timer.stop()
  language=self.model.settings.get('language','zh_CN')
  if language!=self.language:
   self.language=language;set_language(language);self.extension_cache.clear()
  if self.animation.active and self.animation.mode!=self.model.settings.get('riichi_payment_mode','immediate'):self.animation.finish()
  new_speed=max(.1,self.model.settings.get('speed',1.))
  if new_speed!=self.speed:
   now=time.monotonic();ratio=self.speed/new_speed
   for starts in (self.art.status_start,self.art.frame_start,self.art.meld_start,self.art.dora_start,self.art.remaining_start):
    for key,started in starts.items():starts[key]=now-(now-started)*ratio
   for key in ('yaku_started','stage_started'):setattr(self,key,now-(now-getattr(self,key))*ratio)
   if self.art.win_started is not None:self.art.win_started=now-(now-self.art.win_started)*ratio
   if self.chart_started is not None:self.chart_started=now-(now-self.chart_started)*ratio
   if self.final_pt_deadline is not None:self.final_pt_deadline=now+max(0.,self.final_pt_deadline-now)*ratio
   self.extra_until=now+max(0.,getattr(self,'extra_until',now)-now)*ratio
   if self._final_card_change is not None:self._final_card_change.set_speed(new_speed,now)
   if self.animation.active:self.animation.set_speed(new_speed,now)
   self.speed=new_speed;self.timer.start()
  if self.stage=='table' and not self.animation.active and self.model.last_info and self.model.settings['show_yaku']:
   self.set_stage('players_out')
  elif self.stage=='table' and not self.animation.active and self.pending_next:
   if not self.model.last_info and (self.model.state['phase']=='result' or self._can_settle()):self.set_stage('draw_hold')
   else:self.pending_next=False
  if self.stage in ('win','final') and self.info_signature!=self.signature():self.set_stage('win')
  self._sync_sidebar_paging(time.monotonic())
  self._sync_sidebar_visibility(time.monotonic())
  self._sync_commentary_visibility(time.monotonic())
  for key,w in self.windows.items():
   w.apply_topmost(self.model.settings.get('topmost',False))
   label=self.capture_title(w.kind,w.index)
   if w.windowTitle()!=label:w.setWindowTitle(label)
   if w.kind in ('round','dora'):
    base=self.base_size(w.kind)
    if w.base!=base:w.base=base;w.resize(w.width(),round(w.width()*base[1]/base[0]))
   if w.kind=='player':
    base=SIZES['player']
    if w.base!=base:w.base=base;w.resize(w.width(),round(w.width()*base[1]/base[0]))
   show=self.content_visible(key)
   inactive_fullscreen=key in FULLSCREEN_PARTS and not self.model.settings.get('split_fullscreen_windows',False) and not self.capture_previewing(key)
   if key.startswith('player') or key in ('standings','round') or inactive_fullscreen:
    w.input_lock(key=='riichi_effect' or self.model.settings['locked'])
    if w.isVisible():w.hide()
    continue
   if not w.isVisible():w.show()
   if show and key in ('standings','riichi_effect'):w.raise_()
   preview=self.capture_previewing(key)
   w.input_lock((self.model.settings['locked'] or key=='riichi_effect' or not show or self.capture_suppressed(key)) and not preview);w.update()
  self.players_capture.sync_geometry();self.players_capture.apply_topmost(self.model.settings.get('topmost',False))
  if not self.players_capture.isVisible():self.players_capture.show()
  self.players_capture.input_lock((self.model.settings['locked'] or not self.player_capture_visible() or self.capture_suppressed('players')) and not self.capture_previewing('players'))
  self.players_capture.update()
  self.hud_capture.sync_geometry();self.hud_capture.apply_topmost(self.model.settings.get('topmost',False))
  if not self.hud_capture.isVisible():self.hud_capture.show()
  self.hud_capture.input_lock((self.model.settings['locked'] or not self.content_visible('round') or self.capture_suppressed('round')) and not self.capture_previewing('round'))
  self.hud_capture.update()
  for key,playback in (('opening',getattr(self,'opening',None)),('postmatch',getattr(self,'postmatch',None))):
   if playback:playback.capture_preview=self.capture_previewing(key);playback.capture_suppressed=self.capture_suppressed(key)
  self._sync_commentary_geometry()
  self._sync_bottom_geometry()
  self._schedule_status_frames(time.monotonic())
  if getattr(self,'postmatch',None):self.postmatch.sync_display()
  if getattr(self,'opening',None):self.opening.sync_display()
  self.fullscreen_capture.sync_settings()
  split=self.model.settings.get('split_fullscreen_windows',False)
  for key,window in self.split_programme_windows.items():
   window.sync_settings()
   window.setVisible(split or self.capture_previewing(key));window.update()
  self.fullscreen_capture.setVisible(not split or self.capture_previewing('fullscreen'))
  if not split and (self.fullscreen_capture.content_visible() or self.capture_previewing('fullscreen')):self.fullscreen_capture.raise_()
  self.fullscreen_capture.update()
  if hasattr(self,'programme'):self.programme.sync()
  if self.chart_flow_active():self.timer.start()
  if self.riichi_queue.busy:self.timer.start()
 def _schedule_status_frames(self,now):
  """Render moving frames; sleep through their holds and wake at the next orbit."""
  self.status_frame_timer.stop()
  moving=False;delays=[]
  if not self.closing and self.model.settings.get('theme')=='mleague' and self.model.state.get('phase')!='finished':
   for i,player in enumerate(self.riichi_display_state()['players']):
    if status_mode(player) and self.content_visible('player'+str(i)):
     _,active,delay=frame_phase(self.art.frame_start.get(i,self.art.created_at),now,self.speed)
     moving=moving or active
     if delay:delays.append(delay)
  if moving:self.timer.start()
  elif delays:self.status_frame_timer.start(max(1,math.ceil(min(delays)*1000)))
  return moving
 def _wake_status_frames(self):
  if self.closing:return
  self.timer.start();self.tick()
 def tick(self):
  if self.closing:return
  try:self._tick()
  except (OSError,ValueError) as error:
   # Keep the last accepted cue and pending hand; a failed automatic save must
   # not escape Qt or hammer the disk again on every animation frame.
   self.timer.stop();self.display_configuration.failed.emit(str(error))
 def _tick(self):
  now=time.monotonic()
  if self._advance_riichi_presentation(now):self.model.display_changed.emit()
  self._advance_sidebar_paging(now)
  self._advance_sidebar_visibility(now)
  self._advance_commentary_visibility(now)
  if self.chart_started is not None and self.chart_progress(now)>=1.:self.chart_started=None
  if self.commentary_deadline is not None and now>=self.commentary_deadline:
   self.set_extension_visible('commentary',False)
  finished=[key for key,(started,_,_) in self.extension_transitions.items() if now-started>=.32]
  for key in finished:self.extension_transitions.pop(key,None)
  if self._standings_change is not None and now-self._standings_change[0]>=.28:self._standings_change=None
  if self._final_card_change is not None and not self._final_card_change.active(now):self._final_card_change=None
  if finished:self.on_display()
  if self.animation.active and self.animation.elapsed(now)>=self.animation.duration_ms:
   self.animation.finish();self._riichi_payment_active=False
   if self.pending_win and self.model.last_info:self.pending_win=False;self.set_stage('players_out',now)
   elif self.pending_next:self.set_stage('draw_hold',now)
   self.on_display()
  age=self.age(now)
  if self.stage=='players_out' and age>=.45:self.set_stage('win',now);self.on_display()
  elif self.stage=='win' and age>=self.art.win_content_duration()+self.model.settings.get('win_hold',4.):
   # next_hand refuses a finished match. Exit the banner normally and reveal
   # final cards without requiring a separate archive/commit operation.
   self.set_stage('win_out',now);self.on_display()
  elif self.stage=='win_out' and age>=.45:
   self._advance_after_settlement(now)
  elif self.stage=='draw_hold' and age>=self.model.settings.get('win_hold',4.):
   self._advance_after_settlement(now)
  elif self.stage=='players_in' and age>=.45:
   self.set_stage('table',now)
   self.on_display()
  elif self.stage=='players_demo_out' and age>=.45:self.set_stage('players_demo_in',now);self.on_display()
  elif self.stage=='players_demo_in' and age>=.45:self.set_stage('table',now);self.on_display()
  elif self.stage=='final_points_out' and age>=.45:
   self._save_display_settings(final_points_mode=self._final_target);self.set_stage('final_points_in',now);self.on_display();self.model.display_changed.emit()
  elif self.stage=='final_points_in' and age>=.45:self.set_stage('table',now);self.on_display()
  for key,w in self.windows.items():
   if not key.startswith('player') and key not in ('riichi_effect','round') and (self.content_visible(key) or self.capture_previewing(key)):w.update()
  if self.content_visible('round') or self.capture_previewing('round'):self.hud_capture.update()
  if self.capture_previewing('players') or self.player_capture_visible():self.players_capture.update()
  if self.riichi_queue.busy or self.fullscreen_panel():self.fullscreen_capture.update()
  status_moving=self._schedule_status_frames(now)
  if not self.riichi_queue.busy and not status_moving and not self._sidebar_visibility_active(now) and not self._commentary_visibility_active(now) and not self.chart_flow_active() and not self.animation.active and self.stage in ('table','final','settlement_hand','settlement_total','settlement_final') and now>getattr(self,'extra_until',0) and not self.extension_transitions and self._standings_change is None and self.chart_started is None and self.final_pt_deadline is None and self._final_card_change is None and self._sidebar_transition is None:self.timer.stop()
 def finish(self):
  self.animation.finish();self._riichi_payment_active=False;self.art.status_start.clear();self.art.meld_start.clear();self.art.dora_start.clear();self.art.remaining_start.clear();self.art.remaining_from.clear()
  if self.model.last_info and (self.pending_win or self.stage in ('players_out','win') or self.model.settings['show_yaku']):
   self.pending_win=False;self.set_stage('win');now=time.monotonic();self.stage_started=now-self.art.win_content_duration()/self.speed;self.art.win_started=self.stage_started;self.yaku_started=self.stage_started
  elif self.pending_next and self.stage=='table':self.set_stage('draw_hold')
  if self.stage in ('players_demo_out','players_demo_in'):self.set_stage('table')
  self.extra_until=0.;self.on_display()
  if not self.riichi_queue.busy and self.stage=='table' and not self._sidebar_visibility_active() and not self._commentary_visibility_active() and not self.chart_flow_active() and not self._schedule_status_frames(time.monotonic()) and not self.extension_transitions and self._standings_change is None and self.chart_started is None and self.final_pt_deadline is None and self._final_card_change is None and self._sidebar_transition is None:self.timer.stop()
 def yaku_opacity(self,now=None):
  if self.stage=='final':return 1.
  age=self.age(now)
  return 1-ease(age/.45) if self.stage=='win_out' else ease(age/.45) if self.stage=='win' else 0.
 def player_opacity(self,now=None):
  age=self.age(now)
  return 1-ease(age/.45) if self.stage in ('players_out','final_points_out','players_demo_out') else ease(age/.45) if self.stage in ('players_in','final_points_in','players_demo_in') else 0. if self.stage in ('win','win_out','final','settlement_hand','settlement_total','settlement_final') else 1.
 def replay(self):
  if self.model.last_transition:
   b,a,k=self.model.last_transition;s=self.model.state
   if k=='riichi' and self.riichi_queue.busy:return
   if any(a['players'][i]['score']!=s['players'][i]['score'] for i in range(4)) or any(a[key]!=s[key] for key in ('pot','phase')):self.model.last_transition=None;return
   self._save_display_settings(final_points_mode='scores',show_yaku=k in ('ron','tsumo'))
   self._clear_settlement();self._capture_settlement(b,s)
   self.final_pt_deadline=None;self._auto_final_pending=False
   if k=='riichi' and self._riichi_allowed():
    declarations=[i for i,(old,new) in enumerate(zip(b['players'],a['players'])) if not old.get('riichi') and new.get('riichi')]
    if declarations and all(a['players'][i].get('id')==s['players'][i].get('id') for i in declarations):
     self._clear_riichi_presentation();self.animation.finish();self._riichi_payment_active=False
     now=time.monotonic()
     for i in declarations:
      item=self.riichi_queue.enqueue(snapshot_riichi(self.model,i),now);old=b['players'][i];new=s['players'][i]
      self._riichi_holds[item.player_id]=dict(sequence=item.sequence_id,riichi=bool(old.get('riichi')),tenpai=bool(old.get('tenpai')),deposit=max(0,old['score']-new['score']))
     self.art.update(s,self.riichi_display_state());self.stage='table';self.pending_win=False;self.pending_next=False;self.on_display();self.timer.start();return
   self._riichi_payment_active=False;self.stage='table';self.pending_win=k in ('ron','tsumo');self.pending_next=k in ('ron','tsumo','draw');self.animation.begin(b,s,self.speed,self.model.settings.get('riichi_payment_mode','immediate'),theme=self.model.settings.get('theme','mleague'));self.on_display();self.timer.start()
 def extension_opacity(self,key,now=None):
  transition=self.extension_transitions.get(key)
  if transition:
   started,old,new=transition;t=ease(((time.monotonic() if now is None else now)-started)/.32)
   return old+(new-old)*t
  return 1. if self.enabled.get(key,False) else 0.
 def reset_commentary_timer(self):
  seconds=self.model.state.get('broadcast',{}).get('config',{}).get('commentary_duration',0)
  try:seconds=max(0.,min(3600.,float(seconds)))
  except (TypeError,ValueError):seconds=0.
  motion=getattr(self,'_commentary_motion',None)
  if getattr(self,'_commentary_pending',None) is not None or (motion and motion.target and self._commentary_visibility_active()):
   self._commentary_hold_pending=True;self.commentary_deadline=None;self.commentary_timer.stop();return
  self.commentary_deadline=time.monotonic()+seconds if seconds else None
  if self.commentary_deadline is not None:self.commentary_timer.start(max(1,round(seconds*1000)))
  else:self.commentary_timer.stop()
 def _save_display_settings(self,**patch):
  """Commit a durable selection before changing playback, timers or windows."""
  self.display_configuration.commit(patch=patch)
 def set_extension_visible(self,key,value,settings_patch=None):
  if key not in EXTRA_SIZES:raise ValueError('未知的赛事窗口')
  value=bool(value);old=self.extension_opacity(key);was_enabled=self.enabled.get(key,False);enabled=self.enabled.copy();patch=dict(settings_patch or {})
  if value and key in FULLSCREEN_PANELS:
   patch['fullscreen_panel']=key
   if not self.model.settings.get('split_fullscreen_windows',False):
    for other in FULLSCREEN_PANELS:
     if other!=key:enabled[other]=False
  enabled[key]=value;self._save_display_settings(visible=enabled.copy(),**patch)
  if value and key in FULLSCREEN_PANELS and hasattr(self,'programme'):self.programme.before_panel(key)
  self.enabled=enabled;self.model.settings['visible']=enabled.copy()
  if value and key in FULLSCREEN_PANELS and not self.model.settings.get('split_fullscreen_windows',False):
   for other in FULLSCREEN_PANELS:
    if other!=key:self.extension_transitions.pop(other,None)
  if key=='standings' and not was_enabled and value:self._standings_change=None
  self.enabled[key]=value;self.extension_transitions[key]=(time.monotonic(),old,float(value))
  if key=='chart':
   if value:self.replay_chart(show=False)
   else:self.chart_started=None
  if key=='commentary':
   if value:self.reset_commentary_timer()
   else:self.commentary_deadline=None;self.commentary_timer.stop()
  self.timer.start();self.on_display();self.model.display_changed.emit()
 def set_visible(self,key,value):
  if key=='players':
   enabled=self.enabled.copy();enabled['players']=value
   for index in range(4):enabled[f'player{index}']=value
   self._save_display_settings(visible=enabled.copy());self.enabled=enabled
   self.on_display();self.model.display_changed.emit();return
  if key=='riichi_effect':
   if not value:self.clear_riichi_effects()
   return
  if key in EXTRA_SIZES:return self.set_extension_visible(key,value)
  enabled=self.enabled.copy();enabled[key]=value;self._save_display_settings(visible=enabled.copy());self.enabled=enabled
  self.on_display();self.model.display_changed.emit()
 def apply_scene(self,name):
  scenes={'table':{'round','dora','yaku','table_scores',*[f'player{i}' for i in range(4)]},
          'qualification':{'round','dora','table_scores','qualification',*[f'player{i}' for i in range(4)]},
          'review':{'chart'},'clean':set()}
  if name not in scenes:raise ValueError('未知的场景')
  config=self.model.state['broadcast']['config']
  if name=='qualification' and self.sidebar_mode()=='qualification' and config.get('qualification_layout','four')=='four':
   scenes[name]=scenes[name]-{'qualification'}|{'four_conditions'}
  enabled=self.enabled.copy();enabled.update({key:key in scenes[name] for key in self.windows})
  enabled['players']=any(enabled.get(f'player{i}',False) for i in range(4))
  opacities={key:self.extension_opacity(key) for key in self.windows if key in EXTRA_SIZES}
  self._save_display_settings(broadcast_scene=name,visible=enabled.copy())
  if self.postmatch.active or self.postmatch.preparing:self.postmatch.stop()
  if self._opening_active():self.opening.stop()
  for key in self.windows:
   if key in EXTRA_SIZES:
    self.extension_transitions[key]=(time.monotonic(),opacities[key],float(key in scenes[name]))
  self.enabled=enabled;self.model.settings['visible']=enabled.copy()
  if self.enabled.get('chart'):self.replay_chart(show=False)
  else:self.chart_started=None
  self.commentary_deadline=None;self.commentary_timer.stop()
  self.timer.start();self.on_display();self.model.display_changed.emit()
 def export_svg(self,kind,path):
  if kind=='table_scores' and is_team_event(self.model):raise ValueError('团队赛事不使用顶部四人积分条')
  index=0
  if kind.startswith('player'):
   suffix=kind[6:];index=int(suffix) if suffix else 0;kind='player'
   if index not in range(4):raise ValueError('选手编号无效')
  if kind not in SIZES and kind not in EXTRA_SIZES:raise ValueError('未知的导出内容')
  destination=Path(path);destination.parent.mkdir(parents=True,exist_ok=True)
  temporary=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.tmp')
  generator=QSvgGenerator();generator.setFileName(str(temporary));width,height=self.base_size(kind)
  generator.setSize(QSize(width,height));generator.setViewBox(QRectF(0,0,width,height));generator.setResolution(96)
  generator.setTitle(self.model.state.get('broadcast',{}).get('config',{}).get('title') or self.model.state.get('title','麻将比赛'))
  generator.setDescription(tr(self.window_label(kind,index))+' · '+tr('技术统计仅含已录入结算；副露率按有明副露的局数；打点与铳点含本场、不含供托。'))
  painter=QPainter()
  try:
   if not painter.begin(generator):raise OSError('无法写入 SVG 文件')
   self.draw_content(painter,kind,index,static=True);painter.end()
   del generator
   if not temporary.exists() or temporary.stat().st_size<100:raise OSError('SVG 导出失败')
   temporary.replace(destination)
  finally:
   if painter.isActive():painter.end()
   if temporary.exists():temporary.unlink()
  return destination
 def close(self,persist=True):
  if self.closing:return
  if persist:self.save_geometry()
  for monitor in getattr(self,'_capture_dpi_monitors',[]):monitor.dispose()
  self.closing=True;self.timer.stop();self.commentary_timer.stop();self.status_frame_timer.stop();self.sidebar_timer.stop();self.sidebar_hide_timer.stop();self.sidebar_hide_deadline=None
  for signal,callback in ((self.model.changed,self.on_change),(self.model.display_changed,self.on_display),
                          (self.model.changed,self.postmatch.on_model_changed),(self.model.changed,self.opening.on_model_changed),
                          (self.model.changed,self.programme.on_model_changed)):
   try:signal.disconnect(callback)
   except (RuntimeError,TypeError):pass
  self.postmatch.close()
  self.opening.close()
  self._clear_riichi_presentation(update_art=False)
  self.riichi_audio.close()
  if hasattr(self,'programme'):self.programme.close()
  for w in self.windows.values():w.close()
  self.players_capture.close()
  self.hud_capture.close()
  self.fullscreen_capture.close()
  for window in self.split_programme_windows.values():window.close()
  self.display_configuration.dispose()
