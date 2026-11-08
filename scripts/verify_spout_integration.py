"""Opt-in native Spout -> ONNX -> original console acceptance, using photo crops.

Requires INSTALL-Automation setup. All match writes go to a temporary directory.
This deliberately constructed sample is neither real game footage nor an
accuracy benchmark, and it does not claim live OBS/hardware acceptance.
"""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
OUT = ROOT / 'previews' / 'checks' / 'spout'
WAITS = ['1m', '3m', '2s', '5s']
BASE = ['9m'] * 3 + ['3p'] * 3 + ['6z'] * 3 + ['7z'] * 3


def main():
    import cv2
    import numpy as np
    import SpoutGL
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from app_paths import LOCAL_RECOGNITION_SAMPLES
    from console import Console
    from model import Model
    from overlays import OverlayManager
    from recognition_config import default_recognition_config
    from recognition_worker import RecognitionWorker
    from spout_capture import enumerate_senders
    from tile_recognition import MEDIUM_MODEL, OnnxTileRecognizer, read_image, write_image

    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    for filename in ('msyh.ttc', 'msyhbd.ttc'):
        path = Path('C:/Windows/Fonts') / filename
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont('Microsoft YaHei', 10))
    original = LOCAL_RECOGNITION_SAMPLES / 'mleague-hand-hd.jpg'
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    assert digest == '88b364a3c0ca337089bc1dddb7538a3efae2c35b5441504820aa36d645548db4'
    photo = read_image(original)
    detector = OnnxTileRecognizer(MEDIUM_MODEL)
    detected = detector.recognize(photo, roi=(500, 210, 750, 340))
    images = []
    for seat, wait in enumerate(WAITS):
        expected = BASE + [wait]
        hand = np.full((160, 872, 3), (72, 115, 45), dtype=np.uint8)
        for index, tile in enumerate(expected):
            box = max((d for d in detected.detections if d.tile == tile), key=lambda d: d.confidence).bbox
            x1, y1, x2, y2 = map(round, box)
            hand[40:136, 20 + index * 64:84 + index * 64] = cv2.resize(photo[y1:y2, x1:x2], (64, 96), interpolation=cv2.INTER_CUBIC)
        assert Counter(detector.recognize(hand).tiles) == Counter(expected)
        write_image(OUT / f'constructed-position-{seat + 1}.png', hand)
        images.append(cv2.cvtColor(hand, cv2.COLOR_BGR2RGBA))
    del detector
    names = ['MahjongAcceptance_' + uuid.uuid4().hex[:8] + '_' + str(seat + 1) for seat in range(4)]
    sender_stops = [threading.Event() for _ in range(4)]
    senders = []
    errors = []
    inspector = SpoutGL.SpoutReceiver()
    previous_active = inspector.getActiveSender()

    def send(seat):
        sender = SpoutGL.SpoutSender()
        try:
            assert sender.createOpenGL()
            sender.setSenderName(names[seat])
            while not sender_stops[seat].is_set():
                assert sender.sendImage(images[seat], 872, 160, SpoutGL.enums.GL_RGBA, False, 0)
                sender_stops[seat].wait(.06)
        except Exception as error:
            errors.append(f'sender {seat}: {error}')
        finally:
            sender.releaseSender()
            sender.closeOpenGL()

    def pump(predicate, timeout=50):
        deadline = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if errors:
                raise AssertionError(errors)
            if time.monotonic() >= deadline:
                raise TimeoutError('Native Spout/ONNX integration timed out')
            time.sleep(.01)
        app.processEvents()

    started = time.monotonic()
    ui = preview = None
    report = dict(native_spout_tested=True, actual_onnx_tested=True, live_obs_tested=False,
                  physical_camera_tested=False, normal_event_data_touched=False,
                  source_kind='constructed repeated photo crops, not actual broadcast hands',
                  photo_source=str(original.relative_to(ROOT)), photo_sha256=digest,
                  model=MEDIUM_MODEL.name, expected_waits=WAITS)
    try:
        for seat in range(4):
            thread = threading.Thread(target=send, args=(seat,), daemon=True)
            senders.append(thread)
            thread.start()
        pump(lambda: all(name in enumerate_senders() for name in names))
        with tempfile.TemporaryDirectory(prefix='spout-integration-', dir=OUT) as directory:
            model = Model(directory)
            config = default_recognition_config()
            config['sources'] = [dict(enabled=True, kind='spout', path=name, roi=[0., 0., 1., 1.]) for name in names]
            model.settings.update(show_overlays=False, topmost=False, console_font_family='Microsoft YaHei', recognition_assist=config)
            manager = OverlayManager(model)
            ui = Console(model, manager)
            ui.resize(1600, 960)
            ui.show()
            controller = ui.inline_automation
            counts = Counter()
            durations = {seat: [] for seat in range(4)}
            def observed(seat, image, result):
                counts[seat] += 1
                durations[seat].append(result.elapsed_ms)
            controller.preview_received.connect(observed)
            baseline = copy.deepcopy(model.state)
            pump(lambda: all(ui.wait_drafts.get(seat, {}).get('waits') == [WAITS[seat]] for seat in range(4)))
            initial = dict(controller.workers)
            report['draft_waits'] = [ui.wait_drafts[seat]['waits'] for seat in range(4)]
            report['no_match_mutation_before_confirmation'] = model.state == baseline and not model.undo
            assert report['no_match_mutation_before_confirmation']
            for seat in (3, 1, 2):
                before = counts.copy()
                inspector.setActiveSender(names[seat])
                ui.select(seat)
                model.setting('broadcast_scene', 'clean' if seat == 1 else 'table')
                pump(lambda: all(counts[index] > before[index] for index in range(4)))
                assert controller.workers == initial
            report['active_sender_and_console_scene_switches_preserve_four_workers'] = True
            preview_count = []
            preview = RecognitionWorker(config['sources'][0], model_path=MEDIUM_MODEL)
            preview.frame_ready.connect(lambda image, result: preview_count.append(result.tiles))
            preview.failed.connect(errors.append)
            before = counts.copy()
            preview.start()
            pump(lambda: len(preview_count) >= 2 and all(counts[index] > before[index] for index in range(4)))
            preview.requestInterruption()
            pump(lambda: not preview.isRunning())
            report['parallel_settings_preview_frames'] = len(preview_count)
            before = counts.copy()
            pump(lambda: all(counts[index] > before[index] for index in range(4)))
            report['preview_stop_preserves_four_streams'] = True

            ui.select(0)
            ui.loser.setCurrentIndex(1)
            ui.win_tile.setText('1m')
            assert controller.recalculate_win(force=True), ui.win_assist_hint.text()
            ui.preview()
            assert ui.settle_button.isEnabled(), ui.preview_error.text()
            report['win_preview'] = dict(text=ui.win_preview_label.text(),
                yaku=[name for name, button in ui.yaku_buttons.items() if button.isChecked()],
                han=ui.han.value(), fu=ui.fu.currentData())
            ui.grab().save(str(OUT / 'original-console-spout-four-hands.png'))
            sender_stops[0].set()
            senders[0].join(3)
            pump(lambda: controller.observations[0].get('stale') and not ui.settle_button.isEnabled(), timeout=12)
            assert 0 not in controller.wait_ids
            report['disconnect_blocks_stale_win_and_wait_draft'] = True
            report['disconnect_hint'] = ui.preview_error.text()
            other_counts = counts.copy()
            pump(lambda: all(counts[index] > other_counts[index] for index in (1, 2, 3)))
            sender_stops[0].clear()
            thread = threading.Thread(target=send, args=(0,), daemon=True)
            senders.append(thread)
            thread.start()
            pump(lambda: not controller.observations[0].get('stale') and ui.wait_drafts.get(0, {}).get('waits') == ['1m'])
            assert controller.workers == initial
            assert controller.recalculate_win(force=True)
            assert ui.settle_button.isEnabled()
            report['same_sender_reconnect_restores_assistance'] = True
            report['still_uncommitted_before_operator_action'] = model.state == baseline
            for seat in range(4):
                ui.select(seat)
                pump(lambda seat=seat: seat in controller.wait_ids)
                ui.submit_wait_button.click()
                pump(lambda seat=seat: model.state['players'][seat].get('waits') == [WAITS[seat]])
            report['original_wait_submit_commits_each_seat'] = [p['waits'] for p in model.state['players']]
            report['frame_updates'] = dict(counts)
            report['median_inference_ms'] = {str(seat + 1): round(statistics.median(samples), 1) for seat, samples in durations.items()}
            report['elapsed_seconds'] = round(time.monotonic() - started, 2)
            ui.close()
            app.processEvents()
            ui = None
    finally:
        if preview and preview.isRunning():
            preview.requestInterruption()
            preview.wait(5000)
        if ui:
            ui.close()
            app.processEvents()
        for event in sender_stops:
            event.set()
        for thread in senders:
            thread.join(4)
        if previous_active and previous_active in enumerate_senders():
            inspector.setActiveSender(previous_active)
        inspector.releaseReceiver()
    (OUT / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
