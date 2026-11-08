"""Actual ONNX replay -> independent calculator -> HTTP draft -> manual host confirmation.

The scene is explicitly constructed from real-photo crops, not a broadcast hand.
All game operations use a disposable event directory, never the normal data/.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from urllib.request import Request, build_opener, ProxyHandler

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtWidgets import QApplication
from app_paths import LOCAL_RECOGNITION_SAMPLES
from automation_bridge import StableHandGate
from console import Console
from hand_calculation import analyse_hand, calculate_win
from model import Model
from overlays import OverlayManager
from tile_recognition import DEFAULT_MODEL, OnnxTileRecognizer, VideoSource

OUT = ROOT / 'previews' / 'checks' / 'automation'
VIDEO = LOCAL_RECOGNITION_SAMPLES / 'samples' / 'constructed-tenpai-replay.avi'


def main():
    if not VIDEO.is_file():
        raise FileNotFoundError('先运行 tests/build_constructed_recognition_fixture.py 生成本机构造回放')
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix='automation-pipeline-', dir=ROOT / 'tests') as directory:
        model = Model(directory)
        model.settings['show_overlays'] = False
        manager = OverlayManager(model)
        console = Console(model, manager)
        service = console.automation
        try:
            before = copy.deepcopy(model.state)
            history_before = len(model.undo)
            base = service.server.start()
            headers = {'Authorization': 'Bearer ' + service.server.token, 'Content-Type': 'application/json'}
            opener = build_opener(ProxyHandler({}))

            def request(path, payload=None):
                body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
                with opener.open(Request(base + path, data=body, headers=headers), timeout=5) as response:
                    return json.load(response)

            context = request('/v1/context')
            recognizer = OnnxTileRecognizer(DEFAULT_MODEL, confidence=.55)
            gate = StableHandGate(stable_frames=3, minimum_confidence=.55)
            observations = []
            stable = None
            with VideoSource(VIDEO) as source:
                for _ in range(6):
                    okay, frame = source.read()
                    if not okay:
                        break
                    result = recognizer.recognize(frame)
                    observations.append(result.to_dict())
                    stable = gate.observe(result.detections, 13)
                    app.processEvents()
                    if stable:
                        break
            assert stable, '构造回放未通过连续 3 帧稳定条件'
            analysis = analyse_hand(stable, state=context['state'], player=0)
            assert analysis['tenpai'] and analysis['waits'] == ['5s'], analysis
            draft = request('/v1/drafts', dict(schema_version=1, context_id=context['context_id'],
                source='independent-replay-adapter', request_id='constructed-hand-001',
                player=0, kind='waits', data=dict(waits=analysis['waits'],
                no_yaku=analysis['no_yaku'], tiles=stable, remaining=None, furiten=False)))
            app.processEvents()
            assert model.state == before and len(model.undo) == history_before, '收到外部草稿时改变了对局'
            assert draft['status'] == 'pending'
            service.confirm_waits(draft['id'], riichi=False, no_yaku=[], furiten=False, remaining=None)
            assert model.state['players'][0]['waits'] == ['5s']
            assert model.state['players'][0]['tenpai'] and not model.state['players'][0]['riichi']
            assert model.state['phase'] == 'playing'
            after_waits = copy.deepcopy(model.state)
            calculation = calculate_win(stable, '5s', state=service.context()['state'], player=0)
            assert calculation['valid'], calculation
            win = request('/v1/drafts', dict(schema_version=1, context_id=service.context()['context_id'],
                source='another-calculation-adapter', request_id='constructed-win-001', player=0, kind='win',
                data=dict(type='ron', loser=1, han=calculation['han'], fu=calculation['fu'] or 30,
                    yakuman=calculation['yakuman'], yaku=calculation['yaku'], bonuses=calculation['bonuses'])))
            console.inline_automation.receive_draft(win['id'])
            assert model.state == after_waits, '载入役种时发生了结算'
            event, names = console.win_event()
            assert event['winner'] == 0 and event['loser'] == 1 and '四暗刻' in names
            preview = model.preview(event)
            # Explicit operator-confirmation equivalent, entirely in scratch data.
            console.settle()
            assert model.state['phase'] == 'result' and model.state.get('lastResult')
            assert model.state['players'][0]['score'] > before['players'][0]['score']
            report = dict(
                purpose='actual inference and replaceable HTTP producer integration',
                scene_kind='constructed test arrangement from real ML photo crops; not an actual broadcast hand',
                source=str(VIDEO.relative_to(ROOT)), model=DEFAULT_MODEL.name,
                physical_camera_tested=False, normal_event_data_touched=False,
                stable_frame_count=len(observations), detected_tiles=stable,
                median_inference_ms=statistics.median(frame['elapsed_ms'] for frame in observations),
                minimum_confidence=min(d['confidence'] for frame in observations for d in frame['detections']),
                analysis={'tenpai': analysis['tenpai'], 'waits': analysis['waits'], 'no_yaku': analysis['no_yaku']},
                external_draft_status=draft['status'], no_mutation_before_confirmation=True,
                wait_confirmation_atomic=True, win_load_without_settlement=True,
                win_event=event, yaku=names, settlement_preview=preview,
                operator_settlement_phase=model.state['phase'], observations=observations)
            OUT.mkdir(parents=True, exist_ok=True)
            target = OUT / 'pipeline-verification.json'
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({k: report[k] for k in ('stable_frame_count', 'median_inference_ms',
                'minimum_confidence', 'analysis', 'yaku', 'physical_camera_tested', 'normal_event_data_touched')},
                ensure_ascii=False, indent=2))
            print(str(target))
        finally:
            console.close()
            app.processEvents()


if __name__ == '__main__':
    main()
