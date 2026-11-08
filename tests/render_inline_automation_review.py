"""Review the native desk with per-player recognition and existing wait/win editors."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
from console import Console
from model import Model, parse_tiles
from overlays import OverlayManager
from ui_test_support import reveal, visibility

OUT = ROOT / 'previews' / 'checks' / 'operator-console' / 'dark'
HANDS = ('123m123p123s1112z', '123m456p12789s11z', '123456m234p678s1z', '999m333p5s666777z')


def pump(app, seconds=.12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--theme',choices=('dark','light'),default='dark')
    args=parser.parse_args()
    global OUT
    if args.theme=='light':OUT=ROOT/'previews'/'checks'/'operator-console'/'light'
    app = QApplication.instance() or QApplication([])
    for filename in ('msyh.ttc', 'msyhbd.ttc'):
        path = Path('C:/Windows/Fonts') / filename
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont('Microsoft YaHei', 10))
    OUT.mkdir(parents=True, exist_ok=True)
    report = {'formal_event_data_touched': False, 'separate_automation_window': False, 'theme':args.theme, 'sizes': []}
    with tempfile.TemporaryDirectory(prefix='inline-review-', dir=ROOT / 'tests') as directory:
        model = Model(directory)
        model.settings.update(show_overlays=False, topmost=False, console_font_family='Microsoft YaHei',console_theme=args.theme)
        manager = OverlayManager(model)
        ui = Console(model, manager)
        ui.show()
        try:
            for player, hand in enumerate(HANDS):
                ui.inline_automation.receive_tiles(player, parse_tiles(hand), source='验收模拟输入', confidence=.96)
            ui.select(2)
            pump(app)
            for width, height in ((1920, 1080), (1280, 800), (960, 620)):
                ui.resize(width, height)
                pump(app)
                for scroll in ui.workspace.scrolls.values():
                    scroll.verticalScrollBar().setValue(0)
                pump(app)
                filename = f'original_desk_{width}x{height}.png'
                ui.grab().save(str(OUT / filename))
                widgets = {f'recognized_hand_{i}': widget for i, widget in enumerate(ui.recognized_hands)}
                widgets.update(wait_submit=ui.submit_wait_button, win_tile=ui.win_tile,
                               ura_indicators=ui.ura_indicators, win_fill=ui.win_fill_button,
                               winner=ui.winner, loser=ui.loser, settle=ui.settle_button)
                initially_visible={name:visibility(widget,ui) for name,widget in widgets.items()}
                reachable = {}
                for name, widget in widgets.items():
                    reveal(widget, app)
                    reachable[name] = visibility(widget, ui)
                report['sizes'].append(dict(size=[width, height], image=filename, reachable=reachable,initially_visible=initially_visible,
                    panels={key:list(dock.geometry().getRect()) for key,dock in ui.workspace.docks.items() if dock.isVisible()},
                    horizontal_scrollbars={key: scroll.horizontalScrollBar().maximum() for key, scroll in ui.workspace.scrolls.items()}))
            ui.resize(1920, 1080)
            pump(app)
            for scroll in ui.workspace.scrolls.values():
                scroll.verticalScrollBar().setValue(0)
            ui.workspace.docks['players'].grab().save(str(OUT / 'four_players_recognized_hands.png'))
            ui.yaku_buttons['立直'].click()
            ui.yaku_buttons['一发'].click()
            ui.win_tile.setText('1z')
            ui.ura_indicators.setText('1p')
            ui.win_tile.editingFinished.emit()
            ui.ura_indicators.editingFinished.emit()
            pump(app)
            ui.workspace.ensure_visible(ui.score_box)
            pump(app)
            ui.grab().save(str(OUT / 'original_desk_assisted_win.png'))
            event, roles = ui.win_event()
            report['assisted_win'] = {'event': event, 'roles': roles,
                'phase': model.state['phase'], 'no_settlement': model.state['phase'] == 'playing'}
            report['player_hands'] = [widget.tiles for widget in ui.recognized_hands]
            ui.select(0)
            ui.furiten.setChecked(True)
            ui.submit_wait_button.click()
            pump(app)
            ui.grab().save(str(OUT/'original_desk_published_wait.png'))
            report['published_wait_preview']=ui.wait_preview_label.text()
            # One original player row edits through the shared large palette.
            ui.select(0)
            hand=ui.recognized_hands[0]
            hand.begin_tile_edit(len(hand.tiles)-1)
            ui.tile_buttons['3z'].click()
            pump(app)
            ui.grab().save(str(OUT/'original_desk_hand_editing.png'))
            report['hand_correction']={'before':list(hand.tiles),'draft':hand.draft_tiles,
                'model_waits_before_apply':list(model.state['players'][0]['waits']),
                'tile_target':ui.tile_target,'selected_index':hand.selected_index}
            hand.edit_buttons['apply'].click()
            pump(app)
            ui.grab().save(str(OUT/'original_desk_hand_corrected.png'))
            report['hand_correction'].update(after=list(hand.tiles),
                model_waits_after_apply=list(model.state['players'][0]['waits']),
                proposed_waits=list(ui.wait_draft()['waits']),manual_locked=0 in ui.inline_automation.manual_hands)
        finally:
            ui.close()
            app.processEvents()
    (OUT / 'review_checks.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'sizes': [entry['size'] for entry in report['sizes']], 'assisted_win': report['assisted_win']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
