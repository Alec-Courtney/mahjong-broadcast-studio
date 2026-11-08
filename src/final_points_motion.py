"""Keep ML identities fixed while replacing final score numerals with pt."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage,QPainter

from artwork import ease,fmt,ml_card_geometry,paint_brush
from mahjong_values import display_score
from player_layout import PLAYER_LAYOUT
from tournament import match_results
from typography import overlay_scope

FINAL_POINTS_SECONDS=.6


def _image(draw):
    image=QImage(*PLAYER_LAYOUT.canvas,QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:draw(painter)
    finally:painter.end()
    return image


def _label(layout,key):
    item=layout['labels'].get(key)
    if item is None:return _image(lambda p:None)
    spec=layout['style']['text'][key]
    def draw(p):
        p.setTransform(PLAYER_LAYOUT.body_transform(),True)
        p.fillPath(item['ink'],paint_brush(spec['paint'],item['ink'].boundingRect()))
    return _image(draw)


class FinalPointsChange:
    """Only score ink moves; translucent cards are painted exactly once."""
    duration=FINAL_POINTS_SECONDS

    def __init__(self,art,before_mode,after_mode,now,speed=1.,previous=None):
        self.started=now;self.speed=speed;self.frames=[]
        state=art.model.state;rows={row['id']:row for row in match_results(state)}
        for index,player in enumerate(state['players']):
            row=rows[player['id']]
            def geometry(mode):
                value=f"{row['points']:+,.1f}".replace('-','−') if mode=='pt' else fmt(display_score(state,index,art.model.settings.get('riichi_payment_mode','immediate')))
                return ml_card_geometry(player['name'],player.get('team',''),value,row['rank'],settings=art.model.settings)
            old,new=geometry(before_mode),geometry(after_mode)
            def paint_base(p):
                with overlay_scope(art.model.settings):art.player(p,index,now,suppress_numeric=True)
            base=_image(paint_base)
            old_score=previous.score_image(index,now) if previous is not None else _label(old,'score')
            box=PLAYER_LAYOUT.body_transform().mapRect(old['labels']['score']['box'].united(new['labels']['score']['box']))
            self.frames.append(dict(base=base,rank=_label(new,'rank'),old=old_score,new=_label(new,'score'),box=box))

    def elapsed(self,now):return max(0.,(now-self.started)*self.speed)

    def active(self,now):return self.elapsed(now)<self.duration-1e-9

    def set_speed(self,speed,now):
        age=self.elapsed(now);self.started=now-age/speed;self.speed=speed

    def paint_score(self,p,index,now):
        frame=self.frames[index];age=self.elapsed(now)
        old=ease(age/.26);new=ease((age-.30)/.30)
        p.save();p.setClipRect(frame['box'],Qt.ClipOperation.IntersectClip)
        if old<1:
            p.save();p.setOpacity(p.opacity()*(1-old));p.translate(0,-12*old);p.drawImage(0,0,frame['old']);p.restore()
        if new>0:
            p.save();p.setOpacity(p.opacity()*new);p.translate(0,12*(1-new));p.drawImage(0,0,frame['new']);p.restore()
        p.restore()

    def score_image(self,index,now):return _image(lambda p:self.paint_score(p,index,now))

    def paint(self,p,index,now):
        frame=self.frames[index]
        p.drawImage(0,0,frame['base']);p.drawImage(0,0,frame['rank']);self.paint_score(p,index,now)
