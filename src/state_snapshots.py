"""Share immutable archived results across independent table undo snapshots.

The public state remains JSON-compatible dictionaries/lists. Completed archive
records are read-only: corrections construct a replacement record through the
results service. The list of archive records itself remains a mutable list.
"""
import copy


def _readonly(*_args, **_kwargs):
    raise TypeError('历史归档为只读，请通过成绩更正保存替换记录')


class FrozenDict(dict):
    """A JSON object whose payload can safely be shared by deepcopy."""
    def __copy__(self):return self
    def __deepcopy__(self, memo):
        memo[id(self)] = self
        return self
    def __setitem__(self, key, value):
        # Normalizing an already-managed media reference is a harmless no-op.
        if key in self and self[key] == value:return
        _readonly()
    def update(self, *args, **kwargs):
        values = dict(*args, **kwargs)
        if all(key in self and self[key] == value for key, value in values.items()):return
        _readonly()
    def setdefault(self, key, default=None):
        if key in self:return self[key]
        _readonly()
    def __ior__(self, other):self.update(other);return self
    __delitem__ = clear = pop = popitem = _readonly


class FrozenList(list):
    """Nested JSON arrays inside an immutable archived result."""
    def __copy__(self):return self
    def __deepcopy__(self, memo):
        memo[id(self)] = self
        return self
    def __setitem__(self, key, value):
        if self[key] == value:return
        _readonly()
    __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = _readonly
    __iadd__ = __imul__ = _readonly


def freeze(value):
    if isinstance(value, (FrozenDict, FrozenList)):return value
    if isinstance(value, dict):
        result = FrozenDict()
        for key, child in value.items():dict.__setitem__(result, key, freeze(child))
        return result
    if isinstance(value, list):return FrozenList(freeze(child) for child in value)
    return value


def mutable_copy(value):
    """Explicit writable JSON copy for imports, exports or archive corrections."""
    if isinstance(value, dict):return {key: mutable_copy(child) for key, child in value.items()}
    if isinstance(value, list):return [mutable_copy(child) for child in value]
    if isinstance(value, tuple):return tuple(mutable_copy(child) for child in value)
    return copy.deepcopy(value)


def seal_archives(state):
    broadcast = state.get('broadcast', {})
    records = broadcast.get('matches', [])
    for index, record in enumerate(records):
        if not isinstance(record, FrozenDict):records[index] = freeze(record)
    return state


def copy_state(state):
    """Editable table/config copy; stable sealed archives are shared read-only."""
    return copy.deepcopy(state)
