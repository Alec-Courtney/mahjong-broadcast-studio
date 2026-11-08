"""One declaration sound for each actual FIFO start.

The existing post-match one-shot transport supplies the Windows device layer.
This instance has no music path, so it cannot start or restart background music.
"""
from __future__ import annotations

from array import array
import hashlib
import math
import os
from pathlib import Path
import sys
import uuid
import wave

from postmatch_audio import PostMatchAudio

ROOT=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))
RIICHI_SOUND_PATH=ROOT/'resources/riichi_audio/riichi_declare.wav'
RIICHI_SOUND_DURATION=2.5


def _cache_root():
    if getattr(sys,'frozen',False):
        return Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.cache')))/'MahjongOverlayStudio'/'postmatch_audio'/'riichi'
    return ROOT/'.cache/postmatch_audio/riichi'


def prepare_riichi_sound(volume=.7,cache_dir=None):
    """Return a quiet, immutable PCM cue; rendering this opens no audio device."""
    if isinstance(volume,bool) or not isinstance(volume,(int,float)) or not math.isfinite(volume) or not 0<=volume<=1:
        raise ValueError('立直音效音量须为0到1之间的数字')
    with wave.open(str(RIICHI_SOUND_PATH),'rb') as source:
        channels,width,rate,frames=source.getnchannels(),source.getsampwidth(),source.getframerate(),source.getnframes()
        raw=source.readframes(frames)
    if (channels,width,rate)!=(2,2,48000) or frames!=round(RIICHI_SOUND_DURATION*rate):
        raise ValueError('立直音效格式无效')
    key=hashlib.sha256(raw+repr(float(volume)).encode('ascii')).hexdigest()
    folder=Path(cache_dir) if cache_dir is not None else _cache_root()
    path=folder/('riichi_'+key+'.wav')
    if path.is_file():return path
    samples=array('h');samples.frombytes(raw)
    if sys.byteorder!='little':samples.byteswap()
    scaled=array('h',(round(sample*volume) for sample in samples))
    if sys.byteorder!='little':scaled.byteswap()
    folder.mkdir(parents=True,exist_ok=True);temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with wave.open(str(temporary),'wb') as target:
            target.setnchannels(channels);target.setsampwidth(width);target.setframerate(rate);target.writeframes(scaled.tobytes())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)
    return path


class RiichiAudio:
    """Dedupe repeated paints/ticks; call play only when queue.active changes."""
    def __init__(self,volume=.7,cache_dir=None,player_factory=None,enabled=None):
        self.volume=volume;self.cache_dir=cache_dir
        self.enabled=(os.environ.get('QT_QPA_PLATFORM','').lower() not in ('offscreen','minimal')) if enabled is None else bool(enabled)
        self.error='';self.last_sequence_id=0
        self._factory=player_factory or PostMatchAudio
        self._player=None;self._closed=False

    def play(self,sequence_id):
        if isinstance(sequence_id,bool) or not isinstance(sequence_id,int) or sequence_id<=0:
            self.error='立直音效序号无效';return False
        if self._closed:return False
        if sequence_id<=self.last_sequence_id:return True
        self.last_sequence_id=sequence_id;self.error=''
        if not self.enabled or self.volume==0:return True
        try:
            path=prepare_riichi_sound(self.volume,self.cache_dir)
            if self._player is None:self._player=self._factory()
            self._player.path=path
            self._player.duration=RIICHI_SOUND_DURATION
            # The shared transport leaves all independent music players alone.
            self._player.music_path=None
            if not self._player.play(position=0.):
                self.error=self._player.error or '立直音效播放失败';return False
            return True
        except Exception as error:
            self.error='立直音效播放失败：'+str(error);return False

    def stop(self):
        if self._player is None:return True
        result=self._player.finish_effects()
        if not result:self.error=self._player.error
        return result

    def close(self):
        self._closed=True
        return self.stop()
