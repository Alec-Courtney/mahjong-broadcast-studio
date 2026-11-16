"""Restore a workspace backed up by the legacy demo loader."""
from __future__ import annotations


class DemoSession:
    def __init__(self, model):
        self.model = model

    @property
    def active(self):
        return bool(self.model.settings.get('demo_active', False))

    @property
    def backup_path(self):
        return self.model.path / 'demo-restore.mjbackup'

    def restore(self):
        backup = self.backup_path
        legacy=self.model.path/'demo-restore.json'
        if not self.active or not (backup.is_file() or legacy.is_file()):
            raise ValueError('没有可恢复的测试前数据')
        from backup_package import convert_legacy,EXTENSION
        target=backup
        if not target.is_file():convert_legacy(legacy)
        self.model.import_data(target, replace_settings=True, clear_history=True,
                               allow_event_kind_change=True)
        return backup
