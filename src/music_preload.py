"""Silently warm the existing decoded-music cache without copying source media."""
from concurrent.futures import ThreadPoolExecutor
from media_store import resolve_media_path


class MusicPreloader:
    def __init__(self,model):
        self.model=model;self.signature=None;self.future=None;self.closed=False
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='music-preload')
        model.changed.connect(self.schedule);model.display_changed.connect(self.schedule)
        self.schedule()

    def schedule(self,*_):
        if self.closed:return
        config=self.model.state['broadcast']['config']
        reference=config.get('postmatch_music_path','');volume=config.get('postmatch_music_volume',.35)
        if not reference or not config.get('postmatch_music_enabled',True) or not volume:return
        path=resolve_media_path(self.model.path,reference)
        try:details=path.stat()
        except OSError:return
        signature=(str(path.resolve()),details.st_size,details.st_mtime_ns,volume)
        if signature==self.signature:return
        self.signature=signature
        if self.future is not None:self.future.cancel()
        self.future=self.executor.submit(self._prepare,path,volume)

    @staticmethod
    def _prepare(path,volume):
        from postmatch_audio import PostMatchAudio
        audio=PostMatchAudio()
        # prepare() only performs bounded file work; no sound device is opened.
        return audio.prepare(music_path=path,music_volume=volume,sfx_enabled=False)

    def close(self):
        self.closed=True
        if self.future is not None:self.future.cancel()
        self.executor.shutdown(wait=True,cancel_futures=True)
