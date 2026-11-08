"""Read-only Windows capture-device inventory and stable named-camera resolution.

DirectShow monikers expose names without creating capture filters or opening
streams. OpenCV is imported only when the caller explicitly opens a camera.
Primary references:
https://learn.microsoft.com/en-us/windows/win32/directshow/selecting-a-capture-device
https://docs.opencv.org/4.x/d0/da7/videoio_overview.html
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import shutil
import subprocess
import sys
import uuid


class CameraDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class CameraDevice:
    index: int
    name: str
    identifier: str
    backend: str = 'dshow'

    def to_dict(self):
        return asdict(self)

    @property
    def is_virtual(self):
        value = self.name.casefold()
        return 'virtual' in value or '虚拟' in value


def _enumerate_directshow():
    """Call ICreateDevEnum/IPropertyBag through ctypes; no optional COM package."""
    if sys.platform != 'win32':
        return []
    import ctypes

    HRESULT = ctypes.c_int32
    ULONG = ctypes.c_uint32
    LPVOID = ctypes.c_void_p

    class GUID(ctypes.Structure):
        _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16),
                    ('Data3', ctypes.c_uint16), ('Data4', ctypes.c_ubyte * 8)]

    def guid(value):
        return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)

    class Record(ctypes.Structure):
        _fields_ = [('record', LPVOID), ('info', LPVOID)]

    class VariantData(ctypes.Union):
        _fields_ = [('bstrVal', LPVOID), ('llVal', ctypes.c_int64), ('record', Record)]

    class VARIANT(ctypes.Structure):
        _fields_ = [('vt', ctypes.c_uint16), ('reserved1', ctypes.c_uint16),
                    ('reserved2', ctypes.c_uint16), ('reserved3', ctypes.c_uint16),
                    ('data', VariantData)]

    ole32 = ctypes.WinDLL('ole32')
    oleaut32 = ctypes.WinDLL('oleaut32')
    ole32.CoInitializeEx.argtypes = [LPVOID, ULONG]
    ole32.CoInitializeEx.restype = HRESULT
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), LPVOID, ULONG,
                                      ctypes.POINTER(GUID), ctypes.POINTER(LPVOID)]
    ole32.CoCreateInstance.restype = HRESULT
    ole32.CreateBindCtx.argtypes = [ULONG, ctypes.POINTER(LPVOID)]
    ole32.CreateBindCtx.restype = HRESULT
    ole32.CoTaskMemFree.argtypes = [LPVOID]
    ole32.CoTaskMemFree.restype = None
    oleaut32.VariantClear.argtypes = [ctypes.POINTER(VARIANT)]
    oleaut32.VariantClear.restype = HRESULT

    def method(pointer, position, result_type, *argument_types):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(LPVOID))).contents
        return ctypes.WINFUNCTYPE(result_type, LPVOID, *argument_types)(table[position])

    def release(pointer):
        if pointer:
            method(pointer, 2, ULONG)(pointer)

    def property_text(bag, name):
        value = VARIANT()
        try:
            read = method(bag, 3, HRESULT, ctypes.c_wchar_p, ctypes.POINTER(VARIANT), LPVOID)
            hr = read(bag, name, ctypes.byref(value), None)
            if hr >= 0 and value.vt == 8 and value.data.bstrVal:
                return ctypes.wstring_at(value.data.bstrVal)
            return ''
        finally:
            oleaut32.VariantClear(ctypes.byref(value))

    def display_name(moniker):
        context, value = LPVOID(), LPVOID()
        try:
            if ole32.CreateBindCtx(0, ctypes.byref(context)) < 0:
                return ''
            call = method(moniker, 20, HRESULT, LPVOID, LPVOID, ctypes.POINTER(LPVOID))
            if call(moniker, context, None, ctypes.byref(value)) >= 0 and value:
                return ctypes.wstring_at(value)
            return ''
        finally:
            if value:
                ole32.CoTaskMemFree(value)
            release(context)

    hr = ole32.CoInitializeEx(None, 0)
    initialized = hr in (0, 1)
    # The calling Qt GUI thread may already be in an STA. Its existing COM
    # apartment remains usable; do not uninitialize an apartment we did not own.
    if hr < 0 and (hr & 0xffffffff) != 0x80010106:
        raise CameraDiscoveryError(f'无法读取摄像头列表（COM 0x{hr & 0xffffffff:08X}）')
    dev_enum, enumeration = LPVOID(), LPVOID()
    try:
        class_id = guid('62be5d10-60eb-11d0-bd3b-00a0c911ce86')
        enum_iid = guid('29840822-5b84-11d0-bd3b-00a0c911ce86')
        hr = ole32.CoCreateInstance(ctypes.byref(class_id), None, 1,
                                   ctypes.byref(enum_iid), ctypes.byref(dev_enum))
        if hr < 0:
            raise CameraDiscoveryError(f'无法读取摄像头列表（设备枚举 0x{hr & 0xffffffff:08X}）')
        category = guid('860bb310-5d01-11d0-bd3b-00a0c911ce86')
        create = method(dev_enum, 3, HRESULT, ctypes.POINTER(GUID), ctypes.POINTER(LPVOID), ULONG)
        hr = create(dev_enum, ctypes.byref(category), ctypes.byref(enumeration), 0)
        if hr == 1:
            return []
        if hr < 0 or not enumeration:
            raise CameraDiscoveryError(f'无法读取摄像头列表（视频类别 0x{hr & 0xffffffff:08X}）')
        bag_iid = guid('55272a00-42cb-11ce-8135-00aa004bb851')
        next_item = method(enumeration, 3, HRESULT, ULONG, ctypes.POINTER(LPVOID), ctypes.POINTER(ULONG))
        devices = []
        ordinal = 0
        while True:
            moniker, bag, fetched = LPVOID(), LPVOID(), ULONG()
            hr = next_item(enumeration, 1, ctypes.byref(moniker), ctypes.byref(fetched))
            if hr == 1:
                break
            if hr < 0 or not moniker:
                raise CameraDiscoveryError('读取摄像头列表时设备发生变化，请刷新重试')
            try:
                bind = method(moniker, 9, HRESULT, LPVOID, LPVOID,
                              ctypes.POINTER(GUID), ctypes.POINTER(LPVOID))
                name, identifier = '', ''
                if bind(moniker, None, None, ctypes.byref(bag_iid), ctypes.byref(bag)) >= 0 and bag:
                    name = property_text(bag, 'FriendlyName') or property_text(bag, 'Description')
                    identifier = property_text(bag, 'DevicePath')
                identifier = identifier or display_name(moniker)
                name = name or f'视频设备 {ordinal + 1}'
                identifier = identifier or 'dshow-name:' + name.casefold()
                devices.append(CameraDevice(ordinal, name, identifier))
            finally:
                release(bag)
                release(moniker)
                ordinal += 1
        return devices
    finally:
        release(enumeration)
        release(dev_enum)
        if initialized:
            ole32.CoUninitialize()


def _parse_ffmpeg_devices(output):
    devices = []
    current = None
    for line in output.splitlines():
        entry = re.search(r'"(.+)"\s+\((video|none)\)\s*$', line)
        if entry:
            current = len(devices)
            devices.append(CameraDevice(current, entry.group(1), 'dshow-name:' + entry.group(1).casefold()))
            continue
        if re.search(r'".+"\s+\(audio\)\s*$', line):
            current = None
        alternate = re.search(r'Alternative name "(.+)"\s*$', line)
        if alternate and current is not None:
            previous = devices[current]
            devices[current] = CameraDevice(previous.index, previous.name, alternate.group(1))
    return devices


def _enumerate_ffmpeg():
    executable = shutil.which('ffmpeg')
    if not executable:
        raise CameraDiscoveryError('无法读取摄像头列表，请检查视频设备驱动并刷新')
    try:
        result = subprocess.run([executable, '-hide_banner', '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CameraDiscoveryError('读取摄像头列表超时或失败，请稍后刷新') from error
    output = result.stderr + '\n' + result.stdout
    devices = _parse_ffmpeg_devices(output)
    if not devices and not re.search(r'(none found|Could not enumerate video)', output, re.I):
        raise CameraDiscoveryError('无法读取摄像头列表，请检查视频设备驱动并刷新')
    return devices


def enumerate_cameras():
    """Return registered video devices. Listing never starts a camera stream."""
    if sys.platform != 'win32':
        return []
    try:
        return _enumerate_directshow()
    except (OSError, CameraDiscoveryError) as native_error:
        try:
            return _enumerate_ffmpeg()
        except CameraDiscoveryError as error:
            raise CameraDiscoveryError(str(error)) from native_error


def resolve_camera(source, devices=None):
    """Resolve a saved stable identifier to today's DirectShow ordinal.

    Integer input remains a legacy ordinal. A saved named source never silently
    falls back to another device merely because its old ordinal is still valid.
    """
    friendly_only = isinstance(source, str)
    if isinstance(source, CameraDevice):
        source = source.to_dict()
    if type(source) is int:
        if source < 0:
            raise ValueError('无效的摄像头编号')
        if devices is not None:
            match = next((device for device in devices if device.index == source), None)
            if match:
                return match
        return CameraDevice(source, f'摄像头 {source}', '', 'dshow' if sys.platform == 'win32' else 'any')
    if isinstance(source, str):
        if source.isdecimal():
            return resolve_camera(int(source), devices)
        source = {'identifier': source, 'name': source}
    if not isinstance(source, dict):
        raise ValueError('无效的摄像头来源')
    identifier = source.get('identifier') or source.get('device_id') or ''
    name = source.get('name') or source.get('device_name') or ''
    index = source.get('index')
    if not isinstance(identifier, str) or not isinstance(name, str):
        raise ValueError('摄像头名称或标识格式无效')
    if not identifier and not name:
        return resolve_camera(index, devices)
    available = list(enumerate_cameras() if devices is None else devices)
    if identifier:
        matches = [device for device in available if device.identifier.casefold() == identifier.casefold()]
        if not matches:
            # A plain friendly-name string is convenient for scripts, but a
            # saved mapping containing a real identifier must match that ID.
            if friendly_only:
                matches = [device for device in available if device.name.casefold() == name.casefold()]
        if not matches:
            raise CameraDiscoveryError('未找到已选择的摄像头“' + (name or identifier) + '”，请连接设备或刷新后重新选择')
    else:
        matches = [device for device in available if device.name.casefold() == name.casefold()]
    if len(matches) != 1:
        raise CameraDiscoveryError('摄像头名称不唯一或设备已断开，请从设备列表重新选择')
    return matches[0]


def open_camera(source, *, cv2_module=None, devices=None):
    """Explicitly open one selected stream; return (capture, resolved_device)."""
    device = resolve_camera(source, devices)
    if cv2_module is None:
        import cv2 as cv2_module
    backend = getattr(cv2_module, 'CAP_DSHOW', 700) if device.backend == 'dshow' else getattr(cv2_module, 'CAP_ANY', 0)
    capture = None
    try:
        capture = cv2_module.VideoCapture(device.index, backend)
        if not capture.isOpened():
            raise RuntimeError('设备未返回可用视频')
        # Best effort only; capture backends may not support buffer control.
        try:
            capture.set(getattr(cv2_module, 'CAP_PROP_BUFFERSIZE', 38), 1)
        except Exception:
            pass
        return capture, device
    except Exception as error:
        if capture is not None:
            capture.release()
        hint = '；如使用 OBS，请先启动 OBS 虚拟摄像头' if 'obs' in device.name.casefold() else ''
        raise RuntimeError('无法打开“' + device.name + '”，请检查连接、相机权限及是否被其他程序独占' + hint) from error


# Descriptive aliases for callers which prefer a discovery/opening vocabulary.
list_camera_devices = enumerate_cameras
capture_device = open_camera
