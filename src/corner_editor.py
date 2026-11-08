"""A dedicated event-branding editor, independent of the operator settings."""
from __future__ import annotations

from types import SimpleNamespace
from PySide6.QtCore import QRectF,Qt
from PySide6.QtGui import QColor,QPainter
from PySide6.QtWidgets import (QApplication,QDialog,QFileDialog,QFormLayout,QHBoxLayout,QLabel,
    QLineEdit,QScrollArea,QSizePolicy,QVBoxLayout,QWidget)

from corner_header import CORNER_SIZE,corner_options,corner_texts,draw_corner_header,validate_corner_config
from desk_theme import apply_theme,status as status_style
from i18n import tr
from ui_widgets import button,decimal,integer,note,row
from settings_window import SettingsWindow, saves_section


class CornerPreview(QWidget):
    def __init__(self,model,parent=None):
        super().__init__(parent);self.model=model;self.values=corner_options(model.state['broadcast']['config'])
        self.setMinimumHeight(150);self.setMaximumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)

    def paintEvent(self,event):
        painter=QPainter(self)
        try:
            painter.fillRect(self.rect(),QColor('#26313d'))
            for y in range(0,self.height(),18):
                for x in range(0,self.width(),18):
                    if (x//18+y//18)%2:painter.fillRect(x,y,18,18,QColor('#2d3947'))
            scale=min(self.width()/CORNER_SIZE[0],self.height()/CORNER_SIZE[1])
            painter.translate((self.width()-CORNER_SIZE[0]*scale)/2,(self.height()-CORNER_SIZE[1]*scale)/2)
            painter.scale(scale,scale)
            preview=SimpleNamespace(path=self.model.path,settings=self.model.settings,state={'broadcast':{'config':self.values}})
            draw_corner_header(painter,preview)
        finally:painter.end()


class CornerHeaderDialog(SettingsWindow):
    """Local logo import and two upright, naturally spaced text lines."""
    def __init__(self,main,parent=None):
        super().__init__(main if isinstance(main,QWidget) else parent);self.main=main;self.model=main.model;self.manager=main.manager
        self.set_transient_owner(parent or main)
        self.loading=False;self._applying=False;self._theme_signature=None
        self.setWindowTitle('赛事标识与 Logo');self.resize(700,700);self.setMinimumSize(570,540);self.setModal(False)
        root=QVBoxLayout(self);self.preview=CornerPreview(self.model,self);root.addWidget(self.preview)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.form_scroll=scroll;body=QWidget();form=QFormLayout(body);form.setContentsMargins(7,7,7,7);scroll.setWidget(body);root.addWidget(scroll,1)
        self.logo_reference='';self.logo_label=QLabel();self.logo_label.setWordWrap(True);self.logo_label.setProperty('i18nLiteral',True)
        self.choose_logo_button=button('选择本地 Logo',self.choose_logo);self.clear_logo_button=button('清除 Logo',self.clear_logo)
        form.addRow('赛事 Logo',row(self.logo_label,self.choose_logo_button,self.clear_logo_button))
        form.addRow(note('选择或清除 Logo 会立即保存到当前赛事。文字和版式请单独保存。'))
        self.top_text=QLineEdit();self.bottom_text=QLineEdit()
        for control in (self.top_text,self.bottom_text):control.setMaxLength(80)
        self.match=QLineEdit();self.match.setMaxLength(12)
        self.top_size=integer(12,128);self.bottom_size=integer(12,72)
        self.top_tracking=decimal(-4,20,1);self.bottom_tracking=decimal(-4,20,1)
        for control in (self.top_size,self.bottom_size):control.setSuffix(' px')
        for control in (self.top_tracking,self.bottom_tracking):control.setSuffix(' px')
        form.addRow('上段文字',self.top_text);form.addRow('上段字号 / 字间距',row(self.top_size,self.top_tracking))
        form.addRow('下段文字',self.bottom_text);form.addRow('下段字号 / 字间距',row(self.bottom_size,self.bottom_tracking))
        form.addRow('右侧场次（可留空）',self.match)
        self.logo_scale=integer(50,125);self.logo_scale.setSuffix('%')
        self.logo_x=integer(-80,80);self.logo_y=integer(-80,80)
        self.colour=QLineEdit();self.colour.setMaxLength(40)
        form.addRow('Logo 比例',self.logo_scale);form.addRow('Logo 水平 / 垂直偏移',row(self.logo_x,self.logo_y));form.addRow('文字颜色',self.colour)
        form.addRow(note('两段文字使用自然字宽和直体；长文字等比缩小适应边界。横线始终水平。Logo 调整限制在左侧安全区域。'))
        self.status=QLabel();self.status.setWordWrap(True);self.status.setMinimumHeight(28);root.addWidget(self.status)
        self.reload_button=button('重新读取',self.reload);self.apply_button=button('保存',self.apply)
        self.show_button=button('保存并显示',lambda:self.apply(show=True))
        root.addLayout(row(self.reload_button,1,self.apply_button,self.show_button,button('关闭',self.close)))
        for control in (self.top_text,self.bottom_text,self.match,self.colour):control.textChanged.connect(self.draft_changed)
        for control in (self.top_size,self.bottom_size,self.top_tracking,self.bottom_tracking,self.logo_scale,self.logo_x,self.logo_y):control.valueChanged.connect(self.draft_changed)
        self.model.changed.connect(self.on_model_changed);self.model.display_changed.connect(self.on_display_changed)
        self.reload();self.on_display_changed()
        self.track_settings('corner',self.draft_values,self.apply)

    def identity(self):return (str(self.model.path),(self.model.event or {}).get('id'))

    def current_values(self):
        result=corner_options(self.model.state['broadcast']['config'])
        result['corner_top_text'],result['corner_bottom_text']=corner_texts(result)
        return result

    def draft_values(self):
        return dict(corner_top_text=self.top_text.text(),corner_bottom_text=self.bottom_text.text(),corner_match=self.match.text(),
            corner_top_size=self.top_size.value(),corner_bottom_size=self.bottom_size.value(),
            corner_top_tracking=self.top_tracking.value(),corner_bottom_tracking=self.bottom_tracking.value(),
            corner_logo_scale=self.logo_scale.value()/100.,corner_logo_x=self.logo_x.value(),corner_logo_y=self.logo_y.value(),corner_colour=self.colour.text().strip())

    @saves_section('corner')
    def reload(self,*_):
        self.loading=True;self.loaded_identity=self.identity();self.baseline=self.current_values()
        for control,key in ((self.top_text,'corner_top_text'),(self.bottom_text,'corner_bottom_text'),(self.match,'corner_match'),(self.colour,'corner_colour')):control.setText(self.baseline[key])
        for control,key in ((self.top_size,'corner_top_size'),(self.bottom_size,'corner_bottom_size'),(self.top_tracking,'corner_top_tracking'),(self.bottom_tracking,'corner_bottom_tracking'),(self.logo_x,'corner_logo_x'),(self.logo_y,'corner_logo_y')):control.setValue(self.baseline[key])
        self.logo_scale.setValue(round(self.baseline['corner_logo_scale']*100));self.loading=False;self.status.clear();self.draft_changed()

    def draft_changed(self,*_):
        if self.loading:return
        values=self.current_values()
        self.logo_reference=values['corner_logo']
        self.logo_label.setText(tr('已保存赛事 Logo' if self.logo_reference else '尚未选择 Logo'))
        self.logo_label.setToolTip(self.logo_reference)
        try:
            draft=self.draft_values()
            values.update({key:value for key,value in draft.items() if value!=self.baseline[key]})
            validate_corner_config(values);self.preview.values=values;self.preview.update()
        except ValueError as error:status_style(self.status,'error');self.status.setText(str(error))

    def choose_logo(self):
        identity=self.identity()
        path,_=QFileDialog.getOpenFileName(self,'选择赛事 Logo','','图片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if path:
            self.save_logo(path,identity)

    def clear_logo(self):
        self.save_logo('',self.identity())

    def save_logo(self, reference, identity):
        """Persist the image independently of the text and layout drafts."""
        try:
            if identity != self.loaded_identity or identity != self.identity():
                raise ValueError(tr('赛事已切换，请重新选择 Logo'))
            self.model.configure_corner_header({'corner_logo':reference})
        except (ValueError, OSError) as error:
            status_style(self.status, 'error')
            self.status.setText(str(error))
            return
        status_style(self.status, 'success')
        self.status.setText(tr('Logo 已复制到项目素材库并保存' if reference else '赛事 Logo 已清除'))

    @saves_section('corner')
    def apply(self,checked=False,show=False):
        try:
            if self.identity()!=self.loaded_identity:raise ValueError('赛事已切换，请重新读取当前标识')
            current=self.current_values();draft=self.draft_values();changes={key:value for key,value in draft.items() if value!=self.baseline[key]}
            if any(current[key]!=self.baseline[key] and current[key]!=value for key,value in changes.items()):raise ValueError('标识已在别处更新，请重新读取后应用')
            candidate=dict(current);candidate.update(changes);validate_corner_config(candidate)
            self._applying=True
            if changes:self.model.configure_corner_header(changes)
            if show:self.manager.set_extension_visible('corner_header',True)
            self.reload();status_style(self.status,'success');self.status.setText('赛事标识已应用')
        except (ValueError,OSError) as error:status_style(self.status,'error');self.status.setText(str(error));return False
        finally:self._applying=False

    def discard_settings(self):self.reload()

    def on_model_changed(self,before,after,kind):
        if self._applying:return
        if self.identity()!=self.loaded_identity or kind=='import':
            self.reload();self.status.setText('赛事已切换，已读取当前赛事标识。')
        elif kind=='corner_header' and not any(value!=self.baseline[key] for key,value in self.draft_values().items()):self.reload()
        else:self.draft_changed()

    def on_display_changed(self):
        signature=tuple(self.model.settings.get(key) for key in ('console_theme','console_font_family','console_font_size'))
        if signature!=self._theme_signature:self._theme_signature=signature;apply_theme(self,self.model.settings)
        self.preview.update()
