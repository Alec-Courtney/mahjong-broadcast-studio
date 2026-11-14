"""Versioned session persistence, independent of windows and the event library.

The model supplies state migration and engine validation. Ordinary saves and
portable exports share an atomic writer and retain the v6 file format.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from app_settings import validate_console_theme, normalize_player_style
import tournament


MAX_SESSION_BYTES = 50_000_000
MAX_LOCAL_SESSION_BYTES = 256 * 1024 * 1024


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _archive_bytes(record):
    """A sealed result can reuse its encoded bytes across scores and undo."""
    from state_snapshots import FrozenDict
    if isinstance(record, FrozenDict):
        cached = getattr(record, '_session_json_bytes', None)
        if cached is None:
            cached = _json_bytes(record)
            record._session_json_bytes = cached
        return cached
    return _json_bytes(record)


def encode_session(state, settings, last_info, event=None):
    """Keep a complete JSON snapshot without re-encoding stable archives."""
    if not isinstance(state.get('broadcast'), dict):
        result = _json_bytes(SessionRepository.payload(state, settings, last_info, event))
        if len(result) > MAX_LOCAL_SESSION_BYTES:raise ValueError('本地赛事数据超过256MiB，未覆盖已保存的工作区')
        return result
    table = dict(state)
    broadcast = dict(table.pop('broadcast'))
    matches = broadcast.pop('matches', [])
    state_bytes = (_json_bytes(table)[:-1] + (b',' if table else b'') + b'"broadcast":'
                   + _json_bytes(broadcast)[:-1] + (b',' if broadcast else b'')
                   + b'"matches":[' + b','.join(_archive_bytes(record) for record in matches) + b']}}')
    parts = [b'{"version":6,"state":', state_bytes, b',"settings":', _json_bytes(settings),
             b',"last_info":', _json_bytes(last_info)]
    if event is not None:parts.extend((b',"event":', _json_bytes(event)))
    parts.append(b'}')
    result = b''.join(parts)
    if len(result) > MAX_LOCAL_SESSION_BYTES:raise ValueError('本地赛事数据超过256MiB，未覆盖已保存的工作区')
    return result


def atomic_json(path, data, *, max_bytes=None):
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    if max_bytes is not None and len(encoded) > max_bytes:
        raise ValueError('赛事数据超过50MB，请减少图片或音乐大小后再导出')
    atomic_bytes(path, encoded)


def atomic_bytes(path, encoded):
    """Atomic replacement also supports exact restoration after a paired write."""
    from file_transaction import stage_bytes
    if stage_bytes(path, encoded):return
    _atomic_bytes_now(path, encoded)


def _atomic_bytes_now(path, encoded):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        # Keep the sibling path short when the destination is a hashed media name.
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.snapshot-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows can briefly deny replacement while another process inspects the
        # destination. Retry only that error; preserve the original on final failure.
        for delay in (0.01, 0.025, 0.05, 0.1, None):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if delay is None:
                    raise
                time.sleep(delay)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def validate_settings(settings):
    """Validate imported preferences before any current session is replaced."""
    if not isinstance(settings, dict):
        raise ValueError('显示设置格式无效')
    from typography import validate
    validate(settings)
    from desk_tiles import validate_tile_sizes
    validate_tile_sizes(settings)
    from card_style import normalize_card_style, validate_card_style
    for key, theme in (('player_card_style', 'mleague'), ('saikouisen_card_style', 'saikouisen')):
        if key in settings:
            validate_card_style(settings[key])
            settings[key] = normalize_card_style(settings[key], theme)
    if not isinstance(settings.get('player_group_linked', False), bool):
        raise ValueError('四家联动设置无效')
    settings.setdefault('final_points_mode', 'scores')
    settings.setdefault('hud_group_version', 0)
    if settings['final_points_mode'] not in ('scores', 'pt'):
        raise ValueError('终局显示模式无效')
    for key in ('locked', 'topmost', 'show_yaku', 'show_overlays', 'split_fullscreen_windows',
                'final_cards_visible', 'console_dock_animations'):
        if key in settings and not isinstance(settings[key], bool):
            raise ValueError('显示开关格式无效')
    if 'speed' in settings:
        tournament._finite(settings['speed'], '动画速度', .1, 10)
    if 'player_side_padding' in settings:
        tournament._finite(settings['player_side_padding'], '选手 UI 两侧留白', 0, 15)
    validate_console_theme(settings.get('console_theme', 'dark'))
    normalize_player_style(settings, strict=True)
    if settings.get('riichi_payment_mode', 'immediate') not in ('immediate', 'deferred'):
        raise ValueError('立直棒结算模式无效')
    if 'show_melds' in settings and not isinstance(settings['show_melds'], bool):
        raise ValueError('副露显示设置无效')
    if 'final_scores_hold' in settings:
        tournament._finite(settings['final_scores_hold'], '终局点数停留', 1, 60)
    if settings.get('capture_bg', 'transparent') not in ('transparent', 'green', 'blue'):
        raise ValueError('捕获背景格式无效')
    windows = settings.get('windows', {})
    if not isinstance(windows, dict):
        raise ValueError('窗口配置格式无效')
    for key, geometry in windows.items():
        if not isinstance(key, str) or not isinstance(geometry, list) or len(geometry) != 4:
            raise ValueError('窗口位置格式无效')
        for value in geometry:
            tournament._integer(value, '窗口位置', -100000, 100000)
        if geometry[2] <= 0 or geometry[3] <= 0:
            raise ValueError('窗口尺寸必须大于零')
    visible = settings.get('visible', {})
    if not isinstance(visible, dict) or any(
            not isinstance(key, str) or not isinstance(value, bool)
            for key, value in visible.items()):
        raise ValueError('窗口显示设置格式无效')


def reject_constant(value):
    raise ValueError('数据不能包含NaN或无穷值')


class SessionRepository:
    def __init__(self, engine, migrate):
        self.engine = engine
        self.migrate = migrate
        self._revisions = {}

    def observe(self, filename, encoded=None):
        path = Path(filename).resolve()
        if encoded is None:
            try:encoded = path.read_bytes()
            except FileNotFoundError:encoded = None
        revision = hashlib.sha256(encoded).digest() if encoded is not None else None
        self._revisions[path] = revision
        return revision

    def _locked_write(self, path, encoded):
        from PySide6.QtCore import QLockFile
        path = Path(path).resolve()
        library = path.parent.parent
        if (library.name == 'events' and (library.parent/'library.json').is_file()
                and not path.is_file()):
            raise ValueError('赛事文件已删除或移动，请重新打开赛事库中的工作区')
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(path)+'.write.lock')
        if not lock.tryLock(0):
            raise OSError('当前工作区正在被另一个窗口保存，请稍后重试')
        try:
            try:current = path.read_bytes()
            except FileNotFoundError:current = None
            revision = hashlib.sha256(current).digest() if current is not None else None
            if revision != self._revisions.get(path):
                if current is not None and encoded is not None:
                    try:
                        actual = json.loads(current, parse_constant=reject_constant)
                        desired = json.loads(encoded, parse_constant=reject_constant)
                        # Reading an older valid snapshot fills harmless settings
                        # defaults. They do not constitute another table edit.
                        for payload in (actual, desired):
                            if isinstance(payload, dict) and isinstance(payload.get('settings'), dict):
                                validate_settings(payload['settings'])
                        same = actual == desired
                    except (ValueError, TypeError, UnicodeError):same = False
                    if same:
                        self._revisions[path] = revision
                        return
                raise ValueError('当前工作区已在另一个窗口或程序中更新，请从“工作区”菜单选择“重新载入当前工作区”后再操作；未覆盖已有数据')
            if encoded is None:path.unlink(missing_ok=True)
            else:atomic_bytes(path, encoded)
            self._revisions[path] = hashlib.sha256(encoded).digest() if encoded is not None else None
        finally:
            lock.unlock()

    def restore(self, directory, encoded):
        """Roll back a paired write only while our saved revision is still current."""
        self._locked_write(Path(directory)/'session.json', encoded)

    @staticmethod
    def payload(state, settings, last_info, event=None):
        data = {'version': 6, 'state': state, 'settings': settings, 'last_info': last_info}
        if event is not None:
            data['event'] = event
        return data

    def save(self, directory, state, settings, last_info, event=None):
        from media_store import manage_photos
        directory = Path(directory)
        manage_photos(state, directory)
        encoded = encode_session(state, settings, last_info, event)
        self._locked_write(directory / 'session.json', encoded)

    def export(self, filename, directory, state, settings, last_info, event=None):
        from media_store import export_assets
        from state_snapshots import mutable_copy
        state = mutable_copy(state)
        assets = export_assets(state, Path(directory))
        data = self.payload(state, settings, last_info, event)
        data.update(format='MahjongOverlayStudio', asset_version=1, assets=assets)
        atomic_json(filename, data, max_bytes=MAX_SESSION_BYTES)

    def read(self, filename, *, expected_event_id=None, observe=True, local=False):
        source = Path(filename)
        if source.stat().st_size > (MAX_LOCAL_SESSION_BYTES if local else MAX_SESSION_BYTES):
            raise ValueError('本地赛事数据超过256MiB' if local else '数据文件超过50MB')
        try:
            encoded = source.read_bytes()
            data = json.loads(encoded.decode('utf-8-sig'), parse_constant=reject_constant)
            if (not isinstance(data, dict) or data.get('version') not in (5, 6)
                    or not isinstance(data.get('state'), dict)):
                raise ValueError('不支持的数据文件格式或版本')
            state = self.migrate(data['state'], data['version'])
            self.engine.validate(state)
            tournament.validate_broadcast(state)
            settings = copy.deepcopy(data.get('settings', {}))
            validate_settings(settings)
            info = copy.deepcopy(data.get('last_info'))
            if info is not None and (
                    not isinstance(info, dict) or not isinstance(info.get('yaku', []), list)
                    or any(not isinstance(value, str) for value in info.get('yaku', []))):
                raise ValueError('和牌展示数据格式无效')
        except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as error:
            raise ValueError('数据结构无效，未更改当前会话') from error
        from media_store import decode_assets
        if data.get('asset_version', 1) != 1:
            raise ValueError('不支持的媒体素材版本')
        embedded = decode_assets(data['assets']) if 'assets' in data else None
        if data.get('event') is not None:
            from event_metadata import validate_event
            validate_event(data['event'], state)
        if expected_event_id is not None and (not data.get('event') or data['event'].get('id') != expected_event_id):
            raise ValueError('已保存的赛事编号与当前工作区不一致，原文件已保留')
        if observe:self.observe(source, encoded)
        return state, settings, info, embedded, data.get('event')
