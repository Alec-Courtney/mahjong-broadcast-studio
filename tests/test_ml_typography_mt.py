"""Actual glyph sizes and safe migration of broadcast player typography."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QImage, QPainter, QColor
from PySide6.QtWidgets import QApplication
from card_style import default_card_style, normalize_card_style
from artwork import ml_card_geometry, _card_glyph
from player_layout import PLAYER_LAYOUT
from model import Model

APP = QApplication.instance() or QApplication([])


def old_style(theme='mleague'):
    style = default_card_style(theme)
    style.pop('typography_version', None)
    old = ({'team': (14, False), 'name': (28, False), 'score': (47, False), 'rank': (25, False)}
           if theme == 'saikouisen' else
           {'team': (16, True), 'name': (29, True), 'score': (47, False), 'rank': (33, False)})
    style['team_name_gap'] = 3. if theme == 'saikouisen' else 6.
    for key, (size, bold) in old.items():
        style['text'][key].update(family='', size=size, letter_spacing=0., bold=bold)
    return style


class MLTypographyMTTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.font_ids = []
        for path in (Path('C:/Windows/Fonts/msyh.ttc'), Path('C:/Windows/Fonts/msyhbd.ttc')):
            if path.exists():cls.font_ids.append(QFontDatabase.addApplicationFont(str(path)))

    @classmethod
    def tearDownClass(cls):
        for identity in cls.font_ids:
            if identity >= 0:QFontDatabase.removeApplicationFont(identity)

    def test_old_default_metrics_upgrade_without_mutating_input(self):
        for theme in ('mleague', 'saikouisen'):
            with self.subTest(theme=theme):
                old = old_style(theme)
                before = copy.deepcopy(old)
                upgraded = normalize_card_style(old, theme)
                self.assertEqual(upgraded, default_card_style(theme))
                self.assertEqual(old, before)
                self.assertEqual(normalize_card_style(upgraded, theme), upgraded)

    def test_custom_metrics_and_paints_survive_each_role_migration(self):
        for theme in ('mleague', 'saikouisen'):
            for field, custom in (('family', 'Arial'), ('size', 42),
                                  ('letter_spacing', 2.5), ('bold', not old_style(theme)['text']['score']['bold'])):
                with self.subTest(theme=theme, field=field):
                    old = old_style(theme)
                    old['text']['score'][field] = custom
                    old['text']['name']['paint']['start'] = '#FF22AA88'
                    old['backgrounds']['score']['start'] = '#80334455'
                    old['team_name_gap'] = 9.
                    expected = copy.deepcopy(old['text']['score'])
                    result = normalize_card_style(old, theme)
                    self.assertEqual(result['text']['score'], expected)
                    self.assertEqual(result['text']['name']['paint']['start'], '#FF22AA88')
                    self.assertEqual(result['backgrounds']['score']['start'], '#80334455')
                    self.assertEqual(result['team_name_gap'], 9.)

    def test_ml_name_growth_and_score_inner_padding_are_kept_separate(self):
        old = old_style();old['typography_version'] = 3
        old_card = ml_card_geometry('園田賢', '赤坂ドリブンズ', '30,000',
                                    settings={'player_card_style': old})
        card = ml_card_geometry('園田賢', '赤坂ドリブンズ', '30,000')
        score = card['labels']['score']['ink'].boundingRect()
        self.assertTrue(card['style']['text']['score']['bold'])
        self.assertGreaterEqual(score.top()-card['band'].top(), 6.)
        self.assertGreaterEqual(card['band'].bottom()-score.bottom(), 6.)
        self.assertGreaterEqual(card['band'].right()-score.right(), 10.)
        for key in ('team', 'name'):
            before = old_card['labels'][key]['ink'].boundingRect()
            after = card['labels'][key]['ink'].boundingRect()
            self.assertGreater(after.width(), before.width()*1.12)
            self.assertGreater(after.height(), before.height()*1.08)

    def test_actual_long_profiles_and_extreme_scores_stay_in_fixed_regions(self):
        for scale in (1., 1.6):
            for name, team in (('伊達朱里紗', 'KONAMI麻雀格闘倶楽部'),
                               ('岡田紗佳', 'KADOKAWAサクラナイツ'),
                               ('鈴木優', 'U-NEXT Pirates'),
                               ('International Player', 'An unusually long international team name')):
                for score, rank in (('30,000', None), ('−1,200', None), ('123,400', None),
                                    ('+56.7', 1), ('−999.9', 4)):
                    with self.subTest(scale=scale, name=name, score=score):
                        card = ml_card_geometry(name, team, score, rank, settings={'overlay_font_scale': scale})
                        labels = card['labels']
                        for key in ('team', 'name'):
                            ink = labels[key]['ink'].boundingRect()
                            self.assertTrue(card['identity'].adjusted(-.01, -.01, .01, .01).contains(ink))
                            # The status row already contains padding around
                            # its tiles; the identity ink must stay below it.
                            self.assertGreaterEqual(ink.top(), PLAYER_LAYOUT.status_y+PLAYER_LAYOUT.status_height-.01)
                        for key in ('score', 'rank'):
                            if key in labels:
                                self.assertTrue(labels[key]['box'].adjusted(-.01, -.01, .01, .01)
                                                .contains(labels[key]['ink'].boundingRect()))

    def test_saved_custom_ml_and_legacy_fonts_roundtrip_with_upgrade(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
            model = Model(directory)
            old = old_style();old['text']['team'].update(size=23, letter_spacing=2.)
            model.settings['player_card_style'] = old
            model.settings['saikouisen_card_style'] = old_style('saikouisen')
            model.save()
            restored = Model(directory)
            ml = restored.settings['player_card_style']
            self.assertEqual(ml['text']['team']['size'], 23)
            self.assertEqual(ml['text']['team']['letter_spacing'], 2.)
            self.assertEqual(ml['text']['score'], default_card_style()['text']['score'])
            self.assertEqual(restored.settings['saikouisen_card_style'], default_card_style('saikouisen'))

    def test_v2_score_default_relaxes_but_custom_v2_and_saikouisen_are_preserved(self):
        previous = default_card_style()
        previous['typography_version'] = 2
        previous['text']['score'].update(size=58, bold=True, letter_spacing=1.2)
        upgraded = normalize_card_style(previous)
        self.assertEqual(upgraded, default_card_style())
        self.assertEqual(previous['text']['score']['size'], 58)
        for field, value in (('family', 'Arial'), ('size', 53), ('bold', False), ('letter_spacing', .8)):
            custom = copy.deepcopy(previous);custom['text']['score'][field] = value
            self.assertEqual(normalize_card_style(custom)['text']['score'], custom['text']['score'])
        highest = default_card_style('saikouisen')
        self.assertEqual(normalize_card_style(highest, 'saikouisen'), highest)
        self.assertTrue(highest['text']['score']['bold'])

    def test_previous_ml_identity_defaults_upgrade_once_through_each_version(self):
        for version in (1, 2, 3):
            with self.subTest(version=version):
                previous = old_style() if version == 1 else default_card_style()
                previous['typography_version'] = version
                if version > 1:
                    previous['team_name_gap'] = 4.
                    previous['text']['name'].update(size=35, letter_spacing=1.2, bold=True)
                if version == 2:
                    previous['text']['score'].update(size=58, letter_spacing=1.2, bold=True)
                original = copy.deepcopy(previous)
                upgraded = normalize_card_style(previous)
                self.assertEqual(upgraded, default_card_style())
                self.assertEqual(upgraded['typography_version'], 5)
                self.assertEqual(previous, original)
                self.assertEqual(normalize_card_style(upgraded), upgraded)

    def test_ml_identity_upgrade_preserves_edited_name_metrics_other_roles_and_paints(self):
        for version in (1, 2, 3):
            for field, custom in (('family', 'Arial'), ('size', 37),
                                  ('letter_spacing', 2.1), ('bold', False)):
                with self.subTest(version=version, field=field):
                    previous = old_style() if version == 1 else default_card_style()
                    previous['typography_version'] = version
                    if version > 1:
                        previous['team_name_gap'] = 4.
                        previous['text']['name'].update(size=35, letter_spacing=1.2, bold=True)
                    previous['text']['name'][field] = custom
                    previous['text']['name']['paint']['start'] = '#FF22AA88'
                    previous['text']['team'].update(size=23, letter_spacing=2.)
                    previous['text']['score'].update(size=61, bold=False, letter_spacing=.8)
                    previous['text']['rank'].update(size=39, bold=False)
                    previous['backgrounds']['identity']['end'] = '#88112233'
                    previous['team_name_gap'] = 9.
                    upgraded = normalize_card_style(previous)
                    for key in ('team', 'name', 'score', 'rank'):
                        self.assertEqual(upgraded['text'][key], previous['text'][key])
                    self.assertEqual(upgraded['team_name_gap'], 9.)
                    self.assertEqual(upgraded['backgrounds'], previous['backgrounds'])

    def test_ml_identity_upgrade_changes_metrics_but_keeps_paint_and_custom_gap(self):
        for gap in (0., 6., 9., 15.):
            with self.subTest(gap=gap):
                previous = default_card_style()
                previous['typography_version'] = 3
                previous['text']['name']['size'] = 35
                previous['text']['name']['paint']['start'] = '#FF22AA88'
                previous['team_name_gap'] = gap
                upgraded = normalize_card_style(previous)
                self.assertEqual(upgraded['text']['name']['size'], 33)
                self.assertEqual(upgraded['text']['name']['paint'], previous['text']['name']['paint'])
                self.assertEqual(upgraded['team_name_gap'], gap)

    def test_explicit_current_ml_style_and_saikouisen_keep_their_metrics(self):
        current = default_card_style()
        current['text']['name']['size'] = 35
        current['team_name_gap'] = 4.
        self.assertEqual(normalize_card_style(current), current)
        highest = default_card_style('saikouisen')
        self.assertEqual(highest['typography_version'], 2)
        self.assertEqual(highest['team_name_gap'], 3.)
        self.assertEqual(highest['text']['name']['size'], 28)
        self.assertEqual(normalize_card_style(highest, 'saikouisen'), highest)

    def test_previous_default_score_weight_upgrades_without_changing_paint_or_size(self):
        for version in (1, 2, 3, 4):
            with self.subTest(version=version):
                previous = default_card_style()
                previous['typography_version'] = version
                previous['text']['score']['bold'] = False
                previous['text']['score']['paint']['start'] = '#FF22AA88'
                original = copy.deepcopy(previous)
                upgraded = normalize_card_style(previous)
                expected = dict(previous['text']['score'], bold=True)
                self.assertEqual(upgraded['text']['score'], expected)
                self.assertEqual(upgraded['typography_version'], 5)
                self.assertEqual(previous, original)
                self.assertEqual(normalize_card_style(upgraded), upgraded)

    def test_score_weight_upgrade_preserves_custom_metrics_and_current_explicit_regular(self):
        for version in (1, 2, 3, 4):
            for field, value in (('family', 'Arial'), ('size', 53), ('letter_spacing', .8)):
                with self.subTest(version=version, field=field):
                    previous = default_card_style()
                    previous['typography_version'] = version
                    previous['text']['score'].update(bold=False)
                    previous['text']['score'][field] = value
                    self.assertEqual(normalize_card_style(previous)['text']['score'], previous['text']['score'])
        regular = default_card_style()
        regular['text']['score']['bold'] = False
        self.assertEqual(normalize_card_style(regular), regular)
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
            model = Model(directory)
            model.settings['player_card_style'] = regular
            model.save()
            self.assertEqual(Model(directory).settings['player_card_style'], regular)

    def test_default_score_digits_have_more_actual_ink_without_growing_the_band(self):
        bold = default_card_style()
        regular = copy.deepcopy(bold)
        regular['text']['score']['bold'] = False
        labels = []
        ink_pixels = []
        for style in (regular, bold):
            card = ml_card_geometry('', '', '11,111', settings={'player_card_style': style})
            label = card['labels']['score']
            labels.append((card['band'], label['box'], label['ink'].boundingRect()))
            image = QImage(438, 340, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.fillPath(label['ink'], QColor('#ffffff'))
            painter.end()
            ink_pixels.append(sum(alpha >= 128 for alpha in bytes(image.constBits())[3::4]))
        self.assertEqual(labels[0][0], labels[1][0])
        self.assertEqual(labels[0][1], labels[1][1])
        self.assertAlmostEqual(labels[0][2].height(), labels[1][2].height(), delta=2.)
        self.assertGreater(ink_pixels[1], ink_pixels[0]*1.15)

    def numeric_rows(self, label):
        image = QImage(480, 340, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillPath(label['ink'], QColor('#ffffff'));painter.end()
        ink = label['ink'].boundingRect()
        right = int(ink.right())
        return tuple(y for y in range(max(0, int(ink.top())-1), min(340, int(ink.bottom())+2))
                     if any(image.pixelColor(x, y).alpha() > 100 for x in range(right-15, right+1)))

    def test_signed_two_and_three_digit_pt_keep_actual_last_digit_height_with_or_without_rank(self):
        values = ('+56.7', '−56.7', '+156.7', '−156.7', '+999.7', '−999.7')
        for scale in (1., 1.6):
            rows = []
            for rank in (None, 1, 12, 123):
                for value in values:
                    card = ml_card_geometry('', '', value, rank, settings={'overlay_font_scale': scale})
                    rows.append(self.numeric_rows(card['labels']['score']))
            self.assertTrue(rows[0])
            self.assertEqual(len(set(rows)), 1, 'PT numeral size changed with value length or rank presence')

    def test_one_two_and_three_digit_ranks_keep_same_kanji_height(self):
        for scale in (1., 1.6):
            rows = [self.numeric_rows(ml_card_geometry('', '', '+156.7', rank,
                    settings={'overlay_font_scale': scale})['labels']['rank']) for rank in (1, 12, 123)]
            self.assertTrue(rows[0])
            self.assertEqual(rows[0], rows[1])
            self.assertEqual(rows[0], rows[2])

    def test_pt_paths_have_clear_band_and_rank_gaps_for_all_signs_and_lengths(self):
        for rank in (None, 1, 12, 123):
            for value in ('30,000', '−1,200', '+56.7', '−56.7', '+156.7', '−156.7'):
                card = ml_card_geometry('園田賢', '赤坂ドリブンズ', value, rank)
                score = card['labels']['score']['ink'].boundingRect()
                self.assertGreaterEqual(score.top()-card['band'].top(), 6.)
                self.assertGreaterEqual(card['band'].bottom()-score.bottom(), 6.)
                self.assertGreaterEqual(card['band'].right()-score.right(), 10.)
                if rank is not None:
                    rank_ink = card['labels']['rank']['ink'].boundingRect()
                    self.assertGreaterEqual(score.left()-rank_ink.right(), 8.)


if __name__ == '__main__':unittest.main()
