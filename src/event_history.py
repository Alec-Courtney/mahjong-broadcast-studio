"""Read-only standings and half-match records from a saved event."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHeaderView, QLabel, QSplitter, QTabWidget, QVBoxLayout, QWidget

from desk_theme import apply_theme
from event_penalties import net_results
from event_results import recorded_time
from i18n import tr
import ranking_reports as reports
from ui_widgets import button, choice, item, note, row, table


class EventHistoryDialog(QDialog):
    def __init__(self, workspace, state, event, simulated=False):
        super().__init__(workspace)
        self.state = state
        self.settings = workspace.model.settings
        self.event_metadata = event
        self.matches = reports.confirmed_matches(state)[::-1]
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
        summary = note(f"{tr('已确认半庄')} {len(self.matches)}")
        summary.setProperty('i18n_literal', True)
        layout.addWidget(summary)
        if simulated:
            layout.addWidget(note('这是演示赛事，以下为模拟成绩。'))

        tabs = QTabWidget()
        tabs.addTab(self.match_page(), '半庄记录')
        tabs.addTab(self.ranking_page(), '赛事排名')
        layout.addWidget(tabs, 1)
        layout.addLayout(row(1, button('关闭', self.close)))
        apply_theme(self, workspace.model.settings)

    def match_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.match_table = table(['序号', '记录标题', '来源', '局数'])
        self.match_table.horizontalHeader().setStretchLastSection(False)
        self.match_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.match_table.setMinimumWidth(270)
        self.match_table.setRowCount(len(self.matches))
        for index, match in enumerate(self.matches):
            title = match.get('title') or tr('半庄')
            source = tr('补录') if match.get('source') == 'manual' else tr('导播台')
            values = (len(self.matches) - index, title, source, len(match['hands']))
            for column, value in enumerate(values):
                cell = item(value, editable=False)
                if column == 1:
                    cell.setToolTip(title)
                self.match_table.setItem(index, column, cell)
        split.addWidget(self.match_table)

        detail = QWidget()
        details = QVBoxLayout(detail)
        details.setContentsMargins(8, 0, 0, 0)
        self.record_time = note('')
        details.addWidget(self.record_time)
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

    def show_match(self, index, *_):
        match = self.matches[index] if 0 <= index < len(self.matches) else None
        self.final_table.setRowCount(0)
        self.hand_table.setRowCount(0)
        self.final_table.setVisible(match is not None)
        self.record_time.setText(tr('记录时间')+'：'+recorded_time(match) if match else '')
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
