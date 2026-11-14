"""Persistent operator ledger and a local, previewed correction dialog."""
from __future__ import annotations

import copy
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
    QSizePolicy, QVBoxLayout, QWidget)

from hand_history import context_for, event_for, hand_deltas
from mahjong_values import YAKU, yaku_total


def points(value):
    return f'{value:,}'


def signed(value):
    return ('+' if value > 0 else '−' if value < 0 else '') + points(abs(value))


def summary(hand):
    result = hand['result']
    players = hand['players']
    if hand['kind'] == 'draw':
        tenpai = '、'.join(players[i]['name'] for i in result.get('tenpai', [])) or '无人'
        rules = hand.get('context', {}).get('rules', {})
        payment = rules.get('notenPayment')
        if 'manual_deltas' in hand:
            payment_text = ' · 不听罚符见手动收支'
        else:
            actual = payment if 0 < len(result.get('tenpai', [])) < 4 else 0
            payment_text = f" · 不听罚符 {points(actual)}" if actual is not None else ''
        method = '流局 · 听牌：' + tenpai + payment_text
    else:
        method = players[result['winner']]['name'] + (' 自摸' if hand['kind'] == 'tsumo'
                 else ' 荣和 ← ' + players[result['loser']]['name'])
        method += ' · ' + (str(result['yakuman']) + '倍役满' if result.get('yakuman')
                             else f"{result['fu']}符 {result['han']}番")
    if 'manual_deltas' in hand:
        method += ' · 手动收支'
    paid = hand.get('context', {}).get('riichi_paid', [bool(p.get('riichi')) for p in players])
    sticks = '、'.join(p['name'] for p, flag in zip(players, paid) if flag) or '无人'
    return (hand['label'] + ' · ' + method,
            f"本场 {hand['honba']} · 结算前供托 {points(result['potBefore'])} → 局后 {points(result['potAfter'])}"
            + ' · 交立直棒：' + sticks)


def make_table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().hide()
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setDefaultSectionSize(29)
    table.setMinimumHeight(74)
    return table


def put(table, row, column, text, delta=None):
    item = QTableWidgetItem(text)
    item.setToolTip(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if column else Qt.AlignmentFlag.AlignLeft))
    if delta:
        item.setForeground(QColor('#3aa879' if delta > 0 else '#df6464'))
    table.setItem(row, column, item)
    return item


class HandHistoryPanel(QGroupBox):
    def __init__(self, main):
        super().__init__('逐局收支')
        self.main = main
        self.setProperty('i18nLiteral', True)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.latest_title = QLabel('尚无已结算小局')
        self.latest_title.setWordWrap(True)
        self.latest_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.latest_title.setProperty('i18nLiteral', True)
        top.addWidget(self.latest_title, 1)
        self.edit_latest = QPushButton('查看 / 修改上一局')
        self.edit_latest.clicked.connect(lambda: self.open_hand(self.latest_id))
        top.addWidget(self.edit_latest)
        layout.addLayout(top)
        self.latest_detail = QLabel()
        self.latest_detail.setObjectName('muted')
        self.latest_detail.setWordWrap(True)
        self.latest_detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.latest_detail.setProperty('i18nLiteral', True)
        layout.addWidget(self.latest_detail)
        score_grid = QGridLayout()
        self.latest_scores = []
        for i in range(4):
            label = QLabel()
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setProperty('i18nLiteral', True)
            score_grid.addWidget(label, 0, i)
            score_grid.setColumnStretch(i, 1)
            self.latest_scores.append(label)
        layout.addLayout(score_grid)
        self.history = make_table(['历史小局'] + [p['name'] for p in main.model.state['players']])
        self.history.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.history.setMinimumHeight(74)
        self.history.setProperty('i18nLiteral', True)
        self.history.setToolTip('点击历史小局查看详情或修改。收支包含本局交出的立直棒。')
        self.history.cellClicked.connect(self.open_row)
        self.history.cellActivated.connect(self.open_row)
        layout.addWidget(self.history, 1)
        self.latest_id = None
        self.row_ids = []
        self.refresh()

    def refresh(self):
        hands = self.main.model.state['broadcast']['hands']
        self.edit_latest.setEnabled(bool(hands))
        self.latest_id = hands[-1]['id'] if hands else None
        self.history.setHorizontalHeaderLabels(['历史小局'] + [p['name'] for p in self.main.model.state['players']])
        if hands:
            hand = hands[-1]
            title, detail = summary(hand)
            self.latest_title.setText('上一局 · ' + title)
            self.latest_title.setToolTip(title)
            self.latest_detail.setText(detail)
            self.latest_detail.setToolTip(detail)
            before = hand.get('context', {}).get('scores', hand['before_scores'])
            for label, player, a, b, delta in zip(self.latest_scores, hand['players'], before, hand['after_scores'], hand_deltas(hand)):
                label.setText(player['name'] + '  ' + signed(delta) + '\n' + points(a) + ' → ' + points(b))
                label.setToolTip(label.text() + '\n本局收支（含交棒） · 结算前 → 结算后')
        else:
            self.latest_title.setText('当前半庄尚无已结算小局')
            self.latest_detail.setText('结算后常驻显示上一局详情；历史小局点击查看或修改。')
            for label in self.latest_scores:
                label.clear()
        history = list(reversed(hands[:-1]))
        self.row_ids = [hand['id'] for hand in history]
        self.history.setRowCount(len(history))
        for row, hand in enumerate(history):
            label = put(self.history, row, 0, hand['label'])
            label.setToolTip('\n'.join(summary(hand)))
            for column, delta in enumerate(hand_deltas(hand), 1):
                item = put(self.history, row, column, signed(delta), delta)
                item.setToolTip(hand['players'][column - 1]['name'] + ' · ' + signed(delta) + ' 点（含交立直棒）\n点击查看 / 修改')

    def open_row(self, row, _column):
        if row < len(self.row_ids):
            self.open_hand(self.row_ids[row])

    def open_hand(self, hand_id):
        if not hand_id:
            return
        try:
            dialog = HandCorrectionDialog(self.main, hand_id)
            dialog.exec()
            dialog.deleteLater()
        except Exception as error:
            self.main.error(str(error))


class HandCorrectionDialog(QDialog):
    def __init__(self, main, hand_id):
        super().__init__(main)
        self.main, self.model, self.hand_id = main, main.model, hand_id
        self.expected_state = self.model.state
        self.hand = copy.deepcopy(next(h for h in self.expected_state['broadcast']['hands'] if h['id'] == hand_id))
        if 'win_info' not in self.hand and self.expected_state['broadcast']['current_hand'] == hand_id:
            self.hand['win_info'] = copy.deepcopy(self.model.last_info)
        self.context = context_for(self.hand, self.expected_state)
        event = event_for(self.hand)
        self.loading = True
        self.setWindowTitle('小局详情与收支更正 · ' + self.hand['label'])
        self.resize(960, 790)
        screen = self.screen().availableGeometry()
        self.resize(min(self.width(), screen.width() - 40), min(self.height(), screen.height() - 50))
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        content = QVBoxLayout(body)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        note = QLabel('修改后先核对预览，再保存。本局及后续收支、供托和实时点数会一起更新，可在导播台撤销。')
        note.setWordWrap(True)
        content.addWidget(note)
        if 'context' not in self.hand:
            legacy = QLabel('旧记录未保存完整役种与副露；详情按已知结算数据恢复，请核对并补充。')
            legacy.setWordWrap(True)
            legacy.setObjectName('muted')
            content.addWidget(legacy)
        fields = QGridLayout()
        content.addLayout(fields)
        self.kind = QComboBox()
        for label, value in (('荣和', 'ron'), ('自摸', 'tsumo'), ('流局', 'draw')):
            self.kind.addItem(label, value)
        self.kind.setCurrentIndex(self.kind.findData(event['type']))
        self.winner, self.loser, self.dealer, self.round = (QComboBox() for _ in range(4))
        for seat, player in enumerate(self.hand['players']):
            for control in (self.winner, self.loser, self.dealer):
                control.addItem(player['name'], seat)
        self.winner.setCurrentIndex(event.get('winner') or 0)
        self.loser.setCurrentIndex(event.get('loser') if event.get('loser') is not None else 1)
        self.dealer.setCurrentIndex(self.context['dealer'])
        for i in range(8):
            self.round.addItem(('东' if i < 4 else '南') + str(i % 4 + 1) + '局', i)
        self.round.setCurrentIndex(self.context['round_index'])
        self.honba = self.number(self.context['honba'], 0, 10000)
        self.pot = self.number(self.context['pot'] // 1000, 0, 1000000)
        self.pot.setSuffix(' 根')
        self.fu = QComboBox()
        for value in (20, 25, *range(30, 141, 10)):
            self.fu.addItem(str(value), value)
        self.fu.setCurrentIndex(max(0, self.fu.findData(event.get('fu') or 30)))
        self.han = self.number(event.get('han') or 1, 1, 100)
        self.yakuman = self.number(event.get('yakuman') or 0, 0, 100)
        self.noten = self.number(self.context['rules']['notenPayment'], 0, 1000000, 600)
        fields_list = [('结果', self.kind), ('和牌选手', self.winner), ('放铳选手', self.loser),
                       ('局次', self.round), ('庄家', self.dealer), ('本场', self.honba),
                       ('结算前供托', self.pot), ('符数', self.fu), ('番数', self.han),
                       ('役满倍数', self.yakuman), ('流局罚符总额', self.noten)]
        for i, (label, control) in enumerate(fields_list):
            fields.addWidget(QLabel(label), i // 3, (i % 3) * 2)
            fields.addWidget(control, i // 3, (i % 3) * 2 + 1)
        payment_row = QHBoxLayout()
        self.manual = QCheckBox('手动收支（与役种独立）')
        self.manual.setChecked('manual_deltas' in self.hand)
        self.manual.toggled.connect(self.refresh_preview)
        payment_row.addWidget(self.manual)
        payment_row.addStretch()
        payment_row.addWidget(QLabel('局后供托'))
        self.manual_pot = self.number(self.hand['result']['potAfter'] // 1000, 0, 1000000)
        self.manual_pot.setSuffix(' 根')
        self.manual_pot.valueChanged.connect(self.manual_delta_changed)
        payment_row.addWidget(self.manual_pot)
        content.addLayout(payment_row)
        self.seats = make_table(['选手', '交立直棒', '流局听牌', '结算前点数', '本局收支（可改）', '结算后点数'])
        self.seats.setRowCount(4)
        self.seats.verticalHeader().setDefaultSectionSize(38)
        self.seats.setMinimumHeight(202)
        self.seats.setMaximumHeight(210)
        self.seats.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        content.addWidget(self.seats)
        self.paid, self.tenpai, self.scores, self.deltas = [], [], [], []
        for seat, player in enumerate(self.hand['players']):
            put(self.seats, seat, 0, player['name'])
            paid, tenpai = QCheckBox('已交 1,000'), QCheckBox('听牌')
            paid.setChecked(self.context['riichi_paid'][seat])
            tenpai.setChecked(seat in (event.get('tenpai') or []))
            score = self.number(self.context['scores'][seat], -1000000000, 1000000000, 100)
            score.setToolTip('本局结算前的实际点数，已扣除交出的立直棒。')
            self.seats.setCellWidget(seat, 1, paid)
            self.seats.setCellWidget(seat, 2, tenpai)
            self.seats.setCellWidget(seat, 3, score)
            delta = self.number(hand_deltas(self.hand)[seat], -1000000000, 1000000000, 100)
            delta.setToolTip('可直接修改本局净收支（含交出的立直棒），修改后与役种独立保存。')
            delta.valueChanged.connect(self.manual_delta_changed)
            self.seats.setCellWidget(seat, 4, delta)
            self.paid.append(paid)
            self.tenpai.append(tenpai)
            self.scores.append(score)
            self.deltas.append(delta)
            paid.toggled.connect(lambda checked, i=seat: self.deposit_changed(i, checked))
            tenpai.toggled.connect(self.refresh_preview)
            score.valueChanged.connect(self.refresh_preview)
        self.yaku_group = QGroupBox('役种与宝牌')
        yaku_layout = QVBoxLayout(self.yaku_group)
        self.linked = QCheckBox('番数随役种计算')
        yaku_layout.addWidget(self.linked)
        yaku_scroll = QScrollArea()
        yaku_scroll.setWidgetResizable(True)
        yaku_scroll.setMinimumHeight(120)
        yaku_scroll.setMaximumHeight(160)
        yaku_body = QWidget()
        yaku_grid = QGridLayout(yaku_body)
        yaku_scroll.setWidget(yaku_body)
        yaku_layout.addWidget(yaku_scroll)
        info = self.hand.get('win_info') or {}
        names = info.get('yaku', [])
        self.yaku = {}
        for i, (name, _, _) in enumerate(YAKU):
            check = QCheckBox(name)
            check.setChecked(name in names)
            yaku_grid.addWidget(check, i // 5, i % 5)
            check.toggled.connect(self.yaku_changed)
            self.yaku[name] = check
        bonus_row = QHBoxLayout()
        self.bonuses = []
        extra = [name for name in names if name not in self.yaku]
        for name in ('宝牌', '赤宝牌', '里宝牌'):
            matches = [re.fullmatch(re.escape(name) + r'\s+(\d+)', label) for label in names]
            count = sum(int(match[1]) for match in matches if match)
            control = self.number(count, 0, 100)
            self.bonuses.append(control)
            bonus_row.addWidget(QLabel(name))
            bonus_row.addWidget(control)
            control.valueChanged.connect(self.yaku_changed)
            extra = [label for label in extra if not re.fullmatch(re.escape(name) + r'\s+\d+', label)]
        yaku_layout.addLayout(bonus_row)
        self.extra = QLineEdit('、'.join(extra))
        self.extra.setPlaceholderText('补充役种，使用顿号分隔')
        self.extra.textChanged.connect(self.refresh_preview)
        yaku_layout.addWidget(self.extra)
        content.addWidget(self.yaku_group)
        self.live_preview = QLabel()
        self.live_preview.setWordWrap(True)
        self.live_preview.setObjectName('draftPreview')
        layout.addWidget(self.live_preview)
        self.error = QLabel()
        self.error.setObjectName('error')
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText('保存并修正实时分数')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for control in (self.kind, self.winner, self.loser, self.dealer, self.round, self.fu):
            control.currentIndexChanged.connect(self.refresh_preview)
        for control in (self.honba, self.pot, self.han, self.yakuman, self.noten):
            control.valueChanged.connect(self.refresh_preview)
        self.linked.toggled.connect(self.yaku_changed)
        self.loading = False
        self.refresh_preview()

    @staticmethod
    def number(value, lo, hi, step=1):
        control = QSpinBox()
        control.setRange(lo, hi)
        control.setSingleStep(step)
        control.setKeyboardTracking(False)
        control.setValue(value)
        return control

    def deposit_changed(self, seat, checked):
        if self.loading:
            return
        self.loading = True
        delta = 1000 if checked else -1000
        self.scores[seat].setValue(self.scores[seat].value() - delta)
        self.pot.setValue(self.pot.value() + delta // 1000)
        self.loading = False
        self.refresh_preview()

    def manual_delta_changed(self, *_):
        if self.loading:
            return
        self.loading = True
        self.manual.setChecked(True)
        self.loading = False
        self.refresh_preview()

    def yaku_changed(self, *_):
        if self.loading:
            return
        if self.linked.isChecked():
            names = [name for name, control in self.yaku.items() if control.isChecked()]
            opened = any(m['kind'] != 'ankan' for m in self.context['melds'][self.winner.currentIndex()])
            han, yakuman = yaku_total(names, opened, sum(control.value() for control in self.bonuses), manual=True)
            self.loading = True
            self.han.setValue(max(1, han))
            self.yakuman.setValue(yakuman)
            if '七对子' in names or '平和' in names:
                fu = 25 if '七对子' in names else 20 if self.kind.currentData() == 'tsumo' else 30
                self.fu.setCurrentIndex(self.fu.findData(fu))
            self.loading = False
        self.refresh_preview()

    def patch(self):
        kind = self.kind.currentData()
        event = (dict(type='draw', tenpai=[i for i, check in enumerate(self.tenpai) if check.isChecked()])
                 if kind == 'draw' else dict(type=kind, winner=self.winner.currentData(), loser=self.loser.currentData(),
                                            han=self.han.value(), fu=self.fu.currentData(), yakuman=self.yakuman.value()))
        labels = [name for name, control in self.yaku.items() if control.isChecked()]
        labels += [label + ' ' + str(control.value()) for label, control in zip(('宝牌', '赤宝牌', '里宝牌'), self.bonuses) if control.value()]
        labels += [name.strip() for name in re.split('[、,，;；\n]', self.extra.text()) if name.strip()]
        paid = [control.isChecked() for control in self.paid]
        result = dict(event=event, win_info=dict(yaku=labels), context=dict(
            scores=[control.value() for control in self.scores], pot=self.pot.value() * 1000,
            round_index=self.round.currentData(), dealer=self.dealer.currentData(), honba=self.honba.value(),
            riichi_paid=paid, riichi=paid, noten_payment=self.noten.value()))
        result['manual_deltas'] = [control.value() for control in self.deltas] if self.manual.isChecked() else None
        if self.manual.isChecked():
            result['manual_pot_after'] = self.manual_pot.value() * 1000
        return result

    def refresh_preview(self, *_):
        if self.loading:
            return
        self.manual_pot.setEnabled(self.manual.isChecked())
        draw = self.kind.currentData() == 'draw'
        self.winner.setEnabled(not draw)
        self.loser.setEnabled(self.kind.currentData() == 'ron')
        for control in (self.fu, self.han, self.yakuman, self.yaku_group):
            control.setEnabled(not draw)
        self.noten.setEnabled(draw)
        for check in self.tenpai:
            check.setEnabled(draw)
        try:
            # Keep linked values current when the winner or method changes.
            if self.linked.isChecked() and not draw:
                signature = (self.winner.currentIndex(), self.kind.currentData())
                if signature != getattr(self, '_linked_signature', None):
                    self._linked_signature = signature
                    return self.yaku_changed()
            state, _ = self.model.preview_hand_correction(self.hand_id, self.patch(), self.expected_state)
            hand = next(h for h in state['broadcast']['hands'] if h['id'] == self.hand_id)
            for i, (delta, score) in enumerate(zip(hand_deltas(hand), hand['after_scores'])):
                blocker = self.deltas[i].blockSignals(True)
                self.deltas[i].setValue(delta)
                self.deltas[i].blockSignals(blocker)
                put(self.seats, i, 5, points(score))
            if not self.manual.isChecked():
                blocker = self.manual_pot.blockSignals(True)
                self.manual_pot.setValue(hand['result']['potAfter'] // 1000)
                self.manual_pot.blockSignals(blocker)
            self.live_preview.setText('保存后当前实时点数：' + '    ·    '.join(
                player['name'] + ' ' + points(player['score']) + '（' + signed(player['score'] - old['score']) + '）'
                for player, old in zip(state['players'], self.expected_state['players']))
                + f"\n当前供托：{points(state['pot'])} 点。收支包含交棒；结算前点数已扣除交棒。")
            self.error.clear()
            self.save_button.setEnabled(True)
        except Exception as error:
            self.error.setText(str(error))
            self.save_button.setEnabled(False)
            self.live_preview.clear()
            for i in range(4):
                put(self.seats, i, 5, '—')

    def save(self):
        try:
            self.model.correct_hand(self.hand_id, self.patch(), self.expected_state)
            self.accept()
        except Exception as error:
            self.error.setText(str(error))
            self.save_button.setEnabled(False)
