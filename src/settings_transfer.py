"""Portable settings files for the application and an individual event."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from app_settings import default_settings
from application_preferences import APPLICATION_KEYS, capture_preferences
from commentary_style import COMMENTARY_STYLE_KEYS
from corner_header import CORNER_SETTING_KEYS
from event_metadata import KINDS, now, validate_event
from opening_config import OPENING_SETTING_KEYS
from postmatch_config import POSTMATCH_SETTING_KEYS
from session_repository import MAX_SESSION_BYTES, atomic_json, reject_constant, validate_settings
from sidebar_presets import SIDEBAR_SETTING_KEYS

SYSTEM_FORMAT = 'MahjongOverlayStudioSystemSettings'
EVENT_FORMAT = 'MahjongOverlayStudioEventSettings'

EVENT_FIELDS = ('planned_games', 'qualification_count', 'report_columns', 'report_headers',
                'player_plans', 'team_plans', 'team_names')
PLAYER_FIELDS = ('qualification_focus', 'manual_conditions', 'qualification_titles')
RULE_FIELDS = ('kiriageMangan', 'kazoeYakuman')
LAYOUT_VERSIONS = ('layout_version', 'broadcast_canvas_version', 'hud_group_version', 'hud_layout_version',
                   'win_banner_canvas_version', 'sidebar_fixed_canvas_version', 'commentary_layout_version',
                   'fullscreen_canvas_version', 'player_capture_version')
CONFIG_FIELDS = SIDEBAR_SETTING_KEYS | OPENING_SETTING_KEYS | POSTMATCH_SETTING_KEYS | CORNER_SETTING_KEYS | COMMENTARY_STYLE_KEYS | {
    'start_points', 'return_points', 'uma', 'oka_enabled', 'tie_break',
    'settlement_enabled', 'settlement_hold', 'settlement_total_hold', 'riichi_effect_enabled',
    'sidebar_presets', 'sidebar_duration', 'table_score_mode', 'table_score_layout', 'show_match_title',
    'standings_mode', 'standings_page', 'standings_page_size', 'standings_live', 'hud_opacity',
    'chart_mode', 'statistics_scope', 'statistics_view', 'statistics_page',
    'qualification_title', 'qualification_calculation', 'qualification_final', 'qualification_tie_break',
    'commentary_title', 'commentary_body', 'commentary_builtin', 'commentary_duration',
    'custom_terms', 'term_favorites', 'term_buttons', 'term_button_columns', *PLAYER_FIELDS,
}
DISPLAY_FIELDS = {
    'theme', 'player_style_version', 'player_card_style', 'saikouisen_card_style', 'show_melds',
    'riichi_payment_mode', 'speed', 'final_scores_hold', 'overlay_font_family', 'overlay_font_scale',
    'capture_bg', 'locked', 'topmost', 'split_fullscreen_windows', 'player_side_padding',
    'windows', 'visible', 'broadcast_scene', 'player_group_linked', 'fullscreen_panel', *LAYOUT_VERSIONS,
    'qualification_geometries', 'qualification_geometry_mode',
    'commentary_auto', 'commentary_auto_geometry', 'report_output_geometry',
    'console_layout', 'hotkeys', 'recognition_assist',
}


def _select(values, fields):
    if not isinstance(values, dict):
        raise ValueError('设置文件内容无效')
    return {key: copy.deepcopy(values[key]) for key in fields if key in values}


def _read_json(filename, limit=MAX_SESSION_BYTES):
    with Path(filename).open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('设置文件超过大小限制')
    try:
        data = json.loads(raw.decode('utf-8-sig'), parse_constant=reject_constant)
    except (UnicodeError, ValueError) as error:
        raise ValueError('无法读取设置文件') from error
    if not isinstance(data, dict):
        raise ValueError('设置文件格式无效')
    return data


def export_system_settings(model, filename):
    settings = model.preferences.read()
    if settings is None:
        settings = capture_preferences(model.settings)
    atomic_json(filename, dict(format=SYSTEM_FORMAT, version=1, settings=settings))


def import_system_settings(model, filename):
    data = _read_json(filename, 1_000_000)
    if (data.get('format') not in (None, SYSTEM_FORMAT)
            or type(data.get('version')) is not int or data['version'] != 1):
        raise ValueError('请选择全局设置文件')
    values = _select(data.get('settings'), APPLICATION_KEYS)
    if not values:
        raise ValueError('设置文件中没有全局设置')
    capture_preferences(values)
    model.save_preferences(dict(model.settings, **values), application_keys=set(values))


def event_template(state, settings, event):
    return dict(
        format=EVENT_FORMAT, version=1, kind=event['kind'], source_id=event['id'],
        event=_select(event, EVENT_FIELDS), rules=_select(state['rules'], RULE_FIELDS),
        broadcast=_select(state['broadcast']['config'], CONFIG_FIELDS),
        display=_select(settings, DISPLAY_FIELDS),
    )


def export_event_settings(model, filename):
    from media_store import export_assets

    data = event_template(model.state, model.settings, model.event)
    data['assets'] = export_assets(data, model.path)
    atomic_json(filename, data, max_bytes=MAX_SESSION_BYTES)


def read_event_settings(model, filename):
    from media_store import decode_assets

    data = _read_json(filename)
    if data.get('format') == EVENT_FORMAT and type(data.get('version')) is int and data['version'] == 1:
        if not isinstance(data.get('kind'), str) or data['kind'] not in KINDS:
            raise ValueError('赛事设置的比赛类型无效')
        template = dict(kind=data['kind'], source_id=data.get('source_id'),
                        event=_select(data.get('event'), EVENT_FIELDS),
                        rules=_select(data.get('rules'), RULE_FIELDS),
                        broadcast=_select(data.get('broadcast'), CONFIG_FIELDS),
                        display=_select(data.get('display'), DISPLAY_FIELDS))
        return template, decode_assets(data.get('assets', {}))
    if data.get('version') in (5, 6) and isinstance(data.get('state'), dict):
        state, settings, _, embedded, event = model.repository.read(filename, observe=False)
        if event:
            return event_template(state, settings, event), embedded
    raise ValueError('请选择赛事设置文件或完整赛事备份')


def apply_event_template(state, settings, event, template, *, new=False):
    """Use source settings while keeping the target event and its roster."""
    import tournament

    same_kind = event['kind'] == template['kind']
    same_event = same_kind and not new and event['id'] == template.get('source_id')
    fields = ['planned_games']
    if same_kind:
        fields.extend(('qualification_count', 'report_columns', 'report_headers'))
    if same_event:
        fields.extend(('player_plans', 'team_plans', 'team_names'))
    event.update(_select(template['event'], fields))
    event['updated_at'] = now()

    config = copy.deepcopy(template['broadcast'])
    shared_keys = CONFIG_FIELDS if same_event else CONFIG_FIELDS.difference(PLAYER_FIELDS)
    live = state['broadcast']['config']
    for key in shared_keys - config.keys():
        live.pop(key, None)
    if not same_event:
        for key in PLAYER_FIELDS:
            config.pop(key, None)
    if event['kind'] != 'team_league':
        for key in ('sidebar_mode', 'standings_mode'):
            if config.get(key) == 'team':
                config[key] = 'individual'
    if event['kind'] != 'individual_knockout':
        config['qualification_slots'] = event.get('qualification_count', 0) or 2
    tournament.configure(state, config)
    state['rules'].update(copy.deepcopy(template['rules']))
    defaults = default_settings()
    for key in DISPLAY_FIELDS - template['display'].keys():
        if key in defaults:
            settings[key] = defaults[key]
        else:
            settings.pop(key, None)
    settings.update(copy.deepcopy(template['display']))
    validate_settings(settings)
    for key in LAYOUT_VERSIONS:
        if key in settings:
            settings[key] = tournament._integer(settings[key], '布局版本', 0, 10000)
    layouts = settings.get('qualification_geometries', {})
    if not isinstance(layouts, dict):
        raise ValueError('晋级窗口位置格式无效')
    geometry = dict(layouts)
    for key in ('report_output_geometry', 'commentary_auto_geometry'):
        if settings.get(key) is not None:
            geometry[key] = settings[key]
    if geometry:
        validate_settings({'windows': geometry})
    if 'recognition_assist' in settings:
        from recognition_config import normalize_recognition_config
        settings['recognition_assist'] = normalize_recognition_config(settings['recognition_assist'], strict=True)
    if 'hotkeys' in settings:
        from hotkeys import validate_bindings
        validate_bindings(settings['hotkeys'])
    validate_event(event, state)


def import_event_settings(model, filename):
    from event_configuration import save_configuration
    from media_store import manage_photos
    from state_snapshots import copy_state

    template, assets = read_event_settings(model, filename)
    state = copy_state(model.state)
    settings, event = copy.deepcopy((model.settings, model.event))
    apply_event_template(state, settings, event, template)
    manage_photos(state['broadcast']['config'], model.path, source_base=Path(filename).parent,
                  embedded=assets, strict=True)
    save_configuration(model, state, event, settings, clear_history=True, kind='event_settings')
