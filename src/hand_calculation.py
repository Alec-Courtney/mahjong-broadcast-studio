"""Pure calculation adapter for MahjongRepository/mahjong.

Inputs and results contain only ordinary Python data. The adapter does not import
the desk, model, Qt, or recognition code, and never changes its input snapshot.
Concealed tiles exclude all declared melds, including concealed kans. Unless
``win_tile_included`` is explicit, a winning tile is supplied separately.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import re


TILE_TYPES = tuple(f'{n}{s}' for s in 'mps' for n in range(1, 10)) + tuple(f'{n}z' for n in range(1, 8))
ALL_TILES = frozenset(TILE_TYPES + ('0m', '0p', '0s'))
_YAKU_NAMES = {
    'Menzen Tsumo': '门前清自摸和', 'Riichi': '立直', 'Ippatsu': '一发',
    'Chankan': '抢杠', 'Rinshan Kaihou': '岭上开花', 'Haitei Raoyue': '海底摸月',
    'Houtei Raoyui': '河底捞鱼', 'Double Riichi': '两立直', 'Pinfu': '平和',
    'Tanyao': '断么九', 'Iipeiko': '一杯口', 'Yakuhai (haku)': '白',
    'Yakuhai (hatsu)': '发', 'Yakuhai (chun)': '中', 'Sanshoku Doujun': '三色同顺',
    'Ittsu': '一气通贯', 'Chantai': '混全带幺九', 'Honroutou': '混老头',
    'Toitoi': '对对和', 'San Ankou': '三暗刻', 'San Kantsu': '三杠子',
    'Sanshoku Doukou': '三色同刻', 'Chiitoitsu': '七对子', 'Shou Sangen': '小三元',
    'Honitsu': '混一色', 'Junchan': '纯全带幺九', 'Ryanpeikou': '二杯口',
    'Chinitsu': '清一色', 'Kokushi Musou': '国士无双',
    'Kokushi Musou Juusanmen Matchi': '国士无双', 'Chuuren Poutou': '九莲宝灯',
    'Daburu Chuuren Poutou': '九莲宝灯', 'Suu Ankou': '四暗刻',
    'Suu Ankou Tanki': '四暗刻', 'Daisangen': '大三元', 'Shousuushii': '小四喜',
    'Dai Suushii': '大四喜', 'Ryuuiisou': '绿一色', 'Suu Kantsu': '四杠子',
    'Tsuu Iisou': '字一色', 'Chinroutou': '清老头', 'Tenhou': '天和', 'Chiihou': '地和',
}
_ERROR_MESSAGES = {
    'no_yaku': '牌型完整，但没有可荣和的役；宝牌不能单独成为役。',
    'hand_not_winning': '加入和牌张后未组成完整和牌。',
    'winning_tile_not_in_hand': '和牌张不在手牌中。',
    'open_hand_riichi_not_allowed': '副露后不能立直。',
    'ippatsu_without_riichi_not_allowed': '一发必须同时指定立直。',
}


def parse_hand_tiles(value: Sequence[str] | str) -> list[str]:
    """Accept tile codes or familiar grouped notation, without importing the UI."""
    if isinstance(value, str):
        text = re.sub(r'[\s,，]+', '', value.lower())
        if not text:
            return []
        if not re.fullmatch(r'(?:[0-9]+[mps]|[1-7]+z)+', text):
            raise ValueError('牌面格式无效，请使用 123m456p789s11z。')
        value = [n + suit for numbers, suit in re.findall(r'([0-9]+)([mpsz])', text) for n in numbers]
    if not isinstance(value, Sequence):
        raise ValueError('牌面必须是牌面列表或 mpsz 记法。')
    tiles = list(value)
    if any(not isinstance(tile, str) or tile not in ALL_TILES for tile in tiles):
        raise ValueError('牌面格式无效。')
    return tiles


def _tile_index(tile: str) -> int:
    return 'mpsz'.index(tile[1]) * 9 + (4 if tile[0] == '0' else int(tile[0]) - 1)


def _normalized(tile: str) -> str:
    return '5' + tile[1] if tile[0] == '0' else tile


def _counts(tiles: Sequence[str]) -> list[int]:
    out = [0] * 34
    for tile in tiles:
        out[_tile_index(tile)] += 1
    return out


def _library():
    try:
        from mahjong.agari import Agari
        from mahjong.hand_calculating.hand import HandCalculator
        from mahjong.hand_calculating.hand_config import HandConfig, OptionalRules
        from mahjong.hand_calculating.scores import ScoresCalculator
        from mahjong.meld import Meld
        from mahjong.shanten import Shanten
    except ImportError as error:
        raise RuntimeError('未安装听胡牌计算组件，请安装 requirements-recognition.txt 中的 mahjong。') from error
    return Agari, HandCalculator, HandConfig, OptionalRules, ScoresCalculator, Meld, Shanten


def _wind(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        if value not in ('1z', '2z', '3z', '4z'):
            raise ValueError('风牌必须为 1z 至 4z。')
        return _tile_index(value)
    if type(value) is not int or value not in range(27, 31):
        raise ValueError('风牌索引必须为 27 至 30。')
    return value


def _flag(rules, *names, default):
    for name in names:
        if name in rules:
            return bool(rules[name])
    return default


def _context(*, melds, state, player, dora_indicators, player_wind, round_wind, rules, visible_tiles):
    state = state or {}
    if type(player) is not int or player not in range(4):
        raise ValueError('选手必须是 0 至 3 的席位。')
    players = state.get('players') or []
    if players and (not isinstance(players, Sequence) or len(players) != 4):
        raise ValueError('对局快照须包含四位选手。')
    if melds is None:
        melds = players[player].get('melds', []) if players else []
    if not isinstance(melds, Sequence) or len(melds) > 4:
        raise ValueError('每家最多四组副露或暗杠。')
    groups = []
    for meld in melds:
        if not isinstance(meld, Mapping):
            raise ValueError('副露资料无效。')
        kind = meld.get('kind')
        if kind not in ('chi', 'pon', 'daiminkan', 'ankan', 'kakan'):
            raise ValueError('未知副露类型。')
        tiles = parse_hand_tiles(meld.get('tiles', []))
        is_kan = kind in ('daiminkan', 'ankan', 'kakan')
        if len(tiles) != (4 if is_kan else 3):
            raise ValueError('副露张数与类型不符。')
        numbers = sorted(_tile_index(tile) for tile in tiles)
        if kind == 'chi':
            if numbers[0] >= 27 or numbers[0] // 9 != numbers[-1] // 9 or numbers != list(range(numbers[0], numbers[0] + 3)):
                raise ValueError('吃牌必须是同一花色的连续三张。')
        elif len(set(numbers)) != 1:
            raise ValueError('碰杠牌必须为同一种牌。')
        groups.append({'kind': kind, 'tiles': tiles})
    indicators = parse_hand_tiles(state.get('doraIndicators', []) if dora_indicators is None else dora_indicators)
    if len(indicators) > 5:
        raise ValueError('明宝牌指示牌最多五张。')
    other_tiles = parse_hand_tiles(visible_tiles or [])
    if players:
        for index, other in enumerate(players):
            if index != player:
                for meld in other.get('melds', []):
                    other_tiles.extend(parse_hand_tiles(meld.get('tiles', [])))
    context_rules = dict(state.get('rules') or {})
    context_rules.update(rules or {})
    dealer = state.get('dealer', 0)
    round_index = state.get('roundIndex', 0)
    if type(dealer) is not int or dealer not in range(4):
        raise ValueError('庄家席位无效。')
    if type(round_index) is not int or round_index not in range(16):
        raise ValueError('局风索引无效。')
    return {
        'melds': groups, 'dora_indicators': indicators, 'other_tiles': other_tiles,
        'player_wind': _wind(player_wind, 27 + (player - dealer) % 4),
        'round_wind': _wind(round_wind, 27 + round_index // 4), 'rules': context_rules,
        'player': player,
    }


def _known_tiles(concealed, context, ura=()):
    all_tiles = list(concealed) + [t for meld in context['melds'] for t in meld['tiles']]
    all_tiles += context['dora_indicators'] + context['other_tiles'] + list(ura)
    counts = Counter(_normalized(tile) for tile in all_tiles)
    for tile, count in counts.items():
        if count > 4:
            raise ValueError(f'{tile} 已出现 {count} 张，手牌、副露和宝牌指示牌合计不能超过四张。')
    return counts


def _calculate(concealed, win_index, context, *, tsumo, riichi, ippatsu, ura, special_flags,
               validate_physical=True):
    _, HandCalculator, HandConfig, OptionalRules, ScoresCalculator, Meld, _ = _library()
    opened = any(meld['kind'] != 'ankan' for meld in context['melds'])
    if riichi and opened:
        raise ValueError('副露后不能立直。')
    if ippatsu and not riichi:
        raise ValueError('一发必须同时指定立直。')
    if validate_physical:
        _known_tiles(concealed, context, ura)
    # Hand IDs are unique across concealed tiles and declared groups. Indicator
    # IDs encode their types independently: hypothetical evaluation of an
    # exhausted wait must not turn a fifth copy into the next tile's ID. Real
    # winning submissions still validate every visible copy before calculation.
    # Aka are counted from explicit 0 codes below, avoiding an invented red five
    # when four regular fives were entered.
    used = Counter()

    def ids(tiles):
        result = []
        for tile in tiles:
            kind = _tile_index(tile)
            result.append(kind * 4 + used[kind])
            used[kind] += 1
        return result

    concealed_ids = ids(concealed)
    all_ids = list(concealed_ids)
    declared = []
    for group in context['melds']:
        group_ids = ids(group['tiles'])
        all_ids.extend(group_ids)
        kind = {'chi': Meld.CHI, 'pon': Meld.PON, 'daiminkan': Meld.KAN, 'ankan': Meld.KAN, 'kakan': Meld.SHOUMINKAN}[group['kind']]
        declared.append(Meld(meld_type=kind, tiles=group_ids, opened=group['kind'] != 'ankan'))
    dora_ids = [_tile_index(tile) * 4 for tile in context['dora_indicators']]
    ura_ids = [_tile_index(tile) * 4 for tile in ura]
    rules = context['rules']
    aka_enabled = _flag(rules, 'has_aka_dora', 'akaDora', 'redDora', default=True)
    options = OptionalRules(
        has_open_tanyao=_flag(rules, 'has_open_tanyao', 'openTanyao', 'kuitan', default=True),
        has_aka_dora=False,
        has_double_yakuman=_flag(rules, 'has_double_yakuman', 'doubleYakuman', default=False),
        kazoe_limit=HandConfig.KAZOE_LIMITED if _flag(rules, 'kazoeYakuman', default=False) else HandConfig.KAZOE_SANBAIMAN,
        kiriage=_flag(rules, 'kiriageMangan', 'kiriage', default=True),
    )
    flag_names = {
        'rinshan': 'is_rinshan', 'chankan': 'is_chankan', 'haitei': 'is_haitei',
        'houtei': 'is_houtei', 'double_riichi': 'is_daburu_riichi',
        'tenhou': 'is_tenhou', 'chiihou': 'is_chiihou',
    }
    special_flags = special_flags or {}
    if any(key not in flag_names for key in special_flags):
        raise ValueError('未知的特殊和牌条件。')
    extra = {flag_names[key]: bool(value) for key, value in special_flags.items()}
    if extra.get('is_daburu_riichi') and not riichi:
        raise ValueError('两立直必须同时指定立直。')
    config = HandConfig(
        is_tsumo=bool(tsumo), is_riichi=bool(riichi), is_ippatsu=bool(ippatsu),
        player_wind=context['player_wind'], round_wind=context['round_wind'],
        options=options, **extra,
    )
    response = HandCalculator.estimate_hand_value(
        all_ids, concealed_ids[win_index], melds=declared, dora_indicators=dora_ids,
        config=config, ura_dora_indicators=ura_ids,
    )
    base = {
        'valid': not bool(response.error), 'error': response.error,
        'message': _ERROR_MESSAGES.get(response.error, response.error or ''),
        'han': 0, 'fu': 0, 'yakuman': 0, 'yaku': [],
        'bonuses': {'dora': 0, 'aka': 0, 'ura': 0}, 'cost': {},
        'details': [], 'fu_details': [], 'opened': opened,
        'player_wind': context['player_wind'], 'round_wind': context['round_wind'],
    }
    if response.error:
        return base
    details = []
    bonuses = dict(base['bonuses'])
    yaku = []
    yakuman = 0
    for role in response.yaku or []:
        han = role.han_open if opened else role.han_closed
        if role.name in ('Dora', 'Aka Dora', 'Ura Dora'):
            bonuses[{'Dora': 'dora', 'Aka Dora': 'aka', 'Ura Dora': 'ura'}[role.name]] += han
            continue
        name = ('自风' if role.name.startswith('Yakuhai (seat wind') else
                '场风' if role.name.startswith('Yakuhai (round wind') else _YAKU_NAMES.get(role.name))
        if name is None:
            raise RuntimeError(f'计算组件返回尚未接入的役种：{role.name}')
        if name not in yaku:
            yaku.append(name)
        multiplier = max(1, han // 13) if role.is_yakuman else 0
        yakuman += multiplier
        details.append({'name': name, 'source_name': role.name, 'han': han, 'yakuman': multiplier})
    hand_tiles = list(concealed) + [t for group in context['melds'] for t in group['tiles']]
    bonuses['aka'] = sum(tile[0] == '0' for tile in hand_tiles) if aka_enabled and not yakuman else 0
    han = (response.han or 0) + bonuses['aka']
    cost = ScoresCalculator.calculate_scores(han, response.fu or 0, config, bool(yakuman))
    base.update(
        han=0 if yakuman else han, fu=response.fu or 0, yakuman=yakuman,
        yaku=yaku, bonuses=bonuses, cost=dict(cost), details=details,
        fu_details=[dict(item) for item in response.fu_details or []],
    )
    return base


def calculate_win(concealed_tiles, win_tile, *, melds=None, state=None, player=0,
                  tsumo=False, riichi=False, ippatsu=False, ura_indicators=None,
                  win_tile_included=False, dora_indicators=None, player_wind=None,
                  round_wind=None, rules=None, visible_tiles=None, special_flags=None):
    """Calculate a win draft. Bad input raises ValueError; no yaku is ``valid=False``.

    ``state`` is an optional plain read-only snapshot, used only to fill omitted
    melds, dora indicators, winds and rules. Explicit generic arguments override
    those values. No points, statuses or broadcast fields are changed here.
    """
    concealed = parse_hand_tiles(concealed_tiles)
    winning = parse_hand_tiles([win_tile])
    context = _context(melds=melds, state=state, player=player, dora_indicators=dora_indicators,
                       player_wind=player_wind, round_wind=round_wind, rules=rules, visible_tiles=visible_tiles)
    count = 14 - 3 * len(context['melds'])
    if win_tile_included:
        if len(concealed) != count:
            raise ValueError(f'和牌手牌应为 {count} 张（包含和牌张，不含副露及暗杠）。')
        if win_tile not in concealed:
            raise ValueError('指定的和牌张不在识别手牌中；红五与普通五须分别指定。')
        win_index = len(concealed) - 1 - concealed[::-1].index(win_tile)
    else:
        if len(concealed) != count - 1:
            raise ValueError(f'和牌前手牌应为 {count - 1} 张；若已含和牌张，请选择“手牌包含和牌张”。')
        concealed += winning
        win_index = len(concealed) - 1
    ura = parse_hand_tiles(ura_indicators or [])
    if len(ura) > 5:
        raise ValueError('里宝牌指示牌最多五张。')
    result = _calculate(concealed, win_index, context, tsumo=tsumo, riichi=riichi,
                        ippatsu=ippatsu, ura=ura, special_flags=special_flags)
    result['tiles'] = concealed
    result['win_tile'] = win_tile
    result['warnings'] = ['未立直，里宝牌不会计入。'] if ura and not riichi else []
    result['event'] = {
        'type': 'tsumo' if tsumo else 'ron', 'winner': player,
        'han': result['han'], 'fu': result['fu'], 'yakuman': result['yakuman'],
    } if result['valid'] else None
    return result


def analyse_hand(concealed_tiles, *, melds=None, state=None, player=0, riichi=False,
                 dora_indicators=None, player_wind=None, round_wind=None,
                 rules=None, visible_tiles=None):
    """Find shape waits and distinguish ron/tsumo yaku, without declaring riichi.

    Only a 13 - 3*meld_count post-discard concealed hand is accepted. A drawn
    14th tile is never silently discarded. Exhausted waits remain structural
    waits and are separately identified; unseen discards are not guessed and
    furiten remains manual. A fifth copy already inside one's own hand/melds
    cannot be a structural wait.
    """
    Agari, _, _, _, _, _, Shanten = _library()
    concealed = parse_hand_tiles(concealed_tiles)
    context = _context(melds=melds, state=state, player=player, dora_indicators=dora_indicators,
                       player_wind=player_wind, round_wind=round_wind, rules=rules, visible_tiles=visible_tiles)
    expected = 13 - 3 * len(context['melds'])
    if len(concealed) != expected:
        raise ValueError(f'听牌分析需要 {expected} 张手牌（不含副露及暗杠）；摸牌后请先指定弃牌。')
    known = _known_tiles(concealed, context)
    own = Counter(_normalized(tile) for tile in concealed + [t for group in context['melds'] for t in group['tiles']])
    if riichi and any(group['kind'] != 'ankan' for group in context['melds']):
        raise ValueError('副露后不能立直。')
    counts = _counts(concealed)
    shanten = Shanten.calculate_shanten(counts, use_chiitoitsu=not context['melds'], use_kokushi=not context['melds'])
    waits = []
    no_yaku = []
    exhausted = []
    details = []
    for tile in TILE_TYPES:
        index = _tile_index(tile)
        if own[tile] >= 4:
            continue
        counts[index] += 1
        complete = Agari.is_agari(counts)
        counts[index] -= 1
        if not complete:
            continue
        waits.append(tile)
        if known[tile] >= 4:
            exhausted.append(tile)
        tiles = concealed + [tile]
        ron = _calculate(tiles, len(tiles) - 1, context, tsumo=False, riichi=riichi,
                         ippatsu=False, ura=[], special_flags=None, validate_physical=known[tile] < 4)
        tsumo = _calculate(tiles, len(tiles) - 1, context, tsumo=True, riichi=riichi,
                           ippatsu=False, ura=[], special_flags=None, validate_physical=known[tile] < 4)
        if not ron['valid']:
            no_yaku.append(tile)
        details.append({'tile': tile, 'ron': ron, 'tsumo': tsumo,
                        'known_remaining': max(0, 4 - known[tile])})
    return {
        'tiles': concealed, 'shanten': shanten, 'tenpai': bool(waits),
        'waits': waits, 'no_yaku': no_yaku, 'wait_details': details, 'exhausted_waits': exhausted,
        'expected_count': expected, 'opened': any(group['kind'] != 'ankan' for group in context['melds']),
        'warnings': ['振听与未记录的河牌信息需要导播确认。'],
    }


# Both spellings are useful to alternative providers and external integrations.
analyze_hand = analyse_hand
