"""Real Qt shortcuts: focus safety, rebinding, persistence and dialog validation."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,QComboBox,QDialog,QKeySequenceEdit,QLineEdit,QMainWindow,QMenu,
    QPlainTextEdit,QPushButton,QSpinBox,QTextEdit,QVBoxLayout,QWidget,
)
from hotkeys import ACTIONS,DEFAULT_HOTKEYS,ShortcutController,ShortcutDialog,validate_bindings
from model import Model

APP=QApplication.instance() or QApplication([])


class HotkeyTests(unittest.TestCase):
    def setUp(self):
        self.close_answer = patch('settings_window.SettingsWindow.ask_save_changes', return_value=QMessageBox.StandardButton.No)
        self.close_answer.start();self.addCleanup(self.close_answer.stop)
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests')
        self.model=Model(self.tmp.name)
        self.window=QMainWindow();self.central=QWidget();self.window.setCentralWidget(self.central)
        self.layout=QVBoxLayout(self.central)
        self.button=QPushButton('anchor');self.layout.addWidget(self.button)
        self.edit=QLineEdit();self.layout.addWidget(self.edit)
        self.spin=QSpinBox();self.layout.addWidget(self.spin)
        self.combo=QComboBox();self.combo.addItems(['a','b']);self.layout.addWidget(self.combo)
        self.calls={action:0 for action,_,_ in ACTIONS}
        def count(action):self.calls[action]+=1
        actions={action:(lambda action=action:count(action)) for action in self.calls}
        self.controller=ShortcutController(self.window,self.model,actions)
        self.window.show();self.activate()

    def tearDown(self):
        self.controller.dispose();self.window.close();self.window.deleteLater();APP.processEvents();self.tmp.cleanup()

    def activate(self,widget=None):
        self.window.activateWindow();QApplication.setActiveWindow(self.window)
        (widget or self.button).setFocus();APP.processEvents();self.controller.refresh()

    def key(self,key,mods=Qt.KeyboardModifier.NoModifier,widget=None):
        QTest.keyClick(widget or APP.focusWidget() or self.button,key,mods);APP.processEvents()

    def test_real_keys_select_toggle_and_do_not_repeat_or_submit_without_binding(self):
        self.key(Qt.Key.Key_2,Qt.KeyboardModifier.AltModifier)
        self.key(Qt.Key.Key_R);self.key(Qt.Key.Key_F2)
        self.assertEqual((self.calls['seat_2'],self.calls['riichi'],self.calls['focus_ron']),(1,1,1))
        self.assertEqual(self.calls['submit_win'],0)
        self.assertNotIn('submit_win',self.controller.shortcuts)
        for shortcut in self.controller.shortcuts.values():self.assertFalse(shortcut.autoRepeat())
        repeat=QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_R,Qt.KeyboardModifier.NoModifier,'r',True,3)
        QApplication.sendEvent(self.button,repeat);APP.processEvents()
        self.assertEqual(self.calls['riichi'],1)
        self.controller.rebind({'submit_win':'Ctrl+Return'})
        self.key(Qt.Key.Key_Return,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.calls['submit_win'],1)

    def test_text_number_combo_and_sequence_editors_keep_all_actions_disabled(self):
        extra=[QTextEdit(),QPlainTextEdit(),QKeySequenceEdit()]
        for widget in extra:self.layout.addWidget(widget)
        self.controller.rebind({'submit_win':'Ctrl+Return'})
        baseline=dict(self.calls)
        for widget in [self.edit,self.spin,self.combo]+extra:
            self.activate(widget)
            self.assertFalse(self.controller.can_trigger(),type(widget).__name__)
            self.key(Qt.Key.Key_R,widget=widget)
            self.key(Qt.Key.Key_1,Qt.KeyboardModifier.AltModifier,widget)
            self.key(Qt.Key.Key_Return,Qt.KeyboardModifier.ControlModifier,widget)
            self.assertEqual(self.calls,baseline,type(widget).__name__)
        self.combo.setEditable(True);self.activate(self.combo.lineEdit());self.key(Qt.Key.Key_R)
        self.assertEqual(self.calls,baseline)
        self.activate();self.key(Qt.Key.Key_R);self.assertEqual(self.calls['riichi'],1)

    def test_native_editor_undo_is_not_replaced_by_match_undo(self):
        self.activate(self.edit)
        QTest.keyClicks(self.edit,'mahjong');self.assertEqual(self.edit.text(),'mahjong')
        self.key(Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier,self.edit)
        self.assertEqual(self.edit.text(),'');self.assertEqual(self.calls['undo'],0)
        self.activate();self.key(Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.calls['undo'],1)

    def test_modal_and_inactive_windows_do_not_trigger_actions(self):
        dialog=QDialog(self.window);dialog.setModal(True);button=QPushButton('modal',dialog)
        dialog.show();QApplication.setActiveWindow(dialog);button.setFocus();APP.processEvents()
        self.key(Qt.Key.Key_R,widget=button);self.assertEqual(self.calls['riichi'],0)
        dialog.close();dialog.deleteLater();APP.processEvents()
        other=QMainWindow();other.show();QApplication.setActiveWindow(other);APP.processEvents()
        self.key(Qt.Key.Key_R,widget=other);self.assertEqual(self.calls['riichi'],0)
        other.close();other.deleteLater();self.activate();self.key(Qt.Key.Key_R)
        self.assertEqual(self.calls['riichi'],1)

    def test_popup_and_disabled_button_callbacks_cannot_submit(self):
        menu=QMenu(self.window);menu.addAction('item');menu.popup(self.window.mapToGlobal(self.button.pos()))
        APP.processEvents();self.assertIsNotNone(APP.activePopupWidget())
        self.key(Qt.Key.Key_R,widget=menu);self.assertEqual(self.calls['riichi'],0)
        menu.close();menu.deleteLater();self.activate()
        submit=QPushButton('submit',self.central);self.layout.addWidget(submit)
        submit.clicked.connect(lambda:self.calls.__setitem__('submit_win',self.calls['submit_win']+1))
        self.controller.actions['submit_win']=submit.click
        self.controller.rebind({'submit_win':'Ctrl+Return'})
        submit.setEnabled(False);self.key(Qt.Key.Key_Return,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.calls['submit_win'],0)
        submit.setEnabled(True);self.key(Qt.Key.Key_Return,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.calls['submit_win'],1)

    def test_conflicts_invalid_sequences_and_save_failure_leave_binding_atomic(self):
        before=self.controller.bindings;settings=copy.deepcopy(self.model.settings)
        for invalid in ({'riichi':'F2'},{'riichi':'Ctrl+X, Ctrl+C'},{'riichi':'Ctrl'},
                        {'riichi':123},{'unknown_action':'K'}):
            with self.assertRaises(ValueError):self.controller.rebind(invalid)
            self.assertEqual(self.controller.bindings,before);self.assertEqual(self.model.settings,settings)
        with patch.object(self.model,'save',side_effect=OSError('test failure')):
            with self.assertRaises(OSError):self.controller.rebind({'riichi':'Ctrl+R'})
        self.assertEqual(self.controller.bindings,before);self.assertEqual(self.model.settings,settings)
        self.key(Qt.Key.Key_R);self.assertEqual(self.calls['riichi'],1)
        with self.assertRaises(ValueError):validate_bindings({'undo':'ctrl+shift+z'})

    def test_rebind_applies_now_emits_once_and_restarts_with_saved_shortcut(self):
        changed=[]
        self.controller.bindings_changed.connect(lambda value:changed.append(value))
        self.controller.rebind({'riichi':'Ctrl+R','submit_win':'Ctrl+Return','dora':''})
        self.controller.refresh();self.controller.refresh()
        self.assertEqual(len(changed),1)
        self.key(Qt.Key.Key_R);self.assertEqual(self.calls['riichi'],0)
        self.key(Qt.Key.Key_R,Qt.KeyboardModifier.ControlModifier);self.assertEqual(self.calls['riichi'],1)
        self.key(Qt.Key.Key_D);self.assertEqual(self.calls['dora'],0)
        reloaded=Model(self.tmp.name)
        self.assertEqual(reloaded.settings['hotkeys'],self.controller.bindings)
        self.controller.dispose()
        self.controller=ShortcutController(self.window,reloaded,{'riichi':lambda:self.calls.__setitem__('riichi',self.calls['riichi']+1)})
        self.activate();self.key(Qt.Key.Key_R,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.calls['riichi'],2)
        # Importing a different settings object can refresh without rebuilding UI.
        reloaded.settings['hotkeys']['riichi']='Alt+R';self.controller.refresh()
        self.key(Qt.Key.Key_R,Qt.KeyboardModifier.ControlModifier);self.assertEqual(self.calls['riichi'],2)
        self.key(Qt.Key.Key_R,Qt.KeyboardModifier.AltModifier);self.assertEqual(self.calls['riichi'],3)

    def test_dialog_records_conflicts_clear_defaults_cancel_and_save(self):
        original=self.controller.bindings
        dialog=ShortcutDialog(self.controller);dialog.show();QApplication.setActiveWindow(dialog);APP.processEvents()
        editor=dialog.edits['submit_win'];editor.setFocus()
        QTest.keyClick(editor,Qt.Key.Key_Return,Qt.KeyboardModifier.ControlModifier)
        APP.processEvents()
        self.assertEqual(self.calls['submit_win'],0)
        self.assertEqual(editor.keySequence().toString(),'Ctrl+Return')
        dialog.edits['riichi'].setKeySequence(QKeySequence('F2'));dialog.save()
        self.assertTrue(dialog.error_label.text());self.assertEqual(self.controller.bindings,original)
        dialog.clear_all();self.assertTrue(all(e.keySequence().isEmpty() for e in dialog.edits.values()))
        dialog.restore_defaults();self.assertEqual(dialog.edits['riichi'].keySequence().toString(),'R')
        dialog.edits['riichi'].setKeySequence(QKeySequence('Ctrl+R'));dialog.reject()
        self.assertEqual(self.controller.bindings,original)
        dialog.deleteLater();APP.processEvents()
        dialog=ShortcutDialog(self.controller);dialog.edits['riichi'].setKeySequence(QKeySequence('Ctrl+R'));dialog.save()
        self.assertNotEqual(dialog.result(),QDialog.DialogCode.Accepted)
        self.assertEqual(self.controller.bindings['riichi'],'Ctrl+R')
        dialog.deleteLater();APP.processEvents();self.activate()
        self.key(Qt.Key.Key_R,Qt.KeyboardModifier.ControlModifier);self.assertEqual(self.calls['riichi'],1)

    def test_malformed_import_falls_back_without_crashing_or_ambiguous_dispatch(self):
        for bad in (None,[],{'riichi':'F2'},{'riichi':'Ctrl+R, K'},{'unknown':'F1'}):
            self.model.settings['hotkeys']=bad;self.controller.refresh()
            self.assertEqual(self.controller.bindings,DEFAULT_HOTKEYS)
        self.activate();self.key(Qt.Key.Key_R);self.assertEqual(self.calls['riichi'],1)


if __name__=='__main__':unittest.main(verbosity=2)
