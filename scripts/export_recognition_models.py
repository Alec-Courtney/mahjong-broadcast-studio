"""Export the pinned v2 checkpoints once; use ONNX for all live inference.

Install scripts/requirements-recognition-export.txt in a separate environment.
Only the SHA256-verified checkpoints downloaded by the companion downloader
are accepted. Their archive format is never loaded by the broadcast application.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'reference' / 'local-only' / 'resources' / 'recognition'
CHECKPOINTS = {
    'yolo11n_v2_best.pt': 'ea35d50b9568fc67277538038e7c456b3f29ebd65e1de7bd141e5a1d3a9d0ff1',
    'yolo11s_v2_best.pt': '1b755721c3b581fe550d9568c85a6c5cf12558c6232453943b73dc669eafd6fd',
    'yolo11m_v2_best.pt': 'a00a03dd9229f5809d4de3c9474b9bcdd74b7b42a5a507e92a803755e268d372',
}


def main():
    (DIRECTORY / 'export-settings').mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('YOLO_CONFIG_DIR', str(DIRECTORY / 'export-settings'))
    for name, expected in CHECKPOINTS.items():
        target = DIRECTORY / name
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Checkpoint is absent or differs from pinned upstream file: {name}. '
                               'Run scripts/download_recognition_models.py --checkpoints.')
    from ultralytics import YOLO
    import ultralytics
    import torch
    import onnx
    entries = []
    for name, expected in CHECKPOINTS.items():
        model = YOLO(str(DIRECTORY / name))
        output = Path(model.export(format='onnx', imgsz=640, batch=1, device='cpu',
                                   simplify=False, dynamic=False, half=False, opset=17, nms=False))
        metadata = {item.key: item.value for item in onnx.load(str(output)).metadata_props}
        entries.append({'checkpoint': name, 'checkpoint_sha256': expected,
                        'file': output.name, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                        'bytes': output.stat().st_size, 'metadata': metadata})
        print(output.name, entries[-1]['sha256'], flush=True)
    report = {'source_repository': 'https://github.com/nikmomo/Mahjong-YOLO',
              'source_revision': '28ffceed232ad95fd019c47a6c51ae7c78791a0e',
              'build_tools': {'ultralytics': ultralytics.__version__, 'torch': torch.__version__,
                              'onnx': onnx.__version__},
              'arguments': {'imgsz': 640, 'batch': 1, 'device': 'cpu', 'simplify': False,
                             'dynamic': False, 'half': False, 'opset': 17, 'nms': False},
              'files': entries}
    (DIRECTORY / 'export-manifest.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
