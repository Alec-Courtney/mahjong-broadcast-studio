"""Publish event/configuration edits only after their complete snapshot is saved."""
from __future__ import annotations

import copy

import tournament
from event_metadata import validate_event
from state_snapshots import copy_state, seal_archives


def save_configuration(model, state, event, settings=None, *, clear_history=False, kind='broadcast'):
    before = copy_state(model.state)
    after = copy_state(state)
    settings = copy.deepcopy(model.settings if settings is None else settings)
    event = copy.deepcopy(event)
    if model.event_store:
        if not event or event.get('id') != model.event.get('id'):
            raise ValueError('当前赛事已切换，请重新打开设置。')
        if event.get('kind') != model.event.get('kind'):
            raise ValueError('比赛类型在创建后不可修改，请新建赛事并选择所需类型')
        if before['broadcast']['hands'] and before['phase'] != 'finished':
            rule_keys = ('returnPoints', 'uma', 'okaEnabled', 'tieBreak', 'kiriageMangan', 'kazoeYakuman')
            if any(before['rules'].get(key) != after['rules'].get(key) for key in rule_keys):
                raise ValueError('当前半庄进行中，请完成并确认后再修改计分规则')
    # A settings-only import leaves settled and confirmed result snapshots intact.
    if kind != 'event_settings':
        tournament.on_update(before, after, kind)
    tournament.validate_broadcast(after)
    model.engine.validate(after)
    if event:validate_event(event, after)
    model._save_snapshot(after, settings, model.last_info, event)
    seal_archives(after)
    model.state, model.event, model.settings = after, event, settings
    if clear_history:
        model.undo.clear()
        model.redo.clear()
    else:
        model.undo.append((before, copy.deepcopy(model.last_info)))
        model.undo = model.undo[-60:]
        model.redo.clear()
    model.changed.emit(before, after, kind)
    model.display_changed.emit()
