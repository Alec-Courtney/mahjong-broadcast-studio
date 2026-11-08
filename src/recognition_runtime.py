"""Availability checks that never load optional native recognition packages."""
from importlib import util
from pathlib import Path
import sys


MISSING_RECOGNITION = '未安装牌面识别组件。手动导播可正常使用。可运行 INSTALL-Automation.cmd 安装。'


def _installed(name: str) -> bool:
    try:
        spec = util.find_spec(name)
        return bool(spec and spec.origin and Path(spec.origin).is_file())
    except (ImportError, ValueError, OSError):
        return False


def recognition_available() -> bool:
    """Check package files, including after removal from an active environment."""
    return all(_installed(name) for name in ('numpy', 'cv2', 'onnxruntime'))


def _spout_unavailable_reason() -> str:
    if sys.platform != 'win32':
        return 'OBS Spout 仅支持 Windows。可选择摄像头或本地文件。'
    if not _installed('SpoutGL'):
        return '未安装 OBS Spout 接收组件。可运行 INSTALL-Automation.cmd 安装。'
    return ''


def recognition_unavailable_reason(kind: str) -> str:
    if not recognition_available():
        return MISSING_RECOGNITION
    return _spout_unavailable_reason() if kind == 'spout' else ''
