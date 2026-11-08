"""Round controls grow with desk typography and wrap inside narrow docks."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtWidgets import QApplication,QStyle,QStyleOptionComboBox,QStyleOptionSpinBox
from console import Console
from model import Model
from overlays import OverlayManager
try:
    from .ui_test_support import reveal,settle,visibility
except ImportError:
    from ui_test_support import reveal,settle,visibility

APP=QApplication.instance() or QApplication([])


class RoundFontResizingTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.cache').mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='round-font-',dir=ROOT/'.cache')
        self.model=Model(self.tmp.name)
        self.model.settings.update(show_overlays=False,topmost=False,console_font_family='Microsoft YaHei')
        self.manager=OverlayManager(self.model);self.ui=Console(self.model,self.manager)
        self.ui.resize(1460,1000);self.ui.show();settle(APP)

    def tearDown(self):
        self.ui.close();self.ui.deleteLater();APP.processEvents();self.tmp.cleanup()

    def assert_text_fits(self):
        for name in ('round','dealer','honba','pot'):
            control=getattr(self.ui,name)
            if name in ('honba','pot'):
                option=QStyleOptionSpinBox();control.initStyleOption(option)
                rect=control.style().subControlRect(QStyle.ComplexControl.CC_SpinBox,option,QStyle.SubControl.SC_SpinBoxEditField,control)
                text=control.text()
            else:
                option=QStyleOptionComboBox();control.initStyleOption(option)
                rect=control.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,control)
                text=control.currentText()
            with self.subTest(control=name,font=control.font().pixelSize()):
                self.assertGreaterEqual(rect.width(),control.fontMetrics().horizontalAdvance(text))
                self.assertGreater(control.maximumWidth(),108)

    def test_larger_font_expands_control_minimums_and_real_input_widths(self):
        self.model.setting('console_font_size',14);settle(APP)
        baseline={name:(getattr(self.ui,name).minimumWidth(),getattr(self.ui,name).width()) for name in ('round','dealer','honba','pot')}
        for size in (17,20,24):
            self.model.setting('console_font_size',size);settle(APP)
            self.assert_text_fits()
            self.assertEqual(self.ui.workspace.scrolls['round'].horizontalScrollBar().maximum(),0)
            for name in baseline:
                self.assertGreater(getattr(self.ui,name).minimumWidth(),baseline[name][0])
        for name in ('honba','pot'):
            self.assertGreater(getattr(self.ui,name).width(),baseline[name][1])

    def test_wide_and_narrow_round_docks_reflow_without_clipping(self):
        self.model.setting('console_font_size',24)
        dock=self.ui.workspace.docks['round'];dock.setFloating(True);dock.resize(720,520);settle(APP)
        self.assertEqual(self.ui.round.y(),self.ui.dealer.y())
        self.assertEqual(self.ui.honba.y(),self.ui.pot.y())
        self.assert_text_fits()
        dock.resize(280,520);settle(APP)
        self.assertGreater(self.ui.dealer.y(),self.ui.round.y())
        self.assertGreater(self.ui.pot.y(),self.ui.honba.y())
        self.assertEqual(self.ui.workspace.scrolls['round'].horizontalScrollBar().maximum(),0)
        self.assert_text_fits()

    def test_small_desk_keeps_round_inputs_and_hand_actions_reachable_at_large_font(self):
        self.ui.resize(960,620)
        for size in (17,24):
            self.model.setting('console_font_size',size);settle(APP)
            self.ui.honba.setValue(17);self.ui.pot.setValue(7);settle(APP)
            self.assertEqual((self.model.state['honba'],self.model.state['pot']),(17,7000))
            self.assert_text_fits()
            self.assertEqual(self.ui.workspace.scrolls['round'].horizontalScrollBar().maximum(),0)
            for control in (self.ui.round,self.ui.dealer,self.ui.honba,self.ui.pot,
                            self.ui.quick_actions['draw'],self.ui.quick_actions['next_hand']):
                reveal(control,APP)
                self.assertTrue(visibility(control,self.ui)['visible'])


if __name__=='__main__':unittest.main()
