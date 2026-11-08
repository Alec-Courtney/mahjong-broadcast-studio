"""Export a new OBS scene collection with alpha-capable window capture."""
import ctypes
from pathlib import Path
import sys
import uuid

from capture_surface import capture_transform
from session_repository import atomic_json


def window_identity(window):
    if sys.platform != 'win32':raise ValueError('请在 Windows 桌面中导出 OBS 场景')
    name = ctypes.create_unicode_buffer(512)
    get_class = ctypes.windll.user32.GetClassNameW
    get_class.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    if not get_class(int(window.winId()), name, len(name)):
        raise ValueError('窗口尚未建立，请先显示主导播台再导出')
    def encode(text):return text.replace('#', '#22').replace(':', '#3A')
    return ':'.join(encode(value) for value in (window.windowTitle(), name.value, Path(sys.executable).name))


def scene_collection(manager, identity=window_identity):
    """Export the shared programme and player surfaces plus independent modules."""
    sources, items = [], []
    for index, (key, window) in enumerate(manager.capture_windows.items(), 1):
        name = window.windowTitle().replace('麻将UI · ', '')
        source_id = str(uuid.uuid4())
        sources.append(dict(name=name, uuid=source_id, id='window_capture', versioned_id='window_capture',
            settings=dict(window=identity(window), method=2, priority=1, cursor=False, client_area=True, force_sdr=True)))
        transform = capture_transform(window)
        items.append(dict(name=name, source_uuid=source_id, id=index, visible=True, locked=False, rot=0.,
            pos=transform['pos'], scale=transform['scale'], scale_filter='lanczos', align=5, bounds_type=0,
            bounds=dict(x=0., y=0.), crop_left=0, crop_top=0, crop_right=0, crop_bottom=0))
    scene_name = '麻将直播'
    sources.append(dict(name=scene_name, uuid=str(uuid.uuid4()), id='scene', versioned_id='scene',
        settings=dict(items=items, custom_size=False)))
    return dict(name='麻将直播 · 透明窗口', current_scene=scene_name, current_program_scene=scene_name,
        scene_order=[dict(name=scene_name)], sources=sources, groups=[], transitions=[],
        current_transition='Cut', transition_duration=0, base_res_cx=1920, base_res_cy=1080,
        DesktopAudioDevice1=None, DesktopAudioDevice2=None, AuxAudioDevice1=None, AuxAudioDevice2=None)


def export_scene_collection(manager, destination):
    atomic_json(Path(destination), scene_collection(manager))
