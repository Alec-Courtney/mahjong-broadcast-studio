"""The smaller ML presentation retains ratios, capture size and animation targets."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication
import artwork
from artwork import Animation, Artwork, ml_card_geometry
from final_points_motion import FinalPointsChange
from model import Model
from overlays import OverlayManager
from player_layout import PLAYER_LAYOUT

APP = QApplication.instance() or QApplication([])


def pixels(image):
    return bytes(image.constBits())


class CompactPlayerLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.font_ids = []
        for filename in ('msyh.ttc', 'msyhbd.ttc'):
            path = Path('C:/Windows/Fonts')/filename
            if path.exists():
                cls.font_ids.append(QFontDatabase.addApplicationFont(str(path)))

    @classmethod
    def tearDownClass(cls):
        for identity in cls.font_ids:
            if identity >= 0:
                QFontDatabase.removeApplicationFont(identity)

    def setUp(self):
        (ROOT/'.cache').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='compact-player-', dir=ROOT/'.cache')
        self.model = Model(self.tmp.name)
        self.model.settings.update(theme='mleague', show_overlays=True, show_melds=False,
                                   topmost=False, capture_bg='transparent',
                                   overlay_font_family='Microsoft YaHei', overlay_font_scale=1.)
        for player in self.model.state['players']:
            player.update(team='参考队伍', riichi=False, tenpai=False, waits=[], melds=[])
        self.model.state['players'][0].update(tenpai=True, waits=['3m','6m'], remaining=5)
        self.manager = OverlayManager(self.model)
        APP.processEvents()
        self.manager.timer.stop()

    def tearDown(self):
        self.manager.close(persist=False)
        APP.processEvents()
        self.tmp.cleanup()

    def capture(self):
        window = self.manager.players_capture
        image = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        window.render(image)
        return image

    def test_main_glyphs_keep_uniform_scale_while_wider_cards_retain_compact_height(self):
        photos, bands, waits, glyph_ratios = [], [], [], []
        original_photo, original_card, original_tile = (
            artwork.paint_identity_media, artwork.paint_ml_card, artwork.tile)
        def photo(painter, model, profile, bounds, **kwargs):
            photos.append(painter.worldTransform().mapRect(bounds))
            return original_photo(painter, model, profile, bounds, **kwargs)
        def card(painter, *args, **kwargs):
            layout = original_card(painter, *args, **kwargs)
            bands.append(painter.worldTransform().mapRect(layout['band']))
            ink = layout['labels']['score']['ink'].boundingRect()
            mapped = painter.worldTransform().mapRect(ink)
            glyph_ratios.append((mapped.width()/mapped.height(),ink.width()/ink.height()))
            return layout
        def tile(painter, code, x, y, scale=1., *args, **kwargs):
            waits.append(painter.worldTransform().mapRect(QRectF(x,y,25*scale,36*scale)))
            return original_tile(painter, code, x, y, scale, *args, **kwargs)
        with patch('artwork.paint_identity_media', side_effect=photo), \
             patch('artwork.paint_ml_card', side_effect=card), \
             patch('artwork.tile', side_effect=tile):
            image = self.capture()
        self.assertEqual(image.size().toTuple(), (1920,370))
        self.assertEqual(self.manager.players_capture.geometry(), QRect(0,710,1920,370))
        self.assertEqual(len(photos),4)
        for photo, band in zip(photos,bands):
            self.assertAlmostEqual(photo.height(),184.,places=6)
            self.assertAlmostEqual(photo.width()/photo.height(),132/196,places=6)
            self.assertAlmostEqual(band.width(),268.,places=6)
            self.assertAlmostEqual(band.height(),72*184/196,places=6)
            self.assertLess(photo.height()/1080,.18)
            self.assertAlmostEqual(photo.height()/1080,.17037,places=5)
        self.assertEqual(len(waits),2)
        for wait in waits:
            self.assertAlmostEqual(wait.width()/wait.height(),25/36,places=6)
            self.assertGreater(wait.height(),36*.96)
        for actual, natural in glyph_ratios:
            self.assertAlmostEqual(actual,natural,places=6)
        occupied_left = photos[0].left()
        self.assertAlmostEqual(occupied_left,64.,places=5)
        self.assertLess(occupied_left,130.)
        for index in range(3):
            right = bands[index].right()
            gap = photos[index+1].left()-right
            self.assertAlmostEqual(gap,80.,places=5)
        self.assertAlmostEqual(1920-bands[-1].right(),occupied_left,places=5)
        # The complete source can be bottom-aligned directly in a 1080 frame.
        self.assertEqual(self.manager.capture_output_size(self.manager.players_capture),(1920,370))
        self.assertEqual(1080-(710+346),24)
        self.assertTrue(all(image.pixelColor(170,y).alpha()==0 for y in range(346,370)))

    def test_meld_strip_keeps_original_size_and_clear_gap_above_compact_body(self):
        player = self.model.state['players'][0]
        player['melds'] = [dict(id='compact-meld',kind='pon',tiles=['5p']*3,
                                calledIndex=0,**{'from':1})]
        self.model.settings['show_melds'] = True
        strips = []
        original = artwork.rect
        def rectangle(painter,x,y,width,height,*args,**kwargs):
            if width==398 and height==58:
                strips.append(painter.worldTransform().mapRect(QRectF(x,y,width,height)))
            return original(painter,x,y,width,height,*args,**kwargs)
        with patch('artwork.rect', side_effect=rectangle):
            self.capture()
        self.assertEqual(len(strips),1)
        self.assertEqual(strips[0].size().toSize().toTuple(),(398,58))
        self.assertAlmostEqual(strips[0].height(),58.,places=6)
        photo_top = 153.55102040816325
        self.assertAlmostEqual(photo_top-strips[0].bottom(),14.,places=5)

    def test_final_score_to_pt_animation_ends_on_the_same_compact_pixels(self):
        self.model.state['phase'] = 'finished'
        self.model.settings.update(final_cards_visible=True,final_points_mode='scores')
        art = Artwork(self.model, Animation())
        now = art.created_at+20
        change = FinalPointsChange(art,'scores','pt',now)
        self.model.settings['final_points_mode'] = 'pt'
        def image(draw):
            result = QImage(*PLAYER_LAYOUT.canvas,QImage.Format.Format_ARGB32_Premultiplied)
            result.fill(Qt.GlobalColor.transparent)
            painter = QPainter(result)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            try:draw(painter)
            finally:painter.end()
            return result
        animated = image(lambda p:change.paint(p,0,now+1))
        static = image(lambda p:art.draw(p,'player',0,now=now+1))
        self.assertEqual(pixels(animated),pixels(static))

    def test_old_saved_default_row_migrates_once_and_remains_native_capture_width(self):
        for version,origin,step,height in ((3,101,434,340),(4,140,408,348),(5,104,432,348),(6,32,468,348)):
            with self.subTest(layout_version=version):
                self.model.settings['broadcast_canvas_version'] = version
                self.model.settings['windows'].update({f'player{i}':[origin+step*i,710,438,height] for i in range(4)})
                self.manager.restore_geometry()
                expected = {f'player{i}':[50+468*i,710,438,348] for i in range(4)}
                self.assertEqual(self.manager.player_group_snapshot(),expected)
                self.manager.restore_geometry()
                self.assertEqual(self.manager.player_group_snapshot(),expected)
                self.assertEqual(self.manager.players_capture.geometry(),QRect(0,710,1920,370))

    def test_fitting_capture_width_does_not_undo_body_compaction(self):
        before = self.manager.player_group_snapshot()
        self.manager.resize_player_capture(0,710,1920)
        self.manager.on_display()
        self.assertEqual(self.manager.player_group_snapshot(),before)
        self.assertEqual(self.manager.capture_output_size(self.manager.players_capture),(1920,370))
        self.assertEqual(self.capture().size().toTuple(),(1920,370))

    def test_highest_style_and_old_demo_presets_use_the_ml_capture_layout(self):
        capture = self.manager.players_capture
        expected = self.manager.player_group_snapshot()
        handle = int(capture.winId())
        self.model.setting('theme', 'saikouisen')
        for version, row in ((7, None), (7, (104, 710, 432, 438, 348)),
                             (0, (10, 460, 317, 305, 237)), (7, (10, 460, 317, 305, 242))):
            with self.subTest(version=version, row=row):
                self.model.settings['broadcast_canvas_version'] = version
                windows = self.model.settings['windows']
                for index in range(4):
                    key = f'player{index}'
                    windows.pop(key, None)
                    if row:
                        x, y, step, width, height = row
                        windows[key] = [x + index*step, y, width, height]
                self.manager.restore_geometry()
                self.manager.on_display()
                self.assertEqual(self.manager.player_group_snapshot(), expected)
                self.assertEqual(capture.geometry(), QRect(0, 710, 1920, 370))
                self.assertEqual(self.manager.capture_output_size(capture), (1920, 370))
                self.assertEqual(int(capture.winId()), handle)

    def test_switching_styles_preserves_a_custom_capture_and_refits_standings(self):
        self.manager.set_player_group(10, 460, 305, 12, settings_patch={'player_side_padding': 12.})
        self.manager.set_bottom_mode('individual')
        capture = self.manager.players_capture
        capture_geometry = capture.geometry()
        seats = self.manager.player_group_snapshot()
        standings = self.manager.windows['standings']
        ml_standings_geometry = standings.geometry()
        for theme in ('saikouisen', 'mleague'):
            with self.subTest(theme=theme):
                self.model.setting('theme', theme)
                self.assertEqual(capture.geometry(), capture_geometry)
                self.assertEqual(self.manager.player_group_snapshot(), seats)
                self.assertEqual(standings.geometry(), self.manager.bottom_bounds().toAlignedRect())
        self.assertEqual(standings.geometry(), ml_standings_geometry)
        self.manager.restore_geometry()
        self.assertEqual(self.manager.player_group_snapshot(), seats)


if __name__ == '__main__':
    unittest.main()
