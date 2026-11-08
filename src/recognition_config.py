"""Presentation-only recognition source settings, independent of Qt and models."""
from __future__ import annotations

import math
from pathlib import Path

from app_paths import ROOT, LOCAL_RECOGNITION_SAMPLES


_MOVED_SAMPLE_PATHS = {
    ROOT / 'resources/recognition' / name: LOCAL_RECOGNITION_SAMPLES / name
    for name in ('mleague-hand-hd.jpg', 'samples/constructed-tenpai-replay.avi')
}


def default_recognition_config():
    return {'model': 'medium', 'confidence': .55, 'sources': [
        {'enabled': False, 'kind': 'spout', 'path': '', 'roi': [0., 0., 1., 1.]}
        for _ in range(4)]}


def _number(value, low, high, message):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(message)
    return float(value)


def _source(value, player):
    if not isinstance(value, dict):
        raise ValueError(f'第 {player + 1} 席识别来源格式无效')
    enabled = value.get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError(f'第 {player + 1} 席启用设置无效')
    kind = value.get('kind', 'spout')
    if kind not in ('spout', 'image', 'video', 'camera'):
        raise ValueError(f'第 {player + 1} 席识别来源无效')
    path = value.get('path', '')
    if not isinstance(path, str) or len(path) > 4096:
        raise ValueError(f'第 {player + 1} 席来源路径无效')
    path = path.strip()
    if enabled and kind == 'camera':
        path = path or '0'
        if not path.isdecimal() or len(path) > 4:
            raise ValueError(f'第 {player + 1} 席摄像头编号须为非负整数')
        path = str(int(path))
    elif enabled and not path:
        target = 'OBS 来源名称' if kind == 'spout' else '图片或视频'
        raise ValueError(f'请为第 {player + 1} 席选择{target}')
    if path and kind in ('image', 'video'):
        original = Path(path)
        relocated = _MOVED_SAMPLE_PATHS.get(original)
        if relocated and not original.is_file() and relocated.is_file():
            path = str(relocated)
    raw_roi = value.get('roi', [0., 0., 1., 1.])
    if not isinstance(raw_roi, (list, tuple)) or len(raw_roi) != 4:
        raise ValueError(f'第 {player + 1} 席手牌区域须为 x、y、宽、高')
    roi = [_number(v, 0., 1., f'第 {player + 1} 席手牌区域须在 0% 到 100% 之间') for v in raw_roi]
    x, y, width, height = roi
    if width <= 0 or height <= 0 or x + width > 1.000000001 or y + height > 1.000000001:
        raise ValueError(f'第 {player + 1} 席手牌区域超出画面或宽高为零')
    roi[2], roi[3] = min(width, 1. - x), min(height, 1. - y)
    result = {'enabled': enabled, 'kind': kind, 'path': path, 'roi': roi}
    if kind == 'camera':
        for key, maximum in (('identifier', 4096), ('name', 256), ('backend', 32)):
            if key in value:
                if not isinstance(value[key], str) or len(value[key]) > maximum:
                    raise ValueError(f'第 {player + 1} 席摄像头设备信息无效')
                result[key] = value[key]
        if 'index' in value:
            if type(value['index']) is not int or not 0 <= value['index'] <= 9999:
                raise ValueError(f'第 {player + 1} 席摄像头编号无效')
            result['index'] = value['index']
    return result


def normalize_recognition_config(value=None, *, strict=False):
    """Return an isolated complete value; invalid saved sources default disabled.

    Strict validation is for an edited settings form before any setting is saved.
    Loading a missing setting is harmless and never starts a capture source.
    """
    defaults = default_recognition_config()
    if value is None:
        return defaults
    if not isinstance(value, dict):
        if strict:
            raise ValueError('牌面识别设置格式无效')
        return defaults
    model = value.get('model', defaults['model'])
    if model not in ('medium', 'small', 'nano'):
        if strict:
            raise ValueError('牌面识别模型无效')
        model = defaults['model']
    try:
        confidence = _number(value.get('confidence', .55), .1, .99, '识别可信度须在 0.10 到 0.99 之间')
    except ValueError:
        if strict:
            raise
        confidence = .55
    raw_sources = value.get('sources', defaults['sources'])
    if not isinstance(raw_sources, list) or len(raw_sources) != 4:
        if strict:
            raise ValueError('牌面识别须对应当前四席')
        raw_sources = defaults['sources']
    sources = []
    for player, source in enumerate(raw_sources):
        try:
            sources.append(_source(source, player))
        except ValueError:
            if strict:
                raise
            sources.append(defaults['sources'][player])
    return {'model': model, 'confidence': confidence, 'sources': sources}
