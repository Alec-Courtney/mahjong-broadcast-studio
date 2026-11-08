"""Commit durable display preferences separately from playback state.

Native dragging remains a preview until release. A failed commit restores the
last accepted layout, while named layout commands retain their pre-edit state.
"""
from __future__ import annotations

import copy
from PySide6.QtCore import QObject, Signal, QSignalBlocker, QRect


LAYOUT_ATTRIBUTES = (
    '_commentary_auto', '_commentary_auto_geometry', '_commentary_reference',
    '_bottom_player_signature', '_bottom_was_enabled', '_settlement_geometry',
)


class DisplayConfigurationService(QObject):
    failed = Signal(str)

    def __init__(self, manager):
        # Model.event is a legacy public data attribute. Giving it QObject
        # children would invoke that attribute as QObject.event on ChildAdded.
        super().__init__()
        self.manager = manager
        self.accepted = None
        self.context = None
        self._disposed = False

    def dispose(self):
        if self._disposed:return
        self._disposed = True
        self.blockSignals(True)
        if self.receivers('2failed(QString)'):self.failed.disconnect()
        self.accepted = None
        self.context = None
        self.manager = None
        self.deleteLater()

    def run_native(self, action):
        """Qt input events report failures after restoration instead of leaking."""
        try:return action()
        except Exception as error:
            self.failed.emit(str(error))
            return False

    def _context(self):
        model = self.manager.model
        return str(model.path), (model.event or {}).get('id')

    def snapshot(self):
        manager = self.manager
        windows = [*manager.windows.values(), *manager.layout_windows.values()]
        windows.extend([manager.players_capture, manager.hud_capture])
        unique = {id(window): window for window in windows}
        return ([(window, QRect(window.geometry())) for window in unique.values()],
                {key: copy.deepcopy(getattr(manager, key)) for key in LAYOUT_ATTRIBUTES})

    def restore(self, snapshot):
        if snapshot is None:return
        manager = self.manager
        windows, attributes = snapshot
        # Derived capture move handlers must not translate the source layout.
        for window, geometry in windows:
            synchronizing = getattr(window, '_synchronizing', None)
            if synchronizing is not None:window._synchronizing = True
            try:window.setGeometry(geometry)
            finally:
                if synchronizing is not None:window._synchronizing = synchronizing
        for key, value in attributes.items():setattr(manager, key, copy.deepcopy(value))
        manager.players_capture.sync_geometry()
        manager.hud_capture.sync_geometry()
        manager.windows['commentary'].base = manager.base_size('commentary')

    def accept_geometry(self):
        self.accepted = self.snapshot()
        self.context = self._context()

    def commit(self, *, settings=None, patch=None, config=None, kind='presentation'):
        model = self.manager.model
        candidate = copy.deepcopy(model.settings if settings is None else settings)
        candidate.update(copy.deepcopy(patch or {}))
        before = model.state
        # The caller publishes once geometry and transient controls are ready.
        with QSignalBlocker(model):
            if config:
                model.configure_presentation(config, settings=candidate, kind=kind, emit=False)
            else:model.save_preferences(candidate)
        return before

    def publish(self, before=None, kind='presentation'):
        model = self.manager.model
        if before is not None and before is not model.state:
            model.changed.emit(before, model.state, kind)
        self.manager.on_display()
        model.display_changed.emit()

    def geometry_edit(self, edit, *, settings_patch=None, notify=True):
        manager = self.manager
        snapshot = self.snapshot()
        settings = manager.model.settings
        manager.model.settings = copy.deepcopy(settings)
        try:
            edit()
            if settings_patch:manager.model.settings.update(copy.deepcopy(settings_patch))
            candidate = manager._collect_geometry_settings()
            manager.model.settings = settings
            self.commit(settings=candidate)
        except Exception:
            manager.model.settings = settings
            self.restore(snapshot)
            raise
        manager.on_display()
        self.accept_geometry()
        if notify:manager.model.display_changed.emit()

    def save_geometry(self, persist=True, geometry_overrides=None):
        manager = self.manager
        original = manager.model.settings
        transient_layout = {key:copy.deepcopy(getattr(manager, key)) for key in LAYOUT_ATTRIBUTES}
        try:
            candidate = manager._collect_geometry_settings()
            if geometry_overrides:candidate['windows'].update(copy.deepcopy(geometry_overrides))
            if not persist:
                manager.model.settings = candidate
                return
            self.commit(settings=candidate)
        except Exception:
            manager.model.settings = original
            if self.accepted is not None:
                self.restore((self.accepted[0], transient_layout))
                manager._sync_bottom_geometry(force=True)
            raise
        self.accept_geometry()
