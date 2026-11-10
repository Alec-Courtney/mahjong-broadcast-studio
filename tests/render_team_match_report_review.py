"""Reference-style review from two confirmed halves in a disposable event."""
import copy
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QWidget
from event_history import EventHistoryDialog
from event_results import prepare_result, record_result
from event_store import EventStore
from model import Model
from team_match_preview import TeamMatchReportDialog
from team_match_report import build_report, export_png, render_report
from ui_test_support import settle

APP = QApplication.instance() or QApplication([])
for name in ('msyh.ttc', 'msyhbd.ttc'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + name)
APP.setFont(QFont('Microsoft YaHei', 10))
OUT = ROOT / 'previews' / 'checks' / 'team-match-results'
OUT.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(dir=ROOT / '.cache', prefix='team-report-review-') as directory:
    model = Model(directory)
    model.settings.update(console_font_family='Microsoft YaHei', topmost=False)
    store = EventStore(model)
    store.create('示例赛事', 'team_league')
    model.configure_broadcast(dict(start_points=25000, return_points=30000, uma=[30, 10, -10, -30], oka_enabled=True))
    names = ['风铃儿Jinger', '学术の青年', '萌新烂菜叶', '天天独秀',
             '梧桐树下的小易', '时透缘壹', '真的玩不了哩']
    teams = ['地狱守门人', '炽焰天穹队', '野猪奔芙队', '月下梦之队']
    colors = ['#f84d3d', '#a0ff29', '#ffc100', '#b997d8']
    roster = []
    template = model.state['broadcast']['roster'][0]
    for i, name in enumerate(names):
        team = i if i < 4 else {4: 0, 5: 2, 6: 3}[i]
        player = copy.deepcopy(template)
        player.update(id=f'p{i+1}', name=name, team=teams[team], team_id=f'team-{team}', color=colors[team])
        roster.append(player)
    model.set_roster(roster)
    ids = []
    for number, (seats, scores) in enumerate((
            (['p1', 'p2', 'p3', 'p4'], [38700, 8200, 16300, 36800]),
            (['p5', 'p2', 'p6', 'p7'], [35000, 22000, 25700, 17300])), 1):
        match = prepare_result(model, seats, scores, title=f'第{number}半庄')
        record_result(store, match)
        ids.append(match['id'])
    data = build_report(model.state, model.event, ids)
    export_png(render_report(data), OUT / '队伍场次结果示例.png')
    owner = QWidget()
    owner.model, owner.store = model, store
    history = EventHistoryDialog(owner, model.state, model.event, select_matches=True)
    history.check_matches(True)
    history.show()
    settle(APP)
    history.grab().save(str(OUT / '半庄勾选窗口.png'))
    preview = TeamMatchReportDialog(history, ids)
    preview.show()
    settle(APP)
    preview.grab().save(str(OUT / '结果图预览窗口.png'))
    preview.close()
    history.close()
    owner.deleteLater()
    APP.processEvents()
print(OUT)
