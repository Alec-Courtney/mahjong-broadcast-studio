"""A separate event workbench; the broadcast desk stays focused on live input."""
from __future__ import annotations

import copy
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import Qt, QSignalBlocker, QRectF, QSize
from PySide6.QtGui import QPainter, QColor, QIcon, QImage, QPixmap, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,QGroupBox,QGridLayout,
    QScrollArea, QSizePolicy, QStackedWidget, QStyledItemDelegate, QTableWidgetSelectionRange, QVBoxLayout, QWidget,QInputDialog,QMenu,QMessageBox,
)
from ui_widgets import button, row, choice, integer, decimal, note, selected, table, item
from desk_theme import apply_theme, status as set_status_style
from color_input import ColorDelegate, ColorInput
from event_store import EventStore, KINDS, validate_event, now
from event_history import EventHistoryDialog
from event_penalties import net_results, penalty_targets
from event_roster import PLAYER_STATUSES, selected_profile
from event_teams import DEFAULT_COLOR, assigned_team_id, assign_team, is_rgb_color, known_teams, player_color
from i18n import get_language, signals, tr
import ranking_reports as reports
import event_schedule as schedule
from bracket_artwork import render_bracket


def safe_name(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip('. ')[:90] or '赛事'


from settings_window import SettingsWindow, form_values, saves_section


def history_baseline(player):
    saved = player.get('result_baseline', {})
    return dict(counts=saved.get('counts', [0] * 4), max_score=saved.get('max_score'))


class ColumnDialog(SettingsWindow):
    def __init__(self, parent, event, mode):
        super().__init__(parent); self.event_data=event; self.mode=mode; self.setWindowTitle('调整榜单列'); self.resize(400,600)
        layout=QVBoxLayout(self); layout.addWidget(note('拖动或使用上移/下移调整顺序，勾选需要显示的列。排名和名称必须保留。'))
        self.names=copy.deepcopy(event.get('report_headers',{}).get(mode,{}))
        self.list=QListWidget(); self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove); layout.addWidget(self.list)
        self.label_edit=QLineEdit();self.label_edit.setMaxLength(60);layout.addLayout(row('所选表头名称',self.label_edit));self.list.currentItemChanged.connect(self.select_label);self.label_edit.textEdited.connect(self.rename_label)
        self.populate(reports.columns(event,mode)); layout.addLayout(row(button('上移',lambda:self.move(-1)),button('下移',lambda:self.move(1)),button('恢复默认',self.reset)))
        self.error=QLabel();self.error.setObjectName('error');self.error.setWordWrap(True);layout.addWidget(self.error)
        layout.addLayout(row(1,button('关闭',self.close),button('保存列设置',self.save)))
        self.track_settings('columns',lambda:(self.values(),copy.deepcopy(self.names),[(self.list.item(i).data(Qt.ItemDataRole.UserRole),self.list.item(i).checkState()) for i in range(self.list.count())]),self.save)
    @saves_section('columns')
    def save(self):
        workspace=self._settings_owner
        try:
            mode=self.mode
            mapping=copy.deepcopy(workspace.model.event.get('report_columns',{}));mapping[mode]=self.values()
            labels=copy.deepcopy(workspace.model.event.get('report_headers',{}));labels[mode]=copy.deepcopy(self.header_values())
            workspace.store.update(report_columns=mapping,report_headers=labels)
            workspace.refresh_reports();self.error.clear();return True
        except Exception as error:self.error.setText(str(error));return False

    def populate(self, active):
        self.list.clear(); required={'rank','team' if self.mode=='team' else 'name'}
        for key in active+[k for k in reports.allowed_columns(self.event_data['kind'],self.mode) if k not in active]:
            entry=QListWidgetItem(self.names.get(key) or reports.header(key,self.mode)); entry.setData(Qt.ItemDataRole.UserRole,key);entry.setToolTip('计算字段：'+reports.header(key,self.mode))
            entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            entry.setCheckState(Qt.CheckState.Checked if key in active else Qt.CheckState.Unchecked)
            if key in required:entry.setFlags(entry.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.list.addItem(entry)
        self.list.setCurrentRow(0)
    def select_label(self,current,previous):
        self.label_edit.setText(current.text() if current else '')
    def rename_label(self,value):
        current=self.list.currentItem()
        if current:current.setText(value);self.names[current.data(Qt.ItemDataRole.UserRole)]=value.strip()
    def reset(self):
        self.names={};self.populate(reports.default_columns(self.event_data['kind'],self.mode))
    def move(self,direction):
        index=self.list.currentRow(); target=index+direction
        if index>=0 and 0<=target<self.list.count():
            entry=self.list.takeItem(index); self.list.insertItem(target,entry); self.list.setCurrentRow(target)
    def values(self):
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count()) if self.list.item(i).checkState()==Qt.CheckState.Checked]
    def header_values(self):return self.names


class ReportOutputWindow(QWidget):
    """A clean, separately capturable results window; never edits a match."""
    def __init__(self, main):
        super().__init__(None,Qt.WindowType.Window); self.main=main; self.image=None
        self.setWindowTitle('麻将赛事 · 榜单播出'); self.resize(1100,680)
        geometry=main.model.settings.get('report_output_geometry')
        if isinstance(geometry,list) and len(geometry)==4:self.setGeometry(*geometry)
    def set_image(self,image):self.image=image; self.update(); self.show(); self.raise_()
    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor('#0d201d'))
        if self.image is not None:
            size=self.image.size().scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatio)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(QRectF((self.width()-size.width())/2,(self.height()-size.height())/2,size.width(),size.height()),self.image)
        painter.end()
    def closeEvent(self,event):
        # Closing a live output is local. The next workspace save records its position.
        self.main.model.settings['report_output_geometry'] = [self.x(), self.y(), self.width(), self.height()]
        super().closeEvent(event)


class NodeDialog(QDialog):
    def __init__(self,parent,state,event,node=None):
        super().__init__(parent); self.setWindowTitle('编排四人桌'); self.setMinimumWidth(820)
        self.original=copy.deepcopy(node) if node else None; self.state=state; self.event_data=event
        self.node_id=node['id'] if node else uuid.uuid4().hex
        self.structure_locked=bool(node and (node['status']!='pending' or schedule.node_matches(state,node['id'])))
        self._new_profiles={};self.additional_players=[]
        layout=QVBoxLayout(self); form=QFormLayout(); layout.addLayout(form)
        self.name=QLineEdit(node['name'] if node else schedule.next_table_name(event['bracket']['nodes']));self.name.setMaxLength(80)
        self.round=integer(1,32); self.round.setValue(node['round'] if node else 1)
        self.games=integer(1,1000); self.games.setValue(node['planned_games'] if node else 1)
        self.advance=integer(1,3); self.advance.setValue(node['advance_count'] if node else 2)
        form.addRow('桌名',self.name); form.addRow('轮次（数字大在上方）',self.round); form.addRow('本桌半庄数',self.games); form.addRow('晋级人数',self.advance)
        self.sources=[];self.manual_players=[]
        roster=state['broadcast']['roster'];self.profiles={player['id']:player for player in roster}
        self.available_players=[player for player in roster if not player.get('hidden')]
        name_counts={name:sum(player['name']==name for player in roster) for name in {player['name'] for player in roster}}
        for index in range(4):
            combo=QComboBox(); combo.setProperty('i18n_literal',True)
            self.sources.append(combo)
            manual=QComboBox();manual.setEditable(True);manual.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            manual.setProperty('i18n_literal',True);manual.lineEdit().setMaxLength(80)
            manual.addItem('按来源自动','')
            override=(node or {}).get('source_overrides',{}).get(str(index),'')
            for player in roster:
                if player.get('hidden') and player['id']!=override:continue
                title=player['name'] if name_counts[player['name']]==1 else player['name']+' · '+(player.get('team') or player['id'][:8])
                if player.get('hidden'):title+=tr('（已屏蔽）')
                manual.addItem(title,player['id'])
            selected(manual,override);self.manual_players.append(manual)
            form.addRow(f'席位 {index+1}',row(combo,'指定选手',manual))
        self.refresh_sources(initial=True);self.round.valueChanged.connect(self.refresh_sources)
        for widget in (self.round,self.games,self.advance,*self.sources,*self.manual_players):widget.setEnabled(not self.structure_locked)
        layout.addWidget(note('已开赛桌仅可修改桌名。' if self.structure_locked else
            '左侧选择自动来源；右侧可选名册选手或直接输入新名字。清空右侧或选择“按来源自动”即可恢复自动晋级。'))
        self.error=QLabel();self.error.setObjectName('error');self.error.setWordWrap(True);layout.addWidget(self.error)
        layout.addLayout(row(1,button('取消',self.reject),button('保存四人桌',self.accept)))
    def refresh_sources(self,*_,initial=False):
        for index,combo in enumerate(self.sources):
            saved = self.original['sources'][index] if self.original else None
            source = combo.currentData()
            if initial:
                source = saved if self.original else ({'player': self.available_players[index]['id']}
                                                       if index < len(self.available_players) else None)
            blocker=QSignalBlocker(combo);combo.clear();combo.addItem('请选择席位来源',None)
            for player in self.available_players:combo.addItem(player['name'],{'player':player['id']})
            saved_profile = self.profiles.get(saved.get('player')) if saved else None
            if saved_profile and saved_profile.get('hidden'):
                combo.addItem(saved_profile['name']+tr('（已屏蔽）'), saved)
            for upstream in self.event_data['bracket']['nodes']:
                if upstream['round']>=self.round.value() or (self.original and upstream['id']==self.original['id']):continue
                for place in range(1,upstream['advance_count']+1):
                    value={'node':upstream['id'],'place':place};combo.addItem(schedule.source_label(self.event_data,value),value)
            selected(combo,source);del blocker
    def manual_identity(self,combo):
        name=combo.currentText().strip()
        if not name or name=='按来源自动':return None
        identity=combo.currentData()
        if identity in self.profiles and name==combo.itemText(combo.currentIndex()):return identity
        matches=[profile['id'] for profile in self.profiles.values() if profile['name']==name]
        if len(matches)>1:raise ValueError('存在同名选手，请从下拉列表中选择对应队伍或编号')
        if matches and self.profiles[matches[0]].get('hidden'):
            raise ValueError('阵容含有已屏蔽选手，请改选或先恢复该选手')
        if matches:return matches[0]
        if name not in self._new_profiles:
            self._new_profiles[name]=dict(id=uuid.uuid4().hex,name=name,team='',team_id='',photo='',color='#377e72',
                base_points=0.,games=0,status='active',stats={})
        return self._new_profiles[name]['id']
    def value(self):
        node=copy.deepcopy(self.original) if self.original else dict(id=self.node_id,status='pending',winners=[])
        if self.structure_locked:node['name']=self.name.text().strip();return node
        node.update(name=self.name.text().strip(),round=self.round.value(),planned_games=self.games.value(),advance_count=self.advance.value(),sources=[w.currentData() for w in self.sources])
        overrides={str(index):identity for index,combo in enumerate(self.manual_players) if (identity:=self.manual_identity(combo))}
        if overrides:node['source_overrides']=overrides
        else:node.pop('source_overrides',None)
        self.additional_players=[copy.deepcopy(profile) for profile in self._new_profiles.values() if profile['id'] in overrides.values()]
        return node
    def accept(self):
        try:
            node=self.value();event=copy.deepcopy(self.event_data);state=copy.deepcopy(self.state)
            state['broadcast']['roster'].extend(copy.deepcopy(self.additional_players))
            event['bracket']['nodes']=[node if item['id']==node['id'] else item for item in event['bracket']['nodes']] if self.original else [*event['bracket']['nodes'],node]
            schedule.validate_bracket(event['bracket'],state)
        except Exception as error:self.error.setText(str(error));return
        super().accept()


class ResultEntryDialog(QDialog):
    def __init__(self,parent,record=None,node=None):
        super().__init__(parent);self.workspace=parent;self.model=parent.model;self.record=record;self.node=node;self.preview=None
        self.setWindowTitle('补录 / 更正其他桌成绩');self.resize(620,520);layout=QVBoxLayout(self)
        self.title=QLineEdit(record['title'] if record else node['name'] if node else '补录半庄');layout.addLayout(row('记录标题',self.title))
        self.initial_dealer=choice([(f'席位 {i+1}',i) for i in range(4)]);selected(self.initial_dealer,record.get('initial_dealer',0) if record else 0);layout.addLayout(row('起家',self.initial_dealer));self.initial_dealer.currentIndexChanged.connect(self.invalidate)
        self.seats=[];self.scores=[];self.results=QLabel();self.results.setWordWrap(True)
        form=QFormLayout()
        roster=self.model.state['broadcast']['roster']
        if node:
            sources=schedule.resolve_sources(self.model.event,node)
        elif record:
            from ranking_order import match_seats
            sources=match_seats(record) or [r['id'] for r in record['results']]
        else:
            roster=[p for p in roster if not p.get('hidden')]
            sources=([p['id'] for p in roster]+[None]*4)[:4]
        scores={r['id']:r['score'] for r in record['results']} if record else {}
        for i in range(4):
            combo=choice([('请选择选手',None),*((p['name'],p['id']) for p in roster)])
            combo.setProperty('i18n_literal',True);selected(combo,sources[i]);combo.setEnabled(not node and not record)
            score=integer(-1000000000,1000000000,100);score.setValue(scores.get(sources[i],self.model.state['broadcast']['config']['start_points']))
            self.seats.append(combo);self.scores.append(score);form.addRow(f'席位 {i+1}',row(combo,score))
            combo.currentIndexChanged.connect(self.invalidate);score.valueChanged.connect(self.invalidate)
        layout.addLayout(form);layout.addWidget(note('填写半庄最终持点。补录只增加成绩与顺位，不生成逐局技术统计。总持点应包含终局供托；罚分导致的差额由裁判核实。'))
        layout.addWidget(button('计算并预览积分',self.calculate));layout.addWidget(self.results,1)
        self.error=QLabel();self.error.setWordWrap(True);self.error.setObjectName('error');layout.addWidget(self.error)
        self.confirm=button('确认保存成绩',self.save);self.confirm.setEnabled(False);layout.addLayout(row(button('取消',self.reject),1,self.confirm))
    def invalidate(self,*_):
        self.preview=None
        if hasattr(self,'confirm'):self.confirm.setEnabled(False)
    def calculate(self):
        from event_results import prepare_result
        try:
            self.preview=prepare_result(self.model,[w.currentData() for w in self.seats],[w.value() for w in self.scores],
                node_id=self.node['id'] if self.node else '',match_id=self.record['id'] if self.record else None,title=self.title.text().strip() or '补录半庄',initial_dealer=self.initial_dealer.currentData())
            self.results.setText('\n'.join(f"{r['rank']}位  {r['name']}   {r['score']:,}点   {r['points']:+.1f} pt" for r in self.preview['results']))
            self.confirm.setEnabled(True);self.error.clear()
        except Exception as error:self.error.setText(str(error));self.confirm.setEnabled(False)
    def save(self):
        from event_results import record_result
        try:
            if self.preview is None:raise ValueError('请先预览积分')
            record_result(self.workspace.store,self.preview);self.accept()
        except Exception as error:self.error.setText(str(error))


class ResultsDialog(QDialog):
    def __init__(self,workspace):
        super().__init__(workspace)
        self.workspace=workspace
        self.event_id=workspace.model.event['id']
        self.matches=[]
        self.setWindowTitle('已确认成绩与更正')
        self.resize(1120,620)
        self.setMinimumSize(920,440)
        layout=QVBoxLayout(self)
        layout.addWidget(note('每条半庄分两行展示，每行两名选手；pt 为判罚后的入账积分。悬停选手可查看最终持点。'))
        self.table=table(['记录标题','来源','记录时间','选手 / 扣分详情','入账 pt','选手 / 扣分详情','入账 pt'])
        self.table.setAlternatingRowColors(False)
        header=self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in (0,3,5):
            header.setSectionResizeMode(column,QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2,QHeaderView.ResizeMode.Fixed)
        header.sectionResized.connect(self.resize_record_rows)
        layout.addWidget(self.table,1)
        self.status=QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty('i18n_literal',True)
        layout.addWidget(self.status)
        self.edit_button=button('更正选中补录成绩',self.edit)
        self.penalties_button=button('选中半庄的判罚',self.penalties)
        self.delete_button=button('删除选中半庄记录',self.delete)
        layout.addLayout(row(self.edit_button,self.penalties_button,self.delete_button,1,button('关闭',self.close)))
        self.table.itemSelectionChanged.connect(self.select_record)
        self.table.currentCellChanged.connect(self.select_record)
        self.workspace.model.changed.connect(self.reload)
        self.reload()

    def selected_record(self):
        index=self.table.currentRow()//2
        return self.matches[index] if 0<=index<len(self.matches) else None

    def resize_record_rows(self,column,*_):
        if column in (0,3,5):self.table.resizeRowsToContents()

    def select_record(self,*_):
        record=self.selected_record()
        if record:
            start=(self.table.currentRow()//2)*2
            with QSignalBlocker(self.table):
                self.table.clearSelection()
                self.table.setRangeSelected(QTableWidgetSelectionRange(start,0,start+1,self.table.columnCount()-1),True)
        self.edit_button.setEnabled(bool(record and record.get('source')=='manual'))
        self.penalties_button.setEnabled(record is not None)
        self.delete_button.setEnabled(record is not None)

    def reload(self,*_):
        from event_results import recorded_time

        selected_record=self.selected_record()
        identity=selected_record['id'] if selected_record else None
        self.matches=(reports.confirmed_matches(self.workspace.model.state)
                      if self.workspace.model.event['id']==self.event_id else [])
        with QSignalBlocker(self.table):
            self.table.clearSpans()
            self.table.clearContents()
            self.table.setRowCount(2*len(self.matches))
            self.table.setColumnWidth(2,max(114,self.table.fontMetrics().horizontalAdvance('0000-00-00')+24))
            self.populate_records(recorded_time)
            self.table.resizeRowsToContents()
            index=next((index for index,record in enumerate(self.matches) if record['id']==identity),0)
            if self.matches:self.table.setCurrentCell(index*2,0)
        self.select_record()
        if not self.matches:self.status.setText(tr('此赛事暂无已确认的半庄。'))

    def populate_records(self,recorded_time):
        for index,record in enumerate(self.matches):
            start=index*2
            active=[entry for entry in record.get('penalties',[]) if not entry.get('voided_at')]
            metadata={0:record.get('title') or tr('半庄'),1:tr('补录' if record.get('source')=='manual' else '导播台'),
                      2:recorded_time(record).replace(' ','\n')}
            teams=penalty_targets(record,'team')
            for entry in active:
                if entry['scope']=='team':
                    metadata[0]+='\n'+tr('队伍判罚')+'：'+teams[entry['target']]+' '+self.penalty_text(entry)
            for column,value in metadata.items():
                cell=item(value,record['id'],editable=False)
                cell.setToolTip(str(value).replace('\n',' '))
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter if column else Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft)
                self.table.setItem(start,column,cell)
                self.table.setSpan(start,column,2,1)
            for seat,result in enumerate(net_results(record)):
                line=start+seat//2
                column=3+(seat%2)*2
                penalties=[entry for entry in active if entry['scope']=='player' and entry['target']==result['id']]
                label='\n'.join([result['name'],*(self.penalty_text(entry) for entry in penalties)])
                cell=item(label,record['id'],editable=False)
                score=result.get('score')
                tooltip=[label]
                if isinstance(score,(int,float)):tooltip.append(tr('最终持点')+f'：{score:,}')
                tooltip.extend(entry['note'] for entry in penalties if entry.get('note'))
                cell.setToolTip('\n'.join(tooltip))
                self.table.setItem(line,column,cell)
                points=item(f"{result['points']:+,.1f}".replace('-','−'),record['id'],editable=False)
                points.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(line,column+1,points)

    @staticmethod
    def penalty_text(entry):
        return f"−{entry['points']:g} pt · {entry['reason']}"

    def penalties(self):
        record=self.selected_record()
        if record:
            self.workspace.show_match_penalties(record['id'],self)
            self.reload()

    def edit(self):
        record=self.selected_record()
        if not record:return
        if record.get('source')!='manual':self.workspace.status.setText('直播半庄请在导播台更正；此处可更正补录成绩。');return
        dialog=ResultEntryDialog(self.workspace,record=record)
        try:
            if dialog.exec()==QDialog.DialogCode.Accepted:self.reload();self.workspace.refresh_reports();self.workspace.refresh_bracket()
        finally:dialog.deleteLater()

    def delete(self):
        record=self.selected_record()
        if not record:return
        message=tr('删除后，此半庄的积分、场次、技术统计和判罚将从当前赛事中移除，无法通过导播台撤销恢复。')
        message+='\n\n'+(record.get('title') or tr('半庄'))+'\n'+' / '.join(result['name'] for result in record['results'])
        if QMessageBox.question(self,tr('删除半庄记录'),message,
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        try:
            if self.workspace.model.event['id']!=self.event_id:raise ValueError('赛事已切换，请重新打开成绩记录')
            self.workspace.model.records.delete_match(record['id'],expected_record=record)
        except (ValueError,OSError) as error:
            set_status_style(self.status,'error');self.status.setText(str(error));return
        self.reload()
        set_status_style(self.status,'success')
        self.status.setText(tr('半庄记录已删除，榜单已更新。'))




class DeletionNameEdit(QLineEdit):
    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Paste):
            event.accept()
            return
        super().keyPressEvent(event)


class DeleteEventDialog(QDialog):
    def __init__(self, parent, name, current):
        super().__init__(parent)
        self.name = name
        self.setWindowTitle('永久删除赛事')
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        warning = QLabel('此赛事的所有数据将永久清除，包括规则、名册、比赛进度、历史成绩、赛程，以及赛事目录中的素材和导出文件。删除后无法恢复。')
        warning.setWordWrap(True)
        warning.setObjectName('error')
        layout.addWidget(warning)
        if current:
            layout.addWidget(note('删除后将打开赛事库中的另一场赛事。'))
        layout.addWidget(note('删除范围为本赛事目录。共享素材库和另存的备份单独管理。'))
        layout.addWidget(QLabel('请手动输入下面的赛事名称，名称完全一致后才能确认删除。'))
        event_name = QLabel(name)
        event_name.setTextFormat(Qt.TextFormat.PlainText)
        event_name.setProperty('i18n_literal', True)
        event_name.setWordWrap(True)
        layout.addWidget(event_name)
        self.confirmation = DeletionNameEdit()
        self.confirmation.setAccessibleName('输入赛事名称以确认删除')
        self.confirmation.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.confirmation.setAcceptDrops(False)
        layout.addWidget(self.confirmation)
        cancel = button('取消', self.reject)
        cancel.setDefault(True)
        self.confirm = button('确认删除', self.accept)
        self.confirm.setAutoDefault(False)
        self.confirm.setEnabled(False)
        self.confirmation.textChanged.connect(lambda text: self.confirm.setEnabled(text == self.name))
        layout.addLayout(row(1, cancel, self.confirm))
        self.confirmation.setFocus()

    def accept(self):
        if self.confirmation.text() == self.name:
            super().accept()


class TeamAssignmentDelegate(QStyledItemDelegate):
    def __init__(self, workspace):
        super().__init__(workspace.roster)
        self.workspace = workspace

    def createEditor(self, parent, option, index):
        editor = QComboBox(parent)
        editor.setProperty('i18n_literal', True)
        editor.addItem(tr('未分队'), '')
        for label, identity in self.workspace.team_entries(self.workspace.saved_teams):
            editor.addItem(label, identity)
        editor.activated.connect(lambda _: self.commit_team(editor))
        return editor

    def commit_team(self, editor):
        self.commitData.emit(editor)
        self.closeEditor.emit(editor)

    def setEditorData(self, editor, index):
        selected(editor, index.data(Qt.ItemDataRole.UserRole) or '')

    def setModelData(self, editor, model, index):
        identity = editor.currentData() or ''
        model.setData(index, identity, Qt.ItemDataRole.UserRole)
        model.setData(index, self.workspace.team_label(identity))


class EventWorkspaceDialog(SettingsWindow):
    def __init__(self,main):
        super().__init__(main); self.main=main; self.model=main.model; self.loading=False
        self.store=self.model.event_store or EventStore(self.model)
        self._report_source=None;self._reports={};self.report_data=None;self._output_report=None
        self.broadcast_settings=None;self.output_editor=None
        self.setWindowTitle('赛事工作台'); self.resize(1240,820); self.setMinimumSize(980,650)
        root=QVBoxLayout(self); self.heading=QLabel(); self.heading.setProperty('i18n_literal',True);self.heading.setStyleSheet('font-size:21px;font-weight:600;padding:6px 0');root.addWidget(self.heading)
        self.demo_description=QLabel();self.demo_description.setWordWrap(True);self.demo_description.setProperty('i18n_literal',True);self.demo_description.setTextFormat(Qt.TextFormat.PlainText);self.demo_description.setObjectName('muted');root.addWidget(self.demo_description)
        body=QHBoxLayout(); self.nav=QListWidget(); self.nav.setFixedWidth(164)
        self.nav.addItems(['赛事库','赛事设置','名册与场次','排名与结果','淘汰赛程','备份与说明']);body.addWidget(self.nav)
        self.pages=QStackedWidget();body.addWidget(self.pages,1);root.addLayout(body,1)
        self.status=QLabel(); self.status.setWordWrap(True);self.status.setMinimumHeight(28); self.status.setProperty('i18n_literal',True);root.addWidget(self.status)
        root.addLayout(row(QLabel('赛事规则、进度和成绩分别保存，可从赛事库切换。'),1,button('保存修改',self.save_changes),button('返回导播台',self.close)))
        self.build_library();self.build_general();self.build_roster();self.build_reports();self.build_bracket();self.build_backup()
        self.nav.currentRowChanged.connect(self.select_page);self.model.changed.connect(self.on_model_changed);self.model.display_changed.connect(self.on_display_changed)
        self.loaded_id=None;self.reload_all();self.nav.setCurrentRow(0)
        apply_theme(self,self.model.settings)
        self.resize_identity_columns()
        self.track_settings('general',lambda:form_values(self.event_name,self.event_kind,self.total_games,self.advance_count,self.start_points,self.return_points,*self.uma,self.oka,self.tie,self.riichi_payment,self.kiriage,self.kazoe),self.save_general)
        self.track_settings('roster',self.roster_draft_values,self.save_roster)
        self.track_settings('teams',lambda:form_values(self.teams,self.team_logo_drafts) if self.is_team_event() else (),self.save_teams)
        self.track_settings('seats',lambda:form_values(*self.table_seats),self.save_table_seats)
    def after_settings_saved(self):self.reload_all()
    def discard_settings(self):self.reload_all()
    def scroll_page(self):
        scroll=QScrollArea();scroll.setWidgetResizable(True);widget=QWidget();layout=QVBoxLayout(widget)
        layout.setContentsMargins(14,8,14,12);layout.setSpacing(12);scroll.setWidget(widget)
        return scroll,layout
    def page(self):
        scroll,layout=self.scroll_page();self.pages.addWidget(scroll);return layout
    def run(self,callback,message='已保存到当前赛事'):
        try:
            result=callback();set_status_style(self.status,'success');self.status.setText(message);return result
        except Exception as error:
            self._save_failed=True;set_status_style(self.status,'error');self.status.setText(str(error));return None
    def selected_id(self,widget):
        index=widget.currentRow()
        return widget.item(index,0).data(Qt.ItemDataRole.UserRole) if index>=0 and widget.item(index,0) else None
    def build_library(self):
        page=self.page();page.addWidget(QLabel('打开赛事，继续上次的比赛和设置'))
        page.addWidget(note('首次使用可双击列表中的「【演示】」赛事，也可点「导入完整赛事 JSON」选择 demo-suite 文件夹里的示例。计分与试播步骤见「备份与说明」。'))
        self.library_events = {}
        self.library=table(['赛事名称','赛制','选手','已确认半庄','状态']);self.library.setMinimumHeight(235);page.addWidget(self.library)
        self.library.cellDoubleClicked.connect(lambda *_:self.open_selected())
        self.library_history = button('查看历史成绩', self.show_selected_history)
        self.library_delete = button('删除选中赛事', self.delete_selected)
        self.library.currentItemChanged.connect(self.update_library_actions)
        page.addLayout(row(button('打开选中赛事', self.open_selected), self.library_history,
            button('刷新赛事库', self.refresh_library), button('导入完整赛事 JSON', self.import_event),
            1, self.library_delete))
        form=QFormLayout();self.new_name=QLineEdit();self.new_name.setPlaceholderText('例如：秋季联赛 · A组');self.new_name.setMaxLength(100)
        self.new_kind=choice([(label,key) for key,label in KINDS.items()])
        self.settings_source=QComboBox();self.settings_source.setProperty('i18n_literal',True)
        self.settings_file=None
        form.addRow('新赛事名称',self.new_name);form.addRow('比赛类型',self.new_kind)
        form.addRow('设置来源',row(self.settings_source,button('选择设置文件',self.choose_event_template)))
        page.addLayout(form)
        create=button('新建并打开赛事',self.create_event);create.setObjectName('primary');page.addLayout(row(create,1));page.addWidget(note('比赛类型创建后固定。设置来源可以选已有赛事或设置文件，新赛事从默认四席开始。'))
        page.addWidget(note('团队积分赛：个人榜＋团队榜，晋级名额按队伍计算。个人积分赛：全员积分榜，晋级名额按选手计算。个人淘汰赛：每桌独立设置晋级人数。'));page.addStretch()
    def refresh_library(self):
        values=self.store.list_events();self.library_events={event['id']:event for event in values}
        source=self.settings_source.currentData()
        with QSignalBlocker(self.settings_source):
            self.settings_source.clear()
            self.settings_source.addItem(tr('使用默认设置'),'')
            for entry in values:
                if not entry['error']:
                    self.settings_source.addItem(entry['name'],entry['id'])
            if self.settings_file:
                self.settings_source.addItem(Path(self.settings_file).name,'file')
            selected(self.settings_source,source)
        self.library.setRowCount(len(values))
        for index,event in enumerate(values):
            for column,value in enumerate((event['name'],KINDS.get(event['kind'],'—'),event['players'],event['matches'],'当前赛事' if event['current'] else ('文件异常' if event['error'] else '可打开'))):
                self.library.setItem(index,column,item(value,event['id'] if column==0 else None,False))
        current=next((i for i,e in enumerate(values) if e['current']),0);self.library.selectRow(current)
        self.update_library_actions()
    def update_library_actions(self, *_):
        event = self.library_events.get(self.selected_id(self.library))
        readable = bool(event and not event['error'])
        self.library_history.setEnabled(readable)
        can_delete = len(self.library_events) > 1
        self.library_delete.setEnabled(readable and can_delete)
        self.library_delete.setToolTip('' if can_delete else '请先新建或导入另一场赛事，再删除最后一场赛事')
    def show_selected_history(self):
        self.show_event_history(self.selected_id(self.library))
    def show_event_history(self, identity=None):
        def load():
            return EventHistoryDialog(self, *self.store.read_history(identity or self.model.event['id']))

        dialog = self.run(load, '已读取赛事成绩')
        if dialog is None:
            return
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
    def delete_selected(self):
        identity = self.selected_id(self.library)
        event = self.library_events.get(identity)
        if not event or event['error']:
            return
        current = identity == self.model.event['id']
        dialog = DeleteEventDialog(self, event['name'], current)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            confirmation = dialog.confirmation.text()
        finally:
            dialog.deleteLater()
        if current and not self.prepare_event_change():
            return

        def remove():
            try:
                self.store.delete(identity, confirmation)
            finally:
                if current and self.model.event['id'] != identity:
                    self._event_context_change.finish(skip=self)
                self.refresh_library()

        self.run(remove, '赛事及其本地数据已删除')
    def open_selected(self):
        identity = self.selected_id(self.library)
        if not identity or identity == self.model.event['id'] or not self.prepare_event_change():
            return

        def open_event():
            if (self.model.path/'session.json').is_file():
                self.main.manager.save_geometry()
            self.store.open(identity)
            self._event_context_change.finish(skip=self)

        self.run(open_event, '赛事已打开，规则、外观和比赛进度已加载')
    def ask_event_change(self):
        return self._event_context_change.ask()
    def prepare_event_change(self):
        from settings_context import SettingsContextChange
        self._event_context_change=SettingsContextChange(self)
        if self._event_context_change.prepare(self.ask_event_change):
            return True
        if self._event_context_change.save_error:
            self._save_failed=True
            set_status_style(self.status,'error')
            self.status.setText(self._event_context_change.save_error)
        return False
    def create_event(self):
        if not self.prepare_event_change():return
        def create():
            source=self.settings_source.currentData()
            self.main.manager.save_geometry()
            self.store.create(self.new_name.text(),self.new_kind.currentData(),
                              settings_source=source if source!='file' else None,
                              settings_file=self.settings_file if source=='file' else None)
            self.new_name.clear();self.settings_file=None;self.refresh_library()
            self.settings_source.setCurrentIndex(0)
            self._event_context_change.finish(skip=self);self.nav.setCurrentRow(2)
        self.run(create,'新赛事已创建，可在名册中添加选手和选择四席')
    def choose_event_template(self):
        filename,_=QFileDialog.getOpenFileName(self,tr('选择赛事设置文件'),'','JSON (*.json)')
        if filename:
            self.settings_file=filename
            self.refresh_library()
            selected(self.settings_source,'file')
    def import_event(self):
        filename,_=QFileDialog.getOpenFileName(self,'导入为独立赛事','','JSON (*.json)')
        if filename and self.prepare_event_change():self.run(lambda:(self.store.import_new(filename),self._event_context_change.finish(skip=self)),'已导入为独立赛事，原赛事已保留')
    def build_general(self):
        self.event_settings_host=QWidget();host_layout=QVBoxLayout(self.event_settings_host);host_layout.setContentsMargins(8,4,8,0)
        self.general_page,page=self.scroll_page();host_layout.addWidget(self.general_page);self.pages.addWidget(self.event_settings_host)
        form=QFormLayout();self.event_name=QLineEdit();self.event_name.setMaxLength(100);self.event_kind=choice([(label,key) for key,label in KINDS.items()])
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.total_games=integer(0,1000000);self.total_games.setSpecialValueText('未设置');self.advance_count=integer(0,200);self.advance_count.setSpecialValueText('不设晋级线')
        self.event_kind.setEnabled(False);self.event_kind.setToolTip('比赛类型在创建后不可修改；需要其他赛制时请新建赛事。')
        self.planned_games_label=QLabel();self.advance_count_label=QLabel()
        form.addRow('赛事名称',self.event_name);form.addRow('比赛类型（创建后固定）',self.event_kind);form.addRow(self.planned_games_label,self.total_games);form.addRow(self.advance_count_label,self.advance_count)
        self.start_points=integer(100,1000000000,1000);self.return_points=integer(0,1000000000,1000);self.uma=[decimal(-100000,100000) for _ in range(4)]
        for control in self.uma:
            control.setProperty('compactUma', True)
            control.setFixedWidth(124)
        self.oka=QCheckBox('启用起点与精算原点差额的头名奖励');self.tie=choice([('按起家顺序','initial_seat'),('平分顺位分','share')]);self.riichi_payment=choice([('立即扣点','immediate'),('局末单列','deferred')]);self.kiriage=QCheckBox('切上满贯');self.kazoe=QCheckBox('累计役满')
        self.oka.setToolTip('头名额外获得四家的起点差额。例如 25,000 开场、30,000 精算，奖励为 +20 pt。四家顺位分合计须为 0。')
        form.addRow('新半庄开场点数',self.start_points);form.addRow('精算原点',self.return_points);form.addRow('1 / 2 / 3 / 4 位顺位分',row(*self.uma,1));form.addRow('',self.oka);form.addRow('半庄同点处理',self.tie);form.addRow('立直棒显示',self.riichi_payment);form.addRow('',row(self.kiriage,self.kazoe));page.addLayout(form)
        page.addWidget(note('每人或每队可以在“名册与场次”单独覆盖计划场次。开场点数用于下一半庄。累计积分同分时，先达到者在前。同一半庄达到同分时按席位顺序。'))
        page.addLayout(row(button('保存赛事设置',self.save_general),
                           button('前往名册与场次',lambda:self.nav.setCurrentRow(2)),1))
        transfer=QGroupBox('赛事设置文件');transfer_layout=QVBoxLayout(transfer)
        transfer_layout.addWidget(note('设置文件用于迁移规则、播出配置与相关素材。成绩、名册和比赛进度请使用完整赛事备份。'))
        transfer_layout.addLayout(row(button('导出赛事设置',self.export_event_template),
                                      button('导入赛事设置',self.import_event_template),1))
        page.insertWidget(0,transfer);page.addStretch()
    def export_event_template(self):
        filename,_=QFileDialog.getSaveFileName(self,tr('导出赛事设置'),
            safe_name(self.model.event['name'])+'-赛事设置.json','JSON (*.json)')
        if filename:
            from settings_transfer import export_event_settings
            self.run(lambda:export_event_settings(self.model,filename),tr('已导出当前赛事的已保存设置'))
    def import_event_template(self):
        filename,_=QFileDialog.getOpenFileName(self,tr('导入赛事设置'),'','JSON (*.json)')
        if filename and self.prepare_event_change():
            from settings_transfer import import_event_settings
            def load():
                import_event_settings(self.model,filename)
                self._event_context_change.finish(skip=self)
            self.run(load,tr('赛事设置已应用'))
    @saves_section('general')
    def save_general(self):
        def save():
            import tournament
            from settings_drafts import merge_draft
            if self.event_kind.currentData()!=self.model.event['kind']:raise ValueError('比赛类型在创建后不可修改，请新建赛事并选择所需类型')
            baseline=self._general_baseline
            values,changed,conflicts=merge_draft(baseline,self._general_draft(),baseline,self._general_live())
            if conflicts:raise ValueError('同一赛事设置已在其他窗口修改，请重新读取后再保存')
            if not changed:self.load_general();return
            candidate=copy.deepcopy(self.model.event);candidate.update(name=values['name'],planned_games=values['planned_games'],qualification_count=values['qualification_count'],updated_at=now())
            patch=dict(title=candidate['name'],event_mode='team' if candidate['kind']=='team_league' else 'individual',**{key:values[key] for key in ('start_points','return_points','uma','oka_enabled','tie_break')})
            if candidate['kind']!='individual_knockout':
                patch['qualification_mode']=patch['event_mode']
                if candidate['qualification_count']:patch['qualification_slots']=candidate['qualification_count']
            target=copy.deepcopy(self.model.state);tournament.configure(target,patch);target['title']=candidate['name'];target['rules'].update({key:values[key] for key in ('kiriageMangan','kazoeYakuman')});validate_event(candidate,target);self.model.engine.validate(target)
            from event_configuration import save_configuration
            settings=dict(self.model.settings,riichi_payment_mode=values['riichi_payment_mode'])
            save_configuration(self.model,target,candidate,settings,clear_history=True)
            self.load_general();self.refresh_library();self.refresh_heading();self.refresh_reports()
        self.run(save)
    def _general_live(self):
        event=self.model.event;config=self.model.state['broadcast']['config'];rules=self.model.state['rules']
        return dict(name=event['name'],planned_games=event.get('planned_games',0),qualification_count=event.get('qualification_count',0),
            **{key:copy.deepcopy(config[key]) for key in ('start_points','return_points','uma','oka_enabled','tie_break')},
            riichi_payment_mode=self.model.settings.get('riichi_payment_mode','immediate'),
            **{key:rules[key] for key in ('kiriageMangan','kazoeYakuman')})
    def _general_draft(self):
        return dict(name=self.event_name.text().strip(),planned_games=self.total_games.value(),qualification_count=self.advance_count.value(),
            start_points=self.start_points.value(),return_points=self.return_points.value(),uma=[w.value() for w in self.uma],
            oka_enabled=self.oka.isChecked(),tie_break=self.tie.currentData(),riichi_payment_mode=self.riichi_payment.currentData(),
            kiriageMangan=self.kiriage.isChecked(),kazoeYakuman=self.kazoe.isChecked())
    def load_general(self):
        event=self.model.event;config=self.model.state['broadcast']['config'];self.event_name.setText(event['name']);selected(self.event_kind,event['kind'])
        self.event_kind.setEnabled(False);self.planned_games_label.setText('每队计划场次' if event['kind']=='team_league' else '每人计划场次');self.advance_count_label.setText('积分榜晋级队数' if event['kind']=='team_league' else '积分榜晋级人数')
        knockout=event['kind']=='individual_knockout';self.advance_count.setEnabled(not knockout);self.advance_count.setToolTip('淘汰赛在每桌赛程中设置晋级人数。' if knockout else '与详细晋级条件使用同一名额；0 表示不标示积分榜晋级线。')
        self.total_games.setValue(event.get('planned_games',0));self.advance_count.setValue(event.get('qualification_count',0));self.start_points.setValue(config['start_points']);self.return_points.setValue(config['return_points'])
        for widget,value in zip(self.uma,config['uma']):widget.setValue(value)
        self.oka.setChecked(config['oka_enabled']);selected(self.tie,config['tie_break']);selected(self.riichi_payment,self.model.settings.get('riichi_payment_mode','immediate'));self.kiriage.setChecked(self.model.state['rules']['kiriageMangan']);self.kazoe.setChecked(self.model.state['rules']['kazoeYakuman'])
        self._general_baseline=self._general_live()
    def build_roster(self):
        page=self.page();current=QGroupBox('本半庄 · 确认成绩');current_layout=QVBoxLayout(current)
        self.match_record_status=QLabel();self.match_record_status.setWordWrap(True);self.match_record_status.setProperty('i18n_literal',True);current_layout.addWidget(self.match_record_status)
        self.current_match_scores=table(['选手','队伍','当前持点','本半庄积分（pt）']);self.current_match_scores.setRowCount(4)
        self.current_match_scores.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);current_layout.addWidget(self.current_match_scores)
        self.commit_match_button=button('记录本半庄积分',self.confirm_current_match);self.commit_match_button.setObjectName('primary')
        self.current_penalty_button=button('本半庄判罚',self.show_current_penalties)
        current_layout.addLayout(row(self.commit_match_button,self.current_penalty_button,
                                     button('查看已确认成绩',self.show_confirmed_results),1));page.addWidget(current)
        group=QGroupBox('下一半庄 · 从当前赛事名册选择四席');grid=QGridLayout(group)
        self.table_seats=[];self.table_seat_teams=[];self.seat_team_filters=[None]*4
        for index in range(4):
            team, player = QComboBox(), QComboBox()
            for control in (team, player):
                control.setProperty('i18n_literal', True)
                control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            team.setAccessibleName(f'席位 {index+1} 队伍')
            player.setAccessibleName(f'席位 {index+1} 选手')
            self.table_seat_teams.append(team);self.table_seats.append(player)
            team.currentIndexChanged.connect(lambda _, seat=index:self.filter_seat_players(seat))
            team.activated.connect(lambda _, seat=index:self.filter_seat_players(seat))
            player.currentIndexChanged.connect(lambda _, seat=index:self.seat_player_changed(seat))
            grid.setColumnStretch(index, 1)
            grid.addWidget(QLabel(f'席位 {index+1}'),0,index);grid.addWidget(team,1,index);grid.addWidget(player,2,index)
        self.save_seats_button=button('保存四席',self.save_table_seats);self.start_seats_button=button('选择四席并开始新半庄',self.start_selected_match)
        self.match_schedule_button=button('前往淘汰赛程',lambda:self.nav.setCurrentRow(4))
        grid.addLayout(row(self.save_seats_button,self.start_seats_button,self.match_schedule_button,1),3,0,1,4);page.addWidget(group)
        self.team_seat_hint=note('可以直接选择选手，也可以先选队伍来筛选该席选手。选择“全部队伍”可清除筛选。')
        page.addWidget(self.team_seat_hint)
        page.addWidget(note('保存四席用于尚未开打的本场；下一半庄请选好阵容后直接开始，当前半庄须先结束并确认。下拉只连接已保存名册；新增选手后先保存名册。'))
        self.teams_group=QGroupBox('队伍管理');team_layout=QVBoxLayout(self.teams_group)
        team_layout.addWidget(note('先添加并保存队伍，再在选手名册的「所属队伍」列双击选择。队色可点击色块选择，队徽由同队选手共用。'))
        self.teams=table(['队伍编号','队伍名称','计划场次','副标题（上方小字）','显示队名（主行）','历史队伍积分','历史队伍场次','队徽','队伍颜色'],True)
        team_header=self.teams.horizontalHeader()
        team_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        team_header.moveSection(team_header.visualIndex(7),2)
        team_header.moveSection(team_header.visualIndex(8),3)
        self.teams.setItemDelegateForColumn(8,ColorDelegate(self.teams))
        # Keep these fields in the team draft, with editors beside the preview.
        self.teams.setColumnHidden(3,True);self.teams.setColumnHidden(4,True)
        self.teams.setMinimumHeight(190);team_layout.addWidget(self.teams)
        team_layout.addLayout(row(button('添加队伍',self.add_team),
                                   button('选择队徽',lambda:self.choose_logo(self.selected_id(self.teams),team=True)),
                                   button('清除队徽',self.clear_team_logo),
                                  button('保存队伍',self.save_teams),1))
        self.build_team_sidebar_preview(team_layout)
        team_layout.addWidget(note('队伍历史积分和场次留空时汇总成员基数，填写后使用队伍的值。'))
        page.addWidget(self.teams_group)
        roster_group=QGroupBox('选手名册');roster_layout=QVBoxLayout(roster_group)
        roster_layout.addWidget(note('选中一名选手，即可在表格下方编辑照片、颜色和参赛状态。'))
        roster_layout.addWidget(note('历史积分和顺位只填本软件记录之外的部分。有历史场次但顺位未知时可留空。计划场次留空沿用赛事设置，0表示未设总场次。'))
        self.roster=table(['个人昵称','队伍名称','队伍编号','名册状态','历史积分','历史场次','计划场次','历史1位','历史2位','历史3位','历史4位','历史最高分','队徽'],True)
        self.roster.setMinimumHeight(260);roster_layout.addWidget(self.roster)
        self.roster.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.team_assignment_delegate=TeamAssignmentDelegate(self)
        self.build_player_details(roster_layout)
        self.player_action_button = button('删除 / 屏蔽选手', self.manage_selected_player)
        self.follow_team_colors_button = button('全部跟随队色', self.follow_team_colors)
        self.follow_team_colors_button.setToolTip('立即保存当前赛事所有选手的颜色来源')
        self.roster.currentItemChanged.connect(self.update_player_action)
        roster_layout.addLayout(row(button('添加选手',self.add_player),self.player_action_button,
                                    self.follow_team_colors_button,button('保存名册与场次',self.save_roster),
                                    button('重新读取已保存名册',self.load_roster),1))
        page.addWidget(roster_group);page.addStretch()

    def build_player_details(self, layout):
        self.player_details_group = QGroupBox('选手资料')
        details = QGridLayout(self.player_details_group)
        self.detail_name = QLabel()
        self.detail_name.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_name.setProperty('i18n_literal', True)
        self.detail_name.setWordWrap(True)
        self.detail_photo = button('选择照片', self.choose_player_photo)
        self.detail_photo.setIconSize(QSize(48, 44))
        self.detail_clear_photo = button('清除照片', lambda: self.set_player_photo(''))
        self.detail_status = choice(PLAYER_STATUSES)
        self.detail_source = choice([('自定义颜色', False), ('跟随队色', True)])
        self.detail_source_label = QLabel('颜色来源')
        self.detail_color = ColorInput(DEFAULT_COLOR, alpha=False)
        details.addWidget(self.detail_name, 0, 0, 1, 4)
        details.addWidget(QLabel('照片'), 1, 0)
        details.addLayout(row(self.detail_photo, self.detail_clear_photo, 1), 1, 1)
        details.addWidget(QLabel('参赛状态'), 1, 2)
        details.addWidget(self.detail_status, 1, 3)
        details.addWidget(self.detail_source_label, 2, 0)
        details.addWidget(self.detail_source, 2, 1)
        details.addWidget(QLabel('选手颜色'), 2, 2)
        details.addWidget(self.detail_color, 2, 3)
        details.setColumnStretch(1, 1)
        details.setColumnStretch(3, 1)
        layout.addWidget(self.player_details_group)
        self.detail_status.currentIndexChanged.connect(
            lambda _: self.set_player_detail('status', self.detail_status.currentData()))
        self.detail_source.currentIndexChanged.connect(self.change_player_color_source)
        self.detail_color.edit.textChanged.connect(
            lambda value: self.set_player_detail('color', value.strip().lower()))
        self.roster.currentItemChanged.connect(self.refresh_player_details)
        self.roster.itemChanged.connect(self.on_roster_item_changed)

    def original_player_details(self, identity):
        profile = self.roster_original.get(identity, {})
        new = identity not in self.roster_original
        color = QColor(profile.get('color', DEFAULT_COLOR))
        return dict(photo=profile.get('photo', ''), status=profile.get('status', 'active'),
                    color=color.name() if color.isValid() else DEFAULT_COLOR,
                    follow_team_color=profile.get('follow_team_color', new and self.is_team_event()))

    def set_player_detail(self, key, value):
        identity = self.selected_id(self.roster)
        if not identity:
            return
        draft = self.player_detail_drafts.setdefault(identity, {})
        if value == self.original_player_details(identity)[key]:
            draft.pop(key, None)
            if not draft:
                self.player_detail_drafts.pop(identity)
        else:
            draft[key] = value

    def change_player_color_source(self, *_):
        self.set_player_detail('follow_team_color', self.detail_source.currentData())
        self.refresh_player_details()

    def on_roster_item_changed(self, cell):
        if cell.row() == self.roster.currentRow() and cell.column() in (0, 1):
            self.refresh_player_details()

    def refresh_player_details(self, *_):
        from identity_media import load_identity_image

        index = self.roster.currentRow()
        identity = self.selected_id(self.roster)
        self.player_details_group.setEnabled(bool(identity))
        self.detail_name.setText(self.text(self.roster, index, 0))
        team_event = self.is_team_event()
        self.detail_source.setVisible(team_event)
        self.detail_source_label.setVisible(team_event)
        if not identity:
            return
        original = self.original_player_details(identity)
        values = dict(original, **self.player_detail_drafts.get(identity, {}))
        reference = values['photo']
        image = load_identity_image(self.model, reference)
        icon = QIcon()
        if image is not None:
            thumbnail = image.scaled(self.detail_photo.iconSize(), Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
            icon = QIcon(QPixmap.fromImage(thumbnail))
        self.detail_photo.setIcon(icon)
        self.detail_photo.setText(tr('更换照片' if reference else '选择照片'))
        self.detail_clear_photo.setEnabled(bool(reference))
        follow = team_event and values['follow_team_color']
        with QSignalBlocker(self.detail_status), QSignalBlocker(self.detail_source), QSignalBlocker(self.detail_color.edit):
            selected(self.detail_status, values['status'])
            selected(self.detail_source, values['follow_team_color'])
            self.detail_color.setEnabled(not follow)
            color = (player_color(dict(follow_team_color=True, team_id=self.roster_team_id(index), color=original['color']),
                                  self.saved_teams) if follow else values['color'])
            valid = is_rgb_color(color)
            self.detail_color.set_color(color if valid else original['color'])
            if not valid:
                self.detail_color.edit.setText(color)

    def set_player_photo(self, reference):
        self.set_player_detail('photo', reference)
        self.refresh_player_details()

    def choose_player_photo(self, *, filename=None):
        if not self.selected_id(self.roster):
            return
        if filename is None:
            filename, _ = QFileDialog.getOpenFileName(self, tr('选手图片'), '', '图片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if not filename:
            return

        def choose():
            from media_store import import_media_file
            self.set_player_photo(import_media_file(self.model.path, filename, 'photo'))

        self.run(choose, tr('照片已选，保存名册后生效'))

    def build_team_sidebar_preview(self, layout):
        group = QGroupBox('侧栏队伍排名预览')
        self.team_sidebar_group = group
        body = QHBoxLayout(group)
        self.team_sidebar_preview = QLabel()
        self.team_sidebar_preview.setAccessibleName('选中队伍的侧栏排版')
        body.addWidget(self.team_sidebar_preview, 0, Qt.AlignmentFlag.AlignVCenter)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.team_sidebar_subtitle = QLineEdit()
        self.team_sidebar_display_name = QLineEdit()
        self.team_sidebar_subtitle.setPlaceholderText('选填，例如俱乐部名或英文简称')
        self.team_sidebar_display_name.setPlaceholderText('留空使用队伍名称')
        for label, control, column in (
                ('副标题（上方小字）', self.team_sidebar_subtitle, 3),
                ('显示队名（主行）', self.team_sidebar_display_name, 4)):
            control.setMaxLength(80)
            control.textChanged.connect(lambda text, col=column: self.set_team_sidebar_name(col, text))
            form.addRow(label, control)
        form.addRow(note('副标题留空时，侧栏只显示一行队名。'))
        body.addLayout(form, 1)
        layout.addWidget(group)
        self._team_sidebar_preview_key = None
        self.teams.currentItemChanged.connect(self.refresh_team_sidebar_preview)
        self.teams.itemChanged.connect(self.on_team_sidebar_item_changed)
        signals.language_changed.connect(self.refresh_team_sidebar_preview)

    def set_team_sidebar_name(self, column, text):
        cell = self.teams.item(self.teams.currentRow(), column)
        if cell is not None and cell.text() != text:
            cell.setText(text)

    def on_team_sidebar_item_changed(self, cell):
        if cell.row() == self.teams.currentRow() and cell.column() in (1, 3, 4):
            self.refresh_team_sidebar_preview()

    def refresh_team_sidebar_preview(self, *_):
        index = self.teams.currentRow()
        identity = self.selected_id(self.teams)
        cells = [self.teams.item(index, column) for column in (3, 4)]
        valid = bool(self.is_team_event() and identity and all(cell is not None for cell in cells))
        self.team_sidebar_group.setVisible(valid)
        if not valid:
            return
        subtitle, display_name = (cell.text() for cell in cells)
        for control, value in ((self.team_sidebar_subtitle, subtitle),
                               (self.team_sidebar_display_name, display_name)):
            if control.text() != value:
                with QSignalBlocker(control):
                    control.setText(value)
        team_name = self.text(self.teams, index, 1)
        names = (subtitle.strip(), display_name.strip())
        event_name = self.model.event['name']
        settings = self.model.settings
        key = (identity, team_name, names, event_name, get_language(),
               settings.get('overlay_font_family'), settings.get('overlay_font_scale'))
        if self._team_sidebar_preview_key == key:
            return
        from broadcast_artwork import draw_team_sidebar
        from sidebar_paging import SIDEBAR_SIZE, SIDEBAR_FIRST_Y, SIDEBAR_ROW_HEIGHT, SIDEBAR_BOTTOM_PADDING
        from typography import overlay_scope
        preview = SimpleNamespace(state={'broadcast': {'config': {'sidebar_scope': 'current'}}},
                                  event={'name': event_name, 'team_names': {identity: names}})
        height = SIDEBAR_FIRST_Y + SIDEBAR_ROW_HEIGHT + SIDEBAR_BOTTOM_PADDING
        image = QImage(SIDEBAR_SIZE[0], height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            with overlay_scope(settings):
                draw_team_sidebar(painter, preview, rows=[{'id': identity, 'name': team_name}],
                                  highlight_ids={identity})
        finally:
            painter.end()
        self.team_sidebar_preview.setPixmap(QPixmap.fromImage(image))
        self._team_sidebar_preview_key = key

    def load_roster(self):
        if self._saving_all:return
        self.team_logo_drafts={}
        self._load_team_table();self._load_roster_table();self.load_table_seats()
        self.mark_saved('roster');self.mark_saved('teams')
    def resize_identity_columns(self):
        for widget in (self.teams,self.roster):
            widget.resizeColumnsToContents()
    def is_team_event(self):
        return self.model.event['kind']=='team_league'
    @staticmethod
    def team_entries(teams):
        return [(f"{index:02d} · {team['name']}", identity)
                for index, (identity, team) in enumerate(teams.items(), 1)]
    def team_label(self, identity):
        team = self.saved_teams.get(identity)
        return team['name'] if team else tr('未分队')
    def roster_draft_values(self):
        values=form_values(self.roster)[0]
        if not self.is_team_event():
            return values,self.player_detail_drafts,self.team_logo_drafts
        for index,row_values in enumerate(values):
            row_values[1]=self.roster_team_id(index)
        return values,self.player_detail_drafts
    def roster_team_id(self,index):
        cell = self.roster.item(index,1)
        return (cell.data(Qt.ItemDataRole.UserRole) or '') if cell is not None else ''
    def _load_roster_table(self):
        event=self.model.event;roster=self.model.state['broadcast']['roster']
        selected_player=self.selected_id(self.roster)
        self.roster_original={player['id']:copy.deepcopy(player) for player in roster}
        self.player_detail_drafts={}
        self._roster_plans_baseline=copy.deepcopy(event.get('player_plans',{}))
        team_event=self.is_team_event()
        self.follow_team_colors_button.setVisible(team_event)
        self.roster.setItemDelegateForColumn(1,self.team_assignment_delegate if team_event else None)
        self.roster.horizontalHeaderItem(1).setText('所属队伍' if team_event else '队伍名称')
        self.roster.setColumnHidden(2,team_event)
        self.roster.setColumnHidden(12,team_event)
        with QSignalBlocker(self.roster):
            self.roster.setRowCount(len(roster))
            for index,player in enumerate(roster):
                baseline=history_baseline(player)
                games=player.get('games',0)
                counts=baseline['counts']
                values=[player['name'],player.get('team',''),player.get('team_id',''),
                        '已屏蔽' if player.get('hidden') else '可选',player.get('base_points',0),games,
                        self._roster_plans_baseline.get(player['id'],'')]
                values.extend(counts if not games or any(counts) else [''] * 4)
                values.append(baseline['max_score'])
                for column,value in enumerate(values):
                    self.roster.setItem(index,column,item('' if value is None else value,player['id'] if column==0 else None,editable=column!=3))
                if team_event:
                    identity=assigned_team_id(player)
                    self.roster.setItem(index,1,item(self.team_label(identity),identity))
                    self.roster.item(index,2).setText('')
                    self.roster.removeCellWidget(index,12)
                else:
                    self.roster.setCellWidget(index,12,self.logo_button(player))
                    self.roster.setRowHeight(index,max(44,self.roster.rowHeight(index)))
            if roster:
                self.roster.selectRow(next((index for index, player in enumerate(roster) if player['id']==selected_player),0))
        self.roster.resizeColumnsToContents()
        self.refresh_player_details()
        self.update_player_action()
    def _live_team_values(self):
        event = self.model.event
        values = {key:copy.deepcopy(event.get(key,{})) for key in ('team_plans','team_names','team_baselines')}
        values['teams'] = known_teams(self.model.state, event) if self.is_team_event() else {}
        return values
    def _load_team_table(self):
        selected_team=self.selected_id(self.teams)
        self._teams_baseline=self._live_team_values()
        self.saved_teams=self._teams_baseline['teams']
        self.teams_group.setVisible(self.is_team_event())
        with QSignalBlocker(self.teams):
            self.teams.setRowCount(0)
            for identity, team in self.saved_teams.items():
                self.append_team(identity, team['name'])
            if self.saved_teams:
                self.teams.selectRow(next((index for index,identity in enumerate(self.saved_teams)
                                           if identity==selected_team),0))
        self.teams.resizeColumnsToContents()
        self._teams_draft_baseline=self._team_draft()
        self.refresh_team_sidebar_preview()
    def append_team(self,identity,name):
        baseline=self._teams_baseline
        index=self.teams.rowCount()
        self.teams.insertRow(index)
        names=baseline['team_names'].get(identity,['',''])
        base=baseline['team_baselines'].get(identity,{})
        values=[f'{index+1:02d}',name,baseline['team_plans'].get(identity,''),*names,base.get('points',''),base.get('games','')]
        for column,value in enumerate(values):
            self.teams.setItem(index,column,item(value,identity if column==0 else None,editable=column!=0))
        self.teams.setCellWidget(index,7,self.logo_button(self.team_profile(index),team=True))
        self.teams.setItem(index,8,item(self.saved_teams.get(identity,{}).get('color',DEFAULT_COLOR)))
        self.teams.setRowHeight(index,max(44,self.teams.rowHeight(index)))
    def add_team(self):
        identity=uuid.uuid4().hex
        self.append_team(identity,f'队伍 {self.teams.rowCount()+1}')
        self.teams.resizeColumnToContents(7)
        self.teams.resizeColumnToContents(8)
        self.teams.selectRow(self.teams.rowCount()-1)
        return identity
    def team_profile(self,index):
        identity=self.teams.item(index,0).data(Qt.ItemDataRole.UserRole)
        return dict(team_id=identity,team=self.text(self.teams,index,1),
                    team_logo=self.saved_teams.get(identity,{}).get('team_logo',''))
    def _team_draft(self):
        plans={};names={};baselines={};teams={}
        for index in range(self.teams.rowCount()):
            profile=self.team_profile(index)
            identity=profile['team_id'];values=[self.text(self.teams,index,c) for c in range(2,7)]
            teams[identity]=dict(name=profile['team'],team_logo=self.team_logo_drafts.get(identity,profile['team_logo']),
                                 color=self.text(self.teams,index,8).lower())
            if values[0]:plans[identity]=int(values[0])
            if any(values[1:3]):names[identity]=values[1:3]
            if values[3] or values[4]:
                baselines[identity]={'name':profile['team']}
                if values[3]:baselines[identity]['points']=float(values[3])
                if values[4]:baselines[identity]['games']=int(values[4])
        return dict(teams=teams,team_plans=plans,team_names=names,team_baselines=baselines)
    def _refresh_identity_section(self,section):
        """Saving one table must not discard the other table or seat drafts."""
        if self._saving_all:return
        other='teams' if section=='roster' else 'roster'
        preserve_other=other in self.dirty_sections()
        if section=='teams' or not self.is_team_event():self.team_logo_drafts={}
        loaders={'roster':self._load_roster_table,'teams':self._load_team_table}
        loaders[section]()
        if not preserve_other:
            loaders[other]();self.mark_saved(other)
        elif other=='roster':
            self.refresh_roster_team_labels()
    def refresh_roster_team_labels(self):
        if self.is_team_event():
            with QSignalBlocker(self.roster):
                for index in range(self.roster.rowCount()):
                    self.roster.item(index,1).setText(self.team_label(self.roster_team_id(index)))
            self.refresh_player_details()
    def commit_identity_configuration(self,state,event,section,*,refresh=True):
        from event_configuration import save_configuration
        self._saving_identity=True
        try:
            save_configuration(self.model,state,event,clear_history=section=='teams')
        finally:
            self._saving_identity=False
        if refresh:self._refresh_identity_section(section)

    def follow_team_colors(self):
        if not self.is_team_event():return

        def save():
            import tournament
            from state_snapshots import copy_state

            state=copy_state(self.model.state)
            roster=state['broadcast']['roster']
            teams=known_teams(state,self.model.event)
            if any(not player.get('follow_team_color') or player.get('color')!=player_color(player,teams)
                   for player in roster):
                state['broadcast']['teams']=teams
                for player in roster:
                    player['follow_team_color']=True
                tournament.configure(state,{'roster':roster})
                self.commit_identity_configuration(state,self.model.event,'roster',refresh=False)
            # Update just the color baseline so unrelated unsaved edits stay dirty.
            for player in self.model.state['broadcast']['roster']:
                original=self.roster_original.get(player['id'])
                if original is not None:
                    original.update(follow_team_color=True,color=player.get('color',DEFAULT_COLOR))
            for identity,draft in list(self.player_detail_drafts.items()):
                draft.pop('follow_team_color',None);draft.pop('color',None)
                if not draft:self.player_detail_drafts.pop(identity)
            self.refresh_player_details()

        self.run(save,tr('当前赛事所有选手已设为跟随队色'))
    def load_table_seats(self,preserve_draft=False):
        from broadcast_console import seat_choice_entries, update_seat_availability
        team_event = self.is_team_event()
        self.seat_profiles={p['id']:p for p in self.model.state['broadcast']['roster']}
        actual=[p['id'] if not self.seat_profiles[p['id']].get('hidden') else None
                for p in self.model.state['players']]
        seats=[w.currentData() for w in self.table_seats]
        if not preserve_draft or seats==getattr(self,'loaded_table_seats',None):seats=actual
        if not preserve_draft:self.seat_team_filters=[None]*4
        teams = known_teams(self.model.state, self.model.event) if team_event else {}
        profiles = self.seat_profiles.values()
        if team_event:
            profiles = [dict(player,team=teams.get(assigned_team_id(player),{}).get('name',''))
                        for player in profiles]
        self.seat_entries=seat_choice_entries(profiles)
        entries = self.team_entries(teams)
        for index, (team, identity) in enumerate(zip(self.table_seat_teams, seats)):
            player_team = self.seat_player_team(identity)
            filtered = self.seat_team_filters[index]
            if filtered not in teams or (identity is not None and filtered != player_team):
                filtered = None
            self.seat_team_filters[index] = filtered
            blocker = QSignalBlocker(team)
            team.clear();team.addItem(tr('全部队伍'), None)
            for label, team_id in entries:
                team.addItem(label, team_id)
            selected(team, filtered or player_team)
            team.setVisible(team_event)
            del blocker
            self.fill_players_for_seat(index, identity)
        self.team_seat_hint.setVisible(team_event)
        update_seat_availability(self.table_seats)
        if seats==actual:self.mark_saved('seats')
        self.loaded_table_seats=actual
        knockout=self.model.event['kind']=='individual_knockout';self.start_seats_button.setEnabled(not knockout);self.save_seats_button.setEnabled(not knockout)
        self.match_schedule_button.setVisible(knockout)
        self.start_seats_button.setToolTip('淘汰赛请从淘汰赛程开桌' if knockout else '保留本赛事已确认成绩和技术统计，所选四席同时进入新半庄')
    def seat_player_team(self, identity):
        return assigned_team_id(self.seat_profiles.get(identity, {})) or None
    def fill_players_for_seat(self, index, identity):
        from broadcast_console import fill_seat_choice
        team = self.seat_team_filters[index]
        entries = self.seat_entries if team is None else [
            (label, player) for label, player in self.seat_entries if self.seat_player_team(player) == team]
        fill_seat_choice(self.table_seats[index], entries, identity)
    def filter_seat_players(self, index):
        from broadcast_console import update_seat_availability
        team = self.table_seat_teams[index].currentData()
        if team == self.seat_team_filters[index]:
            return
        self.seat_team_filters[index] = team
        self.fill_players_for_seat(index, self.table_seats[index].currentData())
        update_seat_availability(self.table_seats)
    def seat_player_changed(self, index):
        from broadcast_console import update_seat_availability
        identity = self.table_seats[index].currentData()
        if self.is_team_event():
            team = self.table_seat_teams[index]
            with QSignalBlocker(team):
                selected(team, self.seat_team_filters[index] or self.seat_player_team(identity))
        update_seat_availability(self.table_seats)
    @saves_section('seats')
    def save_table_seats(self):
        def save():
            self.model.configure_broadcast({'seats':[w.currentData() for w in self.table_seats]});self.load_table_seats()
        self.run(save,'四席已保存，姓名、照片、队色及队徽均连接赛事名册')
    def start_selected_match(self):
        def start():
            self.model.matches.start_with_seats([w.currentData() for w in self.table_seats]);self.load_table_seats()
        self.run(start,'所选四席的新半庄已开始；本赛事已确认成绩与技术统计保留')
    def refresh_current_match(self):
        import tournament
        from mahjong_values import display_score
        state=self.model.state;matches=state['broadcast']['matches'];finished=state['phase']=='finished'
        record=next((match for match in matches if match['id']==state['id'] and match.get('committed')),None)
        committed=record is not None
        count=sum(bool(match.get('committed')) for match in matches)
        if committed:message='本半庄积分已计入当前赛事；可选择下一半庄四席。'
        elif finished and state['broadcast'].get('result_discarded'):message='本半庄记录已删除；可开始下一半庄，也可重新记录本半庄积分。'
        elif finished:message='本半庄已结束，请核对最终持点并记录积分，再开始下一半庄。'
        else:message='本半庄进行中；完成终局结算后，可在这里记录赛事积分。'
        self.match_record_status.setText(message+f'　当前赛事已确认 {count} 半庄')
        self.commit_match_button.setText('本半庄已记录' if committed else '记录本半庄积分');self.commit_match_button.setEnabled(finished and not committed)
        self.commit_match_button.setToolTip('四名选手的成绩同时计入当前赛事，重复记录不会重复计分' if finished else '请先在导播台完成半庄终局结算')
        self.current_penalty_button.setEnabled(finished and committed)
        self.current_penalty_button.setToolTip('查看或登记本半庄的扣分' if finished and committed else '请先确认该半庄的成绩，再登记判罚')
        entries = net_results(record) if finished and committed else tournament.match_results(state) if finished else []
        results={entry['id']:entry for entry in entries}
        for index,player in enumerate(state['players']):
            score=display_score(state,index,self.model.settings.get('riichi_payment_mode','immediate'));result=results.get(player['id'])
            values=(player['name'],player.get('team',''),f'{score:,}'.replace('-','−'),f"{result['points']:+,.1f}".replace('-','−') if result else '终局后计算')
            for column,value in enumerate(values):
                cell=item(value,editable=False)
                if column>=2:cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.current_match_scores.setItem(index,column,cell)
        self.current_match_scores.setHorizontalHeaderLabels(['选手','队伍','最终持点' if finished else '当前持点',
                                                            '入账积分（pt）' if finished and committed else '本半庄积分（pt）'])
        self.current_match_scores.resizeRowsToContents()
        height=self.current_match_scores.horizontalHeader().sizeHint().height()+sum(self.current_match_scores.rowHeight(index) for index in range(4))+2*self.current_match_scores.frameWidth()+2
        self.current_match_scores.setFixedHeight(height)
    def confirm_current_match(self):
        def confirm():
            self.model.matches.confirm_finished();self.refresh_current_match();self.refresh_reports()
        self.run(confirm,'本半庄积分已计入当前赛事；可以选择四席开始下一半庄')
    def show_confirmed_results(self):
        dialog=ResultsDialog(self)
        try:return dialog.exec()
        finally:dialog.deleteLater()
    def show_current_penalties(self):
        self.show_match_penalties(self.model.state['id'])

    def show_match_penalties(self, match_id, parent=None):
        from penalty_dialog import MatchPenaltyDialog

        try:
            match = self.model.records.penalty_match(match_id)
            dialog = MatchPenaltyDialog(parent or self, match, self.model.settings, model=self.model)
        except (ValueError, OSError) as error:
            set_status_style(self.status, 'error')
            self.status.setText(str(error))
            return
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
    def logo_button(self,profile,team=False):
        from identity_media import load_identity_image
        key=assigned_team_id(profile)
        identity=key if team else profile['id']
        reference=self.team_logo_drafts.get(key,profile.get('team_logo',''))
        control=button('更换图片' if reference else '选择图片',lambda:self.choose_logo(identity,team=team))
        control.setEnabled(bool(key));control.setMaximumWidth(160)
        image=load_identity_image(self.model,reference)
        if image is not None:
            control.setIcon(QIcon(QPixmap.fromImage(image.scaled(
                QSize(48,36),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))))
            control.setIconSize(QSize(48,36))
        control.setToolTip('选择队徽或右键清除。没有有效照片时使用队徽，同队成员共用。')
        control.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        def menu(position):
            actions=QMenu(control);clear=actions.addAction('清除队徽')
            if actions.exec(control.mapToGlobal(position))==clear:self.set_logo_draft(key,'')
        control.customContextMenuRequested.connect(menu)
        return control
    def _logo_roster_profile(self,identity,team=False):
        if team:
            for index in range(self.teams.rowCount()):
                if self.teams.item(index,0).data(Qt.ItemDataRole.UserRole)==identity:
                    return self.team_profile(index)
            return None
        roster=self.model.state['broadcast']['roster']
        for index in range(self.roster.rowCount()):
            cell=self.roster.item(index,0)
            if cell and cell.data(Qt.ItemDataRole.UserRole)==identity:
                if self.is_team_event():
                    return self._logo_roster_profile(self.roster_team_id(index),team=True)
                original=next((p for p in roster if p['id']==identity),dict(id=identity))
                return dict(original,name=cell.text(),team=self.text(self.roster,index,1),team_id=self.text(self.roster,index,2))
        return None
    def choose_logo(self,identity,team=False,filename=None):
        profile=self._logo_roster_profile(identity,team)
        if not profile:
            if team:self.status.setText('请先在队伍列表中选中一支队伍')
            return
        key=assigned_team_id(profile)
        if not key:return
        if filename is None:filename,_=QFileDialog.getOpenFileName(self,'选择队徽','','图片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if not filename:return
        def choose():
            from media_store import manage_photos
            chosen={'name':profile.get('team') or key,'team_logo':str(filename)}
            manage_photos(chosen,self.model.path,strict=True)
            self.set_logo_draft(key,chosen['team_logo'])
        self.run(choose,'队徽已选择，保存队伍后生效' if self.is_team_event() else '队徽已选择，保存名册后生效')
    def clear_team_logo(self):
        identity=self.selected_id(self.teams)
        if identity:self.set_logo_draft(identity,'')
    def set_logo_draft(self,key,reference):
        self.team_logo_drafts[key]=reference
        if self.is_team_event():
            for index in range(self.teams.rowCount()):
                profile=self.team_profile(index)
                if profile['team_id']==key:
                    self.teams.setCellWidget(index,7,self.logo_button(profile,team=True))
                    self.teams.resizeColumnToContents(7)
                    break
        else:
            for index in range(self.roster.rowCount()):
                profile=self._logo_roster_profile(self.roster.item(index,0).data(Qt.ItemDataRole.UserRole))
                if profile and assigned_team_id(profile)==key:self.roster.setCellWidget(index,12,self.logo_button(profile))
    def apply_logo_drafts(self,roster):
        for player in roster:
            key=assigned_team_id(player)
            if key in self.team_logo_drafts:player['team_logo']=self.team_logo_drafts[key]
        return roster
    def text(self,widget,index,column):
        cell=widget.item(index,column);return cell.text().strip() if cell else ''
    def add_player(self):
        index=self.roster.rowCount();self.roster.insertRow(index);identity=uuid.uuid4().hex
        for column,value in enumerate([f'选手{index+1}','','','可选',0,0,'',0,0,0,0,'']):self.roster.setItem(index,column,item(value,identity if column==0 else None,editable=column!=3))
        if self.is_team_event():self.roster.setItem(index,1,item(self.team_label(''),''))
        else:self.roster.setCellWidget(index,12,self.logo_button(dict(id=identity)))
        self.roster.selectRow(index)

    def update_player_action(self, *_):
        identity = self.selected_id(self.roster)
        profile = selected_profile(self.model.state, identity)
        self.player_action_button.setEnabled(bool(identity))
        self.player_action_button.setText(tr('恢复选手' if profile and profile.get('hidden') else '删除 / 屏蔽选手'))

    def manage_selected_player(self):
        from broadcast_console import PLAYER_ACTION_MESSAGES, confirm_player_action

        index = self.roster.currentRow()
        identity = self.selected_id(self.roster)
        if not identity:
            return
        saved = selected_profile(self.model.state, identity)
        try:
            action = confirm_player_action(self, self.model, identity, self.text(self.roster, index, 0),
                                           dirty='roster' in self.dirty_sections())
        except (ValueError, OSError) as error:
            set_status_style(self.status, 'error')
            self.status.setText(str(error))
            return
        if not action:
            return
        if not saved:
            self.player_detail_drafts.pop(identity,None)
            self.roster.removeRow(index)
        set_status_style(self.status, 'success')
        self.status.setText(tr(PLAYER_ACTION_MESSAGES[action]))

    @saves_section('roster')
    def save_roster(self):
        def save():
            import tournament
            originals=self.roster_original;live={p['id']:p for p in self.model.state['broadcast']['roster']};roster=[];plans={}
            team_event=self.is_team_event()
            teams=known_teams(self.model.state,self.model.event) if team_event else {}
            for index in range(self.roster.rowCount()):
                identity=self.roster.item(index,0).data(Qt.ItemDataRole.UserRole);values=[self.text(self.roster,index,c) for c in range(12)]
                if identity in originals and identity not in live:raise ValueError('名册已改变，请重新读取后保存')
                player=copy.deepcopy(live.get(identity,dict(id=identity,color=DEFAULT_COLOR,stats={},status='active')))
                original=originals.get(identity,{})
                draft=dict(name=values[0],base_points=float(values[4]),games=int(values[5]))
                if not team_event:draft.update(team=values[1],team_id=values[2])
                details=self.player_detail_drafts.get(identity,{}).copy()
                original_details=self.original_player_details(identity)
                live_follows=player.get('follow_team_color',False)
                if team_event and identity not in live:
                    details.setdefault('follow_team_color', True)
                if details.get('follow_team_color',original_details['follow_team_color']):
                    details.pop('color',None)
                if 'color' in details:
                    if not is_rgb_color(details['color']):
                        raise ValueError(values[0]+'的颜色无效，请填写如 #377e72')
                    if live_follows and not original_details['follow_team_color']:
                        raise ValueError('选手颜色来源已在其他窗口修改，请重新读取后保存')
                draft.update(details)
                defaults=dict(original_details,team='',team_id='',base_points=0,games=0)
                for key,value in draft.items():
                    old=original.get(key,defaults.get(key))
                    if key=='color' and original_details['follow_team_color'] and live_follows:
                        # The live color may have changed when the team color was saved.
                        old=player.get(key,old)
                    if identity not in live or value!=old:
                        if identity in live and live[identity].get(key,old)!=old and live[identity].get(key,old)!=value:raise ValueError('同一名册字段已在其他窗口修改，请重新读取后保存')
                        player[key]=value
                if team_event:
                    previous=assigned_team_id(original);current=assigned_team_id(player);chosen=self.roster_team_id(index)
                    if chosen!=previous and current not in (previous,chosen):
                        raise ValueError('选手所属队伍已在其他窗口修改，请重新读取后保存')
                    assign_team(player, chosen if chosen!=previous else current, teams)
                else:self.apply_logo_drafts([player])
                counts=[int(v) if v else 0 for v in values[7:11]];baseline=dict(counts=counts,max_score=float(values[11]) if values[11] else None)
                old_baseline=history_baseline(original)
                if baseline!=old_baseline:
                    if history_baseline(player) not in (old_baseline,baseline):raise ValueError('历史基数已在其他窗口修改，请重新读取后保存')
                    player['result_baseline']=dict(player.get('result_baseline',{}),**baseline)
                reports.validate_baseline(player);roster.append(player)
                if values[6]:plans[identity]=int(values[6])
            edited={p['id'] for p in roster}
            roster += [p for identity,p in live.items() if identity not in edited and identity not in originals]
            from settings_drafts import merge_draft
            baseline=self._roster_plans_baseline
            merged,_,conflicts=merge_draft(baseline,plans,baseline,self.model.event.get('player_plans',{}))
            if conflicts:raise ValueError('计划场次已在其他窗口修改，请重新读取后保存')
            candidate=copy.deepcopy(self.model.event);candidate['player_plans']=merged
            state=copy.deepcopy(self.model.state)
            if team_event:state['broadcast']['teams']=teams
            tournament.configure(state,{'roster':roster});validate_event(candidate,state)
            self.commit_identity_configuration(state,candidate,'roster')
        self.run(save)
    @saves_section('teams')
    def save_teams(self):
        def save():
            import tournament
            if not self.is_team_event():return
            roster=copy.deepcopy(self.model.state['broadcast']['roster'])
            from settings_drafts import merge_draft
            live=self._live_team_values()
            values,_,conflicts=merge_draft(self._teams_draft_baseline,self._team_draft(),self._teams_baseline,live)
            if conflicts:raise ValueError('同一队伍设置已在其他窗口修改，请重新读取后保存')
            teams=values.pop('teams')
            for player in roster:
                assign_team(player, assigned_team_id(player), teams)
            candidate=copy.deepcopy(self.model.event);candidate.update(values,updated_at=now())
            state=copy.deepcopy(self.model.state);state['broadcast']['teams']=teams
            tournament.configure(state,{'roster':roster,'team_baselines':values['team_baselines']});validate_event(candidate,state)
            self.commit_identity_configuration(state,candidate,'teams')
        self.run(save)
    def build_reports(self):
        page=self.page();self.report_mode=choice([('个人积分榜','individual'),('团队积分榜','team')]);self.report_mode.activated.connect(self.refresh_reports)
        self.report_preview_button=button('放大彩色预览',self.preview_report);self.report_preview_button.setObjectName('primary')
        page.addLayout(row(self.report_mode,self.report_preview_button,button('调整显示列',self.edit_columns),button('刷新榜单',lambda:self.refresh_reports(force=True)),1))
        self.report_preset=choice([('赛事成绩','results'),('ML 完整数据','ml'),('技术统计','technical')]);page.addLayout(row('个人榜预设',self.report_preset,button('应用预设',self.apply_report_preset),1))
        self.report_table=table([]);self.report_table.setProperty('i18n_literal',True);self.report_table.setMinimumHeight(300);self.report_table.horizontalHeader().setSectionsMovable(True);self.report_table.horizontalHeader().sectionMoved.connect(self.column_moved);self.report_table.horizontalHeader().sectionDoubleClicked.connect(self.rename_header);page.addWidget(self.report_table,1)
        self.report_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.report_page = 1
        self.report_previous = button('上一页', lambda: self.set_report_page(self.report_page-1))
        self.report_next = button('下一页', lambda: self.set_report_page(self.report_page+1))
        self.report_page_label = QLabel()
        self.report_page_label.setProperty('i18n_literal', True)
        page.addLayout(row(button('导出完整榜单图片',self.export_png),button('导出彩色 Excel',self.export_xlsx),button('播出当前榜',self.show_report),1))
        page.addLayout(row(self.report_previous, self.report_page_label, self.report_next, 1))
        page.addLayout(row(button('补录其他桌成绩',self.manual_result),button('成绩记录与更正',self.show_confirmed_results),
            button('查看历史成绩', lambda: self.show_event_history()),1))
        self.report_notes=QLabel();self.report_notes.setWordWrap(True);self.report_notes.setProperty('i18n_literal',True);self.report_notes.setObjectName('muted');page.addWidget(self.report_notes)
        page.addWidget(note('放大彩色预览支持缩放和翻页。拖动表头调整顺序，双击表头修改名称。列设置和表头名称随赛事保存。团队 Excel 包含两张榜，图片导出为当前榜单的整张 PNG。'));page.addStretch()
    def refresh_reports(self,*_,force=False):
        event = self.model.event
        if not event:return
        is_team=self.is_team_event()
        mode=(self.report_mode.currentData() or 'individual') if is_team else 'individual'
        if self.report_mode.count() != (2 if is_team else 1):
            with QSignalBlocker(self.report_mode):
                self.report_mode.clear()
                self.report_mode.addItem('个人积分榜','individual')
                if is_team:self.report_mode.addItem('团队积分榜','team')
                selected(self.report_mode,mode)

        state = self.model.state
        broadcast = state['broadcast']
        source = (event, broadcast['roster'], broadcast.get('teams',{}), tuple(reports.confirmed_matches(state)))
        if source != self._report_source:
            # Archive records are immutable and shared by live table snapshots.
            self._report_source = (*copy.deepcopy(source[:3]), source[3])
            self._reports.clear()
        if force or mode not in self._reports:
            self._reports[mode] = reports.report(state,event,mode)
        data = self._reports[mode]
        if data is not self.report_data:
            if self.report_data and (mode != self.report_data['mode']
                    or event['id'] != self.report_data['event']['id']):
                self.report_page = 1
            self.populate_report_table(data)
            self.report_data = data
        self.update_report_output()
    def populate_report_table(self,data):
        header=self.report_table.horizontalHeader()
        self.report_table.setUpdatesEnabled(False)
        try:
            with QSignalBlocker(header):
                columns=data['columns']
                self.report_table.setColumnCount(len(columns))
                self.report_table.setHorizontalHeaderLabels([reports.header(key,data['mode'],data['event']) for key in columns])
                for index in range(header.count()):
                    visual=header.visualIndex(index)
                    if visual!=index:header.moveSection(visual,index)
                self.report_table.setRowCount(len(data['rows']))
                for index,entry in enumerate(data['rows']):
                    for column,key in enumerate(columns):
                        cell=item(reports.display(key,entry.get(key)),editable=False)
                        if key not in ('name','team'):cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        reports.style_table_item(cell,data,entry,key,index)
                        self.report_table.setItem(index,column,cell)
                self.report_table.resizeColumnsToContents()
        finally:
            self.report_table.setUpdatesEnabled(True)
        self.report_notes.setText('\n'.join(data['notes'][:6]))
    def set_report_page(self, page):
        self.report_page = page
        self.update_report_output()

    def update_report_output(self,*_):
        if self.report_data is None:return
        page_size = reports.REPORT_PAGE_SIZE
        pages = max(1, (len(self.report_data['rows']) + page_size - 1) // page_size)
        self.report_page = min(pages, max(1, self.report_page))
        self.report_previous.setEnabled(self.report_page > 1)
        self.report_next.setEnabled(self.report_page < pages)
        self.report_page_label.setText(tr('第 {page} / {pages} 页').format(page=self.report_page, pages=pages))
        if getattr(self,'output_mode',None)=='report' and getattr(self,'output',None) and self.output.isVisible():
            self.render_report_output()
        if getattr(self,'report_preview',None) and self.report_preview.isVisible():self.report_preview.set_report(self.report_data,self.report_page)
    def render_report_output(self):
        page=self.report_page
        previous=self._output_report
        if previous and previous[0] is self.report_data and previous[1]==page and self.output.isVisible():return
        size = reports.REPORT_PAGE_SIZE
        self.output_window().set_image(reports.render_report(self.report_data, (page-1)*size, size))
        self._output_report=(self.report_data,page)
    def preview_report(self):
        from ranking_preview import RankingPreviewWindow
        self.refresh_reports()
        if not getattr(self,'report_preview',None):
            self.report_preview=RankingPreviewWindow(self)
            if getattr(self.main,'language_controller',None):self.main.language_controller.track(self.report_preview)
            self.report_preview.mode_requested.connect(self.preview_report_mode)
            self.report_preview.page_requested.connect(self.set_report_page)
            self.report_preview.refresh_requested.connect(lambda:self.refresh_reports(force=True))
            self.report_preview.export_requested.connect(self.export_png)
        self.report_preview.set_report(self.report_data,self.report_page)
        self.report_preview.show();self.report_preview.raise_();self.report_preview.activateWindow()
    def preview_report_mode(self,mode):
        selected(self.report_mode,mode)
        self.report_page = 1
        self.refresh_reports()
    def column_moved(self,*_):
        if self.loading:return
        header=self.report_table.horizontalHeader();old=self.report_data['columns'];values=[old[header.logicalIndex(i)] for i in range(header.count())]
        mapping=copy.deepcopy(self.model.event.get('report_columns',{}));mapping[self.report_mode.currentData()]=values
        self.run(lambda:self.store.update(report_columns=mapping),'列顺序已保存；导出使用相同顺序')
    def edit_columns(self):
        dialog=ColumnDialog(self,self.model.event,self.report_mode.currentData())
        try:return dialog.exec()
        finally:dialog.deleteLater()
    def apply_report_preset(self):
        mapping=copy.deepcopy(self.model.event.get('report_columns',{}));mapping['individual']=reports.preset_columns(self.model.event['kind'],self.report_preset.currentData())
        selected(self.report_mode,'individual');self.run(lambda:(self.store.update(report_columns=mapping),self.refresh_reports()),'预设已应用；仍可自由改列顺序、显隐和表头名称')
    def rename_header(self,index):
        key=self.report_data['columns'][index];mode=self.report_mode.currentData()
        value,ok=QInputDialog.getText(self,'修改表头名称','仅修改显示名称，计算方式保持不变',text=reports.header(key,mode,self.model.event))
        if ok:
            labels=copy.deepcopy(self.model.event.get('report_headers',{}));labels.setdefault(mode,{})[key]=value.strip()
            self.run(lambda:(self.store.update(report_headers=labels),self.refresh_reports()),'表头名称已保存，界面和导出同步使用新名称')
    def export_dir(self):
        directory=self.model.path/'exports';directory.mkdir(exist_ok=True);return directory
    def export_xlsx(self):
        path,_=QFileDialog.getSaveFileName(self,'导出彩色 Excel',str(self.export_dir()/(safe_name(self.model.event['name'])+'-积分榜.xlsx')),'Excel (*.xlsx)')
        if path:self.run(lambda:reports.export_xlsx(self.model.state,self.model.event,path),'彩色 Excel 已导出')
    def export_png(self):
        path,_=QFileDialog.getSaveFileName(self,'导出榜单图片',str(self.export_dir()/(safe_name(self.model.event['name'])+'-'+self.report_mode.currentData()+'.png')),'PNG (*.png)')
        if path:self.run(lambda:reports.export_png(reports.report(self.model.state,self.model.event,self.report_mode.currentData()),path),'完整榜单图片已导出')
    def output_window(self):
        if not getattr(self,'output',None):self.output=ReportOutputWindow(self.main)
        return self.output
    def show_report(self):
        self.refresh_reports()
        if getattr(self,'output_mode',None)!='report':self._output_report=None
        self.output_mode='report';self.render_report_output()
    def manual_result(self,node=None):
        def enter():
            if self.model.event['kind']=='individual_knockout' and node is None:raise ValueError('淘汰赛请在“淘汰赛程”选中桌，再补录该桌成绩')
            dialog=ResultEntryDialog(self,node=node)
            if dialog.exec()==QDialog.DialogCode.Accepted:self.refresh_reports();self.refresh_bracket()
        self.run(enter,'成绩记录已更新')
    def build_bracket(self):
        page=self.page();self.bracket_note=note('四人桌位于下方，晋级桌向上排列。先完成本桌全部半庄，再预览并确认晋级。');page.addWidget(self.bracket_note)
        self.node_games=integer(1,1000);self.node_games.setValue(1);self.node_advance=choice([('4进1',1),('4进2',2),('4进3',3)]);selected(self.node_advance,2)
        page.addLayout(row('每桌半庄',self.node_games,'晋级方式',self.node_advance,button('按名册顺序生成赛程',self.generate_bracket),1))
        self.nodes=table(['轮次','桌名','四个席位 / 来源','已确认/计划','状态']);self.nodes.setMinimumHeight(285);page.addWidget(self.nodes)
        page.addLayout(row(button('添加四人桌',lambda:self.edit_node(False)),button('编辑选中桌',lambda:self.edit_node(True)),button('移除未开赛桌',self.remove_node),1))
        page.addLayout(row(button('载入四席并开始本桌',self.start_node),button('补录选中桌成绩',self.manual_node_result),button('预览 / 确认晋级',self.advance_node),1))
        page.addLayout(row(button('查看晋级图',self.show_bracket),button('导出晋级图 PNG',self.export_bracket),button('导出赛程表 Excel',self.export_schedule),1));page.addStretch()
    def refresh_bracket(self):
        event=self.model.event;nodes=event['bracket']['nodes'];roster={p['id']:p['name'] for p in self.model.state['broadcast']['roster']}
        self.nodes.setRowCount(len(nodes));self.bracket_note.setText('四人桌 → 上方晋级桌；完成并确认全部半庄后，才能确认本桌晋级。' if event['kind']=='individual_knockout' else '当前是积分赛事。新建“个人淘汰赛”可使用本页；已保存的赛程保留。')
        for index,node in enumerate(nodes):
            seats=[]
            for identity,source in zip(schedule.resolve_sources(event,node),node['sources']):
                seats.append(roster.get(identity) or schedule.source_label(event,source,roster))
            count=len(schedule.node_matches(self.model.state,node['id']));status={'pending':'待开赛','running':'进行中','complete':'已确认晋级'}[node['status']]
            if node.get('result_signature') and node['result_signature']!=schedule.signature(self.model.state,node):status='成绩更正 · 待核查'
            for column,value in enumerate((node['round'],node['name'],' / '.join(seats),f'{count}/{node["planned_games"]}',status)):
                self.nodes.setItem(index,column,item(value,node['id'] if column==0 else None,False))
        if getattr(self,'output_mode',None)=='bracket' and getattr(self,'output',None) and self.output.isVisible():self.output.set_image(render_bracket(self.model.state,event))
    def require_knockout(self):
        if self.model.event['kind']!='individual_knockout':raise ValueError('请在个人淘汰赛事中编排晋级桌')
    def generate_bracket(self):
        def generate():
            self.require_knockout()
            if any(n['status']!='pending' or schedule.node_matches(self.model.state,n['id']) for n in self.model.event['bracket']['nodes']):raise ValueError('赛程已有开赛记录，不能重新生成；可添加未开赛四人桌')
            roster=[p for p in self.model.state['broadcast']['roster'] if p.get('status','active')=='active']
            bracket=schedule.generate(roster,self.node_games.value(),self.node_advance.currentData());self.store.update(bracket=bracket);self.refresh_bracket()
        self.run(generate,'四人桌已生成，选中一桌即可开始')
    def edit_node(self,existing):
        def edit():
            self.require_knockout();identity=self.selected_id(self.nodes) if existing else None
            node=next((n for n in self.model.event['bracket']['nodes'] if n['id']==identity),None)
            if existing and not node:raise ValueError('请先选中一桌')
            dialog=NodeDialog(self,self.model.state,self.model.event,node)
            if dialog.exec()!=QDialog.DialogCode.Accepted:return
            schedule.save_node(self.store,dialog.value(),dialog.additional_players);self.refresh_bracket()
        self.run(edit)
    def remove_node(self):
        def remove():
            identity=self.selected_id(self.nodes);bracket=copy.deepcopy(self.model.event['bracket']);node=next((n for n in bracket['nodes'] if n['id']==identity),None)
            if not node:raise ValueError('请先选中一桌')
            if node['status']!='pending' or schedule.node_matches(self.model.state,identity) or any(s.get('node')==identity for n in bracket['nodes'] for s in n['sources']):raise ValueError('只能移除未开赛、无成绩且未被下游引用的桌')
            bracket['nodes']=[n for n in bracket['nodes'] if n['id']!=identity];self.store.update(bracket=bracket);self.refresh_bracket()
        self.run(remove)
    def start_node(self):
        def start():
            self.require_knockout();identity=self.selected_id(self.nodes)
            if not identity:raise ValueError('请先选中一桌')
            schedule.start_node(self.store,identity);self.refresh_bracket()
        self.run(start,'四席已载入，新半庄已开始。回导播台录入；终局后记录积分。')
    def manual_node_result(self):
        identity=self.selected_id(self.nodes);node=next((n for n in self.model.event['bracket']['nodes'] if n['id']==identity),None)
        if not node:self.status.setText('请先选中一桌');return
        if None in schedule.resolve_sources(self.model.event,node):self.status.setText('请先确认上游晋级');return
        self.manual_result(node)
    def advance_node(self):
        def advance():
            identity=self.selected_id(self.nodes)
            if not identity:raise ValueError('请先选中一桌')
            rows=schedule.preview_advancement(self.model.state,self.model.event,identity)
            dialog=QDialog(self);dialog.setWindowTitle('核对晋级名单');layout=QVBoxLayout(dialog);layout.addWidget(QLabel('本桌晋级名单（本桌累计积分排序）'))
            for entry in rows:layout.addWidget(QLabel(f"{entry['rank']}  {entry['name']}   {entry['points']:+.1f} pt"))
            layout.addWidget(note('同分时按达到积分的先后排序，同一半庄按席位顺序。确认后，名单填入上方晋级桌。'))
            layout.addLayout(row(button('返回核查',dialog.reject),button('确认晋级',dialog.accept)))
            if dialog.exec()==QDialog.DialogCode.Accepted:schedule.confirm_advancement(self.store,identity);self.refresh_bracket()
        self.run(advance)
    def show_bracket(self):
        self.run(lambda:(self.output_window().set_image(render_bracket(self.model.state,self.model.event)),setattr(self,'output_mode','bracket')),'晋级图已打开，可单独捕获')
    def export_bracket(self):
        path,_=QFileDialog.getSaveFileName(self,'导出晋级图',str(self.export_dir()/(safe_name(self.model.event['name'])+'-晋级图.png')),'PNG (*.png)')
        if path:
            def save():
                image=render_bracket(self.model.state,self.model.event)
                if not image.save(path,'PNG'):raise OSError('无法写入晋级图')
            self.run(save,'晋级图已保存')
    def export_schedule(self):
        path,_=QFileDialog.getSaveFileName(self,'导出赛程表',str(self.export_dir()/(safe_name(self.model.event['name'])+'-赛程.xlsx')),'Excel (*.xlsx)')
        if path:self.run(lambda:reports.export_schedule_xlsx(self.model.state,self.model.event,path),'彩色赛程表已保存')
    def build_backup(self):
        page=self.page()
        page.addWidget(note('练习计分时，先打开演示赛事并返回导播台。在「计分」选择和牌者、和牌方式与番符，荣和还要选择放铳者。核对预览后点「提交结算」。本局结束后点「下一局」，半庄结束后在「半庄管理」记录积分。'))
        page.addWidget(note('要接入 OBS，请在「赛事设置 → 画面与设备 → 播出窗口」点击「导出 OBS 窗口捕获场景」，再在 OBS 的「场景集合 → 导入」选择导出的文件。'))
        page.addWidget(button('导出完整赛事 JSON（含照片）',self.backup_event));page.addWidget(button('导入为另一个独立赛事',self.import_event))
        page.addWidget(button('前往赛事设置文件',lambda:self.select_settings_category('rules')))
        page.addWidget(note('自动保存目录为 data/events/赛事编号/。完整赛事 JSON 包含规则、名册、比赛进度、榜单配置与淘汰赛程，可用于跨电脑搬移。'))
        page.addWidget(note('完整备份会嵌入已选用的照片、队徽、标题卡、背景和音乐。分享备份前请核对个人信息和素材授权。只公布成绩时可导出 Excel 或图片。'))
        self.location=QLabel();self.location.setWordWrap(True);self.location.setProperty('i18n_literal',True);page.addWidget(self.location);page.addStretch()
    def backup_event(self):
        path,_=QFileDialog.getSaveFileName(self,'备份完整赛事',str(self.export_dir()/(safe_name(self.model.event['name'])+'.json')),'JSON (*.json)')
        if path:self.run(lambda:self.model.export_data(path),'完整赛事已导出，包含照片与赛事设置')
    def ensure_event_settings(self):
        factory=getattr(self.main,'broadcast_panel',None)
        if self.broadcast_settings is not None or factory is None:
            return
        panel=factory()
        self.broadcast_settings=panel
        self.event_settings_host.layout().removeWidget(self.general_page)
        panel.attach_event_settings(self,self.event_settings_host,self.general_page)
        self.output_host=QWidget();layout=QVBoxLayout(self.output_host);layout.setContentsMargins(0,0,0,0)
        panel.categories['output']=self.output_host
        panel.tabs.addTab(self.output_host,'画面与设备')
        panel.tabs.currentChanged.connect(self.load_output_editor)
        self.track_settings('broadcast_settings',lambda:bool(panel.dirty_sections()),panel.save_changes)
    def load_output_editor(self,*_):
        if self.output_editor is not None or self.broadcast_settings.tabs.currentWidget() is not self.output_host:
            return
        from settings_dialog import SettingsDialog
        editor=SettingsDialog(self.main,embedded_in=self.output_host)
        self.output_editor=editor
        self.output_host.layout().addWidget(editor)
        self.track_settings('output_settings',lambda:bool(editor.dirty_sections()),self.save_output_settings)
    def save_output_settings(self):
        if self.output_editor.save_changes():
            return True
        self.status.setText(self.output_editor.error.text())
        set_status_style(self.status,'error')
        return False
    def select_settings_category(self,key):
        self.nav.setCurrentRow(1)
        self.ensure_event_settings()
        if self.broadcast_settings is not None:
            self.broadcast_settings.select_category(key)
    def select_page(self,index):
        self.pages.setCurrentIndex(index)
        if index==0:self.refresh_library()
        if index==1:self.ensure_event_settings()
        if index==2:self.refresh_current_match()
        if index==3:self.refresh_reports()
        if index==4:self.refresh_bracket()
    def refresh_heading(self):
        event=self.model.event;self.heading.setText(event['name']+'   ·   '+KINDS[event['kind']]);self.location.setText('当前赛事目录：\n'+str(self.model.path))
        demo=event.get('demo_suite',{});self.demo_description.setVisible(bool(demo));self.demo_description.setText(('演示赛事 · 成绩均为模拟\n'+demo.get('description','')) if demo else '')
    def reload_all(self):
        self.loaded_id=self.model.event['id'];self.refresh_heading();self.refresh_library();self.load_general();self.load_roster();self.refresh_current_match();self.refresh_reports();self.refresh_bracket()
        if self.broadcast_settings is not None:self.broadcast_settings.reload()
        if self.output_editor is not None:self.output_editor.reload_drafts()
        self.mark_saved()
    def on_model_changed(self,before,after,kind):
        if kind in ('import','event_settings'):self.reload_all()
        else:
            old, new = before['broadcast'], after['broadcast']
            roster_changed = (old['roster'] != new['roster'] or old.get('teams') != new.get('teams')
                              or old['config'].get('team_baselines') != new['config'].get('team_baselines'))
            match_started=kind in ('new_match','start_match')
            seats_changed=[p['id'] for p in before['players']] != [p['id'] for p in after['players']]
            if roster_changed or match_started or seats_changed:
                self.load_table_seats(preserve_draft=not match_started)
            if roster_changed and not self._saving_all and not getattr(self,'_saving_identity',False):
                self.refresh_identity_from_model()
            self.refresh_current_match()
            if (self.isVisible() and self.pages.currentIndex()==3) or (getattr(self,'report_preview',None) and self.report_preview.isVisible()):self.refresh_reports()
            if self.isVisible():
                if self.pages.currentIndex()==4:self.refresh_bracket()
        if getattr(self,'output',None) and self.output.isVisible():
            if self.output_mode=='report':self.refresh_reports()
            elif self.output_mode=='bracket':self.refresh_bracket()
    def refresh_identity_from_model(self):
        dirty=self.dirty_sections()
        if 'teams' not in dirty:
            self._load_team_table();self.mark_saved('teams')
        elif self.is_team_event():
            self.saved_teams=known_teams(self.model.state,self.model.event)
        if 'roster' not in dirty:
            self._load_roster_table();self.mark_saved('roster')
        else:
            self.refresh_roster_team_labels()
    def on_display_changed(self):
        signature=tuple(self.model.settings.get(key) for key in ('console_theme','console_font_family','settings_font_size'))
        if signature!=getattr(self,'_theme_signature',None):
            self._theme_signature=signature;apply_theme(self,self.model.settings);self.resize_identity_columns()
        if self.loaded_id!=self.model.event['id']:self.reload_all()
        else:
            self.refresh_heading()
            self.refresh_current_match()
            self.refresh_team_sidebar_preview()
            if not self._saving_all and 'general' not in self.dirty_sections():self.load_general();self.mark_saved('general')
            if (self.isVisible() and self.pages.currentIndex()==3) or (getattr(self,'report_preview',None) and self.report_preview.isVisible()):self.refresh_reports()
    def closeEvent(self,event):
        super().closeEvent(event)
        if event.isAccepted() and getattr(self,'report_preview',None):self.report_preview.close()

