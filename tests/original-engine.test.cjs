'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const E = require('../resources/engine.js');

const scores = state => state.players.map(player => player.score);
const total = state => scores(state).reduce((a, b) => a + b, 0) + state.pot;
const start = config => E.createState({ names: ['A', 'B', 'C', 'D'], ...config });
const action = (state, type, fields = {}) => E.applyEvent(state, { type, ...fields });
const ron = (state, winner = 1, loser = 0, han = 1, fu = 30) => action(state, 'ron', { winner, loser, han, fu });
function freezeDeep(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freezeDeep);
    Object.freeze(value);
  }
  return value;
}

test('new match and preview are independent of caller objects', () => {
  const config = freezeDeep({ names: ['A', 'B', 'C', 'D'], rules: { uma: [30, 10, -10, -30] } });
  const initial = freezeDeep(E.createState(config));
  const preview = E.previewSettlement(initial, { type: 'ron', winner: 1, loser: 0, han: 1, fu: 30 });
  assert.deepEqual(preview.deltas, [-1000, 1000, 0, 0]);
  assert.deepEqual(scores(initial), [30000, 30000, 30000, 30000]);
  assert.equal(initial.lastResult, null);
  const next = action(initial, 'riichi', { player: 1 });
  assert.equal(next.players[1].score, 29000);
  assert.notEqual(next.rules, initial.rules);
  assert.notEqual(next.rules.uma, initial.rules.uma);
  assert.equal(initial.players[1].riichi, false);
});

test('riichi deducts exactly once and does not invent a settlement', () => {
  const s = action(start(), 'riichi', { player: 2 });
  assert.deepEqual(scores(s), [30000, 30000, 29000, 30000]);
  assert.equal(s.pot, 1000);
  assert.equal(s.lastResult, null);
  assert.equal(total(s), 120000);
  assert.throws(() => action(s, 'riichi', { player: 2 }), /重复/);
});

test('open pon blocks riichi; concealed kan preserves menzen', () => {
  const open = action(start(), 'meld', { player: 0, meld: { kind: 'pon', tiles: ['1m', '1m', '1m'], from: 1 } });
  assert.throws(() => action(open, 'riichi', { player: 0 }), /开放副露/);
  const closed = action(start(), 'meld', { player: 0, meld: { kind: 'ankan', tiles: ['1m', '1m', '1m', '1m'] } });
  assert.equal(action(closed, 'riichi', { player: 0 }).players[0].riichi, true);
});

test('chi validates source and repositions the actual claimed tile', () => {
  assert.throws(() => action(start(), 'meld', { player: 0, meld: { kind: 'chi', tiles: ['1m', '2m', '3m'], from: 1 } }), /上家/);
  const s = action(start(), 'meld', { player: 0, meld: { kind: 'chi', tiles: ['4m', '0m', '6m'], from: 3, calledIndex: 2 } });
  assert.deepEqual(s.players[0].melds[0].tiles, ['6m', '4m', '0m']);
  assert.equal(s.players[0].melds[0].calledIndex, 0);
  assert.throws(() => action(start(), 'meld', { player: 0, meld: { kind: 'chi', tiles: ['1z', '2z', '3z'], from: 3 } }), /连续/);
});

test('pon orientation follows source and kakan preserves its red called tile', () => {
  let s = action(start(), 'meld', { player: 0, meld: { kind: 'pon', tiles: ['0p', '5p', '5p'], from: 2, calledIndex: 0 } });
  assert.deepEqual(s.players[0].melds[0].tiles, ['5p', '0p', '5p']);
  assert.equal(s.players[0].melds[0].calledIndex, 1);
  s = action(s, 'meld', { player: 0, meld: { kind: 'kakan', tiles: ['5p'] } });
  assert.equal(s.players[0].melds.length, 1);
  assert.equal(s.players[0].melds[0].kind, 'kakan');
  assert.deepEqual(s.players[0].melds[0].tiles, ['5p', '0p', '5p', '5p']);
  assert.equal(s.players[0].melds[0].from, 2);
  assert.equal(s.players[0].melds[0].stackedOn, 1);
  assert.throws(() => action(s, 'dora', { tile: '5p' }), /超过四张/);
  assert.throws(() => action(start(), 'meld', { player: 0, meld: { kind: 'kakan', tiles: ['5p'] } }), /已有/);
});

test('four-group and four-kan limits are independent of concealed hand knowledge', () => {
  let s = start();
  for (let player = 0; player < 4; player++) s = action(s, 'meld', { player, meld: { kind: 'ankan', tiles: Array(4).fill(`${player + 1}m`) } });
  assert.throws(() => action(s, 'meld', { player: 0, meld: { kind: 'ankan', tiles: Array(4).fill('5m') } }), /四次杠/);
  s = start();
  for (let n = 1; n <= 4; n++) s = action(s, 'meld', { player: 0, meld: { kind: 'ankan', tiles: Array(4).fill(`${n}m`) } });
  assert.throws(() => action(s, 'meld', { player: 0, meld: { kind: 'chi', tiles: ['5m', '6m', '7m'], from: 3 } }), /最多四组/);
});

test('dora slot follows established kan and indicator cycles are exact', () => {
  let s = action(start(), 'dora', { tile: '1z' });
  assert.throws(() => action(s, 'dora', { tile: '2z' }), /先记录/);
  s = action(s, 'meld', { player: 0, meld: { kind: 'ankan', tiles: Array(4).fill('3m') } });
  s = action(s, 'dora', { tile: '2z' });
  assert.equal(s.doraIndicators.length, 2);
  assert.deepEqual(['9m', '0p', '4z', '7z'].map(E.tileFromIndicator), ['1m', '6p', '1z', '5z']);
  assert.throws(() => E.tileFromIndicator('0z'), /编码无效/);
});

test('30fu4han and 60fu3han follow the explicit kiriage switch', () => {
  assert.deepEqual(ron(start(), 1, 0, 4, 30).lastResult.deltas, [-8000, 8000, 0, 0]);
  assert.deepEqual(ron(start({ rules: { kiriageMangan: false } }), 1, 0, 4, 30).lastResult.deltas, [-7700, 7700, 0, 0]);
  assert.equal(ron(start(), 1, 0, 3, 60).lastResult.basePoints, 8000);
  assert.equal(ron(start({ rules: { kiriageMangan: false } }), 1, 0, 3, 60).lastResult.basePoints, 7700);
  assert.equal(ron(start({ rules: { kiriageMangan: false } }), 1, 0, 4, 40).lastResult.basePoints, 8000);
});

test('ordinary limit hands cap correctly; thirteen han is not automatically yakuman', () => {
  const expected = [[5, 8000], [6, 12000], [7, 12000], [8, 16000], [10, 16000], [11, 24000], [13, 24000]];
  for (const [han, amount] of expected) assert.equal(ron(start(), 1, 0, han, 30).lastResult.basePoints, amount);
  assert.equal(ron(start({ rules: { kazoeYakuman: true } }), 1, 0, 13, 30).lastResult.basePoints, 32000);
  const double = action(start(), 'tsumo', { winner: 0, yakuman: 2 });
  assert.deepEqual(double.lastResult.deltas, [96000, -32000, -32000, -32000]);
});

test('child and dealer tsumo use payer-specific hundred-point ceilings', () => {
  assert.deepEqual(action(start(), 'tsumo', { winner: 1, han: 2, fu: 20 }).lastResult.deltas, [-700, 1500, -400, -400]);
  assert.deepEqual(action(start(), 'tsumo', { winner: 0, han: 2, fu: 20 }).lastResult.deltas, [2100, -700, -700, -700]);
  assert.deepEqual(action(start(), 'tsumo', { winner: 1, han: 3, fu: 30 }).lastResult.deltas, [-2000, 4000, -1000, -1000]);
  assert.deepEqual(action(start(), 'tsumo', { winner: 0, han: 4, fu: 30 }).lastResult.deltas, [12000, -4000, -4000, -4000]);
});

test('impossible 20fu/25fu combinations are rejected instead of silently scored', () => {
  assert.throws(() => ron(start(), 1, 0, 2, 20), /20 符/);
  assert.throws(() => action(start(), 'tsumo', { winner: 1, han: 2, fu: 25 }), /七对子/);
  assert.equal(ron(start(), 1, 0, 2, 25).lastResult.basePoints, 1600);
  assert.throws(() => ron(start(), 1, 0, 0, 30), /至少 1 番/);
});

test('full riichi→draw→renchan→child tsumo example conserves 120000', () => {
  let s = start();
  s = action(s, 'riichi', { player: 0 });
  s = action(s, 'riichi', { player: 2 });
  s = action(s, 'draw', { tenpai: [0, 2] });
  assert.deepEqual(scores(s), [30500, 28500, 30500, 28500]);
  assert.equal(s.pot, 2000);
  assert.equal(s.lastResult.next.honba, 1);
  assert.equal(s.lastResult.next.dealer, 0);
  assert.equal(total(s), 120000);
  s = action(s, 'next_hand');
  s = action(s, 'tsumo', { winner: 1, han: 4, fu: 30 });
  assert.deepEqual(scores(s), [26400, 38800, 28400, 26400]);
  assert.equal(s.pot, 0);
  assert.equal(total(s), 120000);
  assert.deepEqual([s.lastResult.next.roundIndex, s.lastResult.next.dealer, s.lastResult.next.honba], [1, 1, 0]);
});

test('ron includes honba once and allocates the pot only to the winner', () => {
  let s = start({ honba: 2 });
  s = action(s, 'riichi', { player: 2 });
  s = ron(s, 1, 0, 3, 30);
  assert.deepEqual(s.lastResult.deltas, [-4500, 5500, 0, 0]);
  assert.equal(s.lastResult.payments[0].base, 3900);
  assert.equal(s.lastResult.payments[0].honba, 600);
  assert.equal(total(s), 120000);
});

test('draw payments for 0, 1, 2, 3, 4 tenpai use the correct denominators', () => {
  const expected = [[0, 0, 0, 0], [3000, -1000, -1000, -1000], [1500, 1500, -1500, -1500], [1000, 1000, 1000, -3000], [0, 0, 0, 0]];
  for (let n = 0; n <= 4; n++) {
    const s = action(start(), 'draw', { tenpai: [0, 1, 2, 3].slice(0, n) });
    assert.deepEqual(s.lastResult.deltas, expected[n]);
    assert.equal(total(s), 120000);
    assert.equal(s.lastResult.next.honba, 1);
  }
  const riichi = action(start(), 'riichi', { player: 3 });
  assert.throws(() => action(riichi, 'draw', { tenpai: [] }), /已立直/);
  assert.throws(() => action(start(), 'draw', { tenpai: [1, 1] }), /重复/);
});

test('draw rotation still increments honba and result retains the old round until NEXT_HAND', () => {
  let s = start({ honba: 2 });
  s = action(s, 'dora', { tile: '6p' });
  s = action(s, 'riichi', { player: 1 });
  s = action(s, 'draw', { tenpai: [1] });
  assert.deepEqual([s.roundIndex, s.dealer, s.honba], [0, 0, 2]);
  assert.deepEqual([s.lastResult.next.roundIndex, s.lastResult.next.dealer, s.lastResult.next.honba], [1, 1, 3]);
  assert.throws(() => action(s, 'draw', { tenpai: [1] }), /已经结算/);
  s = action(s, 'next_hand');
  assert.deepEqual([s.roundIndex, s.dealer, s.honba], [1, 1, 3]);
  assert.equal(s.pot, 1000);
  assert.ok(s.players.every(player => !player.riichi && player.melds.length === 0));
  assert.deepEqual(s.doraIndicators, []);
  assert.equal(s.lastResult, null);
  assert.throws(() => action(s, 'next_hand'), /先完成/);
});

test('south-four dealer tenpai continues; noten or child win completes the half-game', () => {
  const s = start({ roundIndex: 7, honba: 3 });
  const continuation = action(s, 'draw', { tenpai: [3] });
  assert.equal(continuation.phase, 'result');
  assert.deepEqual([continuation.lastResult.next.roundIndex, continuation.lastResult.next.dealer, continuation.lastResult.next.honba], [7, 3, 4]);
  assert.equal(action(s, 'draw', { tenpai: [] }).phase, 'finished');
  assert.equal(ron(s, 1, 0).phase, 'finished');
  assert.equal(ron(s, 3, 0).phase, 'result');
  assert.equal(action(start({ roundIndex: 7, rules: { dealerTenpaiRenchan: false } }), 'draw', { tenpai: [3] }).phase, 'finished');
});

test('optional south-four yame requires a sole leading dealer', () => {
  assert.equal(ron(start({ roundIndex: 7, rules: { agariYame: true } }), 3, 0).phase, 'finished');
  assert.equal(action(start({ roundIndex: 7, rules: { tenpaiYame: true } }), 'draw', { tenpai: [3] }).phase, 'finished');
  assert.equal(action(start({ roundIndex: 7, rules: { tenpaiYame: true } }), 'draw', { tenpai: [0, 1, 2, 3] }).phase, 'result');
});

test('unallocated terminal pot remains visible and is not awarded to first place', () => {
  const s = action(start({ initialScores: [40000, 31200, 27500, 20300], roundIndex: 7, pot: 1000 }), 'draw', { tenpai: [] });
  assert.equal(s.phase, 'finished');
  assert.deepEqual(scores(s), [40000, 31200, 27500, 20300]);
  assert.equal(s.pot, 1000);
  assert.equal(total(s), 120000);
  assert.deepEqual(s.lastResult.terminalPotAwards, []);
});

test('terminal supply allocation changes the final score once, while source state stays unchanged', () => {
  const before = freezeDeep(start({ initialScores: [40000, 31200, 27500, 20300], roundIndex: 7, pot: 1000, rules: { terminalPot: 'top' } }));
  const s = action(before, 'draw', { tenpai: [] });
  assert.deepEqual(scores(s), [41000, 31200, 27500, 20300]);
  assert.deepEqual(s.lastResult.deltas, [1000, 0, 0, 0]);
  assert.equal(s.pot, 0);
  assert.equal(before.pot, 1000);
  assert.equal(total(s), total(before));
  assert.throws(() => action(s, 'draw', { tenpai: [] }), /已经结算/);
  assert.throws(() => action(s, 'next_hand'), /已经结束/);
});

test('M terminal 1000/2000/3000 splits freeze three-way tied ranks before unequal awards', () => {
  const cases = [
    { pot: 1000, initialScores: [33000, 33000, 33000, 0], deltas: [400, 300, 300, 0] },
    { pot: 2000, initialScores: [32600, 32600, 32600, 200], deltas: [800, 600, 600, 0] },
    { pot: 3000, initialScores: [32000, 32000, 32000, 1000], deltas: [1000, 1000, 1000, 0] }
  ];
  for (const item of cases) {
    const s = action(start({ ...item, roundIndex: 7, rules: { terminalPot: 'top', terminalPotTie: 'mleague', startPoints: 25000, uma: [50, 10, -10, -30] } }), 'draw', { tenpai: [] });
    assert.deepEqual(s.lastResult.deltas, item.deltas);
    const result = E.computeStandings(s);
    assert.deepEqual(result.map(row => row.rank), [1, 1, 1, 4]);
    assert.deepEqual(result.map(row => row.uma), [16.8, 16.6, 16.6, -30]);
    assert.ok(result.slice(0, 3).every(row => row.tied && row.tieGroup.length === 3));
    assert.equal(total(s), 100000);
  }
});

test('initial seat controls the remainder, not screen order or the current dealer', () => {
  const s = action(start({ initialScores: [33000, 33000, 33000, 0], initialDealer: 2, dealer: 3, roundIndex: 7, pot: 1000, rules: { terminalPot: 'top' } }), 'draw', { tenpai: [] });
  assert.deepEqual(s.lastResult.deltas, [300, 300, 400, 0]);
  assert.deepEqual(E.computeStandings(s).map(row => row.player), [2, 0, 1, 3]);
  const ordered = action(start({ initialScores: [33000, 33000, 33000, 0], initialDealer: 2, dealer: 3, roundIndex: 7, pot: 1000, rules: { terminalPot: 'top', tieBreak: 'initial_seat' } }), 'draw', { tenpai: [] });
  assert.deepEqual(ordered.lastResult.deltas, [0, 0, 1000, 0]);
  assert.deepEqual(E.computeStandings(ordered).map(row => row.rank), [1, 2, 3, 4]);
});

test('Uma and Oka can be represented separately or combined without double counting', () => {
  const players = [40000, 30000, 20000, 10000].map((score, i) => ({ id: `p${i}`, name: 'ABCD'[i], score }));
  const separate = E.computeStandings(start({ players, rules: { startPoints: 25000, returnPoints: 30000, okaEnabled: true, uma: [30, 10, -10, -30] } }));
  const combined = E.computeStandings(start({ players, rules: { startPoints: 25000, returnPoints: 30000, okaEnabled: false, uma: [50, 10, -10, -30] } }));
  assert.deepEqual(separate.map(row => row.total), [60, 10, -20, -50]);
  assert.deepEqual(combined.map(row => row.total), [60, 10, -20, -50]);
  assert.equal(separate[0].oka, 20);
  assert.equal(combined[0].oka, 0);
  assert.throws(() => start({ rules: { okaEnabled: true, uma: [50, 10, -10, -30] } }), /重复计算/);
});

test('shared final positions divide only the corresponding place bonuses', () => {
  const rows = E.computeStandings(start({ initialScores: [35000, 35000, 30000, 20000] }));
  assert.deepEqual(rows.map(row => row.rank), [1, 1, 3, 4]);
  assert.deepEqual(rows.map(row => row.uma), [20, 20, -10, -30]);
  assert.deepEqual(rows.map(row => row.total), [25, 25, -10, -40]);
});

test('bankruptcy terminates only after exact payment; zero is distinct from negative', () => {
  const negative = ron(start({ initialScores: [7000, 43000, 35000, 35000], rules: { allowNegative: false } }), 1, 0, 4, 30);
  assert.equal(negative.players[0].score, -1000);
  assert.equal(negative.phase, 'finished');
  assert.equal(negative.lastResult.next.reason, 'bankruptcy');
  assert.equal(total(negative), 120000);
  const zero = ron(start({ initialScores: [8000, 42000, 35000, 35000], rules: { allowNegative: false } }), 1, 0, 4, 30);
  assert.equal(zero.players[0].score, 0);
  assert.equal(zero.phase, 'result');
  assert.equal(ron(start({ initialScores: [8000, 42000, 35000, 35000], rules: { allowNegative: false, endOnZero: true } }), 1, 0, 4, 30).phase, 'finished');
  assert.throws(() => action(start({ initialScores: [900, 39100, 40000, 40000], rules: { allowNegative: false } }), 'riichi', { player: 0 }), /至少持有/);
});

test('reasoned correction establishes a new accounting anchor and future events stay automatic', () => {
  assert.throws(() => action(start(), 'correction', { scores: [31000, 30000, 30000, 30000] }), /原因/);
  let s = action(start(), 'correction', { scores: [31000, 30000, 30000, 30000], roundIndex: 2, honba: 1, reason: '接管桌面已确认余额' });
  assert.equal(s.dealer, 2);
  assert.equal(s.accountingTotal, 121000);
  assert.equal(s.accountingAdjustment, 1000);
  assert.equal(s.lastCorrection.accountingAdjustment, 1000);
  assert.equal(E.validateState(s).valid, true);
  s = action(s, 'riichi', { player: 0 });
  s = ron(s, 1, 3, 1, 30);
  assert.equal(total(s), 121000);
  assert.equal(s.lastResult.next.roundIndex, 3);
  assert.equal(s.lastResult.next.honba, 0);
  assert.throws(() => action(s, 'correction', { pot: 0, reason: '不允许旧结果和新余额混存' }), /先撤销/);
});

test('corruption, repeated event ids, and malformed settlements cannot be committed', () => {
  const corrupt = start();
  corrupt.players[0].score += 100;
  assert.equal(E.validateState(corrupt).valid, false);
  assert.throws(() => action(corrupt, 'riichi', { player: 1 }), /合计不一致/);
  const first = action(start(), 'dora', { tile: '1m', id: 'event-1' });
  assert.throws(() => action(first, 'dora', { tile: '1m', id: 'event-1' }), /已处理/);
  assert.throws(() => ron(start(), 0, 0), /同一人/);
  assert.throws(() => ron(start(), 4, 0), /座位编号/);
});
