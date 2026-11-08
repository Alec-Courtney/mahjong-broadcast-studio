"""Reference-sized artwork survives the real four-seat and ranking render paths.

The raster checks run at the offscreen display's 100% DPR. Fractional-DPI
screen transitions are covered separately; a fake reference canvas alone would
not change Qt's native framebuffer density.
"""
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
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication
import artwork
import broadcast_artwork
from model import Model
from overlays import OverlayManager
from player_layout import PLAYER_LAYOUT

APP = QApplication.instance() or QApplication([])
CANVAS = QRect(0, 0, 1920, 1080)


def spans(image, row):
    result = []
    start = None
    for x in range(image.width()+1):
        visible = x < image.width() and image.pixelColor(x, row).alpha() > 0
        if visible and start is None:
            start = x
        elif not visible and start is not None:
            result.append((start, x))
            start = None
    return result


class ReferenceCaptureLayoutTests(unittest.TestCase):
    def setUp(self):
        if APP.primaryScreen().devicePixelRatio() != 1:
            self.skipTest('Native raster assertions require the 100% offscreen display')
        (ROOT/'.cache').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='reference-capture-', dir=ROOT/'.cache')
        self.canvas_patch = patch('overlays.reference_canvas', return_value=CANVAS)
        self.canvas_patch.start()
        self.addCleanup(self.canvas_patch.stop)
        self.model = Model(self.temp.name)
        self.model.settings.update(theme='mleague', show_overlays=True, topmost=False,
                                   capture_bg='transparent', player_side_padding=2.3)
        # Real opaque local image fixtures isolate photo layer order from the
        # deliberately translucent no-photo fallback and ranking accent paint.
        for seat, player in enumerate(self.model.state['players']):
            player['team'] = '参考队伍'
            image = QImage(132, 196, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor(80+seat*35, 95+seat*25, 135+seat*20))
            photo_path = Path(self.temp.name)/f'portrait-{seat}.png'
            self.assertTrue(image.save(str(photo_path)))
            player['photo'] = str(photo_path)
        self.manager = OverlayManager(self.model)
        APP.processEvents()
        self.manager.timer.stop()

    def tearDown(self):
        self.manager.close()
        APP.processEvents()
        self.temp.cleanup()

    def render_players(self):
        window = self.manager.players_capture
        image = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        window.render(image)
        return image

    def render_ranking(self, *, settlement=False):
        self.manager.enabled['standings'] = True
        self.manager._sync_bottom_geometry(force=True)
        window = self.manager.windows['standings']
        capture = self.manager.players_capture
        image = QImage(capture.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            painter.translate(window.x()-capture.x(), window.y()-capture.y())
            painter.scale(window.width()/window.base[0], window.height()/window.base[1])
            if settlement:
                summary = dict(mode='individual', players=[
                    dict(seat=seat, player_id=player['id'], player_name=player['name'],
                         points_before=0., points_after=12.3, rank_after=seat+1)
                    for seat, player in enumerate(self.model.state['players'])])
                broadcast_artwork.draw_extension(painter, 'standings', self.model,
                    layout=self.manager.standings_layout(), settlement=summary,
                    settlement_stage='hand', progress=1.)
            else:
                self.manager.draw_content(painter, 'standings', static=True)
        finally:
            painter.end()
        return image

    def test_actual_four_seat_capture_retains_reference_size_and_equal_margins(self):
        capture = self.manager.players_capture
        self.assertEqual(capture.geometry(), QRect(0, 710, 1920, 370))
        self.assertEqual(capture.base, (1920, 370))
        portraits = []
        original = artwork.paint_identity_media

        def record(painter, model, profile, bounds, **kwargs):
            portraits.append(painter.worldTransform().mapRect(bounds))
            return original(painter, model, profile, bounds, **kwargs)

        with patch('artwork.paint_identity_media', side_effect=record):
            image = self.render_players()
        self.assertEqual(image.size().toTuple(), (1920, 370))
        occupied = spans(image, 310)
        self.assertEqual(len(occupied), 4)
        self.assertEqual(spans(image, 220), occupied)
        self.assertEqual(occupied[0][0], 1920-occupied[-1][1])
        for start, end in occupied:
            self.assertAlmostEqual(end-start, 388, delta=1)
        for index in range(3):
            self.assertAlmostEqual(occupied[index+1][0]-occupied[index][1], 80, delta=1)
        self.assertAlmostEqual(occupied[0][0], 64, delta=1)
        for seat, portrait in enumerate(portraits):
            self.assertAlmostEqual(portrait.x(), 64+468*seat, places=5)
            self.assertAlmostEqual(portrait.y(), 153.551020, places=5)
            self.assertAlmostEqual(portrait.width(), 123.918367, places=5)
            self.assertAlmostEqual(portrait.height(), 184., places=5)
        self.assertEqual(PLAYER_LAYOUT.photo_height, 196)
        # Reference comparison uses occupied frame proportions, independent of
        # the downloaded screenshot's original pixel dimensions.
        for portrait in portraits:
            self.assertGreaterEqual(portrait.height()/CANVAS.height(), .168)
            self.assertLessEqual(portrait.height()/CANVAS.height(), .172)
            self.assertAlmostEqual(portrait.width()/CANVAS.width(), .0645, delta=.001)
        self.assertTrue(all(not spans(image, row) for row in range(346, 370)))
        dealer_bottom = capture.y()+346
        self.assertEqual(image.height()-346, 24)
        self.assertEqual(CANVAS.height()-dealer_bottom, 24)
        self.assertGreaterEqual(CANVAS.height()-dealer_bottom, 24)
        self.assertEqual(image.pixelColor(170, 344).name(), '#e62332')

    def test_win_capture_keeps_native_full_width_and_twenty_pixel_bottom_gaps(self):
        window = self.manager.windows['yaku']
        self.assertEqual(window.geometry(), QRect(0, 840, 1920, 220))
        self.assertEqual(CANVAS.height()-window.y()-window.height(), 20)
        self.model.last_info = dict(winner=0, name=self.model.state['players'][0]['name'],
                                   han=3, fu=40, yaku=['立直', '断幺九', '宝牌 1'])
        image = QImage(1920, 220, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            self.manager.draw_content(painter, 'yaku', static=True)
        finally:
            painter.end()
        self.assertEqual(spans(image, 100), [(50, 438), (490, 1880)])
        self.assertTrue(all(not spans(image, row) for row in range(200, 220)))

    def test_rankings_and_hand_settlement_keep_live_portrait_and_score_band_positions(self):
        original_card = artwork.paint_ml_card
        original_photo = artwork.paint_identity_media

        def geometry_for(render, photo_module):
            bands, photos = [], []

            def card(painter, *args, **kwargs):
                result = original_card(painter, *args, **kwargs)
                bands.append(painter.worldTransform().mapRect(result['band']))
                return result

            def photo(painter, model, profile, bounds, **kwargs):
                photos.append(painter.worldTransform().mapRect(bounds))
                return original_photo(painter, model, profile, bounds, **kwargs)

            with patch('artwork.paint_ml_card', side_effect=card), \
                 patch(photo_module+'.paint_identity_media', side_effect=photo):
                image = render()
            self.assertEqual((len(bands), len(photos)), (4, 4))
            return bands, photos, image

        live_bands, live_photos, live_image = geometry_for(self.render_players, 'artwork')
        for settlement in (False, True):
            with self.subTest(settlement=settlement):
                bands, photos, image = geometry_for(lambda:self.render_ranking(settlement=settlement),
                                                    'broadcast_artwork')
                for before, after in zip(live_bands+live_photos, bands+photos):
                    for a, b in zip(before.getRect(), after.getRect()):
                        self.assertAlmostEqual(a, b, places=5)
                # The 132px photo overlaps the numeric band by 4px. Geometry
                # alone cannot detect a later band painting over its right edge.
                for photo, band in zip(live_photos, live_bands):
                    x, y = round(photo.right()-2), round(band.center().y())
                    self.assertEqual(image.pixelColor(x, y), live_image.pixelColor(x, y))


if __name__ == '__main__':
    unittest.main()
