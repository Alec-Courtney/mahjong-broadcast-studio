"""Application defaults shared by sessions and the event library."""


def default_settings():
    from card_style import default_card_style
    return {
        'theme': 'mleague',
        'player_style_version': 1,
        'player_card_style': default_card_style('mleague'),
        'saikouisen_card_style': default_card_style('saikouisen'),
        'console_theme': 'dark',
        'console_font_size': 14,
        'settings_font_size': 14,
        'console_dock_animations': False,
        'locked': False,
        'topmost': False,
        'capture_bg': 'transparent',
        'split_fullscreen_windows': False,
        'player_side_padding': 2.3,
        'speed': 1.0,
        'windows': {},
        'show_yaku': False,
        'show_overlays': True,
        'final_points_mode': 'scores',
        'final_cards_visible': False,
        'riichi_payment_mode': 'immediate',
        'show_melds': True,
        'final_scores_hold': 4.0,
    }


def validate_console_theme(value):
    if value not in ('dark', 'light'):
        raise ValueError('后台主题无效')


def validate_player_style(value):
    if value not in ('mleague', 'saikouisen'):
        raise ValueError('选手播出样式无效')


def normalize_player_style(settings, *, strict=False):
    """Upgrade unmarked old sessions once, then preserve an explicit style choice."""
    version = settings.get('player_style_version', 0)
    if version == 0 and not isinstance(version, bool):
        settings['theme'] = 'mleague'
    elif type(version) is not int or version != 1:
        if strict:
            raise ValueError('选手播出样式版本无效')
        settings['theme'] = 'mleague'
    elif settings.get('theme', 'mleague') not in ('mleague', 'saikouisen'):
        if strict:
            raise ValueError('选手播出样式无效')
        settings['theme'] = 'mleague'
    settings.setdefault('theme', 'mleague')
    settings['player_style_version'] = 1

