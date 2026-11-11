"""Ranking columns, presets and validation, with no rendering dependency."""
COLUMNS = {
    'rank': ('排名', 68, 'int'), 'name': ('个人昵称', 160, 'text'),
    'team': ('队伍名称', 168, 'text'), 'points': ('个人积分', 112, 'decimal'),
    'games': ('场次数', 110, 'text'), 'average_rank': ('平均顺位', 106, 'average'),
    'first': ('1位次数', 92, 'int'), 'second': ('2位次数', 92, 'int'),
    'third': ('3位次数', 92, 'int'), 'fourth': ('4位次数', 92, 'int'),
    'top_rate': ('Top率', 100, 'percent'), 'top_two_rate': ('连对率', 100, 'percent'),
    'avoid_fourth_rate': ('避四率', 100, 'percent'), 'max_score': ('最高分', 110, 'int'),
    'leader_gap': ('距第一名', 114, 'decimal'), 'cutoff_gap': ('距晋级线', 114, 'decimal'),
    'planned': ('计划场次', 110, 'int'), 'remaining': ('剩余场次', 110, 'int'),
    'progress': ('完成进度', 110, 'percent'),
}
PERSONAL = ['rank', 'team', 'name', 'points', 'games', 'average_rank', 'first', 'second',
            'third', 'fourth', 'top_rate', 'top_two_rate', 'avoid_fourth_rate', 'max_score']
TEAM = ['rank', 'team', 'points', 'leader_gap', 'cutoff_gap', 'games', 'first', 'second', 'third', 'fourth']
PLAN_COLUMNS = ['planned', 'remaining', 'progress']
ML_TECH = ['hands','average_win','call_rate','riichi_rate','win_rate','deal_in_rate','average_deal_in']
TECH = {
    'hands':('总局数',100,'int'),'calls':('副露局数',106,'int'),'riichi':('立直局数',106,'int'),
    'wins':('和牌次数',106,'int'),'deal_ins':('放铳次数',106,'int'),
    'call_rate':('副露率',108,'percent'),'riichi_rate':('立直率',108,'percent'),
    'win_rate':('和牌率',108,'percent'),'deal_in_rate':('放铳率',108,'percent'),
    'average_win':('平均打点',120,'score_average'),'average_deal_in':('平均铳点',120,'score_average'),
    'win_points':('累计和牌得点',145,'int'),'deal_in_points':('累计放铳失点',145,'int'),
    'tsumo_count':('自摸次数',108,'int'),'ron_count':('荣和次数',108,'int'),
    'tsumo_rate':('自摸占和牌比',145,'percent'),
    'riichi_win_rate':('立直后和牌率',145,'percent'),'riichi_deal_in_rate':('立直后放铳率',145,'percent'),
    'call_win_rate':('副露后和牌率',145,'percent'),'call_deal_in_rate':('副露后放铳率',145,'percent'),
    'dama_win_rate':('默听和牌占比',145,'percent'),
    'draw_count':('流局次数',110,'int'),'draw_tenpai_rate':('流局听牌率',140,'percent'),
    'riichi_average_win':('立直和牌平均打点',175,'score_average'),
    'call_average_win':('副露和牌平均打点',175,'score_average'),
}
COLUMNS.update(TECH)


def default_columns(kind,mode='individual'):
    return list(TEAM if mode=='team' else [k for k in PERSONAL if k!='team' or kind=='team_league'])


def preset_columns(kind,preset):
    identity=['rank']+(['team'] if kind=='team_league' else [])+['name']
    if preset=='ml':return default_columns(kind)+ML_TECH
    if preset=='technical':return identity+['hands','call_rate','riichi_rate','average_win','average_deal_in','win_rate','deal_in_rate','riichi_win_rate','call_win_rate','tsumo_rate','draw_tenpai_rate']
    return default_columns(kind)


def allowed_columns(kind, mode='individual'):
    return list(TEAM if mode == 'team' else default_columns(kind)+list(TECH))+PLAN_COLUMNS


def columns(event, mode='individual'):
    allowed = allowed_columns(event.get('kind', 'individual_league'), mode)
    selected = event.get('report_columns', {}).get(mode)
    return [k for k in selected if k in allowed] if selected else default_columns(event.get('kind','individual_league'),mode)


def validate_columns(mapping):
    if not isinstance(mapping, dict) or any(key not in ('individual', 'team') for key in mapping):
        raise ValueError('榜单列配置无效')
    for mode, values in mapping.items():
        allowed = (TEAM if mode == 'team' else PERSONAL+list(TECH))+PLAN_COLUMNS
        if not isinstance(values, list) or not values or len(set(values)) != len(values) or any(k not in allowed for k in values):
            raise ValueError('榜单列须非空、不重复且为支持的字段')
        if 'rank' not in values or ('team' if mode == 'team' else 'name') not in values:
            raise ValueError('榜单必须保留排名与名称')


def validate_headers(mapping):
    if not isinstance(mapping,dict) or any(k not in ('individual','team') for k in mapping):raise ValueError('表头名称配置无效')
    for mode,labels in mapping.items():
        if not isinstance(labels,dict) or any(k not in (TEAM if mode=='team' else PERSONAL+list(TECH))+PLAN_COLUMNS for k in labels):raise ValueError('表头字段无效')
        if any(not isinstance(v,str) or not v.strip() or len(v)>60 or '\n' in v or '\r' in v for v in labels.values()):raise ValueError('表头名称须为1至60个字符的单行文字')


def validate_baseline(player):
    from tournament import _integer, _finite
    base = player.get('result_baseline', {})
    if not isinstance(base, dict):raise ValueError('历史顺位基数无效')
    counts = base.get('counts', [0] * 4)
    if not isinstance(counts, list) or len(counts) != 4:raise ValueError('历史顺位须为四个数字')
    for value in counts:_integer(value, '历史顺位场次', 0, 1000000)
    if sum(counts) > player.get('games', 0):raise ValueError('历史顺位合计不能超过历史场次')
    if base.get('max_score') is not None:_finite(base['max_score'], '历史最高分')
