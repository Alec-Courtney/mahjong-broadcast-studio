"""Event-scoped controls for the reference post-match programme."""
from __future__ import annotations

import math


POSTMATCH_DEFAULTS = {
    'postmatch_chart_hold': 15.,
    'postmatch_stats_hold': 15.,
    'postmatch_music_enabled': True,
    'postmatch_sfx_enabled': True,
    'postmatch_music_volume': .35,
    'postmatch_sfx_volume': .7,
    'postmatch_music_path': '',
    'postmatch_background_path': '',
}
POSTMATCH_SETTING_KEYS = frozenset(POSTMATCH_DEFAULTS)


def validate_postmatch_config(config):
    for key in ('postmatch_chart_hold', 'postmatch_stats_hold'):
        value = config.get(key, POSTMATCH_DEFAULTS[key])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 1 <= value <= 300:
            raise ValueError('赛后完整画面停留须为 1–300 秒')
    for key in ('postmatch_music_volume', 'postmatch_sfx_volume'):
        value = config.get(key, POSTMATCH_DEFAULTS[key])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('赛后音量须为 0–100%')
    for key in ('postmatch_music_enabled', 'postmatch_sfx_enabled'):
        if not isinstance(config.get(key, POSTMATCH_DEFAULTS[key]), bool):
            raise ValueError('赛后声音开关格式无效')
    for key in ('postmatch_music_path', 'postmatch_background_path'):
        value = config.get(key, '')
        if not isinstance(value, str) or len(value) > 4096 or any(c in value for c in '\r\n\0'):
            raise ValueError('赛后媒体路径格式无效')


def postmatch_options(config):
    result = {key: config.get(key, default) for key, default in POSTMATCH_DEFAULTS.items()}
    validate_postmatch_config(result)
    return result
