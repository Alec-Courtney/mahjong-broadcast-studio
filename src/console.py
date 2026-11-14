from __future__ import annotations
import copy,uuid,time,math
from functools import lru_cache
from pathlib import Path
from PySide6.QtCore import Qt,QSize,QSignalBlocker,QTimer,QEvent,Signal
from PySide6.QtGui import QColor,QIcon,QPixmap,QImage,QPainter,QPainterPath,QPen,QValidator
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QLabel,QPushButton,QCheckBox,QComboBox,QSpinBox,QLineEdit,QVBoxLayout,QHBoxLayout,QBoxLayout,QGridLayout,QLayout,QGroupBox,QDialog,QDialogButtonBox,QFileDialog,QFrame,QSizePolicy,QMenu,QGraphicsOpacityEffect,QToolBar,QScrollArea,QStyle,QStyleOptionSpinBox,QStyleOptionComboBox)
from mahjong_values import WINDS,TILES,YAKU,tile_name,normalize_waits,display_score,tile_sort_key
from artwork import tile,fmt,WAIT_TILE_FACE_RECT
from i18n import LANGUAGES,tr,set_language,localize_widgets,LanguageController

from desk_theme import stylesheet, apply_theme, palette as desk_palette
from docking import DockWorkspace, NavigationResizeHandle, PanelCollection
from color_input import ColorInput
from capture_surface import reference_width
from ui_widgets import integer,decimal,quantity_control,WindowScaleSpinBox
from desk_tiles import TILE_SIZE_KEYS,tile_scale
from meld_preview import MeldPreviewWidget

STYLE=stylesheet({})


def button(name,fn=None,checkable=False):
 b=QPushButton(name);b.setCheckable(checkable)
 if fn:b.clicked.connect(fn)
 return b
def display_button(name):
 b=button(name,checkable=True);b.setProperty('displayToggle',True);return b
@lru_cache(maxsize=4)
def history_arrow_icon(direction,theme):
 colors=desk_palette({'console_theme':theme});icon=QIcon()
 for mode in (QIcon.Mode.Normal,QIcon.Mode.Disabled):
  image=QImage(96,96,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
  painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing);painter.scale(4,4)
  if direction=='redo':painter.translate(24,0);painter.scale(-1,1)
  color=QColor(colors['muted'] if mode==QIcon.Mode.Disabled else colors['text'])
  if mode==QIcon.Mode.Disabled:color.setAlphaF(.55)
  painter.setPen(QPen(color,2.,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin));painter.setBrush(Qt.BrushStyle.NoBrush)
  path=QPainterPath();path.moveTo(21,18);path.cubicTo(21,7,10,4,4,12);painter.drawPath(path)
  path=QPainterPath();path.moveTo(4,5);path.lineTo(4,12);path.lineTo(11,12);painter.drawPath(path)
  painter.end();icon.addPixmap(QPixmap.fromImage(image),mode)
 return icon
def box(title):
 g=QGroupBox(title);v=QVBoxLayout(g);v.setSpacing(9);return g,v
def row(*items):
 h=QHBoxLayout();h.setSpacing(7)
 for x in items:
  if isinstance(x,str):
   label=QLabel(x);label.setSizePolicy(QSizePolicy.Policy.Maximum,QSizePolicy.Policy.Preferred);h.addWidget(label)
  elif isinstance(x,int):h.addStretch(x)
  else:h.addWidget(x)
 return h
def combo(values):
 c=QComboBox()
 for item in values:
  name,value=item if isinstance(item,tuple) else (str(item),item);c.addItem(name,value)
 return c
def spin(value=0,lo=0,hi=10000,step=1):
 s=integer(lo,hi,step);s.setValue(value);return s

class OptionalCountSpinBox(QSpinBox):
 """An uncounted wall has no visible number; zero is a confirmed count."""
 def __init__(self):
  super().__init__();quantity_control(self);self.setRange(-1,136);self.setSpecialValueText(' ');self.setSuffix(' 张');self.setKeyboardTracking(False);self.setValue(-1)
 def valueFromText(self,value):
  return -1 if not value.replace(self.suffix(),'').strip() else super().valueFromText(value)
 def validate(self,value,pos):
  if not value.replace(self.suffix(),'').strip():return QValidator.State.Acceptable,value,pos
  return super().validate(value,pos)

class PlayerWaitTiles(QWidget):
 """Published waits stay visible for every seat, even in a narrow player dock."""
 edit_requested=Signal()
 def __init__(self):
  super().__init__();self.waits=[];self.no_yaku=[];self.tenpai=False
  self.setMinimumWidth(40);self.set_tile_scale(1.25);self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
  self.setCursor(Qt.CursorShape.PointingHandCursor)
 def set_tile_scale(self,scale):
  self.tile_scale=scale;self.setFixedHeight(round(36*scale));self.updateGeometry();self.update()
 def set_player(self,player):
  self.waits=list(player.get('waits',[]));self.no_yaku=list(player.get('waits_no_yaku',[]));self.tenpai=player.get('tenpai',False)
  details=[tile_name(code)+(' · '+tr('无役') if code in self.no_yaku else '') for code in self.waits]
  if player.get('furiten'):details.append(tr('振听'))
  description=' / '.join(details) if self.tenpai and details else tr('未填写待牌' if self.tenpai else '未听牌')
  self.setToolTip(description+' · '+tr('点击编辑听牌'));self.setAccessibleName(description);self.update()
 def paintEvent(self,event):
  painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
  if self.tenpai and self.waits:
   width=min(25.*self.tile_scale,self.width()/len(self.waits));scale=width/25.;top=(self.height()-36*scale)/2
   for index,code in enumerate(self.waits):
    painter.setOpacity(.35 if code in self.no_yaku else 1.);tile(painter,code,index*width,top,scale,face_rect=WAIT_TILE_FACE_RECT)
  else:
   painter.setPen(self.palette().color(self.foregroundRole()));painter.setOpacity(.65)
   text=tr('未填写待牌' if self.tenpai else '未听牌');text=self.fontMetrics().elidedText(text,Qt.TextElideMode.ElideRight,self.width())
   painter.drawText(self.rect(),Qt.AlignmentFlag.AlignVCenter,text)
  painter.end()
 def mousePressEvent(self,event):
  if event.button()==Qt.MouseButton.LeftButton:self.edit_requested.emit();event.accept()
  else:super().mousePressEvent(event)

class FuSpinBox(QSpinBox):
 """Direct fu input, keeping the existing score-editor selection interface."""
 currentIndexChanged=Signal(int)
 values=(20,25,*range(30,141,10))
 def __init__(self):
  super().__init__();quantity_control(self);self.setRange(20,140);self.setSingleStep(10);self.setKeyboardTracking(False);self.setValue(30);self.valueChanged.connect(lambda _:self.currentIndexChanged.emit(self.currentIndex()))
 def currentData(self):return self.value()
 def currentIndex(self):return self.findData(self.value())
 def findData(self,value):return self.values.index(value) if value in self.values else -1
 def setCurrentIndex(self,index):
  if 0<=index<len(self.values):self.setValue(self.values[index])
 def stepBy(self,steps):
  index=self.currentIndex();self.setValue(self.values[max(0,min(len(self.values)-1,index+steps))])
 def validate(self,value,pos):
  text=value.strip()
  if text in tuple(map(str,self.values)):return QValidator.State.Acceptable,value,pos
  if not text or any(str(number).startswith(text) for number in self.values):return QValidator.State.Intermediate,value,pos
  return QValidator.State.Invalid,value,pos
def tile_icon(code,side=False):
 img=QImage(39,45,QImage.Format.Format_ARGB32_Premultiplied);img.fill(Qt.GlobalColor.transparent);p=QPainter(img);p.setRenderHint(QPainter.RenderHint.Antialiasing);tile(p,code,4,3,1.05,side);p.end();return QIcon(QPixmap.fromImage(img))

@lru_cache(maxsize=148)
def picker_tile_icon(code,resolution=100):
 """Cache four palette resolution sets, keeping enlarged SVG faces crisp."""
 img=QImage(resolution,round(resolution*36/25),QImage.Format.Format_ARGB32_Premultiplied);img.fill(Qt.GlobalColor.transparent);p=QPainter(img);p.setRenderHint(QPainter.RenderHint.Antialiasing);tile(p,code,0,0,resolution/25);p.end();return QIcon(QPixmap.fromImage(img))

def tile_face_button(code,fn,scale=1.):
 size=QSize(round(25*scale),round(36*scale))
 control=button('',fn);control.setObjectName('tileFace');control.setIcon(picker_tile_icon(code));control.setIconSize(size);control.setFixedSize(size)
 control.setStyleSheet('QPushButton,QPushButton:hover,QPushButton:pressed,QPushButton:disabled{padding:0;margin:0;border:0;border-radius:0;min-height:0;background:transparent}')
 return control

CHOICE_BUTTON_STYLE='QPushButton{padding:3px 4px;min-height:28px}'

class SeatChoiceButton(QPushButton):
 def __init__(self,label,literal=True):
  super().__init__(label);self.full_label=label;self.literal=literal;self.setToolTip(label)
 def resizeEvent(self,event):
  super().resizeEvent(event);label=self.full_label if self.literal else tr(self.full_label);self.setText(self.fontMetrics().elidedText(label,Qt.TextElideMode.ElideRight,max(0,self.width()-14)))

class SeatButtonSelector(QWidget):
 """Visible choices with the desk's existing selector interface."""
 currentIndexChanged=Signal(int)
 def __init__(self,parent=None,columns=2,literal=True):
  super().__init__(parent);self.columns=columns;self.literal=literal;self._choice=QComboBox(self);self._choice.setProperty('i18nLiteral',literal);self._choice.hide();self.buttons=[];self.grid=QGridLayout(self);self.grid.setContentsMargins(0,0,0,0);self.grid.setSpacing(3);self._choice.currentIndexChanged.connect(self._changed)
 def clear(self):
  self._choice.clear();clear_layout(self.grid);self.buttons.clear()
 def addItem(self,label,value):
  index=self._choice.count();self._choice.addItem(label,value);control=SeatChoiceButton(label,self.literal);control.setCheckable(True);control.clicked.connect(lambda _,i=index:self.setCurrentIndex(i));control.setProperty('i18nLiteral',self.literal);control.setMinimumWidth(0);control.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);control.setStyleSheet(CHOICE_BUTTON_STYLE);control.setProperty('operatorChoice',True);self.buttons.append(control);self.grid.addWidget(control,index//self.columns,index%self.columns);self._sync()
 def count(self):return self._choice.count()
 def model(self):return self._choice.model()
 def currentIndex(self):return self._choice.currentIndex()
 def currentData(self):return self._choice.currentData()
 def itemText(self,index):return self._choice.itemText(index)
 def itemData(self,index,role=Qt.ItemDataRole.UserRole):return self._choice.itemData(index,role)
 def findData(self,value):return self._choice.findData(value)
 def setCurrentIndex(self,index):self._choice.setCurrentIndex(index);self._sync()
 def _changed(self,index):self._sync();self.currentIndexChanged.emit(index)
 def _sync(self):
  for i,control in enumerate(self.buttons):
   control.setChecked(i==self.currentIndex());entry=self._choice.model().item(i);control.setEnabled(entry is None or entry.isEnabled())

def clear_layout(layout):
 while layout.count():
  item=layout.takeAt(0)
  if item.widget():item.widget().hide();item.widget().deleteLater()
  elif item.layout():clear_layout(item.layout())
def fill_players(c,model,value,exclude=None):
 c.setProperty('i18nLiteral',True)
 block=QSignalBlocker(c);c.clear()
 for i,p in enumerate(model.state['players']):
  c.addItem(tr(WINDS[(i-model.state['dealer'])%4])+' '+p['name'],i)
  if i==exclude:c.model().item(i).setEnabled(False)
 c.setCurrentIndex(value);del block

from settings_window import SettingsWindow, form_values, guard_settings_input_wheel, saves_section

class ProfilesDialog(SettingsWindow):
 def __init__(self,main):
  super().__init__(main);self.main=main;self.setWindowTitle('选手');self.setMinimumWidth(820);layout=QVBoxLayout(self);grid=QGridLayout();self.fields=[]
  for col,name in enumerate(['图片','姓名','点数','队伍','框色','队色']):grid.addWidget(QLabel(name),0,col)
  for i,p in enumerate(main.model.state['players']):
   pic=button('选择');pic.setFixedWidth(75);name=QLineEdit(p['name']);name.setMaxLength(30);score=spin(display_score(main.model.state,i,main.model.settings.get('riichi_payment_mode')),-1000000000,1000000000,100);team=QLineEdit(p.get('team',''));team.setMaxLength(30);frame=ColorInput(p['color'],alpha=False);teamc=ColorInput(p['team_color'],alpha=False)
   if (main.model.event or {}).get('kind')=='team_league':
    team.setReadOnly(True);team.setToolTip(tr('请在赛事工作台的选手名册中选择所属队伍'))
    from event_roster import selected_profile
    profile=selected_profile(main.model.state,p['id']) or {}
    if profile.get('follow_team_color'):
     teamc.setEnabled(False)
     teamc.setToolTip(tr('此选手跟随队色，可在赛事工作台修改颜色来源'))
   fields={'photo':p.get('photo',''),'name':name,'score':score,'team':team,'color':frame,'team_color':teamc,'picture_button':pic};self.fields.append(fields)
   for col,w in enumerate([pic,name,score,team,frame,teamc]):grid.addWidget(w,i+1,col)
   pic.clicked.connect(lambda _,j=i:self.pick_photo(j))
   pic.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);pic.customContextMenuRequested.connect(lambda position,j=i:self.photo_menu(j,position));self.refresh_photo_icon(i)
  layout.addLayout(grid);buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存');buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消');buttons.accepted.connect(self.apply);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
  buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('关闭')
  self.track_settings('profiles',lambda:[form_values(f['name'],f['score'],f['team'],f['color'],f['team_color'],f['photo']) for f in self.fields],self.apply)
 def pick_color(self,i,key):
  self.fields[i][key].choose()
 def refresh_photo_icon(self,i):
  from identity_media import identity_preview_image
  field=self.fields[i];profile=dict(self.main.model.state['players'][i],photo=field['photo'],team=field['team'].text())
  field['picture_button'].setIcon(QIcon(QPixmap.fromImage(identity_preview_image(self.main.model,profile,44,52))));field['picture_button'].setIconSize(QSize(44,52))
 def photo_menu(self,i,position):
  control=self.fields[i]['picture_button'];menu=QMenu(control);clear=menu.addAction(tr('清除照片'))
  if menu.exec(control.mapToGlobal(position))==clear:self.fields[i]['photo']='';self.refresh_photo_icon(i)
 def pick_photo(self,i):
  path,_=QFileDialog.getOpenFileName(self,'选手图片','','图片 (*.png *.jpg *.jpeg *.webp)')
  if not path:return
  img=QImage(path)
  if img.isNull():self.main.error('无法读取图片');return
  from media_store import archive_import_file,import_media_file
  try:archive_import_file(self.main.model.path,path,category='images')
  except (OSError,ValueError) as error:self.main.error(str(error));return
  fitted=img.scaled(360,495,Qt.AspectRatioMode.KeepAspectRatioByExpanding,Qt.TransformationMode.SmoothTransformation);cropped=fitted.copy((fitted.width()-360)//2,max(0,(fitted.height()-495)//3),360,495)
  temporary=self.main.model.path/('portrait-crop-'+uuid.uuid4().hex+'.jpg')
  try:
   if not cropped.save(str(temporary),'JPG',88):raise OSError('无法保存裁切照片')
   self.fields[i]['photo']=import_media_file(self.main.model.path,temporary,'photo');self.refresh_photo_icon(i)
  except (OSError,ValueError) as error:self.main.error(str(error))
  finally:temporary.unlink(missing_ok=True)
 @saves_section('profiles')
 def apply(self):
  def change(s):
   for i,f in enumerate(self.fields):
    name=f['name'].text().strip()
    if not name:raise ValueError('姓名不能为空')
    credit=display_score(s,i,self.main.model.settings.get('riichi_payment_mode'))-s['players'][i]['score']
    s['players'][i].update(name=name,score=f['score'].value()-credit,photo=f['photo'],color=f['color'].text())
    if f['team_color'].isEnabled():s['players'][i]['team_color']=f['team_color'].text()
    if not f['team'].isReadOnly():s['players'][i]['team']=f['team'].text().strip()
   s['accountingTotal']=sum(p['score'] for p in s['players'])+s['pot']
  try:self.main.model.update(change);return True
  except Exception as e:self.main.error(str(e));return False

class PlayerLayoutDialog(SettingsWindow):
 def __init__(self,main):
  super().__init__(main);self.main=main;self.setWindowTitle('四家布局');self.setMinimumWidth(540);v=QVBoxLayout(self)
  x,y,width,gap=main.manager.player_group_geometry();self.x=spin(x,-20000,20000);self.y=spin(y,-20000,20000);self.width=WindowScaleSpinBox(width,reference_width(main.manager.windows['player0']),120,2400);self.gap=spin(gap,-100,1000)
  self.gap.setSuffix(' px');v.addLayout(row('整体 X',self.x,'整体 Y',self.y));v.addLayout(row('四家尺寸比例',self.width,'间距',self.gap))
  self.side_padding=decimal(0,15,1);self.side_padding.setValue(main.model.settings.get('player_side_padding',2.3));self.side_padding.setSuffix(' %')
  self._layout_baseline=form_values(self.x,self.y,self.width,self.gap)
  v.addLayout(row('两侧留白',self.side_padding,1))
  self.note=QLabel('100% 为1920×1080播出画面的原始设计尺寸，随显示器缩放换算。两侧留白扩展透明画布，保持卡片大小和左右对称；积分／排名同步应用。窗口透明边缘可略微重叠。四家窗口可整体移动，Ctrl＋滚轮整体缩放。');self.note.setWordWrap(True);v.addWidget(self.note)
  v.addLayout(row(button('缩小',lambda:self.scale(1/1.06)),button('放大',lambda:self.scale(1.06)),1,button('保存',self.apply),button('关闭',self.close)))
  self.track_settings('layout',lambda:form_values(self.x,self.y,self.width,self.gap,self.side_padding),self.apply)
 def scale(self,factor):
  self.width.setValue(self.width.value()*factor);self.gap.setValue(round(self.gap.value()*factor))
 @saves_section('layout')
 def apply(self):
  layout=form_values(self.x,self.y,self.width,self.gap)
  try:
   if layout!=self._layout_baseline:
    self.main.manager.set_player_group(self.x.value(),self.y.value(),self.width.pixel_width(),self.gap.value(),settings_patch={'player_side_padding':self.side_padding.value()})
   else:
    settings=copy.deepcopy(self.main.model.settings);settings['player_side_padding']=self.side_padding.value()
    self.main.model.save_preferences(settings)
    self.main.manager.accept_geometry()
  except Exception as error:self.main.error(str(error));return False
  self._layout_baseline=layout
  return True


from settings_dialog import SettingsDialog as LayoutDialog

class Console(QMainWindow):
 def __init__(self,model,manager):
  super().__init__()
  self.setProperty('liveDeskInputs',True);guard_settings_input_wheel()
  self._closing=False
  self.model=model;self.manager=manager;self.selected=0;self.wait_drafts={};self.wait_dirty=set();self.draft=[];self.called_index=0;self.tile_target='meld';self.target_boxes={};self.target_buttons={};self.ready=False;self.yaku_buttons={}
  self.setWindowTitle('麻将转播控制台')
  screen=QApplication.primaryScreen().availableGeometry()
  self.resize(min(1520,screen.width()-40),min(1050,screen.height()-60));self.setMinimumSize(960,620);apply_theme(self,model.settings)
  set_language(model.settings.get('language','zh_CN'));self.language_controller=LanguageController(QApplication.instance());self.language_controller.track(self)
  self.lock=QCheckBox('锁定播出窗口');self.lock.setChecked(model.settings['locked'])
  self.overlays=display_button('显示播出窗口');self.overlays.setChecked(model.settings['show_overlays'])
  self.undo=button('',lambda:self.run(lambda:model.history('undo')));self.redo=button('',lambda:self.run(lambda:model.history('redo')))
  for control in (self.undo,self.redo):control.setIconSize(QSize(22,22));control.setMinimumWidth(36)
  self.language=combo(LANGUAGES);self.language.setProperty('i18nLiteral',True);self.language.setCurrentIndex(self.language.findData(model.settings.get('language','zh_CN')));self.language.currentIndexChanged.connect(self.change_language)
  self.event_label=QLabel();self.event_label.setProperty('i18nLiteral',True);self.event_label.setMaximumWidth(380)
  self.shortcut_button=button('快捷键',lambda:self.shortcuts.open_dialog())
  self.workspace_menu_button=button('工作区');self.workspace_menu=QMenu(self.workspace_menu_button)
  self.workspace_menu.addAction('重新载入当前工作区',self.reload_workspace);self.workspace_menu_button.setMenu(self.workspace_menu)
  if not model.event_store:
   self.workspace_menu.addAction('独立播出设置',lambda:self._open_settings_dialog(output=True))
  header=QWidget();header.setObjectName('consoleHeader');header_layout=QHBoxLayout(header);header_layout.setContentsMargins(12,8,12,8)
  header_layout.addLayout(row(self.event_label,1,self.shortcut_button,self.workspace_menu_button,self.overlays,self.undo,self.redo));self.setMenuWidget(header)
  # These preferences remain available in Settings; the live header stays short.
  self.language.setParent(header);self.language.hide();self.lock.setParent(header);self.lock.hide()
  self.workspace=DockWorkspace(self,model)
  self.build_navigation()
  self.column_layouts=[PanelCollection() for _ in range(3)]
  left,middle,right=self.column_layouts
  self.build_table(left);self.build_score(middle);self.build_meld(middle);self.build_yaku(right)
  for keys,column in ((('players','round','dora','waits'),left),(('score','meld'),middle),(('yaku',),right)):
   for key,widget in zip(keys,column.widgets):self.workspace.add_panel(key,widget.title(),widget)
  for key in ('players','yaku'):self.workspace.scrolls[key].viewport().installEventFilter(self)
  trays=PanelCollection();self.build_picker(trays);self.workspace.add_panel('tiles','牌面',trays.widgets[0]);self.workspace.scrolls['tiles'].viewport().installEventFilter(self)
  self.status=QLabel('');self.status.setObjectName('error');self.statusBar().addWidget(self.status,1);self.statusBar().setSizeGripEnabled(True)
  self.manager.display_configuration.failed.connect(self.error)
  QApplication.instance().installEventFilter(self);self.lock.toggled.connect(lambda v:self.run(lambda:model.setting('locked',v)));self.overlays.toggled.connect(lambda v:self.run(lambda:manager.set_overlays_visible(v)))
  model.changed.connect(self.model_changed);model.display_changed.connect(self.refresh_display)
  quick=PanelCollection();self.build_quick_actions(quick);self.workspace.add_panel('quick','直播快捷操作',self.quick_bar)
  self.build_commentary_panel();self.workspace.add_panel('commentary','术语解释',self.commentary_bar)
  from hand_history_panel import HandHistoryPanel
  self.hand_history=HandHistoryPanel(self);self.workspace.add_panel('history','逐局收支',self.hand_history)
  self.workspace.attach_menu(self.panel_menu);self.workspace.initialize()
  from hotkeys import ShortcutController
  actions={key:widget.click for key,widget in self.quick_actions.items() if key in ('riichi','tenpai','focus_ron','focus_tsumo','draw','next_hand','replay','commentary_show','commentary_hide')}
  actions.update(undo=self.undo.click,redo=self.redo.click,dora=lambda:self.focus_tile_target('dora'),meld=lambda:self.focus_tile_target('meld'),overlays=self.overlays.click,submit_win=self.settle_button.click)
  actions.update({f'seat_{i+1}':lambda j=i:self.select(j) for i in range(4)})
  self.shortcuts=ShortcutController(self,model,actions)
  self.shortcuts.bindings_changed.connect(lambda *_:self.refresh_quick_actions())
  self.ready=True;self.refresh();manager.on_display()
  from automation_bridge import BroadcastAutomation
  self.automation=BroadcastAutomation(model,self)
  from inline_automation import InlineAutomation
  self.inline_automation=InlineAutomation(self)
 def open_recognition_settings(self,player=None):
  if self.model.event_store:
   workspace=self.open_event_settings('output')
   return workspace.output_editor.focus_recognition(player)
  return self._open_settings_dialog(recognition=True,player=player)
 def _open_settings_dialog(self,recognition=False,player=None,output=False):
  from system_settings import SystemSettingsDialog
  output=output or recognition
  dialog=LayoutDialog(self) if output else SystemSettingsDialog(self);self.settings_dialog=dialog
  try:
   self.language_controller.track(dialog)
   if recognition:dialog.focus_recognition(player)
   return dialog.exec()
  finally:
   self.settings_dialog=None
   try:
    if output:
     self.manager.clear_capture_preview(dialog)
     dialog._shutdown_recognition_preview()
   finally:dialog.deleteLater()
 def toggle_player_status(self,index,field):
  def apply():
   if self.recognized_hands[index].editing:raise ValueError('请先采用或取消手牌修正')
   assist=getattr(self,'inline_automation',None);player=self.model.state['players'][index]
   if assist and index in assist.wait_ids and not player.get(field):
    return assist.confirm_waits(index,riichi=field=='riichi' or player.get('riichi',False))
   return self.model.riichi(index) if field=='riichi' else self.model.tenpai(index)
  result=self.run(apply)
  for key,control in (('riichi',self.player_widgets[index][3]),('tenpai',self.player_widgets[index][4])):
   blocker=QSignalBlocker(control);control.setChecked(bool(self.model.state['players'][index].get(key)));del blocker
  self.refresh_quick_actions();return result
 def build_navigation(self):
  self.navigation=QToolBar('工作区',self);self.navigation.setObjectName('operatorNavigation');self.navigation.setMovable(False);self.navigation.setFloatable(False)
  self.addToolBar(Qt.ToolBarArea.LeftToolBarArea,self.navigation)
  self.navigation_scroll=QScrollArea();self.navigation_scroll.setWidgetResizable(True);self.navigation_scroll.setFrameShape(QFrame.Shape.NoFrame);self.navigation_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.navigation_scroll.setFixedWidth(NavigationResizeHandle.DEFAULT_WIDTH);self.navigation_scroll.setMinimumHeight(0);self.navigation_scroll.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Expanding)
  navigation_body=QWidget();navigation_layout=QVBoxLayout(navigation_body);navigation_layout.setContentsMargins(0,0,0,0);navigation_layout.setSpacing(7);self.navigation_scroll.setWidget(navigation_body)
  navigation_frame=QWidget()
  frame_layout=QHBoxLayout(navigation_frame);frame_layout.setContentsMargins(0,0,0,0);frame_layout.setSpacing(0)
  self.navigation_handle=NavigationResizeHandle(self.workspace,self.navigation_scroll,self.navigation,navigation_frame)
  frame_layout.addWidget(self.navigation_scroll);frame_layout.addWidget(self.navigation_handle)
  self.navigation.addWidget(navigation_frame)
  self.navigation_buttons={}
  entries=[('events','赛事库',lambda:self.open_event_workspace(0)),('event_settings','赛事设置',lambda:self.open_event_workspace(1)),('matches','半庄管理',lambda:self.open_event_workspace(2)),('reports','榜单与导出',lambda:self.open_event_workspace(3)),('commit','记录本场积分',self.confirm_event_match),('new_match','新半庄',self.new_event_match),('players','选手资料',self.open_profiles),('broadcast','播出控制',self.open_broadcast),('player_layout','四家布局',self.open_player_layout),('settings','系统设置',self.open_settings)]
  for key,label,callback in entries:
   control=button(label,callback);control.setMinimumWidth(100);control.setObjectName('navigationButton');self.navigation_buttons[key]=control
   if key in ('settings','events','matches','reports','players'):navigation_layout.addWidget(control)
   else:control.setParent(navigation_body);control.hide()
  self.navigation_buttons['events'].setText('赛事管理')
  self.navigation_buttons['reports'].setText('排名与结果')
  self.navigation_buttons['players'].setText('本桌选手资料')
  self.navigation_buttons['matches'].setToolTip('记录本半庄积分，或从当前赛事名册选择四席开始新半庄')
  if getattr(self.model,'ui_only',False):
   for key in ('events','event_settings','matches','reports','commit'):self.navigation_buttons[key].hide()
   self.navigation_buttons['new_match'].show();navigation_layout.addWidget(self.navigation_buttons['new_match'])
  self.font_smaller_button=button('A−',lambda:self.change_console_font_size(-1));self.font_larger_button=button('A+',lambda:self.change_console_font_size(1))
  navigation_layout.addLayout(row(self.font_smaller_button,self.font_larger_button))
  self.player_animation_button=button('选手动画');self.player_animation_menu=QMenu(self.player_animation_button)
  self.player_animation_menu.addAction('选手入场',lambda:self.run(lambda:self.manager.replay_players('entry')))
  self.player_animation_menu.addAction('退场后回场',lambda:self.run(lambda:self.manager.replay_players('cycle')))
  self.player_animation_menu.addAction('收支重播',lambda:self.run(self.manager.replay));self.player_animation_menu.addAction('跳过动画',lambda:self.run(self.manager.finish))
  self.player_animation_button.setMenu(self.player_animation_menu);navigation_layout.addWidget(self.player_animation_button)
  self.panel_menu=QMenu('组件与布局',self)
  self.panel_button=button('组件与布局');self.panel_button.setObjectName('navigationButton');self.panel_button.setMenu(self.panel_menu);self.panel_button.setParent(navigation_body);self.panel_button.hide()
  self.presentation_group=QGroupBox('常用展示');self.presentation_layout=QVBoxLayout(self.presentation_group);self.presentation_layout.setContentsMargins(4,10,4,7);self.presentation_layout.setSpacing(6);navigation_layout.addWidget(self.presentation_group)
  self.corner_group=QGroupBox('赛事标识');self.corner_layout=QVBoxLayout(self.corner_group);self.corner_layout.setContentsMargins(4,10,4,7);self.corner_layout.setSpacing(6);navigation_layout.addWidget(self.corner_group)
  self.sidebar_presets_group=QGroupBox('侧栏快捷展示');self.sidebar_presets_layout=QVBoxLayout(self.sidebar_presets_group);self.sidebar_presets_layout.setContentsMargins(4,10,4,7);self.sidebar_presets_layout.setSpacing(6);navigation_layout.addWidget(self.sidebar_presets_group)
  self.sidebar_close_button=button('关闭侧栏',lambda:self.run(self.manager.force_sidebar_exit));self.sidebar_close_button.setObjectName('navigationButton');self.sidebar_close_button.setToolTip('立即播放侧栏退场动画');self.sidebar_presets_layout.addWidget(self.sidebar_close_button)
  self.sidebar_preset_buttons_layout=QVBoxLayout();self.sidebar_preset_buttons_layout.setSpacing(6);self.sidebar_presets_layout.addLayout(self.sidebar_preset_buttons_layout)
  for group in (self.presentation_group,self.corner_group,self.sidebar_presets_group):group.setStyleSheet('QGroupBox{padding-left:4px;padding-right:4px;}');group.setMinimumWidth(0);group.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
  self.sidebar_presets_manage_button=button('管理',self.open_sidebar_presets);self.sidebar_presets_manage_button.setParent(navigation_body);self.sidebar_presets_manage_button.hide();navigation_layout.addStretch(1);self._sidebar_presets_signature=None;self.refresh_sidebar_presets()
 def refresh_sidebar_presets(self):
  from sidebar_presets import sidebar_presets,preset_available
  from sidebar_presets_dialog import WrappingPresetButton
  self.sidebar_close_button.setEnabled(self.manager.sidebar_quick_close_available())
  presets=sidebar_presets(self.model);signature=(repr(presets),(self.model.event or {}).get('kind'))
  if signature==self._sidebar_presets_signature:return
  self._sidebar_presets_signature=signature;clear_layout(self.sidebar_preset_buttons_layout);self.sidebar_preset_buttons={}
  for preset in presets:
   identity=preset['id'];control=WrappingPresetButton(preset['title']);control.setProperty('i18nLiteral',True);control.setObjectName('navigationButton');control.setToolTip(preset['title']);control.setEnabled(preset_available(self.model,preset));control.clicked.connect(lambda checked=False,identity=identity:self.run(lambda:self.manager.toggle_sidebar_preset(identity)));self.sidebar_preset_buttons_layout.addWidget(control);self.sidebar_preset_buttons[identity]=control
 def change_console_font_size(self,step):
  value=max(9,min(24,int(self.model.settings.get('console_font_size',14))+step))
  self.run(lambda:self.model.setting('console_font_size',value))
 def open_corner_header(self):
  from corner_editor import CornerHeaderDialog
  parent=QApplication.activeModalWidget() or self;visible=bool(getattr(self,'corner_dialog',None) and self.corner_dialog.isVisible())
  if not getattr(self,'corner_dialog',None):self.corner_dialog=CornerHeaderDialog(self,parent);self.language_controller.track(self.corner_dialog)
  elif self.corner_dialog._transient_owner is not parent:
   self.corner_dialog.set_transient_owner(parent)
  if not visible:self.corner_dialog.reload()
  self.corner_dialog.show();self.corner_dialog.raise_();self.corner_dialog.activateWindow();return self.corner_dialog
 def refresh_corner_panel(self,force=False):
  from corner_header import corner_texts
  if not hasattr(self,'corner_bottom_text'):return
  value=corner_texts(self.model.state['broadcast']['config'])[1];identity=(str(self.model.path),(self.model.event or {}).get('id'))
  baseline=getattr(self,'_corner_text_baseline',value);force=force or identity!=getattr(self,'_corner_event_signature',None)
  if force or self.corner_bottom_text.text() in (baseline,value):self.corner_bottom_text.setText(value);baseline=value
  self._corner_text_baseline=baseline;self._corner_event_signature=identity
 def save_corner_text(self):
  from corner_header import corner_texts
  def save():
   current=corner_texts(self.model.state['broadcast']['config'])[1];draft=self.corner_bottom_text.text();baseline=self._corner_text_baseline
   if draft!=baseline:
    if current not in (baseline,draft):raise ValueError('赛事标识已更新，请打开 Logo 设置重新读取')
    self.model.configure_corner_header({'corner_bottom_text':draft})
   self.refresh_corner_panel(force=True)
  return self.run(save)
 def open_sidebar_presets(self):
  from sidebar_presets_dialog import SidebarPresetsDialog
  if not getattr(self,'sidebar_presets_dialog',None):self.sidebar_presets_dialog=SidebarPresetsDialog(self);self.language_controller.track(self.sidebar_presets_dialog)
  if not self.sidebar_presets_dialog.isVisible() and not self.sidebar_presets_dialog.dirty_sections():self.sidebar_presets_dialog.reload()
  self.sidebar_presets_dialog.show();self.sidebar_presets_dialog.raise_();self.sidebar_presets_dialog.activateWindow()
 def open_profiles(self):
  dialog=ProfilesDialog(self)
  try:
   self.language_controller.track(dialog);return dialog.exec()
  finally:dialog.deleteLater()
 def open_player_layout(self):
  dialog=PlayerLayoutDialog(self)
  try:
   self.language_controller.track(dialog);return dialog.exec()
  finally:dialog.deleteLater()
 def open_event_workspace(self,index=0):
  if getattr(self.model,'ui_only',False):
   self.error('当前为独立导播模式。请使用 START-Windows.cmd 打开赛事管理。');return
  from event_workspace import EventWorkspaceDialog
  if not getattr(self,'event_workspace',None):self.event_workspace=EventWorkspaceDialog(self);self.language_controller.track(self.event_workspace)
  self.event_workspace.nav.setCurrentRow(index);self.event_workspace.show();self.event_workspace.raise_();self.event_workspace.activateWindow()
  return self.event_workspace
 def open_event_settings(self,category):
  workspace=self.open_event_workspace(1)
  workspace.select_settings_category(category)
  return workspace
 def confirm_event_match(self):
  self.run(self.model.matches.confirm_finished)
 def new_event_match(self):
  result=self.run(self.model.matches.start_next)
  if result=='schedule':self.open_event_workspace(4)
 def open_settings(self):
  return self._open_settings_dialog()
 def reload_workspace(self):
  from settings_context import SettingsContextChange
  from PySide6.QtWidgets import QMessageBox
  context=SettingsContextChange(self)
  if not context.prepare():return
  answer=QMessageBox.question(self,'重新载入当前工作区','读取磁盘中的最新数据，清除当前未提交的录入和撤销记录，是否继续？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
  if answer!=QMessageBox.StandardButton.Yes:return
  def reload():self.model.reload_current();context.finish()
  self.run(reload)
 def broadcast_panel(self):
  from broadcast_console import BroadcastDialog
  if not getattr(self,'broadcast_dialog',None):
   self.broadcast_dialog=BroadcastDialog(self,workspace_roster=bool(self.model.event_store));self.language_controller.track(self.broadcast_dialog)
  return self.broadcast_dialog
 def build_quick_actions(self,root):
  self.quick_bar=QFrame();self.quick_bar.setObjectName('quickBar')
  self.quick_bar.setProperty('panelBody',True)
  grid=QVBoxLayout(self.quick_bar);grid.setContentsMargins(9,7,9,7);grid.setSpacing(6)
  self.shortcut_controls=QWidget(self.quick_bar);self.shortcut_controls.hide()
  first=QHBoxLayout(self.shortcut_controls);first.setSpacing(6);self.quick_selected=QLabel();self.quick_selected.setProperty('i18nLiteral',True);self.quick_selected.setMinimumWidth(110);first.addWidget(self.quick_selected)
  self.quick_actions={};self.quick_labels={}
  definitions=[
   ('riichi','立直',lambda:self.toggle_player_status(self.selected,'riichi'),True),
   ('tenpai','听牌',lambda:self.toggle_player_status(self.selected,'tenpai'),True),
   ('focus_ron','荣和',lambda:self.focus_win('ron'),False),
   ('focus_tsumo','自摸',lambda:self.focus_win('tsumo'),False),
   ('draw','流局',lambda:self.settle_draw(),False),
   ('draw_declaration','申报',lambda:self.settle_draw(edit=True),False),
   ('next_hand','下一局',lambda:self.run(self.model.next_hand),False),
   ('replay','重播',self.manager.replay,False),
   ('skip','跳过',self.manager.finish,False)]
  for key,label,callback,checkable in definitions:
   control=button(label,callback,checkable);control.setProperty('i18nLiteral',True)
   if key=='draw_declaration':control.setStyleSheet(CHOICE_BUTTON_STYLE)
   else:control.setMinimumWidth(55)
   if key in ('riichi','tenpai'):control.setObjectName(key)
   self.quick_actions[key]=control;self.quick_labels[key]=label
   # Hand progression belongs to the live score panel. The shortcut strip is
   # retained for keyboard actions but intentionally hidden by this workspace.
   (self.hand_actions if key in ('draw','draw_declaration','next_hand') else first).addWidget(control)
  first.addStretch()
  from programme import PAGE_BUTTONS
  self.programme_scroll=QScrollArea(self.quick_bar);self.programme_scroll.setWidgetResizable(True);self.programme_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
  self.programme_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.programme_scroll.setFixedHeight(64)
  body=QWidget();pages=QHBoxLayout(body);pages.setContentsMargins(1,1,1,1);pages.setSpacing(5);self.programme_buttons={}
  for key,label in PAGE_BUTTONS:
   control=button(label,lambda _,page=key:self.run(lambda:self.manager.programme.toggle(page)),key not in ('opening','ending'))
   control.setProperty('i18nLiteral',True);control.setProperty('operatorChoice',True);control.setStyleSheet(CHOICE_BUTTON_STYLE);control.setMinimumWidth(58)
   pages.addWidget(control);self.programme_buttons[key]=control
  self.programme_pause=button('暂停',self.manager.programme.pause_resume,True);self.programme_stop=button('停止',self.manager.programme.stop)
  self.programme_music=button('音乐',lambda:self.manager.programme.music.set_enabled(not self.manager.programme.music.enabled),True)
  for control in (self.programme_pause,self.programme_stop,self.programme_music):control.setStyleSheet(CHOICE_BUTTON_STYLE);control.setMinimumWidth(48);pages.addWidget(control)
  pages.addStretch();self.programme_scroll.setWidget(body);grid.addWidget(self.programme_scroll)
  self.quick_scene=combo([('对局','table'),('晋级战','qualification'),('赛后','review'),('净画面','clean')])
  self.quick_scene.setToolTip('播出场景');self.quick_scene.setMaximumWidth(100);self.quick_scene.activated.connect(lambda _:self.run(lambda:self.manager.apply_scene(self.quick_scene.currentData())))
  self.quick_bottom=combo([('选手信息','players'),('个人积分','individual'),('队伍积分','team')]);self.quick_bottom.setToolTip('底部显示');self.quick_bottom.setMaximumWidth(118)
  self.quick_bottom.activated.connect(lambda _:self.run(lambda:self.manager.set_bottom_mode(self.quick_bottom.currentData())))
  self.presentation_layout.addWidget(self.quick_scene);self.presentation_layout.addWidget(self.quick_bottom)
  self.quick_table_scores=display_button('上方积分');self.quick_conditions=display_button('条件')
  self.quick_table_scores.toggled.connect(lambda visible:self.run(lambda:self.manager.set_extension_visible('table_scores',visible)))
  self.quick_conditions.toggled.connect(lambda visible:self.run(self.manager.show_conditions if visible else self.manager.hide_conditions))
  self.presentation_layout.addWidget(self.quick_table_scores);self.presentation_layout.addWidget(self.quick_conditions)
  self.quick_corner=QCheckBox('显示');self.quick_corner.toggled.connect(lambda visible:self.run(lambda:self.manager.set_extension_visible('corner_header',visible)));self.corner_layout.addWidget(self.quick_corner)
  self.corner_bottom_text=QLineEdit();self.corner_bottom_text.setMaxLength(80);self.corner_bottom_text.setMinimumWidth(0);self.corner_bottom_text.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
  self.corner_layout.addWidget(QLabel('下段文字'));self.corner_layout.addWidget(self.corner_bottom_text)
  self.corner_apply_button=button('应用文字',self.save_corner_text);self.corner_logo_button=button('Logo 设置',self.open_corner_header);self.corner_layout.addWidget(self.corner_apply_button);self.corner_layout.addWidget(self.corner_logo_button);self.refresh_corner_panel(force=True)
  self.presentation_layout.addWidget(self.show_melds)
  self.quick_postmatch_play=self.programme_buttons['ending']
  self.quick_postmatch_pause=self.programme_pause
  self.quick_postmatch_stop=self.programme_stop
  self.quick_postmatch_status=QLabel();self.quick_postmatch_status.setProperty('i18n_literal',True);self.quick_postmatch_status.setWordWrap(True);self.quick_postmatch_status.setMinimumWidth(0)
  policy=self.quick_postmatch_status.sizePolicy();policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored);self.quick_postmatch_status.setSizePolicy(policy)
  self.quick_postmatch_status.setParent(self.shortcut_controls);self.quick_postmatch_status.hide()
  self.manager.programme.changed.connect(self.refresh_postmatch_actions)
  self.manager.programme.music.failed.connect(self.error)
  self.manager.postmatch.failed.connect(self.error)
  root.insertWidget(1,self.quick_bar)
 def refresh_postmatch_actions(self):
  from programme import PAGE_BUTTONS
  controller=self.manager.programme;key=controller.current_key;playback=self.manager.postmatch
  for page,control in self.programme_buttons.items():
   control.setText(tr(dict(PAGE_BUTTONS)[page]))
   control.setEnabled(not page.startswith('ending') or self.model.state.get('phase')=='finished')
   blocker=QSignalBlocker(control);control.setChecked(page==key);del blocker
  active=key is not None or controller.music.enabled or playback.preparing
  self.programme_pause.setEnabled(active and not playback.preparing);self.programme_stop.setEnabled(active)
  self.programme_pause.setText(tr('继续' if controller.paused else '暂停'))
  self.programme_stop.setText(tr('停止'));self.programme_music.setText(tr('音乐'))
  music_available=bool(self.model.state['broadcast']['config'].get('postmatch_music_path')) or controller.music.enabled
  self.programme_music.setEnabled(music_available)
  for control,value in ((self.programme_pause,controller.paused),(self.programme_music,controller.music.enabled)):
   blocker=QSignalBlocker(control);control.setChecked(value);del blocker
  self.quick_postmatch_status.setText(playback.status_text())
  self.programme_music.setToolTip(tr('独立播放背景音乐' if music_available else '请先选择本地音乐'))
 def build_commentary_panel(self):
  self.commentary_bar=QFrame();layout=QVBoxLayout(self.commentary_bar);layout.setContentsMargins(9,7,9,7);layout.setSpacing(6)
  self.quick_terms=self.broadcast_panel().create_quick_terms_widget(self.commentary_bar,columns=3);layout.addWidget(self.quick_terms)
  controls=QHBoxLayout()
  for key,label,callback in [
   ('commentary_show','上屏',self.show_current_commentary),
   ('commentary_hide','收起',lambda:self.run(lambda:self.manager.set_extension_visible('commentary',False)))]:
   control=button(label,callback);control.setProperty('i18nLiteral',True);self.quick_actions[key]=control;self.quick_labels[key]=label;controls.addWidget(control)
  self.term_manager_button=button('编辑词库',self.open_commentary);controls.addWidget(self.term_manager_button)
  self.term_layout_button=button('按钮布局',self.broadcast_panel().edit_term_buttons);controls.addWidget(self.term_layout_button)
  layout.addLayout(controls)
 def focus_win(self,kind):
  self.win_kind(kind);self.workspace.ensure_visible(self.score_box)
  self.han.setFocus();self.han.selectAll()
 def open_commentary(self):
  if self.model.event_store:return self.open_event_settings('commentary')
  self.open_broadcast();self.broadcast_panel().select_category('commentary')
 def show_current_commentary(self):
  self.broadcast_panel().show_commentary()
 def refresh_quick_actions(self):
  if not hasattr(self,'quick_actions'):return
  self.refresh_postmatch_actions()
  s=self.model.state;p=s['players'][self.selected];playing=s.get('phase')=='playing'
  self.quick_selected.setText(str(self.selected+1)+' · '+p['name'])
  hints={'draw':'按当前听牌状态结算。有人盖牌或需补录听牌时，请点旁边的“申报”。',
         'draw_declaration':'核对四席实际亮牌申报，再确认流局'}
  for key,label in self.quick_labels.items():
   control=self.quick_actions[key];control.setText(tr(label));key_label=self.shortcuts.key_label(key) if hasattr(self,'shortcuts') else ''
   control.setToolTip(tr(hints.get(key,label))+('  ['+key_label+']' if key_label else ''))
  self.quick_actions['draw_declaration'].setAccessibleName(tr('流局申报'))
  for key in ('riichi','tenpai','draw','draw_declaration'):self.quick_actions[key].setEnabled(playing)
  for key,field in [('riichi','riichi'),('tenpai','tenpai')]:
   blocker=QSignalBlocker(self.quick_actions[key]);self.quick_actions[key].setChecked(bool(p.get(field)));del blocker
  self.quick_actions['next_hand'].setEnabled(s.get('phase')=='result' and not (s.get('lastResult') or {}).get('next',{}).get('finished',False))
  self.quick_actions['replay'].setEnabled(bool(self.model.last_transition))
  from broadcast_console import refresh_bottom_selector
  refresh_bottom_selector(self.quick_bottom,self.manager)
  for widget,value in ((self.quick_table_scores,self.manager.enabled.get('table_scores',False)),(self.quick_conditions,self.manager.conditions_visible()),(self.quick_corner,self.manager.enabled.get('corner_header',False))):
   blocker=QSignalBlocker(widget);widget.setChecked(value);del blocker
  from sidebar_paging import is_team_event
  # A team event cannot turn this individual-only strip on, but must still be
  # able to clear a visible setting inherited from an older scene or session.
  self.quick_table_scores.setEnabled(not is_team_event(self.model) or self.quick_table_scores.isChecked())
  for widget,value in [(self.quick_scene,self.model.settings.get('broadcast_scene','table'))]:
   blocker=QSignalBlocker(widget);widget.setCurrentIndex(max(0,widget.findData(value)));del blocker
  if hasattr(self,'shortcuts'):self.shortcuts.refresh()
  self.refresh_history_controls()
 def refresh_history_controls(self):
  theme=self.model.settings.get('console_theme','dark')
  for key,label in (('undo','撤销'),('redo','恢复已撤销操作')):
   control=getattr(self,key);control.setIcon(history_arrow_icon(key,theme));control.setAccessibleName(tr(label))
   binding=self.shortcuts.key_label(key) if hasattr(self,'shortcuts') else ''
   control.setToolTip(tr(label)+('  ('+binding+')' if binding else '\n'+tr('未设置快捷键')))
 def change_language(self):
  language=self.language.currentData() or 'zh_CN'
  try:self.model.setting('language',language)
  except Exception as error:
   self.refresh_display();self.error(str(error));return
  set_language(language)
  if getattr(self,'broadcast_dialog',None):self.broadcast_dialog.refresh_language()
  self.refresh();localize_widgets(self)
 def open_broadcast(self):
  if self.model.event_store:return self.open_event_settings('presentation')
  panel=self.broadcast_panel();panel.show();panel.refresh_language();localize_widgets(panel);panel.raise_();panel.activateWindow()
 def error(self,message):self.status.setText(tr(message));QTimer.singleShot(6000,self.status,self.status.clear)
 def model_changed(self,before,after,kind):
  if kind in ('manual_result','commentary','delete_match_record'):
   # Another table's result must not rebuild an operator's in-progress inputs.
   self.refresh_quick_actions();return
  if kind=='import':
   set_language(self.model.settings.get('language','zh_CN'))
   self.refresh_corner_panel(force=True)
  if kind in ('next_hand','clear','new_match','start_match','import'):
   self._automation_yakuman=None
   self.ready=False;self.draft=[];self.called_index=0;self.wait_drafts.clear();self.wait_dirty.clear()
   for b in self.yaku_buttons.values():b.setChecked(False)
   for count in self.bonuses:count.setValue(0)
   self.linked.setChecked(False);self.han.setValue(1);self.fu.setCurrentIndex(self.fu.findData(30));self.ready=True
  if kind=='restore':
   # Undo/redo restores the published table, including every seat's waits.
   # A pending empty draft must not conceal the restored tiles in the editor.
   self.wait_drafts.clear();self.wait_dirty.clear()
  if kind=='waits':
   self.refresh_wait_editor();self.refresh_player_waits()
   for i,(_,_,_,riichi,tenpai) in enumerate(self.player_widgets):
    tenpai.setChecked(after['players'][i]['tenpai']);self.draw_buttons[i].setChecked(after['players'][i]['tenpai'])
   self.undo.setEnabled(bool(self.model.undo));self.redo.setEnabled(bool(self.model.redo));self.refresh_quick_actions();self.preview();return
  self.refresh()
  if kind not in ('next_hand','clear','new_match','start_match','import','restore','hand_correction'):
   activated=next((i for i,(a,b) in enumerate(zip(before['players'],after['players'])) if (b['riichi'] and not a['riichi']) or (b.get('tenpai') and not a.get('tenpai'))),None)
   if activated is not None:
    self.select(activated);self.set_tile_target('waits');self.workspace.ensure_visible(self.target_boxes['waits'])
 def run(self,fn):
  try:return fn()
  except Exception as e:
   if self.ready:self.refresh_display()
   self.error(str(e));return None
 def build_table(self,left):
  g,v=box('局况');self.round_box=g;self.round=combo([(WINDS[i//4]+str(i%4+1)+'局',i) for i in range(8)]);self.dealer=QComboBox();self.honba=spin();self.pot=spin(0,0,1000000)
  self.match_title=QLineEdit(self.model.state.get('title',''));self.match_title.setMaxLength(80);self.match_title.setPlaceholderText('A1联赛 第1节');self.match_title.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);v.addLayout(row('比赛',self.match_title))
  self.match_title.editingFinished.connect(lambda:self.field('title',self.match_title.text().strip()) if self.match_title.text().strip()!=self.model.state.get('title','') else None)
  self.round_field_rows=[]
  for names,controls in ((('局','庄家'),(self.round,self.dealer)),(('本场','供托'),(self.honba,self.pot))):
   fields=QGridLayout();fields.setHorizontalSpacing(4);fields.setVerticalSpacing(5);pairs=[]
   for label,control in zip(names,controls):
    caption=QLabel(label);caption.setSizePolicy(QSizePolicy.Policy.Maximum,QSizePolicy.Policy.Preferred);pairs.append((caption,control))
    control.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
   self.round_field_rows.append((fields,pairs));v.addLayout(fields)
  for control in (self.round,self.dealer):control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
  left.addWidget(g)
  self.round.currentIndexChanged.connect(lambda:self.field('roundIndex',self.round.currentData()));self.dealer.currentIndexChanged.connect(lambda:self.field('dealer',self.dealer.currentData()));self.honba.valueChanged.connect(lambda v:self.field('honba',v));self.pot.valueChanged.connect(lambda v:self.field('pot',v*1000))
  g,v=box('选手');self.player_grid=QGridLayout();self.player_grid.setSpacing(3);self.player_widgets=[];self.recognized_hands=[];self.player_wait_widgets=[]
  from recognized_hand import RecognizedHandWidget
  for i in range(4):
   panel=QFrame();panel.setObjectName('playerControl');body=QVBoxLayout(panel);body.setContentsMargins(3,0,3,0);body.setSpacing(0);pv=QHBoxLayout();pv.setContentsMargins(0,0,0,0);pv.setSpacing(4);select=button('',lambda _,j=i:self.select(j));select.setProperty('i18nLiteral',True);select.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);score=spin(0,-1000000000,1000000000,100);score.setMinimumWidth(85);score.setMaximumWidth(125);score.editingFinished.connect(lambda j=i:self.edit_score(j));riichi=button('立直',lambda _,j=i:self.toggle_player_status(j,'riichi'),True);riichi.setObjectName('riichi');tenpai=button('听牌',lambda _,j=i:self.toggle_player_status(j,'tenpai'),True);tenpai.setObjectName('tenpai');riichi.setFixedWidth(48);tenpai.setFixedWidth(48);pv.addWidget(select,1);pv.addWidget(score);pv.addWidget(riichi);pv.addWidget(tenpai);body.addLayout(pv);hand=RecognizedHandWidget(i);body.addWidget(hand);self.recognized_hands.append(hand);self.player_grid.addWidget(panel,i,0);self.player_widgets.append((panel,select,score,riichi,tenpai))
   score.valueChanged.connect(lambda _,j=i:self.edit_score(j));score.setToolTip('每次增减 100 点，也可直接输入点数')
   hand.edit_requested.connect(self.edit_recognized_hand)
   if hasattr(hand,'edit_finished'):hand.edit_finished.connect(self.hand_edit_finished)
   if hasattr(hand,'selection_changed'):hand.selection_changed.connect(self.hand_selection_changed)
   waits=PlayerWaitTiles();waits.edit_requested.connect(lambda j=i:self.edit_player_waits(j))
   remaining=OptionalCountSpinBox();remaining.setFixedWidth(108);remaining.setAccessibleName('山存量');remaining.setToolTip('修改后立即生效；清空表示未知')
   remaining.valueChanged.connect(lambda value,j=i:self.change_player_remaining(j,value))
   wait_line=row(waits,'山存',remaining);wait_line.setSpacing(3);body.addLayout(wait_line)
   self.player_wait_widgets.append((waits,remaining))
  v.addLayout(self.player_grid)
  left.insertWidget(0,g)
  g,v=box('宝牌');self.target_boxes['dora']=g
  self.dora_board=QWidget();self.dora_board.setMinimumSize(1,1);self.dora_board.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored);self.dora_board.installEventFilter(self)
  self.dora_row=QHBoxLayout(self.dora_board);self.dora_row.setContentsMargins(0,0,0,0);self.dora_row.setSpacing(7);self.dora_row.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
  v.addWidget(self.dora_board,1);left.addWidget(g)
  g,v=box('听牌');self.target_boxes['waits']=g;self.wait_owner=QLabel();self.wait_owner.setProperty('i18nLiteral',True);self.wait_enabled=QCheckBox('显示听牌');v.addLayout(row(self.wait_owner,self.wait_enabled))
  self.remaining=OptionalCountSpinBox();self.remaining.setFixedWidth(108);self.remaining.setToolTip('首次随听牌提交；已播出后修改山存数量立即生效');self.clear_remaining_button=button('清除',lambda:self.remaining.setValue(-1));remaining_row=row('山存',self.remaining,self.clear_remaining_button);remaining_row.setSpacing(3);v.addLayout(remaining_row)
  self.wait_no_yaku=button('无役',checkable=True);self.furiten=button('振听',checkable=True)
  for control in (self.wait_no_yaku,self.furiten):control.setProperty('operatorChoice',True);control.setStyleSheet(CHOICE_BUTTON_STYLE)
  self.wait_no_yaku.setToolTip('开启后，点击已有待牌切换有役／无役，新增待牌直接设为无役');self.furiten.setToolTip('选中为振听，再次点击取消；随听牌草稿一起提交')
  v.addLayout(row(self.wait_no_yaku,self.furiten));self.furiten.toggled.connect(self.change_furiten);self.wait_no_yaku.toggled.connect(lambda _:self.refresh_wait_editor())
  self.wait_row=QGridLayout();self.wait_row.setSpacing(7);self.wait_row.setAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop);v.addLayout(self.wait_row)
  self.wait_preview_label=QLabel();self.wait_preview_label.setObjectName('draftPreview');self.wait_preview_label.setProperty('i18nLiteral',True);self.wait_preview_label.setWordWrap(True);v.addWidget(self.wait_preview_label)
  self.clear_wait_button=button('清空待牌',self.clear_wait_draft);self.submit_wait_button=button('提交听牌',self.submit_waits);self.submit_wait_button.setObjectName('primary');self.submit_wait_button.setToolTip('按当前草稿更新该选手；立直请直接点击该选手的立直按钮');v.addLayout(row(self.clear_wait_button,self.submit_wait_button));left.addWidget(g)
  self.remaining.valueChanged.connect(self.change_remaining);self.wait_enabled.toggled.connect(lambda value:self.run(lambda:self.model.set_tenpai(self.selected,value)) if self.ready else None)
 def edit_player_waits(self,index):
  self.select(index);self.focus_tile_target('waits');self.workspace.ensure_visible(self.target_boxes['waits'])
 def refresh_player_waits(self):
  for index,(waits,remaining) in enumerate(self.player_wait_widgets):
   player=self.model.state['players'][index];waits.set_player(player)
   blocker=QSignalBlocker(remaining);remaining.setValue(-1 if player.get('remaining') is None else player['remaining']);del blocker
   remaining.setEnabled(self.model.state['phase']=='playing' and not self.recognized_hands[index].editing)
 def change_player_remaining(self,index,value):
  if not self.ready:return
  remaining=None if value<0 else value
  if remaining==self.model.state['players'][index].get('remaining'):return
  draft=self.wait_drafts.get(index);previous=draft['remaining'] if draft is not None else None
  if draft is not None:draft['remaining']=remaining
  try:self.model.set_remaining(index,remaining)
  except Exception as error:
   if draft is not None:draft['remaining']=previous
   self.refresh_player_waits();self.refresh_wait_editor();self.error(str(error))
 def edit_score(self,index):
  if not self.ready:return
  value=self.player_widgets[index][2].value()
  if value!=display_score(self.model.state,index,self.model.settings.get('riichi_payment_mode')):
   self.run(lambda:self.model.set_score(index,value))
   actual=display_score(self.model.state,index,self.model.settings.get('riichi_payment_mode'))
   if self.player_widgets[index][2].value()!=actual:
    control=self.player_widgets[index][2];blocker=QSignalBlocker(control);control.setValue(actual);del blocker
 def wait_draft(self):
  if self.selected not in self.wait_drafts:
   player=self.model.state['players'][self.selected];self.wait_drafts[self.selected]={'waits':list(player['waits']),'remaining':player['remaining'],'no_yaku':list(player.get('waits_no_yaku',[])),'furiten':player.get('furiten',False)}
  return self.wait_drafts[self.selected]
 def refresh_wait_editor(self):
  if self.selected not in self.wait_dirty:self.wait_drafts.pop(self.selected,None)
  draft=self.wait_draft();player=self.model.state['players'][self.selected];self.wait_owner.setText(player['name'])
  for widget,value in ((self.remaining,-1 if draft['remaining'] is None else draft['remaining']),(self.wait_enabled,player['tenpai']),(self.furiten,draft['furiten'])):
   blocker=QSignalBlocker(widget)
   if widget is self.remaining:widget.setValue(value)
   else:widget.setChecked(value)
   del blocker
  self.clear_remaining_button.setEnabled(draft['remaining'] is not None);clear_layout(self.wait_row)
  for i,code in enumerate(draft['waits']):
   action='左键切换有役／无役' if self.wait_no_yaku.isChecked() else '左键移除 / 右键切换有役'
   control=tile_face_button(code,lambda _,j=i:self.click_wait(j),tile_scale(self.model.settings,'wait'));control.setToolTip(tile_name(code)+' · '+('无役' if code in draft['no_yaku'] else '有役')+' · '+action);control.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);control.customContextMenuRequested.connect(lambda _,t=code:self.toggle_wait_yaku(t));self.wait_row.addWidget(control,i//7,i%7)
   if code in draft['no_yaku']:
    effect=QGraphicsOpacityEffect(control);effect.setOpacity(.35);control.setGraphicsEffect(effect)
  self.reflow_wait_tiles();self.refresh_wait_preview()
  self.submit_wait_button.setEnabled(self.model.state['phase']=='playing' and not self.recognized_hands[self.selected].editing)
 def reflow_wait_tiles(self):
  if not hasattr(self,'wait_row'):return
  width=self.target_boxes['waits'].contentsRect().width()-18
  if 'waits' in self.workspace.scrolls:width=min(width,self.workspace.scrolls['waits'].viewport().width()-18)
  tile_width=round(25*tile_scale(self.model.settings,'wait'));columns=max(1,min(7,(max(1,width)+7)//(tile_width+7)))
  controls=[self.wait_row.itemAt(i).widget() for i in range(self.wait_row.count())]
  size=QSize(tile_width,round(36*tile_scale(self.model.settings,'wait')))
  for control in controls:control.ensurePolished();control.setFixedSize(size)
  signature=(columns,tuple(controls))
  if signature==getattr(self,'_wait_tile_layout_signature',None):return
  self._wait_tile_layout_signature=signature
  for control in controls:self.wait_row.removeWidget(control)
  for index,control in enumerate(controls):self.wait_row.addWidget(control,index//columns,index%columns)
 def player_input_blocked_message(self):
  return '半庄已结束，请开始新半庄后录入副露或听牌' if self.model.state['phase']=='finished' else '本局已结算，请进入下一局后录入副露或听牌'
 def refresh_wait_preview(self):
  if self.model.state['phase']!='playing':
   self.wait_preview_label.setText(self.player_input_blocked_message());self.wait_preview_label.setProperty('published',False);self.wait_preview_label.style().unpolish(self.wait_preview_label);self.wait_preview_label.style().polish(self.wait_preview_label);self.wait_preview_label.setToolTip(self.player_input_blocked_message());return
  draft=self.wait_draft();player=self.model.state['players'][self.selected]
  changed=any(draft[key]!=player.get(source,default) for key,source,default in (('waits','waits',[]),('remaining','remaining',None),('no_yaku','waits_no_yaku',[]),('furiten','furiten',False)))
  pending=changed or self.selected in self.wait_dirty
  state='待提交' if pending else '已播出' if player.get('tenpai') else '待提交'
  details=['立直' if player.get('riichi') else '普通听牌']
  if draft['furiten']:details.append('振听')
  if draft['no_yaku']:details.append('无役 '+ '、'.join(tile_name(tile) for tile in draft['no_yaku']))
  if draft['remaining'] is not None:details.append('山存 '+str(draft['remaining']))
  self.wait_preview_label.setText(state+' · '+' · '.join(details) if draft['waits'] else ('待提交 · 清除当前待牌' if player.get('waits') else '选择待牌，或等待手牌识别'))
  self.wait_preview_label.setProperty('published',state=='已播出');self.wait_preview_label.style().unpolish(self.wait_preview_label);self.wait_preview_label.style().polish(self.wait_preview_label)
  self.wait_preview_label.setToolTip('上方牌面即本次提交内容；暗牌为无役。开启“无役”后点击牌面可切换有役／无役，也可右键切换。')
 def clear_wait_draft(self):
  self.wait_draft()['waits']=[];self.wait_draft()['no_yaku']=[];self.wait_dirty.add(self.selected);self.refresh_wait_editor()
 def change_furiten(self,value):
  if self.ready:self.wait_draft()['furiten']=value;self.wait_dirty.add(self.selected);self.refresh_wait_preview()
 def toggle_wait_yaku(self,code):
  draft=self.wait_draft()
  if code in draft['no_yaku']:draft['no_yaku'].remove(code)
  else:draft['no_yaku'].append(code)
  self.wait_dirty.add(self.selected);self.refresh_wait_editor()
 def click_wait(self,index):
  if self.wait_no_yaku.isChecked():self.toggle_wait_yaku(self.wait_draft()['waits'][index])
  else:self.remove_wait(index)
 def submit_waits(self):
  if self.model.state['phase']!='playing':self.error(self.player_input_blocked_message());return
  if self.recognized_hands[self.selected].editing:
   self.error('请先采用或取消手牌修正');return
  if hasattr(self,'inline_automation') and self.selected in self.inline_automation.wait_ids:
   self.run(lambda:self.inline_automation.confirm_waits(self.selected));return
  draft=copy.deepcopy(self.wait_draft());index=self.selected
  def submit():
   dirty=index in self.wait_dirty;self.wait_dirty.discard(index)
   try:self.model.submit_waits(index,draft['waits'],draft['remaining'],draft['no_yaku'],draft['furiten'])
   except Exception:
    self.wait_drafts[index]=draft
    if dirty:self.wait_dirty.add(index)
    self.refresh_wait_editor();raise
  self.run(submit)
 def build_picker(self,root):
  g,v=box('牌面');self.picker_box=g;g.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding);g.installEventFilter(self);v.setSpacing(2);self.picker_target=QLabel();self.picker_target.setObjectName('muted');self.picker_target.setMinimumWidth(0);self.picker_target.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);v.addWidget(self.picker_target)
  self.picker_header=QWidget();self.picker_header.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);header=QGridLayout(self.picker_header);header.setContentsMargins(0,0,0,0);header.setSpacing(3);self.picker_target_keys=('meld','dora','waits','win_tile','ura','hand')
  for index,(key,title) in enumerate(zip(self.picker_target_keys,('副露','宝牌','听牌','和牌张','里指示','手牌'))):
   b=button(title,lambda _,k=key:self.focus_tile_target(k),True);b.setMinimumWidth(0);b.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);b.setStyleSheet(CHOICE_BUTTON_STYLE);self.target_buttons[key]=b;header.addWidget(b,0,index)
  self.picker_header_grid=header;v.addWidget(self.picker_header)
  self.picker_board=QWidget();self.picker_board.setMinimumSize(10,4);self.picker_board.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored);self.picker_board.installEventFilter(self)
  grid=QGridLayout(self.picker_board);grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint);grid.setContentsMargins(0,0,0,0);grid.setSpacing(0);grid.setAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop);self.tile_buttons={}
  self.picker_rows=[[str(n)+suit for n in range(1,10)]+['0'+suit] for suit in 'mps']+[[str(n)+'z' for n in range(1,8)]]
  ordered=[code for codes in self.picker_rows for code in codes]
  for r,codes in enumerate(self.picker_rows):
   for col,t in enumerate(codes):
    b=button('',lambda _,code=t:self.pick_tile(code));b.setObjectName('tileChoice');b.setIcon(picker_tile_icon(t));b.setIconSize(QSize(46,66));b.setFixedSize(46,66);b.setStyleSheet('QPushButton{padding:0;margin:0;border:0;border-radius:0;min-height:0}');b.setToolTip(tile_name(t));self.tile_buttons[t]=b;grid.addWidget(b,r,col)
  self.picker_grid=grid;self.picker_order=ordered;v.addWidget(self.picker_board,1);root.addWidget(g);self.reflow_picker();self.set_tile_target('meld')
 def reflow_picker(self):
  if not hasattr(self,'picker_board') or getattr(self,'_picker_reflow_busy',False):return
  self._picker_reflow_busy=True
  try:
   # The board owns only the space below the target label/buttons. Both axes
   # therefore constrain the faces without counting the header as tile space.
   self._picker_columns=10;buttons=[self.target_buttons[key] for key in self.picker_target_keys if not self.target_buttons[key].isHidden()]
   if not buttons:buttons=[self.target_buttons[key] for key in self.picker_target_keys]
   available=max(1,self.picker_header.width());columns=1
   for candidate in range(len(buttons),0,-1):
    widths=[max(control.minimumSizeHint().width() for control in buttons[column::candidate]) for column in range(candidate)]
    if sum(widths)+(candidate-1)*self.picker_header_grid.horizontalSpacing()<=available:columns=candidate;break
   signature=(columns,tuple(buttons))
   if signature!=getattr(self,'_picker_header_signature',None):
    self._picker_header_signature=signature
    self._picker_header_columns=columns
    for key in self.picker_target_keys:self.picker_header_grid.removeWidget(self.target_buttons[key])
    for index,control in enumerate(buttons):self.picker_header_grid.addWidget(control,index//columns,index%columns)
   for column in range(len(self.picker_target_keys)):
    self.picker_header_grid.setColumnMinimumWidth(column,widths[column] if column<columns else 0)
    self.picker_header_grid.setColumnStretch(column,1 if column<columns else 0)
   if hasattr(self,'workspace') and 'tiles' in self.workspace.docks:
    dock=self.workspace.docks['tiles'];title=dock.titleBarWidget()
    dock.setMinimumHeight(self.picker_box.minimumSizeHint().height()+(title.sizeHint().height() if title else 24)+2)
   scale=min(self.picker_board.width()/(10*46),self.picker_board.height()/(4*66))
   size=QSize(max(1,int(46*scale)),max(1,int(66*scale)))
   if size!=getattr(self,'_picker_tile_size',None):
    self._picker_tile_size=size
    for control in self.tile_buttons.values():control.setFixedSize(size);control.setIconSize(size)
   pixels=math.ceil(max(size.width(),size.height()*25/36)*self.picker_board.devicePixelRatioF())
   resolution=max(100,((pixels+24)//25)*25)
   if resolution!=getattr(self,'_picker_icon_resolution',None):
    self._picker_icon_resolution=resolution
    for code,control in self.tile_buttons.items():control.setIcon(picker_tile_icon(code,resolution))
  finally:self._picker_reflow_busy=False
 def resizeEvent(self,event):
  super().resizeEvent(event)
  if hasattr(self,'navigation_handle'):self.navigation_handle.fit_width()
  if hasattr(self,'picker_grid'):self.reflow_picker()
  if hasattr(self,'yaku_grid'):self.reflow_live_panels()
 def reflow_dora(self):
  if not hasattr(self,'dora_board') or getattr(self,'_dora_reflow_busy',False):return
  self._dora_reflow_busy=True
  try:
   controls=[self.dora_row.itemAt(i).widget() for i in range(self.dora_row.count()) if self.dora_row.itemAt(i).widget()]
   if not controls:return
   count=len(controls);width=max(1,self.dora_board.width()-(count-1)*self.dora_row.spacing())
   scale=min(width/(count*25),max(1,self.dora_board.height())/36)
   size=QSize(max(1,int(25*scale)),max(1,int(36*scale)))
   pixels=math.ceil(max(size.width(),size.height()*25/36)*self.dora_board.devicePixelRatioF());resolution=max(100,((pixels+24)//25)*25)
   for code,control in zip(self.model.dora_tiles,controls):
    control.setFixedSize(size);control.setIconSize(size);control.setIcon(picker_tile_icon(code,resolution))
  finally:self._dora_reflow_busy=False
 def reflow_round_controls(self):
  """Keep round inputs readable as the desk font or the dock width changes."""
  if not hasattr(self,'round_field_rows') or getattr(self,'_round_reflow_busy',False):return
  self._round_reflow_busy=True
  try:
   margins=self.round_box.layout().contentsMargins();contents=self.round_box.contentsRect()
   width=self.round_box.width()
   if hasattr(self,'workspace') and 'round' in self.workspace.scrolls:width=min(width,self.workspace.scrolls['round'].viewport().width())
   available=max(1,width-(self.round_box.width()-contents.width())-margins.left()-margins.right())
   for fields,pairs in self.round_field_rows:
    for _,control in pairs:
     control.ensurePolished();metrics=control.fontMetrics()
     if isinstance(control,QSpinBox):
      option=QStyleOptionSpinBox();control.initStyleOption(option)
      edit=control.style().subControlRect(QStyle.ComplexControl.CC_SpinBox,option,QStyle.SubControl.SC_SpinBoxEditField,control)
      chrome=max(0,control.width()-edit.width())
      value=max(metrics.horizontalAdvance('0'),metrics.horizontalAdvance(control.text()));padding=8
     else:
      option=QStyleOptionComboBox();control.initStyleOption(option)
      edit=control.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,control)
      chrome=max(0,control.width()-edit.width())
      value=max((metrics.horizontalAdvance(control.itemText(i)) for i in range(control.count())),default=0);padding=2
     control.setMinimumWidth(value+chrome+padding);control.updateGeometry()
    required=sum(label.sizeHint().width()+control.minimumWidth() for label,control in pairs)+3*fields.horizontalSpacing()
    columns=2 if required<=available else 1
    labels_above=columns==1 and any(label.sizeHint().width()+control.minimumWidth()+fields.horizontalSpacing()>available for label,control in pairs)
    if fields.property('roundColumns')!=columns or fields.property('roundLabelsAbove')!=labels_above:
     fields.setProperty('roundColumns',columns);fields.setProperty('roundLabelsAbove',labels_above)
     for label,control in pairs:fields.removeWidget(label);fields.removeWidget(control)
     for column in range(4):fields.setColumnStretch(column,0)
     fields.setColumnStretch(1,1)
     if columns==2:fields.setColumnStretch(3,1)
     for index,(label,control) in enumerate(pairs):
      if labels_above:
       fields.addWidget(label,index*2,0,1,2);fields.addWidget(control,index*2+1,0,1,2)
      else:
       row_index=index//columns;column=(index%columns)*2
       fields.addWidget(label,row_index,column);fields.addWidget(control,row_index,column+1)
   self.round_box.updateGeometry()
  finally:self._round_reflow_busy=False
 def reflow_live_panels(self):
  if not hasattr(self,'workspace') or 'yaku' not in self.workspace.scrolls:return
  self.reflow_round_controls()
  self.reflow_score_numbers()
  self.reflow_wait_tiles()
  available=self.workspace.scrolls['yaku'].viewport().width()-26
  controls=[self.yaku_buttons[name] for name,_,_ in YAKU[:30]]
  columns=1
  for candidate in range(5,0,-1):
   widths=[max(control.minimumSizeHint().width() for control in controls[column::candidate]) for column in range(candidate)]
   if sum(widths)+(candidate-1)*3<=available:columns=candidate;break
  if columns!=getattr(self,'_yaku_columns',None):
   self._yaku_columns=columns
   for i,control in enumerate(controls):self.yaku_grid.removeWidget(control);self.yaku_grid.addWidget(control,i//columns,i%columns)
  compact=self.workspace.scrolls['players'].viewport().width()<340
  for _,_,_,riichi,tenpai in self.player_widgets:
   riichi.setVisible(not compact);tenpai.setVisible(not compact)
  required=self.workspace.scrolls['players'].widget().minimumSizeHint().height()+28
  editing=any(hand.editing for hand in self.recognized_hands)
  # Large desk tiles must leave room for native docking at the window edges.
  minimum_cap=min(430 if editing else 390,max(160,self.height()//3)) if self.height()>=900 else 160
  self.workspace.docks['players'].setMinimumHeight(min(required+12,minimum_cap))
 def eventFilter(self,obj,event):
  if self._closing:return False
  if obj is getattr(self,'dora_board',None) and event.type() in (QEvent.Type.Resize,QEvent.Type.DevicePixelRatioChange):self.reflow_dora()
  if obj in (getattr(self,'picker_box',None),getattr(self,'picker_board',None)) and event.type() in (QEvent.Type.Resize,QEvent.Type.DevicePixelRatioChange) and hasattr(self,'picker_grid'):self.reflow_picker()
  if hasattr(self,'picker_target_keys') and hasattr(self,'picker_grid') and event.type() in (QEvent.Type.Show,QEvent.Type.Hide,QEvent.Type.FontChange) and any(obj is self.target_buttons[key] for key in self.picker_target_keys):self.reflow_picker()
  if hasattr(self,'workspace') and 'tiles' in self.workspace.scrolls and obj is self.workspace.scrolls['tiles'].viewport() and event.type()==QEvent.Type.Resize:self.reflow_picker()
  if hasattr(self,'workspace') and hasattr(self,'yaku_grid') and event.type()==QEvent.Type.Resize and any(obj is self.workspace.scrolls[key].viewport() for key in ('players','round','score','waits','yaku')):self.reflow_live_panels()
  # Activation and layout rebuilds can restore focus to an unrelated editor.
  # Only an operator click or keyboard navigation may change the tile target.
  explicit_focus=event.type()==QEvent.Type.FocusIn and event.reason() in (Qt.FocusReason.MouseFocusReason,Qt.FocusReason.TabFocusReason,Qt.FocusReason.BacktabFocusReason,Qt.FocusReason.ShortcutFocusReason)
  if self.ready and (event.type()==QEvent.Type.MouseButtonPress or explicit_focus) and isinstance(obj,QWidget):
   if obj is getattr(self,'win_tile',None):self.set_tile_target('win_tile')
   elif obj is getattr(self,'ura_indicators',None):self.set_tile_target('ura')
   for key,g in self.target_boxes.items():
    if obj is g or g.isAncestorOf(obj):
     self.set_tile_target(key);break
  return super().eventFilter(obj,event)
 def focus_tile_target(self,key):
  if key=='hand':
   widget=self.recognized_hands[self.selected]
   if not getattr(widget,'is_tile_editing',False):widget.begin_tile_edit(0 if widget.tiles else None)
  self.set_tile_target(key)
  panel='yaku' if key in ('win_tile','ura') else key
  if hasattr(self,'workspace') and panel in self.workspace.docks:self.workspace.show_panel(panel)
  if key=='hand':self.workspace.show_panel('tiles');self.workspace.ensure_visible(self.recognized_hands[self.selected])
 def set_tile_target(self,key):
  self.tile_target=key
  for k,g in self.target_boxes.items():
   if g.property('tileTarget')!=(k==key):g.setProperty('tileTarget',k==key);g.style().unpolish(g);g.style().polish(g);g.update()
  for k,b in self.target_buttons.items():b.setChecked(k==key)
  if hasattr(self,'picker_target'):
   caption=self.hand_picker_caption() if key=='hand' else {'meld':'副露','dora':'宝牌','waits':'听牌','win_tile':'和牌张','ura':'里指示牌'}[key]+(' · '+self.model.state['players'][self.selected]['name'] if key!='dora' else '')
   self.picker_target.setText(caption);self.picker_target.setToolTip(caption)
 def pick_tile(self,code):
  if self.tile_target=='dora':self.run(lambda:self.model.dora(code))
  elif self.tile_target=='waits':self.add_wait_draft(code)
  elif self.tile_target=='win_tile':self.win_tile.setText(code);self.win_tile.editingFinished.emit()
  elif self.tile_target=='ura':self.ura_indicators.setText((self.ura_indicators.text().strip()+' '+code).strip());self.ura_indicators.editingFinished.emit()
  elif self.tile_target=='hand':
   widget=self.recognized_hands[self.selected]
   if getattr(widget,'is_tile_editing',False):widget.replace_selected_tile(code)
   else:self.error('先点击需要修正的手牌')
  else:self.add_draft(code)
 def edit_recognized_hand(self,player):
  for widget in self.recognized_hands:
   if widget.player!=player and widget.editing:widget.cancel_edit()
  if self.selected!=player:self.select(player)
  self.set_tile_target('hand');self.workspace.show_panel('tiles')
  self.refresh_wait_editor();self.refresh_player_waits();self.preview()
  QTimer.singleShot(0,self,lambda:self.reveal_hand_editor(player))
 def reveal_hand_editor(self,player):
  if self._closing:return
  self.reflow_live_panels();self.workspace.ensure_visible(self.recognized_hands[player])
 def hand_edit_finished(self,player,accepted):
  self.reflow_live_panels()
  self.refresh_wait_editor();self.refresh_player_waits();self.preview()
  if player==self.selected and self.tile_target=='hand':
   caption='手牌已采用 · 点击手牌可继续修正' if accepted else '修正手牌 · 点击手牌开始'
   self.picker_target.setText(caption);self.picker_target.setToolTip(caption)
 def hand_picker_caption(self):
  widget=self.recognized_hands[self.selected]
  prefix='修正手牌 · 位置 '+str(self.selected+1)+' · '
  if not getattr(widget,'is_tile_editing',False):return prefix+'点击手牌开始'
  return prefix+('点大牌补入' if widget.selected_index is None else '第'+str(widget.selected_index+1)+'张 / 点大牌替换')
 def hand_selection_changed(self,player):
  if player==self.selected:self.set_tile_target('hand')
 def field(self,key,value):
  if self.ready and value is not None:
   self.run(lambda:self.model.field(key,value))
   actual=self.model.state[key]
   if actual!=value:
    control={'title':self.match_title,'roundIndex':self.round,'dealer':self.dealer,'honba':self.honba,'pot':self.pot}.get(key)
    if control is not None:
     blocker=QSignalBlocker(control)
     if key=='title':control.setText(actual)
     elif key in ('roundIndex','dealer'):control.setCurrentIndex(control.findData(actual))
     else:control.setValue(actual//1000 if key=='pot' else actual)
     del blocker
 def select(self,index):
  if index<0:return
  previous=self.selected
  if index!=previous:
   for widget in self.recognized_hands:
    if widget.player!=index and widget.editing:widget.cancel_edit()
  if hasattr(self,'inline_automation'):self.inline_automation.before_select()
  self._automation_yakuman=None
  self.selected=index;self.draft=[];self.refresh()
  if hasattr(self,'inline_automation'):self.inline_automation.selected(previous)
 def add_wait_draft(self,code):
  draft=self.wait_draft();code=normalize_waits([code])[0];draft['waits']=normalize_waits(draft['waits']+[code])
  if self.wait_no_yaku.isChecked() and code not in draft['no_yaku']:draft['no_yaku'].append(code)
  self.wait_dirty.add(self.selected);self.refresh_wait_editor()
 def remove_wait(self,index):
  draft=self.wait_draft();code=draft['waits'].pop(index)
  if code in draft['no_yaku']:draft['no_yaku'].remove(code)
  self.wait_dirty.add(self.selected);self.refresh_wait_editor()
 def change_remaining(self,value):
  if not self.ready:return
  index=self.selected;draft=self.wait_draft();previous=draft['remaining'];was_dirty=index in self.wait_dirty;draft['remaining']=None if value<0 else value;player=self.model.state['players'][index]
  live=self.model.state['phase']=='playing' and player.get('tenpai') and player.get('waits') and not self.recognized_hands[index].editing
  if live:
   # Updating the live count must not publish any other pending wait edits.
   pending=any(draft[key]!=player.get(source,default) for key,source,default in (('waits','waits',[]),('no_yaku','waits_no_yaku',[]),('furiten','furiten',False)))
   if pending:self.wait_dirty.add(index)
   else:self.wait_dirty.discard(index)
   try:
    if draft['remaining']!=player.get('remaining'):self.model.set_remaining(index,draft['remaining'])
   except Exception as error:
    draft['remaining']=previous
    if was_dirty:self.wait_dirty.add(index)
    else:self.wait_dirty.discard(index)
    self.refresh_wait_editor();self.error(str(error));return
  else:self.wait_dirty.add(index)
  self.clear_remaining_button.setEnabled(value>=0);self.refresh_wait_preview()
 def build_score(self,middle):
  g,v=box('计分');self.score_box=g;self.hand_actions=QHBoxLayout();self.hand_actions.setSpacing(7);v.addLayout(self.hand_actions)
  self.winner=SeatButtonSelector();self.loser=SeatButtonSelector();self.winner_buttons=self.winner.buttons;self.loser_buttons=self.loser.buttons;v.addLayout(row('和牌',self.winner));v.addLayout(row('放铳',self.loser));self.ron=button('荣和',lambda:self.win_kind('ron'),True);self.tsumo=button('自摸',lambda:self.win_kind('tsumo'),True);self.ron.setStyleSheet(CHOICE_BUTTON_STYLE);self.tsumo.setStyleSheet(CHOICE_BUTTON_STYLE);self.ron.setProperty('operatorChoice',True);self.tsumo.setProperty('operatorChoice',True);self.ron.setChecked(True);self.kind='ron';v.addLayout(row(self.ron,self.tsumo))
  self.win_tile=QLineEdit();self.win_tile.setMaximumWidth(84);self.win_tile.setMaxLength(3);self.win_tile.setPlaceholderText('点牌面');self.win_tile.setToolTip('点击此框后从下方选择和牌张，也可直接输入如 3m；根据该选手识别手牌辅助填写役种')
  self.ura_indicators=QLineEdit();self.ura_indicators.setPlaceholderText('点牌面');self.ura_indicators.setClearButtonEnabled(True);self.ura_indicators.setToolTip('点击此框后从下方依次选择里宝牌指示牌，也可输入如 4m1z；明宝牌和副露自动读取系统')
  self.win_fill_button=button('更新役种',lambda:self.inline_automation.update_win() if hasattr(self,'inline_automation') else None);self.win_fill_button.setEnabled(False)
  self.win_assist_hint=QLabel();self.win_assist_hint.setObjectName('muted');self.win_assist_hint.setWordWrap(True)
  self.win_tile.setMinimumWidth(0);self.ura_indicators.setMinimumWidth(0);self.ura_indicators.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
  self.win_tile.editingFinished.connect(lambda:self.inline_automation.recalculate_win() if hasattr(self,'inline_automation') else None);self.ura_indicators.editingFinished.connect(lambda:self.inline_automation.recalculate_win() if hasattr(self,'inline_automation') else None)
  self.fu=FuSpinBox();self.fu.setMaximumWidth(108);self.han=spin(3,1,100);self.han.setMaximumWidth(108);self.linked=QCheckBox('番数联动');self.fu_buttons={};quick_fu=QHBoxLayout();quick_fu.setSpacing(3)
  for value in (30,40,50):
   control=button(str(value)+'符',lambda _,n=value:self.choose_fu(n),True);control.setProperty('operatorChoice',True);control.setStyleSheet(CHOICE_BUTTON_STYLE);control.setMinimumWidth(0);control.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);self.fu_buttons[value]=control;quick_fu.addWidget(control)
  v.addLayout(quick_fu);self.score_numbers=QGridLayout();self.score_numbers.setSpacing(7);self.fu_label=QLabel('符');self.han_label=QLabel('番');v.addLayout(self.score_numbers);self.reflow_score_numbers()
  self.fu_hint=QLabel();self.fu_hint.setObjectName('muted');self.fu_hint.setWordWrap(True);self.fu_hint.hide();v.addWidget(self.fu_hint);v.addWidget(self.linked)
  for control in (self.winner,self.loser,self.fu,self.han):control.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);control.setMinimumWidth(0)
  self.win_preview_label=QLabel();self.win_preview_label.setObjectName('draftPreview');self.win_preview_label.setProperty('i18nLiteral',True);self.win_preview_label.setWordWrap(True);v.addWidget(self.win_preview_label)
  self.preview_grid=QGridLayout();v.addLayout(self.preview_grid);self.preview_error=QLabel('');self.preview_error.setWordWrap(True);self.preview_error.setObjectName('error');v.addWidget(self.preview_error);self.settle_button=button('提交结算',self.settle);self.settle_button.setObjectName('primary');self.settle_button.setToolTip('按上方预览提交和牌与收支；不会再弹出确认窗口');v.addWidget(self.settle_button)
  # Duplicate status buttons and playback preferences keep their existing API,
  # while the live desk uses the four seats and the visible score actions.
  self.score_preferences=QWidget(self);preferences=QVBoxLayout(self.score_preferences);self.score_preferences.hide()
  self.draw_buttons=[];draw=row('听牌')
  for i in range(4):b=button(WINDS[i],lambda _,j=i:self.toggle_player_status(j,'tenpai'),True);self.draw_buttons.append(b);draw.addWidget(b)
  draw.addWidget(button('流局',lambda:self.settle_draw()));preferences.addLayout(draw)
  self.speed=combo([('0.75×',.75),('1×',1.),('1.25×',1.25)]);self.speed.setCurrentIndex(self.speed.findData(self.model.settings['speed']));self.replay_button=button('重播',self.manager.replay);preferences.addLayout(row(self.replay_button,button('跳过',self.manager.finish),self.speed));middle.addWidget(g)
  self.final_scores_hold=spin(round(self.model.settings.get('final_scores_hold',4.)),1,60);self.final_scores_hold.setSuffix(' 秒');self.final_scores_hold.valueChanged.connect(lambda value:self.run(lambda:self.model.setting('final_scores_hold',value)) if self.ready else None);preferences.addLayout(row('终局点数停留',self.final_scores_hold))
  self.winner.currentIndexChanged.connect(lambda i:self.select(i) if self.ready else None);self.loser.currentIndexChanged.connect(self.preview);self.fu.currentIndexChanged.connect(self.preview);self.han.valueChanged.connect(self.manual_han);self.linked.toggled.connect(lambda _:self.yaku_changed());self.speed.currentIndexChanged.connect(lambda:self.run(lambda:self.model.setting('speed',self.speed.currentData())) if self.ready else None)
  self.fu.currentIndexChanged.connect(lambda _:self.inline_automation.win_edited('fu') if self.ready and hasattr(self,'inline_automation') else None)
  self.linked.clicked.connect(lambda _:self.inline_automation.win_edited('linked') if hasattr(self,'inline_automation') else None)
 def settle_draw(self,edit=False):
  state=self.model.state
  if state['phase']!='playing':return
  declared=[i for i,p in enumerate(state['players']) if p['tenpai'] or p['riichi']]
  if edit:
   dialog=QDialog(self);dialog.setWindowTitle('流局申报');dialog.setMinimumWidth(360)
   layout=QVBoxLayout(dialog)
   note=QLabel('勾选亮牌申报听牌的选手。盖牌按不听结算，已立直选手须申报听牌。')
   note.setWordWrap(True);layout.addWidget(note)
   seats={}
   for offset,wind in enumerate(WINDS):
    i=(state['dealer']+offset)%4;player=state['players'][i]
    label=tr(wind)+' · '+player['name'].replace('&','&&')
    if player['riichi']:label+=' · '+tr('已立直')
    check=QCheckBox(label);check.setProperty('i18nLiteral',True)
    check.setChecked(i in declared);check.setEnabled(not player['riichi'])
    seats[i]=check;layout.addWidget(check)
   buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
   confirm=buttons.button(QDialogButtonBox.StandardButton.Ok);confirm.setText('确认流局');confirm.setDefault(True)
   buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
   buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
   localize_widgets(dialog)
   try:
    if dialog.exec()!=QDialog.DialogCode.Accepted:return
    if self.model.state is not state:return self.error('对局状态已变化，请重新核对流局申报')
    declared=sorted(i for i,check in seats.items() if check.isChecked())
   finally:dialog.deleteLater()
  return self.run(lambda:self.model.draw(declared))
 def win_kind(self,kind):
  self.kind=kind;self.ron.setChecked(kind=='ron');self.tsumo.setChecked(kind=='tsumo')
  if kind=='ron':self.yaku_buttons['门前清自摸和'].setChecked(False)
  self.yaku_changed()
  if hasattr(self,'inline_automation'):self.inline_automation.recalculate_win()
 def choose_fu(self,value):
  if self.fu.isReadOnly():return
  self.fu.setValue(value);self.preview()
  if hasattr(self,'inline_automation'):self.inline_automation.win_edited('fu')
 def reflow_score_numbers(self):
  if not hasattr(self,'score_numbers'):return
  if hasattr(self,'quick_actions') and 'draw' in self.quick_actions:
   actions=[self.quick_actions[key] for key in ('draw','draw_declaration','next_hand')]
   required=sum(max(control.minimumWidth(),control.minimumSizeHint().width()) for control in actions)+(len(actions)-1)*self.hand_actions.spacing()
   self.hand_actions.setDirection(QBoxLayout.Direction.LeftToRight if required<=self.score_box.contentsRect().width()-18 else QBoxLayout.Direction.TopToBottom)
  narrow=self.workspace.scrolls['score'].viewport().width()<300 if 'score' in self.workspace.scrolls else False
  if narrow==getattr(self,'_score_numbers_narrow',None):return
  self._score_numbers_narrow=narrow
  for control in (self.fu_label,self.fu,self.han_label,self.han):self.score_numbers.removeWidget(control)
  self.score_numbers.addWidget(self.fu_label,0,0);self.score_numbers.addWidget(self.fu,0,1)
  self.score_numbers.addWidget(self.han_label,1 if narrow else 0,0 if narrow else 2);self.score_numbers.addWidget(self.han,1 if narrow else 0,1 if narrow else 3)
 def update_fu_from_yaku(self):
  fixed=None;reason=''
  if not self.model.state['players'][self.selected]['melds']:
   if self.kind=='tsumo' and (self.yaku_buttons['七对子'].isChecked() or self.yaku_buttons['平和'].isChecked()):self.yaku_buttons['门前清自摸和'].setChecked(True)
   if self.yaku_buttons['七对子'].isChecked():fixed=25;reason='七对子固定 25 符'
   elif self.yaku_buttons['平和'].isChecked():
    fixed=20 if self.kind=='tsumo' else 30;reason='平和自摸固定 20 符' if self.kind=='tsumo' else '平和荣和固定 30 符'
  blocker=QSignalBlocker(self.fu)
  if fixed is not None:self.fu.setValue(fixed)
  self.fu.setReadOnly(fixed is not None);del blocker
  self.fu.setToolTip(reason or '直接输入符数，或点击 30／40／50 符快捷键');self.fu_hint.setText(reason);self.fu_hint.setVisible(fixed is not None)
  for value,control in self.fu_buttons.items():
   blocker=QSignalBlocker(control);control.setChecked(self.fu.value()==value);control.setEnabled(fixed is None);del blocker
 def manual_han(self,_):
  if self.ready:
   if hasattr(self,'inline_automation'):self.inline_automation.win_edited('han')
   b=QSignalBlocker(self.linked);self.linked.setChecked(False);del b;self.preview()
 def win_event(self):
  from mahjong_values import yaku_total
  names=[n for n,b in self.yaku_buttons.items() if b.isChecked()];opened=any(m['kind']!='ankan' for m in self.model.state['players'][self.selected]['melds']);bonus=sum(x.value() for x in self.bonuses);han,yakuman=yaku_total(names,opened,bonus,manual=True)
  calculated=getattr(self,'_automation_yakuman',None)
  if yakuman and calculated and calculated[0]==frozenset(names):yakuman=calculated[1]
  return {'type':self.kind,'winner':self.selected,'loser':self.loser.currentIndex(),'fu':self.fu.currentData(),'han':han if self.linked.isChecked() else self.han.value(),'yakuman':yakuman},names
 def preview(self,*_):
  if not self.ready:return
  self.update_fu_from_yaku()
  self.loser.setEnabled(self.kind=='ron');clear_layout(self.preview_grid);self.preview_error.clear();self.preview_error.hide();self.settle_button.setEnabled(False);self.win_preview_label.setText('结算预览')
  try:
   e,_=self.win_event();result=self.model.preview(e)
   players=self.model.state['players'];winner=players[e['winner']]['name'];method='自摸' if self.kind=='tsumo' else '荣和 ← '+players[e['loser']]['name'];value=str(e['yakuman'])+'倍役满' if e['yakuman'] else str(e['fu'])+'符 '+str(e['han'])+'番'
   self.win_preview_label.setText('待提交 · '+winner+' '+method+' · '+value)
   for i,p in enumerate(self.model.state['players']):
    delta=result.get('displayDeltas',result['deltas'])[i];label=QLabel(('+' if delta>0 else '−' if delta<0 else '')+fmt(abs(delta)));label.setObjectName('deltaPlus' if delta>0 else 'deltaMinus' if delta<0 else 'muted');label.setAlignment(Qt.AlignmentFlag.AlignRight);name_label=QLabel(p['name']);name_label.setProperty('i18nLiteral',True);name_label.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);name_label.setToolTip(p['name']);self.preview_grid.addWidget(name_label,i//2,i%2*2);self.preview_grid.addWidget(label,i//2,i%2*2+1)
   allowed=True;assist=getattr(self,'inline_automation',None)
   if assist and hasattr(assist,'win_review_status'):
    message,allowed=assist.win_review_status()
    if message:self.preview_error.setObjectName('muted' if allowed else 'error');self.preview_error.style().unpolish(self.preview_error);self.preview_error.style().polish(self.preview_error);self.preview_error.setText(message);self.preview_error.show()
   self.settle_button.setEnabled(allowed)
  except Exception as err:self.preview_error.setObjectName('error');self.preview_error.style().unpolish(self.preview_error);self.preview_error.style().polish(self.preview_error);self.preview_error.setText(str(err));self.preview_error.show()
 def settle(self):
  def apply():
   assist=getattr(self,'inline_automation',None)
   if assist and hasattr(assist,'win_review_status'):
    message,allowed=assist.win_review_status()
    if not allowed:raise ValueError(message)
   e,names=self.win_event();result=self.model.preview(e);info=self.make_info(e,names,result);self.model.settle(e,info)
  self.run(apply)
 def make_info(self,event,names,result):
  p=self.model.state['players'][self.selected];labels=list(names)
  for label,s in zip(['宝牌','赤宝牌','里宝牌'],self.bonuses):
   if s.value():labels.append(label+' '+str(s.value()))
  return {'name':p['name'],'winner':self.selected,'kind':event['type'],'color':p['color'],'han':event['han'],'fu':event['fu'],'yakuman':event.get('yakuman',0),'yaku':labels,'limit':result.get('limit',''),'base_points':result.get('basePoints',0)}
 def build_meld(self,middle):
  g,v=box('副露');self.target_boxes['meld']=g;self.meld_owner=QLabel();self.meld_owner.setProperty('i18nLiteral',True);v.addWidget(self.meld_owner)
  self.meld_kind=SeatButtonSelector(columns=5,literal=False)
  for label,value in [('吃','chi'),('碰','pon'),('明杠','daiminkan'),('暗杠','ankan'),('加杠','kakan')]:self.meld_kind.addItem(label,value)
  self.meld_kind.setCurrentIndex(1);self.meld_from=SeatButtonSelector(columns=3,literal=False)
  for label,value in [('上家',3),('对家',2),('下家',1)]:self.meld_from.addItem(label,value)
  v.addWidget(self.meld_kind);v.addLayout(row('来源',self.meld_from))
  self.draft_row=QHBoxLayout();self.draft_row.setContentsMargins(0,0,0,0)
  self.meld_draft_preview=MeldPreviewWidget(parent=g);self.meld_draft_preview.tile_clicked.connect(self.choose_called);self.meld_draft_preview.tile_removed.connect(self.remove_draft)
  self.draft_row.addWidget(self.meld_draft_preview);v.addLayout(self.draft_row)
  self.add_meld_button=button('添加副露',self.add_meld);v.addLayout(row(button('清空',self.clear_draft),self.add_meld_button));self.meld_rows=QVBoxLayout();v.addLayout(self.meld_rows);middle.addWidget(g)
  self.show_melds=display_button('显示副露区');self.show_melds.setChecked(self.model.settings.get('show_melds',True));v.insertWidget(0,self.show_melds);self.show_melds.toggled.connect(lambda value:self.run(lambda:self.model.setting('show_melds',value)) if self.ready else None)
  self.meld_kind.currentIndexChanged.connect(self.change_meld_kind)
  self.meld_from.currentIndexChanged.connect(self.refresh_draft)
 def change_meld_kind(self,*_):
  if self.meld_kind.currentData()=='chi':
   blocker=QSignalBlocker(self.meld_from);self.meld_from.setCurrentIndex(self.meld_from.findData(3));del blocker
  self.clear_draft()
 def clear_draft(self,*_):self.draft=[];self.called_index=0;self.refresh_draft()
 def add_draft(self,t):
  kind=self.meld_kind.currentData();n=4 if kind in ['ankan','daiminkan','kakan'] else 3
  complete=[t]+[('5'+t[1]) if t[0]=='0' else t]*(n-1)
  if kind=='kakan':
   base=lambda code:'5'+code[1] if code[0]=='0' else code
   pon=next((m for m in self.model.state['players'][self.selected]['melds'] if m['kind']=='pon' and base(m['tiles'][0])==base(t)),None)
   complete=list(pon['tiles'])+[t] if pon else complete[1:]+[t]
  if not self.draft and kind!='chi':self.draft=complete
  elif len(self.draft)<n:self.draft.append(t)
  else:self.draft=[t] if kind=='chi' else complete
  self.refresh_draft()
 def choose_called(self,index):self.called_index=index;self.refresh_draft()
 def meld_draft_group(self):
  kind=self.meld_kind.currentData();source=None if kind=='ankan' else (self.selected+self.meld_from.currentData())%4
  required=4 if kind in ('ankan','daiminkan','kakan') else 3;group=None
  if len(self.draft)==required:
   try:
    before=self.model.state['players'][self.selected]['melds']
    after=self.model.engine.apply(self.model.state,{'type':'meld','player':self.selected,'meld':{'kind':kind,'tiles':self.draft.copy(),'from':source,'calledIndex':self.called_index}})['players'][self.selected]['melds']
    group=next((new for old,new in zip(before,after) if old!=new),after[-1])
   except ValueError:pass
  if group is None:
   indexed=list(enumerate(self.draft));called=None
   if indexed and kind!='ankan':
    claimed=indexed.pop(self.called_index)
    if kind=='chi':indexed.sort(key=lambda entry:tile_sort_key(entry[1]))
    slot=0 if self.meld_from.currentData()==3 else 1 if self.meld_from.currentData()==2 else (3 if kind=='daiminkan' else 2)
    called=min(slot,len(indexed));indexed.insert(called,claimed)
   group={'kind':kind,'tiles':[code for _,code in indexed],'from':source,'calledIndex':called}
  remaining=list(range(len(self.draft)));indices=[None]*len(group['tiles']);called=group.get('calledIndex')
  if called is not None and group['tiles'][called]==self.draft[self.called_index]:
   indices[called]=self.called_index;remaining.remove(self.called_index)
  for index,code in enumerate(group['tiles']):
   if indices[index] is None:
    original=next(candidate for candidate in remaining if self.draft[candidate]==code);indices[index]=original;remaining.remove(original)
  return group,indices
 def refresh_draft(self,*_):
  kind=self.meld_kind.currentData();self.called_index=min(self.called_index,max(0,len(self.draft)-1))
  group,indices=self.meld_draft_group() if self.draft else (None,None)
  self.meld_draft_preview.set_group(group,tile_scale(self.model.settings,'meld'),indices)
  self.meld_from.setEnabled(kind!='ankan');required=4 if kind in ['ankan','daiminkan','kakan'] else 3;playing=self.model.state['phase']=='playing';self.add_meld_button.setEnabled(playing and len(self.draft)==required);self.add_meld_button.setToolTip('添加当前副露' if playing else self.player_input_blocked_message())
 def remove_draft(self,i):self.draft.pop(i);self.refresh_draft()
 def add_meld(self):
  if self.model.state['phase']!='playing':self.error(self.player_input_blocked_message());return
  def add():
   kind=self.meld_kind.currentData();source=None if kind=='ankan' else (self.selected+self.meld_from.currentData())%4
   self.model.meld(self.selected,kind,self.draft.copy(),source,self.called_index);self.clear_draft()
  self.run(add)
 def build_yaku(self,right):
  g,v=box('役种');self.show_yaku=QCheckBox('和牌画面',g);self.show_yaku.setChecked(self.model.settings['show_yaku']);self.show_yaku.hide();self.yaku_grid=QGridLayout();self.yaku_grid.setSpacing(3)
  v.addLayout(row('和牌张',self.win_tile,'里指示牌',self.ura_indicators,self.win_fill_button));v.addWidget(self.win_assist_hint)
  for i,(name,closed,opened) in enumerate(YAKU[:30]):
   b=button(name,lambda _,n=name:self.role_clicked(n),True);b.setMinimumWidth(0);b.setStyleSheet(CHOICE_BUTTON_STYLE);b.setProperty('operatorChoice',True);self.yaku_buttons[name]=b;self.yaku_grid.addWidget(b,i//5,i%5)
  self.bonuses=[];bonus_row=QHBoxLayout();bonus_row.setSpacing(5)
  for name in ['宝牌','赤宝牌','里宝牌']:
   vv=QVBoxLayout();vv.setSpacing(3);vv.addWidget(QLabel(name));s=spin(0,0,40);s.setMinimumWidth(94);s.setMaximumWidth(108);s.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);s.valueChanged.connect(self.bonus_changed);self.bonuses.append(s);vv.addWidget(s);bonus_row.addLayout(vv)
  bonus_row.addStretch(1)
  v.addLayout(bonus_row);v.addLayout(self.yaku_grid);self.yaku_summary=QLabel('');self.yaku_summary.setObjectName('muted');v.addLayout(row(self.yaku_summary,1,button('清空',self.clear_yaku),button('更新和牌',self.update_info)))
  self.yakuman_toggle=button('+ 役满',self.set_yakuman_expanded,True);self.yakuman_toggle.setObjectName('yakumanToggle');self.yakuman_toggle.setProperty('i18nLiteral',True);v.addWidget(self.yakuman_toggle)
  self.yakuman_body=QWidget(g);self.yakuman_body.setObjectName('yakumanBody');grid=QGridLayout(self.yakuman_body);grid.setContentsMargins(0,0,0,0);grid.setSpacing(3)
  for i,(name,closed,opened) in enumerate(YAKU[30:]):
   b=button(name,lambda _,n=name:self.role_clicked(n),True);b.setStyleSheet(CHOICE_BUTTON_STYLE);b.setProperty('operatorChoice',True);self.yaku_buttons[name]=b;grid.addWidget(b,i//3,i%3)
  v.addWidget(self.yakuman_body);self.yakuman_body.hide();right.addWidget(g);self.show_yaku.toggled.connect(lambda value:self.run(lambda:self.model.setting('show_yaku',value)) if self.ready else None)
 def set_yakuman_expanded(self,expanded):
  expanded=bool(expanded);self.yakuman_body.setVisible(expanded)
  blocker=QSignalBlocker(self.yakuman_toggle);self.yakuman_toggle.setChecked(expanded);del blocker
  self.sync_yakuman_fold()
  if hasattr(self,'workspace'):self.workspace.schedule_save()
 def sync_yakuman_fold(self,*_):
  count=sum(self.yaku_buttons[name].isChecked() for name,_,_ in YAKU[30:]);self.yakuman_toggle.setText(('− ' if self.yakuman_toggle.isChecked() else '+ ')+tr('役满')+(' · '+str(count) if count else ''))
 def role_clicked(self,name):
  self._automation_yakuman=None
  if name=='门前清自摸和':
   self.kind='tsumo' if self.yaku_buttons[name].isChecked() else 'ron';self.ron.setChecked(self.kind=='ron');self.tsumo.setChecked(self.kind=='tsumo')
  self.linked.setChecked(True)
  self.yaku_changed()
  if hasattr(self,'inline_automation'):self.inline_automation.win_edited(name)
 def bonus_changed(self,*_):
  if self.ready:
   if hasattr(self,'inline_automation'):self.inline_automation.win_edited('bonus')
   self.linked.setChecked(True);self.yaku_changed()
 def yaku_changed(self):
  if not self.ready:return
  self.update_fu_from_yaku()
  self.sync_yakuman_fold()
  try:
   e,names=self.win_event();self.yaku_summary.setText((str(e['yakuman'])+'倍役满') if e['yakuman'] else str(e['han'])+'番')
   if self.linked.isChecked() and not e['yakuman']:
    b=QSignalBlocker(self.han);self.han.setValue(max(1,e['han']));del b
  except Exception as err:self.yaku_summary.setText(str(err))
  self.preview()
 def clear_yaku(self):
  if hasattr(self,'inline_automation'):self.inline_automation.win_edited('clear')
  self._automation_yakuman=None
  for b in self.yaku_buttons.values():b.setChecked(False)
  for s in self.bonuses:
   block=QSignalBlocker(s);s.setValue(0);del block
  self.linked.setChecked(False);self.yaku_changed()
 def update_info(self):
  def update():
   assist=getattr(self,'inline_automation',None)
   if assist and hasattr(assist,'win_review_status'):
    message,allowed=assist.win_review_status()
    if not allowed:raise ValueError(message)
   e,names=self.win_event()
   if self.model.state['phase']=='playing':result=self.model.preview(e)
   else:result=self.model.state.get('lastResult') or {}
   self.model.save_win_info(self.make_info(e,names,result),show=True)
  self.run(update)
 def refresh_display(self):
  self.undo.setEnabled(bool(self.model.undo));self.redo.setEnabled(bool(self.model.redo))
  if hasattr(self,'replay_button'):self.replay_button.setEnabled(bool(self.model.last_transition))
  event=self.model.event
  self.event_label.setText('独立导播 · 工作区自动保存' if getattr(self.model,'ui_only',False) else ('当前赛事：'+event['name']) if event else '当前会话 · 点击赛事库建立独立赛事')
  self.event_label.setToolTip(str(self.model.path))
  for widget,key in ((self.lock,'locked'),(self.overlays,'show_overlays')):
   blocker=QSignalBlocker(widget);widget.setChecked(self.model.settings[key]);del blocker
  blocker=QSignalBlocker(self.speed);self.speed.setCurrentIndex(self.speed.findData(self.model.settings.get('speed',1.)));del blocker
  b=QSignalBlocker(self.final_scores_hold);self.final_scores_hold.setValue(round(self.model.settings.get('final_scores_hold',4.)));del b
  b=QSignalBlocker(self.show_yaku);self.show_yaku.setChecked(self.model.settings['show_yaku']);del b
  b=QSignalBlocker(self.language);self.language.setCurrentIndex(self.language.findData(self.model.settings.get('language','zh_CN')));del b
  for _,_,_,riichi,tenpai in self.player_widgets:
   riichi.setFixedWidth(round((64 if self.model.settings.get('language')=='ja_JP' else 48)*self.model.settings.get('console_font_size',14)/12));tenpai.setFixedWidth(riichi.width())
  for widget,key in ((self.show_melds,'show_melds'),):
   blocker=QSignalBlocker(widget);widget.setChecked(self.model.settings.get(key,True));del blocker
  if self.ready:
   for i,(_,_,score,_,_) in enumerate(self.player_widgets):
    if not score.hasFocus():score.setValue(display_score(self.model.state,i,self.model.settings.get('riichi_payment_mode')))
  self.refresh_sidebar_presets();self.refresh_corner_panel();localize_widgets(self);self.refresh_quick_actions()
  size=int(self.model.settings.get('console_font_size',14));self.font_smaller_button.setEnabled(size>9);self.font_larger_button.setEnabled(size<24)
  self.font_smaller_button.setToolTip('减小导播台字号 · '+str(size)+' px');self.font_larger_button.setToolTip('增大导播台字号 · '+str(size)+' px')
  self.apply_typography()
  self.refresh_desk_tile_sizes()
  self.navigation_handle.fit_width()
  self.reflow_round_controls()
  mode=self.model.settings.get('riichi_payment_mode','immediate')
  if self.ready and getattr(self,'_preview_riichi_mode',None)!=mode:self._preview_riichi_mode=mode;self.preview()
 def apply_typography(self):
  signature=tuple(self.model.settings.get(key) for key in ('console_theme','console_font_family','console_font_size'))
  if signature==getattr(self,'_desk_font_signature',None):return
  self._desk_font_signature=signature;apply_theme(self,self.model.settings)
  self.reflow_round_controls()
  if hasattr(self,'picker_grid'):self.reflow_picker()
  if getattr(self,'broadcast_dialog',None):apply_theme(self.broadcast_dialog,self.model.settings)
 def refresh_desk_tile_sizes(self):
  signature=tuple(self.model.settings.get(key,100) for key in TILE_SIZE_KEYS)
  if signature==getattr(self,'_desk_tile_sizes_signature',None):return
  self._desk_tile_sizes_signature=signature
  for waits,_ in self.player_wait_widgets:waits.set_tile_scale(tile_scale(self.model.settings,'wait'))
  self.refresh_wait_editor();self.refresh_draft();self.refresh_meld_rows();self.reflow_live_panels()
 def refresh_meld_rows(self):
  clear_layout(self.meld_rows);p=self.model.state['players'][self.selected]
  for i,m in enumerate(p['melds']):
   source='自家' if m['kind']=='ankan' else {3:'上家',2:'对家',1:'下家'}.get((m.get('from',self.selected)-self.selected)%4,'');kind={'chi':'吃','pon':'碰','daiminkan':'明杠','ankan':'暗杠','kakan':'加杠'}[m['kind']]
   line=QHBoxLayout();line.setSpacing(4);label=QLabel(kind+' · '+source);label.setWordWrap(True);line.addWidget(label)
   line.addWidget(MeldPreviewWidget(m,tile_scale(self.model.settings,'meld'),self.target_boxes['meld']));line.addStretch()
   remove=button('删除',lambda _,j=i:self.run(lambda:self.model.remove_meld(self.selected,j)));line.addWidget(remove);self.meld_rows.addLayout(line)
 def refresh(self):
  if not self.ready:return
  self.ready=False;s=self.model.state;playing=True;self.round.setCurrentIndex(s['roundIndex']);self.honba.setValue(s['honba']);self.pot.setValue(s['pot']//1000)
  self.match_title.setText(s.get('title',''))
  fill_players(self.dealer,self.model,s['dealer']);fill_players(self.winner,self.model,self.selected);self.wait_owner.setText(s['players'][self.selected]['name']);self.meld_owner.setText(s['players'][self.selected]['name'])
  loser=self.loser.currentIndex();fill_players(self.loser,self.model,loser if loser>=0 and loser!=self.selected else (self.selected+1)%4,self.selected)
  for i,(panel,select,score,riichi,tenpai) in enumerate(self.player_widgets):
   p=s['players'][i];select.setText(tr(WINDS[(i-s['dealer'])%4])+' '+p['name']);select.setToolTip(p['name']);select.setProperty('currentSeat',i==self.selected);select.style().unpolish(select);select.style().polish(select);score.setValue(display_score(s,i,self.model.settings.get('riichi_payment_mode')));riichi.setChecked(p['riichi']);tenpai.setChecked(p['tenpai']);riichi.setEnabled(playing);tenpai.setEnabled(True);b=self.draw_buttons[i];b.setText(WINDS[(i-s['dealer'])%4]);b.setChecked(p['tenpai']);b.setEnabled(True)
  clear_layout(self.dora_row)
  for i,t in enumerate(self.model.dora_tiles):
   b=tile_face_button(t,lambda _,j=i:self.run(lambda:self.model.dora(index=j)));b.setToolTip('移除'+tile_name(t));b.setEnabled(playing);self.dora_row.addWidget(b)
  self.dora_row.addStretch(1);self.reflow_dora();self.refresh_wait_editor();self.refresh_player_waits();p=s['players'][self.selected]
  self.refresh_meld_rows()
  self.undo.setEnabled(bool(self.model.undo));self.redo.setEnabled(bool(self.model.redo));self.replay_button.setEnabled(bool(self.model.last_transition));self.ready=True;self.refresh_display();self.refresh_draft();self.set_tile_target(self.tile_target);self.yaku_changed()
  self.hand_history.refresh()
 def closeEvent(self,event):
  if self._closing:event.accept();return
  from settings_context import settings_windows
  approved=[]
  for dialog in settings_windows(self.model):
   if not dialog._confirm_close():event.ignore();return
   approved.append(dialog)
  original_settings=self.model.settings
  self.model.settings=copy.deepcopy(original_settings)
  try:
   self.manager.save_geometry(persist=False)
   candidate=dict(self.model.settings,console_layout=self.workspace.layout_value())
   output=getattr(getattr(self,'event_workspace',None),'output',None)
   if output is not None:candidate['report_output_geometry']=[output.x(),output.y(),output.width(),output.height()]
   self.model.settings=original_settings
   self.model.save_preferences(candidate)
   self.manager.accept_geometry()
  except Exception as error:
   self.model.settings=original_settings;self.error('关闭前保存失败：'+str(error));event.ignore();return
  self._closing=True
  app=QApplication.instance();app.removeEventFilter(self)
  if hasattr(self,'shortcuts'):self.shortcuts.dispose()
  self.language_controller.dispose()
  for dialog in approved:dialog._allow_close=True
  if hasattr(self,'inline_automation'):self.inline_automation.close()
  if hasattr(self,'automation'):self.automation.close()
  self.workspace.dispose()
  if getattr(self,'event_workspace',None):
   if getattr(self.event_workspace,'output',None):self.event_workspace.output.close()
   self.event_workspace.close()
  if getattr(self,'broadcast_dialog',None) and self.broadcast_dialog.isWindow():self.broadcast_dialog.close()
  if getattr(self,'corner_dialog',None):self.corner_dialog.close()
  if getattr(self,'sidebar_presets_dialog',None):self.sidebar_presets_dialog.close()
  self.manager.close(persist=False);event.accept();app.quit()
