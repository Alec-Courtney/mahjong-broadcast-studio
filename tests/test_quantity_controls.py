"""Large native minus/plus targets keep integer, decimal and live-count input."""
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractSpinBox, QStyle, QStyleOptionSpinBox
from console import FuSpinBox, OptionalCountSpinBox
from desk_theme import apply_theme
from ui_widgets import decimal, integer, WindowScaleSpinBox

APP = QApplication.instance() or QApplication([])


class QuantityControlTests(unittest.TestCase):
    def test_existing_small_and_large_window_sizes_are_not_clipped_in_percent_editor(self):
        widget = WindowScaleSpinBox(4380, 438)
        try:
            self.assertEqual(widget.value(), 1000)
            self.assertEqual(widget.pixel_width(), 4380)
            widget.setValue(500)
            self.assertEqual(widget.pixel_width(), 2190)
            widget.set_pixel_width(100, 400)
            self.assertEqual(widget.value(), 25)
            self.assertEqual(widget.pixel_width(), 100)
        finally:widget.deleteLater();APP.processEvents()

    def click_step(self, widget, upward):
        option = QStyleOptionSpinBox(); widget.initStyleOption(option)
        control = QStyle.SubControl.SC_SpinBoxUp if upward else QStyle.SubControl.SC_SpinBoxDown
        rect = widget.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, control, widget)
        self.assertGreaterEqual(rect.width(), 28); self.assertGreaterEqual(rect.height(), 30)
        self.assertEqual(widget.buttonSymbols(), QAbstractSpinBox.ButtonSymbols.PlusMinus)
        edit = widget.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, QStyle.SubControl.SC_SpinBoxEditField, widget)
        self.assertGreaterEqual(rect.left(), edit.right())
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=rect.center())

    def test_integer_step_targets_work_in_both_themes_and_respect_limits(self):
        for theme in ('dark', 'light'):
            widget = integer(0, 200, 100); apply_theme(widget, {'console_theme': theme})
            widget.resize(128, 38); widget.show(); APP.processEvents()
            try:
                self.click_step(widget, True); self.assertEqual(widget.value(), 100)
                self.click_step(widget, False); self.assertEqual(widget.value(), 0)
                self.click_step(widget, False); self.assertEqual(widget.value(), 0)
                widget.setValue(200); self.click_step(widget, True); self.assertEqual(widget.value(), 200)
            finally: widget.close(); widget.deleteLater(); APP.processEvents()

    def test_decimal_step_and_blank_wall_count_remain_editable(self):
        widget = decimal(-2, 12, 1); widget.setSingleStep(.5)
        count = OptionalCountSpinBox()
        for control in (widget, count):
            apply_theme(control, {}); control.resize(108, 38); control.show()
        APP.processEvents()
        try:
            self.click_step(widget, True); self.assertAlmostEqual(widget.value(), .5)
            self.click_step(widget, False); self.assertAlmostEqual(widget.value(), 0.)
            self.assertEqual(count.value(), -1); self.click_step(count, True); self.assertEqual(count.value(), 0)
            self.click_step(count, True); self.assertEqual(count.value(), 1)
            count.lineEdit().setText('136 张'); count.interpretText(); self.assertEqual(count.value(), 136)
            self.click_step(count, True); self.assertEqual(count.value(), 136)
        finally:
            for control in (widget, count): control.close(); control.deleteLater()
            APP.processEvents()

    def test_fu_steps_by_ten_with_25_between_20_and_30(self):
        widget = FuSpinBox(); apply_theme(widget, {})
        widget.resize(108, 38); widget.show(); APP.processEvents()
        try:
            self.assertEqual(widget.singleStep(), 10)
            widget.setValue(20)
            sequence = [20, 25, *range(30, 141, 10)]
            for value in sequence[1:]:
                self.click_step(widget, True); self.assertEqual(widget.value(), value)
            self.click_step(widget, True); self.assertEqual(widget.value(), 140)
            for value in reversed(sequence[:-1]):
                self.click_step(widget, False); self.assertEqual(widget.value(), value)
            self.click_step(widget, False); self.assertEqual(widget.value(), 20)
            QTest.keyClick(widget, Qt.Key.Key_Up); self.assertEqual(widget.value(), 25)
            QTest.keyClick(widget, Qt.Key.Key_Up); self.assertEqual(widget.value(), 30)
            QTest.keyClick(widget, Qt.Key.Key_Down); self.assertEqual(widget.value(), 25)
        finally: widget.close(); widget.deleteLater(); APP.processEvents()


if __name__ == '__main__': unittest.main()
