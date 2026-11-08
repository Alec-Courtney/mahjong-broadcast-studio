"""Aligned ML backgrounds keep the complete signed score and rank visible."""
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from artwork import ml_card_geometry
from player_layout import PLAYER_LAYOUT

APP = QApplication.instance() or QApplication([])


class SignedScoreWidthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fonts = [QFontDatabase.addApplicationFont(str(Path('C:/Windows/Fonts')/name))
                     for name in ('msyh.ttc', 'msyhbd.ttc')]

    @classmethod
    def tearDownClass(cls):
        for identity in cls.fonts:
            if identity >= 0: QFontDatabase.removeApplicationFont(identity)

    def test_score_band_matches_identity_edges_for_both_card_sizes(self):
        for geometry in ({}, {'x': 142, 'width': 260, 'height': 165}):
            card = ml_card_geometry('选手', '队伍', '−13,600', 1, **geometry)
            self.assertEqual(card['band'].left(), card['identity'].left())
            self.assertEqual(card['band'].right(), card['identity'].right())
            self.assertLessEqual(PLAYER_LAYOUT.body_transform().mapRect(card['band']).right(),
                                 PLAYER_LAYOUT.canvas[0])

    def test_signed_score_ink_fits_in_the_band_with_or_without_rank(self):
        for value in ('−13,600', '−25,000', '−100,000', '−999,999', '−1,000,000,000'):
            for rank in (None, 1):
                with self.subTest(value=value, rank=rank):
                    card = ml_card_geometry('', '', value, rank)
                    score = card['labels']['score']
                    ink = score['ink'].boundingRect()
                    self.assertFalse(ink.isEmpty())
                    self.assertTrue(score['box'].adjusted(-.01, -.01, .01, .01).contains(ink))
                    self.assertGreaterEqual(card['band'].right()-ink.right(), 12)
                    if rank is not None:
                        self.assertGreaterEqual(ink.left()-card['labels']['rank']['ink'].boundingRect().right(), 8)


if __name__ == '__main__':
    unittest.main()
