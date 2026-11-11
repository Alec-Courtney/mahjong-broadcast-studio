"""Native tournament desk; form drafts never silently change the broadcast."""
from __future__ import annotations
import copy,csv,math,uuid
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from i18n import tr,get_language,signals
from glossary import CATEGORIES,get_term,term_text,terms_for_category,resolve_term_id
from demo_console import DemoSession
from PySide6.QtCore import Qt,QEvent,QSignalBlocker,QTimer
from PySide6.QtGui import QColor,QIcon,QPixmap
from PySide6.QtWidgets import (QAbstractItemView,QCheckBox,QComboBox,QDialog,QFileDialog,QFormLayout,QGridLayout,QGroupBox,QHBoxLayout,QHeaderView,QLabel,QLineEdit,QMenu,QMessageBox,QScrollArea,QSizePolicy,QTableWidget,QTableWidgetItem,QTabWidget,QTextEdit,QVBoxLayout,QWidget)

from ui_widgets import button, row, choice, integer, decimal, selected, note, SidebarPages
from desk_theme import apply_theme, status as set_status_style
from color_input import ColorDelegate
from event_roster import PLAYER_STATUSES, change_player, removal_reasons, selected_profile
from settings_preview import ArtworkPreview,RiichiAnimationPreview

EXTENSIONS={'table_scores':'上方积分','qualification':'侧边展示栏','four_conditions':'四人条件（全屏）','commentary':'术语解释','standings':'底部积分','statistics':'技术统计','chart':'比赛走势','corner_header':'右上角标识'}
COUNTERS=[('hands','总局数'),('calls','副露局数'),('riichi','立直局数'),('wins','和牌局数'),('deal_ins','放铳局数'),('win_points','和牌得点'),('deal_in_points','放铳失点')]
# Kept as a public pair list for existing integrations; only practical terms appear.
TERMS=[(entry['zh_title'],entry['zh_body']) for entry in terms_for_category()]
DEFAULT_TERM_FAVORITES=('one_chance','nidouke','no_chance','matagi_suji','push_pull','kanzen_ishanten')
POSTMATCH_AUDITION_SECONDS=12

def refresh_bottom_selector(widget,manager):
 """Both desks expose the same final score/pt choice after the last hand."""
 items=([('选手信息','players'),('最终点数／名次','final_scores'),('结算 pt','final_pt')] if manager.model.state.get('phase')=='finished'
        else [('选手信息','players'),('个人积分','individual'),('队伍积分','team')])
 if manager.can_show_settlement():items += [('本场结果','match_result'),('赛事总积分','total_result')]
 blocker=QSignalBlocker(widget);widget.setProperty('i18n_literal',True)
 if [widget.itemData(i) for i in range(widget.count())]!=[value for _,value in items]:
  widget.clear()
  for label,value in items:widget.addItem(tr(label),value)
 for i,(label,_) in enumerate(items):widget.setItemText(i,tr(label))
 widget.setToolTip(tr('底部显示'));selected(widget,manager.bottom_mode());del blocker
def grid_table(headers,editable=True):
 w=QTableWidget(0,len(headers));w.setHorizontalHeaderLabels(headers);w.verticalHeader().hide();w.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);w.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);w.setAlternatingRowColors(True)
 if not editable:w.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
 return w
def format_rate(value):return '—' if value is None else f'{100*value:.1f}%'
def format_average(value):return '—' if value is None else f'{value:,.0f}'

def seat_choice_entries(roster):
 """Distinct operator labels; item data always carries the original stable ID."""
 roster=[player for player in roster if not player.get('hidden')]
 labels=[(f"{p.get('team')} · {p['name']}" if p.get('team') else p['name']) for p in roster]
 counts=Counter(labels)
 return [(label+(f" · {p['id']}" if counts[label]>1 else ''),p['id']) for label,p in zip(labels,roster)]

def update_seat_availability(widgets):
 chosen=[widget.currentData() for widget in widgets]
 for widget in widgets:
  for index in range(widget.count()):
   identity=widget.itemData(index);entry=widget.model().item(index)
   if entry:entry.setEnabled(identity is None or identity==widget.currentData() or identity not in chosen)

def fill_seat_choice(widget,entries,identity):
 blocker=QSignalBlocker(widget);widget.setEditable(False);widget.clear();widget.addItem(tr('请选择选手'),None)
 for label,player_id in entries:
  widget.addItem(label,player_id);widget.setItemData(widget.count()-1,player_id,Qt.ItemDataRole.ToolTipRole)
 selected(widget,identity)

def fill_seat_choices(widgets,roster,seats):
 entries=seat_choice_entries(roster)
 for widget,identity in zip(widgets,seats):fill_seat_choice(widget,entries,identity)
 update_seat_availability(widgets)


PLAYER_ACTION_MESSAGES = {'delete': '选手已删除', 'hide': '选手已屏蔽', 'restore': '选手已恢复'}


def confirm_player_action(parent, model, identity, name, *, dirty=False):
    profile = selected_profile(model.state, identity)
    if profile and dirty:
        raise ValueError(tr('请先保存名册修改，再删除、屏蔽或恢复已保存的选手'))
    reasons = removal_reasons(model, profile) if profile else []
    if profile and profile.get('hidden'):
        action, title = 'restore', '恢复选手'
        detail = '恢复后，该选手会重新进入个人榜和选人列表。'
    elif reasons:
        action, title = 'hide', '屏蔽选手'
        detail = '该选手只能屏蔽。屏蔽后从个人榜和新对局选人列表移除，保留原成绩、团队积分和当前席位。'
        detail = '\n'.join([tr(detail), *(tr(reason) for reason in reasons)])
    else:
        action, title = 'delete', '删除选手'
        detail = '删除后，该选手将从名册中永久移除。'
    event_context = (model.path, (model.event or {}).get('id'))
    question = QMessageBox(QMessageBox.Icon.Warning, tr(title), (profile or {}).get('name', name),
                           QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, parent)
    question.setTextFormat(Qt.TextFormat.PlainText)
    question.setInformativeText(tr(detail))
    question.button(QMessageBox.StandardButton.Yes).setText(tr(title))
    question.button(QMessageBox.StandardButton.No).setText(tr('取消'))
    question.setDefaultButton(QMessageBox.StandardButton.No)
    if question.exec() != QMessageBox.StandardButton.Yes:
        return None
    if event_context != (model.path, (model.event or {}).get('id')):
        raise ValueError(tr('赛事已切换，请重新选择选手'))
    if profile:
        change_player(model, identity, action)
    return action


class QuickTermBar(QWidget):
 """Reusable mouse shortcuts; model updates never overwrite an editor draft."""
 def __init__(self,panel,parent=None,columns=6,limit=None):
  super().__init__(parent);self.panel=panel;self.columns=max(1,int(columns));self.limit=limit;self.signature=None;self.buttons={};self.grid=QGridLayout(self);self.grid.setContentsMargins(0,0,0,0);self.grid.setSpacing(5)
  self.panel.model.changed.connect(self.refresh);signals.language_changed.connect(self.refresh);self.refresh()
 def refresh(self,*_):
  entries=[]
  for spec in self.panel.favorite_buttons():
   entry=self.panel.term_entry(spec['ref'])
   if entry:entries.append((spec['slot'],spec['ref'],spec.get('label',''),entry))
  if self.limit is not None:entries=entries[:max(0,int(self.limit))]
  columns=self.panel.config().get('term_button_columns',self.columns)
  signature=(get_language(),columns,tuple(entries))
  if signature==self.signature:return
  self.signature=signature;self.buttons={}
  while self.grid.count():
   item=self.grid.takeAt(0)
   if item.widget():item.widget().hide();item.widget().deleteLater()
  occupied={entry[0] for entry in entries}
  for slot in range(max(occupied,default=-1)+1):
   if slot not in occupied:
    blank=QWidget();blank.setMinimumHeight(34);self.grid.addWidget(blank,slot//columns,slot%columns)
  for slot,ref,label,entry in entries:
   title,body=self.panel.entry_display(entry);w=button(label or title,lambda checked=False,ref=ref:self.panel.show_term_entry(ref));w.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);w.setMinimumWidth(60);w.setProperty('i18n_literal',True);w.setProperty('termReference',ref);w.setToolTip(title+'\n'+body);w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);w.customContextMenuRequested.connect(lambda pos,w=w,ref=ref:self.open_menu(ref,w,pos));self.grid.addWidget(w,slot//columns,slot%columns);self.buttons[ref]=w
  if not entries:self.grid.addWidget(QLabel(tr('暂无常用术语')),0,0)
 def open_menu(self,ref,widget,pos):
  menu=QMenu(self);edit=menu.addAction(tr('编辑此术语'));remove=menu.addAction(tr('移出常用'))
  action=menu.exec(widget.mapToGlobal(pos))
  if action==edit:self.panel.edit_term_entry(ref)
  elif action==remove:self.panel.remove_favorite(ref)

from settings_window import SettingsWindow, form_values, saves_section

class BroadcastDialog(SettingsWindow):
 """Reusable nonmodal operator window. Drafts survive live score updates."""
 def __init__(self,main,*,workspace_roster=False):
  super().__init__(main);self.main=main;self.model=main.model;self.manager=main.manager;self.demo_session=DemoSession(self.model);self.loading=False;self.photo_drafts={};self.roster_original={};self.active_builtin=None;self.active_term_id=None;self.editing_term_ref=None;self.setting_term=False;self._config_drafts={}
  self.event_workspace=None;self.workspace_roster=workspace_roster
  self.setWindowTitle('赛事控制');self.setModal(False);self.resize(1130,820);self.setMinimumSize(960,650)
  root=QVBoxLayout(self);self.scene_controls=QWidget();scene_layout=QVBoxLayout(self.scene_controls);scene_layout.setContentsMargins(0,0,0,0);root.addWidget(self.scene_controls)
  self.demo_badge=QLabel();self.demo_badge.setObjectName('muted');scene_layout.addLayout(row('播出场景',button('对局',lambda:self.scene('table')),button('晋级战',lambda:self.scene('qualification')),button('赛后',lambda:self.scene('review')),button('净画面',lambda:self.scene('clean')),1,self.demo_badge))
  self.visibility={};visibility_grid=QGridLayout()
  for index,(key,label) in enumerate(EXTENSIONS.items()):
   w=QCheckBox(label);w.toggled.connect(lambda enabled,key=key:self.toggle(key,enabled));self.visibility[key]=w;visibility_grid.addWidget(w,index//5,index%5)
  scene_layout.addLayout(visibility_grid);self.tabs=SidebarPages();root.addWidget(self.tabs,1)
  self.categories={}
  self.build_presentation();self.categories['presentation']=self.tabs.widget(0);self.categories['presentation'].installEventFilter(self)
  if not self.workspace_roster:
   self.build_roster()
   self.tabs.nav.item(1).setHidden(True)
  for key,build in (('qualification',self.build_qualification),('commentary',self.build_commentary),
                    ('statistics',self.build_statistics)):
   build()
   self.categories[key]=self.tabs.widget(self.tabs.count()-1)
  self.footer=QWidget();footer=QVBoxLayout(self.footer);footer.setContentsMargins(0,0,0,0)
  footer.addLayout(row(button('重新读取',self.reload),button('保存修改',self.save_changes),1,button('记录本场积分',self.commit_match),button('新半庄',self.start_match),button('关闭',self.close)))
  root.addWidget(self.footer)
  self.status_source='';self.status=QLabel('');self.status.setWordWrap(True);set_status_style(self.status,'success');root.addWidget(self.status)
  self.status.setMinimumHeight(28)
  apply_theme(self,self.model.settings)
  self.model.changed.connect(self.on_model_changed);self.model.display_changed.connect(self.on_display_changed);self.tabs.currentChanged.connect(self.on_category_changed);self.reload()
  self.track_settings('presentation',lambda:form_values(self.title,self.event_mode,self.settlement_enabled,self.settlement_hold,self.settlement_total_hold,self.statistics_scope,self.statistics_view,self.statistics_page,self.show_match_title,self.table_score_mode,self.table_score_layout),self.save_presentation)
  self.track_settings('postmatch',self.postmatch_values,self.save_postmatch)
  if self.workspace_roster:
   self.track_technical_baselines()
  else:
   self.track_settings('roster',lambda:form_values(self.roster,*self.seats,self.photo_drafts),self.save_roster)
  self.track_settings('qualification',lambda:form_values(self.qualification_layout,self.qualification_title,self.qualification_focus,self.qualification_target,self.qualification_slots,self.qualification_mode,self.qualification_final,self.qualification_tie,self.manual_conditions,*((self.start_points,self.return_points,*self.uma,self.oka,self.tie_break) if not self.model.event_store else ())),self.save_qualification)
  self.track_settings('term',lambda:form_values(self.commentary_title,self.commentary_body),self.save_term)
  self.track_settings('commentary_style',self.commentary_style.pending_values,self.save_commentary_style)
 def after_settings_saved(self):self.reload()
 def discard_settings(self):self.reload()

 def attach_event_settings(self, workspace, parent, rules_page):
  self.hide()
  self.event_workspace=workspace
  self.setParent(parent, Qt.WindowType.Widget)
  self.setMinimumSize(0,0)
  self.layout().setContentsMargins(0,0,0,0)
  old=self.tabs
  self.tabs=QTabWidget()
  self.tabs.setUsesScrollButtons(True)
  self.categories['rules']=rules_page
  self.tabs.addTab(rules_page,'规则与设置文件')
  for key,title in (('presentation','播出与动画'),('qualification','晋级条件'),
                    ('commentary','术语与字幕'),('statistics','统计与导出')):
   self.tabs.addTab(self.categories[key],title)
  self.layout().replaceWidget(old,self.tabs)
  old.hide();self.footer.hide();self.status.hide();self.status=workspace.status
  if not self.workspace_roster:
   self._settings_sections.pop('roster',None)
   self.stats_save.clicked.disconnect(self.save_roster)
   self.stats_save.clicked.connect(self.save_technical_baselines)
   self.load_technical_baselines()
   self.track_technical_baselines()
  commentary=self.categories['commentary'].widget().layout()
  commentary.insertWidget(commentary.count()-1,button('保存字幕停留时间',self.save_commentary_duration))
  self.track_settings('commentary_duration',lambda:form_values(self.commentary_duration),self.save_commentary_duration)
  presentation=self.categories['presentation'].widget().layout()
  presentation.insertWidget(0,self.scene_controls)
  self.attach_live_animation_settings()
  presentation.addWidget(button('管理侧栏方案',self.main.open_sidebar_presets))
  self.tabs.currentChanged.connect(self.on_category_changed)
  parent.layout().addWidget(self)
  self.show()

 def select_category(self, key):
  if self.event_workspace:
   self.tabs.setCurrentWidget(self.categories[key])
  else:
   self.tabs.setCurrentIndex(self.tabs.stack.indexOf(self.categories[key]))

 def is_qualification_page(self):
  return self.tabs.widget(self.tabs.currentIndex()) is self.categories['qualification']

 def on_category_changed(self,*_):
  if self.is_qualification_page() and not self.loading:
   self.refresh_conditions()

 def eventFilter(self,watched,event):
  if watched is getattr(self,'categories',{}).get('presentation') and event.type()==QEvent.Type.Hide:
   self.stop_music_audition()
  return super().eventFilter(watched,event)

 def build_animation_controls(self, page):
  self.animation_group=QGroupBox('立直特效预览');layout=QHBoxLayout(self.animation_group)
  self.animation_form=QFormLayout()
  self.riichi_preview=RiichiAnimationPreview(self.model)
  self.riichi_preview.setAccessibleName(tr('立直特效预览'))
  self.riichi_preview_seat=QComboBox();self.riichi_preview_seat.setProperty('i18n_literal',True)
  self.riichi_preview_seat.currentIndexChanged.connect(self.riichi_preview.stop)
  self.riichi_preview_button=button('播放立直预览',self.play_riichi_preview)
  self.riichi_preview_button.setAutoDefault(False)
  self.animation_form.addRow('预览选手',self.riichi_preview_seat)
  self.animation_form.addRow('',row(self.riichi_preview_button,button('停止预览',self.riichi_preview.stop),1))
  layout.addLayout(self.animation_form,1);layout.addWidget(self.riichi_preview,2);page.addWidget(self.animation_group)
  signals.language_changed.connect(self.riichi_preview.invalidate)

 def attach_live_animation_settings(self):
  self.animation_group.setTitle('直播动画')
  self.live_speed=decimal(.1,10,2);self.live_speed.setSuffix(' ×');self.live_speed.setSingleStep(.25)
  self.live_riichi=QCheckBox('启用立直特效')
  self.animation_form.insertRow(0,'动画速度',self.live_speed)
  self.animation_form.insertRow(1,'',self.live_riichi)
  self.animation_form.insertRow(2,'',button('保存直播动画',self.save_live_animations))
  self.load_live_animations()
  self.track_settings('live_animation',lambda:form_values(self.live_speed,self.live_riichi),self.save_live_animations)

 def refresh_riichi_preview_seats(self):
  players=self.model.state['players']
  signature=(self.model.path,tuple((p['id'],p['name']) for p in players))
  if signature==getattr(self,'_riichi_preview_roster',None):return
  self._riichi_preview_roster=signature;self.riichi_preview.stop()
  seat=self.riichi_preview_seat.currentData();blocker=QSignalBlocker(self.riichi_preview_seat)
  self.riichi_preview_seat.clear()
  for index,player in enumerate(players):self.riichi_preview_seat.addItem(f'{index+1} · {player["name"]}',index)
  selected(self.riichi_preview_seat,seat);del blocker
  self.riichi_preview_button.setEnabled(bool(players))

 def play_riichi_preview(self):
  seat=self.riichi_preview_seat.currentData()
  if seat is not None:self.riichi_preview.play(seat)

 def animation_values(self):
  return dict(speed=self.model.settings.get('speed',1.),
              riichi_effect_enabled=self.config().get('riichi_effect_enabled',True))

 def animation_draft(self):
  return dict(speed=self.live_speed.value(),
              riichi_effect_enabled=self.live_riichi.isChecked())

 def load_live_animations(self):
  self._animation_live=self.animation_values()
  self.live_speed.setValue(self._animation_live['speed'])
  self.live_riichi.setChecked(self._animation_live['riichi_effect_enabled'])
  self._animation_draft=self.animation_draft()

 @saves_section('live_animation')
 def save_live_animations(self):
  def save():
   from settings_drafts import merge_draft
   if not self.live_speed.hasAcceptableInput():raise ValueError('动画速度尚未填写完整')
   self.live_speed.interpretText()
   merged,changed,conflicts=merge_draft(self._animation_draft,self.animation_draft(),
                                      self._animation_live,self.animation_values())
   if conflicts:raise ValueError('同一动画设置已在其他窗口修改，请重新读取后再保存')
   if changed:
    settings=dict(self.model.settings,**{key:merged[key] for key in changed if key!='riichi_effect_enabled'})
    if 'riichi_effect_enabled' in changed:
     self.model.configure_presentation({'riichi_effect_enabled':merged['riichi_effect_enabled']},settings)
    else:self.model.save_preferences(settings)
   self.load_live_animations()
  self.run(save,'直播动画已保存到当前赛事')

 def page(self,title):
  scroll=QScrollArea();scroll.setWidgetResizable(True);body=QWidget();layout=QVBoxLayout(body);layout.setContentsMargins(16,14,16,14);layout.setSpacing(12);scroll.setWidget(body);self.tabs.addTab(scroll,title);return layout
 def run(self,callback,success=''):
  try:
   result=callback();set_status_style(self.status,'success');self.status_source=success;self.status.setText(tr(success));self.refresh_live();return result
  except Exception as error:self._save_failed=True;set_status_style(self.status,'error');self.status_source=str(error);self.status.setText(tr(str(error)));return None
 def config(self):return self.model.state['broadcast']['config']
 def _config_draft(self,section):
  return {'presentation':self.presentation_values,'postmatch':self.postmatch_values,'qualification':self.qualification_values}[section]()
 def _config_live(self,section,draft):
  live={key:copy.deepcopy(self.config().get(key)) for key in draft}
  if section=='postmatch':
   from postmatch_config import postmatch_options
   return postmatch_options(self.config())
  if section=='presentation':
   defaults=dict(statistics_scope='all',statistics_view='rates',statistics_page=0,show_match_title=True)
   for key,default in defaults.items():live[key]=copy.deepcopy(self.config().get(key,default))
   live['_bottom_mode']=self.manager.bottom_mode()
  if section=='qualification':
   from qualification_presentation import qualification_context
   context=qualification_context(self.model);live.update(qualification_mode=context['mode'],qualification_slots=context['slots'])
  return live
 def _capture_config_draft(self,section):
  draft=self._config_draft(section);self._config_drafts[section]=(copy.deepcopy(draft),self._config_live(section,draft))
 def _refresh_config_draft(self,section):
  """Reload only the committed section so its next baseline matches the form."""
  values=self._config_live(section,self._config_draft(section));loading=self.loading;self.loading=True
  try:
   if section=='postmatch':self.load_postmatch_controls(self.config())
   else:
    names={'presentation':{'title':'title','event_mode':'event_mode','settlement_enabled':'settlement_enabled','settlement_hold':'settlement_hold','settlement_total_hold':'settlement_total_hold','statistics_scope':'statistics_scope','statistics_view':'statistics_view','statistics_page':'statistics_page','show_match_title':'show_match_title','table_score_mode':'table_score_mode','table_score_layout':'table_score_layout','_bottom_mode':'standings_mode'},
           'qualification':{'qualification_layout':'qualification_layout','qualification_title':'qualification_title','qualification_focus':'qualification_focus','qualification_mode':'qualification_target','qualification_slots':'qualification_slots','qualification_calculation':'qualification_mode','qualification_final':'qualification_final','qualification_tie_break':'qualification_tie','start_points':'start_points','return_points':'return_points','oka_enabled':'oka','tie_break':'tie_break'}}[section]
    for key,name in names.items():
     if key not in values:continue
     control=getattr(self,name);value=values[key]
     if isinstance(control,QComboBox):selected(control,value)
     elif isinstance(control,QCheckBox):control.setChecked(value)
     elif isinstance(control,QLineEdit):control.setText(value)
     else:control.setValue(value+1 if key=='statistics_page' else value)
    if section=='qualification':
     for index in range(self.manual_conditions.rowCount()):
      identity=self.manual_conditions.item(index,0).data(Qt.ItemDataRole.UserRole)
      self.manual_conditions.item(index,1).setText(values.get('manual_conditions',{}).get(identity,''))
      self.manual_conditions.item(index,2).setText(values.get('qualification_titles',{}).get(identity,''))
     if 'uma' in values:
      for control,value in zip(self.uma,values['uma']):control.setValue(value)
     self.update_qualification_unit()
  finally:self.loading=loading
  self._capture_config_draft(section)
 def _merge_config_draft(self,section):
  from settings_drafts import merge_draft
  draft=self._config_draft(section);baseline,original=self._config_drafts[section]
  merged,changed,conflicts=merge_draft(baseline,draft,original,self._config_live(section,draft))
  if conflicts:raise ValueError('同一设置已在其他窗口修改，请重新读取后再修改并保存。')
  return merged,changed
 def patch(self,values,message='已应用'):
  from sidebar_presets import SIDEBAR_SETTING_KEYS
  command=self.model.configure_sidebar if set(values)<=SIDEBAR_SETTING_KEYS|{'sidebar_presets','sidebar_duration'} else self.model.configure_broadcast
  return self.run(lambda:command(values),message)
 def build_presentation(self):
  page=self.page('播出');form=QFormLayout();self.title=QLineEdit();self.title.setMaxLength(100)
  self.build_animation_controls(page)
  self.build_opening_controls(page)
  self.build_postmatch_controls(page)
  self.event_mode=choice([('个人赛','individual'),('队伍赛','team')])
  self.settlement_enabled=QCheckBox('允许手动展示赛后结算');self.settlement_hold=decimal(1,30,1);self.settlement_total_hold=decimal(1,30,1)
  for duration in (self.settlement_hold,self.settlement_total_hold):duration.setSingleStep(.5);duration.setSuffix(' 秒')
  self.settlement_enabled.toggled.connect(self.update_settlement_controls)
  self.standings_mode=choice([('选手信息','players'),('个人积分','individual'),('队伍积分','team')]);self.standings_mode.activated.connect(lambda _:self.run(lambda:self.manager.set_bottom_mode(self.standings_mode.currentData())))
  self.statistics_scope=choice([('累计统计','all'),('当前半庄','match')]);self.statistics_view=choice([('技术数据','rates'),('比赛结果','result')]);self.statistics_page=integer(1,999)
  self.sidebar_mode=choice([('单人条件','qualification'),('队伍排名','team'),('个人排名','individual')]);self.sidebar_page=integer(1,999);self.sidebar_page_size=integer(2,10)
  self.sidebar_scope=choice([('当前四席','current'),('全员分页','all')]);self.sidebar_auto_page=QCheckBox('自动切页');self.sidebar_page_interval=integer(5,60);self.sidebar_page_interval.setSuffix(' 秒')
  self.sidebar_duration=integer(0,3600);self.sidebar_duration.setSpecialValueText('持续展示');self.sidebar_duration.setSuffix(' 秒');self.sidebar_duration.setToolTip('展示指定秒数后自动收起；设为0时持续展示。')
  self.sidebar_close_button=button('关闭侧栏',lambda:self.run(lambda:self.manager.set_extension_visible('qualification',False),'侧栏已关闭'))
  self.fixed_first_page_button=button('固定第一页',self.fixed_first_sidebar_page)
  self.sidebar_mode.activated.connect(lambda _:self.set_sidebar_mode())
  self.sidebar_scope.activated.connect(self.save_sidebar_page);self.sidebar_auto_page.toggled.connect(self.save_sidebar_page);self.sidebar_page_interval.valueChanged.connect(self.save_sidebar_page)
  self.sidebar_page.valueChanged.connect(self.save_sidebar_page);self.sidebar_page_size.valueChanged.connect(self.save_sidebar_page);self.sidebar_duration.valueChanged.connect(self.save_sidebar_page)
  self.show_match_title=QCheckBox('显示局况上方赛事标题');self.replay_chart_button=button('重播点数走势',lambda:self.run(self.manager.replay_chart))
  self.table_score_mode=choice([('自动：两把制显示第一把，其余累计','auto'),('第一半庄结果','first_game'),('累计 pt','cumulative')])
  self.table_score_layout=choice([('紧凑双行','compact'),('四人横排','columns')])
  form.addRow('赛事标题',self.title);form.addRow('比赛类型',self.event_mode);form.addRow('',self.settlement_enabled)
  for control in (self.settlement_hold,self.settlement_total_hold):control.setParent(self);control.hide()
  form.addRow('左侧内容',self.sidebar_mode);form.addRow('排名范围',self.sidebar_scope);form.addRow('翻页',row(self.sidebar_auto_page,self.sidebar_page_interval,self.fixed_first_page_button,1));form.addRow('侧栏页码',row(self.sidebar_page,'每页',self.sidebar_page_size,1));form.addRow('底部显示',self.standings_mode);form.addRow('点数走势',self.replay_chart_button)
  form.addRow('侧栏收起',row(self.sidebar_duration,self.sidebar_close_button,1))
  form.addRow('统计范围',self.statistics_scope);form.addRow('统计版式',self.statistics_view);form.addRow('统计页码',self.statistics_page);form.addRow('',self.show_match_title)
  self.place_table_scores_button=button('定位到局况旁',lambda:self.run(self.manager.place_table_scores))
  form.addRow('上方积分',row(self.table_score_mode,self.place_table_scores_button,1))
  form.addRow('积分条版式',self.table_score_layout)
  page.addLayout(form);page.addLayout(row(button('保存显示设置',self.save_presentation),1))
  self.match_summary=QLabel();self.match_summary.setWordWrap(True);page.addWidget(self.match_summary);page.addWidget(note('每个模块都是独立透明窗口；在“布局”中调位置和尺寸。新半庄保留已记录的赛事数据，积分需单独记录。'));page.addStretch()
 def build_opening_controls(self,page):
  group=QGroupBox('赛前开场');layout=QVBoxLayout(group)
  self.opening_title_label=QLabel();self.opening_title_label.setWordWrap(True);self.opening_title_label.setProperty('i18n_literal',True)
  layout.addLayout(row('标题卡',self.opening_title_label,button('选择本地图片',self.choose_opening_title),button('使用默认封面',lambda:self.run(lambda:self.model.configure_opening({'opening_title':''}))),button('使用赛事文字',lambda:self.run(lambda:self.model.configure_opening({'opening_title':'','opening_title_text_only':True}))),1))
  previews=QHBoxLayout()
  from match_opening import DESIGN_SIZE
  from corner_header import CORNER_SIZE
  for title,size,draw,attr in (
      ('标题卡预览',DESIGN_SIZE,self.draw_opening_title_preview,'opening_title_preview'),
      ('赛事标识预览',CORNER_SIZE,self.draw_corner_preview,'corner_preview')):
   section=QGroupBox(title);section_layout=QVBoxLayout(section)
   preview=ArtworkPreview(size,draw,height=180);setattr(self,attr,preview)
   section_layout.addWidget(preview)
   if attr=='corner_preview':
    self.corner_editor_button=button('赛事标识与 Logo 设置',self.open_corner_editor)
    section_layout.addWidget(self.corner_editor_button)
   else:section_layout.addStretch()
   previews.addWidget(section,1)
  layout.addLayout(previews)
  self.title.textChanged.connect(self.opening_title_preview.invalidate)
  self.opening_play=button('播放完整开场',lambda:self.run(lambda:self.manager.programme.select('opening')))
  self.opening_stop=button('停止开场',self.manager.opening.stop)
  layout.addLayout(row(self.opening_play,self.opening_stop,1))
  self.opening_status=QLabel();self.opening_status.setWordWrap(True);self.opening_status.setProperty('i18n_literal',True);layout.addWidget(self.opening_status)
  layout.addWidget(note('标题卡、四队全员名单、本场出战选手连续播放约18秒，随后衔接四席入场；在选手名单中调整本场四席。'))
  page.addWidget(group);self.manager.opening.changed.connect(self.refresh_opening_controls)
 def choose_opening_title(self):
  from media_store import resolve_media_path
  reference=self.config().get('opening_title','')
  start=str(resolve_media_path(self.model.path,reference)) if reference else ''
  path,_=QFileDialog.getOpenFileName(self,'选择开场标题卡',start,'图片 (*.png *.jpg *.jpeg *.webp *.bmp)')
  if path:self.run(lambda:self.model.configure_opening({'opening_title':path}),'标题卡已复制到项目素材库，随当前赛事保存')
 def refresh_opening_controls(self):
  from opening_config import opening_title_reference
  reference=opening_title_reference(self.model);playback=self.manager.opening
  self.opening_title_label.setText('本地标题卡（已保存到项目）' if reference.startswith('assets/imported/') else Path(reference).name if reference else '赛事标题文字')
  self.opening_play.setEnabled(self.model.state.get('phase')=='playing' and self.config().get('event_mode')=='team' and not playback.active)
  self.opening_stop.setEnabled(playback.active);self.opening_status.setText(playback.status_text())
  signature=(self.model.path,reference,self.config().get('title'),self.model.state.get('title'),
             (self.model.event or {}).get('name'),self.model.settings.get('overlay_font_family'),self.model.settings.get('overlay_font_scale'))
  if signature!=getattr(self,'_opening_preview_signature',None):
   self._opening_preview_signature=signature;self.opening_title_preview.invalidate()
 def draw_opening_title_preview(self,painter):
  from match_opening import draw_opening_title_preview
  broadcast=dict(self.model.state['broadcast'],config=dict(self.config(),title=self.title.text().strip()))
  model=SimpleNamespace(state=dict(self.model.state,broadcast=broadcast),path=self.model.path,
                        settings=self.model.settings,event=self.model.event,asset_root=self.model.asset_root)
  draw_opening_title_preview(painter,model)
 def draw_corner_preview(self,painter):
  from broadcast_artwork import draw_extension
  draw_extension(painter,'corner_header',self.model)
 def refresh_corner_preview(self):
  from corner_header import CORNER_SETTING_KEYS
  signature=(self.model.path,{key:self.config().get(key) for key in CORNER_SETTING_KEYS})
  if signature!=getattr(self,'_corner_preview_signature',None):
   from corner_header import corner_canvas_size
   self._corner_preview_signature=signature;self.corner_preview.set_design_size(corner_canvas_size(self.config()));self.corner_preview.invalidate()
 def open_corner_editor(self):
  if callable(getattr(self.main,'open_corner_header',None)):return self.main.open_corner_header()
  from corner_editor import CornerHeaderDialog
  if not getattr(self,'corner_dialog',None):self.corner_dialog=CornerHeaderDialog(self.main,self)
  if not self.corner_dialog.isVisible():self.corner_dialog.reload()
  self.corner_dialog.show();self.corner_dialog.raise_();self.corner_dialog.activateWindow();return self.corner_dialog
 def build_postmatch_controls(self,page):
  group=QGroupBox('赛后连播 · 折线图 → 简易统计');layout=QVBoxLayout(group)
  self.postmatch_play=button('播放 / 重播',self.play_postmatch)
  self.postmatch_pause=button('暂停',self.manager.postmatch.pause_resume)
  self.postmatch_next=button('转入统计',self.manager.postmatch.next_page)
  self.postmatch_stop=button('停止画面与声音',self.manager.postmatch.stop)
  layout.addLayout(row(self.postmatch_play,self.postmatch_pause,self.postmatch_next,self.postmatch_stop,1))
  self.postmatch_status=QLabel();self.postmatch_status.setWordWrap(True);self.postmatch_status.setProperty('i18n_literal',True);layout.addWidget(self.postmatch_status)
  form=QFormLayout();self.postmatch_chart_hold=decimal(1,300,1);self.postmatch_stats_hold=decimal(1,300,1)
  for w in (self.postmatch_chart_hold,self.postmatch_stats_hold):w.setSuffix(' 秒');w.setSingleStep(1)
  form.addRow('完整画面停留',row('折线图',self.postmatch_chart_hold,'简易统计',self.postmatch_stats_hold,1))
  self.postmatch_music=QCheckBox('背景音乐');self.postmatch_sfx=QCheckBox('转场音效')
  self.postmatch_music.toggled.connect(self.toggle_postmatch_music)
  self.postmatch_music_volume=integer(0,100);self.postmatch_sfx_volume=integer(0,100)
  for w in (self.postmatch_music_volume,self.postmatch_sfx_volume):w.setSuffix('%')
  form.addRow('声音',row(self.postmatch_music,self.postmatch_music_volume,self.postmatch_sfx,self.postmatch_sfx_volume,1))
  self.postmatch_music_path='';self.postmatch_background_path=''
  self.postmatch_music_label=QLabel();self.postmatch_music_label.setWordWrap(True);self.postmatch_music_label.setProperty('i18n_literal',True)
  self.postmatch_background_label=QLabel();self.postmatch_background_label.setWordWrap(True);self.postmatch_background_label.setProperty('i18n_literal',True)
  form.addRow('音乐来源',row(self.postmatch_music_label,button('选择音乐',self.choose_postmatch_music),button('清除音乐',lambda:self.set_postmatch_media('music','')),1))
  self._postmatch_audition=None
  self._audition_timer=QTimer(self);self._audition_timer.setSingleShot(True);self._audition_timer.setTimerType(Qt.TimerType.PreciseTimer)
  self._audition_timer.timeout.connect(self.stop_music_audition)
  self.postmatch_audition_button=button('试听音乐',self.toggle_music_audition)
  self.postmatch_audition_button.setAutoDefault(False)
  self.postmatch_audition_status=QLabel();self.postmatch_audition_status.setWordWrap(True);self.postmatch_audition_status.setProperty('i18n_literal',True)
  form.addRow('',row(self.postmatch_audition_button,self.postmatch_audition_status,1))
  self.postmatch_music_volume.valueChanged.connect(self.stop_music_audition)
  form.addRow('磨砂背景',row(self.postmatch_background_label,button('选择图片',self.choose_postmatch_background),button('恢复默认背景',lambda:self.set_postmatch_media('background','')),1))
  layout.addLayout(form);layout.addLayout(row(button('应用连播设置',self.save_postmatch),1))
  layout.addWidget(note('选择本地音乐后可试听和循环播放。未选择音乐时，开场和片尾只播放已启用的音效。'))
  page.addWidget(group)
  self.manager.postmatch.changed.connect(self.refresh_postmatch_controls)
  self.manager.postmatch.failed.connect(self.postmatch_failed)
  live_music=getattr(getattr(self.manager,'programme',None),'music',None)
  if live_music is not None:live_music.changed.connect(self.stop_audition_for_live_music)

 def live_music_enabled(self):
  live_music=getattr(getattr(self.manager,'programme',None),'music',None)
  return self.manager.postmatch.music_active or bool(live_music and live_music.enabled)
 def stop_audition_for_live_music(self):
  if self._postmatch_audition and self._postmatch_audition.enabled and self.live_music_enabled():self.stop_music_audition()
 def toggle_music_audition(self):
  if self._postmatch_audition and self._postmatch_audition.enabled:
   self.stop_music_audition();return
  if not self.postmatch_music_path:
   self.postmatch_audition_status.setText(tr('请先选择本地音乐'))
   return
  if self.live_music_enabled():
   self.postmatch_audition_status.setText(tr('请先停止当前播出的音乐'))
   return
  volume=self.postmatch_music_volume.value()/100.
  if volume==0:
   self.postmatch_audition_status.setText(tr('提高音乐音量后可试听'))
   return
  if self._postmatch_audition is None:
   from programme import ProgrammeMusic
   audition=ProgrammeMusic(self.model);self._postmatch_audition=audition
   audition.changed.connect(self.refresh_music_audition)
   audition.failed.connect(self.postmatch_audition_status.setText)
   self.destroyed.connect(audition.close)
  self._postmatch_audition.audition(self.postmatch_music_path,volume,POSTMATCH_AUDITION_SECONDS)
 def stop_music_audition(self,*_):
  if self._postmatch_audition and self._postmatch_audition.enabled:
   self._postmatch_audition.set_enabled(False)
  self.postmatch_audition_status.clear()
 def refresh_music_audition(self):
  audition=self._postmatch_audition
  self.postmatch_audition_button.setText(tr('停止试听' if audition.enabled else '试听音乐'))
  if not audition.enabled:self._audition_timer.stop()
  elif audition.preparing:self.postmatch_audition_status.setText(tr('正在准备试听…'))
  elif audition.audio:
   self.postmatch_audition_status.setText(tr('正在试听 · {seconds} 秒').format(seconds=POSTMATCH_AUDITION_SECONDS))
   self._audition_timer.start(POSTMATCH_AUDITION_SECONDS*1000)
 def postmatch_values(self):
  return {'postmatch_chart_hold':self.postmatch_chart_hold.value(),'postmatch_stats_hold':self.postmatch_stats_hold.value(),
          'postmatch_music_enabled':self.postmatch_music.isChecked(),'postmatch_sfx_enabled':self.postmatch_sfx.isChecked(),
          'postmatch_music_volume':self.postmatch_music_volume.value()/100.,'postmatch_sfx_volume':self.postmatch_sfx_volume.value()/100.,
          'postmatch_music_path':self.postmatch_music_path,'postmatch_background_path':self.postmatch_background_path}
 def load_postmatch_controls(self,config):
  from postmatch_config import postmatch_options
  values=postmatch_options(config)
  self.postmatch_chart_hold.setValue(values['postmatch_chart_hold']);self.postmatch_stats_hold.setValue(values['postmatch_stats_hold'])
  self.postmatch_music.setChecked(values['postmatch_music_enabled']);self.postmatch_sfx.setChecked(values['postmatch_sfx_enabled'])
  self.postmatch_music_volume.setValue(round(values['postmatch_music_volume']*100));self.postmatch_sfx_volume.setValue(round(values['postmatch_sfx_volume']*100))
  self.set_postmatch_media('music',values['postmatch_music_path']);self.set_postmatch_media('background',values['postmatch_background_path'])
  self.refresh_postmatch_controls()
 def set_postmatch_media(self,kind,path):
  if kind=='music' and path!=self.postmatch_music_path:self.stop_music_audition()
  setattr(self,'postmatch_'+kind+'_path',path)
  label=getattr(self,'postmatch_'+kind+'_label');name=('本地背景音乐' if kind=='music' else '本地背景图片') if path.startswith('assets/imported/') else Path(path).name
  label.setText(name if path else tr('未选择音乐' if kind=='music' else '默认磨砂背景'));label.setToolTip(path)
 def toggle_postmatch_music(self,enabled):
  if self.loading:return
  self.run(lambda:self.model.configure_postmatch({'postmatch_music_enabled':bool(enabled)}))
  self.manager.postmatch.set_music_enabled(enabled)
 def choose_postmatch_music(self):
  from media_store import resolve_media_path
  start=str(resolve_media_path(self.model.path,self.postmatch_music_path)) if self.postmatch_music_path else ''
  path,_=QFileDialog.getOpenFileName(self,'选择赛后背景音乐',start,'音乐 (*.wav *.mp3 *.flac *.ogg *.m4a *.aac);;所有文件 (*)')
  if path:
   from media_store import import_media_file
   self.run(lambda:self.set_postmatch_media('music',import_media_file(self.model.path,path,'postmatch_music_path')),'音乐已复制到项目素材库')
 def choose_postmatch_background(self):
  from media_store import resolve_media_path
  start=str(resolve_media_path(self.model.path,self.postmatch_background_path)) if self.postmatch_background_path else ''
  path,_=QFileDialog.getOpenFileName(self,'选择赛后磨砂背景原图',start,'图片 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*)')
  if path:
   from media_store import import_media_file
   self.run(lambda:self.set_postmatch_media('background',import_media_file(self.model.path,path,'postmatch_background_path')),'背景已复制到项目素材库')
 @saves_section('postmatch')
 def save_postmatch(self):
  return self.run(self._save_postmatch_draft,'赛后连播设置已保存到当前赛事；下次播放生效')
 def _save_postmatch_draft(self):
  merged,changed=self._merge_config_draft('postmatch')
  if changed:self.model.configure_postmatch({key:merged[key] for key in changed})
  self._refresh_config_draft('postmatch')
 def play_postmatch(self):
  def play():
   self.stop_music_audition()
   self._save_postmatch_draft();self.mark_saved('postmatch');self.manager.programme.select('ending')
  self.run(play)
 def postmatch_failed(self,message):
  self.status_source=message;self.status.setText(message);set_status_style(self.status,'error')
 def refresh_postmatch_controls(self):
  playback=self.manager.postmatch
  self.stop_audition_for_live_music()
  self.postmatch_play.setEnabled(self.model.state.get('phase')=='finished' and not playback.preparing)
  self.postmatch_pause.setEnabled(playback.can_pause);self.postmatch_stop.setEnabled(playback.active or playback.preparing or playback.music_active or playback.paused)
  self.postmatch_next.setEnabled(playback.active and playback.elapsed<5.967+playback.chart_hold)
  self.postmatch_pause.setText(tr('继续' if playback.paused else '暂停'));self.postmatch_status.setText(playback.status_text())
 def update_sidebar_controls(self):
  ranking=self.sidebar_mode.currentData() in ('individual','team');all_players=self.sidebar_scope.currentData()=='all';automatic=self.sidebar_auto_page.isChecked()
  self.sidebar_scope.setEnabled(ranking);self.sidebar_auto_page.setEnabled(ranking and all_players);self.sidebar_page_interval.setEnabled(ranking and all_players and automatic)
  self.sidebar_page_size.setEnabled(ranking and all_players);self.sidebar_page.setEnabled(ranking and all_players and not automatic)
  self.sidebar_close_button.setEnabled(self.manager.enabled.get('qualification',False))
 def update_table_score_controls(self):
  is_team=(self.model.event or {}).get('kind')=='team_league' or self.config().get('event_mode')=='team'
  for widget in (self.visibility['table_scores'],self.table_score_mode,self.table_score_layout,self.place_table_scores_button):widget.setEnabled(not is_team)
 def update_settlement_controls(self,*_):
  for widget in (self.settlement_hold,self.settlement_total_hold):widget.setEnabled(self.settlement_enabled.isChecked())
 @saves_section('presentation')
 def save_presentation(self):
  def save():
   merged,changed=self._merge_config_draft('presentation');values={key:merged[key] for key in changed if key!='_bottom_mode'}
   if values:self.model.configure_broadcast(values)
   if '_bottom_mode' in changed:self.manager.set_bottom_mode(merged['_bottom_mode'])
   self._refresh_config_draft('presentation')
  self.run(save,'已应用')
 def presentation_values(self):
  return {'title':self.title.text().strip(),'event_mode':self.event_mode.currentData(),'settlement_enabled':self.settlement_enabled.isChecked(),'settlement_hold':self.settlement_hold.value(),'settlement_total_hold':self.settlement_total_hold.value(),'statistics_scope':self.statistics_scope.currentData(),'statistics_view':self.statistics_view.currentData(),'statistics_page':self.statistics_page.value()-1,'show_match_title':self.show_match_title.isChecked(),'table_score_mode':self.table_score_mode.currentData(),'table_score_layout':self.table_score_layout.currentData(),'_bottom_mode':self.standings_mode.currentData()}
 def set_sidebar_mode(self):
  if self.loading:return
  mode=self.sidebar_mode.currentData()
  self.run(lambda:self.manager.set_sidebar_mode(mode),'已应用')
 def save_sidebar_page(self,*_):
  if self.loading:return
  self.patch({'sidebar_page':self.sidebar_page.value()-1,'sidebar_page_size':self.sidebar_page_size.value(),'sidebar_scope':self.sidebar_scope.currentData(),'sidebar_auto_page':self.sidebar_auto_page.isChecked(),'sidebar_page_interval':self.sidebar_page_interval.value(),'sidebar_duration':self.sidebar_duration.value()})
  self.update_sidebar_controls()
 def fixed_first_sidebar_page(self):
  def show():
   self.model.configure_sidebar({'sidebar_mode':'individual','sidebar_scope':'all','sidebar_auto_page':False,'sidebar_page':0})
   self.manager.set_sidebar_mode('individual',True)
  self.run(show,'个人排名已固定在第一页')
 def build_roster(self):
  page=self.page('名册与积分');page.addWidget(note('历史积分填写本场开始前的累计值。同一队伍编号的成员积分相加；全队基准分只填在一名成员名下。'))
  self.roster=grid_table(['姓名','队名','队伍编号','颜色','基准积分','已赛场次','名册状态','参赛状态','照片']);self.roster.setItemDelegateForColumn(3,ColorDelegate(self.roster));self.roster.setMinimumHeight(270);self.roster.itemChanged.connect(self.refresh_seat_choices);page.addWidget(self.roster);page.addLayout(row(button('添加选手',self.add_player),button('删除 / 屏蔽选手',self.remove_player),1))
  group=QGroupBox('本场四席');grid=QGridLayout(group);self.seats=[]
  for index in range(4):
   w=QComboBox();w.setEditable(False);w.setProperty('i18n_literal',True);self.seats.append(w);w.currentIndexChanged.connect(lambda *_:update_seat_availability(self.seats));grid.addWidget(QLabel(f'席位 {index+1}'),0,index);grid.addWidget(w,1,index)
  page.addWidget(group);page.addWidget(note('选中已屏蔽的选手，再点「删除 / 屏蔽选手」可恢复。当前半庄结束并确认后，可选择下一场阵容。'));page.addLayout(row(button('保存名册与席位',self.save_roster),button('选择四席并开始新半庄',self.start_selected_match),1));page.addStretch()
 def roster_item(self,index,column):
  if column==7 and self.roster.cellWidget(index,column):return self.roster.cellWidget(index,column).currentData()
  item=self.roster.item(index,column);return item.text().strip() if item else ''
 def append_roster(self,player):
  index=self.roster.rowCount();self.roster.insertRow(index)
  values=[player.get('name',''),player.get('team',''),player.get('team_id',''),player.get('color','#377e72'),player.get('base_points',0),player.get('games',0),'已屏蔽' if player.get('hidden') else '可选']
  for column,value in enumerate(values):
   item=QTableWidgetItem(str(value))
   if column==0:item.setData(Qt.ItemDataRole.UserRole,player['id'])
   if column==6:item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable)
   if column in (1,2) and self.managed_team_roster():
    item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable)
    item.setToolTip(tr('请在赛事工作台的选手名册中选择所属队伍'))
   self.roster.setItem(index,column,item)
  self.set_photo_button(index,player)
  state=choice(PLAYER_STATUSES);selected(state,player.get('status','active'));self.roster.setCellWidget(index,7,state)
 def set_photo_button(self,index,player):
  from identity_media import identity_preview_image
  control=button('选择照片',lambda checked=False,identity=player['id']:self.pick_roster_photo(identity));photo=self.photo_drafts.get(player['id'],player.get('photo',''))
  control.setIcon(QIcon(QPixmap.fromImage(identity_preview_image(self.model,dict(player,photo=photo),48,60))));control.setToolTip(player.get('name',''))
  control.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
  def menu(position):
   actions=QMenu(control);clear=actions.addAction(tr('清除照片'))
   if actions.exec(control.mapToGlobal(position))==clear:self.photo_drafts[player['id']]='';self.set_photo_button(index,player)
  control.customContextMenuRequested.connect(menu)
  self.roster.setCellWidget(index,8,control)
 def pick_roster_photo(self,identity):
  filename,_=QFileDialog.getOpenFileName(self,tr('选手图片'),'','图片 (*.png *.jpg *.jpeg *.webp)')
  if not filename:return
  from media_store import manage_photos
  def pick():
   profile={'id':identity,'photo':filename};manage_photos(profile,self.model.path,strict=True);self.photo_drafts[identity]=profile['photo']
   for index in range(self.roster.rowCount()):
    if self.roster.item(index,0).data(Qt.ItemDataRole.UserRole)==identity:self.set_photo_button(index,profile);break
  self.run(pick,'照片已选，保存名册后生效')
 def add_player(self):
  player={'id':uuid.uuid4().hex,'name':f'选手{self.roster.rowCount()+1}','stats':{key:0 for key,_ in COUNTERS}};self.roster_original[player['id']]=copy.deepcopy(player);self.loading=True;self.append_roster(player);self.append_baseline(player);self.loading=False;self.refresh_seat_choices()
 def remove_player(self):
  cell=self.roster.item(self.roster.currentRow(),0)
  if cell is None:
   self.status.setText(tr('请先选中一名选手'))
   return
  identity=cell.data(Qt.ItemDataRole.UserRole)
  saved=selected_profile(self.model.state,identity)
  try:
   action=confirm_player_action(self,self.model,identity,cell.text(),dirty='roster' in self.dirty_sections())
  except (ValueError,OSError) as error:
   set_status_style(self.status,'error');self.status.setText(str(error));return
  if not action:return
  if not saved:
   self.loading=True
   self.roster.removeRow(cell.row())
   for index in range(self.baselines.rowCount()-1,-1,-1):
    if self.baselines.item(index,0).data(Qt.ItemDataRole.UserRole)==identity:self.baselines.removeRow(index)
   self.photo_drafts.pop(identity,None);self.roster_original.pop(identity,None)
   self.loading=False;self.refresh_seat_choices()
  self.status_source=PLAYER_ACTION_MESSAGES[action]
  set_status_style(self.status,'success');self.status.setText(tr(self.status_source))
 def refresh_seat_choices(self,*_):
  if self.loading:return
  entries=[dict(name=self.roster_item(i,0),team=self.roster_item(i,1),id=self.roster.item(i,0).data(Qt.ItemDataRole.UserRole)) for i in range(self.roster.rowCount()) if self.roster.item(i,0)]
  hidden={p['id'] for p in self.model.state['broadcast']['roster'] if p.get('hidden')}
  entries=[entry for entry in entries if entry['id'] not in hidden]
  fill_seat_choices(self.seats,entries,[w.currentData() for w in self.seats])
 def collect_roster(self):
  # Merge against live state: this nonmodal form may have remained open while
  # profiles, points or the event roster were edited in another window.
  current={p['id']:p for p in self.model.state['broadcast']['roster']}
  baselines=self.technical_draft()
  players=[];draft_ids=set()
  for index in range(self.roster.rowCount()):
   identity=self.roster.item(index,0).data(Qt.ItemDataRole.UserRole);values=[self.roster_item(index,c) for c in range(8)];draft_ids.add(identity)
   if identity in self.loaded_roster_ids and identity not in current:raise ValueError('名册已在其他窗口更改，请重新读取后再保存')
   if not values[0]:raise ValueError('选手姓名不能为空')
   if not QColor(values[3]).isValid():raise ValueError(values[0]+'的颜色无效，请填写如 #377e72')
   if values[7] not in ('active','qualified','eliminated','inactive'):raise ValueError('选手状态无效')
   points,games=float(values[4]),int(values[5])
   if not math.isfinite(points) or games<0:raise ValueError('积分必须为有限数值，场次不能为负数')
   original=self.roster_original.get(identity,{})
   player=copy.deepcopy(current.get(identity,original))
   player['id']=identity
   draft=dict(name=values[0],team=values[1],team_id=values[2],color=QColor(values[3]).name(),base_points=points,games=games,status=values[7])
   if self.managed_team_roster():
    draft.pop('team');draft.pop('team_id')
   defaults=dict(name='',team='',team_id='',color='#377e72',base_points=0,games=0,status='active')
   for key,value in draft.items():
    old=original.get(key,defaults[key])
    if key=='color' and QColor(old).isValid():old=QColor(old).name()
    if identity not in current or value!=old:
     player[key]=value
     if key=='color' and player.get('follow_team_color'):player['follow_team_color']=False
   previous_stats=original.get('stats',original.get('baseline_stats',{}))
   merged_stats=copy.deepcopy(player.get('stats',player.get('baseline_stats',{})))
   for key,value in baselines.get(identity,{}).items():
    if identity not in current or value!=previous_stats.get(key,0):merged_stats[key]=value
   if identity in self.photo_drafts:player['photo']=self.photo_drafts[identity]
   player['stats']=merged_stats;players.append(player)
  # Keep entries added externally after this dialog was opened.
  for identity,player in current.items():
   if identity not in draft_ids and identity not in self.loaded_roster_ids:players.append(copy.deepcopy(player))
  return players
 def managed_team_roster(self):
  return (self.model.event or {}).get('kind')=='team_league'
 @saves_section('roster')
 def save_roster(self):
  def save():
   roster=self.collect_roster();seats=[w.currentData() for w in self.seats]
   if seats==self.loaded_seat_choices:seats=[p['id'] for p in self.model.state['players']]
   if None in seats or len(set(seats))!=4:raise ValueError('四个席位必须分别选择不同的选手')
   self.model.configure_broadcast({'roster':roster,'seats':seats});self.reload()
  self.run(save,'名册、历史基数与席位已保存')
 def start_selected_match(self):
  def start():
   self.model.matches.start_with_seats([w.currentData() for w in self.seats],self.collect_roster());self.reload()
  self.run(start,'所选四席的新半庄已开始；本赛事已确认成绩与技术统计保留')
 def reload_roster(self):
  loading=self.loading;self.loading=True
  roster=self.model.state['broadcast']['roster']
  self.photo_drafts={};self.roster.setRowCount(0);self.roster.setColumnHidden(2,self.managed_team_roster());self.baselines.setRowCount(0)
  self.loaded_roster_ids={p['id'] for p in roster}
  self.loaded_seat_ids=[p['id'] for p in self.model.state['players']]
  self.roster_original={p['id']:copy.deepcopy(p) for p in roster}
  for player in roster:
   self.append_roster(player);self.append_baseline(player)
  fill_seat_choices(self.seats,roster,self.loaded_seat_ids)
  self.loaded_seat_choices=[w.currentData() for w in self.seats]
  self.loading=loading;self.mark_saved('roster')
 def build_qualification(self):
  page=self.page('晋级条件');form=QFormLayout();self.qualification_focus=QComboBox();self.qualification_focus.setProperty('i18n_literal',True);self.qualification_target=choice([('个人赛','individual'),('队伍赛','team')]);self.qualification_slots=integer(1,200);self.qualification_mode=choice([('自动计算','auto'),('手动条件','manual')]);self.qualification_final=QCheckBox('按本局结束即结赛计算');self.start_points=integer(100,1000000000,100);self.return_points=integer(0,1000000000,100);self.uma=[decimal(-100000,100000) for _ in range(4)];self.oka=QCheckBox('启用头名奖励（起点与精算原点差额）');self.tie_break=choice([('同点按起家顺序','initial_seat'),('同点平分顺位分','share')]);self.qualification_tie=choice([('按达到积分的先后排序','arrival'),('必须严格超过晋级线','strict')])
  self.qualification_layout=choice([('四人并排 · 全屏节目','four'),('单人条件 · 侧边展示栏','sidebar')]);self.qualification_title=QLineEdit();self.qualification_title.setMaxLength(80);self.qualification_title.setPlaceholderText('留空：按赛程自动显示晋级 / 冠军条件')
  self.qualification_heading_preview=QLabel();self.qualification_heading_preview.setWordWrap(True);self.qualification_heading_preview.setProperty('i18n_literal',True)
  form.addRow('条件版式',self.qualification_layout);form.addRow('统一标题',self.qualification_title);form.addRow('当前标题',self.qualification_heading_preview)
  self.qualification_slots_label=QLabel();self.qualification_scope_note=note('')
  form.addRow('晋级对象',self.qualification_target);form.addRow(self.qualification_slots_label,self.qualification_slots);form.addRow('焦点选手',self.qualification_focus);form.addRow('条件来源',self.qualification_mode);form.addRow('',self.qualification_final);form.addRow('累计同分',self.qualification_tie)
  top=QHBoxLayout();top.addLayout(form,1)
  preview=QGroupBox('晋级画面预览');preview_layout=QVBoxLayout(preview)
  self.qualification_preview=ArtworkPreview((1920,1080),self.draw_qualification_preview,height=300)
  preview_layout.addWidget(self.qualification_preview);top.addWidget(preview,2)
  page.addLayout(top);page.addWidget(self.qualification_scope_note)
  self.manual_conditions=grid_table(['选手','手动条件正文','单独标题（留空跟随统一标题）']);self.manual_conditions.setMinimumHeight(150);self.manual_conditions.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents);page.addWidget(self.manual_conditions)
  if self.model.event_store:
   page.addWidget(note('精算原点、顺位分和半庄同点规则统一在赛事管理的“赛事设置”修改。'))
   for control in (self.start_points,self.return_points,*self.uma,self.oka,self.tie_break):control.setParent(self);control.hide()
  else:
   rules=QGroupBox('独立导播计分规则');rules_form=QFormLayout(rules);rules_form.addRow('新半庄开场点数',self.start_points);rules_form.addRow('精算原点',self.return_points);rules_form.addRow('1 / 2 / 3 / 4 位顺位分',row(*self.uma));rules_form.addRow('',self.oka);rules_form.addRow('半庄同点处理',self.tie_break);page.addWidget(rules)
  page.addWidget(note('四人并排通过“全屏节目”显示，不受焦点选手影响；单人条件使用固定尺寸的“侧边展示栏”，未指定选手时显示东家。标题留空按下一阶段自动命名；末轮显示冠军条件。'))
  page.addLayout(row(button('保存晋级设置',self.save_qualification),button('刷新晋级条件',self.refresh_conditions),button('条件上屏',lambda:self.run(self.manager.show_conditions)),button('收起条件',lambda:self.run(self.manager.hide_conditions)),1))
  self.conditions=grid_table(['选手','和牌来源','晋级条件'],False);self.conditions.setMinimumHeight(230);self.conditions.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents);self.conditions.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.ResizeToContents);page.addWidget(self.conditions);page.addStretch()
  self.qualification_target.currentIndexChanged.connect(self.update_qualification_unit)
  for control in (self.qualification_layout,self.qualification_focus,self.qualification_target,self.qualification_mode,self.qualification_tie,self.tie_break):
   control.currentIndexChanged.connect(self.refresh_qualification_preview)
  for control in (self.qualification_slots,self.return_points,*self.uma):control.valueChanged.connect(self.refresh_qualification_preview)
  for control in (self.qualification_final,self.oka):control.toggled.connect(self.refresh_qualification_preview)
  self.qualification_title.textChanged.connect(self.refresh_qualification_preview)
  self.manual_conditions.itemChanged.connect(self.refresh_qualification_preview)
  signals.language_changed.connect(self.refresh_qualification_preview)
 def refresh_qualification_preview(self,*_):
  from broadcast_artwork import EXTRA_SIZES
  kind='qualification' if self.qualification_layout.currentData()=='sidebar' else 'four_conditions'
  self.qualification_preview.set_design_size(EXTRA_SIZES[kind])
  self.qualification_preview.invalidate()
 def draw_qualification_preview(self,painter):
  from broadcast_artwork import draw_extension
  from qualification_presentation import qualification_context
  draft=self.qualification_values();config=dict(self.config(),**draft);config['sidebar_mode']='qualification'
  state=self.model.state;event=self.model.event
  if self.model.event_store and event and not qualification_context(self.model)['locked'] and event.get('kind')!='individual_knockout':
   event=dict(event,qualification_count=draft['qualification_slots'])
  broadcast=dict(state['broadcast'],config=config)
  model=SimpleNamespace(state=dict(state,broadcast=broadcast),event=event,event_store=self.model.event_store,
                        settings=self.model.settings,path=self.model.path)
  kind='qualification' if draft['qualification_layout']=='sidebar' else 'four_conditions'
  draw_extension(painter,kind,model)
 def update_qualification_unit(self,*_):
  team=self.qualification_target.currentData()=='team';self.qualification_slots_label.setText(tr('晋级队数' if team else '晋级人数'));self.qualification_slots.setSuffix(tr(' 队' if team else ' 人'))
 def load_qualification_scope(self):
  from qualification_presentation import qualification_context
  context=qualification_context(self.model)
  blockers=[QSignalBlocker(w) for w in (self.qualification_target,self.qualification_slots)]
  selected(self.qualification_target,context['mode']);self.qualification_slots.setValue(context['slots']);self.qualification_target.setEnabled(not self.model.event_store and not context['locked']);self.qualification_slots.setEnabled(not context['locked']);self.qualification_scope_note.setText(tr(context['note']));self.update_qualification_unit()
  del blockers
 def qualification_values(self):
  conditions={};titles={}
  for index in range(self.manual_conditions.rowCount()):
   identity=self.manual_conditions.item(index,0).data(Qt.ItemDataRole.UserRole);conditions[identity]=self.manual_conditions.item(index,1).text().strip()
   title=self.manual_conditions.item(index,2).text().strip()
   if title:titles[identity]=title
   else:titles.pop(identity,None)
  values={'qualification_layout':self.qualification_layout.currentData(),'qualification_title':self.qualification_title.text().strip(),'qualification_titles':titles,'qualification_focus':self.qualification_focus.currentData() or '','qualification_mode':self.qualification_target.currentData(),'qualification_slots':self.qualification_slots.value(),'qualification_calculation':self.qualification_mode.currentData(),'qualification_final':self.qualification_final.isChecked(),'qualification_tie_break':self.qualification_tie.currentData(),'manual_conditions':conditions}
  if not self.model.event_store:values.update(start_points=self.start_points.value(),return_points=self.return_points.value(),uma=[w.value() for w in self.uma],oka_enabled=self.oka.isChecked(),tie_break=self.tie_break.currentData())
  return values
 @saves_section('qualification')
 def save_qualification(self):
  def save():
   from qualification_presentation import qualification_context
   from event_metadata import now
   from event_configuration import save_configuration
   import tournament
   merged,changed=self._merge_config_draft('qualification');values={key:merged[key] for key in changed}
   context=qualification_context(self.model);event=copy.deepcopy(self.model.event)
   if self.model.event_store:
    values['qualification_mode']=context['mode']
    if context['locked']:values['qualification_slots']=context['slots']
    elif event['kind']!='individual_knockout' and 'qualification_slots' in changed:event.update(qualification_count=merged['qualification_slots'],updated_at=now())
   if changed:
    state=copy.deepcopy(self.model.state);tournament.configure(state,values)
    save_configuration(self.model,state,event)
   self._refresh_config_draft('qualification')
   self.refresh_conditions()
  self.run(save,'晋级设置已保存；积分榜与条件试算使用同一晋级名额')
 def build_commentary(self):
  page=self.page('术语解释')
  favorites=QGroupBox('常用术语');favorite_layout=QVBoxLayout(favorites);self.quick_terms=self.create_quick_terms_widget(favorites);favorite_layout.addWidget(self.quick_terms);favorite_layout.addLayout(row(button('按钮布局',self.edit_term_buttons),1));page.addWidget(favorites)
  self.term_category=choice([('全部分类','')]+[(entry['zh_title'],entry['id']) for entry in CATEGORIES]+[('自定义','custom')]);self.term_category.currentIndexChanged.connect(self.filter_terms)
  self.terms=QComboBox();self.terms.setProperty('i18n_literal',True);self.terms.setPlaceholderText(tr('选择实战术语'));self.terms.setMaxVisibleItems(14);self.terms.currentIndexChanged.connect(self.choose_term)
  self.add_favorite_button=button('加入常用',self.add_favorite);self.remove_favorite_button=button('移出常用',lambda:self.remove_favorite())
  self.commentary_title=QLineEdit();self.commentary_title.setMaxLength(60);self.commentary_body=QTextEdit();self.commentary_body.setAcceptRichText(False);self.commentary_body.setMinimumHeight(160)
  self.commentary_title.textChanged.connect(self.term_edited);self.commentary_body.textChanged.connect(self.term_edited)
  from commentary_editor import CommentaryStyleEditor
  self.commentary_style=CommentaryStyleEditor(self);page.insertWidget(0,self.commentary_style)
  self.commentary_duration=integer(0,600);self.commentary_duration.setSuffix(' 秒');self.commentary_duration.setSpecialValueText('常驻');form=QFormLayout()
  form.addRow('术语分类',self.term_category);form.addRow('实战术语',row(self.terms,self.add_favorite_button,self.remove_favorite_button));form.addRow('标题',self.commentary_title);form.addRow('解释',self.commentary_body);form.addRow('停留时间',self.commentary_duration);page.addLayout(form)
  self.center_commentary_button=button('自动定位',lambda:self.run(self.manager.center_commentary))
  page.addLayout(row(button('显示 / 重新计时',self.show_commentary),button('收起',lambda:self.toggle('commentary',False)),self.center_commentary_button,button('保存此术语',self.save_term),button('删除自定义术语',self.remove_term),1))
  page.addWidget(note('保存此术语只更新词库。常用按钮一键上屏，右键编辑。'));page.addStretch()
 def create_quick_terms_widget(self,parent=None,columns=6,limit=None):
  return QuickTermBar(self,parent,columns,limit)
 def favorite_refs(self):
  values=[]
  for ref in self.config().get('term_favorites',DEFAULT_TERM_FAVORITES):
   identity=self.favorite_ref(self.term_entry(ref)) or ref
   if identity not in values:values.append(identity)
  return values
 def favorite_buttons(self):
  values=self.config().get('term_buttons')
  if values is None:values=[dict(slot=i,ref=ref,label='') for i,ref in enumerate(self.favorite_refs())]
  buttons=[];used=set()
  for value in values:
   ref=self.favorite_ref(self.term_entry(value['ref']))
   if ref and ref not in used:buttons.append(dict(value,ref=ref));used.add(ref)
  return buttons
 def edit_term_buttons(self):
  from term_buttons import TermButtonDialog
  dialog=TermButtonDialog(self)
  try:
   self.main.language_controller.track(dialog);return dialog.exec()
  finally:dialog.deleteLater()
 def term_entry(self,ref):
  """Resolve a builtin ID, custom:title, or exact title without mutating state."""
  if not isinstance(ref,str):return None
  custom=[(str(v[0]),str(v[1])) for v in self.config().get('custom_terms',[]) if isinstance(v,(list,tuple)) and len(v)==2]
  exact=ref[7:] if ref.startswith('custom:') else ref
  for title,body in reversed(custom):
   if title==exact:return (title,body,False,'custom:'+title)
  if ref.startswith('custom:'):return None
  identity=ref if get_term(ref) else resolve_term_id(ref);term=get_term(identity)
  if not term:return None
  for title,body in reversed(custom):
   if resolve_term_id(title)==identity:return (title,body,False,'custom:'+title)
  return (term['zh_title'],term['zh_body'],True,identity)
 def entry_display(self,entry):
  return term_text(entry[3],get_language()) if entry[2] else entry[:2]
 def favorite_ref(self,entry):
  if not entry:return None
  return entry[3] if entry[2] else resolve_term_id(entry[0]) or entry[3]
 def update_favorite_controls(self):
  if not hasattr(self,'add_favorite_button'):return
  ref=self.favorite_ref(self.terms.currentData());present=ref in self.favorite_refs()
  self.add_favorite_button.setEnabled(bool(ref) and not present);self.remove_favorite_button.setEnabled(bool(ref) and present)
 def add_favorite(self):
  def save():
   ref=self.favorite_ref(self.terms.currentData())
   if not ref:raise ValueError('请先选择一条术语')
   values=self.favorite_refs()
   if ref not in values:
    if len(values)>=12:raise ValueError('常用术语最多12项')
    buttons=self.favorite_buttons();used={entry['slot'] for entry in buttons};slot=next(i for i in range(12) if i not in used)
    self.model.configure_commentary({'term_favorites':values+[ref],'term_buttons':buttons+[dict(slot=slot,ref=ref,label='')]})
   self.update_favorite_controls()
  self.run(save,'已加入常用术语')
 def remove_favorite(self,ref=None):
  def save():
   identity=ref or self.favorite_ref(self.terms.currentData())
   if not identity:raise ValueError('请先选择一条术语')
   self.model.configure_commentary({'term_favorites':[v for v in self.favorite_refs() if v!=identity],'term_buttons':[entry for entry in self.favorite_buttons() if entry['ref']!=identity]});self.update_favorite_controls()
  self.run(save,'已移出常用术语')
 def show_term_entry(self,ref):
  """Publish one saved entry; leave all current editing fields untouched."""
  def show():
   entry=self.term_entry(ref)
   if not entry:raise ValueError('术语不存在')
   self.model.configure_commentary({'commentary_title':entry[0],'commentary_body':entry[1],'commentary_builtin':entry[2],'commentary_duration':self.commentary_duration.value()});self.manager.set_extension_visible('commentary',True)
  return self.run(show,'解释已上屏')
 def edit_term_entry(self,ref):
  entry=self.term_entry(ref)
  if not entry:
   def missing():raise ValueError('术语不存在')
   return self.run(missing)
  category=get_term(entry[3])['category'] if entry[2] else 'custom';selected(self.term_category,category);self.load_terms();self.select_term_entry(*entry[:3]);self.choose_term()
  if self.model.event_store and hasattr(self.main,'open_event_settings'):
   self.main.open_event_settings('commentary')
  else:
   self.select_category('commentary');self.show();self.raise_();self.activateWindow()
  return entry
 def set_term_editor(self,title,body):
  self.setting_term=True
  try:self.commentary_title.setText(title);self.commentary_body.setPlainText(body)
  finally:self.setting_term=False
  self.commentary_style.refresh_preview()
 def choose_term(self,*_):
  if self.loading:return
  entry=self.terms.currentData();self.update_favorite_controls()
  if not entry:return
  self.editing_term_ref=entry[3];self.active_builtin=(entry[0],entry[1]) if entry[2] else None;self.active_term_id=entry[3] if entry[2] else None
  self.set_term_editor(*self.entry_display(entry))
  self.mark_saved('term')
 def filter_terms(self,*_):
  if not self.loading:self.load_terms()
 def term_edited(self,*_):
  if not self.setting_term and not self.loading:self.active_builtin=None;self.active_term_id=None
 def refresh_language(self):
  self.status.setText(tr(self.status_source));self.terms.setPlaceholderText(tr('选择实战术语'))
  for index in range(self.terms.count()):
   entry=self.terms.itemData(index)
   if entry and entry[2]:self.terms.setItemText(index,term_text(entry[3],get_language())[0])
  self.qualification_focus.setItemText(0,tr('默认：东家'));self.update_qualification_unit()
  self.refresh_live();self.refresh_conditions();self.quick_terms.refresh()
  if self.active_term_id:self.set_term_editor(*term_text(self.active_term_id,get_language()))
  elif self.active_builtin:self.set_term_editor(tr(self.active_builtin[0]),tr(self.active_builtin[1]))
  else:return
  self.mark_saved('term')
 def commentary_patch(self):
  title=self.commentary_title.text().strip();body=self.commentary_body.toPlainText().strip()
  if not title or not body:raise ValueError('请填写解释标题与正文')
  if len(body)>500:raise ValueError('正文请控制在500字以内')
  if self.active_builtin:title,body=self.active_builtin
  return {'commentary_title':title,'commentary_body':body,'commentary_duration':self.commentary_duration.value(),'commentary_builtin':bool(self.active_builtin)}
 def show_commentary(self):
  def show():
   patch=self.commentary_patch()
   patch.update(self.commentary_style.edited_values())
   self.model.configure_commentary(patch)
   self.commentary_style.load(self.config())
   self.mark_saved('commentary_style')
   self.manager.set_extension_visible('commentary',True)
   self.mark_saved('commentary_duration')
  self.run(show,'解释已上屏')
 @saves_section('commentary_style')
 def save_commentary_style(self):
  self.run(self.commentary_style.save,'术语字幕样式已保存')
 @saves_section('commentary_duration')
 def save_commentary_duration(self):
  def save():
   if not self.commentary_duration.hasAcceptableInput():raise ValueError('字幕停留时间尚未填写完整')
   self.commentary_duration.interpretText()
   value=self.commentary_duration.value()
   if value!=self.config().get('commentary_duration',12):
    self.model.configure_commentary({'commentary_duration':value})
  self.run(save,'字幕停留时间已保存')
 @saves_section('term')
 def save_term(self):
  def save():
   title=self.commentary_title.text().strip();body=self.commentary_body.toPlainText().strip()
   if not title or not body:raise ValueError('请填写解释标题与正文')
   if len(body)>500:raise ValueError('正文请控制在500字以内')
   old_title=self.editing_term_ref[7:] if self.editing_term_ref and self.editing_term_ref.startswith('custom:') else None
   original_terms=self.config().get('custom_terms',[]);source_id=resolve_term_id(old_title) if old_title else self.editing_term_ref;target_id=resolve_term_id(title)
   if any(v[0]==title for v in original_terms) and title!=old_title or source_id and target_id and get_term(source_id) and source_id!=target_id:raise ValueError('该术语标题已存在，请使用不同标题')
   terms=[v for v in copy.deepcopy(original_terms) if v[0] not in (title,old_title)];terms.append([title,body]);values={'custom_terms':terms}
   old_refs={self.editing_term_ref,source_id};new_ref=target_id or 'custom:'+title
   values['term_favorites']=list(dict.fromkeys(new_ref if v in old_refs else v for v in self.favorite_refs()))
   values['term_buttons']=[dict(entry,ref=new_ref if entry['ref'] in old_refs else entry['ref']) for entry in self.favorite_buttons()]
   self.model.configure_commentary(values);self.active_builtin=None;self.active_term_id=None;self.editing_term_ref='custom:'+title;self.load_terms();self.select_term_entry(title,body,False)
   if self.terms.currentIndex()<0:selected(self.term_category,'custom');self.load_terms();self.select_term_entry(title,body,False)
   self.update_favorite_controls()
  self.run(save,'术语已保存')
 def remove_term(self):
  def remove():
   title=self.editing_term_ref[7:] if self.editing_term_ref and self.editing_term_ref.startswith('custom:') else self.commentary_title.text().strip();terms=self.config().get('custom_terms',[])
   if not any(entry[0]==title for entry in terms):raise ValueError('内置术语保留；可编辑后另存')
   self.model.configure_commentary({'custom_terms':[entry for entry in terms if entry[0]!=title],'term_favorites':[ref for ref in self.favorite_refs() if ref!='custom:'+title],'term_buttons':[entry for entry in self.favorite_buttons() if entry['ref']!='custom:'+title]});self.editing_term_ref=None;self.load_terms()
  self.run(remove,'自定义术语已删除')
 def select_term_entry(self,title,body,builtin):
  blocker=QSignalBlocker(self.terms);self.terms.setCurrentIndex(-1)
  for index in range(self.terms.count()):
   entry=self.terms.itemData(index)
   if entry[:3]==(title,body,builtin):self.terms.setCurrentIndex(index);break
  del blocker
  self.update_favorite_controls()
 def load_terms(self):
  # Filtering is a picker action; it must never replace an unsaved explanation.
  current=self.terms.currentData();category=self.term_category.currentData() or '';custom=[];overrides={}
  for entry in self.config().get('custom_terms',[]):
   if not isinstance(entry,(list,tuple)) or len(entry)!=2:continue
   title,body=map(str,entry);identity=resolve_term_id(title);custom.append((title,body,identity))
   if identity:overrides[identity]=(title,body)
  entries=[];used=set()
  if category!='custom':
   for term in terms_for_category(category or None):
    identity=term['id']
    if identity in overrides:
     title,body=overrides[identity];entries.append((title,body,False,'custom:'+title));used.add(title)
    else:entries.append((term['zh_title'],term['zh_body'],True,identity))
  for title,body,identity in custom:
   if title in used:continue
   source=get_term(identity) if identity else None
   if category in ('','custom') or source and source['category']==category:entries.append((title,body,False,'custom:'+title))
  blocker=QSignalBlocker(self.terms);self.terms.clear()
  for title,body,builtin,identity in entries:self.terms.addItem(term_text(identity,get_language())[0] if builtin else title,(title,body,builtin,identity))
  self.terms.setCurrentIndex(-1)
  if current:
   for index in range(self.terms.count()):
    if self.terms.itemData(index)==current:self.terms.setCurrentIndex(index);break
  del blocker
  self.update_favorite_controls()
 def load_commentary(self,config):
  term=(config.get('commentary_title',TERMS[0][0]),config.get('commentary_body',TERMS[0][1]));identity=resolve_term_id(*term)
  self.active_term_id=identity if config.get('commentary_builtin',bool(identity)) else None
  self.active_builtin=term_text(identity,'zh_CN') if self.active_term_id else None
  custom=self.term_entry('custom:'+term[0]);self.editing_term_ref=identity if self.active_term_id else custom[3] if custom else None
  self.load_terms();self.select_term_entry(*(self.active_builtin or term),bool(self.active_builtin))
  self.set_term_editor(*(term_text(identity,get_language()) if self.active_term_id else term));self.commentary_duration.setValue(config.get('commentary_duration',12))
  self.commentary_style.load(config)
 def build_statistics(self):
  page=self.page('数据与导出');self.stats_summary=QLabel();page.addWidget(self.stats_summary);self.stats=grid_table(['选手','局数','副露率','立直率','和牌率','放铳率','平均和牌','平均铳点'],False);self.stats.setMinimumHeight(190);page.addWidget(self.stats);page.addWidget(QLabel('历史统计基数'));self.baselines=grid_table(['选手']+[label for _,label in COUNTERS]);self.baselines.setMinimumHeight(180);page.addWidget(self.baselines)
  page.addWidget(note('基数只填本系统记录以外的数据。副露按发生副露的局数统计，暗杠不计；和牌/放铳点数含本场、不含供托。'))
  save=self.save_technical_baselines if self.workspace_roster else self.save_roster
  self.stats_save=button('保存历史基数',save);page.addLayout(row(self.stats_save,1))
  self.export_kind=choice([(name,key) for key,name in EXTENSIONS.items()]);selected(self.export_kind,'chart');page.addLayout(row('SVG',self.export_kind,button('导出 SVG',self.export_svg),1))
  page.addLayout(row(button('导出统计 CSV',self.export_csv),button('保存完整数据 JSON',self.export_json),button('导入数据 JSON',self.import_json),1));page.addWidget(note('SVG保存当前显示配置的矢量图；JSON包含选手资料、照片、逐局记录、赛事积分与显示设置；CSV用于表格分析。'))
  self.demo_restore=button('恢复测试前数据',self.restore_demo);self.demo_restore.hide();page.addLayout(row(self.demo_restore,1));page.addStretch()
 def refresh_demo_status(self):
  active=self.demo_session.active
  demo=active or bool((self.model.event or {}).get('demo_suite'))
  self.demo_badge.setText(tr('演示数据') if demo else '')
  self.demo_restore.setVisible(active)
  self.demo_restore.setEnabled(active and self.demo_session.backup_path.is_file())
 def restore_demo(self):
  from settings_context import SettingsContextChange
  context=SettingsContextChange(self.event_workspace or self)
  if not context.prepare():
   if context.save_error:self.status.setText(context.save_error);set_status_style(self.status,'error')
   return
  def restore():
   self.demo_session.restore();self.reload();self.refresh_language();context.finish()
   if hasattr(self.main,'refresh'):self.main.refresh()
  self.run(restore,'已恢复测试前的数据与布局')
 def technical_values(self):
  result={}
  for player in self.model.state['broadcast']['roster']:
   stats=player.get('stats',player.get('baseline_stats',{}))
   result[player['id']]={key:stats.get(key,0) for key,_ in COUNTERS}
  return result
 def technical_draft(self):
  baselines={}
  for index in range(self.baselines.rowCount()):
   identity=self.baselines.item(index,0).data(Qt.ItemDataRole.UserRole)
   stats={}
   for column,(key,label) in enumerate(COUNTERS,1):
    value=int(self.baselines.item(index,column).text())
    if value<0:raise ValueError(label+'不能为负数')
    stats[key]=value
   baselines[identity]=stats
  return baselines
 def load_technical_baselines(self):
  self.baselines.setRowCount(0)
  for player in self.model.state['broadcast']['roster']:
   self.append_baseline(player)
  self._technical_baseline=self.technical_values()
  self.mark_saved('technical')
 def track_technical_baselines(self):
  self.track_settings('technical',lambda:form_values(self.baselines),self.save_technical_baselines)
 @saves_section('technical')
 def save_technical_baselines(self):
  def save():
   from settings_drafts import merge_draft
   baseline=self._technical_baseline
   merged,changed,conflicts=merge_draft(baseline,self.technical_draft(),baseline,self.technical_values())
   if conflicts:raise ValueError('同一技术统计基数已修改，请重新读取后再保存')
   if changed:
    roster=copy.deepcopy(self.model.state['broadcast']['roster'])
    for player in roster:
     if player['id'] in changed:
      stats=player.get('stats',player.get('baseline_stats',{}))
      player['stats']=dict(stats,**merged[player['id']])
    self.model.set_roster(roster)
   self.load_technical_baselines()
  self.run(save,'技术统计基数已保存')
 def append_baseline(self,player):
  index=self.baselines.rowCount();self.baselines.insertRow(index);name=QTableWidgetItem(player.get('name',''));name.setData(Qt.ItemDataRole.UserRole,player['id']);name.setFlags(name.flags()&~Qt.ItemFlag.ItemIsEditable);self.baselines.setItem(index,0,name);stats=player.get('stats',player.get('baseline_stats',{}))
  for column,(key,_) in enumerate(COUNTERS,1):self.baselines.setItem(index,column,QTableWidgetItem(str(stats.get(key,0))))
 def export_directory(self):
  path=self.model.path/'exports';path.mkdir(parents=True,exist_ok=True);return path
 def export_svg(self):
  kind=self.export_kind.currentData();path,_=QFileDialog.getSaveFileName(self,tr('导出 SVG'),str(self.export_directory()/(kind+'.svg')),'SVG (*.svg)')
  if path:self.run(lambda:self.manager.export_svg(kind,path),'SVG 已保存')
 def export_csv(self):
  path,_=QFileDialog.getSaveFileName(self,tr('导出统计 CSV'),str(self.export_directory()/'statistics.csv'),'CSV (*.csv)')
  if not path:return
  def export():
   from tournament import statistics
   rows=statistics(self.model.state);fields=['id','name','hands','calls','riichi','wins','deal_ins','call_rate','riichi_rate','win_rate','deal_in_rate','win_points','deal_in_points','average_win','average_deal_in']
   with Path(path).open('w',encoding='utf-8-sig',newline='') as stream:
    writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader()
    for entry in rows:writer.writerow({key:entry.get(key) for key in fields})
  self.run(export,'统计 CSV 已保存（比率为 0–1）')
 def export_json(self):
  path,_=QFileDialog.getSaveFileName(self,tr('保存数据'),str(self.export_directory()/'tournament.json'),'JSON (*.json)')
  if path:self.run(lambda:self.model.export_data(path),'赛事数据已保存')
 def import_json(self):
  if self.event_workspace:
   self.event_workspace.import_event()
   return
  path,_=QFileDialog.getOpenFileName(self,tr('导入数据'),str(self.export_directory()),'JSON (*.json)')
  if path:
   from settings_context import SettingsContextChange
   context=SettingsContextChange(self)
   if not context.prepare():return
   def load():
    if self.model.event_store:self.model.event_store.import_new(path)
    else:self.model.import_data(path)
    self.reload();context.finish()
   self.run(load,'赛事数据已导入')
 def toggle(self,key,enabled):
  if not self.loading:self.run(lambda:self.manager.set_extension_visible(key,enabled))
 def scene(self,name):self.run(lambda:self.manager.apply_scene(name))
 def commit_match(self):
  command=self.model.matches.confirm_finished if self.model.event_store else self.model.commit_match
  self.run(command,'本场积分已记录；重复记录不会重复计分')
 def start_match(self):
  def start():
   outcome=self.model.matches.start_next() if self.model.event_store else self.model.start_match()
   if outcome=='schedule':
    self.main.open_event_workspace(4)
    return 'schedule'
   self.reload()
   return 'started'
  outcome=self.run(start)
  if outcome:self.status_source='请在淘汰赛程选择下一场' if outcome=='schedule' else '新半庄已开始';self.status.setText(tr(self.status_source))
 def reload(self):
  if self._saving_all:return
  self.riichi_preview.stop();self.stop_music_audition()
  self.loading=True;config=self.config();self.title.setText(config.get('title',''));selected(self.standings_mode,self.manager.bottom_mode());selected(self.event_mode,config.get('event_mode','individual'));self.settlement_enabled.setChecked(config.get('settlement_enabled',True));self.settlement_hold.setValue(config.get('settlement_hold',3.));self.settlement_total_hold.setValue(config.get('settlement_total_hold',3.));self.update_settlement_controls()
  self.load_postmatch_controls(config)
  self.refresh_opening_controls()
  self.event_mode.setEnabled(not self.model.event_store)
  if self.model.event_store:self.event_mode.setToolTip('比赛类型在创建后固定；需要其他赛制时请新建赛事。')
  selected(self.statistics_scope,config.get('statistics_scope','all'));selected(self.statistics_view,config.get('statistics_view','rates'));self.statistics_page.setValue(config.get('statistics_page',0)+1);self.show_match_title.setChecked(config.get('show_match_title',True))
  selected(self.table_score_mode,config.get('table_score_mode','auto'));selected(self.table_score_layout,config.get('table_score_layout','compact'));selected(self.qualification_layout,config.get('qualification_layout','four'));self.qualification_title.setText(config.get('qualification_title',''))
  selected(self.sidebar_mode,config.get('sidebar_mode','qualification'));self.sidebar_page.setValue(config.get('sidebar_page',0)+1);self.sidebar_page_size.setValue(config.get('sidebar_page_size',10));selected(self.sidebar_scope,config.get('sidebar_scope','all'));self.sidebar_auto_page.setChecked(config.get('sidebar_auto_page',True));self.sidebar_page_interval.setValue(config.get('sidebar_page_interval',10))
  self.sidebar_duration.setValue(config.get('sidebar_duration',15))
  self.load_qualification_players()
  self.load_qualification_scope();selected(self.qualification_mode,config.get('qualification_calculation','auto'));self.qualification_final.setChecked(config.get('qualification_final',False));self.start_points.setValue(config.get('start_points',25000));self.return_points.setValue(config.get('return_points',30000))
  for w,value in zip(self.uma,config.get('uma',[30,10,-10,-30])):w.setValue(value)
  self.oka.setChecked(config.get('oka_enabled',False));selected(self.tie_break,config.get('tie_break','initial_seat'));selected(self.qualification_tie,config.get('qualification_tie_break','arrival'))
  if self.workspace_roster or self.event_workspace:
   self.load_technical_baselines()
  else:
   self.reload_roster()
  if 'live_animation' in self._settings_sections:self.load_live_animations()
  self.active_builtin=None;self.active_term_id=None;self.editing_term_ref=None;self.setting_term=False;self.loading=False
  self.load_commentary(config)
  self.refresh_qualification_preview()
  self.refresh_live()
  if not (self.workspace_roster or self.event_workspace) or self.is_qualification_page():
   self.refresh_conditions()
  for section in ('presentation','postmatch','qualification'):self._capture_config_draft(section)
  self.mark_saved()
 def load_qualification_players(self):
  config=self.config();players=self.model.state['players']
  self.qualification_focus.clear();self.qualification_focus.addItem('默认：东家','')
  for player in players:self.qualification_focus.addItem(player['name'],player['id'])
  selected(self.qualification_focus,config.get('qualification_focus',''))
  self.manual_conditions.setRowCount(len(players))
  for index,player in enumerate(players):
   identity=player['id'];cell=QTableWidgetItem(player['name']);cell.setData(Qt.ItemDataRole.UserRole,identity)
   cell.setFlags(cell.flags()&~Qt.ItemFlag.ItemIsEditable);self.manual_conditions.setItem(index,0,cell)
   self.manual_conditions.setItem(index,1,QTableWidgetItem(config.get('manual_conditions',{}).get(identity,'')))
   self.manual_conditions.setItem(index,2,QTableWidgetItem(config.get('qualification_titles',{}).get(identity,'')))
 def on_model_changed(self,*_):
  if len(_)>2 and _[2] in ('import','event_settings'):
   self.riichi_preview.stop();self.stop_music_audition()
   if not self.event_workspace:self.reload()
   return
  if (self.workspace_roster or self.event_workspace) and len(_)>2:
   before,after,_kind=_
   dirty=self.dirty_sections()
   if before['broadcast']['roster']!=after['broadcast']['roster'] and 'technical' not in dirty:
    self.load_technical_baselines()
   if [(p['id'],p['name']) for p in before['players']]!=[(p['id'],p['name']) for p in after['players']] and 'qualification' not in dirty:
    self.load_qualification_players();self._capture_config_draft('qualification');self.mark_saved('qualification')
  elif len(_)>2 and _[2] in ('new_match','start_match') and [p['id'] for p in self.model.state['players']]!=self.loaded_seat_ids:self.reload();return
  elif len(_)>2 and _[2]=='roster_management':
   if 'roster' in self.dirty_sections():self.refresh_seat_choices()
   else:self.reload_roster()
  self.refresh_live()
  if self.is_qualification_page() and not self.loading:self.refresh_conditions()
 def on_display_changed(self):
  signature=tuple(self.model.settings.get(key) for key in ('console_theme','console_font_family','settings_font_size'))
  if signature!=getattr(self,'_theme_signature',None):
   self._theme_signature=signature;apply_theme(self,self.model.settings)
  dirty=self.dirty_sections()
  if 'live_animation' in self._settings_sections and 'live_animation' not in dirty and self._animation_live!=self.animation_values():
   self.load_live_animations();self.mark_saved('live_animation')
  if 'qualification' not in dirty:
   self.load_qualification_scope();self.mark_saved('qualification')
   if 'qualification' in self._config_drafts:
    draft=self.qualification_values();live=self._config_live('qualification',draft)
    for key in ('qualification_mode','qualification_slots'):
     self._config_drafts['qualification'][0][key]=copy.deepcopy(draft[key]);self._config_drafts['qualification'][1][key]=copy.deepcopy(live[key])
  self.refresh_qualification_preview()
  self.refresh_live()
 def refresh_live(self):
  if self.loading or not hasattr(self,'stats'):return
  self.refresh_postmatch_controls()
  self.refresh_opening_controls()
  self.refresh_corner_preview()
  self.refresh_riichi_preview_seats()
  from tournament import statistics
  refresh_bottom_selector(self.standings_mode,self.manager)
  sidebar_blockers=[QSignalBlocker(w) for w in (self.sidebar_mode,self.sidebar_page,self.sidebar_page_size,self.sidebar_scope,self.sidebar_auto_page,self.sidebar_page_interval,self.sidebar_duration)]
  selected(self.sidebar_mode,self.config().get('sidebar_mode','qualification'))
  if not self.sidebar_page.hasFocus():self.sidebar_page.setValue(self.config().get('sidebar_page',0)+1)
  if not self.sidebar_page_size.hasFocus():self.sidebar_page_size.setValue(self.config().get('sidebar_page_size',10))
  selected(self.sidebar_scope,self.config().get('sidebar_scope','all'));self.sidebar_auto_page.setChecked(self.config().get('sidebar_auto_page',True))
  if not self.sidebar_page_interval.hasFocus():self.sidebar_page_interval.setValue(self.config().get('sidebar_page_interval',10))
  if not self.sidebar_duration.hasFocus():self.sidebar_duration.setValue(self.config().get('sidebar_duration',15))
  del sidebar_blockers
  self.update_sidebar_controls();self.update_table_score_controls()
  for key,w in self.visibility.items():
   blocker=QSignalBlocker(w);w.setChecked(self.manager.enabled.get(key,False));del blocker
  broadcast=self.model.state['broadcast'];hands=broadcast.get('hands',[]);matches=broadcast.get('matches',[]);committed=sum(bool(entry.get('committed',False)) for entry in matches);self.match_summary.setText(tr(f'已记录 {len(hands)} 局　已入账 {committed} 场'))
  rows=statistics(self.model.state);self.stats.setRowCount(len(rows))
  for index,entry in enumerate(rows):
   values=[entry.get('name',''),entry.get('hands',0),format_rate(entry.get('call_rate')),format_rate(entry.get('riichi_rate')),format_rate(entry.get('win_rate')),format_rate(entry.get('deal_in_rate')),format_average(entry.get('average_win')),format_average(entry.get('average_deal_in'))]
   for column,value in enumerate(values):self.stats.setItem(index,column,QTableWidgetItem(str(value)))
  self.stats_summary.setText(tr('累计技术统计 · 无记录时比率显示 —'));self.refresh_demo_status()
 def refresh_conditions(self):
  from qualification_presentation import qualification_titles,qualification_data,condition_label
  self.refresh_qualification_preview()
  titles=qualification_titles(self.model);self.qualification_heading_preview.setText(' / '.join(dict.fromkeys(titles.values())))
  result=qualification_data(self.model);self.conditions.setRowCount(0)
  for entry in result.get('rows',[]):
   if entry.get('manual'):groups=[(tr('手动条件'),entry.get('condition',''))]
   else:
    grouped={}
    for scenario in entry.get('scenarios',[]):
     if not scenario.get('qualifies',True):continue
     key=(scenario.get('type'),scenario.get('from'))
     if key in grouped:continue
     label=tr('自摸') if scenario.get('type')=='tsumo' else tr('直击')+' · '+scenario.get('name','')
     grouped[key]=(label,condition_label(scenario.get('text','')))
    groups=list(grouped.values()) or [(tr('当前条件'),tr(entry.get('condition','')))]
   for label,condition in groups:
    index=self.conditions.rowCount();self.conditions.insertRow(index)
    for column,value in enumerate((entry.get('name',''),label,condition)):
     item=QTableWidgetItem(str(value))
     if column==2 and entry.get('manual'):item.setData(Qt.ItemDataRole.UserRole+872,True)
     self.conditions.setItem(index,column,item)
  self.conditions.resizeRowsToContents()
