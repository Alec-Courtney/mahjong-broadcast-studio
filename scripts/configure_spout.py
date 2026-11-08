"""One-time Spout frame freshness setup, with exact previous-value restoration.

The SDK reads this user setting at construction. Restart OBS and the operator
application after enabling it. Runtime capture never changes global settings.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / '.cache' / 'spout' / 'frame-count-before-install.json'
KEY = r'Software\Leading Edge\Spout'
VALUE = 'Framecount'


def _read(winreg):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            value, kind = winreg.QueryValueEx(key, VALUE)
            binary = isinstance(value, bytes)
            return {'exists': True, 'value': base64.b64encode(value).decode('ascii') if binary else value,
                    'type': kind, 'binary': binary}
    except FileNotFoundError:
        return {'exists': False}


def enable(*, backup=BACKUP):
    if sys.platform != 'win32':
        raise RuntimeError('Spout 输入仅适用于 Windows。')
    import winreg
    previous = _read(winreg)
    if previous.get('type') == winreg.REG_DWORD and previous.get('value') == 1:
        return 'Spout 新帧计数已启用。'
    backup = Path(backup)
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open('x', encoding='utf-8') as target:
            json.dump({'schema': 1, 'key': KEY, 'name': VALUE, 'previous': previous}, target,
                      ensure_ascii=False, indent=2)
            target.flush()
            os.fsync(target.fileno())
    else:
        # Never overwrite the first backup, including an interrupted install.
        saved = json.loads(backup.read_text(encoding='utf-8'))
        if saved.get('key') != KEY or saved.get('name') != VALUE or saved.get('schema') != 1:
            raise RuntimeError('Spout 设置备份不匹配，未修改系统设置。')
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE, 0, winreg.REG_DWORD, 1)
    return '已启用 Spout 新帧计数；请重启 OBS 和导播台，使发送与接收同时生效。'


def restore(*, backup=BACKUP):
    import winreg
    backup = Path(backup)
    if not backup.exists():
        return '没有需要恢复的 Spout 设置备份。'
    saved = json.loads(backup.read_text(encoding='utf-8'))
    if saved.get('key') != KEY or saved.get('name') != VALUE or saved.get('schema') != 1:
        raise RuntimeError('Spout 设置备份不匹配，未修改系统设置。')
    current = _read(winreg)
    previous = saved['previous']
    if current != previous:
        if current.get('type') != winreg.REG_DWORD or current.get('value') != 1:
            raise RuntimeError('Spout 帧计数已由其他操作修改，保留现有值与备份。')
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as key:
            if previous['exists']:
                value = base64.b64decode(previous['value']) if previous.get('binary') else previous['value']
                winreg.SetValueEx(key, VALUE, 0, previous['type'], value)
            else:
                winreg.DeleteValue(key, VALUE)
    backup.unlink()
    return '已恢复安装前的 Spout 新帧计数设置。'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--enable-frame-count', action='store_true')
    group.add_argument('--restore-frame-count', action='store_true')
    parser.add_argument('--verification', action='store_true',
                        help='Use a separate temporary-test backup; preserve an existing installation backup.')
    args = parser.parse_args()
    try:
        backup = BACKUP.with_name('frame-count-before-verification.json') if args.verification else BACKUP
        print(enable(backup=backup) if args.enable_frame_count else restore(backup=backup))
    except (OSError, ValueError, RuntimeError) as error:
        print(f'Spout 设置未完成：{error}', file=sys.stderr)
        raise SystemExit(1)
