"""Operator-owned full-screen cues and one independent background-music bus."""
import threading
import time
from PySide6.QtCore import QObject,Signal
from PySide6.QtGui import QImage,QPainter
from PySide6.QtCore import Qt

PAGE_BUTTONS=(('opening','开场连播'),('opening_title','标题'),('opening_roster','队伍介绍'),
              ('opening_focus','本场选手'),('four_conditions','四人条件'),('chart','点数走势'),
              ('statistics','技术统计'),('ending_chart','片尾走势'),('ending_statistics','片尾统计'),('ending','片尾连播'))
PANELS=('four_conditions','chart','statistics')


class ProgrammeMusic(QObject):
    changed=Signal()
    failed=Signal(str)
    prepared=Signal(int,object,str)

    def __init__(self,model,audio_factory=None):
        super().__init__();self.model=model;self.audio=None;self.enabled=False;self.automatic=False
        self.paused=False;self.preparing=False;self._generation=0;self._options=None;self._closed=False
        self.prepared.connect(self._ready)
        self.audio_factory=audio_factory

    def set_enabled(self,enabled,automatic=False):
        if self._closed and enabled:return
        config=self.model.state['broadcast']['config'];reference=config.get('postmatch_music_path','')
        enabled=bool(enabled and reference);self.automatic=automatic
        if not enabled:
            self.enabled=False;self.preparing=False;self._generation+=1;self.paused=False
            if self.audio:self.audio.close();self.audio=None
            self.changed.emit();return
        self._start(self._music_options(reference, config.get('postmatch_music_volume', .35)))

    def audition(self, reference, volume, seconds):
        """Play an explicit draft selection on an independently owned bus."""
        if not reference:return
        options = self._music_options(reference, volume)
        options['music_preview_seconds'] = seconds
        self._start(options)

    def _music_options(self, reference, volume):
        from media_store import resolve_media_path

        return dict(music_path=str(resolve_media_path(self.model.path, reference)),
                    music_volume=volume, music_enabled=True, sfx_enabled=False)

    def _start(self, options):
        if self._closed:return
        if self.enabled and self._options==options:return
        self._generation+=1;token=self._generation;self.enabled=True;self.preparing=True;self._options=options
        if self.audio:self.audio.close();self.audio=None
        if options['music_volume']<=0:self.preparing=False;self.changed.emit();return
        self.changed.emit()
        def prepare():
            audio=None;error=''
            try:
                from postmatch_audio import PostMatchAudio
                audio=self.audio_factory() if self.audio_factory is not None else PostMatchAudio()
                audio.prepare(**options);error=audio.error or ''
            except Exception as exc:error=str(exc)
            self.prepared.emit(token,audio,error)
        threading.Thread(target=prepare,name='ProgrammeMusicPrepare',daemon=True).start()

    def _ready(self,token,audio,error):
        if self._closed or token!=self._generation or not self.enabled:
            if audio:audio.close()
            return
        self.preparing=False
        if error:
            if audio:audio.close()
            self.enabled=False;self.failed.emit(error)
        else:
            self.audio=audio
            if not self.paused:self._play()
        self.changed.emit()

    def _play(self):
        try:
            if self.audio.set_music_enabled(True) is False:raise RuntimeError(self.audio.error or '背景音乐无法播放')
        except Exception as exc:
            self.enabled=False
            if self.audio:self.audio.close();self.audio=None
            self.failed.emit(str(exc))

    def set_paused(self,paused):
        paused=bool(paused)
        if paused==self.paused:return
        self.paused=paused
        if self.audio:
            if paused:
                try:
                    if self.audio.pause() is False:raise RuntimeError(self.audio.error or '背景音乐无法暂停')
                except Exception as exc:
                    self.enabled=False;self.audio.close();self.audio=None;self.failed.emit(str(exc))
            else:self._play()
        self.changed.emit()

    def leave_automatic(self):
        if self.automatic and self.enabled:self.set_enabled(False,automatic=True)

    def close(self):
        self._closed=True;self.set_enabled(False)


class ProgrammeController(QObject):
    changed=Signal()

    def __init__(self,manager):
        super().__init__();self.manager=manager;self.music=ProgrammeMusic(manager.model)
        self._switching=False;self._session=None;self._signature=None;self._static_paused=False
        self.frozen_kind=None;self.frozen_frame=None;self._paused_at=0.
        manager.postmatch.music_bus=self.music
        manager.opening.changed.connect(self.sync);manager.postmatch.changed.connect(self.sync)
        self.music.changed.connect(self.sync);manager.model.changed.connect(self.on_model_changed)

    @property
    def current_key(self):
        if self.manager.opening.active:return 'opening_'+self.manager.opening.current_page
        if self.manager.postmatch.active or self.manager.postmatch.preparing:
            playback=self.manager.postmatch
            if playback.page:return 'ending_'+playback.page
            from postmatch_sequence import ENTRY_DURATION
            return 'ending_chart' if playback.elapsed<ENTRY_DURATION+playback.chart_hold else 'ending_statistics'
        return self.manager.fullscreen_panel()

    @property
    def paused(self):
        return self._static_paused or self.manager.opening.paused or self.manager.postmatch.paused

    def sync(self):
        if self._switching or self.manager.closing:return
        opening,ending=self.manager.opening,self.manager.postmatch
        session=('opening',id(opening.snapshot),opening.page) if opening.active else ('ending',id(ending.snapshot)) if ending.active else None
        if session!=self._session:
            self._session=session
            if session:
                self._switching=True
                try:
                    if opening.active:ending.stop()
                    else:opening.stop()
                    self._clear_panels();self._static_paused=False;self.frozen_kind=self.frozen_frame=None
                finally:self._switching=False
                self.music.set_enabled(self.manager.model.state['broadcast']['config'].get('postmatch_music_enabled',True),automatic=True)
            elif not ending.preparing:self.music.leave_automatic()
        if self.current_key is None and not self.music.enabled and not ending.preparing:
            self._static_paused=False;self.frozen_kind=self.frozen_frame=None
        self.music.set_paused(self.paused)
        signature=(self.current_key,self.paused,self.music.enabled,self.music.preparing,opening.state,ending.state)
        if signature!=self._signature:self._signature=signature;self.changed.emit()

    def _clear_panels(self):
        for key in PANELS:
            self.manager.enabled[key]=False;self.manager.extension_transitions.pop(key,None)
        self.manager.model.settings.setdefault('visible',{}).update({key:False for key in PANELS})

    def _stop_visuals(self):
        self.manager.opening.stop();self.manager.postmatch.stop();self._clear_panels()
        self._static_paused=False;self.frozen_kind=self.frozen_frame=None;self._session=None

    def toggle(self,key):
        if key not in dict(PAGE_BUTTONS):raise ValueError('播出页面无效')
        if key!=self.current_key:return self.select(key)
        enabled=self.manager.enabled.copy();enabled.update({panel:False for panel in PANELS})
        self.manager._save_display_settings(visible=enabled)
        self._switching=True
        try:self._stop_visuals();self.music.leave_automatic()
        finally:
            self._switching=False;self.manager.on_display();self.sync()

    def select(self,key):
        if key not in dict(PAGE_BUTTONS):raise ValueError('播出页面无效')
        if key.startswith('ending') and self.manager.model.state.get('phase')!='finished':raise ValueError('请在半庄结束后显示片尾')
        patch={'show_overlays':True}
        if self.manager.model.settings.get('broadcast_scene')=='clean':patch['broadcast_scene']='table'
        if key in PANELS:
            self.manager.set_extension_visible(key,True,settings_patch=patch)
            self.sync()
            return
        enabled=self.manager.enabled.copy();enabled.update({panel:False for panel in PANELS})
        self.manager._save_display_settings(visible=enabled,**patch)
        self._switching=True
        try:
            self._stop_visuals()
            if key.startswith('opening'):
                self.manager.opening.start(None if key=='opening' else key.removeprefix('opening_'))
            elif key.startswith('ending'):
                self.manager.postmatch.start(None if key=='ending' else key.removeprefix('ending_'))
            else:
                self.music.leave_automatic();self.manager.set_extension_visible(key,True)
        finally:
            self._switching=False;self.manager.on_display();self.sync()

    def before_panel(self,key):
        if self._switching:return
        self._switching=True
        try:
            if self.manager.model.settings.get('split_fullscreen_windows',False):
                self.manager.opening.stop();self.manager.postmatch.stop()
                self._static_paused=False;self.frozen_kind=self.frozen_frame=None;self._session=None
            else:self._stop_visuals()
            self.music.leave_automatic()
        finally:self._switching=False

    def pause_resume(self):
        if self.manager.opening.active:self.manager.opening.pause_resume()
        elif self.manager.postmatch.active:self.manager.postmatch.pause_resume()
        else:
            key=self.current_key
            if not self._static_paused:
                if key:
                    frame=QImage(*self.manager.base_size(key),QImage.Format.Format_ARGB32_Premultiplied);frame.fill(Qt.GlobalColor.transparent)
                    p=QPainter(frame)
                    try:self.manager.paint_content(p,key)
                    finally:p.end()
                    self.frozen_kind,self.frozen_frame=key,frame
                self._paused_at=time.monotonic();self._static_paused=True
            else:
                delay=time.monotonic()-self._paused_at
                if key=='chart':
                    if self.manager.chart_started is not None:self.manager.chart_started+=delay
                    self.manager.chart_flow_started+=delay
                if key in self.manager.extension_transitions:
                    start,old,new=self.manager.extension_transitions[key];self.manager.extension_transitions[key]=(start+delay,old,new)
                self._static_paused=False;self.frozen_kind=self.frozen_frame=None
            self.sync()
        self.manager.on_display();self.sync()

    def stop(self):
        enabled=self.manager.enabled.copy();enabled.update({panel:False for panel in PANELS})
        self.manager._save_display_settings(visible=enabled)
        self._switching=True
        try:self._stop_visuals();self.music.set_enabled(False)
        finally:self._switching=False
        self.manager.on_display();self.sync()

    def on_model_changed(self,before,after,kind):
        if self.manager.closing:return
        if kind not in ('import','clear','restore','start_match','new_match','next_hand','delete_match_record'):return
        self._switching=True
        try:
            self.manager.opening.stop();self.manager.postmatch.stop();self.music.set_enabled(False)
            self._static_paused=False;self.frozen_kind=self.frozen_frame=None;self._session=None
        finally:self._switching=False
        self.manager.on_display();self.sync()

    def close(self):self.music.close()
