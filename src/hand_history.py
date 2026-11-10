"""Editable hand ledger. Reuse the payment engine and preserve operator corrections."""
from __future__ import annotations

import copy


def settlement_context(state):
    """Capture the table immediately before payment, including paid deposits."""
    result = state.get('lastResult') if state['phase'] != 'playing' else None
    scores = [p['score'] for p in state['players']]
    pot = state['pot']
    if result:
        scores = [score - delta for score, delta in zip(scores, result['deltas'])]
        pot += result['potBefore'] - result['potAfter']
    existing = next((h for h in state['broadcast']['hands']
                     if h['id'] == state['broadcast']['current_hand']), {})
    paid = existing.get('context', {}).get('riichi_paid') if result else None
    return dict(scores=scores, pot=pot, round_index=state['roundIndex'],
                dealer=state['dealer'], honba=state['honba'],
                riichi_paid=list(paid) if paid is not None else [p['riichi'] for p in state['players']],
                riichi=[p['riichi'] for p in state['players']],
                melds=copy.deepcopy([p['melds'] for p in state['players']]),
                dora_indicators=state['doraIndicators'][:], rules=copy.deepcopy(state['rules']))


def context_for(hand, state):
    if 'context' in hand:
        return copy.deepcopy(hand['context'])
    # Older sessions contain payment facts, but no yaku or full hand snapshot.
    result = hand['result']
    riichi = [bool(p.get('riichi')) for p in hand['players']]
    return dict(scores=result['beforeScores'][:], pot=result['potBefore'],
                round_index=hand['round_index'], honba=hand['honba'], dealer=result['dealer'],
                riichi_paid=riichi[:], riichi=riichi, melds=[[] for _ in range(4)],
                dora_indicators=[], rules=copy.deepcopy(state['rules']))


def event_for(hand):
    if 'event' in hand:
        return copy.deepcopy(hand['event'])
    result = hand['result']
    if hand['kind'] == 'draw':
        return dict(type='draw', tenpai=result['tenpai'][:])
    return dict(type=hand['kind'], winner=result['winner'], loser=result['loser'],
                han=result['han'], fu=result['fu'], yakuman=result.get('yakuman', 0))


def hand_deltas(hand):
    paid = hand.get('context', {}).get('riichi_paid', [bool(p.get('riichi')) for p in hand['players']])
    before = hand.get('context', {}).get('scores', hand['before_scores'])
    return [after - score - 1000 * flag for after, score, flag
            in zip(hand['after_scores'], before, paid)]


def validate_context(context):
    def integer(value, lo, hi, unit=1):
        return isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi and value % unit == 0
    if not isinstance(context, dict):
        raise ValueError('小局场况格式无效')
    if not isinstance(context.get('scores'), list) or len(context['scores']) != 4 or any(
            not integer(score, -1000000000, 1000000000, 100) for score in context['scores']):
        raise ValueError('结算前点数须为四个百点整数')
    for key in ('riichi', 'riichi_paid'):
        if not isinstance(context.get(key), list) or len(context[key]) != 4 or any(type(flag) is not bool for flag in context[key]):
            raise ValueError('立直交棒记录格式无效')
    for key, hi, unit in (('pot', 1000000000, 1000), ('round_index', 7, 1), ('dealer', 3, 1), ('honba', 10000, 1)):
        if not integer(context.get(key), 0, hi, unit):
            raise ValueError('小局本场、供托或座位格式无效')
    if not isinstance(context.get('melds'), list) or len(context['melds']) != 4 or any(not isinstance(m, list) for m in context['melds']):
        raise ValueError('小局副露记录格式无效')
    if not isinstance(context.get('rules'), dict) or not isinstance(context.get('dora_indicators'), list):
        raise ValueError('小局计分上下文格式无效')


def _table_for(state, hand, context):
    # Avoid copying the event archive into each individual engine calculation.
    table = {key: copy.deepcopy(value) for key, value in state.items() if key != 'broadcast'}
    table.update(phase='playing', lastResult=None, finalRanking=None,
                 roundIndex=context['round_index'], dealer=context['dealer'], honba=context['honba'],
                 pot=context['pot'], rules=copy.deepcopy(context['rules']),
                 doraIndicators=context['dora_indicators'][:],
                 accountingTotal=sum(context['scores']) + context['pot'])
    for i, player in enumerate(table['players']):
        player.update(id=hand['players'][i]['id'], name=hand['players'][i]['name'],
                      score=context['scores'][i], riichi=context['riichi'][i],
                      melds=copy.deepcopy(context['melds'][i]), tenpai=False, waits=[], remaining=None)
        player.pop('waits_no_yaku', None)
    return table


def _caption(info, event, result, players):
    if event['type'] == 'draw':
        return None
    info = copy.deepcopy(info or {})
    winner = event['winner']
    info.update(winner=winner, name=players[winner]['name'], kind=event['type'],
                han=result['han'], fu=result['fu'], yakuman=result['yakuman'],
                limit=result['limit'], base_points=result['basePoints'])
    info.setdefault('yaku', [])
    return info


def revise(state, engine, hand_id, patch):
    """Change one hand and replay the remaining ledger without erasing manual gaps.

    Point/pot gaps between saved hands represent operator corrections and are
    retained. Automatically advanced round fields follow corrected results;
    explicitly overridden fields keep their recorded values.
    """
    hands = state['broadcast']['hands']
    index = next((i for i, hand in enumerate(hands) if hand['id'] == hand_id), None)
    if index is None:
        raise ValueError('该小局记录已变化，请重新打开')
    if not isinstance(patch, dict) or set(patch) - {'context', 'event', 'win_info', 'manual_deltas', 'manual_pot_after'}:
        raise ValueError('小局更正格式无效')
    original = copy.deepcopy(hands)
    carry = [0] * 4
    pot_carry = 0
    previous_old = previous_new = None
    for i in range(index, len(hands)):
        old = original[i]
        hand = copy.deepcopy(old)
        context = context_for(old, state)
        context['scores'] = [score + delta for score, delta in zip(context['scores'], carry)]
        context['pot'] += pot_carry
        if previous_old:
            for key, field in (('round_index', 'roundIndex'), ('dealer', 'dealer'), ('honba', 'honba')):
                if context[key] == previous_old['result']['next'][field]:
                    context[key] = previous_new['result']['next'][field]
        event = event_for(old)
        info = old.get('win_info')
        if i == index:
            edits = patch.get('context', {})
            if not isinstance(edits, dict) or set(edits) - {'scores', 'pot', 'round_index', 'dealer', 'honba', 'riichi_paid', 'riichi', 'noten_payment'}:
                raise ValueError('可修改的小局场况字段无效')
            # Toggling a deposit changes both the payer and the table pot. A
            # supplied score/pot is an explicit replacement of that auto value.
            paid = edits.get('riichi_paid', context['riichi_paid'])
            if not isinstance(paid, list) or len(paid) != 4 or any(type(flag) is not bool for flag in paid):
                raise ValueError('立直交棒记录格式无效')
            changes = [1000 * (int(a) - int(b)) for a, b in zip(paid, context['riichi_paid'])]
            context['scores'] = [score - delta for score, delta in zip(context['scores'], changes)]
            context['pot'] += sum(changes)
            if 'riichi_paid' in edits and 'riichi' not in edits:
                context['riichi'] = paid[:]
            for key, value in edits.items():
                if key == 'noten_payment':
                    context['rules']['notenPayment'] = value
                else:
                    context[key] = copy.deepcopy(value)
            event = copy.deepcopy(patch.get('event', event))
            info = patch.get('win_info', info)
        if not isinstance(event, dict) or event.get('type') not in ('ron', 'tsumo', 'draw'):
            raise ValueError('小局结果须为荣和、自摸或流局')
        event.pop('id', None)
        if info is not None and (not isinstance(info, dict) or not isinstance(info.get('yaku', []), list)
                or any(not isinstance(label, str) for label in info.get('yaku', []))):
            raise ValueError('役种记录格式无效')
        validate_context(context)
        calculated = engine.apply(_table_for(state, hand, context), event)
        manual_deltas = patch.get('manual_deltas', old.get('manual_deltas')) if i == index else old.get('manual_deltas')
        if manual_deltas is not None:
            if not isinstance(manual_deltas, list) or len(manual_deltas) != 4 or any(
                    type(delta) is not int or abs(delta) > 1000000000 or delta % 100 for delta in manual_deltas):
                raise ValueError('手动收支须为四个百点整数')
            manual_pot = (patch['manual_pot_after'] if i == index and 'manual_pot_after' in patch
                          else old['manual_pot_after'] if 'manual_pot_after' in old else calculated['pot'])
            after = [score + 1000 * paid + delta for score, paid, delta
                     in zip(context['scores'], context['riichi_paid'], manual_deltas)]
            calculated = engine.invoke('reviseSettlement', calculated, dict(afterScores=after, potAfter=manual_pot))
            hand.update(manual_deltas=manual_deltas[:], manual_pot_after=manual_pot)
        else:
            hand.pop('manual_deltas', None)
            hand.pop('manual_pot_after', None)
        result = calculated['lastResult']
        # Stable IDs keep references and repeated saves from creating new hands.
        result['id'] = old['result']['id']
        manual = ([0] * 4 if i == index and patch.get('manual_deltas') is not None else
                  [a - b for a, b in zip(old['after_scores'], old['result']['afterScores'])])
        after_scores = [score + delta for score, delta in zip(result['afterScores'], manual)]
        hand.update(context=context, event=event, win_info=_caption(info, event, result, hand['players']),
                    kind=event['type'], round_index=context['round_index'], honba=context['honba'],
                    label=('东' if context['round_index'] < 4 else '南') + str(context['round_index'] % 4 + 1) + '局'
                          + (f" {context['honba']}本场" if context['honba'] else ''),
                    before_scores=context['scores'][:], after_scores=after_scores, result=result)
        for seat, player in enumerate(hand['players']):
            won = result['winner'] == seat
            lost = result['kind'] == 'ron' and result['loser'] == seat
            player.update(riichi=int(context['riichi'][seat]), wins=int(won), deal_ins=int(lost),
                          win_points=sum(p['amount'] for p in result['payments'] if p['to'] == seat) if won else 0,
                          deal_in_points=sum(p['amount'] for p in result['payments'] if p['from'] == seat) if lost else 0)
        hands[i] = hand
        carry = [a - b for a, b in zip(after_scores, old['after_scores'])]
        pot_carry = result['potAfter'] - old['result']['potAfter']
        previous_old, previous_new = old, hand
    for player, delta in zip(state['players'], carry):
        player['score'] += delta
    state['pot'] += pot_carry
    last_old, last_new = original[-1], hands[-1]
    live_result = bool(state.get('lastResult') and state['broadcast']['current_hand'] == last_new['id'])
    info = None
    if live_result:
        state['lastResult'] = copy.deepcopy(last_new['result'])
        state['phase'] = 'finished' if last_new['result']['next']['finished'] else 'result'
        state['finalRanking'] = calculated.get('finalRanking')
        for field, key in (('roundIndex', 'round_index'), ('dealer', 'dealer'), ('honba', 'honba')):
            if state[field] == context_for(last_old, state)[key]:
                state[field] = last_new['context'][key]
        for i, player in enumerate(state['players']):
            player['riichi'] = last_new['context']['riichi'][i]
        info = copy.deepcopy(last_new['win_info'])
    else:
        for field in ('roundIndex', 'dealer', 'honba'):
            if state[field] == last_old['result']['next'][field]:
                state[field] = last_new['result']['next'][field]
    total = sum(p['score'] for p in state['players']) + state['pot']
    state['accountingAdjustment'] = state.get('accountingAdjustment', 0) + total - state['accountingTotal']
    state['accountingTotal'] = total
    return info
