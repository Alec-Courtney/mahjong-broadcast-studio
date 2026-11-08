"""Event-scoped title media for the pre-match team introduction."""
from pathlib import Path
import sys

DEFAULT_TITLE_FILE = 'SHOU联盟 S2赛季标题卡.png'
OPENING_DEFAULTS = {'opening_title': '', 'opening_title_text_only': False}
OPENING_SETTING_KEYS = frozenset(OPENING_DEFAULTS)


def opening_title_reference(model):
    """Keep scratch projects isolated; resolve the default from the app's project."""
    config=model.state.get('broadcast',{}).get('config',{})
    if config.get('opening_title_text_only',False):return ''
    if config.get('opening_title'):return config['opening_title']
    from media_store import asset_project_root
    root=Path(getattr(model,'asset_root',None) or asset_project_root(model.path))
    candidates=[root/DEFAULT_TITLE_FILE]
    if getattr(sys,'frozen',False):
        candidates.append(Path(getattr(sys,'_MEIPASS',root))/DEFAULT_TITLE_FILE)
    return next((str(path) for path in candidates if path.is_file()),'')


def validate_opening_config(config):
    reference = config.get('opening_title', '')
    if (not isinstance(reference, str) or len(reference) > 4096
            or any(char in reference for char in '\r\n\0')):
        raise ValueError('开场标题卡路径格式无效')
    if not isinstance(config.get('opening_title_text_only',False),bool):
        raise ValueError('开场文字标题开关格式无效')
