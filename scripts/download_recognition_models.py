"""Download pinned public Mahjong-YOLO assets without requiring PyTorch.

Run with the project's Python. The default download installs two CPU ONNX
models. The optional checkpoints are only for separate local conversion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / 'reference' / 'local-only' / 'resources' / 'recognition'
REPOSITORY = 'nikmomo/Mahjong-YOLO'
REVISION = '28ffceed232ad95fd019c47a6c51ae7c78791a0e'
RUNTIME_ASSETS = (
    ('models/nano/mahjong-yolon-best.onnx', 'mahjong-yolon-best.onnx', 10611888),
    ('models/small/mahjong-yolos-best.onnx', 'mahjong-yolos-best.onnx', 37963138),
    ('LICENSE', 'LICENSE-Mahjong-YOLO.txt', 1067),
)
CHECKPOINT_ASSETS = (
    ('models/nano/mahjong-yolon-best.pt', 'mahjong-yolon-best.pt', 5475354),
    ('trained_models_v2/yolo11n_best.pt', 'yolo11n_v2_best.pt', 5489818),
    ('trained_models_v2/yolo11s_best.pt', 'yolo11s_v2_best.pt', 19210010),
    ('trained_models_v2/yolo11m_best.pt', 'yolo11m_v2_best.pt', 40573548),
)
SAMPLE_ASSETS = (
    ('inference_validation/validation_01_0464a319-000018.png', 'upstream-validation-01.png', 3744061),
    ('inference_validation/validation_02_6c1a4a28-000008.png', 'upstream-validation-02.png', 4165691),
)
EXPECTED_SHA256 = {
    'mahjong-yolon-best.onnx': '3c7732c022d41c1a3f48cea931ce626416d92486ab5b86692312941d0cc22226',
    'mahjong-yolos-best.onnx': 'ac3747c42dfe41682fc0436909574032fdd90717bcd14d42b2d41181b12124eb',
    'mahjong-yolon-best.pt': '5cb589075e18edbb0a40037b48fd88715e319d0f34e304e4dfc588da5d15f42e',
    'yolo11n_v2_best.pt': 'ea35d50b9568fc67277538038e7c456b3f29ebd65e1de7bd141e5a1d3a9d0ff1',
    'yolo11s_v2_best.pt': '1b755721c3b581fe550d9568c85a6c5cf12558c6232453943b73dc669eafd6fd',
    'yolo11m_v2_best.pt': 'a00a03dd9229f5809d4de3c9474b9bcdd74b7b42a5a507e92a803755e268d372',
    'LICENSE-Mahjong-YOLO.txt': '0902daf1b9bf23e87c1d2cdfe6b5a76c8a717477f25af6a8af34bd2d087fab29',
    'upstream-validation-01.png': '5251fefb67c3f2089ab1090af1586997a9de0320f26a30b56460acfe4e2e020f',
    'upstream-validation-02.png': '21d89e38972c6986b3242d5cdec92a61cb9a991899fc6cf34308847df68d75b7',
    'mleague-hand.jpg': 'f75eb2213b475e1388ed3dbd7fb93ff4bd1de53966f0651a83b23ad490a36355',
    'mleague-hand-hd.jpg': '88b364a3c0ca337089bc1dddb7538a3efae2c35b5441504820aa36d645548db4',
    'mleague-chinitsu.jpg': '2d41db2416d314c631a4b0dbb1c005041dc2c156bead5b94b2276c50eda2a698',
    'mleague-chiitoi.jpg': '85b89e5e3c3ce1d0dc02a6d799405626b1ad48dbbb98d189cbe95465f40baffe',
}
ML_SAMPLES = (
    ('mleague-hand.jpg', 'https://times-abema.ismcdn.jp/mwimgs/a/7/724w/img_a7a7f2f0ea65c44944bc20379631615a399394.jpg', 71441,
     'https://times.abema.tv/articles/photo/10116191'),
    ('mleague-hand-hd.jpg', 'https://times-abema.ismcdn.jp/mwimgs/a/7/1448w/img_a7a7f2f0ea65c44944bc20379631615a399394.jpg', 198039,
     'https://times.abema.tv/articles/photo/10116191'),
    ('mleague-chinitsu.jpg', 'https://times-abema.ismcdn.jp/mwimgs/7/e/1200w/img_7e859be7c3c2c2d3943ee316e46fd619424525.jpg', 133543,
     'https://times.abema.tv/articles/-/10205552'),
    ('mleague-chiitoi.jpg', 'https://times-abema.ismcdn.jp/mwimgs/c/7/1448w/img_c7ab4b0405cf02096a2a8d79b947aa4d289250.jpg', 201257,
     'https://times.abema.tv/articles/-/10049229'),
)


def download(url: str, destination: Path, expected_size: int | None = None) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or (expected_size and destination.stat().st_size != expected_size):
        temporary = destination.with_suffix(destination.suffix + '.partial')
        request = urllib.request.Request(url, headers={'User-Agent': 'MahjongOverlayStudio/0.4'})
        with urllib.request.urlopen(request, timeout=90) as response, temporary.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if expected_size and temporary.stat().st_size != expected_size:
            raise RuntimeError(f'Download size differs for {destination.name}')
        temporary.replace(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    expected = EXPECTED_SHA256.get(destination.name)
    if expected and digest != expected:
        raise RuntimeError(f'File differs from verified upstream SHA256: {destination.name}')
    return {'file': destination.name, 'url': url, 'bytes': destination.stat().st_size,
            'sha256': digest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoints', action='store_true', help='Also download PT checkpoints for local ONNX conversion')
    parser.add_argument('--samples', action='store_true', help='Also download evaluation images to the local reference folder')
    args = parser.parse_args()
    assets = list(RUNTIME_ASSETS)
    if args.checkpoints:
        assets.extend(CHECKPOINT_ASSETS)
    if args.samples:
        assets.extend(SAMPLE_ASSETS)
    entries = []
    for source, target, size in assets:
        url = f'https://raw.githubusercontent.com/{REPOSITORY}/{REVISION}/{source}'
        entry = download(url, DESTINATION / target, size)
        entries.append(entry)
        print(f"{entry['file']} {entry['bytes']} bytes SHA256 {entry['sha256']}")
    if args.samples:
        samples = []
        for target, url, size, article in ML_SAMPLES:
            entry = download(url, DESTINATION / target, size)
            entry.update(article=article, attribution='ABEMA TIMES / M.LEAGUE. Public article still, local evaluation only; not a model training dataset.')
            samples.append(entry)
            print(f"{entry['file']} {entry['bytes']} bytes SHA256 {entry['sha256']}")
        sample_manifest = {'purpose': 'User-requested offline recognition evaluation using real M.LEAGUE camera stills',
                           'samples': samples,
                           'upstream_composites': 'upstream-validation-01.png and upstream-validation-02.png are upstream annotated evaluation figures, not clean independent ground truth. See manifest.json.'}
        (DESTINATION / 'sample-manifest.json').write_text(json.dumps(sample_manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    manifest = {'repository': f'https://github.com/{REPOSITORY}', 'revision': REVISION,
                'license': 'Repository LICENSE: MIT; ONNX model metadata: Ultralytics AGPL-3.0. See THIRD_PARTY.md.', 'files': entries,
                'runtime': 'ONNX Runtime CPU. PT checkpoint archived, never loaded by application.'}
    (DESTINATION / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
