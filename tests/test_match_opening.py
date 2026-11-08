"""Opening snapshots freeze real event identities and contain their media."""
from __future__ import annotations
import copy
from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QRectF,Qt
from PySide6.QtGui import QColor,QImage,QPainter
from PySide6.QtWidgets import QApplication
from match_opening import DESIGN_SIZE,OPENING_DURATION,draw_opening,opening_timeline,prepare_opening,title_bounds

APP=QApplication.instance() or QApplication([])


class MatchOpeningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='opening-',dir=ROOT/'tests')
        self.folder=Path(self.temp.name).resolve();self.assertTrue(self.folder.is_relative_to(ROOT/'tests'))
        colors=['#27a7bb','#bb347b','#d6a832','#407dce'];roster=[];players=[]
        for team,color in enumerate(colors):
            logo=QImage(120,80,QImage.Format.Format_ARGB32_Premultiplied);logo.fill(QColor(color));logo.save(str(self.folder/f'{team}.png'))
            for member in range(4):
                profile=dict(id=f'p{team}-{member}',name=f'队{team+1}选手{member+1}',team_id=f't{team}',team=f'第{team+1}队',
                             team_logo=f'{team}.png',team_color=color,color=color,photo='')
                roster.append(profile)
                if member==0:players.append(copy.deepcopy(profile))
        self.model=SimpleNamespace(path=self.folder,settings={},event=None,state=dict(dealer=0,title='测试赛事',players=players,
                                  broadcast=dict(roster=roster,config=dict(title='测试团队赛',opening_title=''))))

    def tearDown(self):self.temp.cleanup()

    def render(self,snapshot,time):
        image=QImage(*DESIGN_SIZE,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
        painter=QPainter(image)
        try:frame=draw_opening(painter,snapshot,time)
        finally:painter.end()
        return image,frame

    def test_complete_event_rosters_and_selected_flags_follow_replacement_player(self):
        replacement=copy.deepcopy(self.model.state['broadcast']['roster'][2])
        replacement.update(name='新出战选手',team_color='#fe5522')
        self.model.state['players'][0]=replacement
        before=copy.deepcopy(self.model.state);snapshot=prepare_opening(self.model)
        self.assertEqual([len(t.members) for t in snapshot.teams],[4]*4)
        self.assertEqual(sum(len(t.members) for t in snapshot.teams),16)
        self.assertEqual([p.player_id for p in snapshot.players],[replacement['id'],'p1-0','p2-0','p3-0'])
        self.assertEqual([m.name for m in snapshot.teams[0].members if m.selected],['新出战选手'])
        self.assertEqual(snapshot.players[0].team_color,'#fe5522')
        self.assertEqual(snapshot.teams[0].color,'#fe5522')
        self.assertEqual(self.model.state,before)

    def test_snapshot_contains_no_mutable_live_media_or_profile_references(self):
        snapshot=prepare_opening(self.model);before,_=self.render(snapshot,13.)
        with self.assertRaises(FrozenInstanceError):snapshot.title='changed'
        self.assertIsInstance(snapshot.teams,tuple)
        self.assertIsInstance(snapshot.teams[0].members,tuple)
        self.assertIsInstance(snapshot.players[0].media.png,bytes)
        self.model.state['players'][0].update(name='已修改',team_color='#ffffff')
        self.model.state['broadcast']['roster'].clear()
        replacement=QImage(120,80,QImage.Format.Format_ARGB32_Premultiplied);replacement.fill(Qt.GlobalColor.black);replacement.save(str(self.folder/'0.png'))
        after,_=self.render(snapshot,13.)
        self.assertEqual(bytes(before.constBits()),bytes(after.constBits()))
        self.assertEqual(snapshot.players[0].name,'队1选手1')

    def test_missing_portrait_uses_own_team_logo_then_own_wordmark(self):
        player=self.model.state['players'][0];player['photo']='missing.png'
        snapshot=prepare_opening(self.model)
        self.assertEqual(snapshot.players[0].media.kind,'team_logo')
        self.assertEqual(QImage.fromData(snapshot.players[0].media.png).pixelColor(60,40).name(),'#27a7bb')
        player['team_logo']='missing-logo.png'
        for profile in self.model.state['broadcast']['roster']:
            if profile['team_id']=='t0':profile['team_logo']='missing-logo.png'
        snapshot=prepare_opening(self.model)
        self.assertEqual(snapshot.players[0].media.kind,'team')
        self.assertEqual(snapshot.players[0].media.label,'第1队')
        self.assertEqual(snapshot.players[0].media.png,b'')

    def test_real_portrait_wins_over_team_logo_and_is_frozen(self):
        photo=QImage(80,120,QImage.Format.Format_ARGB32_Premultiplied);photo.fill(QColor('#f67b4c'));photo.save(str(self.folder/'portrait.png'))
        self.model.state['players'][1]['photo']='portrait.png'
        snapshot=prepare_opening(self.model)
        self.assertEqual(snapshot.players[1].media.kind,'photo')
        self.assertEqual((snapshot.players[1].media.width,snapshot.players[1].media.height),(80,120))
        self.assertEqual(QImage.fromData(snapshot.players[1].media.png).pixelColor(20,20).name(),'#f67b4c')

    def test_title_is_configured_contained_intact_and_default_is_generic(self):
        self.assertEqual(prepare_opening(self.model).title_image.png,b'')
        image=QImage(417,235,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor('#eef1f3'))
        image.setPixelColor(0,0,QColor('red'));image.setPixelColor(416,234,QColor('green'))
        source=self.folder/'title.png';image.save(str(source))
        self.model.state['broadcast']['config']['opening_title']='title.png'
        snapshot=prepare_opening(self.model);frozen=QImage.fromData(snapshot.title_image.png)
        self.assertEqual(frozen.pixelColor(0,0),QColor('red'))
        self.assertEqual(frozen.pixelColor(416,234),QColor('green'))
        bounds=title_bounds(snapshot)
        self.assertTrue(QRectF(0,0,*DESIGN_SIZE).contains(bounds))
        self.assertAlmostEqual(bounds.width()/bounds.height(),417/235)
        self.assertEqual(prepare_opening(self.model,title_reference='').title_image.png,b'')

    def test_requires_four_distinct_teams_and_four_distinct_active_people(self):
        self.model.state['players'][1]['team_id']='t0'
        with self.assertRaisesRegex(ValueError,'四支不同团队'):prepare_opening(self.model)
        self.model.state['players'][1]['team_id']='t1';self.model.state['players'][1]['id']='p0-0'
        with self.assertRaisesRegex(ValueError,'四位不同'):prepare_opening(self.model)
        self.model.state['players'].pop()
        with self.assertRaisesRegex(ValueError,'四席'):prepare_opening(self.model)

    def test_default_cover_comes_from_project_root_and_keeps_custom_and_text_choices(self):
        from opening_config import DEFAULT_TITLE_FILE
        image=QImage(320,180,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor('#27a7bb'))
        image.save(str(self.folder/DEFAULT_TITLE_FILE))
        snapshot=prepare_opening(self.model)
        self.assertEqual(snapshot.title_reference,str(self.folder/DEFAULT_TITLE_FILE))
        self.assertEqual(QImage.fromData(snapshot.title_image.png).pixelColor(20,20),QColor('#27a7bb'))
        self.model.state['broadcast']['config']['opening_title_text_only']=True
        self.assertEqual(prepare_opening(self.model).title_image.png,b'')
        self.model.state['broadcast']['config'].update(opening_title_text_only=False,opening_title='0.png')
        self.assertEqual(prepare_opening(self.model).title_reference,'0.png')
        self.assertEqual(prepare_opening(self.model,title_reference='').title_image.png,b'')

    def test_timeline_has_ordered_phases_and_transparent_complete_end(self):
        self.assertEqual(OPENING_DURATION,18.)
        for time,phase in [(0,'title'),(4,'roster'),(11,'focus'),(17.2,'exit'),(18,'complete')]:
            self.assertEqual(opening_timeline(time).phase,phase)
        self.assertEqual(opening_timeline(0).opacity,0.)
        self.assertEqual(opening_timeline(18).opacity,0.)
        self.assertTrue(opening_timeline(40).complete)
        for invalid in (True,float('nan'),float('inf'),'2'):
            with self.assertRaises(ValueError):opening_timeline(invalid)
        snapshot=prepare_opening(self.model)
        for time in (0,18):
            image,_=self.render(snapshot,time)
            self.assertFalse(any(bytes(image.constBits())[3::4]))

    def test_long_names_and_large_rosters_never_paint_outside_design_canvas(self):
        player=self.model.state['players'][0];player.update(name='非常长的中文出战姓名与EnglishName'*6,team='VeryLongTeamName'*12)
        roster=self.model.state['broadcast']['roster']
        roster.extend(dict(roster[0],id='extra-'+str(i),name='候补选手长姓名'+str(i)) for i in range(20))
        self.model.settings['overlay_font_scale']=1.6;snapshot=prepare_opening(self.model)
        self.assertEqual(len(snapshot.teams[0].members),24)
        for time in (7.,13.):
            image=QImage(2000,1160,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
            painter=QPainter(image);painter.translate(40,40);painter.setOpacity(.8);transform=painter.worldTransform()
            try:
                draw_opening(painter,snapshot,time)
                self.assertEqual(painter.worldTransform(),transform);self.assertEqual(painter.opacity(),.8)
            finally:painter.end()
            alpha=bytes(image.constBits())[3::4]
            self.assertFalse(any(alpha[:40*2000]));self.assertFalse(any(alpha[1120*2000:]))
            for y in range(40,1120):
                self.assertFalse(any(alpha[y*2000:y*2000+40]));self.assertFalse(any(alpha[y*2000+1960:(y+1)*2000]))


if __name__=='__main__':unittest.main()
