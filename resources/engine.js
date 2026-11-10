(function (root, factory) {
  'use strict';
  var engine = factory();
  if (typeof module === 'object' && module.exports) module.exports = engine;
  if (root) root.MahjongEngine = engine;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // This engine records confirmed table facts. It does not infer yaku or legal
  // waits from video or an unknown concealed hand. Scores are integer points;
  // tournament results are calculated internally in tenths of a point.
  var DEFAULT_RULES = {
    startPoints: 30000,
    returnPoints: 30000,
    uma: [30, 10, -10, -30],
    okaEnabled: false,
    terminalPot: 'unallocated',
    terminalPotTie: 'mleague',
    tieBreak: 'share',
    dealerTenpaiRenchan: true,
    agariYame: false,
    tenpaiYame: false,
    notenPayment: 3000,
    honbaRon: 300,
    honbaTsumoEach: 100,
    allowNegative: true,
    endOnZero: false,
    kiriageMangan: true,
    kazoeYakuman: false
  };
  var BOOLEAN_RULES = ['okaEnabled', 'dealerTenpaiRenchan', 'agariYame', 'tenpaiYame', 'allowNegative', 'endOnZero', 'kiriageMangan', 'kazoeYakuman'];
  var MELD_KINDS = ['chi', 'pon', 'daiminkan', 'ankan', 'kakan'];
  var WINDS = ['东', '南'];

  function copy(value) {
    if (value === null || typeof value !== 'object') return value;
    if (Array.isArray(value)) return value.map(copy);
    var out = {};
    Object.keys(value).forEach(function (key) { out[key] = copy(value[key]); });
    return out;
  }
  function fail(message) { throw new Error(message); }
  function assert(condition, message) { if (!condition) fail(message); }
  function integer(value) { return typeof value === 'number' && Number.isSafeInteger(value); }
  function pointValue(value) { return integer(value) && Math.abs(value) <= 1000000000 && value % 100 === 0; }
  function sum(values) { return values.reduce(function (a, b) { return a + b; }, 0); }
  function cleanZero(value) { return value === 0 ? 0 : value; }
  function seatDistance(player, initialDealer) { return (player - initialDealer + 4) % 4; }
  function playerIndex(value, label) {
    assert(integer(value) && value >= 0 && value <= 3, (label || '选手') + '必须是 0 到 3 的座位编号');
    return value;
  }
  function scoresOf(state) { return state.players.map(function (player) { return player.score; }); }
  function roundLabel(index) { return WINDS[Math.floor(index / 4)] + (index % 4 + 1) + '局'; }
  function formatPoints(value) { return Number(value).toLocaleString('en-US'); }
  function isOpen(player) { return player.melds.some(function (meld) { return meld.kind !== 'ankan'; }); }

  function normalizeTile(code) {
    assert(typeof code === 'string' && /^(?:[0-9][mps]|[1-7]z)$/.test(code), '牌编码无效：请使用 1m～9m、1p～9p、1s～9s、1z～7z，赤五使用 0m／0p／0s');
    return code[0] === '0' ? '5' + code[1] : code;
  }
  function tileFromIndicator(code) {
    var tile = normalizeTile(code), number = Number(tile[0]), suit = tile[1];
    if (suit !== 'z') return String(number === 9 ? 1 : number + 1) + suit;
    if (number <= 4) return String(number === 4 ? 1 : number + 1) + 'z';
    return String(number === 7 ? 5 : number + 1) + 'z';
  }
  function indicatorFromTile(code) {
    var tile = normalizeTile(code), number = Number(tile[0]), suit = tile[1];
    if (suit !== 'z') return String(number === 1 ? 9 : number - 1) + suit;
    if (number <= 4) return String(number === 1 ? 4 : number - 1) + 'z';
    return String(number === 5 ? 7 : number - 1) + 'z';
  }

  function rulesFrom(input) {
    var rules = copy(DEFAULT_RULES);
    if (input) Object.keys(input).forEach(function (key) { rules[key] = copy(input[key]); });
    if (rules.terminalPot === 'award_top') rules.terminalPot = 'top';
    if (rules.tieBreak === 'seat' || rules.tieBreak === 'seat_order') rules.tieBreak = 'initial_seat';
    checkRules(rules);
    return rules;
  }
  function checkRules(rules) {
    assert(rules && typeof rules === 'object', '缺少规则配置');
    assert(pointValue(rules.startPoints) && rules.startPoints > 0, '起始点数必须是正的百点整数');
    assert(pointValue(rules.returnPoints) && rules.returnPoints >= 0, '返还点必须是非负的百点整数');
    assert(Array.isArray(rules.uma) && rules.uma.length === 4 && rules.uma.every(function (value) {
      return typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= 100000 && Math.abs(value * 10 - Math.round(value * 10)) < 1e-8;
    }), '顺位分必须是四个数字，最多保留一位小数');
    BOOLEAN_RULES.forEach(function (key) { assert(typeof rules[key] === 'boolean', '规则 ' + key + ' 必须为开或关'); });
    assert(!rules.okaEnabled || Math.abs(sum(rules.uma)) < 1e-8, '启用起点奖励时四家顺位分合计必须为 0，避免重复计算奖励');
    assert(['unallocated', 'top'].indexOf(rules.terminalPot) >= 0, '终局供托设置必须为未分配或首位领取');
    assert(['mleague', 'equal_100'].indexOf(rules.terminalPotTie) >= 0, '未知的并列供托分配方式');
    assert(['share', 'initial_seat'].indexOf(rules.tieBreak) >= 0, '同分方式必须为共享顺位或起家顺序');
    assert(pointValue(rules.notenPayment) && rules.notenPayment >= 0 && rules.notenPayment % 600 === 0, '不听罚符总额必须为 600 的非负整数倍，以便按百点分配');
    assert(pointValue(rules.honbaRon) && rules.honbaRon >= 0, '荣和每本场加点必须为非负百点整数');
    assert(pointValue(rules.honbaTsumoEach) && rules.honbaTsumoEach >= 0, '自摸每家每本场加点必须为非负百点整数');
  }

  function countKnownTiles(state) {
    var counts = {}, kans = 0;
    function add(tile) {
      var normalized = normalizeTile(tile);
      counts[normalized] = (counts[normalized] || 0) + 1;
      assert(state.manualDisplay || counts[normalized] <= 4, normalized + ' 已出现超过四张，请检查牌组和宝牌指示牌');
    }
    state.players.forEach(function (player, owner) {
      assert(Array.isArray(player.melds) && player.melds.length <= 4, '每家最多只能有四组牌');
      player.melds.forEach(function (meld) {
        assert(meld && MELD_KINDS.indexOf(meld.kind) >= 0, '未知牌组类型');
        assert(Array.isArray(meld.tiles), '牌组缺少牌面');
        var isKan = ['daiminkan', 'ankan', 'kakan'].indexOf(meld.kind) >= 0;
        assert(meld.tiles.length === (isKan ? 4 : 3), '牌组张数与类型不符');
        if (isKan) kans += 1;
        if (meld.kind === 'ankan') {
          assert(meld.from === null && meld.calledIndex === null, '暗杠没有鸣牌来源或横置牌');
        } else {
          playerIndex(meld.from, '副露来源');
          assert(meld.from !== owner, '副露来源不能是自己');
          var relation = (meld.from - owner + 4) % 4;
          var expected = relation === 3 ? 0 : relation === 2 ? 1 : (meld.kind === 'daiminkan' ? 3 : 2);
          assert(meld.calledIndex === expected, '副露横牌位置与鸣牌来源不符');
          assert(state.manualDisplay || meld.kind !== 'chi' || relation === 3, '吃牌只能来自上家');
          if (meld.kind === 'kakan') assert(meld.addedIndex === 3 && meld.stackedOn === meld.calledIndex, '加杠必须叠在原碰的横牌上');
        }
        var normalized = meld.tiles.map(normalizeTile);
        if (meld.kind === 'chi') {
          var sorted = normalized.slice().sort();
          assert(sorted.every(function (tile) { return tile[1] === sorted[0][1] && tile[1] !== 'z'; }) && Number(sorted[1][0]) === Number(sorted[0][0]) + 1 && Number(sorted[2][0]) === Number(sorted[1][0]) + 1, '吃牌必须组成同一花色的连续三张');
        } else {
          assert(normalized.every(function (tile) { return tile === normalized[0]; }), '碰杠牌必须为同一种牌');
        }
        meld.tiles.forEach(add);
      });
    });
    assert(state.manualDisplay || kans <= 4, '一局最多只能成立四次杠');
    state.doraIndicators.forEach(add);
    assert(state.doraIndicators.length <= 5, '宝牌指示牌最多五张');
    assert(state.manualDisplay || state.doraIndicators.length <= kans + 1, '新增杠宝牌前必须先记录已成立的杠');
    return { counts: counts, kans: kans };
  }

  function validateState(state) {
    var errors = [], warnings = [];
    try {
      assert(state && typeof state === 'object', '缺少对局状态');
      assert(state.schemaVersion === 1, '不支持的对局数据版本');
      if (state.manualDisplay !== undefined) assert(typeof state.manualDisplay === 'boolean', '导播模式必须为开或关');
      checkRules(state.rules);
      assert(Array.isArray(state.players) && state.players.length === 4, '必须有四位选手');
      assert(Array.isArray(state.initialScores) && state.initialScores.length === 4 && state.initialScores.every(pointValue), '起始点数记录必须是四个百点整数');
      var ids = new Set();
      state.players.forEach(function (player) {
        assert(player && typeof player.id === 'string' && player.id.length > 0 && !ids.has(player.id), '选手标识必须存在且互不重复');
        ids.add(player.id);
        assert(typeof player.name === 'string' && player.name.trim().length > 0, '选手姓名不能为空');
        assert(pointValue(player.score), '选手点数必须是有效的百点整数');
        assert(typeof player.riichi === 'boolean', '立直状态必须为开或关');
        if (player.tenpai !== undefined) assert(typeof player.tenpai === 'boolean', '听牌状态必须为开或关');
        if (player.waits !== undefined) {
          assert(Array.isArray(player.waits), '待牌列表无效');
          var normalizedWaits = player.waits.map(normalizeTile);
          assert(new Set(normalizedWaits).size === normalizedWaits.length, '同一种待牌不能重复');
        }
        if (player.remaining !== undefined && player.remaining !== null) {
          assert(integer(player.remaining) && player.remaining >= 0 && player.remaining <= (state.manualDisplay ? 136 : (player.waits || []).length * 4), '山存张数必须是有效范围内的整数');
        }
        assert(Array.isArray(player.melds), '牌组列表无效');
        assert(state.manualDisplay || !player.riichi || !isOpen(player), '有开放副露的选手不能保持立直状态');
      });
      playerIndex(state.initialDealer, '起庄');
      playerIndex(state.dealer, '庄家');
      assert(integer(state.roundIndex) && state.roundIndex >= 0 && state.roundIndex <= 7, '当前版本支持东一局至南四局');
      assert(integer(state.honba) && state.honba >= 0 && state.honba <= 10000, '本场必须是非负整数');
      assert(integer(state.pot) && state.pot >= 0 && state.pot <= 1000000000 && state.pot % 1000 === 0, '供托必须是非负的千点整数');
      assert(Array.isArray(state.doraIndicators), '宝牌指示牌列表无效');
      assert(['playing', 'result', 'finished'].indexOf(state.phase) >= 0, '未知对局阶段');
      if (state.phase !== 'playing') {
        assert(state.lastResult && state.lastResult.next, '结果阶段缺少结算记录');
        assert(state.phase !== 'finished' || state.lastResult.next.finished, '终局状态与结算不一致');
      }
      if (state.phase === 'playing' && !state.manualDisplay) {
        assert(state.pot >= state.players.filter(function (player) { return player.riichi; }).length * 1000, '供托少于本局已成立的立直棒，请更正点数或撤销错误立直');
      }
      if (state.accountingTotal !== undefined) {
        assert(integer(state.accountingTotal) && sum(scoresOf(state)) + state.pot === state.accountingTotal, '四家点数与供托的合计不一致，请通过更正事件修复');
      }
      countKnownTiles(state);
      if (state.dealer !== (state.initialDealer + state.roundIndex) % 4) warnings.push('当前庄家使用了手动修正值，后续将以此庄家继续推进');
      if (!state.rules.allowNegative && state.phase === 'playing' && state.players.some(function (player) { return player.score < 0; })) warnings.push('当前有人负分；本局结算时将按负分终止规则处理');
    } catch (error) { errors.push(error.message || String(error)); }
    return { valid: errors.length === 0, errors: errors, warnings: warnings };
  }
  function requireValid(state) {
    var validation = validateState(state);
    if (!validation.valid) fail(validation.errors.join('；'));
  }

  function createState(config) {
    config = config || {};
    var rules = rulesFrom(config.rules), suppliedPlayers = config.players || [];
    var names = config.names || suppliedPlayers.map(function (player) { return typeof player === 'string' ? player : player.name; });
    if (!names.length) names = ['选手 A', '选手 B', '选手 C', '选手 D'];
    assert(Array.isArray(names) && names.length === 4, '请输入四位选手姓名');
    var initialScores = config.initialScores ? copy(config.initialScores) : [rules.startPoints, rules.startPoints, rules.startPoints, rules.startPoints];
    assert(Array.isArray(initialScores) && initialScores.length === 4, '起始点数需要四个值');
    var players = names.map(function (name, index) {
      var supplied = suppliedPlayers[index] && typeof suppliedPlayers[index] === 'object' ? suppliedPlayers[index] : {};
      return { id: supplied.id || 'p' + (index + 1), name: String(name).trim(), score: supplied.score !== undefined ? supplied.score : initialScores[index], riichi: false, melds: [] };
    });
    var initialDealer = config.initialDealer === undefined ? 0 : config.initialDealer;
    var index = config.roundIndex === undefined ? 0 : config.roundIndex;
    var pot = config.pot === undefined ? 0 : config.pot;
    var state = {
      schemaVersion: 1,
      id: config.id || 'match-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8),
      title: typeof config.title === 'string' && config.title.trim() ? config.title.trim() : '单半庄对局',
      initialDealer: initialDealer,
      initialScores: initialScores,
      initialPot: pot,
      rules: rules,
      players: players,
      dealer: config.dealer === undefined ? (initialDealer + index) % 4 : config.dealer,
      roundIndex: index,
      honba: config.honba === undefined ? 0 : config.honba,
      pot: pot,
      doraIndicators: copy(config.doraIndicators || []),
      phase: 'playing',
      lastResult: null,
      finalRanking: null,
      accountingTotal: sum(players.map(function (player) { return player.score; })) + pot,
      accountingAdjustment: 0,
      lastCorrection: null,
      revision: 0,
      handSequence: 0,
      appliedEventIds: []
    };
    requireValid(state);
    return state;
  }

  function rankingSnapshot(state, scores) {
    var order = [0, 1, 2, 3].sort(function (a, b) { return scores[b] - scores[a] || seatDistance(a, state.initialDealer) - seatDistance(b, state.initialDealer); });
    var groups = [];
    order.forEach(function (player, position) {
      var previous = groups[groups.length - 1];
      if (state.rules.tieBreak === 'share' && previous && scores[previous.players[0]] === scores[player]) {
        previous.players.push(player);
        previous.places.push(position + 1);
      } else groups.push({ rank: position + 1, players: [player], places: [position + 1] });
    });
    return { scoresBeforePot: scores.slice(), groups: groups, tieBreak: state.rules.tieBreak };
  }
  // Exact conservation at the chosen unit. The first eligible initial seat
  // receives any indivisible remainder; this is a documented project policy.
  function splitUnits(total, count) {
    var each = Math.floor(total / count), result = new Array(count).fill(each);
    result[0] += total - each * count;
    return result;
  }
  function computeStandings(state) {
    requireValid(state);
    var scores = scoresOf(state);
    var snapshot = state.phase === 'finished' && state.finalRanking ? state.finalRanking : rankingSnapshot(state, scores);
    var rows = [], okaTenths = state.rules.okaEnabled ? Math.round((state.rules.returnPoints - state.rules.startPoints) * 4 / 100) : 0;
    snapshot.groups.forEach(function (group) {
      var umaTotal = sum(group.places.map(function (place) { return Math.round(state.rules.uma[place - 1] * 10); }));
      var umas = splitUnits(umaTotal, group.players.length);
      var okas = group.rank === 1 ? splitUnits(okaTenths, group.players.length) : new Array(group.players.length).fill(0);
      group.players.forEach(function (player, offset) {
        var pointTenths = (scores[player] - state.rules.returnPoints) / 100;
        rows.push({
          player: player, index: player, id: state.players[player].id, name: state.players[player].name,
          score: scores[player], finalScore: scores[player], scoreBeforePot: snapshot.scoresBeforePot[player],
          rank: group.rank, rankLabel: group.players.length > 1 ? '并列 ' + group.rank : String(group.rank),
          tied: group.players.length > 1, tieGroup: group.players.slice(), places: group.places.slice(),
          position: rows.length + 1, net: scores[player] - state.initialScores[player],
          pointScore: cleanZero(pointTenths / 10), uma: cleanZero(umas[offset] / 10), oka: cleanZero(okas[offset] / 10),
          rankPoints: cleanZero((umas[offset] + okas[offset]) / 10),
          total: cleanZero((pointTenths + umas[offset] + okas[offset]) / 10)
        });
      });
    });
    return rows;
  }

  function winningBase(state, event) {
    var multiplier = event.yakuman === true ? 1 : (event.yakuman || 0);
    assert(integer(multiplier) && multiplier >= 0 && multiplier <= 10, '役满倍数必须是 0 到 10 的整数');
    if (multiplier > 0) return { base: 8000 * multiplier, han: null, fu: null, yakuman: multiplier, limit: multiplier === 1 ? '役满' : multiplier + ' 倍役满' };
    assert(integer(event.han) && event.han >= 1 && event.han <= 100, '请输入至少 1 番的有效翻数；役种需由已确认的和牌事实提供');
    assert(integer(event.fu) && [20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140].indexOf(event.fu) >= 0, '符数必须为 20、25 或 30 到 140 之间的十位整数');
    if (event.fu === 20 && !state.manualDisplay) {
      assert(event.type === 'tsumo' && event.han >= 2 && state.players[event.winner].melds.length === 0, '20 符只适用于门清平和自摸，至少 2 番且不能有牌组');
    }
    if (event.fu === 25 && !state.manualDisplay) {
      assert(state.players[event.winner].melds.length === 0 && event.han >= (event.type === 'tsumo' ? 3 : 2), '25 符七对子不能有牌组；荣和至少 2 番，自摸至少 3 番');
    }
    var han = event.han, fu = event.fu, base, limit = '';
    if (han >= 13 && state.rules.kazoeYakuman) { base = 8000; limit = '累计役满'; }
    else if (han >= 11) { base = 6000; limit = '三倍满'; }
    else if (han >= 8) { base = 4000; limit = '倍满'; }
    else if (han >= 6) { base = 3000; limit = '跳满'; }
    else {
      base = fu * Math.pow(2, han + 2);
      if (han >= 5 || base >= 2000 || (state.rules.kiriageMangan && base === 1920)) { base = 2000; limit = '满贯'; }
    }
    return { base: base, han: han, fu: fu, yakuman: 0, limit: limit };
  }
  function ceil100(value) { return Math.ceil(value / 100) * 100; }

  function nextAfter(state, event, afterScores) {
    var dealerWin = event.type !== 'draw' && event.winner === state.dealer;
    var dealerTenpai = event.type === 'draw' && event.tenpai.indexOf(state.dealer) >= 0;
    var repeats = dealerWin || (event.type === 'draw' && dealerTenpai && state.rules.dealerTenpaiRenchan);
    var honba = event.type === 'draw' || dealerWin ? state.honba + 1 : 0;
    var next = { finished: false, roundIndex: state.roundIndex, dealer: state.dealer, honba: honba, pot: state.pot, reason: repeats ? (dealerWin ? 'dealer_win' : 'dealer_tenpai') : 'rotation' };
    var bankrupt = !state.rules.allowNegative && afterScores.some(function (score) { return state.rules.endOnZero ? score <= 0 : score < 0; });
    var dealerOnlyTop = afterScores.every(function (score, player) { return player === state.dealer || score < afterScores[state.dealer]; });
    if (bankrupt) { next.finished = true; next.reason = 'bankruptcy'; }
    else if (state.roundIndex === 7 && repeats && dealerOnlyTop && ((dealerWin && state.rules.agariYame) || (dealerTenpai && state.rules.tenpaiYame))) {
      next.finished = true;
      next.reason = dealerWin ? 'agari_yame' : 'tenpai_yame';
    } else if (!repeats && state.roundIndex === 7) { next.finished = true; next.reason = 'south4_end'; }
    else if (!repeats) { next.roundIndex += 1; next.dealer = (state.dealer + 1) % 4; }
    next.label = next.finished ? '半庄结束' : roundLabel(next.roundIndex) + (next.honba ? ' ' + next.honba + ' 本场' : '');
    return next;
  }

  function distributeTerminalPot(state, afterScores, snapshot) {
    if (state.rules.terminalPot !== 'top' || state.pot === 0) return [];
    var top = snapshot.groups[0].players, awards;
    if (top.length === 3 && state.rules.terminalPotTie === 'mleague' && state.pot % 3 !== 0) {
      var sticks = state.pot / 1000;
      awards = [400 * sticks, 300 * sticks, 300 * sticks];
    } else awards = splitUnits(state.pot / 100, top.length).map(function (units) { return units * 100; });
    var output = top.map(function (player, index) {
      afterScores[player] += awards[index];
      return { player: player, amount: awards[index] };
    });
    state.pot = 0;
    return output;
  }

  function settle(state, event) {
    assert(state.manualDisplay || state.phase === 'playing', '本局已经结算，请进入下一局或撤销本次结算');
    var visibleScores = scoresOf(state);
    // In operator mode a second confirmation revises this hand's payment.
    // Reverse only that payment, retaining subsequent profile/display edits.
    if (state.manualDisplay && state.phase !== 'playing' && state.lastResult) {
      var previous = state.lastResult;
      state.players.forEach(function (player, index) { player.score -= previous.deltas[index]; });
      state.pot += previous.potBefore - previous.potAfter;
      state.finalRanking = null;
    }
    var beforeScores = scoresOf(state), afterScores = beforeScores.slice(), potBefore = state.pot;
    var deltas = [0, 0, 0, 0], payments = [], potAwards = [], baseInfo = null, detail, title;
    if (event.type === 'draw') {
      assert(Array.isArray(event.tenpai), '请提供正式听牌的选手列表');
      event.tenpai.forEach(function (player) { playerIndex(player, '听牌选手'); });
      assert(new Set(event.tenpai).size === event.tenpai.length, '听牌选手不能重复');
      state.players.forEach(function (player, index) { assert(state.manualDisplay || !player.riichi || event.tenpai.indexOf(index) >= 0, '已立直选手必须声明听牌；若为诈立或裁定，请撤销错误事件后另行更正'); });
      var count = event.tenpai.length;
      if (count > 0 && count < 4) {
        state.players.forEach(function (_, player) {
          deltas[player] = event.tenpai.indexOf(player) >= 0 ? state.rules.notenPayment / count : -state.rules.notenPayment / (4 - count);
        });
      }
      afterScores = afterScores.map(function (score, player) { return score + deltas[player]; });
      title = '流局';
      detail = count === 0 ? '四家不听' : (count === 4 ? '四家听牌' : event.tenpai.map(function (player) { return state.players[player].name; }).join('、') + ' 听牌 · 场上 ' + formatPoints(state.rules.notenPayment) + ' 点');
    } else {
      playerIndex(event.winner, '和牌选手');
      if (event.type === 'ron') {
        playerIndex(event.loser, '放铳选手');
        assert(event.winner !== event.loser, '和牌者与放铳者不能是同一人');
      }
      baseInfo = winningBase(state, event);
      if (event.type === 'ron') {
        var ronBase = ceil100(baseInfo.base * (event.winner === state.dealer ? 6 : 4));
        var ronHonba = state.honba * state.rules.honbaRon;
        payments.push({ from: event.loser, to: event.winner, amount: ronBase + ronHonba, base: ronBase, honba: ronHonba });
      } else {
        state.players.forEach(function (_, player) {
          if (player === event.winner) return;
          var multiplier = event.winner === state.dealer || player === state.dealer ? 2 : 1;
          var paidBase = ceil100(baseInfo.base * multiplier), paidHonba = state.honba * state.rules.honbaTsumoEach;
          payments.push({ from: player, to: event.winner, amount: paidBase + paidHonba, base: paidBase, honba: paidHonba });
        });
      }
      payments.forEach(function (payment) { afterScores[payment.from] -= payment.amount; afterScores[payment.to] += payment.amount; });
      if (state.pot) { afterScores[event.winner] += state.pot; potAwards.push({ player: event.winner, amount: state.pot }); state.pot = 0; }
      title = state.players[event.winner].name + (event.type === 'tsumo' ? ' 自摸' : ' 荣和');
      detail = (baseInfo.yakuman ? baseInfo.limit : baseInfo.fu + ' 符 ' + baseInfo.han + ' 番' + (baseInfo.limit ? ' · ' + baseInfo.limit : ''));
      detail += ' · 基础 ' + formatPoints(sum(payments.map(function (payment) { return payment.base; }))) + ' 点';
      if (state.honba) detail += ' · ' + state.honba + ' 本场';
      if (potBefore) detail += ' · 供托 ' + formatPoints(potBefore);
    }
    var next = nextAfter(state, event, afterScores);
    var terminalAwards = [];
    if (next.finished) {
      // Freeze rank groups BEFORE dividing terminal deposits. A 400/300/300
      // allocation must never turn a shared first into a new sole first.
      state.finalRanking = rankingSnapshot(state, afterScores);
      terminalAwards = distributeTerminalPot(state, afterScores, state.finalRanking);
      potAwards = potAwards.concat(terminalAwards);
      if (event.type === 'draw' && potBefore) detail += state.rules.terminalPot === 'top' ? ' · 终局供托分配给首位' : ' · 终局供托 ' + formatPoints(potBefore) + ' 点未分配';
    }
    next.pot = state.pot;
    deltas = afterScores.map(function (score, player) { return score - beforeScores[player]; });
    state.players.forEach(function (player, index) { player.score = afterScores[index]; });
    state.phase = next.finished ? 'finished' : 'result';
    state.lastResult = {
      id: state.id + '-result-' + (state.revision + 1),
      kind: event.type, title: title, detail: detail,
      roundIndex: state.roundIndex, dealer: state.dealer, honba: state.honba,
      deltas: deltas, beforeScores: beforeScores, afterScores: afterScores,
      potBefore: potBefore, potAfter: state.pot,
      winner: event.type === 'draw' ? null : event.winner,
      loser: event.type === 'ron' ? event.loser : null,
      fu: baseInfo ? baseInfo.fu : null, han: baseInfo ? baseInfo.han : null,
      yakuman: baseInfo ? baseInfo.yakuman : 0, limit: baseInfo ? baseInfo.limit : '',
      basePoints: baseInfo ? sum(payments.map(function (payment) { return payment.base; })) : 0,
      payments: payments, potAwards: potAwards, terminalPotAwards: terminalAwards,
      tenpai: event.type === 'draw' ? event.tenpai.slice() : null,
      next: next
    };
    if (state.manualDisplay) state.lastResult.displayDeltas = afterScores.map(function (score, index) { return score - visibleScores[index]; });
  }

  function canonicalMeld(state, player, input) {
    assert(input && MELD_KINDS.indexOf(input.kind) >= 0, '请选择吃、碰、明杠、暗杠或加杠');
    assert(Array.isArray(input.tiles) && input.tiles.length > 0, '请提供牌组的牌面');
    input.tiles.forEach(normalizeTile);
    var kind = input.kind, normalized = input.tiles.map(normalizeTile), from = input.from;
    assert(state.manualDisplay || !state.players[player].riichi || kind === 'ankan', '立直后不能吃、碰或明杠');
    if (kind === 'kakan') {
      assert(input.tiles.length === 1 || input.tiles.length === 4, '加杠请提供新增的一张牌或完整的四张牌');
      assert(normalized.every(function (tile) { return tile === normalized[0]; }), '加杠必须是同一种牌');
      var suppliedIndex = input.meldIndex !== undefined ? input.meldIndex : input.ponIndex;
      var index = suppliedIndex !== undefined ? suppliedIndex : state.players[player].melds.findIndex(function (meld) { return meld.kind === 'pon' && normalizeTile(meld.tiles[0]) === normalized[0]; });
      var original = state.players[player].melds[index];
      if (state.manualDisplay && !original) {
        assert(state.players[player].melds.length < 4, '每家最多四组牌，不能继续新增');
        if (from === undefined || from === null) from = (player + 3) % 4;
        playerIndex(from, '鸣牌来源');
        assert(from !== player, '不能鸣自己的牌');
        var directTiles = input.tiles.length === 1 ? [normalized[0], normalized[0], normalized[0], input.tiles[0]] : input.tiles.slice();
        var directCalled = input.calledIndex === undefined ? 0 : input.calledIndex;
        assert(integer(directCalled) && directCalled >= 0 && directCalled < directTiles.length, '原横置牌的位置无效');
        var claimedTile = directTiles.splice(directCalled, 1)[0];
        var directRelation = (from - player + 4) % 4;
        var directSlot = directRelation === 3 ? 0 : directRelation === 2 ? 1 : 2;
        directTiles.splice(directSlot, 0, claimedTile);
        return { replaceIndex: -1, meld: { id: state.id + '-meld-' + (state.revision + 1), kind: 'kakan', tiles: directTiles, from: from, calledIndex: directSlot, addedIndex: 3, stackedOn: directSlot } };
      }
      assert(original && original.kind === 'pon' && normalizeTile(original.tiles[0]) === normalized[0], '加杠必须选择自己已有的同种碰牌');
      var added;
      if (input.tiles.length === 1) added = input.tiles[0];
      else {
        var remaining = input.tiles.slice();
        original.tiles.forEach(function (tile) {
          var found = remaining.indexOf(tile);
          assert(found >= 0, '加杠必须保留原碰牌的实际牌面，赤五不能被替换');
          remaining.splice(found, 1);
        });
        added = remaining[0];
      }
      var upgraded = copy(original);
      upgraded.kind = 'kakan';
      upgraded.tiles.push(added);
      upgraded.addedIndex = 3;
      upgraded.stackedOn = original.calledIndex;
      upgraded.upgradedFrom = 'pon';
      return { replaceIndex: index, meld: upgraded };
    }
    assert(state.players[player].melds.length < 4, '每家最多四组牌，不能继续新增');
    var kan = kind === 'ankan' || kind === 'daiminkan';
    assert(input.tiles.length === (kan ? 4 : 3), kan ? '杠牌必须提供四张牌' : '吃碰必须提供三张牌');
    var tiles = input.tiles.slice(), calledIndex = null;
    if (kind === 'chi') {
      if (from === undefined || from === null) from = (player + 3) % 4;
      assert(state.manualDisplay || from === (player + 3) % 4, '吃牌只能来自上家');
      var sorted = normalized.slice().sort();
      assert(sorted.every(function (tile) { return tile[1] === sorted[0][1] && tile[1] !== 'z'; }) && Number(sorted[1][0]) === Number(sorted[0][0]) + 1 && Number(sorted[2][0]) === Number(sorted[1][0]) + 1, '吃牌必须是同花色连续三张');
      var chosen = input.calledIndex === undefined ? 0 : input.calledIndex;
      assert(integer(chosen) && chosen >= 0 && chosen < 3, '被吃牌的位置必须是 0、1 或 2');
      var called = tiles.splice(chosen, 1)[0];
      tiles.sort(function (a, b) { return Number(normalizeTile(a)[0]) - Number(normalizeTile(b)[0]); });
      var chiRelation = (from - player + 4) % 4;
      calledIndex = state.manualDisplay ? (chiRelation === 3 ? 0 : chiRelation === 2 ? 1 : 2) : 0;
      tiles.splice(calledIndex, 0, called);
    } else {
      assert(normalized.every(function (tile) { return tile === normalized[0]; }), '碰杠牌必须是同一种牌');
      if (kind === 'ankan') from = null;
      else {
        playerIndex(from, '鸣牌来源');
        assert(from !== player, '不能鸣自己的牌');
        var sourceIndex = input.calledIndex === undefined ? 0 : input.calledIndex;
        assert(integer(sourceIndex) && sourceIndex >= 0 && sourceIndex < tiles.length, '鸣入牌的位置无效');
        var claimed = tiles.splice(sourceIndex, 1)[0];
        var relation = (from - player + 4) % 4;
        calledIndex = relation === 3 ? 0 : relation === 2 ? 1 : (kan ? 3 : 2);
        tiles.splice(calledIndex, 0, claimed);
      }
    }
    return { replaceIndex: -1, meld: { id: state.id + '-meld-' + (state.revision + 1), kind: kind, tiles: tiles, from: from, calledIndex: calledIndex } };
  }

  function applyEvent(inputState, inputEvent) {
    requireValid(inputState);
    assert(inputEvent && typeof inputEvent.type === 'string', '缺少事件类型');
    var state = copy(inputState), event = copy(inputEvent);
    if (event.id !== undefined) {
      assert(typeof event.id === 'string' && event.id.length > 0, '事件标识必须是非空字符串');
      assert((state.appliedEventIds || []).indexOf(event.id) < 0, '该事件已处理，请勿重复提交');
    }
    if (['ron', 'tsumo', 'draw'].indexOf(event.type) >= 0) settle(state, event);
    else if (event.type === 'next_hand') {
      assert(state.phase === 'result' && state.lastResult && !state.lastResult.next.finished, state.phase === 'finished' ? '本半庄已经结束，请新建半庄' : '请先完成本局结算');
      var next = state.lastResult.next;
      state.roundIndex = next.roundIndex;
      state.dealer = next.dealer;
      state.honba = next.honba;
      state.pot = next.pot;
      state.players.forEach(function (player) { player.riichi = false; player.melds = []; player.tenpai = false; player.waits = []; player.remaining = null; });
      state.doraIndicators = [];
      state.phase = 'playing';
      state.lastResult = null;
      state.finalRanking = null;
      state.handSequence = (state.handSequence || 0) + 1;
    } else {
      assert(state.manualDisplay || state.phase === 'playing', event.type === 'correction' ? '请先撤销本次结算，再更正对局状态' : '请先进入下一局或撤销本次结算');
      if (event.type === 'riichi') {
        var player = playerIndex(event.player);
        assert(!state.players[player].riichi, '该选手已立直，不能重复扣除立直棒');
        assert(state.manualDisplay || !isOpen(state.players[player]), '已有开放副露，不能立直');
        assert(state.manualDisplay || state.rules.allowNegative || state.players[player].score >= 1000, '当前规则要求至少持有 1000 点才能立直');
        state.players[player].score -= 1000;
        state.players[player].riichi = true;
        state.pot += 1000;
      } else if (event.type === 'meld') {
        var owner = playerIndex(event.player), normalizedMeld = canonicalMeld(state, owner, event.meld);
        if (normalizedMeld.replaceIndex >= 0) state.players[owner].melds[normalizedMeld.replaceIndex] = normalizedMeld.meld;
        else state.players[owner].melds.push(normalizedMeld.meld);
      } else if (event.type === 'dora') {
        normalizeTile(event.tile);
        state.doraIndicators.push(event.tile);
      } else if (event.type === 'correction') {
        assert(typeof event.reason === 'string' && event.reason.trim().length > 0, '手动更正需要填写原因');
        assert(['scores', 'pot', 'honba', 'roundIndex', 'dealer'].some(function (key) { return event[key] !== undefined; }), '请至少更正一个字段');
        var before = { scores: scoresOf(state), pot: state.pot, honba: state.honba, roundIndex: state.roundIndex, dealer: state.dealer };
        if (event.scores !== undefined) {
          assert(Array.isArray(event.scores) && event.scores.length === 4 && event.scores.every(pointValue), '更正点数需要四个有效的百点整数');
          state.players.forEach(function (player, index) { player.score = event.scores[index]; });
        }
        ['pot', 'honba', 'roundIndex', 'dealer'].forEach(function (key) { if (event[key] !== undefined) state[key] = event[key]; });
        if (event.roundIndex !== undefined && event.dealer === undefined) state.dealer = (state.initialDealer + event.roundIndex) % 4;
        var newTotal = sum(scoresOf(state)) + state.pot;
        var adjustment = newTotal - state.accountingTotal;
        state.accountingAdjustment = (state.accountingAdjustment || 0) + adjustment;
        state.accountingTotal = newTotal;
        state.lastCorrection = { reason: event.reason.trim(), revision: state.revision + 1, before: before, after: { scores: scoresOf(state), pot: state.pot, honba: state.honba, roundIndex: state.roundIndex, dealer: state.dealer }, accountingAdjustment: adjustment };
      } else fail('不支持的事件：' + event.type);
    }
    state.revision = (state.revision || 0) + 1;
    state.lastEvent = { type: event.type, id: event.id || null, revision: state.revision };
    state.appliedEventIds = state.appliedEventIds || [];
    if (event.id) state.appliedEventIds.push(event.id);
    requireValid(state);
    return state;
  }
  function previewSettlement(state, event) {
    assert(event && ['ron', 'tsumo', 'draw'].indexOf(event.type) >= 0, '只能预览荣和、自摸或流局结算');
    return applyEvent(state, event).lastResult;
  }

  function reviseSettlement(inputState, values) {
    // Operator-entered payments are authoritative; yaku remain descriptive.
    requireValid(inputState);
    assert(inputState.manualDisplay && inputState.phase !== 'playing' && inputState.lastResult, '请先选择已结算的小局');
    assert(values && Array.isArray(values.afterScores) && values.afterScores.length === 4 && values.afterScores.every(pointValue), '手动收支须为四个有效的百点整数');
    assert(integer(values.potAfter) && values.potAfter >= 0 && values.potAfter <= 1000000000 && values.potAfter % 1000 === 0, '局后供托须为非负千点整数');
    var state = copy(inputState), r = state.lastResult, after = values.afterScores.slice();
    var sameScores = after.every(function (score, i) { return score === state.players[i].score; });
    var samePot = state.pot === values.potAfter;
    state.pot = values.potAfter;
    var next = nextAfter(state, {type: r.kind, winner: r.winner, tenpai: r.tenpai}, after);
    next.pot = state.pot;
    var awarded = Math.max(0, r.potBefore - state.pot);
    if (sum(r.potAwards.map(function (award) { return award.amount; })) !== awarded) {
      r.potAwards = r.kind !== 'draw' && awarded ? [{player: r.winner, amount: awarded}] : [];
      r.terminalPotAwards = [];
    }
    r.deltas = after.map(function (score, i) { return score - r.beforeScores[i]; });
    r.afterScores = after.slice();
    r.displayDeltas = r.deltas.slice();
    r.potAfter = state.pot;
    r.next = next;
    r.manualPayment = true;
    r.detail += ' · 手动收支';
    // Reconstruct actual transfers for statistics and payment explanations.
    var ordinary = r.deltas.map(function (delta, i) {
      return delta - sum(r.potAwards.filter(function (a) { return a.player === i; }).map(function (a) { return a.amount; }));
    });
    var remaining = ordinary.slice();
    r.payments = [];
    remaining.forEach(function (value, from) {
      if (value >= 0) return;
      remaining.forEach(function (_, to) {
        var amount = Math.min(-remaining[from], Math.max(0, remaining[to]));
        if (amount <= 0) return;
        r.payments.push({from: from, to: to, amount: amount, base: amount, honba: 0});
        remaining[from] += amount;
        remaining[to] -= amount;
      });
    });
    r.basePoints = r.kind === 'draw' ? 0 : Math.max(0, ordinary[r.winner]);
    state.finalRanking = next.finished ? (sameScores && samePot && state.finalRanking || rankingSnapshot(state, after)) : null;
    state.players.forEach(function (player, i) { player.score = after[i]; });
    state.phase = next.finished ? 'finished' : 'result';
    var newTotal = sum(after) + state.pot;
    state.accountingAdjustment = (state.accountingAdjustment || 0) + newTotal - state.accountingTotal;
    state.accountingTotal = newTotal;
    requireValid(state);
    return state;
  }

  return {
    version: '1.0.0',
    createState: createState,
    applyEvent: applyEvent,
    previewSettlement: previewSettlement,
    reviseSettlement: reviseSettlement,
    computeStandings: computeStandings,
    validateState: validateState,
    tileFromIndicator: tileFromIndicator,
    indicatorFromTile: indicatorFromTile,
    defaultRules: copy(DEFAULT_RULES)
  };
});
