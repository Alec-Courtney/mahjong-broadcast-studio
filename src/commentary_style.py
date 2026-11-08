"""Event-owned typography for the on-air glossary."""
from __future__ import annotations

COMMENTARY_STYLE_DEFAULTS = {
    'commentary_title_font_family': '',
    'commentary_title_font_size': 26,
    'commentary_title_letter_spacing': 0.,
    'commentary_body_font_family': '',
    'commentary_body_font_size': 24,
    'commentary_body_letter_spacing': 0.,
}
COMMENTARY_STYLE_KEYS = frozenset(COMMENTARY_STYLE_DEFAULTS)
FONT_SIZE_RANGE = (8, 80)
LETTER_SPACING_RANGE = (-2, 12)


def commentary_style_options(config):
    return {key: config.get(key, default) for key, default in COMMENTARY_STYLE_DEFAULTS.items()}


def validate_commentary_style(config):
    values = commentary_style_options(config)
    for role in ('title', 'body'):
        family = values[f'commentary_{role}_font_family']
        size = values[f'commentary_{role}_font_size']
        spacing = values[f'commentary_{role}_letter_spacing']
        if not isinstance(family, str) or len(family) > 150 or any(char in family for char in '\r\n\0'):
            raise ValueError('术语字幕字体无效')
        if type(size) is not int or not FONT_SIZE_RANGE[0] <= size <= FONT_SIZE_RANGE[1]:
            raise ValueError('术语字幕字号须为 8–80 px')
        if (isinstance(spacing, bool) or not isinstance(spacing, (int, float)) or
                not LETTER_SPACING_RANGE[0] <= spacing <= LETTER_SPACING_RANGE[1]):
            raise ValueError('术语字幕字间距须为 -2–12 px')
