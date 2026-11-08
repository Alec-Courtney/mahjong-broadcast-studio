"""Independent looping music and single-pass post-match effects.

The bundled assets are ordinary PCM WAVs. Mixing and Windows playback require
only the standard library; ffmpeg is used only when importing other formats.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import wave


ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
ASSET_DIR = ROOT / 'resources' / 'postmatch_audio'
SAMPLE_RATE = 48000
CHANNELS = 2
ENTRY_DURATION = 5.967
TRANSITION_DURATION = 3.0
FADE_OUT = .5
# Reserve headroom for the independently mixed effects at their loudest setting.
# The packaged stereo effects peak below .355; .59 + .355 stays below .95.
MUSIC_HEADROOM_GAIN = .59


@dataclass(frozen=True)
class SoundCue:
    name: str
    time: float
    gain_db: float


def sound_cues(chart_hold=15.):
    """The two exit cues move with the graph's complete-state hold."""
    hold = _duration(chart_hold)
    transition = ENTRY_DURATION + hold
    return (
        SoundCue('title_axes_reveal', .967, -3.),
        SoundCue('graph_reveal', 2., 0.),
        SoundCue('photo_cards_reveal', 4.817, -4.),
        SoundCue('layout_sweep', transition + .120, 0.),
        SoundCue('data_sweep', transition + 2.220, -2.),
    )


def _duration(value):
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 600:
        raise ValueError('停留时长必须在 0–600 秒之间')
    return result


def soundtrack_duration(chart_hold=15., stats_hold=15., fade_out=FADE_OUT):
    return ENTRY_DURATION + _duration(chart_hold) + TRANSITION_DURATION + _duration(stats_hold) + _duration(fade_out)


def _volume(value):
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('音量必须在 0–1 之间')
    return value


def _pcm(path, max_seconds=None):
    with wave.open(str(path), 'rb') as source:
        if source.getparams()[:3] != (CHANNELS, 2, SAMPLE_RATE):
            raise ValueError('音频需要 48kHz、立体声、16bit PCM WAV')
        frames = source.getnframes()
        if max_seconds is not None:
            frames = min(frames, max(0, math.ceil(max_seconds * SAMPLE_RATE)))
        result = array('h')
        result.frombytes(source.readframes(frames))
        if sys.byteorder != 'little':
            result.byteswap()
        return result


def _decode_music(path, seconds):
    """Bound memory use even when the selected music is a long recording."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError('找不到所选背景音乐：' + str(path))
    try:
        result = _pcm(path, seconds)
    except (wave.Error, EOFError, ValueError):
        pass
    else:
        if not result:
            raise ValueError('背景音乐中没有可用的声音')
        return result
    converter = shutil.which('ffmpeg')
    if not converter:
        raise ValueError('此音乐格式需要 ffmpeg；可先选择 48kHz 立体声 PCM WAV')
    command = [converter, '-nostdin', '-hide_banner', '-loglevel', 'error',
               '-i', str(path), '-t', f'{seconds:.6f}', '-vn', '-ac', '2',
               '-ar', str(SAMPLE_RATE), '-f', 's16le', '-']
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=90)
    if process.returncode:
        detail = process.stderr.decode('utf-8', errors='replace').strip()
        raise ValueError('无法读取背景音乐：' + detail[-400:])
    result = array('h')
    result.frombytes(process.stdout)
    if sys.byteorder != 'little':
        result.byteswap()
    if not result:
        raise ValueError('背景音乐中没有可用的声音')
    return result


def _music_cycle(source):
    """Join a clean music source with a short crossfade, without an SE track.

    The tail/head overlap sits at the beginning of the returned cycle. Its file
    boundary therefore joins adjacent source samples and can loop in waveOut.
    """
    frames = len(source)//CHANNELS
    if not frames:
        raise ValueError('背景音乐中没有可用的声音')
    overlap = min(round(.4*SAMPLE_RATE), frames//8)
    if not overlap:
        return source
    result = array('h')
    tail = frames-overlap
    for frame in range(overlap):
        ratio = frame/max(1, overlap-1)
        for channel in range(CHANNELS):
            a = source[(tail+frame)*CHANNELS+channel]
            b = source[frame*CHANNELS+channel]
            result.append(round(a*(1-ratio)+b*ratio))
    result.extend(source[overlap*CHANNELS:tail*CHANNELS])
    return result


def _write_pcm(output, samples):
    output = Path(output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = array('h', samples)
    if sys.byteorder != 'little':
        payload.byteswap()
    temporary = output.with_name(output.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with wave.open(str(temporary), 'wb') as destination:
            destination.setnchannels(CHANNELS)
            destination.setsampwidth(2)
            destination.setframerate(SAMPLE_RATE)
            destination.writeframes(payload.tobytes())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def _add_music(target, source, gain):
    total_frames = len(target) // CHANNELS
    if not source or not gain:
        return
    cycle = _music_cycle(source)
    cycle_frames = len(cycle) // CHANNELS
    offset = 0
    while offset < total_frames:
        available = min(cycle_frames, total_frames-offset)
        for frame in range(available):
            i, j = (offset+frame)*CHANNELS, frame*CHANNELS
            target[i] += cycle[j]*gain/32768.
            target[i+1] += cycle[j+1]*gain/32768.
        offset += cycle_frames


def mix_soundtrack(output_path, chart_hold=15., stats_hold=15., music_path='',
                   music_volume=.35, sfx_volume=.7, music_enabled=True,
                   sfx_enabled=True, fade_out=FADE_OUT, asset_dir=None):
    """Render a stereo WAV; return its absolute Path.

    The complete-state holds are excluded from the entry/transition durations.
    The final half second is an additional audio/visual fade, not part of the
    table's fifteen-second complete-state hold. Peaks are limited by one global
    gain adjustment, preserving the relative music/effect balance.
    """
    seconds = soundtrack_duration(chart_hold, stats_hold, fade_out)
    music_volume, sfx_volume = _volume(music_volume), _volume(sfx_volume)
    assets = Path(asset_dir) if asset_dir is not None else ASSET_DIR
    count = round(seconds*SAMPLE_RATE)
    samples = array('f', [0.]) * (count*CHANNELS)
    if music_enabled and music_volume and str(music_path or '').strip():
        source = _decode_music(music_path, seconds)
        _add_music(samples, source, music_volume*MUSIC_HEADROOM_GAIN)
    if sfx_enabled and sfx_volume:
        for cue in sound_cues(chart_hold):
            source = _pcm(assets/(cue.name+'.wav'))
            first = round(cue.time*SAMPLE_RATE)*CHANNELS
            gain = sfx_volume * 10**(cue.gain_db/20) / 32768.
            for i in range(min(len(source), len(samples)-first)):
                samples[first+i] += source[i]*gain
    # A common short attack and programme fade also cover every optional source.
    attack = min(count, round(.4*SAMPLE_RATE))
    release = min(count, round(float(fade_out)*SAMPLE_RATE))
    for frame in range(attack):
        gain = frame/max(1, attack)
        samples[frame*2] *= gain
        samples[frame*2+1] *= gain
    if release:
        for frame in range(release):
            gain = (release-1-frame)/max(1, release-1)
            i = (count-release+frame)*CHANNELS
            samples[i] *= gain
            samples[i+1] *= gain
    peak = max((abs(value) for value in samples), default=0.)
    scale = min(1., .95/peak) if peak else 1.
    result = array('h', (round(value*scale*32767) for value in samples))
    if sys.byteorder != 'little':
        result.byteswap()
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with wave.open(str(temporary), 'wb') as destination:
            destination.setnchannels(CHANNELS)
            destination.setsampwidth(2)
            destination.setframerate(SAMPLE_RATE)
            destination.writeframes(result.tobytes())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


class _WaveFormat(ctypes.Structure):
    _fields_ = [('wFormatTag', ctypes.c_ushort), ('nChannels', ctypes.c_ushort),
                ('nSamplesPerSec', ctypes.c_uint32), ('nAvgBytesPerSec', ctypes.c_uint32),
                ('nBlockAlign', ctypes.c_ushort), ('wBitsPerSample', ctypes.c_ushort),
                ('cbSize', ctypes.c_ushort)]


class _WaveHeader(ctypes.Structure):
    _fields_ = [('lpData', ctypes.c_void_p), ('dwBufferLength', ctypes.c_uint32),
                ('dwBytesRecorded', ctypes.c_uint32), ('dwUser', ctypes.c_size_t),
                ('dwFlags', ctypes.c_uint32), ('dwLoops', ctypes.c_uint32),
                ('lpNext', ctypes.c_void_p), ('reserved', ctypes.c_size_t)]


class _WaveLoopPlayer:
    """A single prepared PCM buffer loops in winmm, without a polling gap."""
    def __init__(self):
        self._device = None
        self._handle = None
        self._header = None
        self._buffer = None
        self.paused = False

    @property
    def active(self):
        return self._handle is not None

    def _library(self):
        if sys.platform != 'win32':
            raise RuntimeError('当前系统没有可用的背景音乐播放设备')
        if self._device is None:
            device = ctypes.WinDLL('winmm')
            device.waveOutOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint,
                                           ctypes.POINTER(_WaveFormat), ctypes.c_size_t,
                                           ctypes.c_size_t, ctypes.c_uint]
            for name in ('waveOutPrepareHeader', 'waveOutWrite', 'waveOutUnprepareHeader'):
                getattr(device, name).argtypes = [ctypes.c_void_p, ctypes.POINTER(_WaveHeader), ctypes.c_uint]
                getattr(device, name).restype = ctypes.c_uint
            for name in ('waveOutReset', 'waveOutPause', 'waveOutRestart', 'waveOutClose'):
                getattr(device, name).argtypes = [ctypes.c_void_p]
                getattr(device, name).restype = ctypes.c_uint
            device.waveOutOpen.restype = ctypes.c_uint
            device.waveOutGetErrorTextW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
            self._device = device
        return self._device

    def _check(self, result):
        if result:
            detail = ctypes.create_unicode_buffer(512)
            self._device.waveOutGetErrorTextW(result, detail, len(detail))
            raise RuntimeError(detail.value or ('声音设备错误 '+str(result)))

    def play(self, path):
        self.close()
        device = self._library()
        samples = _pcm(path)
        if not samples:
            raise ValueError('背景音乐中没有可用的声音')
        if sys.byteorder != 'little':
            samples.byteswap()
        self._buffer = ctypes.create_string_buffer(samples.tobytes())
        self._header = _WaveHeader(ctypes.cast(self._buffer, ctypes.c_void_p), len(samples)*2,
                                   0, 0, 0, 0, None, 0)
        format_ = _WaveFormat(1, CHANNELS, SAMPLE_RATE, SAMPLE_RATE*CHANNELS*2, CHANNELS*2, 16, 0)
        handle = ctypes.c_void_p()
        self._check(device.waveOutOpen(ctypes.byref(handle), 0xFFFFFFFF, ctypes.byref(format_), 0, 0, 0))
        self._handle = handle
        try:
            self._check(device.waveOutPrepareHeader(handle, ctypes.byref(self._header), ctypes.sizeof(_WaveHeader)))
            self._header.dwFlags |= 0x00000004 | 0x00000008  # WHDR_BEGINLOOP | WHDR_ENDLOOP
            self._header.dwLoops = 0xFFFFFFFF
            self._check(device.waveOutWrite(handle, ctypes.byref(self._header), ctypes.sizeof(_WaveHeader)))
            self.paused = False
        except Exception:
            self.close()
            raise

    def pause(self):
        if self.active and not self.paused:
            self._check(self._device.waveOutPause(self._handle))
            self.paused = True

    def resume(self):
        if self.active and self.paused:
            self._check(self._device.waveOutRestart(self._handle))
            self.paused = False

    def close(self):
        if self._handle is not None:
            self._check(self._device.waveOutReset(self._handle))
            if self._header is not None and self._header.dwFlags & 0x2:
                self._check(self._device.waveOutUnprepareHeader(self._handle, ctypes.byref(self._header), ctypes.sizeof(_WaveHeader)))
            self._check(self._device.waveOutClose(self._handle))
            self._handle = None
        self._buffer = self._header = None
        self.paused = False


class PostMatchAudio:
    """Loop clean music independently; play each scheduled effect only once.

    All operations return a bool except prepare(), which returns Path or None.
    ``error`` contains a user-readable error. Muted playback succeeds quietly.
    prepare() only works with files and is safe for a background worker.
    play/pause/resume/seek/stop/close run on the main thread. seek affects only
    the effect timeline, so advancing the graphic never restarts its music.
    finish_effects() closes the completed effect track while music continues.
    """
    def __init__(self, parent=None, cache_dir=None):
        self.parent = parent
        if cache_dir is None:
            cache_dir = (Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'.cache')))/'MahjongOverlayStudio'/'postmatch_audio'
                         if getattr(sys, 'frozen', False) else ROOT/'.cache'/'postmatch_audio')
        self.cache_dir = Path(cache_dir)
        self.path = None
        self.music_path = None
        self.error = ''
        self.duration = 0.
        self._device = None
        self._alias = 'mahjong_postmatch_' + uuid.uuid4().hex
        self._opened_path = None
        self._paused = 0.
        self._playing = False
        self._effects_finished = False
        self._music_enabled = True
        self._music = _WaveLoopPlayer()
        self._active_music_path = None

    @property
    def music_active(self):
        return self._music.active

    @property
    def music_paused(self):
        return self._music.active and self._music.paused

    def prepare(self, chart_hold=15., stats_hold=15., music_path='', music_volume=.35,
                sfx_volume=.7, music_enabled=True, sfx_enabled=True, *, music_preview_seconds=None):
        self.error = ''
        self.path = None
        self.music_path = None
        self._paused = 0.
        self._effects_finished = False
        music = str(music_path or '').strip()
        self._music_enabled = bool(music_enabled and music)
        try:
            self.duration = soundtrack_duration(chart_hold, stats_hold)
            music_volume, sfx_volume = _volume(music_volume), _volume(sfx_volume)
            preview = _duration(music_preview_seconds) if music_preview_seconds is not None else None
            if preview == 0:raise ValueError('试听时长须大于 0 秒')
            prepare_music = self._music_enabled and music_volume > 0
            if not (prepare_music or sfx_enabled and sfx_volume):
                return None
            fingerprint = None
            if prepare_music:
                candidate = Path(music).expanduser().resolve()
                if not candidate.is_file():
                    raise ValueError('找不到所选背景音乐：' + str(candidate))
                details = candidate.stat()
                fingerprint = (str(candidate), details.st_size, details.st_mtime_ns)
            resources = []
            if sfx_enabled and sfx_volume:
                resources.extend(ASSET_DIR/(cue.name+'.wav') for cue in sound_cues(chart_hold))
            metadata = [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in resources]
            arguments = dict(chart_hold=chart_hold, stats_hold=stats_hold, music_path=music,
                             music_volume=music_volume, sfx_volume=sfx_volume,
                             music_enabled=music_enabled, sfx_enabled=sfx_enabled)
            key = hashlib.sha256(json.dumps([8, arguments, fingerprint, metadata], sort_keys=True).encode()).hexdigest()[:24]
            target = self.cache_dir/(key+'-effects.wav')
            valid = False
            if sfx_enabled and sfx_volume and target.is_file():
                try:
                    with wave.open(str(target), 'rb') as stream:
                        valid = stream.getparams()[:3] == (2, 2, SAMPLE_RATE) and abs(stream.getnframes()/SAMPLE_RATE-self.duration) < 1/SAMPLE_RATE
                except (wave.Error, EOFError):
                    pass
            if sfx_enabled and sfx_volume:
                self.path = target if valid else mix_soundtrack(target, chart_hold=chart_hold, stats_hold=stats_hold,
                                                               music_enabled=False, sfx_enabled=True, sfx_volume=sfx_volume)
            if prepare_music:
                music_args = [6, fingerprint, music_volume]
                if preview is not None:
                    music_args.append(('preview', preview))
                music_key = hashlib.sha256(json.dumps(music_args).encode()).hexdigest()[:24]
                music_target = self.cache_dir/(music_key+'-music.wav')
                if not music_target.is_file():
                    source = _decode_music(music, preview or 600.)
                    cycle = source if preview is not None else _music_cycle(source)
                    limited = array('h', (round(sample*music_volume*MUSIC_HEADROOM_GAIN) for sample in cycle))
                    _write_pcm(music_target, limited)
                self.music_path = music_target
            return self.path or self.music_path
        except Exception as error:
            self.error = '声音准备失败：' + str(error)
            return None

    def _command(self, value):
        if sys.platform != 'win32':
            raise RuntimeError('当前系统没有可用的声音播放设备')
        if self._device is None:
            self._device = ctypes.WinDLL('winmm')
            self._device.mciSendStringW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
            self._device.mciSendStringW.restype = ctypes.c_uint
            self._device.mciGetErrorStringW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
        response = ctypes.create_unicode_buffer(512)
        result = self._device.mciSendStringW(value, response, len(response), None)
        if result:
            detail = ctypes.create_unicode_buffer(512)
            self._device.mciGetErrorStringW(result, detail, len(detail))
            raise RuntimeError(detail.value or ('声音设备错误 '+str(result)))
        return response.value

    def _open(self):
        if self._opened_path == self.path:
            return
        if self._opened_path is not None:
            self._command(f'close {self._alias}')
            self._opened_path = None
        self._command(f'open "{self.path}" type waveaudio alias {self._alias}')
        self._opened_path = self.path
        self._command(f'set {self._alias} time format milliseconds')

    def _play_effects(self, position):
        if self.path is None or self._effects_finished:
            return
        self._open()
        self._paused = max(0., min(float(position), self.duration))
        self._command(f'play {self._alias} from {round(self._paused*1000)}')
        self._playing = True

    def play(self, position=0., preserve_music=True):
        self.error = ''
        try:
            if self._music_enabled and self.music_path is not None:
                if not self._music.active or not preserve_music or self._active_music_path != self.music_path:
                    self._music.play(self.music_path)
                    self._active_music_path = self.music_path
                elif self._music.paused:
                    self._music.resume()
            elif self._music.active:
                self._music.close()
                self._active_music_path = None
            self._effects_finished = False
            self._play_effects(position)
            return True
        except Exception as error:
            self.error = '声音播放失败：' + str(error)
            self._playing = False
            return False

    def pause(self):
        self.error = ''
        try:
            self._music.pause()
            if self.path is not None and self._opened_path is not None and self._playing:
                self._command(f'pause {self._alias}')
                self._paused = float(self._command(f'status {self._alias} position'))/1000
                self._playing = False
            return True
        except Exception as error:
            self.error = '声音暂停失败：' + str(error)
            return False

    def resume(self):
        self.error = ''
        try:
            self._music.resume()
            if not self._effects_finished:
                self._play_effects(self._paused)
            return True
        except Exception as error:
            self.error = '声音继续失败：' + str(error)
            return False

    def seek(self, position):
        self.error = ''
        self._paused = max(0., min(float(position), self.duration))
        try:
            if self._playing:
                self._play_effects(self._paused)
            return True
        except Exception as error:
            self.error = '音效跳转失败：' + str(error)
            return False

    def finish_effects(self):
        """End only the one-shot effects; the clean music device keeps looping."""
        self._paused = 0.
        self._playing = False
        self._effects_finished = True
        if self._opened_path is None:
            return True
        try:
            self._command(f'close {self._alias}')
            self._opened_path = None
            return True
        except Exception as error:
            self.error = '音效停止失败：' + str(error)
            return False

    def stop_music(self):
        self._music_enabled = False
        try:
            self._music.close()
            self._active_music_path = None
            return True
        except Exception as error:
            self.error = '背景音乐停止失败：' + str(error)
            return False

    def set_music_enabled(self, enabled):
        self.error = ''
        self._music_enabled = bool(enabled)
        if not enabled:
            return self.stop_music()
        try:
            if self.music_path is None:
                raise ValueError('请重新播放以载入背景音乐')
            if self._music.active:
                self._music.resume()
            else:
                self._music.play(self.music_path)
                self._active_music_path = self.music_path
            return True
        except Exception as error:
            self.error = '背景音乐播放失败：' + str(error)
            return False

    def stop(self):
        effects_ok = self.finish_effects()
        enabled = self._music_enabled
        music_ok = self.stop_music()
        self._music_enabled = enabled
        return effects_ok and music_ok

    def close(self):
        return self.stop()
