"""Construct a labeled tenpai fixture from real-photo crops, never claim it real.

This fixture exists to exercise detection -> stable hand -> calculation -> draft.
Its repeated tiles are deliberately assembled and are excluded from real-camera
accuracy statistics. The source image's public article is in sample-manifest.json.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from app_paths import LOCAL_RECOGNITION_SAMPLES
from tile_recognition import (MEDIUM_MODEL, OnnxTileRecognizer,
                              VideoSource, annotate, read_image, write_image)
import cv2
import numpy as np

EXPECTED_TILES = ['9m'] * 3 + ['3p'] * 3 + ['6z'] * 3 + ['7z'] * 3 + ['5s']
EXPECTED_WAITS = ['5s']
SOURCE_SHA256 = '88b364a3c0ca337089bc1dddb7538a3efae2c35b5441504820aa36d645548db4'


def build_fixture(destination=None):
    destination = Path(destination or LOCAL_RECOGNITION_SAMPLES / 'samples')
    destination.mkdir(parents=True, exist_ok=True)
    source_path = LOCAL_RECOGNITION_SAMPLES / 'mleague-hand-hd.jpg'
    if not source_path.is_file():
        raise FileNotFoundError('缺少本机测试图片，请先运行 scripts/download_recognition_models.py --samples')
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if source_hash != SOURCE_SHA256:
        raise RuntimeError('构造测试所用实拍来源已改变，请重新核对标注')
    provider = OnnxTileRecognizer(MEDIUM_MODEL, confidence=.55, threads=2)
    original = read_image(source_path)
    detected = provider.recognize(original, roi=(500, 210, 750, 340))
    best = {tile: max((item for item in detected.detections if item.tile == tile),
                      key=lambda item: item.confidence) for tile in set(EXPECTED_TILES)}
    frame = np.full((160, 872, 3), (72, 115, 45), dtype=np.uint8)
    for index, tile in enumerate(EXPECTED_TILES):
        x1, y1, x2, y2 = [round(value) for value in best[tile].bbox]
        crop = cv2.resize(original[y1:y2, x1:x2], (64, 96), interpolation=cv2.INTER_CUBIC)
        frame[40:136, 20 + index * 64:84 + index * 64] = crop
    image_path = destination / 'constructed-tenpai.png'
    video_path = destination / 'constructed-tenpai-replay.avi'
    write_image(image_path, frame)
    result = provider.recognize(frame)
    write_image(destination / 'constructed-tenpai-recognized.png', annotate(frame, result))
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*'MJPG'), 5, (872, 160))
    if not writer.isOpened():
        raise RuntimeError('无法生成构造测试回放')
    for _ in range(30):
        writer.write(frame)
    writer.release()
    with VideoSource(video_path) as source:
        success, decoded = source.read()
        if not success:
            raise RuntimeError('无法重新读取构造测试回放')
        video_result = provider.recognize(decoded)
    from hand_calculation import analyse_hand, calculate_win
    analysis = analyse_hand(result.tiles)
    winning = calculate_win(result.tiles, '5s')
    report = {'kind': 'synthetic test arrangement from real-photo tile crops',
              'source': str(source_path.relative_to(ROOT)), 'source_sha256': source_hash,
              'source_attribution': 'ABEMA TIMES / M.LEAGUE',
              'expected_tiles': EXPECTED_TILES, 'expected_waits': EXPECTED_WAITS,
              'source_bboxes': {tile: best[tile].bbox for tile in best},
              'model': str(MEDIUM_MODEL.relative_to(ROOT)), 'confidence': .55,
              'image': str(image_path.relative_to(ROOT)), 'video': str(video_path.relative_to(ROOT)),
              'image_result': result.to_dict(), 'video_first_frame_result': video_result.to_dict(),
              'exact_image_hand': result.tiles == EXPECTED_TILES,
              'exact_video_hand': video_result.tiles == EXPECTED_TILES,
              'calculated_waits': analysis['waits'], 'calculated_tenpai': analysis['tenpai'],
              'calculated_win': winning,
              'note': 'Repeated tile crops are a constructed integration test, not an actual M.LEAGUE hand or accuracy benchmark.'}
    (destination / 'constructed-tenpai-source.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if result.tiles != EXPECTED_TILES or video_result.tiles != EXPECTED_TILES:
        raise AssertionError('构造测试图识别与标注不一致，请查看报告')
    if not analysis['tenpai'] or analysis['waits'] != EXPECTED_WAITS or not winning['valid']:
        raise AssertionError('真实推理输出未通过听牌或和牌计算')
    return report


if __name__ == '__main__':
    report = build_fixture()
    print(json.dumps({key: report[key] for key in ['expected_tiles', 'expected_waits', 'exact_image_hand', 'exact_video_hand']}, indent=2))
