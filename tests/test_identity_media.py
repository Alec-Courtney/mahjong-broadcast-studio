"""Real renderer and portable-event coverage for portrait/team-logo fallbacks."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QRectF,Qt
from PySide6.QtGui import QImage,QPainter,QColor,QFontDatabase
from PySide6.QtWidgets import QApplication,QWidget
from model import Model
from media_store import resolve_media_path
from event_store import EventStore
from identity_media import resolve_identity_media,paint_identity_media,contain_rect,identity_preview_image
import artwork
import broadcast_artwork as broadcast
import legacy_player_artwork as legacy
import tournament

APP=QApplication.instance() or QApplication([])
for font in ('C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/msyhbd.ttc'):
    if Path(font).exists():QFontDatabase.addApplicationFont(font)


class IdentityMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.root=Path(self.tmp.name)
        self.model=Model(self.root/'event')
        self.photo=self.root/'photo.png';self.logo=self.root/'logo.png'
        photo=QImage(40,70,QImage.Format.Format_ARGB32);photo.fill(QColor('#ee2244'));photo.save(str(self.photo))
        logo=QImage(200,50,QImage.Format.Format_ARGB32);logo.fill(Qt.GlobalColor.transparent)
        painter=QPainter(logo)
        for x,y,color in ((0,0,'#ff0000'),(184,0,'#00ff00'),(0,34,'#0000ff'),(184,34,'#ffff00')):painter.fillRect(x,y,16,16,QColor(color))
        painter.end();logo.save(str(self.logo))

    def tearDown(self):
        APP.processEvents();self.tmp.cleanup()

    def configure(self,case):
        roster=copy.deepcopy(self.model.state['broadcast']['roster'])
        for index,player in enumerate(roster):
            player.update(team='演示青岚队',team_id='demo-team',photo='',team_logo='')
            if case=='photo':player.update(photo=str(self.photo),team_logo=str(self.logo))
            elif case=='logo':player['team_logo']=str(self.logo)
            elif case=='bad_photo':player.update(photo='does-not-exist.png',team_logo=str(self.logo))
            elif case=='bad_logo':player['team_logo']='missing-logo.png'
            elif case=='neutral':player.update(team='',team_id='')
        self.model.configure_broadcast({'roster':roster})

    def test_priority_invalid_paths_and_personal_neutral_are_explicit(self):
        for case,kind in [('photo','photo'),('logo','team_logo'),('bad_photo','team_logo'),('bad_logo','team'),('team','team'),('neutral','neutral')]:
            with self.subTest(case=case):
                self.configure(case)
                player=self.model.state['players'][0]
                self.assertEqual(resolve_identity_media(self.model,player).kind,kind)
                before=copy.deepcopy(self.model.state);identity_preview_image(self.model,player)
                self.assertEqual(self.model.state,before)
        self.assertEqual(resolve_identity_media(self.model,dict(id='unassigned:p1',name='选手一',team_id='unassigned:p1'),team=True).kind,'neutral')

    def test_team_card_uses_logo_and_never_a_members_portrait(self):
        self.configure('photo')
        row={'id':'demo-team','name':'演示青岚队'}
        self.assertEqual(resolve_identity_media(self.model,row,team=True).kind,'team_logo')
        self.assertEqual(resolve_identity_media(self.model,self.model.state['players'][0]).kind,'photo')

    def test_snapshot_explicit_missing_photo_is_not_replaced_by_current_portrait(self):
        self.configure('photo')
        snapshot=dict(self.model.state['players'][0],photo='',team_logo='')
        self.assertEqual(resolve_identity_media(self.model,snapshot).kind,'team_logo')

    def test_contain_preserves_four_logo_corners_and_transparency_with_padding(self):
        self.configure('logo');profile=self.model.state['players'][0]
        raw=QImage(str(self.logo));box=QRectF(0,0,120,165);target=contain_rect(raw,box)
        self.assertAlmostEqual(target.width()/target.height(),4.)
        self.assertGreaterEqual(target.left(),12.)
        image=identity_preview_image(self.model,profile,120,165)
        colors={image.pixelColor(x,y).name() for x in range(image.width()) for y in range(image.height())}
        self.assertTrue({'#ff0000','#00ff00','#0000ff','#ffff00'}.issubset(colors))
        self.assertEqual(image.pixelColor(2,2).alpha(),179)
        tall=QImage(40,200,QImage.Format.Format_ARGB32);tall.fill(QColor('white'))
        fit=contain_rect(tall,box)
        self.assertAlmostEqual(fit.width()/fit.height(),.2)
        self.assertTrue(box.adjusted(4,4,-4,-4).contains(fit))

    def test_team_wordmark_is_complete_and_non_photo_mat_readable_on_white(self):
        self.configure('team');profile=self.model.state['players'][0]
        profile=dict(profile,team='完整保留名称的跨地区特别邀请演示俱乐部')
        captured=[];original=QPainter.drawText
        def trace(painter,*args):
            captured.extend(value for value in args if isinstance(value,str));return original(painter,*args)
        with patch.object(QPainter,'drawText',new=trace):image=identity_preview_image(self.model,profile,120,165)
        self.assertIn(profile['team'],captured);self.assertFalse(any('…' in value for value in captured))
        self.assertEqual(image.pixelColor(2,2).alpha(),179)
        white=QImage(120,165,QImage.Format.Format_ARGB32);white.fill(QColor('white'));p=QPainter(white);p.drawImage(0,0,image);p.end()
        self.assertLess(white.pixelColor(2,2).lightness(),90)
        self.configure('neutral');neutral=identity_preview_image(self.model,self.model.state['players'][0])
        self.assertEqual(neutral.pixelColor(2,2).alpha(),179)

    def test_logo_survives_new_half_export_relocation_and_restart(self):
        self.configure('logo')
        reference=self.model.state['players'][0]['team_logo']
        self.assertTrue(reference.startswith('assets/imported/logos/'))
        self.model.start_match();self.assertEqual(self.model.state['players'][0]['team_logo'],reference)
        before=copy.deepcopy(self.model.state);destination=self.root/'portable.json';self.model.export_data(destination)
        payload=json.loads(destination.read_text(encoding='utf-8'))
        self.assertEqual(len(payload['assets']),1);self.assertIn(reference,payload['assets'])
        self.logo.unlink();resolve_media_path(self.model.path,reference).unlink()
        relocated=Model(self.root/'relocated');relocated.import_data(destination)
        self.assertEqual(relocated.state,before)
        self.assertEqual(resolve_identity_media(relocated,relocated.state['players'][0]).kind,'team_logo')
        restarted=Model(relocated.path)
        self.assertEqual(restarted.state,before)

    def test_old_archived_logos_stay_frozen_when_current_team_asset_changes(self):
        self.configure('logo');original=self.model.state['players'][0]['team_logo']
        self.model.settle(dict(type='ron',winner=1,loser=2,han=1,fu=30),None);self.model.commit_match()
        recorded=copy.deepcopy(self.model.state['broadcast']['matches'][0])
        self.configure('team')
        current=self.model.state['broadcast']['matches'][0]
        self.assertEqual(current['profiles'],recorded['profiles'])
        self.assertEqual(next(r for r in current['results'] if r['id']=='p1')['team_logo'],original)

    def test_corrupt_embedded_logo_is_rejected_without_replacing_the_event(self):
        self.configure('logo');destination=self.root/'logo.json';self.model.export_data(destination)
        payload=json.loads(destination.read_text(encoding='utf-8'));next(iter(payload['assets'].values()))['sha256']='bad'
        destination.write_text(json.dumps(payload),encoding='utf-8');before=copy.deepcopy(self.model.state)
        with self.assertRaisesRegex(ValueError,'校验'):self.model.import_data(destination)
        self.assertEqual(self.model.state,before)

    def test_event_logo_draft_controls_save_clear_and_isolate_other_events(self):
        from types import SimpleNamespace
        from event_workspace import EventWorkspaceDialog
        self.configure('team');self.model.configure_broadcast({'event_mode':'team'});store=EventStore(self.model)
        parent=QWidget();parent.model=self.model;parent.open_settings=lambda:None;parent.open_player_layout=lambda:None;parent.shortcuts=SimpleNamespace(open_dialog=lambda:None)
        dialog=EventWorkspaceDialog(parent)
        try:
            original=self.model.event['id']
            dialog.choose_logo('demo-team',team=True,filename=str(self.logo))
            self.assertFalse(self.model.state['players'][0].get('team_logo'))
            self.assertIn('demo-team',dialog.team_logo_drafts)
            dialog.save_teams()
            reference=self.model.state['players'][0]['team_logo']
            self.assertTrue(reference.startswith('assets/imported/logos/'))
            self.assertTrue(all(player.get('team_logo')==reference for player in self.model.state['broadcast']['roster']))
            before_icon=dialog.teams.cellWidget(0,7).icon().pixmap(48,36).toImage()
            dialog.set_logo_draft('demo-team','')
            after_icon=dialog.teams.cellWidget(0,7).icon().pixmap(48,36).toImage()
            self.assertNotEqual(before_icon,after_icon)
            self.assertEqual(self.model.state['players'][0]['team_logo'],reference)
            dialog.save_teams();self.assertFalse(self.model.state['players'][0].get('team_logo'))
            dialog.choose_logo('demo-team',team=True,filename=str(self.logo));dialog.save_teams()
            store.create('独立个人赛事','individual_league')
            self.assertFalse(self.model.state['players'][0].get('team_logo'))
            store.open(original)
            self.assertEqual(resolve_identity_media(self.model,self.model.state['players'][0]).kind,'team_logo')
        finally:
            dialog.hide();dialog.deleteLater();parent.deleteLater();APP.processEvents()

    def test_both_photo_editors_keep_existing_logo_when_photo_is_cleared(self):
        from console import ProfilesDialog
        from broadcast_console import BroadcastDialog
        from overlays import OverlayManager
        self.configure('photo');reference=self.model.state['players'][0]['team_logo']
        self.model.settings['show_overlays']=False;manager=OverlayManager(self.model)
        parent=QWidget();parent.model=self.model;parent.manager=manager;parent.error=lambda message:self.fail(message)
        profile=ProfilesDialog(parent)
        panel=None
        try:
            profile.fields[0]['photo']='';profile.apply()
            self.assertEqual(self.model.state['players'][0]['team_logo'],reference)
            panel=BroadcastDialog(parent);panel.photo_drafts['p1']=''
            collected=panel.collect_roster()
            self.assertEqual(next(player for player in collected if player['id']=='p1')['team_logo'],reference)
        finally:
            if panel:panel.hide();panel.deleteLater()
            profile.deleteLater();parent.deleteLater();manager.close();APP.processEvents()

    def test_actual_players_wins_standings_result_rates_chart_and_settlement_matrix(self):
        from identity_media import paint_identity_media as original
        seen=[]
        def draw(*args,**kwargs):
            result=original(*args,**kwargs);seen.append(result.kind);return result
        def frame(width,height,callback):
            image=QImage(width,height,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent);p=QPainter(image)
            try:callback(p)
            finally:p.end()
        with patch.object(artwork,'paint_identity_media',side_effect=draw),patch.object(broadcast,'paint_identity_media',side_effect=draw),patch.object(legacy,'paint_identity_media',side_effect=draw):
            for theme in ('mleague','saikouisen'):
                for case,expected in [('photo','photo'),('logo','team_logo'),('bad_photo','team_logo'),('team','team'),('neutral','neutral')]:
                    with self.subTest(theme=theme,case=case):
                        self.configure(case);self.model.settings['theme']=theme
                        art=artwork.Artwork(self.model,artwork.Animation());before=copy.deepcopy(self.model.state)
                        seen.clear();frame(438,340,lambda p:art.draw(p,'player',0,now=art.created_at+10));self.assertEqual(seen,[expected])
                        for label,callback in [('result_identity',lambda p:broadcast._identity(p,self.model,dict(self.model.state['players'][0]),0,0,500,160)),
                                               ('individual_standing',lambda p:broadcast._standing_card(p,self.model,dict(self.model.state['players'][0],rank=1,points=12.3),'individual'))]:
                            seen.clear();frame(600,220,callback);self.assertIn(expected,seen,label)
                        self.assertEqual(self.model.state,before)
            self.configure('logo')
            self.model.field('roundIndex',7);self.model.field('dealer',3)
            self.model.settle(dict(type='ron',winner=1,loser=2,han=3,fu=30),dict(winner=1,name='选手二',han=3,fu=30,yaku=['断幺九']))
            self.model.commit_match()
            for theme in ('mleague','saikouisen'):
                self.model.settings['theme']=theme;art=artwork.Artwork(self.model,artwork.Animation());before=copy.deepcopy(self.model.state)
                seen.clear();frame(*artwork.SIZES['yaku'],lambda p:art.draw(p,'yaku',now=art.created_at+30));self.assertIn('team_logo',seen)
                for view in ('rates','result'):
                    self.model.state['broadcast']['config']['statistics_view']=view
                    seen.clear();frame(1600,800,lambda p:broadcast.draw_extension(p,'statistics',self.model));self.assertIn('team_logo',seen)
                seen.clear();frame(1600,900,lambda p:broadcast.draw_extension(p,'chart',self.model));self.assertIn('team_logo',seen)
                for mode in ('individual','team'):
                    summary=tournament.final_settlement_summary(self.model.state,mode)
                    for stage in ('hand','total'):
                        seen.clear();frame(1840,160,lambda p:broadcast.draw_extension(p,'standings',self.model,settlement=summary,settlement_stage=stage));self.assertIn('team_logo',seen)
                if 'statistics_view' in before['broadcast']['config']:self.model.state['broadcast']['config']['statistics_view']=before['broadcast']['config']['statistics_view']
                else:self.model.state['broadcast']['config'].pop('statistics_view',None)
                self.assertEqual(self.model.state,before)


if __name__=='__main__':unittest.main()
