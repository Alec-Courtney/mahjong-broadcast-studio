"""Edit the date/time of any confirmed live or manually entered half-game."""
from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import QDateTimeEdit, QDialog, QFormLayout, QLabel, QVBoxLayout

from desk_theme import apply_theme
from event_results import recorded_datetime
from i18n import tr
from ui_widgets import button, note, row


class MatchRecordTimeDialog(QDialog):
    def __init__(self, parent, model, match):
        super().__init__(parent)
        self.model, self.match = model, match
        self.context = (model.path, (model.event or {}).get('id'))
        self.setProperty('settingsWindow', True)
        self.setWindowTitle('修改半庄记录时间')
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        title = note(match.get('title') or tr('半庄'))
        title.setProperty('i18n_literal', True)
        layout.addWidget(title)
        form = QFormLayout()
        self.time = QDateTimeEdit(QDateTime.currentDateTime())
        self.time.setCalendarPopup(True)
        self.time.setDisplayFormat('yyyy-MM-dd HH:mm:ss')
        self.time.setKeyboardTracking(False)
        saved = recorded_datetime(match)
        if saved:
            self.time.setDateTime(QDateTime.fromSecsSinceEpoch(int(saved.timestamp())).toLocalTime())
        form.addRow('记录时间', self.time)
        layout.addLayout(form)
        layout.addWidget(note('结果图按记录日期和时间计算当天的第几半庄，编号依据本赛事全部已确认记录。'))
        self.error = QLabel()
        self.error.setObjectName('error')
        self.error.setWordWrap(True)
        self.error.setProperty('i18n_literal', True)
        layout.addWidget(self.error)
        self.save_button = button('保存时间', self.save)
        self.save_button.setObjectName('primary')
        layout.addLayout(row(1, button('取消', self.reject), self.save_button))
        apply_theme(self, model.settings)

    def save(self):
        try:
            if self.context != (self.model.path, (self.model.event or {}).get('id')):
                raise ValueError('赛事已切换，请重新打开半庄记录')
            value = self.time.dateTime().toPython().astimezone().isoformat(timespec='seconds')
            self.model.records.update_recorded_at(self.match['id'], value, expected_record=self.match)
        except (ValueError, OSError) as error:
            self.error.setText(tr(str(error)))
            return
        self.accept()
