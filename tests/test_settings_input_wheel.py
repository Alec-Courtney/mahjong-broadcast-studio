"""Setting fields ignore the wheel while page scrolling and live inputs work."""
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget
from settings_window import SettingsWindow

APP = QApplication.instance() or QApplication([])


def wheel(widget, delta=120):
    local = QPointF(widget.rect().center())
    event = QWheelEvent(local, QPointF(widget.mapToGlobal(local.toPoint())), QPoint(), QPoint(0, delta),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    return event


class SettingsInputWheelTests(unittest.TestCase):
    def test_wheel_keeps_settings_values_but_scrolls_the_page_and_adjusts_live_inputs(self):
        settings = SettingsWindow()
        live_desk = QWidget()
        live_desk.setProperty('liveDeskInputs', True)
        try:
            settings.resize(360, 240)
            body = QWidget()
            body.setMinimumHeight(600)
            fields = QVBoxLayout(body)
            spin = QSpinBox()
            spin.setValue(5)
            combo = QComboBox()
            combo.addItems(['甲', '乙'])
            fields.addWidget(spin)
            fields.addWidget(combo)
            fields.addStretch()
            scroll = QScrollArea()
            scroll.setWidget(body)
            QVBoxLayout(settings).addWidget(scroll)
            settings.show()
            APP.processEvents()
            spin.setFocus()
            wheel(spin)
            wheel(combo, -120)
            self.assertEqual(spin.value(), 5)
            self.assertEqual(combo.currentIndex(), 0)
            scroll.verticalScrollBar().setValue(0)
            wheel(scroll.viewport(), -120)
            self.assertGreater(scroll.verticalScrollBar().value(), 0)
            live_spin = QSpinBox(live_desk)
            live_spin.setValue(5)
            wheel(live_spin)
            self.assertEqual(live_spin.value(), 6)
        finally:
            settings.deleteLater()
            live_desk.deleteLater()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == '__main__':
    unittest.main()
