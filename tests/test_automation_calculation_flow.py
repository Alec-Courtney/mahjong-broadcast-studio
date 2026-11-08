"""Recognition/calculation drafts cross the desk boundary only after confirmation."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from PySide6.QtWidgets import QApplication
from automation_api import DraftError
from console import Console
from hand_calculation import parse_hand_tiles
from model import Model
from overlays import OverlayManager

APP = QApplication.instance() or QApplication([])


class AutomationCalculationFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'tests')
        self.model = Model(self.tmp.name)
        self.model.settings['show_overlays'] = False
        self.manager = OverlayManager(self.model)
        self.main = Console(self.model, self.manager)
        self.service = self.main.automation

    def tearDown(self):
        self.main.close()
        self.main.deleteLater()
        self.manager.close()
        APP.processEvents()
        self.tmp.cleanup()

    def tiles(self, text, player=1):
        return self.service.submit('tiles', player, {'tiles': parse_hand_tiles(text)}, source='offline-test')

    def test_ordinary_wait_draft_is_read_only_then_manual_flags_commit_together(self):
        before = copy.deepcopy(self.model.state)
        history = len(self.model.undo)
        tiles = self.tiles('123m456p12789s11z')
        analysis, draft = self.service.analyse_tiles(tiles['id'])
        self.assertEqual(analysis['no_yaku'], ['3s'])
        self.assertEqual(self.model.state, before)
        self.assertEqual(len(self.model.undo), history)
        self.service.confirm_waits(draft['id'], riichi=False, no_yaku=['3s'], furiten=True, remaining=0)
        player = self.model.state['players'][1]
        self.assertTrue(player['tenpai'])
        self.assertFalse(player['riichi'])
        self.assertEqual(player['waits'], ['3s'])
        self.assertEqual(player['waits_no_yaku'], ['3s'])
        self.assertTrue(player['furiten'])
        self.assertEqual(player['remaining'], 0)
        self.assertEqual(self.model.state['pot'], before['pot'])
        self.assertEqual(len(self.model.undo), history + 1)
        self.assertEqual(self.service.inbox.get(draft['id'])['status'], 'applied')

    def test_riichi_confirmation_pays_once_and_undo_restores_whole_update(self):
        before = copy.deepcopy(self.model.state)
        tiles = self.tiles('123m456p12789s11z')
        _, draft = self.service.analyse_tiles(tiles['id'], riichi=True)
        self.service.confirm_waits(draft['id'], riichi=True, no_yaku=[], furiten=False)
        player = self.model.state['players'][1]
        self.assertTrue(player['riichi'])
        self.assertTrue(player['tenpai'])
        self.assertEqual(player['waits_no_yaku'], [])
        self.assertEqual(player['score'], before['players'][1]['score'] - 1000)
        self.assertEqual(self.model.state['pot'], before['pot'] + 1000)
        paid = copy.deepcopy(self.model.state)
        with self.assertRaises(DraftError):
            self.service.confirm_waits(draft['id'], riichi=True)
        self.assertEqual(self.model.state, paid)
        self.model.history('undo')
        self.assertEqual(self.model.state, before)

    def test_win_load_reads_dora_counts_red_and_ura_without_settling(self):
        self.model.dora('5m')
        tiles = self.tiles('123406m123p4577s')
        before = copy.deepcopy(self.model.state)
        history = len(self.model.undo)
        draft = self.service.calculate_win(tiles['id'], '6s', riichi=True, ippatsu=True,
                                           ura_indicators=['4s'], loser=2)
        self.assertEqual(draft['data']['bonuses'], {'dora': 1, 'aka': 1, 'ura': 1})
        self.assertEqual(draft['data']['han'], 6)
        self.main.select(draft['player'])
        self.assertTrue(self.main.inline_automation.fill_win(draft))
        event, names = self.main.win_event()
        self.assertEqual((event['type'], event['winner'], event['loser']), ('ron', 1, 2))
        self.assertEqual((event['han'], event['fu']), (6, 30))
        self.assertEqual(set(names), {'立直', '一发', '平和'})
        self.assertEqual([control.value() for control in self.main.bonuses], [1, 1, 1])
        self.assertTrue(self.main.yaku_buttons['立直'].isChecked())
        preview = self.model.preview(event)
        self.assertEqual(preview['basePoints'], 12000)
        self.assertEqual(preview['basePoints'], draft['data']['calculation']['cost']['main'])
        self.assertEqual(self.model.state, before)
        self.assertEqual(len(self.model.undo), history)
        self.assertIsNone(self.model.last_info)
        self.assertTrue(self.main.settle_button.isEnabled())
        self.assertEqual(self.service.inbox.get(draft['id'])['status'], 'applied')

    def test_melds_and_actual_dora_are_read_from_current_context(self):
        self.model.meld(1, 'pon', ['5z'] * 3, 0)
        self.model.dora('5z')
        tiles = self.tiles('234m234p67s22z')
        analysis, waits = self.service.analyse_tiles(tiles['id'])
        self.assertEqual(analysis['expected_count'], 10)
        self.assertEqual(analysis['waits'], ['5s', '8s'])
        self.assertEqual(waits['data']['no_yaku'], [])
        draft = self.service.calculate_win(waits['id'], '8s', loser=2)
        self.assertEqual(draft['data']['yaku'], ['白'])
        self.assertEqual(draft['data']['bonuses']['dora'], 3)
        self.assertEqual(draft['data']['han'], 4)
        before = copy.deepcopy(self.model.state)
        self.main.select(draft['player'])
        self.assertTrue(self.main.inline_automation.fill_win(draft))
        event, _ = self.main.win_event()
        self.assertEqual(event['han'], 4)
        self.assertEqual(self.model.preview(event)['basePoints'], 8000)
        self.assertEqual(self.model.state, before)

    def test_tsumo_load_preserves_pinfu_twenty_fu_and_winner(self):
        tiles = self.tiles('123456m123p4577s', player=2)
        draft = self.service.calculate_win(tiles['id'], '6s', tsumo=True)
        self.main.select(draft['player'])
        self.assertTrue(self.main.inline_automation.fill_win(draft))
        event, names = self.main.win_event()
        self.assertEqual((event['type'], event['winner'], event['han'], event['fu']), ('tsumo', 2, 2, 20))
        self.assertEqual(set(names), {'门前清自摸和', '平和'})
        self.assertTrue(self.main.tsumo.isChecked())
        self.assertFalse(self.main.loser.isEnabled())
        self.assertEqual(self.model.state['phase'], 'playing')

    def test_kokushi_yakuman_can_load_zero_fu_calculation(self):
        tiles = self.tiles('19m19p19s1234567z')
        draft = self.service.calculate_win(tiles['id'], '1m', loser=0)
        self.assertEqual(draft['data']['yakuman'], 1)
        self.assertEqual(draft['data']['calculation']['fu'], 0)
        self.main.select(draft['player'])
        self.assertTrue(self.main.inline_automation.fill_win(draft))
        event, names = self.main.win_event()
        self.assertEqual(event['yakuman'], 1)
        self.assertEqual(names, ['国士无双'])
        self.assertEqual(self.model.preview(event)['basePoints'], 32000)
        self.assertEqual(self.model.state['phase'], 'playing')

    def test_double_yakuman_transfer_preserves_multiplier_until_roles_change(self):
        self.model.update(lambda state: state['rules'].update(doubleYakuman=True))
        tiles = self.tiles('19m19p19s1234567z')
        draft = self.service.calculate_win(tiles['id'], '1m', loser=0)
        self.assertEqual(draft['data']['yakuman'], 2)
        self.main.select(draft['player'])
        self.assertTrue(self.main.inline_automation.fill_win(draft))
        event, _ = self.main.win_event()
        self.assertEqual(event['yakuman'], 2)
        self.assertEqual(self.model.preview(event)['basePoints'], 64000)
        self.main.yaku_buttons['国士无双'].setChecked(False)
        self.main.yaku_buttons['大三元'].setChecked(True)
        self.assertEqual(self.main.win_event()[0]['yakuman'], 1)

    def test_invalid_or_stale_drafts_cannot_modify_current_hand(self):
        too_many = self.tiles('123456m123p45677s')
        before = copy.deepcopy(self.model.state)
        with self.assertRaises(ValueError):
            self.service.analyse_tiles(too_many['id'])
        self.assertEqual(self.model.state, before)
        good = self.tiles('123456m123p4577s')
        _, wait = self.service.analyse_tiles(good['id'])
        win_tiles = self.tiles('123456m123p4577s')
        win = self.service.calculate_win(win_tiles['id'], '6s', loser=0)
        self.model.dora('9m')
        current = copy.deepcopy(self.model.state)
        self.assertEqual(self.service.inbox.get(wait['id'])['status'], 'stale')
        with self.assertRaises(DraftError):
            self.service.confirm_waits(wait['id'])
        self.assertFalse(self.main.inline_automation.fill_win(win))
        with self.assertRaises(DraftError):
            self.service.calculate_win(good['id'], '6s')
        self.assertEqual(self.model.state, current)

    def test_bad_manual_wait_flags_and_no_yaku_win_remain_drafts(self):
        tiles = self.tiles('123m456p12789s11z')
        _, wait = self.service.analyse_tiles(tiles['id'])
        before = copy.deepcopy(self.model.state)
        with self.assertRaises(DraftError):
            self.service.confirm_waits(wait['id'], no_yaku=['7z'])
        with self.assertRaises(DraftError):
            self.service.calculate_win(wait['id'], '3s')
        self.assertEqual(self.service.inbox.get(wait['id'])['status'], 'pending')
        self.assertEqual(self.model.state, before)


if __name__ == '__main__':
    unittest.main()
