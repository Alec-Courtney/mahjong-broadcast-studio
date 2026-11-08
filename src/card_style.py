"""Small, versioned player-card styles shared by rendering and the draft editor."""
from __future__ import annotations

import copy
import math
import re

from PySide6.QtCore import QPointF
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient


def _paint(mode='solid', start='#FFFFFFFF', end='#FFFFFFFF', direction='horizontal'):
    return dict(mode=mode, start=start, end=end, direction=direction)


def default_card_style(theme='mleague'):
    def label(size, bold, color='#FFFFFFFF', spacing=0.):
        return dict(family='', size=size, letter_spacing=spacing, bold=bold, paint=_paint(start=color, end=color))
    if theme == 'saikouisen':
        return {
            'version': 1, 'typography_version': 2, 'team_name_gap': 3.,
            'text': {'team': label(14, False, '#FF536064'), 'name': label(28, True, '#FF111516'),
                     'score': label(53, True, '#FF070909'), 'rank': label(25, False)},
            'backgrounds': {key: dict(source='custom', **_paint(start='#FFFDFDF9', end='#FFFDFDF9'))
                            for key in ('score', 'identity')},
        }
    return {
        'version': 1,
        'typography_version': 5,
        'team_name_gap': 11.,
        'text': {'team': label(20, True, spacing=1.), 'name': label(33, True, spacing=1.2),
                 'score': label(54, True, spacing=.4), 'rank': label(35, True, spacing=.4)},
        'backgrounds': {
            'score': dict(source='team', **_paint('gradient', '#70167E9E', '#DC062D4D')),
            'identity': dict(source='custom', **_paint('gradient', '#20101418', '#CD101418', 'vertical')),
        },
    }


def _number(value, low, high, label):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high):
        raise ValueError(label+'超出范围')


def _validate_paint(value, background=False):
    if not isinstance(value, dict):
        raise ValueError('卡片颜色设置无效')
    if value.get('mode', 'solid') not in ('solid', 'gradient'):
        raise ValueError('卡片填充方式无效')
    if value.get('direction', 'horizontal') not in ('horizontal', 'vertical', 'diagonal'):
        raise ValueError('卡片渐变方向无效')
    for key in ('start', 'end'):
        color = value.get(key, '#FFFFFFFF')
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?', color):
            raise ValueError('卡片颜色须为 #RRGGBB 或 #AARRGGBB')
    if background and value.get('source', 'custom') not in ('team', 'custom'):
        raise ValueError('卡片背景颜色来源无效')


def validate_card_style(value):
    """Validate a partial or complete imported style without changing it."""
    if not isinstance(value, dict):
        raise ValueError('选手卡样式设置无效')
    if type(value.get('version', 1)) is not int or value.get('version', 1) != 1:
        raise ValueError('不支持的选手卡样式版本')
    if type(value.get('typography_version', 1)) is not int or value.get('typography_version', 1) not in (1, 2, 3, 4, 5):
        raise ValueError('不支持的选手卡字体版本')
    _number(value.get('team_name_gap', 6), 0, 20, '队名姓名间距')
    labels = value.get('text', {})
    if not isinstance(labels, dict):
        raise ValueError('选手卡文字设置无效')
    for key in ('team', 'name', 'score', 'rank'):
        spec = labels.get(key, {})
        if not isinstance(spec, dict):
            raise ValueError('选手卡文字设置无效')
        family = spec.get('family', '')
        if not isinstance(family, str) or len(family) > 150 or any(c in family for c in '\r\n{};'):
            raise ValueError('选手卡字体名称无效')
        _number(spec.get('size', 16), 8, 80, '选手卡字号')
        _number(spec.get('letter_spacing', 0), -2, 12, '选手卡字间距')
        if not isinstance(spec.get('bold', False), bool):
            raise ValueError('选手卡字重设置无效')
        _validate_paint(spec.get('paint', {}))
    backgrounds = value.get('backgrounds', {})
    if not isinstance(backgrounds, dict):
        raise ValueError('选手卡背景设置无效')
    for key in ('score', 'identity'):
        _validate_paint(backgrounds.get(key, {}), background=True)


def normalize_card_style(value=None, theme='mleague'):
    """Return a fresh complete style. Invalid input raises ValueError."""
    value = {} if value is None else value
    validate_card_style(value)
    defaults = default_card_style(theme)
    value = copy.deepcopy(value)
    previous_identity = dict(source='custom', **_paint('gradient', '#00101418', '#B5101418', 'vertical'))
    if theme != 'saikouisen' and value.get('backgrounds', {}).get('identity') == previous_identity:
        value['backgrounds']['identity'] = copy.deepcopy(defaults['backgrounds']['identity'])
    if value.get('typography_version', 1) == 1:
        # Upgrade only unchanged old typography. Preserve chosen fonts, sizes,
        # tracking, weights and all user paints/backgrounds independently.
        old = ({'team': (14, False), 'name': (28, False), 'score': (47, False), 'rank': (25, False)}
               if theme == 'saikouisen' else
               {'team': (16, True), 'name': (29, True), 'score': (47, False), 'rank': (33, False)})
        for key, (size, bold) in old.items():
            spec = value.get('text', {}).get(key)
            if spec is not None and (spec.get('family', ''), spec.get('size', size),
                    spec.get('letter_spacing', 0.), spec.get('bold', bold)) == ('', size, 0., bold):
                spec.update({field: defaults['text'][key][field]
                             for field in ('family', 'size', 'letter_spacing', 'bold')})
        old_gap = 3. if theme == 'saikouisen' else 6.
        if value.get('team_name_gap', old_gap) == old_gap:
            value['team_name_gap'] = defaults['team_name_gap']
        value['typography_version'] = 2
    if theme != 'saikouisen' and value.get('typography_version', 1) == 2:
        spec = value.get('text', {}).get('score')
        if spec is not None and (spec.get('family', ''), spec.get('size', 58),
                spec.get('letter_spacing', 1.2), spec.get('bold', True)) == ('', 58, 1.2, True):
            spec.update({field: defaults['text']['score'][field]
                         for field in ('family', 'size', 'letter_spacing', 'bold')})
        value['typography_version'] = 3
    if theme != 'saikouisen' and value.get('typography_version', 1) == 3:
        # Move the unchanged ML identity pair closer to the wait tiles without
        # overriding an edited font, tracking, weight, size, gap or paint.
        spec = value.get('text', {}).get('name')
        if spec is not None and (spec.get('family', ''), spec.get('size', 35),
                spec.get('letter_spacing', 1.2), spec.get('bold', True)) == ('', 35, 1.2, True):
            spec.update({field: defaults['text']['name'][field]
                         for field in ('family', 'size', 'letter_spacing', 'bold')})
        if value.get('team_name_gap', 4.) == 4.:
            value['team_name_gap'] = defaults['team_name_gap']
        value['typography_version'] = 4
    if theme != 'saikouisen' and value.get('typography_version', 1) == 4:
        # Strengthen unchanged ML point digits while retaining edited metrics,
        # paints, and an explicit weight chosen after this migration.
        spec = value.get('text', {}).get('score')
        if spec is not None and (spec.get('family', ''), spec.get('size', 54),
                spec.get('letter_spacing', .4), spec.get('bold', False)) == ('', 54, .4, False):
            spec['bold'] = True
        value['typography_version'] = 5
    def merge(base, incoming):
        for key in base:
            if key not in incoming:
                continue
            if isinstance(base[key], dict):
                merge(base[key], incoming[key])
            else:
                base[key] = copy.deepcopy(incoming[key])
    merge(defaults, value)
    return defaults


def get_card_style(settings=None, theme=None):
    settings = settings or {}
    theme = theme or settings.get('theme', 'mleague')
    key = 'saikouisen_card_style' if theme == 'saikouisen' else 'player_card_style'
    return normalize_card_style(settings.get(key), theme)


def card_font(spec, settings=None, fallback='Microsoft YaHei'):
    settings = settings or {}
    scale = settings.get('overlay_font_scale', 1.)
    font = QFont(spec.get('family') or settings.get('overlay_font_family') or fallback)
    font.setPixelSize(max(1, round(spec['size'] * scale)))
    font.setWeight(QFont.Weight.DemiBold if spec['bold'] else QFont.Weight.Normal)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spec['letter_spacing'] * scale)
    return font


def paint_brush(spec, bounds, accent=None):
    """Use local geometry for gradients, including vector/SVG output."""
    first, last = QColor(spec['start']), QColor(spec['end'])
    if spec.get('source') == 'team' and accent is not None:
        base = QColor(accent)
        first_alpha, last_alpha = first.alpha(), last.alpha()
        first, last = (base.darker(190), QColor(base)) if spec['mode'] == 'gradient' else (QColor(base), QColor(base))
        first.setAlpha(first_alpha)
        last.setAlpha(last_alpha)
    if spec['mode'] == 'solid':
        return QBrush(first)
    direction = spec['direction']
    origin = bounds.topLeft()
    end = (QPointF(bounds.right(), bounds.top()) if direction == 'horizontal'
           else QPointF(bounds.left(), bounds.bottom()) if direction == 'vertical'
           else bounds.bottomRight())
    gradient = QLinearGradient(origin, end)
    gradient.setColorAt(0, first)
    gradient.setColorAt(1, last)
    return QBrush(gradient)
