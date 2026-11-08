"""Isolated OBS + official Spout filter acceptance (no installed profile writes).

Prepare copies a minimal portable runtime into .cache/obs-integration. Start
that executable hidden with --portable, then use --verify. Optional diagnostic
dependency: websocket-client. The test only creates image sources; no devices,
recording or network streaming are started.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import socket
import sys
import threading
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from app_paths import LOCAL_REFERENCE_RESOURCES
from download_obs_plugin import PORTABLE_FILE, PORTABLE_SHA256

CACHE = ROOT / '.cache' / 'obs-integration'
OUT = ROOT / 'previews' / 'checks' / 'obs-spout'
DEFAULT_OBS = Path(r'C:\Program Files (x86)\Steam\steamapps\common\OBS Studio')
PLUGIN = LOCAL_REFERENCE_RESOURCES / 'obs-spout' / PORTABLE_FILE


def prepare(installed):
    assert (installed / 'bin/64bit/obs64.exe').is_file()
    if not PLUGIN.is_file() or hashlib.sha256(PLUGIN.read_bytes()).hexdigest() != PORTABLE_SHA256:
        raise FileNotFoundError('缺少已校验的本地 Spout ZIP，请运行 scripts/download_obs_plugin.py --portable')
    for path in ('bin/64bit', 'data/libobs', 'data/obs-studio'):
        shutil.copytree(installed / path, CACHE / path, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('*.pdb', '*.log'))
    for module in ('image-source', 'obs-filters', 'obs-transitions', 'obs-websocket',
                   'obs-outputs', 'obs-x264', 'obs-ffmpeg', 'rtmp-services'):
        target = CACHE / 'obs-plugins/64bit' / (module + '.dll')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(installed / 'obs-plugins/64bit' / (module + '.dll'), target)
        shutil.copytree(installed / 'data/obs-plugins' / module,
                        CACHE / 'data/obs-plugins' / module, dirs_exist_ok=True)
    with zipfile.ZipFile(PLUGIN) as archive:
        for item in archive.infolist():
            target = (CACHE / item.filename).resolve()
            if not target.is_relative_to(CACHE.resolve()):
                raise ValueError('Unsafe archive path')
            if not item.is_dir() and not item.filename.endswith('.pdb'):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))
    (CACHE / 'portable_mode.txt').write_text('', encoding='utf-8')
    (CACHE / 'disable_updater').write_text('', encoding='utf-8')
    config = CACHE / 'config/obs-studio'
    config.mkdir(parents=True, exist_ok=True)
    (config / 'global.ini').write_text('[General]\nLanguage=en-US\nLicenseAccepted=true\nFirstRun=false\n', encoding='utf-8')
    (config / 'user.ini').write_text('''[General]
LicenseAccepted=true
FirstRun=false
ConfirmOnExit=false
[Basic]
Profile=Acceptance
ProfileDir=Acceptance
SceneCollection=Acceptance
SceneCollectionFile=Acceptance
[BasicWindow]
SysTrayEnabled=false
WarnBeforeStartingStream=true
WarnBeforeStoppingStream=true
PreviewEnabled=true
[win_spout]
auto_start=false
continuous_broadcast=true
spout_output_name=AcceptanceUnusedProgram
''', encoding='utf-8')
    profile = config / 'basic/profiles/Acceptance'
    profile.mkdir(parents=True, exist_ok=True)
    (profile / 'basic.ini').write_text('''[General]
Name=Acceptance
[Video]
BaseCX=1280
BaseCY=720
OutputCX=1280
OutputCY=720
FPSType=0
FPSCommon=30
[Audio]
SampleRate=48000
ChannelSetup=Stereo
[Output]
Mode=Simple
[SimpleOutput]
StreamEncoder=x264
VBitrate=2500
ABitrate=160
''', encoding='utf-8')
    scenes = config / 'basic/scenes'
    scenes.mkdir(parents=True, exist_ok=True)
    (scenes / 'Acceptance.json').write_text(json.dumps({
        'name': 'Acceptance', 'current_scene': 'Blank', 'current_program_scene': 'Blank',
        'scene_order': [{'name': 'Blank'}], 'sources': [
            {'name': 'Blank', 'uuid': str(uuid.uuid4()), 'id': 'scene', 'versioned_id': 'scene',
             'settings': {'items': []}}], 'groups': [], 'transitions': [],
        'current_transition': 'Cut', 'transition_duration': 0,
        'DesktopAudioDevice1': None, 'DesktopAudioDevice2': None,
        'AuxAudioDevice1': None, 'AuxAudioDevice2': None, 'AuxAudioDevice3': None,
    }), encoding='utf-8')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    access = dict(port=port, password=secrets.token_urlsafe(32))
    (CACHE / 'access.json').write_text(json.dumps(access), encoding='utf-8')
    wsconfig = config / 'plugin_config/obs-websocket'
    wsconfig.mkdir(parents=True, exist_ok=True)
    (wsconfig / 'config.json').write_text(json.dumps({
        'first_load': False, 'server_enabled': True, 'server_port': port,
        'alerts_enabled': False, 'auth_required': True, 'server_password': access['password'],
    }), encoding='utf-8')
    print('Portable OBS prepared inside project cache; installed OBS unchanged.')


class RPC:
    def __init__(self):
        import websocket
        access = json.loads((CACHE / 'access.json').read_text(encoding='utf-8'))
        self.socket = websocket.create_connection(f'ws://127.0.0.1:{access["port"]}', timeout=5,
                                                 http_no_proxy=['127.0.0.1', 'localhost'])
        hello = json.loads(self.socket.recv())['d']
        auth = hello.get('authentication')
        identify = {'rpcVersion': 1, 'eventSubscriptions': 0}
        if auth:
            secret = base64.b64encode(hashlib.sha256((access['password'] + auth['salt']).encode()).digest()).decode()
            identify['authentication'] = base64.b64encode(hashlib.sha256((secret + auth['challenge']).encode()).digest()).decode()
        self.socket.send(json.dumps({'op': 1, 'd': identify}))
        assert json.loads(self.socket.recv())['op'] == 2
        self.index = 0

    def call(self, kind, **data):
        self.index += 1
        identity = str(self.index)
        self.socket.send(json.dumps({'op': 6, 'd': {'requestType': kind, 'requestId': identity, 'requestData': data}}))
        while True:
            response = json.loads(self.socket.recv())
            if response['op'] == 7 and response['d']['requestId'] == identity:
                value = response['d']
                if not value['requestStatus']['result']:
                    raise RuntimeError(f'{kind}: {value["requestStatus"]}')
                return value.get('responseData', {})

    def close(self):
        self.socket.close()


def verify():
    from spout_capture import SpoutCapture, enumerate_senders
    from tile_recognition import OnnxTileRecognizer, MEDIUM_MODEL, write_image
    OUT.mkdir(parents=True, exist_ok=True)
    rpc = RPC()
    ready_deadline = time.monotonic() + 20
    while True:
        try:
            version = rpc.call('GetVersion')
            break
        except RuntimeError as error:
            if '207' not in str(error) or time.monotonic() > ready_deadline:
                raise
            time.sleep(.25)
    names = [f'OBS_Actual_Position_{i + 1}' for i in range(4)]
    scenes = [f'Camera {i + 1}' for i in range(4)]
    sources = [f'Constructed hand {i + 1}' for i in range(4)]
    report = dict(actual_obs_tested=True, physical_camera_tested=False,
                  installed_obs_configuration_changed=False,
                  obs_version=version['obsVersion'], plugin_version='1.13.0',
                  image_source_kind='constructed photo crop arrangements',
                  continuous_filter_broadcast=True, native_spout_receiver=True)
    stop = threading.Event()
    counts = Counter()
    results = {}
    errors = []
    threads = []
    captures = {}
    def receive(seat):
        try:
            with SpoutCapture(names[seat]) as capture:
                while not stop.is_set():
                    okay, frame = capture.read()
                    if okay:
                        counts[seat] += 1
                        captures[seat] = frame
        except Exception as error:
            errors.append(str(error))
    def wait(predicate, timeout=20):
        end = time.monotonic() + timeout
        while not predicate():
            if errors: raise AssertionError(errors)
            if time.monotonic() > end: raise TimeoutError(f'OBS Spout wait: counts={dict(counts)}, senders={enumerate_senders()}')
            time.sleep(.03)
    try:
        for seat in range(4):
            rpc.call('CreateScene', sceneName=scenes[seat])
            image = ROOT / 'previews/checks/spout' / f'constructed-position-{seat + 1}.png'
            assert image.is_file(), 'Run verify_spout_integration.py first'
            rpc.call('CreateInput', sceneName=scenes[seat], inputName=sources[seat], inputKind='image_source',
                     inputSettings={'file': str(image), 'unload': False}, sceneItemEnabled=True)
            rpc.call('CreateSourceFilter', sourceName=sources[seat], filterName='Fixed position output',
                     filterKind='win_spout_filter', filterSettings={'spout_filter_name': names[seat]})
            thread = threading.Thread(target=receive, args=(seat,), daemon=True)
            threads.append(thread)
            thread.start()
        wait(lambda: all(counts[seat] >= 3 for seat in range(4)))
        report['cold_hidden_sources_receive_without_being_program'] = True
        switches = []
        for scene in ('Camera 4', 'Blank', 'Camera 2', 'Camera 1'):
            before = counts.copy()
            rpc.call('SetCurrentProgramScene', sceneName=scene)
            wait(lambda: all(counts[seat] >= before[seat] + 5 for seat in range(4)))
            switches.append({'program': rpc.call('GetCurrentProgramScene')['currentProgramSceneName'], 'received_frames': dict(counts)})
        report['program_scene_switches'] = switches
        report['all_four_sources_continued'] = True
        detector = OnnxTileRecognizer(MEDIUM_MODEL)
        from hand_calculation import analyse_hand
        waits = ['1m', '3m', '2s', '5s']
        for seat in range(4):
            frame = captures[seat].copy()
            result = detector.recognize(frame)
            found = analyse_hand(result.tiles)
            assert found['tenpai'] and found['waits'] == [waits[seat]], (seat, result.tiles, found)
            write_image(OUT / f'actual-obs-source-{seat + 1}.png', frame)
            results[str(seat + 1)] = {'tiles': result.tiles, 'waits': found['waits'], 'inference_ms': result.elapsed_ms}
        report['actual_obs_frames_onnx_waits'] = results
        screenshot = rpc.call('GetSourceScreenshot', sourceName='Camera 1', imageFormat='png', imageWidth=1280, imageHeight=720)
        (OUT / 'actual-obs-program.png').write_bytes(base64.b64decode(screenshot['imageData'].split(',', 1)[1]))
        # Disabling one filter must not silently switch its fixed receiver.
        rpc.call('SetSourceFilterEnabled', sourceName=sources[0], filterName='Fixed position output', filterEnabled=False)
        time.sleep(.35)
        paused = counts[0]
        time.sleep(.4)
        assert counts[0] == paused
        other = counts.copy()
        wait(lambda: all(counts[seat] > other[seat] for seat in (1, 2, 3)))
        rpc.call('SetSourceFilterEnabled', sourceName=sources[0], filterName='Fixed position output', filterEnabled=True)
        wait(lambda: counts[0] > paused + 3)
        report['filter_pause_does_not_reuse_old_frames_or_affect_other_seats'] = True
        report['filter_reenable_recovers'] = True
        report['obs_stats'] = rpc.call('GetStats')
        (OUT / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        stop.set()
        for thread in threads:
            thread.join(4)
        rpc.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--prepare', action='store_true')
    action.add_argument('--verify', action='store_true')
    parser.add_argument('--installed-obs', type=Path, default=DEFAULT_OBS)
    args = parser.parse_args()
    prepare(args.installed_obs) if args.prepare else verify()
