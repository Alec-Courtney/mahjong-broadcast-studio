"""Fetch pinned official installers; verify release SHA-256 before use."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reference' / 'local-only' / 'resources' / 'obs-spout'
RELEASES = {
    '1.13.0': ('OBS 32 系列', '8fbdc684cefab2f12fec2e2817b4f1082d9e42b0d4cf16f14bd9418f988d0473'),
    '1.15.0': ('OBS 33 及以后', 'b49480a6c1f0c41eb1881ba21a4356961b4ee8b75394a0793c6005285b2ee8c6'),
}
PORTABLE_VERSION = '1.13.0'
PORTABLE_FILE = 'win-spout-1.13.0-windows-x64-portable.zip'
PORTABLE_SHA256 = '64bff539c710ceb005fe9766c44dc251f0513c1378f64b7fba9fee20f6beeefb'


def download(version, name, expected):
    url = f'https://github.com/Off-World-Live/obs-spout2-plugin/releases/download/{version}/{name}'
    path = OUT / name
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        with urlopen(Request(url, headers={'User-Agent': 'MahjongOverlayStudio'}), timeout=60) as response:
            data = response.read(4 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError(f'{name} 与官方校验值不符，未保存')
        partial = path.with_suffix('.download')
        partial.write_bytes(data)
        partial.replace(path)
    return url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=RELEASES, help='默认准备两种版本，不执行安装')
    parser.add_argument('--portable', action='store_true', help='同时下载本地 OBS 联调所需的 1.13.0 ZIP')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for version in ([args.version] if args.version else RELEASES):
        compatibility, expected = RELEASES[version]
        name = f'OBS_Spout2_Plugin_Install_v{version}.exe'
        url = download(version, name, expected)
        rows.append(dict(file=name, version=version, compatibility=compatibility, sha256=expected,
                         source=url, release=f'https://github.com/Off-World-Live/obs-spout2-plugin/releases/tag/{version}'))
        print(f'{compatibility}: {name}')
    if args.portable:
        download(PORTABLE_VERSION, PORTABLE_FILE, PORTABLE_SHA256)
        print(f'本地联调: {PORTABLE_FILE}')
    (OUT / 'installers.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
