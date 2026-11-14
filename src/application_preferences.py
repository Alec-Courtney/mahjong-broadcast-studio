"""Local operator preferences, independent of the selected event or UI workspace.

Event files keep a portable snapshot for exports. The local library preference
file is authoritative when opening events, so an older event cannot change the
operator's theme, language or desk fonts.
"""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from pathlib import Path
from PySide6.QtCore import QLockFile

from app_settings import default_settings, validate_console_theme
from session_repository import atomic_json, reject_constant


APPLICATION_KEYS = ('console_theme', 'console_font_family', 'console_font_size',
                    'settings_font_size', 'language', 'console_dock_animations',
                    'console_meld_tile_scale', 'console_wait_tile_scale')


def preference_defaults():
    defaults = default_settings()
    return {key: defaults.get(key, '' if key == 'console_font_family' else 'zh_CN')
            for key in APPLICATION_KEYS}


def capture_preferences(settings):
    values = preference_defaults()
    values.update({key: copy.deepcopy(settings[key]) for key in APPLICATION_KEYS if key in settings})
    from typography import validate
    validate(values)
    from desk_tiles import validate_tile_sizes
    validate_tile_sizes(values)
    validate_console_theme(values['console_theme'])
    if values['language'] not in ('zh_CN', 'ja_JP'):
        raise ValueError('界面语言无效')
    if not isinstance(values['console_dock_animations'], bool):
        raise ValueError('导播界面动画开关无效')
    return values


def infer_preferences_root(directory):
    directory = Path(directory).resolve()
    # Model(event_directory) is also used by restart/export tools. Reuse the
    # owning library's preferences rather than creating an event-local copy.
    if directory.parent.name == 'events' and (directory.parent.parent / 'library.json').is_file():
        return directory.parent.parent
    return directory


class ApplicationPreferences:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.path = self.root / 'application-preferences.json'
        self.baseline = None
        self._transaction_lock = None

    @contextmanager
    def transaction(self):
        """Serialize a preference merge and its paired session write across apps."""
        if self._transaction_lock is not None:
            yield
            return
        self.root.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(self.root / 'application-preferences.lock'))
        if not lock.tryLock(1000):raise ValueError('界面设置正在被其他窗口保存，请稍后重试')
        self._transaction_lock = lock
        try:
            yield
        finally:
            self._transaction_lock = None
            lock.unlock()

    def read(self):
        """Return None for unmigrated or unreadable preferences; keep source files."""
        try:
            if self.path.stat().st_size > 1_000_000:
                return None
            data = json.loads(self.path.read_text(encoding='utf-8-sig'), parse_constant=reject_constant)
            if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('settings'), dict):
                return None
            return capture_preferences(data['settings'])
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def apply(self, settings):
        values = self.read()
        if values is None:
            values = self._legacy_preferences()
        if values is not None:
            settings.update(values)
        self.baseline = capture_preferences(settings)

    def _legacy_preferences(self):
        """Bootstrap even when the first upgraded launch uses the UI-only desk.

        Read only operator fields from the active old session. A missing/broken
        library must never stop a standalone desk from opening.
        """
        try:
            source = self.root / 'session.json'
            index_path = self.root / 'library.json'
            if index_path.is_file():
                if index_path.stat().st_size > 1_000_000:
                    return None
                index = json.loads(index_path.read_text(encoding='utf-8-sig'), parse_constant=reject_constant)
                from event_metadata import ID
                identity = index.get('active_id') if isinstance(index, dict) else None
                if not isinstance(identity, str) or not ID.fullmatch(identity):
                    return None
                source = self.root / 'events' / identity / 'session.json'
            from session_repository import MAX_LOCAL_SESSION_BYTES
            if source.stat().st_size > MAX_LOCAL_SESSION_BYTES:
                return None
            payload = json.loads(source.read_text(encoding='utf-8-sig'), parse_constant=reject_constant)
            if not isinstance(payload, dict) or not isinstance(payload.get('settings'), dict):
                return None
            return capture_preferences(payload['settings'])
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def merge(self, settings, explicit_keys=()):
        """Only locally edited fields replace another workspace's newer values."""
        local = capture_preferences(settings)
        latest = self.read()
        if latest is None:
            return local
        baseline = self.baseline or local
        changed = {key for key in APPLICATION_KEYS if local[key] != baseline[key]}
        changed.update(set(explicit_keys) & set(APPLICATION_KEYS))
        latest.update({key: local[key] for key in changed})
        return latest

    def save(self, settings, *, prepared=None):
        with self.transaction():
            values = capture_preferences(prepared) if prepared is not None else self.merge(settings)
            if values != self.read():
                atomic_json(self.path, dict(version=1, settings=values))
            self.baseline = copy.deepcopy(values)
            settings.update(values)
