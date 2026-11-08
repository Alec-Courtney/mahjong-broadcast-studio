"""Inspect and maintain the penalty ledger of one confirmed half-game."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import QDateTimeEdit, QDialog, QFormLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QVBoxLayout

from desk_theme import apply_theme
from event_penalties import MAX_NOTE, MAX_PENALTY_PT, MAX_REASON, penalty_targets, penalty_totals
from i18n import tr
from ranking_order import match_seats
from ui_widgets import button, choice, decimal, item, note, row, selected, table


class PenaltyEditorDialog(QDialog):
    def __init__(self, parent, model, match, entry=None):
        super().__init__(parent)
        self.model = model
        self.match = match
        self.entry = entry
        self.setProperty('settingsWindow', True)
        self.setWindowTitle('更正判罚' if entry else '登记判罚')
        self.setMinimumWidth(490)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        scopes = [('选手判罚', 'player')]
        if model.event['kind'] == 'team_league' and penalty_targets(match, 'team'):
            scopes.append(('队伍判罚', 'team'))
        self.scope = choice(scopes)
        self.target = choice([])
        self.target.setProperty('i18n_literal', True)
        if len(scopes) > 1:
            form.addRow('判罚口径', self.scope)
        form.addRow('被判对象', self.target)
        self.points = decimal(0, MAX_PENALTY_PT, 1)
        self.points.setSuffix(' pt')
        form.addRow('扣除 pt（填正数）', self.points)
        self.time = QDateTimeEdit(QDateTime.currentDateTime())
        self.time.setCalendarPopup(True)
        self.time.setDisplayFormat('yyyy-MM-dd HH:mm:ss')
        form.addRow('判罚时间', self.time)
        self.reason = QLineEdit()
        self.reason.setMaxLength(MAX_REASON)
        self.remark = QLineEdit()
        self.remark.setMaxLength(MAX_NOTE)
        form.addRow('判罚原因', self.reason)
        form.addRow('备注（选填）', self.remark)
        layout.addWidget(note('选手判罚扣个人累计 pt，并反映在该半庄所属队伍的积分。队伍判罚直接扣队伍累计 pt。'
                              if model.event['kind'] == 'team_league' else '判罚从选手的累计 pt 扣除。'))
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setObjectName('error')
        layout.addWidget(self.error)
        layout.addLayout(row(1, button('取消', self.reject), button('保存判罚', self.save)))
        if entry:
            selected(self.scope, entry['scope'])
            self.points.setValue(entry['points'])
            self.time.setDateTime(QDateTime.fromString(entry['time'], Qt.DateFormat.ISODate).toLocalTime())
            self.reason.setText(entry['reason'])
            self.remark.setText(entry.get('note', ''))
        self.refresh_targets(entry['target'] if entry else None)
        self.scope.currentIndexChanged.connect(lambda _: self.refresh_targets())
        apply_theme(self, model.settings)

    def refresh_targets(self, identity=None):
        scope = self.scope.currentData()
        self.target.clear()
        self.target.addItem(tr('请选择被判对象'), None)
        targets = penalty_targets(self.match, scope)
        seats = match_seats(self.match) if scope == 'player' else []
        ordered = dict.fromkeys([*seats, *targets])
        for target in ordered:
            if target not in targets:
                continue
            name = targets[target]
            if target in seats:
                name = f"{tr('席位')} {seats.index(target)+1} · {name}"
            self.target.addItem(name, target)
        selected(self.target, identity)

    def save(self):
        try:
            self.parentWidget().check_context()
            self.model.records.save_penalty(
                self.match['id'], scope=self.scope.currentData(), target=self.target.currentData(),
                points=self.points.value(), time=self.time.dateTime().toPython().astimezone().isoformat(timespec='seconds'),
                reason=self.reason.text().strip(), note=self.remark.text().strip(),
                penalty_id=self.entry['id'] if self.entry else None)
        except (ValueError, OSError) as error:
            self.error.setText(tr(str(error)))
            return
        self.accept()


class MatchPenaltyDialog(QDialog):
    def __init__(self, parent, match, settings, *, model=None):
        super().__init__(parent)
        self.model = model
        self.context = (model.path, model.event['id']) if model else None
        self.match = match
        self.setProperty('settingsWindow', True)
        self.setWindowTitle('半庄判罚记录')
        self.resize(930, 430)
        layout = QVBoxLayout(self)
        self.summary = note('')
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        self.table = table(['判罚时间', '被判对象', '扣除 pt', '判罚原因', '备注', '状态'])
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in (3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 178)
        self.table.setColumnWidth(1, 155)
        layout.addWidget(self.table, 1)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setObjectName('error')
        layout.addWidget(self.error)
        actions = []
        if model:
            self.add_button = button('登记判罚', lambda: self.edit(False))
            self.edit_button = button('更正判罚', lambda: self.edit(True))
            self.void_button = button('撤销判罚', self.void)
            self.table.currentCellChanged.connect(self.update_actions)
            actions.extend([self.add_button, self.edit_button, self.void_button])
        layout.addLayout(row(*actions, 1, button('关闭', self.close)))
        self.reload()
        apply_theme(self, settings)

    def check_context(self):
        if self.context != (self.model.path, (self.model.event or {}).get('id')):
            raise ValueError('赛事已切换，请重新打开该半庄')

    def current_entry(self):
        index = self.table.currentRow()
        entries = self.match.get('penalties', [])
        return entries[index] if 0 <= index < len(entries) else None

    def update_actions(self, *_):
        entry = self.current_entry()
        enabled = bool(entry and not entry.get('voided_at'))
        self.edit_button.setEnabled(enabled)
        self.void_button.setEnabled(enabled)

    def reload(self):
        if self.model:
            self.check_context()
            self.match = self.model.records.penalty_match(self.match['id'])
        entries = self.match.get('penalties', [])
        personal, teams = penalty_totals(self.match)
        total = sum(personal.values()) + sum(teams.values())
        title = self.match.get('title') or tr('半庄')
        self.summary.setText(f"{title} · {tr('生效扣分')} {total:g} pt")
        self.table.setRowCount(len(entries))
        for index, entry in enumerate(entries):
            name = penalty_targets(self.match, entry['scope'])[entry['target']]
            scope = tr('队伍判罚' if entry['scope'] == 'team' else '选手判罚')
            values = (datetime.fromisoformat(entry['time']).astimezone().strftime('%Y-%m-%d %H:%M:%S'),
                      f'{name} · {scope}', f"−{entry['points']:.1f}", entry['reason'],
                      entry.get('note', ''), tr('已撤销' if entry.get('voided_at') else '生效'))
            for column, value in enumerate(values):
                cell = item(value, editable=False)
                cell.setToolTip(value)
                self.table.setItem(index, column, cell)
        if self.model:
            self.update_actions()

    def edit(self, existing):
        try:
            self.reload()
            entry = self.current_entry() if existing else None
            if existing and (entry is None or entry.get('voided_at')):
                return
            dialog = PenaltyEditorDialog(self, self.model, self.match, entry)
            try:
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    self.reload()
                    self.error.clear()
            finally:
                dialog.deleteLater()
        except (ValueError, OSError) as error:
            self.error.setText(tr(str(error)))

    def void(self):
        entry = self.current_entry()
        if not entry or entry.get('voided_at'):
            return
        answer = QMessageBox.question(self, tr('撤销判罚'), tr('确认撤销这笔扣分？原判罚记录会保留为已撤销。'),
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.check_context()
            self.model.records.void_penalty(self.match['id'], entry['id'])
            self.reload()
            self.error.clear()
        except (ValueError, OSError) as error:
            self.error.setText(tr(str(error)))
