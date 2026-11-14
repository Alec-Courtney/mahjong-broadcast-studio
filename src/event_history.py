"""Saved standings and half-match records, with time edits for the active event."""
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QDialog, QHeaderView, QLabel, QSplitter, QTabWidget, QVBoxLayout, QWidget

from desk_theme import apply_theme
from event_penalties import net_results
from event_results import recorded_time
from i18n import tr
import ranking_reports as reports
from ui_widgets import button, choice, item, note, row, table


class EventHistoryDialog(QDialog):
    def __init__(self, workspace, state, event, simulated=False, select_matches=False):
        super().__init__(workspace)
        self.state = state
        self.model = workspace.model
        self.settings = workspace.model.settings
        self.event_metadata = event
        self.matches = reports.confirmed_matches(state)[::-1]
        self.selection_mode = False
        self.setProperty('settingsWindow', True)
        self.setWindowTitle('历史赛事成绩')
        self.resize(1120, 710)
        layout = QVBoxLayout(self)
        title = QLabel(event['name'])
        title.setObjectName('sectionTitle')
        title.setProperty('i18n_literal', True)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        self.summary = note(f"{tr('已确认半庄')} {len(self.matches)}")
        self.summary.setProperty('i18n_literal', True)
        layout.addWidget(self.summary)
        if simulated:
            layout.addWidget(note('这是演示赛事，以下为模拟成绩。'))

        tabs = QTabWidget()
        tabs.addTab(self.match_page(), '半庄记录')
        tabs.addTab(self.ranking_page(), '赛事排名')
        layout.addWidget(tabs, 1)
        self.selection_controls = QWidget()
        controls = QVBoxLayout(self.selection_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(note('勾选需要汇总的半庄。图片按记录时间排列；第几半庄按本赛事当天全部已确认记录计算，顶部只合计所选场次，积分包含生效判罚。'))
        self.selection_count = QLabel()
        self.selection_count.setProperty('i18n_literal', True)
        self.generate_button = button('生成队伍结果图', self.generate_team_report)
        self.generate_button.setObjectName('primary')
        controls.addLayout(row(button('全选', lambda: self.check_matches(True)),
                               button('清空', lambda: self.check_matches(False)),
                               self.selection_count, 1, self.generate_button))
        layout.addWidget(self.selection_controls)
        self.selection_controls.hide()
        self.select_button = button('选择半庄生成队伍结果图', self.enable_match_selection)
        self.select_button.setVisible(event['kind'] == 'team_league')
        layout.addLayout(row(self.select_button, 1))
        self.export_error = note('')
        layout.addWidget(self.export_error)
        layout.addLayout(row(1, button('关闭', self.close)))
        self.match_table.itemChanged.connect(self.update_selection_count)
        if select_matches:
            self.enable_match_selection()
        apply_theme(self, workspace.model.settings)
        self.model.changed.connect(self.reload_records)

    def enable_match_selection(self):
        if self.event_metadata['kind'] != 'team_league' or self.selection_mode:
            return
        self.selection_mode = True
        self.select_button.hide()
        self.selection_controls.show()
        with QSignalBlocker(self.match_table):
            for index, match in enumerate(self.matches):
                cell = self.match_table.item(index, 0)
                cell.setFlags(cell.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                cell.setData(Qt.ItemDataRole.UserRole, match['id'])
                cell.setCheckState(Qt.CheckState.Unchecked)
        self.update_selection_count()

    def selected_match_ids(self):
        return [match['id'] for index, match in enumerate(self.matches)
                if self.match_table.item(index, 0).checkState() == Qt.CheckState.Checked]

    def update_selection_count(self, *_):
        count = len(self.selected_match_ids()) if self.selection_mode else 0
        self.selection_count.setText(f'已选 {count} / {len(self.matches)} 场')
        self.generate_button.setEnabled(count > 0)

    def check_matches(self, checked):
        with QSignalBlocker(self.match_table):
            for index in range(len(self.matches)):
                self.match_table.item(index, 0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.update_selection_count()

    def generate_team_report(self):
        from team_match_preview import TeamMatchReportDialog

        try:
            dialog = TeamMatchReportDialog(self, self.selected_match_ids())
            self.export_error.clear()
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()
        except (ValueError, OSError) as error:
            self.export_error.setText(str(error))

    def match_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.match_table = table(['序号', '记录标题', '来源', '局数'])
        self.match_table.horizontalHeader().setStretchLastSection(False)
        self.match_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.match_table.setMinimumWidth(270)
        self.populate_matches()
        split.addWidget(self.match_table)

        detail = QWidget()
        details = QVBoxLayout(detail)
        details.setContentsMargins(8, 0, 0, 0)
        self.record_time = note('')
        self.edit_time_button = button('修改记录时间', self.edit_record_time)
        details.addLayout(row(self.record_time, 1, self.edit_time_button))
        self.final_table = table(['名次', '选手', '队伍', '最终持点', '入账积分（pt）'])
        self.final_table.horizontalHeader().setStretchLastSection(False)
        self.final_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.final_table.setColumnHidden(2, self.event_metadata['kind'] != 'team_league')
        details.addWidget(self.final_table, 1)
        self.penalties_button = button('查看半庄判罚', self.show_penalties)
        details.addLayout(row(self.penalties_button, 1))
        self.hand_note = note('')
        details.addWidget(self.hand_note)
        self.hand_table = table([])
        self.hand_table.setProperty('i18n_literal', True)
        details.addWidget(self.hand_table, 2)
        split.addWidget(detail)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        layout.addWidget(split, 1)

        self.match_table.currentCellChanged.connect(self.show_match)
        if self.matches:
            self.match_table.selectRow(0)
        else:
            self.show_match(-1)
        return page

    def is_current_event(self):
        return (self.model.event or {}).get('id') == self.event_metadata['id']

    def populate_matches(self, checked=()):
        self.match_table.setRowCount(len(self.matches))
        for index, match in enumerate(self.matches):
            title = match.get('title') or tr('半庄')
            source = tr('补录') if match.get('source') == 'manual' else tr('导播台')
            values = (len(self.matches) - index, title, source, len(match['hands']))
            for column, value in enumerate(values):
                cell = item(value, editable=False)
                if column == 1:
                    cell.setToolTip(title)
                if column == 0 and self.selection_mode:
                    cell.setFlags(cell.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    cell.setData(Qt.ItemDataRole.UserRole, match['id'])
                    cell.setCheckState(Qt.CheckState.Checked if match['id'] in checked else Qt.CheckState.Unchecked)
                self.match_table.setItem(index, column, cell)

    def reload_records(self, *_):
        if not self.is_current_event():
            self.edit_time_button.setEnabled(False)
            return
        checked = set(self.selected_match_ids()) if self.selection_mode else set()
        index = self.match_table.currentRow()
        identity = self.matches[index]['id'] if 0 <= index < len(self.matches) else None
        self.state = self.model.state
        self.matches = reports.confirmed_matches(self.state)[::-1]
        index = next((i for i, match in enumerate(self.matches) if match['id'] == identity), 0)
        with QSignalBlocker(self.match_table):
            self.populate_matches(checked)
            if self.matches:
                self.match_table.selectRow(index)
        self.show_match(index)
        self.summary.setText(f"{tr('已确认半庄')} {len(self.matches)}")
        self.update_selection_count()
        self.show_ranking()

    def edit_record_time(self):
        from match_record_time import MatchRecordTimeDialog

        index = self.match_table.currentRow()
        if not self.is_current_event() or not 0 <= index < len(self.matches):
            return
        dialog = MatchRecordTimeDialog(self, self.model, self.matches[index])
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.export_error.setText(tr('记录时间已保存，生成结果图将使用新的日期和当天序号。'))
        finally:
            dialog.deleteLater()

    def show_match(self, index, *_):
        match = self.matches[index] if 0 <= index < len(self.matches) else None
        self.final_table.setRowCount(0)
        self.hand_table.setRowCount(0)
        self.final_table.setVisible(match is not None)
        self.record_time.setText(tr('记录时间')+'：'+recorded_time(match) if match else '')
        self.edit_time_button.setEnabled(match is not None and self.is_current_event())
        self.edit_time_button.setToolTip('' if self.is_current_event() else tr('请先打开此赛事，再修改记录时间。'))
        self.penalties_button.setEnabled(bool(match and match.get('penalties')))
        self.hand_table.setVisible(bool(match and match['hands']))
        if match is None:
            self.hand_note.setText(tr('此赛事暂无已确认的半庄。'))
            return

        results = sorted(net_results(match), key=lambda result: result['rank'])
        self.final_table.setRowCount(len(results))
        for index, result in enumerate(results):
            score = result.get('score')
            values = (result['rank'], result.get('name') or result['id'], result.get('team') or '—',
                f'{score:,}' if isinstance(score, (int, float)) else '—', reports.display('points', result['points']))
            for column, value in enumerate(values):
                cell = item(value, editable=False)
                if column in (0, 3, 4):
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.final_table.setItem(index, column, cell)

        hands = match['hands']
        if not hands:
            message = '此半庄只补录了最终成绩。' if match.get('source') == 'manual' else '此半庄没有逐局记录。'
            self.hand_note.setText(tr(message))
            return

        message = '逐局持点（括号内为本局变动）'
        if match.get('partial'):
            message = '逐局持点（括号内为本局变动，记录从中途开始）'
        self.hand_note.setText(tr(message))
        first_seats = hands[0]['players']
        labels = [tr('局况'), tr('结算结果'), *(player.get('name') or player['id'] for player in first_seats)]
        self.hand_table.setColumnCount(len(labels))
        self.hand_table.setHorizontalHeaderLabels(labels)
        self.hand_table.setRowCount(len(hands))
        for index, hand in enumerate(hands):
            detail = self.hand_outcome(hand)
            scores = {player['id']: f'{after:,} ({after - before:+,})'
                for player, before, after in zip(hand['players'], hand['before_scores'], hand['after_scores'])}
            values = [tr(hand.get('label') or '—'), detail, *(scores.get(player['id'], '—') for player in first_seats)]
            for column, value in enumerate(values):
                self.hand_table.setItem(index, column, item(value, editable=False))

    def show_penalties(self):
        from penalty_dialog import MatchPenaltyDialog

        index = self.match_table.currentRow()
        if 0 <= index < len(self.matches):
            dialog = MatchPenaltyDialog(self, self.matches[index], self.settings)
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()

    @staticmethod
    def hand_outcome(hand):
        kind = hand['kind']
        if kind == 'draw':
            result = hand.get('result')
            tenpai = result.get('tenpai') if isinstance(result, dict) else None
            if not isinstance(tenpai, list):
                return tr('流局')
            names = '、'.join(player.get('name') or player['id']
                for seat, player in enumerate(hand['players']) if seat in tenpai)
            return f"{tr('流局')} · {tr('申报听牌')} {names or tr('无')}"
        outcome = tr('自摸' if kind == 'tsumo' else '荣和')
        winners = '、'.join(player.get('name') or player['id'] for player in hand['players'] if player.get('wins'))
        losers = '、'.join(player.get('name') or player['id'] for player in hand['players'] if player.get('deal_ins'))
        if winners and losers:
            return f'{outcome} · {winners} ← {losers}'
        return f'{outcome} · {winners}' if winners else outcome

    def ranking_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        if self.event_metadata['kind'] == 'team_league':
            self.ranking_mode = choice([('个人积分榜', 'individual'), ('团队积分榜', 'team')])
            self.ranking_mode.currentIndexChanged.connect(self.show_ranking)
            layout.addLayout(row(self.ranking_mode, 1))
        layout.addWidget(note('榜单使用已确认半庄和该赛事保存的历史积分基数。'))
        self.ranking_table = table([])
        self.ranking_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.ranking_table.setProperty('i18n_literal', True)
        layout.addWidget(self.ranking_table, 1)
        self.show_ranking()
        return page

    def show_ranking(self, *_):
        mode = self.ranking_mode.currentData() if self.event_metadata['kind'] == 'team_league' else 'individual'
        data = reports.report(self.state, self.event_metadata, mode)
        keys = data['columns']
        custom = self.event_metadata.get('report_headers', {}).get(mode, {})
        self.ranking_table.setColumnCount(len(keys))
        self.ranking_table.setHorizontalHeaderLabels([custom.get(key) or tr(reports.header(key, mode)) for key in keys])
        self.ranking_table.setRowCount(len(data['rows']))
        for index, result in enumerate(data['rows']):
            for column, key in enumerate(keys):
                cell = item(reports.display(key, result.get(key)), editable=False)
                reports.style_table_item(cell, data, result, key, index)
                self.ranking_table.setItem(index, column, cell)
        self.ranking_table.resizeColumnsToContents()
