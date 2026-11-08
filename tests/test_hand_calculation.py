"""Contract tests for the UI-independent MahjongRepository adapter."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from hand_calculation import analyse_hand, calculate_win, parse_hand_tiles


def meld(kind, notation):
    return {'kind': kind, 'tiles': parse_hand_tiles(notation)}


class HandCalculationTests(unittest.TestCase):
    def test_two_sided_pinfu_and_read_only_snapshot(self):
        state = {'players': [{'melds': []} for _ in range(4)], 'dealer': 2,
                 'roundIndex': 4, 'rules': {'kiriageMangan': True}, 'doraIndicators': ['9p']}
        original = copy.deepcopy(state)
        result = analyse_hand('123456m123p4577s', state=state, player=1)
        self.assertTrue(result['tenpai'])
        self.assertEqual(result['shanten'], 0)
        self.assertEqual(result['waits'], ['3s', '6s'])
        self.assertEqual(result['no_yaku'], [])
        self.assertEqual(result['wait_details'][0]['ron']['player_wind'], 30)
        self.assertEqual(result['wait_details'][0]['ron']['round_wind'], 28)
        self.assertEqual(state, original)

    def test_shape_wait_without_ron_yaku_but_tsumo_legal(self):
        result = analyse_hand('123m456p12789s11z')
        self.assertEqual(result['waits'], ['3s'])
        self.assertEqual(result['no_yaku'], ['3s'])
        self.assertFalse(result['wait_details'][0]['ron']['valid'])
        self.assertTrue(result['wait_details'][0]['tsumo']['valid'])
        riichi = analyse_hand('123m456p12789s11z', riichi=True)
        self.assertEqual(riichi['no_yaku'], [])

    def test_dora_never_legalizes_no_yaku(self):
        result = calculate_win('123m456p12789s11z', '3s', dora_indicators=['9m'])
        self.assertFalse(result['valid'])
        self.assertEqual(result['error'], 'no_yaku')
        self.assertIsNone(result['event'])

    def test_open_hand_reads_yakuhai_meld_and_reduces_concealed_count(self):
        groups = [meld('pon', '555z')]
        analysis = analyse_hand('234m234p67s22z', melds=groups)
        self.assertEqual(analysis['expected_count'], 10)
        self.assertEqual(analysis['waits'], ['5s', '8s'])
        result = calculate_win('234m234p67s22z', '8s', melds=groups)
        self.assertEqual(result['yaku'], ['白'])
        self.assertEqual(result['han'], 1)
        self.assertTrue(result['opened'])
        with self.assertRaisesRegex(ValueError, '副露后不能立直'):
            calculate_win('234m234p67s22z', '8s', melds=groups, riichi=True)

    def test_open_tanyao_rule_is_respected(self):
        groups = [meld('chi', '234m')]
        yes = calculate_win('234p678s2267m', '8m', melds=groups)
        no = calculate_win('234p678s2267m', '8m', melds=groups, rules={'openTanyao': False})
        self.assertTrue(yes['valid'])
        self.assertIn('断么九', yes['yaku'])
        self.assertEqual(no['error'], 'no_yaku')

    def test_red_and_ura_bonuses_are_explicit_and_ura_requires_riichi(self):
        args = dict(dora_indicators=['4m'], ura_indicators=['4s'])
        result = calculate_win('123406m123p4577s', '6s', riichi=True, ippatsu=True, **args)
        self.assertTrue(result['valid'])
        self.assertEqual(result['bonuses'], {'dora': 1, 'aka': 1, 'ura': 1})
        self.assertEqual(result['han'], 6)
        self.assertIn('立直', result['yaku'])
        self.assertIn('一发', result['yaku'])
        ordinary = calculate_win('123406m123p4577s', '6s', **args)
        self.assertEqual(ordinary['bonuses']['ura'], 0)
        self.assertEqual(len(ordinary['warnings']), 1)

    def test_four_normal_fives_do_not_invent_a_red_bonus(self):
        result = calculate_win('34555m123p789s11z', '5m', riichi=True)
        self.assertTrue(result['valid'])
        self.assertEqual(result['bonuses']['aka'], 0)
        self.assertEqual(result['han'], 1)

    def test_closed_kan_stays_closed_and_all_kans_count_four_dora(self):
        for kind in ('ankan', 'daiminkan', 'kakan'):
            with self.subTest(kind=kind):
                result = calculate_win('234m234p67s22z', '8s', melds=[meld(kind, '5555z')],
                                       dora_indicators=['7z'], riichi=kind == 'ankan')
                self.assertTrue(result['valid'])
                self.assertEqual(result['bonuses']['dora'], 4)
                self.assertEqual(result['opened'], kind != 'ankan')
                if kind == 'ankan':
                    self.assertIn('立直', result['yaku'])

    def test_winds_follow_rotated_dealer_and_south_round(self):
        state = {'players': [{'melds': []} for _ in range(4)], 'dealer': 3,
                 'roundIndex': 5, 'doraIndicators': []}
        groups = [meld('pon', '222z')]
        result = calculate_win('234m678p67s11z', '8s', state=state, player=0, melds=groups)
        self.assertIn('自风', result['yaku'])
        self.assertIn('场风', result['yaku'])
        self.assertEqual(result['han'], 2)

    def test_seven_pairs_and_kokushi(self):
        pairs = analyse_hand('1199m2277p3355s7z')
        self.assertEqual(pairs['waits'], ['7z'])
        win = calculate_win('1199m2277p3355s7z', '7z')
        self.assertEqual(win['yaku'], ['七对子'])
        self.assertEqual(win['fu'], 25)
        orphans = analyse_hand('19m19p19s1234567z')
        self.assertEqual(len(orphans['waits']), 13)
        win = calculate_win('19m19p19s1234567z', '1m')
        self.assertEqual(win['yaku'], ['国士无双'])
        self.assertEqual(win['yakuman'], 1)
        self.assertEqual(win['han'], 0)
        double = calculate_win('19m19p19s1234567z', '1m', rules={'doubleYakuman': True})
        self.assertEqual(double['yakuman'], 2)

    def test_pinfu_and_seven_pairs_have_fixed_fu_for_each_win_method(self):
        for tsumo in (False,True):
            pinfu=calculate_win('123456m234p67s22p','8s',tsumo=tsumo)
            self.assertTrue(pinfu['valid']);self.assertIn('平和',pinfu['yaku'])
            self.assertEqual(pinfu['fu'],20 if tsumo else 30)
            pairs=calculate_win('1199m2277p3355s7z','7z',tsumo=tsumo)
            self.assertTrue(pairs['valid']);self.assertIn('七对子',pairs['yaku'])
            self.assertEqual(pairs['fu'],25)

    def test_win_tile_must_be_explicit_about_inclusion(self):
        hand = '123456m123p45677s'
        with self.assertRaisesRegex(ValueError, '已含和牌张'):
            calculate_win(hand, '6s')
        result = calculate_win(hand, '6s', win_tile_included=True)
        self.assertTrue(result['valid'])
        self.assertEqual(len(result['tiles']), 14)
        with self.assertRaisesRegex(ValueError, '不在识别手牌'):
            calculate_win(hand, '0s', win_tile_included=True)
        with self.assertRaisesRegex(ValueError, '摸牌后'):
            analyse_hand(hand)

    def test_copy_limits_include_reds_other_melds_and_indicators(self):
        with self.assertRaisesRegex(ValueError, '不能超过四张'):
            analyse_hand('05555m123p123s11z')
        state = {'players': [{'melds': []} for _ in range(4)], 'doraIndicators': ['1m']}
        state['players'][2]['melds'] = [meld('pon', '111m')]
        with self.assertRaisesRegex(ValueError, '不能超过四张'):
            analyse_hand('123456m123p4577s', state=state)

    def test_exhausted_waits_remain_structural_tenpai_but_real_win_is_rejected(self):
        state = {'players': [{'melds': []} for _ in range(4)], 'doraIndicators': ['3s']}
        state['players'][2]['melds'] = [meld('pon', '333s')]
        result = analyse_hand('123m456p12789s11z', state=state)
        self.assertTrue(result['tenpai'])
        self.assertEqual(result['waits'], ['3s'])
        self.assertEqual(result['exhausted_waits'], ['3s'])
        self.assertEqual(result['wait_details'][0]['known_remaining'], 0)
        self.assertEqual(result['no_yaku'], ['3s'])
        self.assertTrue(result['wait_details'][0]['tsumo']['valid'])
        with self.assertRaisesRegex(ValueError, '不能超过四张'):
            calculate_win('123m456p12789s11z', '3s', state=state, riichi=True)

    def test_wait_does_not_require_a_fifth_copy_already_in_own_hand(self):
        result = analyse_hand('3333s123m456p111z')
        self.assertNotIn('3s', result['waits'])

    def test_bad_melds_flags_and_formats_are_rejected(self):
        for group in (meld('chi', '123z'), meld('chi', '124m'), meld('pon', '123m'), meld('ankan', '111m')):
            with self.subTest(group=group), self.assertRaises(ValueError):
                analyse_hand('234m234p67s22z', melds=[group])
        for text in ('0z', '10z', '8z', '1x', 'abc'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_hand_tiles(text)
        with self.assertRaisesRegex(ValueError, '一发'):
            calculate_win('123456m123p4577s', '6s', ippatsu=True)


if __name__ == '__main__':
    unittest.main()
