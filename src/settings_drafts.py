"""Three-way merging for editors sharing live configuration fields."""
import copy

_MISSING = object()


def _value_at(values, path):
    for part in path:
        if not isinstance(values, dict) or part not in values:
            return _MISSING
        values = values[part]
    return values


def _edited_paths(before, after, path=()):
    if isinstance(before, dict) and isinstance(after, dict):
        for key in dict.fromkeys([*before, *after]):
            value = after.get(key, _MISSING)
            yield from _edited_paths(before.get(key, _MISSING), value, path + (key,))
    elif before != after:
        yield path, after


def merge_draft(baseline_draft, draft, baseline_live, live):
    """Preserve others' changes, rejecting only divergent edits to one field."""
    result, changed, conflicts = copy.deepcopy(live), set(), []
    for path, value in _edited_paths(baseline_draft, draft):
        old, current = _value_at(baseline_live, path), _value_at(live, path)
        if current != old and current != value:
            conflicts.append(path)
            continue
        target = result
        for part in path[:-1]:
            if not isinstance(target.get(part), dict):target[part] = {}
            target = target[part]
        if value is _MISSING:target.pop(path[-1], None)
        else:target[path[-1]] = copy.deepcopy(value)
        changed.add(path[0])
    return result, changed, conflicts
