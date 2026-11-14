"""Code-native vector drawing. No web view, HTML, browser or network service."""
from __future__ import annotations
import math, copy, time
from dataclasses import dataclass
import tournament
from PySide6.QtCore import Qt,QRectF,QPointF,QByteArray
from PySide6.QtGui import QColor,QFont,QFontMetricsF,QPainter,QPainterPath,QPen,QPixmap,QImage,QTransform
from app_paths import ROOT
from mahjong_values import WINDS,TEAM_COLORS,display_score,payment_lines,normalize_waits,tile_sort_key
from i18n import tr
from PySide6.QtSvg import QSvgRenderer
from typography import overlay_scope,overlay_font
from player_layout import PLAYER_LAYOUT, DEALER_COLOR
from card_style import get_card_style, card_font, paint_brush
from player_status_frame import status_mode, paint_status_frame
from panel_typography import draw_single_line, single_line_layout
from identity_media import paint_identity_media
from tenpai_animation import WaitFrame, WaitMotion
from win_layout import CANVAS_SIZE as WIN_CANVAS_SIZE,SIDE_MARGIN as WIN_SIDE_MARGIN

FONT='Microsoft YaHei'
ROUND_HEADER=34
ROUND_WIDTH=384
HUD_HEIGHT=108+ROUND_HEADER
GAIN_COLOR='#00e450'
LOSS_COLOR='#f51e28'
TILE_FACE_RECT=QRectF(2.25,3.,20.5,30.)
WAIT_TILE_FACE_RECT=QRectF(1.5,2.,22.,32.)
DORA_SCALE=2.1
DORA_STEP=64
DORA_X=10
DORA_Y=ROUND_HEADER+16

def color(s):return QColor(s)
def rect(p,x,y,w,h,fill,stroke=None,width=1):
 p.setBrush(color(fill) if fill else Qt.BrushStyle.NoBrush);p.setPen(QPen(color(stroke),width) if stroke else Qt.PenStyle.NoPen);p.drawRect(QRectF(x,y,w,h))
def fitted_font(value,width,size,bold=False,minimum=12,height=None):
 f=overlay_font(size,bold,FONT)
 while QFontMetricsF(f).horizontalAdvance(str(value))>width and f.pixelSize()>minimum:f.setPixelSize(f.pixelSize()-1)
 if height is not None and overlay_font(size,fallback=FONT).pixelSize()!=round(size):
  while f.pixelSize()>round(size) and QFontMetricsF(f).tightBoundingRect(str(value)).height()>height:f.setPixelSize(f.pixelSize()-1)
 return f

def text_ink_bounds(value,bounds,font,align):
 return single_line_layout(value,bounds,font,align,minimum=12).ink_bounds.intersected(bounds)

def text(p,value,x,y,w,h,size=24,fill='#ffffff',align=Qt.AlignmentFlag.AlignLeft,bold=False,literal=False,ink=None,brush=None):
 value=tr(value,literal=literal)
 if ink is not None:
  p.fillPath(ink,brush or color(fill));return
 p.setPen(color(fill))
 return draw_single_line(p,value,QRectF(x,y,w,h),fitted_font(value,w,size,bold,height=h),align,minimum=12)
def _card_glyph(value,spec,settings,width,height=None,numeric=False,fit_reference=None):
 """Keep visible ink inside its region; letter spacing participates in fitting."""
 font=card_font(spec,settings,FONT)
 def glyph(label=value):
  path=QPainterPath();path.addText(0,0,font,str(label));return path
 def reference_width():
  return max(glyph(label).boundingRect().width() for label in fit_reference) if fit_reference else path.boundingRect().width()
 path=glyph()
 while reference_width()>width and font.pixelSize()>8:
  font.setPixelSize(font.pixelSize()-1);path=glyph()
 bounds=path.boundingRect();ink_height=bounds.height()
 if numeric:
  digits=QPainterPath();digits.addText(0,0,font,'0123456789');center=digits.boundingRect().center().y()
  # Use one punctuation envelope for every numeric string. Otherwise only
  # labels containing a comma shrink, making equal score digits different sizes.
  safe=QPainterPath();safe.addText(0,0,font,'0123456789,.−+')
  safe_bounds=safe.boundingRect().united(bounds)
  ink_height=2*max(center-safe_bounds.top(),safe_bounds.bottom()-center)
  path=QTransform.fromTranslate(0,-center).map(path)
 factor=min(1.,width/max(1.,bounds.width()),height/max(1.,ink_height) if height else 1.)
 if factor<1:path=QTransform().scale(factor,factor).map(path)
 return path


def _place_card_glyph(path,box,right=True,bottom=None,numeric=False):
 bounds=path.boundingRect()
 if bounds.isEmpty():return path
 x=box.right()-bounds.right() if right else box.left()-bounds.left()
 y=(box.center().y()-(0 if numeric else bounds.center().y())) if bottom is None else bottom-bounds.bottom()
 return QTransform.fromTranslate(x,y).map(path)


def _card_number(value,spec,settings,box,right=True,reference_width=None):
 numeric=any(char.isdigit() for char in str(value))
 reference=None
 if numeric and '.' in str(value):reference=('+999.9','−999.9')
 elif numeric and str(value).endswith('位'):reference=('999位',)
 width=min(box.width(),reference_width) if reference and reference_width is not None else box.width()
 path=_card_glyph(value,spec,settings,width,box.height(),numeric=numeric,fit_reference=reference)
 return _place_card_glyph(path,box,right,numeric=numeric)


def _identity_glyphs(name,team,style,settings,box,right=True):
 name_path=_card_glyph(name,style['text']['name'],settings,box.width())
 team_path=_card_glyph(team,style['text']['team'],settings,box.width())
 gap=style['team_name_gap']
 # Both lines share the same fixed block. Fit as a pair before positioning so
 # different CJK/Latin glyph bearings never change their visible interline gap.
 total=name_path.boundingRect().height()+team_path.boundingRect().height()
 available=box.height()-gap if team else box.height()
 if total>available:
  factor=max(.1,available/max(1.,total));transform=QTransform().scale(factor,factor)
  name_path=transform.map(name_path);team_path=transform.map(team_path)
 name_path=_place_card_glyph(name_path,box,right,bottom=box.bottom())
 team_path=_place_card_glyph(team_path,box,right,bottom=name_path.boundingRect().top()-gap)
 return team_path,name_path


def ml_card_geometry(name,team,value,rank=None,x=None,y=133,width=None,settings=None,*,height=196):
 """One set of ink positions for live, final, replacement and preview cards."""
 settings=settings or {};style=get_card_style(settings,'mleague')
 x=PLAYER_LAYOUT.card_x if x is None else x
 width=PLAYER_LAYOUT.card_width if width is None else width
 compact=height<=165
 band_height=62 if compact else 72
 labels={};identity=QRectF(x,y,width,height-band_height);band=QRectF(x,identity.bottom(),width,band_height)
 text_height=54 if compact else 64
 text_top=identity.bottom()-(6 if compact else 5)-text_height
 team_path,name_path=_identity_glyphs(name,team,style,settings,QRectF(x+10,text_top,width-20,text_height))
 labels['team']=dict(value=str(team),box=QRectF(x+10,text_top,width-20,24),ink=team_path)
 labels['name']=dict(value=str(name),box=QRectF(x+10,text_top+24,width-20,text_height-24),ink=name_path)
 # Keep six pixels above and below the complete number. PT labels reserve the
 # same signed three-digit width with or without an adjacent rank.
 number_height=band.height()-12;number_top=band.top()+6
 score_box=QRectF(x+12,number_top,width-25,number_height)
 if rank is not None:
  rank_box=QRectF(x+10,number_top,78,number_height);score_box=QRectF(x+97,number_top,width-110,number_height)
  rank_value=str(rank)+'位'
  labels['rank']=dict(value=rank_value,box=rank_box,ink=_card_number(rank_value,style['text']['rank'],settings,rank_box,False))
 labels['score']=dict(value=str(value),box=score_box,ink=_card_number(value,style['text']['score'],settings,score_box,reference_width=width-110))
 return dict(style=style,identity=identity,band=band,labels=labels)


def saikouisen_card_geometry(name,team,value,rank=None,x=142,y=164,width=260,settings=None):
 """A taller numeric field follows the 2025 A1 broadcast white card."""
 settings=settings or {};style=get_card_style(settings,'saikouisen');labels={}
 team_path,name_path=_identity_glyphs(name,team,style,settings,QRectF(x+13,y+2,width-26,38),False)
 if not team:
  name_box=QRectF(x+13,y+2,width-26,38)
  name_path=_place_card_glyph(_card_glyph(name,style['text']['name'],settings,name_box.width(),name_box.height()),name_box,False)
 labels['team']=dict(value=str(team),box=QRectF(x+13,y+1,width-26,16),ink=team_path)
 labels['name']=dict(value=str(name),box=QRectF(x+13,y+15,width-26,27),ink=name_path)
 score_box=QRectF(x+12,y+43,width-25,59)
 labels['score']=dict(value=str(value),box=score_box,ink=_card_number(value,style['text']['score'],settings,score_box))
 if rank is not None:
  rank_box=QRectF(x+13,y+112,width-26,53);rank_value=str(rank)+'位'
  labels['rank']=dict(value=rank_value,box=rank_box,ink=_card_number(rank_value,style['text']['rank'],settings,rank_box,False))
 return dict(style=style,identity=QRectF(x,y,width,42),band=QRectF(x,y+42,width,62),
             status=QRectF(x,y+112,width,53),status_fill='#b3000000',corner_radius=4.,labels=labels)


def _paint_card(p,layout,accent,suppress_numeric=False,identity_alpha=1.):
 style=layout['style']
 p.save()
 radius=layout.get('corner_radius',0)
 if radius:
  clip=QPainterPath();clip.addRoundedRect(layout['identity'].united(layout['band']),radius,radius)
  p.setClipPath(clip,Qt.ClipOperation.IntersectClip)
 for key,area in (('identity',layout['identity']),('score',layout['band'])):
  p.fillRect(area,paint_brush(style['backgrounds'][key],area,accent))
 p.restore()
 if 'status' in layout:p.fillRect(layout['status'],color(layout.get('status_fill','#050808')))
 for key,item in layout['labels'].items():
  if suppress_numeric and key in ('score','rank'):continue
  spec=style['text'][key];box=item['box'];ink=item['ink']
  p.save()
  if key in ('team','name'):p.setOpacity(p.opacity()*identity_alpha)
  text(p,item['value'],box.x(),box.y(),box.width(),box.height(),spec['size'],literal=True,
       ink=ink,brush=paint_brush(spec['paint'],ink.boundingRect()))
  p.restore()
 return layout


def paint_ml_card(p,name,team,accent,value,rank=None,x=None,y=133,width=None,settings=None,*,height=196,suppress_numeric=False,identity_alpha=1.):
 """Shared ML card; default gradients and every text style use the same geometry."""
 return _paint_card(p,ml_card_geometry(name,team,value,rank,x,y,width,settings,height=height),accent,suppress_numeric,identity_alpha)


def paint_saikouisen_card(p,name,team,accent,value,rank=None,x=142,y=164,width=260,settings=None):
 return _paint_card(p,saikouisen_card_geometry(name,team,value,rank,x,y,width,settings),accent)


def paint_card_label(p,value,spec,box,settings=None,right=False):
 """Custom-width labels in the preserved legacy settlement status strip."""
 ink=_place_card_glyph(_card_glyph(value,spec,settings,box.width(),box.height()),box,right)
 text(p,value,box.x(),box.y(),box.width(),box.height(),spec['size'],literal=True,
      ink=ink,brush=paint_brush(spec['paint'],ink.boundingRect()))
 return ink


def ease(t):
 t=max(0.,min(1.,t));return t*t*t*(t*(t*6-15)+10)
def fmt(n):return f'{round(n):,}'.replace('-','−')

def status_badge_paths(label,left,y,width=120.):
 # Keep glyph ink and the trapezoid in the same local coordinate system.
 shape=QPainterPath();shape.moveTo(left,y+34);shape.lineTo(left+width,y)
 shape.lineTo(left+width,y+53);shape.lineTo(left,y+53);shape.closeSubpath()
 safe=QPainterPath();safe.moveTo(left+2,y+35);safe.lineTo(left+width-2,y+3)
 safe.lineTo(left+width-2,y+51);safe.lineTo(left+2,y+51);safe.closeSubpath()
 value=tr(label)
 for size in range(24,11,-1):
  font=fitted_font(value,width-24,size,True)
  glyph=QPainterPath();glyph.addText(0,0,font,value);box=glyph.boundingRect()
  transform=QTransform();transform.translate(left+width/2,y+38)
  transform.translate(-box.center().x(),-box.center().y());ink=transform.map(glyph)
  if ink.subtracted(safe).isEmpty():break
 return shape,ink

def paint_status_badge(p,label,left,y,fill,width=120.):
 shape,ink=status_badge_paths(label,left,y,width);p.fillPath(shape,color(fill))
 p.save();p.setClipPath(shape,Qt.ClipOperation.IntersectClip)
 p.fillPath(ink,color('#ffffff'));p.restore()

TILE_NAMES={**{str(n)+s:prefix+str(n)+'.svg' for s,prefix in [('m','Man'),('p','Pin'),('s','Sou')] for n in range(1,10)},**{str(i+1)+'z':name+'.svg' for i,name in enumerate(['Ton','Nan','Shaa','Pei','Haku','Hatsu','Chun'])},**{'0'+s:prefix+'5-Dora.svg' for s,prefix in [('m','Man'),('p','Pin'),('s','Sou')]}}
_SVGS={}
_STICKS={}

def asset_svg(name):
 if name not in _SVGS:
  path=ROOT/'resources/tiles'/name
  if name in ('Ton.svg','Nan.svg','Shaa.svg','Pei.svg'):
   data=path.read_bytes().replace(b'#142896',b'#141414').replace(b'#19328c',b'#141414')
   renderer=QSvgRenderer(QByteArray(data))
  else:renderer=QSvgRenderer(str(path))
  if not renderer.isValid():raise ValueError('牌面素材无法读取：'+name)
  _SVGS[name]=renderer
 return _SVGS[name]

def tile(p,code,x,y,scale=1.,side=False,*,face_rect=None):
 p.save();p.translate(x,y);p.scale(scale,scale)
 if side:p.translate(0,25);p.rotate(-90)
 bounds=QRectF(0,0,25,36)
 if not code or code=='back':asset_svg('Back.svg').render(p,bounds)
 else:
  asset_svg('Front.svg').render(p,bounds)
  asset_svg(TILE_NAMES[code]).render(p,TILE_FACE_RECT if face_rect is None else face_rect)
 p.restore()

def meld_width(m,scale=1.):
 # The fourth tile of an added kan is stacked, so it takes no extra width.
 count=3 if m['kind']=='kakan' else len(m['tiles'])
 return (count*27+(11 if m['kind']!='ankan' else 0))*scale
def meld_tiles(m):
 """Design-space faces shared by broadcast painting and desk hit targets."""
 kind=m['kind'];tiles=list(m['tiles']);called=m.get('calledIndex');cursor=0;result=[]
 if kind=='chi' and called is not None:
  claimed=tiles.pop(called);tiles.sort(key=tile_sort_key);tiles.insert(called,claimed)
 if kind=='ankan':
  for i in (0,3):
   if i<len(tiles) and len(tiles)>1 and tiles[i][0]=='0':tiles[1],tiles[i]=tiles[i],tiles[1]
 for i,t in enumerate(tiles):
  if kind=='kakan' and i==3:continue
  side=kind!='ankan' and i==called
  back=kind=='ankan' and i in (0,3)
  result.append((i,t,cursor,11 if side else 0,side,back))
  if kind=='kakan' and i==called and len(tiles)>3:result.append((3,tiles[3],cursor,-14,True,False))
  cursor+=38 if side else 27
 return result

def meld(p,m,x,y,scale=1.,tile_progress=None):
 for index,code,dx,dy,side,back in meld_tiles(m):
  progress=tile_progress(index) if tile_progress else 1.
  p.save();p.setOpacity(p.opacity()*progress);tile(p,'back' if back else code,x+dx*scale,y+dy*scale,scale,side);p.restore()
 return x+meld_width(m,scale)

@dataclass(frozen=True)
class PaymentTiming:
 duration_ms:int
 payment_end_ms:int
 merge_at_ms:int
 merge_duration_ms:int
 fade_at_ms:int
 fade_duration_ms:int
 roll_at_ms:int
 roll_duration_ms:int
 entry_duration_ms:int
 winner_entry_ms:int
 other_entry_ms:int
 pot_update_ms:int


# Position/right-edge alignment is evidenced by H05/W05 reference stills.
# These slower durations implement the user's correction, not measured motion.
ML_PAYMENT_TIMING=PaymentTiming(3600,3140,2200,940,2800,340,2360,1080,480,320,800,3140)
LEGACY_PAYMENT_TIMING=PaymentTiming(2260,1620,1000,620,1350,270,1160,940,360,0,0,1660)


class Animation:
 duration_ms=ML_PAYMENT_TIMING.duration_ms
 def __init__(self):
  self.before=None;self.after=None;self.started=0.;self.speed=1.;self.active=False;self.start_scores=[];self.mode='immediate';self.lines=[]
  self._tracks={};self._empty_started=None;self._presentation=None;self.theme='mleague';self.timing=ML_PAYMENT_TIMING
 def begin(self,before,after,speed=1.,mode='immediate',*,preserve_other_tracks=False,theme='mleague'):
  now=time.monotonic()
  current=[self.value(i,now) for i in range(4)] if self.active else [display_score(before,i,mode) for i in range(4)]
  self.set_speed(speed,now)
  theme='saikouisen' if theme=='saikouisen' else 'mleague'
  # A later declaration owns only its own seat. Other players can still be
  # showing payments at slow speed when the fixed-duration declaration ends.
  previous={i:track for i,track in self._tracks.items()
            if preserve_other_tracks and self.active and mode==self.mode and theme==self.theme and self.elapsed(now,index=i)<self.duration_ms}
  self.theme=theme;self.timing=LEGACY_PAYMENT_TIMING if theme=='saikouisen' else ML_PAYMENT_TIMING;self.duration_ms=self.timing.duration_ms
  result=after.get('lastResult') or {};settlement=after.get('phase') in ('result','finished') and result.get('kind') in ('ron','tsumo','draw')
  self._presentation=dict(started=now,players=copy.deepcopy(before['players']),kind=result['kind'],winner=result.get('winner')) if settlement and theme=='mleague' else None
  incoming=payment_lines(before,after,mode);lines=[(0,0)]*4;tracks={}
  for i,line in enumerate(incoming):
   target=display_score(after,i,mode)
   if any(line) or target!=display_score(before,i,mode):
    delay=(self.timing.winner_entry_ms if result.get('winner') in (None,i) else self.timing.other_entry_ms) if settlement else 0
    deposit_only=after.get('phase')=='playing' and before['players'][i].get('riichi')!=after['players'][i].get('riichi')
    tracks[i]=dict(started=now,score=current[i],target=target,line=line,entry_delay=delay,deposit_only=deposit_only)
   elif i in previous:tracks[i]=previous[i]
   if i in tracks:lines[i]=tracks[i]['line']
  self.mode=mode;self.lines=lines;self._tracks=tracks
  self.before=copy.deepcopy(before);self.after=copy.deepcopy(after);self.started=now;self.active=True;self.start_scores=current
  self._empty_started=now if not tracks else None
 def elapsed(self,now=None,index=None):
  """Per-seat progress, or the least progress until every payment finishes."""
  now=time.monotonic() if now is None else now
  if index is not None:
   track=self._tracks.get(index)
   return (now-track['started'])*1000*self.speed if track else self.duration_ms
  starts=[track['started'] for track in self._tracks.values()]
  if not starts:starts=[self._empty_started if self._empty_started is not None else self.started]
  return min((now-started)*1000*self.speed for started in starts)
 def set_speed(self,speed,now=None):
  """Preserve the currently visible progress of every seat when speed changes."""
  now=time.monotonic() if now is None else now;speed=max(.1,float(speed))
  ratio=self.speed/speed
  for track in self._tracks.values():track['started']=now-(now-track['started'])*ratio
  if self._empty_started is not None:self._empty_started=now-(now-self._empty_started)*ratio
  if self._presentation is not None:self._presentation['started']=now-(now-self._presentation['started'])*ratio
  self.started=now-(now-self.started)*ratio;self.speed=speed
 def clear_presentation(self):
  """Restore normal player labels when the caller returns/replaces the cards."""
  self._presentation=None
 def presentation_frame(self,now=None):
  """Designed hand-payment order; retained after finish until the cards return."""
  if self._presentation is None:return None
  now=time.monotonic() if now is None else now
  elapsed=(now-self._presentation['started'])*1000*self.speed
  leave=ease(elapsed/160)
  return dict(players=self._presentation['players'],accessory_alpha=1-leave,
              accessory_y=-12*leave,identity_alpha=1-ease((elapsed-160)/160))
 def payment_delay(self,index):
  return self._tracks.get(index,{}).get('entry_delay',0)
 @property
 def payment_end_ms(self):return self.timing.payment_end_ms
 @property
 def pot_update_ms(self):return self.timing.pot_update_ms
 def payment_display_lines(self,index):
  """Keep the conserved ledger split; a declaration/refund occupies the stick row."""
  ordinary,deposit=self.lines[index]
  return (0,ordinary+deposit) if self._tracks.get(index,{}).get('deposit_only') else (ordinary,deposit)
 def value(self,index,now=None):
  if not self.active:return None
  track=self._tracks.get(index)
  if track is None:return display_score(self.after,index,self.mode)
  a=track['score'];b=track['target'];return a+(b-a)*ease((self.elapsed(now,index=index)-self.timing.roll_at_ms)/self.timing.roll_duration_ms)
 def finish(self):self.active=False;self._tracks.clear();self._empty_started=None

def stick(p,x,y,kind,width=110,height=17):
 name='riichi' if kind=='pot' else 'honba'
 if name not in _STICKS:_STICKS[name]=QPixmap(str(ROOT/'resources/sticks'/(name+'.png')))
 img=_STICKS[name]
 if img.isNull():raise ValueError('点棒素材无法读取：'+name)
 # Stretch only the plain white spans, keeping caps and center dots circular.
 sw,sh=img.width(),img.height();middle=(sw-sh)/2
 pieces=[(0,sh,height),(sh,middle-sh,(width-3*height)/2),
         (middle,sh,height),(middle+sh,middle-sh,(width-3*height)/2),(sw-sh,sh,height)]
 cursor=x
 for sx,source_width,target_width in pieces:
  overlap=.6 if sx+source_width<sw else 0
  p.drawPixmap(QRectF(cursor,y,target_width+overlap,height),img,QRectF(sx,0,source_width,sh));cursor+=target_width

class Artwork:
 def __init__(self,model,animation):
  self.model=model;self.animation=animation;self.images={};self.status_start={};self.meld_start={};self.dora_start={};self.remaining_start={};self.remaining_from={};self.wait_motion={};self.win_started=None
  self.created_at=time.monotonic();self.frame_start={i:self.created_at for i,p in enumerate(model.state['players']) if status_mode(p)}
 def transition(self,started,now,duration):
  if started is None:return 1.
  return ease((now-started)*max(.1,self.model.settings.get('speed',1.))/duration)
 def update(self,before,after):
  now=time.monotonic()
  for i,p in enumerate(after['players']):
   prev=before['players'][i]
   old_motion=self.wait_motion.get(i)
   if self.model.settings.get('theme')=='saikouisen' or after.get('phase')!='playing' or p.get('id')!=prev.get('id'):
    self.wait_motion.pop(i,None)
   elif bool(p.get('tenpai'))!=bool(prev.get('tenpai')):
    entering=bool(p.get('tenpai'))
    origin=old_motion.frame(now,self.model.settings.get('speed',1.)) if old_motion else WaitFrame(0.,-24.,0.,0.) if entering else WaitFrame()
    source=copy.deepcopy(p if entering else prev)
    remaining=self.remaining_frame(i,prev.get('remaining'),now) if not entering else None
    self.wait_motion[i]=WaitMotion(now,entering,source,remaining,origin)
   if (p['riichi'],p.get('tenpai'))!=(prev['riichi'],prev.get('tenpai')):self.status_start[i]=now
   if after.get('phase')=='finished' or not status_mode(p):self.frame_start.pop(i,None)
   elif status_mode(p)!=status_mode(prev) or p.get('id')!=prev.get('id') or before.get('phase')=='finished':self.frame_start[i]=now
   if not p.get('tenpai') or p.get('remaining') is None:
    self.remaining_start.pop(i,None);self.remaining_from.pop(i,None)
   elif p.get('remaining')!=prev.get('remaining') and (prev.get('tenpai') or self.model.settings.get('theme')=='saikouisen'):
    # Preserve the actually visible frame if the operator edits again mid-roll.
    self.remaining_from[i]=self.remaining_frame(i,prev.get('remaining') if prev.get('tenpai') else None,now)
    self.remaining_start[i]=now
   previous={m.get('id',str(j)):m for j,m in enumerate(prev['melds'])}
   for j,m in enumerate(p['melds']):
    key=m.get('id',str(j));old_group=previous.get(key)
    if m!=old_group:
     for k in range(len(m['tiles'])):
      if old_group and old_group['kind']=='pon' and m['kind']=='kakan' and k<3:continue
      self.meld_start[(i,key,k)]=now+(k*.11/max(.1,self.model.settings.get('speed',1.)) if old_group is None else 0)
  old=before.get('doraIndicators',[]);new=after.get('doraIndicators',[])
  self.dora_start={i:self.dora_start[i] for i,t in enumerate(new) if i<len(old) and old[i]==t and i in self.dora_start}
  for i,t in enumerate(new):
   if i>=len(old) or old[i]!=t:self.dora_start[i]=now
 def remaining_frame(self,index,value,now):
  ml=self.model.settings.get('theme')!='saikouisen';width,size=(61,36) if ml else (34,25)
  image=QImage(width,53,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
  if value is None:return image
  p=QPainter(image);p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
  progress=self.transition(self.remaining_start.get(index),now,.38)
  if progress<1. and index in self.remaining_from:
   p.save();p.setOpacity(1-progress);p.drawImage(QPointF(0,10*progress),self.remaining_from[index]);p.restore()
  p.setOpacity(progress)
  text(p,str(value),0,-10*(1-progress),width,53,size,'#ffffff',Qt.AlignmentFlag.AlignCenter,True)
  p.end();return image
 def remaining_number(self,p,index,value,x,y,now):
  p.drawImage(QPointF(x,y),self.remaining_frame(index,value,now))
 def draw(self,p,kind,index=0,now=None,*,show_dora=True):
  with overlay_scope(self.model.settings):self._draw(p,kind,index,now,show_dora=show_dora)
 def _draw(self,p,kind,index=0,now=None,*,show_dora=True):
  now=now or time.monotonic();p.setRenderHint(QPainter.RenderHint.Antialiasing);p.setRenderHint(QPainter.RenderHint.TextAntialiasing);p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
  if kind=='player':self.player(p,index,now)
  elif kind=='round':self.round(p,show_dora=show_dora)
  elif kind=='dora':self.dora(p,now)
  elif kind=='yaku':self.yaku(p,now)
 def round(self,p,*,show_dora=True):
  s=self.model.state;pot=self.animation.before['pot'] if self.animation.active and self.animation.elapsed()<self.animation.pot_update_ms else s['pot']
  saikouisen=self.model.settings.get('theme')=='saikouisen'
  if saikouisen:
   dora_width=DORA_X+max(0,len(self.model.dora_tiles))*DORA_STEP+12 if self.model.dora_tiles else 0
   rect(p,0,0,ROUND_WIDTH+dora_width,HUD_HEIGHT,'#a6000000')
  else:
   dora_count=len(self.model.dora_tiles) if show_dora else 0
   dora_width=DORA_X+dora_count*DORA_STEP+12 if dora_count else 12
   top=0 if s.get('broadcast',{}).get('config',{}).get('show_match_title',True) else ROUND_HEADER
   p.save();p.setPen(Qt.PenStyle.NoPen);p.setBrush(color('#a6404040'))
   p.drawRoundedRect(QRectF(0,top,ROUND_WIDTH+dora_width,HUD_HEIGHT-top),18,18);p.restore()
  text(p,s.get('title',''),16,0,ROUND_WIDTH-32,30,20,literal=not s.get('id','').startswith('demo-'))
  p.save();p.translate(0,ROUND_HEADER)
  if not saikouisen:
   p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(color('#ffffff'),6))
   p.drawRoundedRect(QRectF(19,16,190,72),13,13)
  label='终局' if s['phase']=='finished' else WINDS[s['roundIndex']//4]+' '+str(s['roundIndex']%4+1)+' 局'
  text(p,label,20 if saikouisen else 27,13,196 if saikouisen else 174,78,56 if saikouisen else 49,
       align=Qt.AlignmentFlag.AlignCenter,bold=saikouisen)
  for y,kind,value in [(25,'pot',pot//1000),(68,'honba',s['honba'])]:
   stick(p,238,y,kind);text(p,value,354,y-12,29,41,33,align=Qt.AlignmentFlag.AlignCenter)
  p.restore()
 def dora(self,p,now=None):
  now=now or time.monotonic();tiles=self.model.dora_tiles
  if not tiles:return
  # Paint only revealed tile faces; the shared HUD background is drawn by round().
  for i,code in enumerate(tiles):
   t=self.transition(self.dora_start.get(i),now,.62)
   p.save();p.setOpacity(p.opacity()*t)
   tile(p,code,DORA_X+i*DORA_STEP,DORA_Y,DORA_SCALE);p.restore()
 def player(self,p,i,now,suppress_numeric=False):
  """ML card: portrait riichi frame, dealer underline, fixed team score band."""
  if self.model.settings.get("theme")=="saikouisen":
   from legacy_player_artwork import draw_player
   return draw_player(self,p,i,now)
  state=self.model.state;player=state['players'][i];layout=PLAYER_LAYOUT
  finished=state['phase']=='finished'
  presentation=self.animation.presentation_frame(now)
  final=next((row for row in tournament.match_results(state) if row['id']==player['id']),None) if finished and self.model.settings.get('final_cards_visible',False) else None
  points=final is not None and self.model.settings.get('final_points_mode','scores')=='pt' and not self.animation.active
  status_player=dict(player,riichi=presentation['players'][i].get('riichi',False),tenpai=presentation['players'][i].get('tenpai',False)) if presentation else player
  score=self.animation.value(i,now)
  if score is None:score=display_score(state,i,self.model.settings.get('riichi_payment_mode'))
  value=f"{final['points']:+,.1f}".replace('-','−') if points else fmt(score)
  rank=final['rank'] if final and not self.animation.active else None
  p.save();p.setTransform(layout.body_transform(),True)
  paint_ml_card(p,player['name'],player.get('team',''),player.get('team_color',TEAM_COLORS[i]),value,rank,settings=self.model.settings,suppress_numeric=suppress_numeric,identity_alpha=presentation['identity_alpha'] if presentation else 1.)
  self.player_photo(p,status_player,i,now,state['phase']!='playing' and not presentation,presentation['accessory_alpha'] if presentation else 1.)
  if not finished:
   if state['phase']=='playing' and not presentation:self.player_waits(p,player,i,now)
   if i==state['dealer']:
    rect(p,layout.left,layout.dealer_y,layout.card_x+layout.card_width-layout.left,4,DEALER_COLOR)
  if presentation:
   if presentation['accessory_alpha']>0:
    p.save();p.setOpacity(p.opacity()*presentation['accessory_alpha']);p.translate(0,presentation['accessory_y'])
    self.player_waits(p,presentation['players'][i],i,now)
    p.restore()
  self.player_payments(p,i,now)
  p.restore()
  # Melds retain their own size; only their placement follows the lower card.
  if presentation:
   if presentation['accessory_alpha']>0:
    p.save();p.setOpacity(p.opacity()*presentation['accessory_alpha']);p.translate(0,presentation['accessory_y'])
    self.player_melds(p,presentation['players'][i],i,now,False);p.restore()
  else:self.player_melds(p,player,i,now,state['phase']!='playing')

 def player_photo(self,p,player,index,now,finished=False,status_alpha=1.):
  layout=PLAYER_LAYOUT;left=layout.left;top=layout.top;photo_w=layout.photo_width
  area=QRectF(left,top,photo_w,layout.photo_height)
  paint_identity_media(p,self.model,player,area,radius=15)
  # Portrait status stays separate from the dealer underline and wait tiles.
  mode=status_mode(player)
  if mode and not finished and status_alpha>0:
   p.save();p.setOpacity(p.opacity()*status_alpha*self.transition(self.status_start.get(index),now,.46))
   paint_status_frame(p,area,mode,self.frame_start.get(index,self.created_at),now,self.model.settings.get('speed',1.));p.restore()

 def player_waits(self,p,player,index,now):
  motion=self.wait_motion.get(index)
  if motion and motion.player.get('id')!=player.get('id'):motion=None
  if motion and not motion.entering:
   if not motion.active(now,self.model.settings.get('speed',1.)):return
   player=motion.player
  elif not player.get('tenpai'):return
  frame=motion.frame(now,self.model.settings.get('speed',1.)) if motion else WaitFrame()
  layout=PLAYER_LAYOUT;card_x=layout.card_x;card_w=layout.card_width;y=layout.status_y
  p.save();p.setClipRect(QRectF(card_x,y,card_w,layout.status_height),Qt.ClipOperation.IntersectClip)
  base_alpha=p.opacity()
  if player.get('furiten'):
   p.setOpacity(base_alpha*frame.tiles_alpha);rect(p,card_x,y,card_w,layout.status_height,'#486cba4b')
  waits=normalize_waits(player.get('waits',[])) if player.get('tenpai') else []
  remaining=player.get('remaining') if player.get('tenpai') else None
  available=card_w-24-61;scale=min(1.2,available/max(1,len(waits)*32))
  start=card_x+12+(available-len(waits)*32*scale)/2
  for position,code in enumerate(waits):
   p.save();p.setOpacity(base_alpha*frame.tiles_alpha)
   if code in player.get('waits_no_yaku',[]):p.setOpacity(p.opacity()*.32)
   tile(p,code,start+position*32*scale+frame.tiles_x,y+(layout.status_height-36*scale)/2,scale,face_rect=WAIT_TILE_FACE_RECT);p.restore()
  if remaining is not None:
   p.setOpacity(base_alpha)
   rect(p,card_x+card_w-66,y+6+38*(1-frame.line_fraction),1.3,38*frame.line_fraction,'#d9ffffff')
   p.setOpacity(base_alpha*frame.count_alpha)
   if motion and not motion.entering and motion.remaining is not None:
    p.drawImage(QPointF(card_x+card_w-61,y+(layout.status_height-53)/2),motion.remaining)
   else:self.remaining_number(p,index,remaining,card_x+card_w-61,y+(layout.status_height-53)/2,now)
  p.restore()

 def player_melds(self,p,player,index,now,finished=False):
  """Keep the highest-league meld strip and its right-to-left reveal intact."""
  if finished or not self.model.settings.get('show_melds',True):return
  melds=player['melds']
  if not melds:return
  # The meld strip keeps its own width when the main identity plate widens.
  right_edge=402
  offset=0 if self.model.settings.get('theme')=='saikouisen' else PLAYER_LAYOUT.body_transform().mapRect(QRectF(14,PLAYER_LAYOUT.top,132,196)).top()-168
  p.save();p.translate(0 if self.model.settings.get('theme')=='saikouisen' else 5,offset);rect(p,4,96,right_edge-4,58,'#62232c32');rect(p,4,96,right_edge-4,1.4,'#a6cbd1d2');rect(p,4,152.6,right_edge-4,1.4,'#a6cbd1d2')
  total=sum(meld_width(group) for group in melds)+11*max(0,len(melds)-1)
  scale=min(1.,(right_edge-24)/max(1,total));right=right_edge-12
  for position,group in enumerate(melds):
   key=group.get('id',str(position));progress=lambda tile_index:self.transition(self.meld_start.get((index,key,tile_index)),now,.26)
   x=right-meld_width(group,scale)
   meld(p,group,x,146-36*scale,scale,progress);right=x-11*scale
  p.restore()

 def player_payments(self,p,index,now):
  if not self.animation.active:return
  elapsed=self.animation.elapsed(now,index=index)
  timing=self.animation.timing
  if elapsed>=timing.payment_end_ms:return
  entry_delay=self.animation.payment_delay(index) if self.model.settings.get('theme')!='saikouisen' else 0
  if elapsed<=entry_delay:return
  ordinary,deposit=self.animation.payment_display_lines(index);layout=PLAYER_LAYOUT
  settlement=self.model.settings.get('theme')!='saikouisen' and self.animation.presentation_frame(now) is not None
  main_y,deposit_y=layout.payment_rows(self.model.settings.get('show_melds',True),bool(deposit),bool(ordinary),settlement=settlement,legacy=self.model.settings.get('theme')=='saikouisen')
  rows=[(amount,y) for amount,y in ((deposit,deposit_y),(ordinary,main_y)) if amount]
  if not rows:return
  entry_lift=0. if settlement else min(18.,max(0.,min(y for _,y in rows)-24))
  labels=tuple(('+' if amount>0 else '−')+fmt(abs(amount)) for amount,_ in rows) if settlement else None
  for amount,y in rows:self.payment_number(p,index,amount,y,elapsed,entry_lift,entry_delay=entry_delay,entry_duration=timing.entry_duration_ms if entry_delay else 360,settlement=settlement,fit_reference=labels)
 def payment_number(self,p,i,delta,start_y,elapsed,entry_lift=18.,*,entry_delay=0,entry_duration=360,settlement=False,fit_reference=None):
  timing=self.animation.timing
  age=max(0.,elapsed-entry_delay);alpha=ease(age/entry_duration);merge=ease((elapsed-timing.merge_at_ms)/timing.merge_duration_ms);alpha*=1-ease((elapsed-timing.fade_at_ms)/timing.fade_duration_ms)
  entry=ease(age/entry_duration);start_y-=entry_lift*(1-entry);layout=PLAYER_LAYOUT
  final_score=fmt(display_score(self.model.state,i,self.animation.mode))
  geometry=saikouisen_card_geometry if self.model.settings.get('theme')=='saikouisen' else ml_card_geometry
  score=geometry('', '', final_score, settings=self.model.settings)['labels']['score']
  target=QPointF(score['ink'].boundingRect().right() if settlement else score['ink'].boundingRect().center().x(),score['box'].center().y())
  label=('+' if delta>0 else '−')+fmt(abs(delta))
  width=max(1.,target.x()-layout.card_x-10) if settlement else min(layout.card_width,2*min(target.x()-4,layout.canvas[0]-4-target.x()))
  # One glyph style for ordinary and deposit payments. Fit once so global
  # font scaling cannot be applied twice. Both settlement rows fit as one pair.
  spec=dict(family='',size=48,letter_spacing=0.,bold=False)
  ink=_card_glyph(label,spec,self.model.settings,width,44,numeric=True,fit_reference=fit_reference)
  center=QPointF(ink.boundingRect().right() if settlement else ink.boundingRect().center().x(),0)
  p.save();p.setOpacity(p.opacity()*alpha);p.translate(target.x(),start_y+(target.y()-start_y)*merge);p.translate(-center.x(),-center.y())
  text(p,label,0,0,width,44,48,GAIN_COLOR if delta>0 else LOSS_COLOR,Qt.AlignmentFlag.AlignCenter,ink=ink);p.restore()
 def win_content_duration(self):
  return .85+.28*len((self.model.last_info or {}).get('yaku',[]))
 def yaku(self,p,now=None):
  p.save();p.translate(WIN_SIDE_MARGIN,0)
  try:self._yaku_content(p,now)
  finally:p.restore()
 def _yaku_content(self,p,now=None):
  info=self.model.last_info
  if not info:return
  now=now or time.monotonic();age=(now-self.win_started)*self.model.settings.get('speed',1.) if self.win_started is not None else 99.
  index=info.get('winner',next((i for i,d in enumerate(self.model.state['players']) if d['name']==info['name']),0));d=self.model.state['players'][index];accent=d.get('team_color','#377e72')
  # W01/W03: one winner at left, a separate dark role strip and green total.
  rect(p,144,0,254,200,'#c8000000')
  rect(p,450,0,1390,200,'#c8000000')
  paint_identity_media(p,self.model,d,QRectF(10,0,134,200),radius=20)
  text(p,d.get('team',''),154,47,234,30,20,align=Qt.AlignmentFlag.AlignCenter,literal=True)
  text(p,info['name'],154,78,234,44,32,align=Qt.AlignmentFlag.AlignCenter,bold=True,literal=True)
  rect(p,144,132,254,68,accent)
  text(p,fmt(d['score']),154,131,234,68,56,align=Qt.AlignmentFlag.AlignCenter,bold=True)
  names=[tr(n) for n in info.get('yaku',[])]
  # Flow whole role names across rows; never squeeze each into a fixed grid cell.
  for size in range(32,17,-1):
   f=overlay_font(size,fallback=FONT);metrics=QFontMetricsF(f);positions=[];x=0;row=0
   for name in names:
    width=min(960,metrics.horizontalAdvance(name)+4)
    if x and x+width>960:x=0;row+=1
    positions.append((x,row,width));x+=width+28
   if (row+1)*(size+20)<=170:break
  for j,(name,(x,row,width)) in enumerate(zip(names,positions)):
   alpha=ease((age-.30-j*.28)/.24);p.save();p.setOpacity(p.opacity()*alpha);text(p,name,505+x,28+row*(size+20),width,size+20,size);p.restore()
  rect(p,1505,32,2,132,'#ffffff')
  text(p,'合计',1545,40,250,36,24)
  alpha=ease((age-.45-len(names)*.28)/.4);p.save();p.setOpacity(p.opacity()*alpha)
  if info.get('yakuman'):
   count='役满' if info['yakuman']==1 else str(info['yakuman'])+'倍役满'
   text(p,count,1570,82,240,70,48,GAIN_COLOR)
  else:
   han=str(info['han'])+'翻';f=overlay_font(48,fallback=FONT);advance=QFontMetricsF(f).horizontalAdvance(han)
   text(p,han,1570,82,155,70,48,GAIN_COLOR)
   text(p,str(info['fu'])+'符',1570+min(advance,145)+8,99,105,48,28,GAIN_COLOR)
  p.restore()

SIZES={'player':PLAYER_LAYOUT.canvas,'round':(ROUND_WIDTH,HUD_HEIGHT),'dora':(344,HUD_HEIGHT),'yaku':WIN_CANVAS_SIZE}
def render_image(art,kind,index=0,scale=1.,now=None):
 w,h=SIZES[kind];img=QImage(round(w*scale),round(h*scale),QImage.Format.Format_ARGB32_Premultiplied);img.fill(Qt.GlobalColor.transparent);p=QPainter(img);p.scale(scale,scale);art.draw(p,kind,index,now);p.end();return img
