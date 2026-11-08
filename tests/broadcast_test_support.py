"""Shared isolated broadcast fixtures and the current programme background."""
import hashlib
from pathlib import Path
import sys
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
SIZE = (1920, 1080)


def initialize_fonts(app):
    from PySide6.QtGui import QFont,QFontDatabase
    import artwork
    for path in (Path('C:/Windows/Fonts/msyh.ttc'),Path('C:/Windows/Fonts/msyhbd.ttc'),
                 Path('C:/Windows/Fonts/arial.ttf'),Path('C:/Windows/Fonts/arialbd.ttf'),ROOT/'resources/NotoSansCJKjp-Regular.otf'):
        if path.is_file():QFontDatabase.addApplicationFont(str(path))
    families=QFontDatabase.families()
    family=next((name for name in ('Microsoft YaHei','Microsoft YaHei UI','Noto Sans CJK JP') if name in families),'Sans Serif')
    artwork.FONT=family;app.setFont(QFont(family,10))
    return family


def review_model(directory,family):
    from model import Model
    from event_store import EventStore
    from media_store import import_media_file
    model=Model(directory)
    store=EventStore(model)
    store.create('转播效果验收 · 四队模拟赛事','team_league')
    teams=(('wave','海浪','demo_wave_teal_v1.png','#36b9c6'),
           ('meteor','流星','demo_meteor_magenta_v1.png','#bf437e'),
           ('peak','山峰','demo_peak_gold_v1.png','#e8b147'),
           ('bird','飞鸟','demo_bird_blue_v1.png','#3d78bf'))
    roster=[]
    for index,(identity,name,filename,colour) in enumerate(teams):
        logo=import_media_file(model.path,ROOT/'resources/demo_team_logos'/filename,'team_logo')
        roster.append(dict(id='review-player-'+identity,name=name+'选手',team=name+'队',team_id='review-team-'+identity,
            team_logo=logo,color=colour,base_points=(84.2,35.9,-22.6,-97.5)[index],games=0,seed=index+1))
    model.configure_broadcast(dict(roster=roster,seats=[row['id'] for row in roster],event_mode='team',
        title='转播效果验收 · 四队模拟赛事',sidebar_mode='team',sidebar_scope='all',sidebar_auto_page=False,sidebar_page_size=10,
        settlement_enabled=False,riichi_effect_enabled=True))
    # The review explicitly imports a local image; production defaults stay blank.
    local_logo=ROOT/'resources/demo_team_logos/demo_wave_teal_v1.png'
    logo=import_media_file(model.path,local_logo,'corner_logo')
    model.configure_corner_header(dict(corner_logo=logo,corner_season='26–27',corner_date='Oct. 29',corner_match='1'))
    model.settings.update(theme='mleague',show_overlays=True,topmost=False,locked=False,console_font_family=family,
                          overlay_font_family=family,broadcast_scene='qualification')
    model.settings['visible']={key:False for key in model.settings.get('visible',{})}
    model.settings['visible'].update(qualification=True,corner_header=True)
    model.save()
    return model


def formal_inventory():
    directory=ROOT/'data'
    return {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob('*') if path.is_file()} if directory.exists() else {}


def background():
    """An original neutral table impression; no reference-video UI is baked in."""
    image=QImage(*SIZE,QImage.Format.Format_RGB32)
    p=QPainter(image);p.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        gradient=QLinearGradient(0,0,0,1080)
        for at,colour in ((0,'#172b33'),(.48,'#34494f'),(1,'#122831')):gradient.setColorAt(at,QColor(colour))
        p.fillRect(image.rect(),gradient);p.setPen(Qt.PenStyle.NoPen)
        for spread in range(56,0,-2):
            p.setBrush(QColor(3,16,22,3));p.drawRoundedRect(QRectF(476-spread,236-spread/2,1336+2*spread,565+spread),110,110)
        mat=QLinearGradient(0,248,0,788);mat.setColorAt(0,QColor('#244d53'));mat.setColorAt(1,QColor('#143c44'))
        p.setBrush(mat);p.drawRoundedRect(QRectF(490,248,1310,540),105,105)
        p.setBrush(QColor('#0f293343'));p.drawRoundedRect(QRectF(548,298,1194,438),74,74)
    finally:p.end()
    return image
