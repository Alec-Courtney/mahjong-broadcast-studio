"""Run real ONNX inference on public M.LEAGUE stills and a local replay.

This is an explicit evaluation, not a unit test and not a camera certification.
The photographic sample manifest records sources and ownership. Report exact
manual label accuracy for the 13-tile upright hand; other angles are stress
cases. A replay made from still images exercises the local-video input only.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from app_paths import LOCAL_RECOGNITION_SAMPLES
from tile_recognition import OnnxTileRecognizer, MODEL_DIRECTORY, VideoSource, annotate, read_image, recognition_model_path, write_image
import cv2
import numpy as np

OUT = ROOT / 'previews' / 'checks' / 'vision'
CASES = [
    dict(file='mleague-hand.jpg', roi=(250, 105, 375, 170), expected_count=13,
         expected_tiles=['2s', '5s', '6s', '4z', '6z', '7z', '3p', '3p', '5p', '1m', '1m', '3m', '9m'],
         note='13 upright concealed tiles. Manual labels read from the original ABEMA still, left to right.'),
    dict(file='mleague-chinitsu.jpg', roi=(385, 235, 625, 260), expected_count=12,
         expected_tiles=['1p', '1p', '2p', '3p', '4p', '0p', '7p', '8p', '9p', '6m', '7s', '2s'],
         note='12 visible upright faces; the drawn tile is held under the hand and excluded. Not a complete 13-tile recognition input.'),
    dict(file='mleague-chiitoi.jpg', roi=(455, 245, 795, 340), expected_count=14,
         expected_tiles=None,
         note='Laid-down winning hand, oblique angle and perspective. Count-only stress case; no claimed class ground truth.'),
]


def metrics(result, case, gate_confidence=.65):
    accepted = [d for d in result.detections if d.tile and d.confidence >= gate_confidence]
    values = dict(detections=[d.to_dict() for d in result.detections], tiles=result.tiles,
                  detected_count=len(result.tiles), expected_count=case['expected_count'],
                  min_confidence=min((d.confidence for d in result.detections), default=0),
                  elapsed_ms=result.elapsed_ms, unknown_count=result.unknown_count,
                  high_confidence_count=len(accepted), gate_confidence=gate_confidence,
                  complete_at_gate_confidence=len(accepted) == case['expected_count'] and not result.unknown_count)
    if case['expected_tiles'] is not None:
        predicted, expected = Counter(result.tiles), Counter(case['expected_tiles'])
        correct = sum((predicted & expected).values())
        values.update(correct_tile_count=correct, missing_tiles=list((expected - predicted).elements()),
                      extra_or_wrong_tiles=list((predicted - expected).elements()),
                      exact_hand=result.tiles == case['expected_tiles'],
                      class_precision=correct / max(1, len(result.tiles)),
                      class_recall=correct / len(case['expected_tiles']))
    return values


def replay_path():
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / 'mleague-still-replay.avi'
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*'MJPG'), 5, (1280, 720))
    if not writer.isOpened():
        raise RuntimeError('Unable to create local replay')
    for case in CASES:
        frame = read_image(LOCAL_RECOGNITION_SAMPLES / case['file'])
        resized = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_AREA)
        # This caption is outside the hand ROI. The replay is explicitly synthetic.
        cv2.putText(resized, 'STILL-IMAGE REPLAY / OFFLINE INPUT TEST', (12, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 220, 255), 2, cv2.LINE_AA)
        for _ in range(15):
            writer.write(resized)
    writer.release()
    # A single camera angle is convenient for verifying ROI and stable drafts.
    single = OUT / 'mleague-hand-replay.avi'
    writer = cv2.VideoWriter(str(single), cv2.VideoWriter_fourcc(*'MJPG'), 5, (1280, 720))
    if not writer.isOpened():
        raise RuntimeError('Unable to create single-scene replay')
    upright = cv2.resize(read_image(LOCAL_RECOGNITION_SAMPLES / CASES[0]['file']), (1280, 720), interpolation=cv2.INTER_AREA)
    cv2.putText(upright, 'STILL-IMAGE REPLAY / OFFLINE INPUT TEST', (12, 22),
                cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 220, 255), 2, cv2.LINE_AA)
    for _ in range(30):
        writer.write(upright)
    writer.release()
    return target


def main():
    if not all((LOCAL_RECOGNITION_SAMPLES / case['file']).is_file() for case in CASES):
        raise FileNotFoundError('缺少本机测试图片，请先运行 scripts/download_recognition_models.py --samples')
    OUT.mkdir(parents=True, exist_ok=True)
    models = [MODEL_DIRECTORY / name for name in ['mahjong-yolon-best.onnx', 'mahjong-yolos-best.onnx',
                                                   'yolo11n_v2_best.onnx', 'yolo11s_v2_best.onnx', 'yolo11m_v2_best.onnx']]
    results = []
    for model in models:
        if not model.is_file():
            continue
        for confidence in (.3, .55):
            provider = OnnxTileRecognizer(model, confidence=confidence, threads=2)
            provider.recognize(np.zeros((640, 640, 3), dtype=np.uint8))  # warmup, not timed in summary
            for case in CASES:
                frame = read_image(LOCAL_RECOGNITION_SAMPLES / case['file'])
                result = provider.recognize(frame, roi=case['roi'])
                item = dict(model=model.name, confidence=confidence, case=case, **metrics(result, case, .55))
                results.append(item)
                target = OUT / f"{Path(case['file']).stem}-{model.stem}-conf{round(confidence*100):02d}.png"
                write_image(target, annotate(frame, result))
                print(json.dumps({k: item[k] for k in ['model', 'confidence', 'detected_count', 'expected_count', 'min_confidence', 'elapsed_ms']}
                                 | {'sample': case['file'], 'tiles': result.tiles}, ensure_ascii=False), flush=True)
    video = replay_path()
    best = recognition_model_path()
    provider = OnnxTileRecognizer(best, confidence=.55, threads=2)
    video_results, decoded = [], 0
    with VideoSource(video) as source:
        while True:
            success, frame = source.read()
            if not success:
                break
            if decoded % 5 == 0:
                case = CASES[min(decoded // 15, len(CASES) - 1)]
                original = read_image(LOCAL_RECOGNITION_SAMPLES / case['file'])
                x, y, w, h = case['roi']
                sx, sy = 1280 / original.shape[1], 720 / original.shape[0]
                roi = (round(x * sx), round(y * sy), round(w * sx), round(h * sy))
                result = provider.recognize(frame, roi=roi)
                video_results.append({'frame': decoded, 'source_case': case['file'], **metrics(result, case, .55)})
            decoded += 1
    manifest = LOCAL_RECOGNITION_SAMPLES / 'sample-manifest.json'
    if not manifest.is_file():
        manifest = ROOT / 'resources' / 'recognition' / 'sample-manifest.json'
    report = {'scope': 'Offline CPU inference on three real M.LEAGUE article stills, plus video replay made from those stills.',
              'limitations': ['No physical camera was connected.', 'Three stills are a tiny evaluation sample, not an accuracy benchmark.',
                              'The upstream old and v2 model classes differ; class names are read from each ONNX metadata.',
                              'Oblique faces, red fives, occlusion and exposure changes can misclassify or omit tiles.',
                              'A stable frame with the correct count is not proof that every tile class is correct. Operator review remains required.',
                              'The replay is synthetic motion-free source footage, not a captured M.LEAGUE video.'],
              'sources': json.loads(manifest.read_text(encoding='utf-8')),
              'provider': {'default_model': best.name, 'confidence': .55, 'diagnostic_confidence': .3,
                           'iou': .45, 'threads': 2, 'runtime': 'onnxruntime CPU',
                           'opencv': cv2.__version__, 'numpy': np.__version__},
              'images': results,
              'video': {'file': str(video.relative_to(ROOT)), 'model': best.name, 'decoded_frames': decoded,
                        'sampled_frames': len(video_results), 'sample_results': video_results,
                        'median_inference_ms': statistics.median(x['elapsed_ms'] for x in video_results)}}
    from build_constructed_recognition_fixture import build_fixture
    constructed = build_fixture(OUT)
    report['constructed_integration_fixture'] = {
        key: constructed[key] for key in ['kind', 'expected_tiles', 'expected_waits', 'exact_image_hand', 'exact_video_hand', 'calculated_waits', 'calculated_tenpai', 'image', 'video']}
    (OUT / 'recognition-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print('Report:', OUT / 'recognition-report.json')
    print('Replay decoded:', decoded, 'sampled:', len(video_results))


if __name__ == '__main__':
    main()
