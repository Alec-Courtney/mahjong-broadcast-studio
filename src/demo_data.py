"""Bundled, explicitly labelled demonstration sessions.

Match results are synthetic, except the identified 2025 qualification-position
reproduction. Loading returns an independent JSON object; no live data is touched.
Run this module to rebuild the resources with the real settlement engine.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import tempfile

from app_paths import ROOT, APP_DIR
from model import Model
import tournament
from glossary import term_text

DEMO_SCENARIOS = [
    ('team_complete', '完整团队赛 · 40名演示选手'),
    ('individual_complete', '完整个人赛 · 16名演示选手'),
    ('animation_start', '开局与操作演示'),
    ('live_full', '完整对局显示'),
    ('m_tournament', '晋级条件复现'),
    ('team_ranking', '十队联赛排名'),
    ('individual_ranking', '十六人个人赛与统计'),
    ('match_finished', '完整半庄与赛后曲线'),
    ('final_edge', '同分、负分与终局供托'),
    ('long_names', '长名称与多役种结算'),
    ('yakuman_single', '单倍役满结算'),
    ('yakuman_multiple', '双倍役满结算'),
]
DEMO_DESCRIPTIONS = {
    'team_complete': 'M.League 的10支队伍、40名选手及官网肖像，附50个模拟半庄和逐局技术统计。可继续操作当前半庄。',
    'individual_complete': '16名 M.League 选手及官网肖像，附20个模拟半庄、个人积分、逐局技术统计和当前对局。',
    'animation_start': '四名演示选手的干净开局，供测试宝牌、副露、立直、和牌支付及自动推进。',
    'live_full': '五张宝牌、吃碰明杠暗杠加杠、立直与听牌、山存零和未知、本场及供托。',
    'm_tournament': '复现2025年6月2日A组第二场的公开积分局面，显示自摸与直击条件，可切换四进一、四进二、四进三。',
    'team_ranking': '十支虚构队伍的开场前已归档积分，仅高亮本桌队伍；底部按四席显示所属队伍名次及积分，半庄结束并记录本场积分后展示最终变化。',
    'individual_ranking': '十六名虚构选手的开场前已归档积分与历史统计，当前四人高亮；底部固定四席显示名次及积分，半庄结束并记录本场积分后展示最终结果。',
    'match_finished': '用真实结算引擎生成十二局完整半庄，包含两次流局、连庄、立直、副露、荣和与自摸；点数曲线四条线从起点同步延伸。',
    'final_edge': '南四局三家同点第一、第四家负分，1000点终局供托按400、300、300分配；并列顺位保持不变。',
    'long_names': '长姓名和长队名，十一番三倍满及多项役种，用于检查字号、换行和边界。',
    'yakuman_single': '大三元单倍役满的支付与结算显示，含负分选手。',
    'yakuman_multiple': '四暗刻加字一色的双倍役满，检查役满倍数、六万四千点支付及负分。',
}
DEMO_ROOT = ROOT / 'resources' / 'demo'
COLORS = ['#4b9270', '#bf4d63', '#4a81b8', '#dfb34e', '#836bb4', '#48a4a3', '#c97444', '#71859a', '#98a164', '#b06996', '#4e8aa0', '#af554c', '#8a91a9', '#c17e3a', '#629c8a', '#a6a9ac']
NAMES = ['青木 凛', '朝雾 遥', '白石 奏', '橘 结衣', '藤原 真', '樱井 澪', '神谷 悠', '高桥 晴', '浅野 葵', '森川 光', '结城 玲', '小野 莲', '中村 雪', '宫泽 诚', '水野 凪', '北原 奈绪']
TEAMS = ['青岚', '绯樱', '海蓝', '金曜', '紫苑', '翡翠', '琥珀', '银翼', '若叶', '星月', '天青', '朱雀', '月白', '橙光', '浅葱', '雪见']
KEYS = ['round', 'dora', 'player0', 'player1', 'player2', 'player3', 'yaku', 'qualification', 'commentary', 'standings', 'statistics', 'chart']


def _resolve_assets(value):
    if isinstance(value, dict):
        return {k: str(DEMO_ROOT / v[len('demo://'):]) if k == 'photo' and isinstance(v, str) and v.startswith('demo://') else _resolve_assets(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_assets(v) for v in value]
    return value


def load_demo_state(scenario_id='live_full'):
    """Return a complete Model.import_data payload, resolving bundled images."""
    if scenario_id not in dict(DEMO_SCENARIOS):
        raise ValueError('未知的演示场景')
    path = DEMO_ROOT / (scenario_id + '.json')
    if not path.is_file():
        raise ValueError('演示数据文件缺失')
    payload = json.loads(path.read_text(encoding='utf-8'))
    if payload.get('demo', {}).get('id') != scenario_id:
        raise ValueError('演示场景标识不一致')
    return _resolve_assets(payload)


def demo_league_event(payload, identity, title=None):
    from event_metadata import new_event
    from ranking_schema import allowed_columns

    demo = payload['demo']
    broadcast = payload['state']['broadcast']
    is_team = broadcast['config']['event_mode'] == 'team'
    kind = 'team_league' if is_team else 'individual_league'
    event = new_event(title or broadcast['config']['title'], kind, identity)
    planned = demo['planned_appearances_per_player']
    event.update(planned_games=planned * 4 if is_team else planned,
                 qualification_count=demo['qualification_count'],
                 player_plans={player['id']: planned for player in broadcast['roster']},
                 report_columns={'individual': allowed_columns(kind)},
                 demo_suite=dict(key=demo['id'], description=demo['description'], synthetic_results=True))
    return event


def load_into(model, scenario_id='live_full'):
    """Prepare a disposable model for fixture tests or preview generation."""
    if model.event_store is not None or model.event is not None or getattr(model, 'ui_only', False):
        raise ValueError('请在赛事库打开独立演示赛事')
    payload = load_demo_state(scenario_id)
    with tempfile.TemporaryDirectory(prefix='mahjong-demo-import-',dir=model.path) as temporary:
        path = Path(temporary) / 'session.json'
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        model.import_data(path)
    if payload['settings'].get('show_yaku') and model.last_info:
        model.setting('show_yaku', True)
    return model


def build_demo_model(scenario_id='live_full', data_dir=None):
    """Create an isolated model suitable for previews and animation recording."""
    temporary = tempfile.TemporaryDirectory(prefix='mahjong-demo-model-',dir=APP_DIR) if data_dir is None else None
    model = Model(data_dir if data_dir is not None else temporary.name)
    if temporary is not None:
        model._demo_temporary_directory = temporary
    return load_into(model, scenario_id)


make_demo_model = build_demo_model


def _settings(visible, show_yaku=False):
    windows = {
        'round': [20, 12, 218, 61], 'dora': [238, 12, 195, 61],
        'commentary': [340, 81, 600, 52], 'qualification': [20, 114, 208, 338],
        'standings': [20, 588, 1240, 108], 'statistics': [40, 60, 1200, 600],
        'chart': [0, 0, 1280, 720], 'yaku': [20, 530, 1240, 148],
    }
    for i in range(4):
        windows['player' + str(i)] = [10 + i * 317, 460, 305, 237]
    return dict(theme='saikouisen', locked=False, topmost=False, capture_bg='transparent', speed=1.,
                windows=windows, commentary_auto=True, commentary_auto_geometry=windows['commentary'][:], layout_version=7, visible={k: k in visible for k in KEYS},
                show_yaku=show_yaku, show_overlays=True, language='zh_CN', broadcast_scene='table')


def _baseline(index):
    hands = 180 + index * 13
    wins = 38 + index * 2
    deal_ins = 17 + index
    return dict(hands=hands, calls=42 + index * 3, riichi=34 + index * 2, wins=wins,
                deal_ins=deal_ins, win_points=wins * (6200 + index * 130), deal_in_points=deal_ins * (5200 + index * 140))


def _roster(count=4, historical=True):
    rows = []
    for i in range(count):
        rows.append(dict(id=f'demo-p{i+1}', name=NAMES[i], team=TEAMS[i], team_id=f'demo-team-{i+1}',
                         color=COLORS[i], photo=f'demo://avatars/{i+1}.png', base_points=0., games=20 + i if historical else 0,
                         status='active', stats=_baseline(i) if historical else {}))
    return rows


def _new_model(path, title, historical=True):
    model = Model(path)
    roster = _roster(historical=historical)
    model.configure_broadcast(dict(roster=roster, seats=[p['id'] for p in roster], title=title,
        start_points=25000, return_points=30000, uma=[50,10,-10,-30], oka_enabled=False, tie_break='share',
        qualification_tie_break='arrival', show_match_title=False, hud_opacity=.82, standings_mode='individual',
        standings_live=False, event_mode='individual', settlement_enabled=True, settlement_hold=3.0, settlement_total_hold=3.0, statistics_scope='all', statistics_view='rates',
        chart_mode='scores', statistics_page=0, qualification_slots=2, qualification_final=False,
        commentary_title=term_text('nidouke','zh_CN')[0], commentary_body=term_text('nidouke','zh_CN')[1],
        commentary_builtin=True, commentary_duration=0))
    model.field('title', title)
    model.start_match()
    # Stable IDs make fixture diffs and comparison exports easy to inspect.
    model.state['id'] = 'demo-' + Path(path).name
    return model


def _position(model, scores, round_index=0, dealer=0, honba=0, pot=0):
    def change(s):
        for player, score in zip(s['players'], scores):
            player['score'] = score
        s.update(roundIndex=round_index, dealer=dealer, honba=honba, pot=pot, accountingTotal=sum(scores)+pot)
    model.update(change)


def _win(model, winner, loser=None, han=3, fu=30, yakuman=0, yaku=None):
    event = dict(type='tsumo' if loser is None else 'ron', winner=winner, han=han, fu=fu, yakuman=yakuman)
    if loser is not None:
        event['loser'] = loser
    result = model.preview(event)
    player = model.state['players'][winner]
    info = dict(name=player['name'], winner=winner, kind=event['type'], color=player['color'], han=han, fu=fu,
                yakuman=yakuman, yaku=yaku or ['立直', '宝牌 2'], limit=result['limit'], base_points=result['basePoints'])
    model.settle(event, info)


def _serialize(model, scenario, visible, show_yaku=False, fictional=True):
    model.settings = _settings(visible, show_yaku)
    if model.state['broadcast']['config'].get('show_match_title'):
        model.settings['windows']['round'][3] = 81
        model.settings['windows']['dora'][3] = 81
    tournament.validate_broadcast(model.state)
    model.engine.validate(model.state)
    return dict(format='MahjongOverlayStudio', version=6, state=copy.deepcopy(model.state),
                settings=copy.deepcopy(model.settings), last_info=copy.deepcopy(model.last_info),
                demo=dict(id=scenario, label=dict(DEMO_SCENARIOS)[scenario], description=DEMO_DESCRIPTIONS[scenario],
                          fictional=fictional, recorded_by='real settlement engine',
                          source='reference/broadcast/20261025/research.json' if not fictional else 'synthetic demonstration'))


def _complete_match(model):
    # E1 repeat, E2 repeat on draw, E3 repeat, S3 repeat -> 12 total hands.
    events = [
        ('ron',0,1,3,30), ('tsumo',2,None,2,30), ('draw',[1,2],None,0,0),
        ('ron',3,0,4,40), ('ron',2,3,2,40), ('ron',1,2,5,30),
        ('tsumo',0,None,3,40), ('draw',[1,2,3],None,0,0),
        ('ron',3,1,3,30), ('tsumo',2,None,3,40), ('ron',0,2,3,30), ('tsumo',1,None,4,30),
    ]
    for number, event in enumerate(events):
        kind, winner, loser, han, fu = event
        model.dora(['5m','3p','7s','6z'][number % 4])
        if kind == 'draw':
            model.riichi(winner[0])
            for index in winner:
                model.set_tenpai(index, True)
            model.meld((winner[0]+2)%4, 'pon', ['5p']*3, (winner[0]+3)%4)
            model.draw()
        else:
            # Alternate open/closed winners; other players also have recorded calls.
            if number in (3,5):
                model.meld(winner,'chi',['2m','3m','4m'],(winner+3)%4)
                yaku=['断幺九','宝牌 '+str(max(1,han-1))]
            else:
                model.riichi(winner)
                yaku=['立直','门前清自摸和'] + (['宝牌 '+str(han-2)] if han>2 else []) if kind=='tsumo' else ['立直','宝牌 '+str(han-1)]
            caller=(winner+1)%4
            model.meld(caller,'pon',['7s']*3,(caller+1)%4)
            _win(model,winner,loser,han,fu,yaku=yaku)
        if number < len(events)-1:
            if not model.next_hand():
                raise ValueError('演示半庄未按预期推进')
    if model.state['phase'] != 'finished' or len(model.state['broadcast']['hands']) != 12:
        raise ValueError('演示半庄生成失败')
    model.commit_match()


def _avatar_files(destination):
    """Abstract demo identity cards, deliberately not portraits of real people."""
    from PySide6.QtCore import Qt,QRectF
    from PySide6.QtGui import QImage,QPainter,QColor,QFont,QFontDatabase,QFontMetrics,QLinearGradient
    import os
    # Offscreen Qt may not discover Windows fonts. Register a concrete font before
    # baking labels into PNG; otherwise even ASCII can turn into missing-glyph boxes.
    font_root=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
    families=[]
    for path in (font_root/'msyh.ttc',font_root/'arial.ttf',ROOT/'resources'/'NotoSansCJKjp-Regular.otf'):
        if not path.is_file():continue
        identifier=QFontDatabase.addApplicationFont(str(path))
        if identifier>=0:families.extend(QFontDatabase.applicationFontFamilies(identifier))
    families.extend(QFontDatabase.families())
    label_font=None
    for family in families:
        candidate=QFont(family);candidate.setPixelSize(32);candidate.setBold(True)
        metrics=QFontMetrics(candidate)
        if all(metrics.inFont(letter) for letter in 'DEMO0123456789'):
            label_font=candidate;break
    if label_font is None:raise ValueError('演示头像字体不可用')
    folder=destination/'avatars';folder.mkdir(parents=True,exist_ok=True)
    for i,color in enumerate(COLORS):
        image=QImage(360,495,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor('#101a24'))
        painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient=QLinearGradient(0,0,360,495);gradient.setColorAt(0,QColor(color));gradient.setColorAt(1,QColor('#111b28'))
        painter.fillRect(image.rect(),gradient);painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255,255,255,35));painter.drawEllipse(QRectF(-65,215,470,470))
        painter.setBrush(QColor(255,255,255,210));painter.drawEllipse(QRectF(117,98,126,126))
        painter.drawRoundedRect(QRectF(70,236,220,215),105,105)
        painter.setPen(QColor('#ffffff'));painter.setFont(label_font)
        painter.drawText(QRectF(20,18,320,50),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,f'DEMO  {i+1:02d}')
        painter.end()
        if not image.save(str(folder/f'{i+1}.png')):raise ValueError('演示头像生成失败')


def generate_demo_resources(destination=None):
    """Reproducible builder; never opens the user's data directory."""
    from PySide6.QtWidgets import QApplication
    app=QApplication.instance() or QApplication([])
    destination=Path(destination or DEMO_ROOT);destination.mkdir(parents=True,exist_ok=True)
    _avatar_files(destination)
    payloads={}
    table=['round','dora','player0','player1','player2','player3','yaku']
    with tempfile.TemporaryDirectory(prefix='mahjong-demo-generation-',dir=APP_DIR) as temporary:
        for scenario,label in DEMO_SCENARIOS:
            if scenario in ('team_complete','individual_complete'):
                from complete_demo import generate_complete_fixture
                payloads[scenario]=generate_complete_fixture(scenario,destination)
                continue
            model=_new_model(Path(temporary)/scenario,'演示 · '+label,historical=scenario in ('live_full','team_ranking','individual_ranking'))
            visible=list(table);show_yaku=False;fictional=True
            if scenario=='animation_start':
                model.field('honba',1)
            elif scenario=='live_full':
                _position(model,[28000,21000,31000,18000],round_index=2,dealer=2,honba=2,pot=2000)
                model.riichi(0);model.set_waits(0,['2m','5m','8m']);model.set_remaining(0,0)
                model.set_waits(1,['3p','6p']);model.set_remaining(1,None)
                model.set_waits(3,['5z']);model.set_remaining(3,2)
                model.meld(1,'chi',['3m','4m','5m'],0);model.meld(1,'pon',['7p']*3,2)
                model.meld(2,'pon',['6s']*3,0);model.meld(2,'kakan',['6s'],0)
                model.meld(2,'daiminkan',['7z']*4,3);model.meld(3,'ankan',['1p']*4,None)
                for tile in ['1m','5p','7s','1z','5z']:model.dora(tile)
                visible.append('commentary')
            elif scenario=='m_tournament':
                roster=copy.deepcopy(model.state['broadcast']['roster'])
                for p,name,points in zip(roster,['岡田紗佳','二階堂瑠美','三浦智博','石井一馬'],[1.4,67.7,-46.7,-22.4]):
                    p.update(name=name,team='',team_id='',base_points=points,games=1,stats={},photo='')
                model.configure_broadcast(dict(roster=roster,qualification_slots=2,qualification_final=True,qualification_focus='demo-p3',
                                               commentary_title=term_text('one_chance','zh_CN')[0],commentary_body=term_text('one_chance','zh_CN')[1]))
                _position(model,[27200,15900,27000,29900],round_index=7,dealer=3)
                model.dora('5p');visible+=['qualification','commentary'];fictional=False
            elif scenario=='team_ranking':
                roster=_roster(10)
                for p,points in zip(roster,[132.4,119.6,101.5,70.0,56.4,-12.0,-21.4,-64.2,-95.3,-151.1]):p['base_points']=points
                for p in roster[7:]:p['status']='eliminated'
                model.configure_broadcast(dict(roster=roster,event_mode='team',standings_mode='team',sidebar_mode='team'))
                _position(model,[17000,39000,26000,18000],round_index=6,dealer=2)
                visible=list(table)+['qualification']
            elif scenario=='individual_ranking':
                roster=_roster(16)
                for p,points in zip(roster,[88.6,71.3,62.7,44.1,18.8,-5.4,-31.2,-82.6,400.,350.,300.,250.,200.,175.,150.,125.]):p['base_points']=points
                model.configure_broadcast(dict(roster=roster,standings_mode='individual',sidebar_mode='individual',statistics_scope='all'))
                _position(model,[39000,17000,18000,26000],round_index=5,dealer=1)
                visible=list(table)+['qualification']
            elif scenario=='match_finished':
                _complete_match(model)
                model.configure_broadcast(dict(statistics_scope='match',statistics_view='result'))
                visible=['chart']
            elif scenario=='final_edge':
                _position(model,[36000,36000,36000,-9000],round_index=7,dealer=3,pot=1000)
                model.update(lambda s:s['rules'].update(terminalPot='top',terminalPotTie='equal_100'))
                model.draw();model.commit_match();model.configure_broadcast(dict(statistics_scope='match',statistics_view='result'))
                visible=['statistics']
            elif scenario=='long_names':
                roster=copy.deepcopy(model.state['broadcast']['roster'])
                roster[0].update(name='竞技麻将特别代表・青木 凛',team='晨星联合竞技麻将俱乐部特别代表队')
                roster[1].update(name='International Riichi Club Akira',team='International Invitational')
                model.configure_broadcast(dict(roster=roster,show_match_title=True))
                _position(model,[25000]*4,round_index=7,dealer=3)
                model.riichi(0)
                _win(model,0,3,11,30,yaku=['立直','一发','平和','断幺九','三色同顺','宝牌 3','赤宝牌 1','里宝牌 1'])
                visible=['yaku'];show_yaku=True
            elif scenario in ('yakuman_single','yakuman_multiple'):
                _position(model,[25000]*4,round_index=7,dealer=3)
                multiple=1 if scenario=='yakuman_single' else 2
                _win(model,1,0,0,30,multiple,['大三元'] if multiple==1 else ['四暗刻','字一色'])
                visible=['yaku'];show_yaku=True
            payloads[scenario]=_serialize(model,scenario,visible,show_yaku,fictional)
            (destination/(scenario+'.json')).write_text(json.dumps(payloads[scenario],ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    catalog=[dict(id=k,label=label,description=DEMO_DESCRIPTIONS[k],filename=k+'.json') for k,label in DEMO_SCENARIOS]
    (destination/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding='utf-8')
    return payloads


if __name__=='__main__':
    created=generate_demo_resources()
    print(f'Generated {len(created)} demonstration sessions.')
