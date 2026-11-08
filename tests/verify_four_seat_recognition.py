"""Actual ONNX four-position verification over a shared local replay.

The four feeds are explicitly constructed from real-photo crops. The mock camera
wraps a local video file and simulates an OBS output fixed independently of the
Program scene. This does not certify a live OBS or physical-camera installation.
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
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import cv2
import numpy as np
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
from app_paths import LOCAL_RECOGNITION_SAMPLES
from camera_devices import CameraDevice
from console import Console
from hand_calculation import analyse_hand
from model import Model
from overlays import OverlayManager
from recognition_config import default_recognition_config
from shared_camera import CameraCapturePool
from tile_recognition import MEDIUM_MODEL, OnnxTileRecognizer, VideoSource, annotate, read_image, write_image

OUT = ROOT / 'previews' / 'checks' / 'four-seat'
SINGLES = ('1m', '3m', '2s', '5s')
BASE_TILES = ['9m'] * 3 + ['3p'] * 3 + ['6z'] * 3 + ['7z'] * 3
QUADS = ((0., 0., .5, .5), (.5, 0., .5, .5), (0., .5, .5, .5), (.5, .5, .5, .5))
DEVICE = CameraDevice(0, 'OFFLINE fixed recognition source', 'offline-constructed-four-position')


def pump_until(app, predicate, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(.01)
    app.processEvents()
    return predicate()


def build_scene():
    from build_constructed_recognition_fixture import build_fixture
    baseline = LOCAL_RECOGNITION_SAMPLES / 'samples' / 'constructed-tenpai.png'
    if not baseline.is_file():
        build_fixture()
    base = read_image(baseline)
    source_path = LOCAL_RECOGNITION_SAMPLES / 'mleague-hand-hd.jpg'
    source = read_image(source_path)
    provider = OnnxTileRecognizer(MEDIUM_MODEL, confidence=.55, threads=2)
    source_result = provider.recognize(source, roi=(500, 210, 750, 340))
    scene = np.empty((320, 1744, 3), dtype=np.uint8)
    expected = []
    crops = {}
    for seat, tile in enumerate(SINGLES):
        detection = max((d for d in source_result.detections if d.tile == tile), key=lambda d: d.confidence)
        x1, y1, x2, y2 = (round(v) for v in detection.bbox)
        hand = base.copy()
        hand[40:136, 788:852] = cv2.resize(source[y1:y2, x1:x2], (64, 96), interpolation=cv2.INTER_CUBIC)
        left, top = (seat % 2) * 872, (seat // 2) * 160
        scene[top:top + 160, left:left + 872] = hand
        expected.append(BASE_TILES + [tile])
        crops[tile] = detection.bbox
    write_image(OUT / 'constructed-four-positions.png', scene)
    annotated = scene.copy()
    results = []
    for seat, (x, y, width, height) in enumerate(QUADS):
        result = provider.recognize(scene, roi=(round(x * 1744), round(y * 320), round(width * 1744), round(height * 320)))
        assert Counter(result.tiles) == Counter(expected[seat]), (seat, result.tiles, expected[seat])
        analysis = analyse_hand(result.tiles)
        assert analysis['tenpai'] and analysis['waits'] == [SINGLES[seat]], analysis
        annotated = annotate(annotated, result)
        results.append(dict(position=seat + 1, result=result.to_dict(), waits=analysis['waits']))
    write_image(OUT / 'constructed-four-positions-recognized.png', annotated)
    replay = OUT / 'constructed-four-positions.avi'
    writer = cv2.VideoWriter(str(replay), cv2.VideoWriter_fourcc(*'MJPG'), 10, (1744, 320))
    if not writer.isOpened():
        raise RuntimeError('Cannot write the offline constructed replay')
    for _ in range(30):
        writer.write(scene)
    writer.release()
    return replay, expected, dict(kind='constructed photo-crop arrangement, not a real broadcast hand',
        source=str(source_path.relative_to(ROOT)), source_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        singleton_crop_boxes=crops, per_position=results)


class ReplayCapture:
    def __init__(self, video):
        self.reader = cv2.VideoCapture(str(video))
        if not self.reader.isOpened():
            raise RuntimeError('Cannot read the constructed replay')
        self.released = False
        self.release_count = 0
        self.read_count = 0
        self.program_scene = 'program-camera-1'
        self.lock = threading.Lock()

    def isOpened(self):
        return not self.released and self.reader.isOpened()

    def get(self, key):
        return self.reader.get(key)

    def read(self):
        time.sleep(.04)
        if self.released:
            return False, None
        okay, frame = self.reader.read()
        if not okay and not self.released:
            self.reader.set(cv2.CAP_PROP_POS_FRAMES, 0)
            okay, frame = self.reader.read()
        if okay:
            self.read_count += 1
        return okay, frame

    def release(self):
        with self.lock:
            if not self.released:
                self.released = True
                self.release_count += 1
                self.reader.release()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    for filename in ('msyh.ttc', 'msyhbd.ttc'):
        font = Path('C:/Windows/Fonts') / filename
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont('Microsoft YaHei', 10))
    video, expected, construction = build_scene()
    opened = []
    capture = ReplayCapture(video)
    def resolve(source):
        if not isinstance(source, dict) or source.get('identifier') != DEVICE.identifier:
            raise AssertionError('Only the offline replay descriptor may be opened')
        return DEVICE
    def open_capture(device, *, cv2_module=None):
        opened.append(device.identifier)
        return capture, device
    pool = CameraCapturePool(resolver=resolve, opener=open_capture)
    report = dict(purpose='four fixed positions over one shared continuous input',
        scene_kind='explicit constructed test, not real broadcast footage',
        live_obs_tested=False, physical_camera_tested=False, normal_event_data_touched=False,
        model=MEDIUM_MODEL.name, source=str(video.relative_to(ROOT)), construction=construction)
    with patch('shared_camera.DEFAULT_POOL', pool), tempfile.TemporaryDirectory(prefix='four-source-review-', dir=ROOT / 'tests') as directory:
        model = Model(directory)
        config = default_recognition_config()
        config['sources'] = [dict(enabled=True, kind='camera', path='0', identifier=DEVICE.identifier,
            name=DEVICE.name, index=0, backend='dshow', roi=list(roi)) for roi in QUADS]
        model.settings.update(show_overlays=False, topmost=False, console_font_family='Microsoft YaHei', recognition_assist=config)
        manager = OverlayManager(model)
        ui = Console(model, manager)
        ui.resize(1920, 1080)
        ui.show()
        controller = ui.inline_automation
        counts = Counter()
        durations = {seat: [] for seat in range(4)}
        def observed(seat, image, result):
            counts.update([seat])
            durations[seat].append(result.elapsed_ms)
        controller.preview_received.connect(observed)
        before = copy.deepcopy(model.state)
        try:
            assert pump_until(app, lambda: all(seat in controller.observations and model.state['phase'] == 'playing' and ui.wait_drafts.get(seat, {}).get('waits') == [SINGLES[seat]] for seat in range(4))), [hand.status for hand in ui.recognized_hands]
            initial_workers = dict(controller.workers)
            switches = []
            for scene in ('program-camera-4', 'title-card', 'program-camera-2'):
                previous = counts.copy()
                capture.program_scene = scene
                ui.select((ui.selected + 1) % 4)
                model.setting('broadcast_scene', 'clean' if scene == 'title-card' else 'table')
                assert pump_until(app, lambda: all(counts[seat] > previous[seat] for seat in range(4)))
                assert all(controller.workers[seat] is initial_workers[seat] and initial_workers[seat].isRunning() for seat in range(4))
                switches.append(dict(program=scene, updates=dict(counts)))
            assert opened == [DEVICE.identifier], opened
            assert model.state == before and not model.undo
            with VideoSource(config['sources'][0]) as preview:
                okay, frame = preview.read()
                assert okay and frame.shape == (320, 1744, 3)
                assert opened == [DEVICE.identifier]
            assert capture.release_count == 0
            assert all(controller.workers[seat].isRunning() for seat in range(4))
            ui.workspace.ensure_visible(ui.player_widgets[0][0])
            app.processEvents()
            ui.grab().save(str(OUT / 'original-desk-four-continuous-positions.png'))
            report.update(one_capture_open_count=len(opened), four_position_waits={str(seat + 1): ui.wait_drafts[seat]['waits'] for seat in range(4)},
                frame_updates=dict(counts), simulated_program_switches=switches,
                same_workers_after_switches=True, fifth_preview_reader_did_not_reopen_or_stop_capture=True,
                no_match_mutation_before_confirmation=True,
                median_inference_ms={str(seat + 1): statistics.median(durations[seat]) for seat in range(4)})
        finally:
            ui.close()
            app.processEvents()
            closer = getattr(pool, 'close', None) or getattr(pool, 'close_all', None)
            if closer:
                closer()
            capture.release()
    report['capture_release_count'] = capture.release_count
    (OUT / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('one_capture_open_count', 'four_position_waits',
        'same_workers_after_switches', 'fifth_preview_reader_did_not_reopen_or_stop_capture',
        'physical_camera_tested', 'live_obs_tested', 'capture_release_count')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
