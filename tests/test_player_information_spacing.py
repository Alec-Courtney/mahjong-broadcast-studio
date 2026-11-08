"""Real glyph/tile pixels keep ML information in the reference proportions.

All ratios use the complete 1920x1080 programme, never the transparent strip.
Missing team data leaves its reserved line empty, as requested.
"""
import copy
import hashlib
import json
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
from card_style import default_card_style
from model import Model
from player_layout import PLAYER_LAYOUT

APP = QApplication.instance() or QApplication([])
FRAME_HEIGHT = 1080


def white_rows(image, bounds):
    """Read visible white cores, excluding antialiased/JPEG edge fringes."""
    x0, y0, x1, y1 = bounds
    rows = []
    for y in range(y0, y1):
        count = 0
        for x in range(x0, x1):
            c = image.pixelColor(x, y)
            channels = (c.red(), c.green(), c.blue())
            if c.alpha() > 180 and min(channels) > 205 and max(channels)-min(channels) < 40:
                count += 1
        if count >= 4:
            rows.append(y)
    return rows


def row_bounds(image, bounds):
    rows = white_rows(image, bounds)
    if not rows:
        raise AssertionError('Expected actual white glyph/tile pixels')
    return rows[0], rows[-1]+1


def image_bytes(image):
    return bytes(image.constBits())


def render(model):
    art = Artwork(model, Animation())
    image = QImage(*PLAYER_LAYOUT.canvas, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        art.draw(painter, 'player', 0, now=art.created_at+20)
    finally:
        painter.end()
    return image


class PlayerInformationSpacingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.font_ids = []
        for file in ('msyh.ttc', 'msyhbd.ttc'):
            path = Path('C:/Windows/Fonts')/file
            if path.exists():
                cls.font_ids.append(QFontDatabase.addApplicationFont(str(path)))

    @classmethod
    def tearDownClass(cls):
        for identity in cls.font_ids:
            if identity >= 0:
                QFontDatabase.removeApplicationFont(identity)

    def setUp(self):
        (ROOT/'.cache').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='information-spacing-', dir=ROOT/'.cache')
        self.model = Model(self.tmp.name, preferences_root=Path(self.tmp.name)/'preferences')
        self.model.settings.update(theme='mleague', show_melds=False,
                                   overlay_font_family='Microsoft YaHei', overlay_font_scale=1.)
        self.player = self.model.state['players'][0]
        self.player.update(name='黒沢咲', team='TEAM RAIDEN / 雷電', photo='', riichi=False,
                           tenpai=True, waits=['3m', '6m'], remaining=3, melds=[])
        photo = QImage(132, 196, QImage.Format.Format_ARGB32_Premultiplied)
        photo.fill(QColor('#285064'))
        photo_path = Path(self.tmp.name)/'opaque-portrait.png'
        self.assertTrue(photo.save(str(photo_path)))
        self.player['photo'] = str(photo_path)

    def tearDown(self):
        self.tmp.cleanup()

    def assert_reference_information(self, image):
        waits = row_bounds(image, (154, 145, 333, 203))
        team = row_bounds(image, (154, 203, 403, 231))
        name = row_bounds(image, (154, 231, 403, 276))
        score = row_bounds(image, (154, 276, 403, 348))
        # H03's measured wait-to-team separation is ~0.98% of the whole frame.
        self.assertGreaterEqual((team[0]-waits[1])/FRAME_HEIGHT, .007)
        self.assertLessEqual((team[0]-waits[1])/FRAME_HEIGHT, .013)
        self.assertGreaterEqual((name[0]-team[1])/FRAME_HEIGHT, .006)
        self.assertLessEqual((name[0]-team[1])/FRAME_HEIGHT, .016)
        self.assertGreaterEqual((score[0]-name[1])/FRAME_HEIGHT, .012)
        self.assertLessEqual((score[0]-name[1])/FRAME_HEIGHT, .026)
        self.assertGreaterEqual((waits[1]-waits[0])/FRAME_HEIGHT, .033)
        self.assertLessEqual((waits[1]-waits[0])/FRAME_HEIGHT, .043)
        self.assertGreaterEqual((name[1]-name[0])/FRAME_HEIGHT, .024)
        self.assertLessEqual((name[1]-name[0])/FRAME_HEIGHT, .032)

    def test_actual_default_white_glyphs_match_reference_information_spacing(self):
        self.assert_reference_information(render(self.model))

    def test_missing_team_leaves_empty_line_without_moving_photo_name_or_score(self):
        with_team = render(self.model)
        geometry = ml_card_geometry(self.player['name'], self.player['team'], '25,000',
                                    settings=self.model.settings)
        self.player['team'] = ''
        without_team = render(self.model)
        empty_geometry = ml_card_geometry(self.player['name'], '', '25,000',
                                          settings=self.model.settings)
        self.assertEqual(empty_geometry['identity'], geometry['identity'])
        self.assertEqual(empty_geometry['band'], geometry['band'])
        self.assertEqual(empty_geometry['labels']['name']['ink'], geometry['labels']['name']['ink'])
        self.assertEqual(image_bytes(with_team.copy(QRect(0, 231, 438, 117))),
                         image_bytes(without_team.copy(QRect(0, 231, 438, 117))))
        self.assertFalse(white_rows(without_team, (154, 203, 403, 231)))
        self.assertEqual(image_bytes(with_team.copy(QRect(26, 154, 120, 183))),
                         image_bytes(without_team.copy(QRect(26, 154, 120, 183))))

    def test_meld_visibility_does_not_rescale_or_reflow_the_normal_card(self):
        self.player['melds'] = [dict(id='spacing-pon', kind='pon', tiles=['5p']*3,
                                    calledIndex=0, **{'from': 1})]
        hidden = render(self.model)
        self.model.settings['show_melds'] = True
        shown = render(self.model)
        body = QRect(0, 145, 438, 203)
        self.assertEqual(image_bytes(hidden.copy(body)), image_bytes(shown.copy(body)))

    def test_ml_numeric_region_has_no_remaining_prefix_and_legacy_keeps_it(self):
        original = artwork.text
        for theme, has_prefix in (('mleague', False), ('saikouisen', True)):
            self.model.settings['theme'] = theme
            with self.subTest(theme=theme), patch('artwork.text', wraps=original) as draw_text:
                render(self.model)
                labels = [str(call.args[1]) for call in draw_text.call_args_list]
                self.assertEqual('余' in labels, has_prefix)
            art = Artwork(self.model, Animation())
            frame = art.remaining_frame(0, 3, art.created_at+20)
            digit = row_bounds(frame, (0, 0, frame.width(), frame.height()))
            if theme == 'mleague':
                self.assertGreaterEqual((digit[1]-digit[0])/FRAME_HEIGHT, .023)
                self.assertLessEqual((digit[1]-digit[0])/FRAME_HEIGHT, .033)

    def test_long_wait_list_never_enters_the_reserved_number_region(self):
        original = artwork.tile
        for length in (1, 7, 13):
            self.player['waits'] = ['1m','2m','3m','4m','5m','6m','7m',
                                    '8m','9m','1p','2p','3p','4p'][:length]
            self.player['remaining'] = 136
            regions = []
            def record(painter, code, x, y, scale=1., *args, **kwargs):
                regions.append(painter.worldTransform().mapRect(QRectF(x, y, 25*scale, 36*scale)))
                return original(painter, code, x, y, scale, *args, **kwargs)
            with self.subTest(waits=length), patch('artwork.tile', side_effect=record):
                image = render(self.model)
                self.assertEqual(len(regions), length)
                self.assertLessEqual(max(region.right() for region in regions), 340)
                self.assertTrue(white_rows(image, (345, 145, 403, 203)))

    def test_actual_active_session_copy_migrates_without_collapsing_missing_team(self):
        library = ROOT/'data/library.json'
        if not library.exists():
            self.skipTest('No attached local event library')
        active = json.loads(library.read_text(encoding='utf-8'))['active_id']
        source = ROOT/'data/events'/active/'session.json'
        if not source.exists():
            self.skipTest('No active event session')
        encoded = source.read_bytes()
        digest = hashlib.sha256(encoded).digest()
        target = Path(self.tmp.name)/'active-session-copy'
        target.mkdir()
        (target/'session.json').write_bytes(encoded)
        copied = Model(target, asset_root=target, preferences_root=target/'preferences')
        style = copied.settings['player_card_style']
        self.assertEqual(style['typography_version'], default_card_style()['typography_version'])
        stored_style = json.loads(encoded)['settings'].get('player_card_style', {})
        old_name = stored_style.get('text', {}).get('name', {})
        if (stored_style.get('typography_version', 1) <= 3 and
                old_name.get('size', 35) == 35 and not old_name.get('family', '')):
            self.assertEqual(style['text']['name']['size'], default_card_style()['text']['name']['size'])
        copied.state['phase'] = 'playing'
        player = copied.state['players'][0]
        player.update(tenpai=True, riichi=False, waits=['3m','6m'], remaining=3, melds=[])
        copied.settings.update(theme='mleague', show_melds=False)
        name = player['name']
        empty = ml_card_geometry(name, '', '25,000', settings=copied.settings)
        populated = ml_card_geometry(name, 'TEAM RAIDEN / 雷電', '25,000', settings=copied.settings)
        self.assertEqual(empty['identity'], populated['identity'])
        self.assertEqual(empty['labels']['name']['ink'], populated['labels']['name']['ink'])
        player['team'] = 'TEAM RAIDEN / 雷電'
        self.assert_reference_information(render(copied))
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), digest)


if __name__ == '__main__':
    unittest.main()
