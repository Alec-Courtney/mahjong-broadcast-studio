'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const E = require('../resources/engine.js');
const scores = state => state.players.map(player => player.score);
const create = rules => ({...E.createState({names: ['A', 'B', 'C', 'D'], rules}), manualDisplay: true});

test('manual income is independent of yaku and updates actual transfers without mutating its source', () => {
  let table = E.applyEvent(create(), {type: 'riichi', player: 1});
  table = E.applyEvent(table, {type: 'ron', winner: 1, loser: 2, han: 1, fu: 30});
  const before = JSON.stringify(table);
  const corrected = E.reviseSettlement(table, {afterScores: [30000, 39000, 21000, 30000], potAfter: 0});
  assert.equal(JSON.stringify(table), before);
  assert.deepEqual(scores(corrected), [30000, 39000, 21000, 30000]);
  assert.equal(corrected.lastResult.han, 1);
  assert.deepEqual(corrected.lastResult.deltas, [0, 10000, -9000, 0]);
  assert.deepEqual(corrected.lastResult.payments, [{from: 2, to: 1, amount: 9000, base: 9000, honba: 0}]);
  assert.equal(E.validateState(corrected).valid, true);
  const replaced = E.applyEvent(corrected, {type: 'ron', winner: 3, loser: 2, han: 1, fu: 30});
  assert.deepEqual(scores(replaced), [30000, 29000, 29000, 32000]);
});

test('manual scores decide bankruptcy and maintain a revised accounting total', () => {
  const table = E.applyEvent(create({allowNegative: false}), {type: 'ron', winner: 1, loser: 0, han: 1, fu: 30});
  const corrected = E.reviseSettlement(table, {afterScores: [-100, 60100, 30100, 30000], potAfter: 0});
  assert.equal(corrected.phase, 'finished');
  assert.equal(corrected.lastResult.next.reason, 'bankruptcy');
  assert.equal(corrected.accountingTotal, 120100);
  assert.equal(corrected.accountingAdjustment, 100);
  assert.equal(E.validateState(corrected).valid, true);
});

test('unchanged terminal payments keep frozen tied ranks and malformed input never mutates state', () => {
  let table = create({terminalPot: 'top'});
  table.roundIndex = 7;
  table.dealer = 3;
  table = E.applyEvent(table, {type: 'riichi', player: 0});
  table = E.applyEvent(table, {type: 'draw', tenpai: []});
  const original = JSON.stringify(table);
  const corrected = E.reviseSettlement(table, {afterScores: scores(table), potAfter: table.pot});
  assert.deepEqual(corrected.finalRanking, table.finalRanking);
  assert.throws(() => E.reviseSettlement(table, {afterScores: [30001, 30000, 30000, 30000], potAfter: 0}), /百点/);
  assert.throws(() => E.reviseSettlement(table, {afterScores: scores(table), potAfter: -1000}), /供托/);
  assert.equal(JSON.stringify(table), original);
});
