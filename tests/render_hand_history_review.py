"""Isolated desk previews for the persistent hand ledger and correction dialog."""
import os
from pathlib import Path
import sys
import tempfile
import json

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
from console import Console
from model import Model
from overlays import OverlayManager
from hand_history_panel import HandCorrectionDialog
from ui_test_support import settle, visibility

APP = QApplication.instance() or QApplication([])
for font in ('msyh.ttc', 'msyhbd.ttc'):
    QFontDatabase.addApplicationFont(str(Path('C:/Windows/Fonts') / font))
APP.setFont(QFont('Microsoft YaHei', 10))
OUT = ROOT / 'previews' / 'checks' / 'hand-history'
OUT.mkdir(parents=True, exist_ok=True)
report = []
with tempfile.TemporaryDirectory(dir=ROOT / '.cache', prefix='ledger-review-') as directory:
    model = Model(directory)
    model.settings.update(show_overlays=False, topmost=False, console_layout=None, console_font_family='Microsoft YaHei')
    manager = OverlayManager(model)
    ui = Console(model, manager)
    ui.show()
    model.riichi(1)
    model.settle(dict(type='ron', winner=1, loser=2, han=3, fu=40), dict(yaku=['立直', '宝牌 2']))
    model.next_hand()
    model.riichi(3)
    model.draw([0, 3])
    model.next_hand()
    model.settle(dict(type='tsumo', winner=2, han=2, fu=30), dict(yaku=['门前清自摸和', '断幺九']))
    try:
        for width, height in ((1520, 1050), (1280, 800), (960, 620)):
            ui.resize(width, height)
            settle(APP)
            ui.grab().save(str(OUT / f'console-{width}x{height}.png'))
            panel = ui.hand_history
            report.append(dict(size=[ui.width(), ui.height()], edit=visibility(panel.edit_latest, ui),
                               header=visibility(panel.history.horizontalHeader(), ui),
                               panel=list(ui.workspace.docks['history'].geometry().getRect()),
                               body=list(panel.geometry().getRect()),
                               scroll=ui.workspace.scrolls['history'].horizontalScrollBar().maximum()))
        ui.resize(1280, 800)
        dialog = HandCorrectionDialog(ui, model.state['broadcast']['hands'][0]['id'])
        dialog.resize(1020, 790)
        dialog.show()
        settle(APP)
        dialog.grab().save(str(OUT / 'hand-details.png'))
        dialog.deltas[1].setValue(9000)
        dialog.deltas[2].setValue(-9000)
        settle(APP)
        dialog.grab().save(str(OUT / 'manual-payment.png'))
        dialog.close()
        dialog.deleteLater()
    finally:
        ui.close()
        ui.deleteLater()
        APP.processEvents()
(OUT / 'review.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
