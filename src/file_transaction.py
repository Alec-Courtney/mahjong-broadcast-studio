"""Small in-memory grouping for related JSON saves, with exception rollback.

The existing complete JSON files remain the only persistent representation.
No journal, recovery directory or startup migration is created.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_active = ContextVar('workspace_file_transaction', default=None)
_NAMES = {'session.json', 'application-preferences.json', 'library.json'}


def _write(path, content):
    from session_repository import _atomic_bytes_now
    _atomic_bytes_now(path, content)


class _Transaction:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.writes = {}

    def stage(self, path, content):
        path = Path(path).resolve()
        if path.name not in _NAMES:return False
        if path == self.root or not path.is_relative_to(self.root):
            raise ValueError('配对保存的文件不在同一工作区中')
        self.writes[path] = content
        return True

    def commit(self):
        previous = {path: path.read_bytes() if path.exists() else None for path in self.writes}
        changed = [path for path, content in self.writes.items() if previous[path] != content]
        completed = []
        try:
            for path in changed:
                _write(path, self.writes[path])
                completed.append(path)
        except Exception as failure:
            rollback_errors = []
            for path in reversed(completed):
                try:
                    if previous[path] is None:path.unlink(missing_ok=True)
                    else:_write(path, previous[path])
                except OSError as error:rollback_errors.append(error)
            if rollback_errors:raise OSError('保存失败，部分文件无法恢复，请检查磁盘后重试') from failure
            raise


def stage_bytes(path, content):
    transaction = _active.get()
    return transaction is not None and transaction.stage(path, content)


@contextmanager
def atomic_writes(root):
    """Publish a related file group after preparation; roll back caught errors."""
    root = Path(root).resolve()
    existing = _active.get()
    if existing is not None:
        if root != existing.root:raise ValueError('不能嵌套不同工作区的配对保存')
        yield
        return
    transaction = _Transaction(root)
    token = _active.set(transaction)
    try:
        yield
    except BaseException:
        raise
    else:
        _active.reset(token)
        token = None
        transaction.commit()
    finally:
        if token is not None:_active.reset(token)
