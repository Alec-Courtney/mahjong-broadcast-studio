"""Media inspection helpers used by the current ML programme mixer."""
from array import array
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import postmatch_audio as audio
RATE = audio.SAMPLE_RATE
CHANNELS = audio.CHANNELS


def run(command, timeout=180):
    result = subprocess.run([str(x) for x in command], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        detail = result.stderr.decode('utf-8', errors='replace')
        raise RuntimeError(f'{Path(str(command[0])).name} failed: {detail[-3000:]}')
    return result


def file_sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def pcm_info(path):
    with wave.open(str(path), 'rb') as source:
        if source.getparams()[:3] != (2, 2, RATE):
            raise ValueError('Audio must be 48 kHz stereo PCM16 WAV.')
        return dict(frames=source.getnframes(), sample_rate=RATE, channels=2,
                    sample_width=2, duration_seconds=source.getnframes()/RATE,
                    sha256=file_sha(path))


def read_pcm(path):
    pcm_info(path)
    with wave.open(str(path), 'rb') as source:
        samples = array('h')
        samples.frombytes(source.readframes(source.getnframes()))
    if sys.byteorder != 'little':
        samples.byteswap()
    return samples


def inspect_music_join(path, source_path, volume):
    loop = read_pcm(path)
    original = audio._decode_music(source_path, 600.)
    boundary = len(loop)
    actual = [loop[c]-loop[-CHANNELS+c] for c in range(CHANNELS)]
    gain = float(volume)*audio.MUSIC_HEADROOM_GAIN
    head = boundary if boundary < len(original) else 0
    reference = [round(original[head+c]*gain)-round(original[boundary-CHANNELS+c]*gain)
                 for c in range(CHANNELS)]
    if actual != reference:
        raise RuntimeError('The prepared loop boundary differs from the expected source samples.')
    return dict(cycle_seconds=boundary/(RATE*CHANNELS),
                crossfade_seconds=(len(original)-boundary)/(RATE*CHANNELS),
                join_step_pcm_lsb=actual, source_step_pcm_lsb=reference)


def probe_video(path, ffprobe):
    data = json.loads(run([ffprobe, '-v', 'error', '-show_streams', '-show_format',
                           '-of', 'json', path]).stdout)
    videos = [s for s in data['streams'] if s['codec_type']=='video']
    if len(videos) != 1:
        raise ValueError('Expected exactly one video stream.')
    stream = videos[0]
    if stream.get('duration_ts') is not None and stream.get('time_base'):
        duration = float(int(stream['duration_ts'])*Fraction(stream['time_base']))
    else:
        duration = float(stream.get('duration', data['format']['duration']))
    fps = float(Fraction(stream.get('avg_frame_rate') or stream['r_frame_rate']))
    return data, duration, fps


def video_stream_hash(path, ffmpeg):
    return run([ffmpeg, '-nostdin', '-v', 'error', '-i', path, '-map', '0:v:0',
                '-c:v', 'copy', '-f', 'hash', '-hash', 'sha256', '-']).stdout.decode().strip()


def encoded_audio_metrics(path, ffmpeg):
    result = run([ffmpeg, '-nostdin', '-hide_banner', '-i', path, '-map', '0:a:0',
                  '-af', 'astats=metadata=0:reset=0', '-f', 'null', '-'])
    log = result.stderr.decode('utf-8', errors='replace')
    def last_number(label):
        matches = re.findall(re.escape(label)+r':\s*([-+\d.einf]+)', log)
        return float(matches[-1]) if matches else None
    peak = last_number('Peak level dB')
    rms = last_number('RMS level dB')
    if peak is None or not math.isfinite(peak) or peak >= 0:
        raise RuntimeError('AAC decode has no measurable safe headroom.')
    return dict(peak_dbfs=peak, rms_dbfs=rms,
                nan_samples=last_number('Number of NaNs'),
                infinite_samples=last_number('Number of Infs'))
