"""Named event-scoped sidebar buttons; table identities and scoring stay live."""
from __future__ import annotations

import copy
from PySide6.QtCore import Qt,QRect,QSize,QSignalBlocker
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (QCheckBox,QDialog,QFormLayout,QHBoxLayout,QInputDialog,QLabel,QLineEdit,QListWidget,QListWidgetItem,QPushButton,QSizePolicy,QStyle,QStyleOptionButton,QStylePainter,QVBoxLayout,QWidget)
from desk_theme import apply_theme,status as set_status_style
from sidebar_presets import sidebar_presets,make_current_preset,preset_available
from ui_widgets import button,choice,integer,row,selected


class WrappingPresetButton(QPushButton):
    """Keep custom button names complete within the narrow operator rail."""
    TEXT_FLAGS=Qt.TextFlag.TextWrapAnywhere|Qt.AlignmentFlag.AlignCenter

    def __init__(self,title,parent=None):
        super().__init__(title,parent);policy=self.sizePolicy();policy.setHeightForWidth(True);self.setSizePolicy(policy)

    def heightForWidth(self,width):
        bounds=self.fontMetrics().boundingRect(QRect(0,0,max(1,width-14),20000),self.TEXT_FLAGS,self.text())
        return max(34,bounds.height()+16)

    def sizeHint(self):
        return QSize(146,self.heightForWidth(146))

    def minimumSizeHint(self):
        return QSize(70,34)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        height=self.heightForWidth(self.width())
        if self.minimumHeight()!=height:self.setMinimumHeight(height)

    def paintEvent(self,event):
        option=QStyleOptionButton();self.initStyleOption(option);option.text='';painter=QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_PushButton,option)
        group=QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        painter.setPen(self.palette().color(group,QPalette.ColorRole.ButtonText))
        painter.drawText(self.rect().adjusted(7,5,-7,-5),self.TEXT_FLAGS,self.text())


from settings_window import SettingsWindow, saves_section

class SidebarPresetsDialog(SettingsWindow):
    def __init__(self,main):
        super().__init__(main);self.main=main;self.model=main.model;self.manager=main.manager;self.entries=[];self.position=None
        self.setWindowTitle('侧栏快捷展示');self.resize(840,610);self.setMinimumSize(730,560)
        root=QVBoxLayout(self);body=QHBoxLayout();root.addLayout(body,1)
        left=QVBoxLayout();self.list=QListWidget();self.list.setProperty('i18nLiteral',True);self.list.setMinimumWidth(215);self.list.setMaximumWidth(270);self.list.setWordWrap(True);left.addWidget(self.list,1)
        left.addLayout(row(button('上移',lambda:self.move_selected(-1)),button('下移',lambda:self.move_selected(1)),button('删除',self.delete_selected)))
        left.addWidget(button('保存当前展示为预设',lambda:self.add_current()));body.addLayout(left,1)
        right=QVBoxLayout();self.form_body=QWidget();form=QFormLayout(self.form_body);form.setContentsMargins(0,0,0,0)
        self.title=QLineEdit();self.title.setMaxLength(80);self.title.setProperty('i18nLiteral',True)
        self.mode=choice([('个人排名','individual'),('团队排名','team'),('晋级条件','qualification')]);self.scope=choice([('全员分页','all'),('当前四席','current')])
        self.page=integer(1,999);self.page_size=integer(2,10);self.auto=QCheckBox('自动切页');self.interval=integer(5,60);self.interval.setSuffix(' 秒')
        self.layout_mode=choice([('四人并排 · 全屏节目','four'),('单人条件 · 侧边展示栏','sidebar')]);self.use_geometry=QCheckBox('应用保存的位置（不改变尺寸）');self.geometry_text=QLabel();self.geometry_text.setObjectName('muted');self.geometry_text.setWordWrap(True)
        form.addRow('按钮名称',self.title);form.addRow('展示内容',self.mode);form.addRow('排名范围',self.scope);form.addRow('页码',self.page);form.addRow('每页人数',self.page_size);form.addRow('翻页',row(self.auto,self.interval,1));form.addRow('条件版式',self.layout_mode)
        form.addRow('',self.use_geometry);form.addRow('窗口位置',self.geometry_text);form.addRow('',button('使用当前窗口位置',self.capture_position));right.addWidget(self.form_body)
        self.save_button=button('保存修改',self.save_selected);self.apply_button=button('应用展示',self.apply_selected);right.addLayout(row(self.save_button,self.apply_button,1));right.addStretch(1);body.addLayout(right,2)
        self.status=QLabel();self.status.setWordWrap(True);self.status.setMinimumHeight(28);root.addWidget(self.status);root.addLayout(row(1,button('关闭',self.close)))
        self.list.currentRowChanged.connect(self.select_entry);self.mode.currentIndexChanged.connect(self.update_controls);self.scope.currentIndexChanged.connect(self.update_controls);self.auto.toggled.connect(self.update_controls);self.use_geometry.toggled.connect(self.show_geometry)
        self.layout_mode.currentIndexChanged.connect(self.update_controls)
        self.model.changed.connect(self.on_model_changed);apply_theme(self,self.model.settings);self.reload()
        self.track_settings('presets',self.draft_entries,self.save_selected)

    def draft_entries(self):
        self.store_form(self.list.currentRow())
        return copy.deepcopy(self.entries)

    def discard_settings(self):self.reload()

    def run(self,callback,message='预设已保存到当前赛事'):
        try:
            result=callback();set_status_style(self.status,'success');self.status.setText(message);return result
        except Exception as error:
            self._save_failed=True;set_status_style(self.status,'error');self.status.setText(str(error));return None

    @saves_section('presets')
    def reload(self,identity=None):
        identity=identity or (self.entries[self.list.currentRow()]['id'] if 0<=self.list.currentRow()<len(self.entries) else None)
        self.loaded_event_id=(self.model.event or {}).get('id');self.entries=sidebar_presets(self.model);blocker=QSignalBlocker(self.list);self.list.clear()
        for entry in self.entries:
            item=QListWidgetItem(entry['title']);item.setData(Qt.ItemDataRole.UserRole,entry['id']);self.list.addItem(item)
        current=next((i for i,entry in enumerate(self.entries) if entry['id']==identity),0)
        self.list.setCurrentRow(current if self.entries else -1);del blocker;self._selected=-1;self.select_entry(self.list.currentRow())

    def store_form(self,index):
        if not 0<=index<len(self.entries):return
        entry=self.entries[index];entry['title']=self.title.text().strip()
        entry['settings']=dict(sidebar_mode=self.mode.currentData(),sidebar_scope=self.scope.currentData(),sidebar_page=self.page.value()-1,sidebar_page_size=self.page_size.value(),sidebar_auto_page=self.auto.isChecked(),sidebar_page_interval=self.interval.value(),qualification_layout=self.layout_mode.currentData())
        full=self.mode.currentData()=='qualification' and self.layout_mode.currentData()=='four'
        if not full and self.use_geometry.isChecked() and self.position:entry['geometry']=copy.deepcopy(self.position)
        else:entry.pop('geometry',None)
        self.list.item(index).setText(entry['title'])

    def select_entry(self,index):
        self.store_form(getattr(self,'_selected',-1));self._selected=index;valid=0<=index<len(self.entries);self.form_body.setEnabled(valid);self.save_button.setEnabled(valid)
        if valid:
            entry=self.entries[index];settings=entry['settings'];self.title.setText(entry['title']);selected(self.mode,settings.get('sidebar_mode','individual'));selected(self.scope,settings.get('sidebar_scope','all'))
            self.page.setValue(settings.get('sidebar_page',0)+1);self.page_size.setValue(settings.get('sidebar_page_size',10));self.auto.setChecked(settings.get('sidebar_auto_page',True));self.interval.setValue(settings.get('sidebar_page_interval',10));selected(self.layout_mode,settings.get('qualification_layout','four'))
            self.position=copy.deepcopy(entry.get('geometry'));self.use_geometry.setChecked(bool(self.position));self.show_geometry()
        self.update_controls()

    def show_geometry(self,*_):
        full=self.mode.currentData()=='qualification' and self.layout_mode.currentData()=='four'
        self.geometry_text.setText('使用全屏节目窗口的位置与尺寸' if full else ('位置已保存 · '+f'X {self.position[0]} / Y {self.position[1]}') if self.use_geometry.isChecked() and self.position else '保留当前窗口位置')

    def update_controls(self,*_):
        ranking=self.mode.currentData() in ('individual','team');all_players=self.scope.currentData()=='all'
        self.scope.setEnabled(ranking);self.page_size.setEnabled(ranking and all_players);self.page.setEnabled(ranking and all_players and not self.auto.isChecked());self.auto.setEnabled(ranking and all_players);self.interval.setEnabled(ranking and all_players and self.auto.isChecked());self.layout_mode.setEnabled(not ranking)
        self.apply_button.setEnabled(0<=self.list.currentRow()<len(self.entries) and preset_available(self.model,{'settings':{'sidebar_mode':self.mode.currentData()}}))
        self.use_geometry.setEnabled(ranking or self.layout_mode.currentData()!='four');self.show_geometry()

    @saves_section('presets')
    def save_selected(self):
        def save():
            self.require_current_event()
            index=self.list.currentRow();self.store_form(index);identity=self.entries[index]['id'];self.manager.save_sidebar_presets(self.entries);self.reload(identity);return identity
        return self.run(save)

    def apply_selected(self):
        identity=self.save_selected()
        if identity:self.run(lambda:self.manager.apply_sidebar_preset(identity),'侧栏展示已切换')

    def add_current(self,title=None):
        if title is None:
            title,accepted=QInputDialog.getText(self,'保存当前展示','按钮名称',text='新侧栏')
            if not accepted:return
        def add():
            self.require_current_event()
            self.store_form(self.list.currentRow());entry=make_current_preset(title,self.manager);self.manager.save_sidebar_presets([*self.entries,entry]);self.reload(entry['id']);return entry['id']
        return self.run(add)

    def capture_position(self):
        def capture():
            self.require_current_event();self.position=make_current_preset('窗口位置',self.manager).get('geometry');self.use_geometry.setChecked(bool(self.position));self.show_geometry()
        self.run(capture,'已采用当前侧栏窗口位置，保存修改后生效')

    def move_selected(self,offset):
        index=self.list.currentRow();target=index+offset
        if not 0<=target<len(self.entries):return
        def move():
            self.require_current_event()
            self.store_form(index);identity=self.entries[index]['id'];self.entries[index],self.entries[target]=self.entries[target],self.entries[index];self.manager.save_sidebar_presets(self.entries);self.reload(identity)
        self.run(move)

    def delete_selected(self):
        index=self.list.currentRow()
        if not 0<=index<len(self.entries):return
        def delete():
            self.require_current_event()
            entries=copy.deepcopy(self.entries);entries.pop(index);self.manager.save_sidebar_presets(entries);self.reload()
        self.run(delete)

    def on_model_changed(self,before,after,kind):
        if kind=='import':
            previous=self.loaded_event_id;self.reload()
            if previous!=self.loaded_event_id:self.status.setText('赛事已切换，已重新读取本赛事预设')

    def require_current_event(self):
        if self.loaded_event_id!=(self.model.event or {}).get('id'):
            self.reload();raise ValueError('赛事已切换，请重新编辑本赛事预设')
