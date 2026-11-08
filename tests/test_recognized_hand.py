"""Player-row feedback stays compact and requires an intentional correction."""
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from recognized_hand import RecognizedHandWidget, compact_hand, parse_hand_text

APP = QApplication.instance() or QApplication([])
HAND = parse_hand_text('123m405p678s12344z')


class RecognizedHandTests(unittest.TestCase):
    def setUp(self):
        self.widget = RecognizedHandWidget(2)
        self.widget.resize(280, 52)
        self.widget.show()
        APP.processEvents()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        APP.processEvents()

    def observation(self):
        self.widget.set_observation(HAND, source='replacement-recognizer', confidence=.89,
                                    status='已听五索 · 待确认', pending=True)
        self.widget.resize(280, self.widget.sizeHint().height())
        APP.processEvents()

    def test_empty_row_uses_one_hint_line_without_an_empty_tile_strip(self):
        self.assertEqual(self.widget.tiles, [])
        self.assertLessEqual(self.widget.sizeHint().height(), 24)
        self.assertEqual(self.widget.tile_rects(), [])
        sources = []
        self.widget.source_requested.connect(sources.append)
        self.widget.source_button.click()
        self.assertEqual(sources, [2])

    def test_fourteen_actual_faces_fit_beneath_a_narrow_player_row(self):
        self.observation()
        rects = self.widget.tile_rects()
        self.assertEqual(len(rects), 14)
        self.assertEqual(self.widget.tiles, HAND)
        self.assertIn('0p', self.widget.tiles)
        self.assertTrue(all(rect.width() >= 17 and rect.height() >= 24 for rect in rects))
        self.assertTrue(all(rect.top() == 0 for rect in rects))
        self.assertLess(rects[-1].right(), self.widget.source_button.x())
        self.assertLessEqual(self.widget.sizeHint().height(), 48)
        image = self.widget.grab().toImage()
        bright = 0
        red = 0
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                bright += color.red() > 205 and color.green() > 200 and color.blue() > 195
                red += color.red() > color.green() * 1.4 and color.red() > color.blue() * 1.3
        self.assertGreater(bright, 1500)
        self.assertGreater(red, 3)
        self.assertIn('89%', self.widget.toolTip())
        self.assertIn('replacement-recognizer', self.widget.toolTip())

    def test_genuinely_small_width_wraps_without_clipping_faces(self):
        self.observation()
        self.widget.resize(210, 90)
        APP.processEvents()
        rects = self.widget.tile_rects()
        self.assertGreater(len({rect.top() for rect in rects}), 1)
        self.assertTrue(all(rect.left() >= 0 and rect.right() <= self.widget.source_button.x()
                            for rect in rects))
        self.assertLessEqual(self.widget.sizeHint().height(), 82)
        self.widget.begin_tile_edit(0)
        self.widget.resize(210, self.widget.sizeHint().height())
        self.assertTrue(all(control.geometry().right() < self.widget.width() and control.geometry().bottom() < self.widget.height()
                            for control in self.widget.edit_buttons.values()))
        self.widget.cancel_edit()
        self.widget.resize(420, 55)
        APP.processEvents()
        self.assertTrue(all(rect.top() == 0 for rect in self.widget.tile_rects()))
        self.assertGreaterEqual(self.widget.tile_rects()[0].width(), 22)

    def test_unknown_positions_and_stale_results_are_visibly_retained(self):
        self.widget.set_observation(['1m', None, '0p'], status='识别需核对', stale=True)
        self.assertEqual(self.widget.tiles, ['1m', '0p', None])
        self.assertEqual(len(self.widget.tile_rects()), 3)
        self.assertIn('已过期', self.widget.toolTip())
        self.assertIn('?', compact_hand(self.widget.tiles))
        self.assertFalse(self.widget.grab().isNull())
        self.widget.begin_tile_edit(2)
        self.widget.replace_selected_tile('6z')
        self.assertTrue(self.widget.commit_tile_edit())
        self.assertEqual(self.widget.tiles, ['1m', '0p', '6z'])

    def test_count_mismatch_is_not_hidden_by_truncating_the_observation(self):
        observed = HAND + ['9p']
        self.widget.set_observation(observed, status='牌数异常 · 请核对')
        self.assertCountEqual(self.widget.tiles, observed)
        self.assertEqual(len(self.widget.tile_rects()), 15)

    def test_enter_confirms_a_correction_but_focus_changes_do_not(self):
        self.observation()
        changes = []
        self.widget.hand_edited.connect(lambda player, tiles: changes.append((player, tiles)))
        self.widget.begin_edit()
        self.widget.editor.setText('135m055p22z')
        self.widget.source_button.setFocus()
        APP.processEvents()
        self.assertEqual(changes, [])
        self.assertTrue(self.widget.editing)
        QTest.keyClick(self.widget.editor, Qt.Key.Key_Return)
        self.assertEqual(changes, [(2, parse_hand_text('135m055p22z'))])
        self.assertFalse(self.widget.editing)
        self.assertEqual(self.widget.source, '手动修正')

    def test_click_edit_replaces_adds_deletes_and_commits_only_once(self):
        self.observation()
        changes, finished = [], []
        self.widget.hand_edited.connect(lambda player, tiles: changes.append((player, tiles)))
        self.widget.edit_finished.connect(lambda *args: finished.append(args))
        QTest.mouseClick(self.widget, Qt.MouseButton.LeftButton, pos=self.widget.tile_rects()[4].center().toPoint())
        self.assertTrue(self.widget.is_tile_editing)
        self.assertEqual(self.widget.selected_index, 4)
        self.assertTrue(self.widget.replace_selected_tile('9p'))
        self.assertEqual(self.widget.tiles, HAND)
        self.assertEqual(self.widget.draft_tiles[4], '9p')
        self.assertFalse(self.widget.append_mode())
        self.assertTrue(self.widget.delete_selected_tile())
        self.assertTrue(self.widget.append_mode())
        self.assertTrue(self.widget.replace_selected_tile('0p'))
        self.assertEqual(len(self.widget.draft_tiles), 14)
        self.assertEqual(changes, [])
        self.widget.set_observation(['2m'] * 3, status='new camera result')
        self.assertEqual(self.widget.tiles, HAND)
        self.widget.resize(280, self.widget.sizeHint().height())
        self.assertTrue(all(control.geometry().bottom() < self.widget.height() for control in self.widget.edit_buttons.values()))
        self.assertTrue(self.widget.commit_tile_edit())
        self.assertEqual(len(changes), 1)
        self.assertCountEqual(changes[0][1], HAND)
        self.assertEqual(finished, [(2, True)])
        self.assertFalse(self.widget.editing)
        self.assertLessEqual(self.widget.sizeHint().height(), 48)

    def test_incoming_frames_do_not_overwrite_an_operator_edit_and_escape_applies_latest(self):
        self.observation()
        self.widget.begin_edit()
        self.widget.editor.setText('123m')
        self.widget.set_observation(['9s'] * 3, status='未听牌')
        self.assertEqual(self.widget.editor.text(), '123m')
        self.assertEqual(self.widget.tiles, HAND)
        QTest.keyClick(self.widget.editor, Qt.Key.Key_Escape)
        self.assertFalse(self.widget.editing)
        self.assertEqual(self.widget.tiles, ['9s'] * 3)
        self.assertEqual(self.widget.status, '未听牌')
        self.widget.begin_tile_edit(0)
        self.widget.replace_selected_tile('4m')
        self.widget.cancel_edit()
        self.assertEqual(self.widget.tiles, ['9s'] * 3)
        self.widget.begin_tile_edit(0)
        self.widget.replace_selected_tile('4m')
        self.widget.set_observation(['7z', '2p', '0m', '5m', '1m'], status='latest')
        QTest.keyClick(self.widget, Qt.Key.Key_Escape)
        self.assertEqual(self.widget.tiles, ['1m', '0m', '5m', '2p', '7z'])

    def test_invalid_editor_text_is_not_emitted_and_clear_discards_old_corrections(self):
        self.observation()
        changes = []
        self.widget.hand_edited.connect(lambda *args: changes.append(args))
        self.widget.begin_edit()
        self.widget.editor.setText('8z')
        QTest.keyClick(self.widget.editor, Qt.Key.Key_Return)
        self.assertTrue(self.widget.editing)
        self.assertEqual(changes, [])
        self.widget.set_observation(['9p'], stale=True)
        self.widget.clear()
        self.assertFalse(self.widget.editing)
        self.assertEqual(self.widget.tiles, [])
        QTest.keyClick(self.widget.editor, Qt.Key.Key_Return)
        self.assertEqual(changes, [])

    def test_compact_editor_preserves_order_and_red_fives(self):
        source = ['1m', '2m', '0p', '5p', '9m', '1z']
        self.assertEqual(parse_hand_text(compact_hand(source)), source)
        for text in ('', '8z', '1x', '123456789m123456p'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_hand_text(text)


if __name__ == '__main__':
    unittest.main()
