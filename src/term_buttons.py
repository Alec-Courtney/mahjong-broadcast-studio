"""A small draft editor for glossary shortcuts; content stays in the glossary."""
import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QTextEdit, QVBoxLayout, QWidget

from desk_theme import apply_theme
from glossary import resolve_term_id, terms_for_category
from i18n import tr
from ui_widgets import button, choice, integer, note, row, selected


from settings_window import SettingsWindow, saves_section

class TermButtonDialog(SettingsWindow):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.event_id = (panel.model.event or {}).get('id')
        self.setWindowTitle('术语按钮布局')
        self.resize(860, 630)
        self.loading = False
        self.current_slot = 0
        self.slots = [None] * 12
        for value in panel.favorite_buttons():
            entry = panel.term_entry(value['ref'])
            if entry:
                self.slots[value['slot']] = dict(value, title=entry[0], body=entry[1], original=entry[:2])
        layout = QVBoxLayout(self)
        layout.addWidget(note('选择槽位编辑按钮；移到目标槽位会交换位置，空位会保留。保存不改变正在播出的解释。'))
        self.columns = integer(1, 6)
        self.columns.setValue(panel.config().get('term_button_columns', 3))
        layout.addLayout(row('每行列数', self.columns, 1))
        body = QHBoxLayout()
        grid_widget = QWidget()
        self.grid = QGridLayout(grid_widget)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        body.addWidget(grid_widget, 1)
        self.buttons = []
        for slot in range(12):
            control = button('', lambda checked=False, slot=slot: self.select_slot(slot))
            control.setCheckable(True)
            control.setMinimumHeight(42)
            control.setMinimumWidth(58)
            control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            control.setProperty('i18nLiteral', True)
            self.buttons.append(control)
        form_widget = QWidget()
        form_widget.setMinimumWidth(300)
        form = QVBoxLayout(form_widget)
        self.slot_title = QLabel()
        self.slot_title.setProperty('i18nLiteral', True)
        form.addWidget(self.slot_title)
        self.reference = choice([(tr('空位'), None)] + [(panel.entry_display(panel.term_entry(term['id']))[0], term['id'])
                                                    for term in terms_for_category()])
        self.reference.setProperty('i18nLiteral', True)
        for title, _ in panel.config().get('custom_terms', []):
            self.reference.addItem(title, 'custom:' + title)
        self.label = QLineEdit()
        self.label.setMaxLength(80)
        self.label.setPlaceholderText('留空使用术语标题')
        self.title = QLineEdit()
        self.title.setMaxLength(60)
        self.content = QTextEdit()
        self.content.setAcceptRichText(False)
        self.content.setMinimumHeight(160)
        fields = QFormLayout()
        fields.addRow('关联术语', self.reference)
        fields.addRow('按钮名称', self.label)
        fields.addRow('上屏标题', self.title)
        fields.addRow('上屏正文', self.content)
        form.addLayout(fields)
        self.destination = choice([(str(slot + 1), slot) for slot in range(12)])
        form.addLayout(row('目标槽位', self.destination, button('交换位置', self.move_slot)))
        form.addWidget(button('清空此槽位', self.clear_slot))
        form.addWidget(note('修改标题或正文会保存为词库条目；按钮名称只影响后台。'))
        form.addStretch()
        body.addWidget(form_widget, 1)
        layout.addLayout(body, 1)
        self.error = QLabel()
        self.error.setObjectName('error')
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        save = button('保存', self.apply)
        save.setObjectName('primary')
        layout.addLayout(row(1, button('关闭', self.close), save))
        self.columns.valueChanged.connect(self.rebuild_grid)
        self.reference.currentIndexChanged.connect(self.change_reference)
        self.label.textChanged.connect(self.store_fields)
        self.title.textChanged.connect(self.store_fields)
        self.content.textChanged.connect(self.store_fields)
        self.track_settings('buttons',lambda:(copy.deepcopy(self.slots),self.columns.value()),self.apply)
        self.rebuild_grid()
        self.load_slot()
        apply_theme(self, panel.model.settings)

    def rebuild_grid(self, *_):
        for control in self.buttons:
            self.grid.removeWidget(control)
        for slot, control in enumerate(self.buttons):
            value = self.slots[slot]
            text = value.get('label') or value['title'] if value else tr('空位')
            control.setText(f'{slot+1} · {text}')
            control.setChecked(slot == self.current_slot)
            self.grid.addWidget(control, slot // self.columns.value(), slot % self.columns.value())

    def select_slot(self, slot):
        self.current_slot = slot
        self.load_slot()
        self.rebuild_grid()

    def load_slot(self):
        self.loading = True
        value = self.slots[self.current_slot]
        self.slot_title.setText(tr('槽位') + ' ' + str(self.current_slot + 1))
        selected(self.reference, value['ref'] if value else None)
        self.label.setText(value['label'] if value else '')
        self.title.setText(value['title'] if value else '')
        self.content.setPlainText(value['body'] if value else '')
        for widget in (self.label, self.title, self.content):
            widget.setEnabled(value is not None)
        self.destination.setCurrentIndex(self.current_slot)
        self.loading = False

    def change_reference(self, *_):
        if self.loading: return
        ref = self.reference.currentData()
        entry = self.panel.term_entry(ref) if ref else None
        if entry: ref = self.panel.favorite_ref(entry)
        self.slots[self.current_slot] = dict(ref=ref, label='', title=entry[0], body=entry[1], original=entry[:2]) if entry else None
        self.load_slot()
        self.rebuild_grid()

    def store_fields(self, *_):
        if self.loading: return
        value = self.slots[self.current_slot]
        if value:
            value.update(label=self.label.text().strip(), title=self.title.text().strip(), body=self.content.toPlainText().strip())
            self.rebuild_grid()

    def move_slot(self):
        target = self.destination.currentData()
        self.slots[self.current_slot], self.slots[target] = self.slots[target], self.slots[self.current_slot]
        self.select_slot(target)

    def clear_slot(self):
        self.slots[self.current_slot] = None
        self.load_slot()
        self.rebuild_grid()

    @saves_section('buttons')
    def apply(self):
        try:
            if (self.panel.model.event or {}).get('id') != self.event_id:
                raise ValueError('当前赛事已切换，请重新打开设置。')
            custom = copy.deepcopy(self.panel.config().get('custom_terms', []))
            buttons = []
            for slot, value in enumerate(self.slots):
                if value is None: continue
                title, content = value['title'], value['body']
                if not title or not content: raise ValueError('请填写解释标题与正文')
                if len(content) > 500: raise ValueError('正文请控制在500字以内')
                ref = self.panel.favorite_ref(self.panel.term_entry(value['ref'])) or value['ref']
                if (title, content) != value['original']:
                    target_id = resolve_term_id(title)
                    if title != value['original'][0] and (any(entry[0] == title for entry in custom)
                            or target_id and target_id != resolve_term_id(value['original'][0])):
                        raise ValueError('该术语标题已存在，请使用不同标题')
                    custom = [entry for entry in custom if entry[0] != title] + [[title, content]]
                    ref = target_id or 'custom:' + title
                if any(entry['ref'] == ref for entry in buttons):
                    raise ValueError('同一术语只能安排一个按钮')
                buttons.append(dict(slot=slot, ref=ref, label=value['label']))
            self.panel.model.configure_commentary(dict(custom_terms=custom, term_favorites=[entry['ref'] for entry in buttons],
                                                       term_buttons=buttons, term_button_columns=self.columns.value()))
            self.panel.load_terms()
            for value in self.slots:
                if value is not None:
                    value['original']=(value['title'],value['body'])
            for entry in buttons:
                self.slots[entry['slot']]['ref']=entry['ref']
            self.error.clear()
            return True
        except Exception as error:
            self.error.setText(tr(str(error)))
            return False
