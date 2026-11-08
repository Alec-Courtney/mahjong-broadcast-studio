"""Stage destinations and four-seat qualification presentation regressions."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from model import Model
from qualification_presentation import (automatic_qualification_title,
    qualification_data, qualification_panel_size, qualification_titles,
    FOUR_PANEL_SIZE, MT_CARD_WIDTH, MT_CARD_GAP)

APP = QApplication.instance() or QApplication([])


class QualificationPresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'tests')
        self.model = Model(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def stage(self, current_round, rounds=5, planned=2):
        nodes = []
        previous = None
        for number in range(1, rounds+1):
            identity = f'r{number}'
            sources = [{'node': previous, 'place': 1}] if previous else []
            nodes.append(dict(id=identity, round=number, sources=sources,
                              advance_count=2, planned_games=planned))
            previous = identity
        self.model.event = {'bracket': {'nodes': nodes, 'active_node': f'r{current_round}'}}
        self.model.state['broadcast']['active_node_id'] = f'r{current_round}'

    def test_titles_name_the_next_stage_and_championship(self):
        for stage, title in ((1, '第2轮晋级条件'), (2, '第3轮晋级条件'),
                             (3, '半决赛晋级条件'), (4, '决赛晋级条件'),
                             (5, '冠军条件')):
            with self.subTest(stage=stage):
                self.stage(stage)
                self.assertEqual(automatic_qualification_title(self.model), title)

    def test_per_player_titles_override_global_and_keep_names_literal(self):
        self.stage(4)
        config = self.model.state['broadcast']['config']
        config.update(qualification_title='本组复活条件',
                      qualification_titles={'p1': '第一轮晋级条件', 'p2': '  '})
        titles = qualification_titles(self.model)
        self.assertEqual(titles['p1'], '第一轮晋级条件')
        self.assertEqual(titles['p2'], '本组复活条件')
        config['qualification_title'] = ''
        self.assertEqual(qualification_titles(self.model)['p3'], '决赛晋级条件')

    def test_old_schedule_selection_does_not_relabel_a_free_table(self):
        self.stage(5)
        self.model.state['broadcast'].pop('active_node_id')
        self.assertEqual(automatic_qualification_title(self.model), '晋级条件')
        data = qualification_data(self.model)
        self.assertEqual(sum(row['status'] == 'inside' for row in data['rows']), 2)

    def test_championship_uses_first_place_without_changing_event_rules(self):
        self.stage(5)
        before = copy.deepcopy(self.model.state)
        data = qualification_data(self.model)
        self.assertEqual(sum(row['status'] == 'inside' for row in data['rows']), 1)
        self.assertTrue(all(row['title'] == '冠军条件' for row in data['rows']))
        self.assertEqual(self.model.state, before)
        self.assertIn('尚非本轮最后半庄', data['display_note'])

    def test_four_seats_have_independent_all_source_conditions(self):
        self.stage(4, planned=1)
        self.model.state['players'].reverse()
        data = qualification_data(self.model)
        self.assertEqual([row['id'] for row in data['rows']], ['p4', 'p3', 'p2', 'p1'])
        for row in data['rows']:
            self.assertEqual(len(row['ron']), 3)
            self.assertEqual({entry['name'] for entry in row['ron']},
                             {p['name'] for p in self.model.state['players'] if p['id'] != row['id']})
        self.assertIn('本局结束试算', data['display_note'])

    def test_four_manual_cards_preserve_each_entered_condition(self):
        config = self.model.state['broadcast']['config']
        conditions = {p['id']: f'条件 {index}：流局听牌或对手放铳'
                      for index, p in enumerate(self.model.state['players'])}
        config.update(qualification_calculation='manual', manual_conditions=conditions)
        data = qualification_data(self.model)
        self.assertEqual({row['id']: row['condition'] for row in data['rows']}, conditions)
        self.assertEqual(data['display_note'], '手动条件')

    def test_fourth_card_is_rendered_and_gaps_stay_transparent(self):
        from broadcast_artwork import draw_extension
        frame = QImage(1920,1080, QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(Qt.GlobalColor.transparent)
        painter = QPainter(frame)
        draw_extension(painter, 'four_conditions', self.model)
        painter.end()
        for index in range(4):
            x = 60+index*(MT_CARD_WIDTH+MT_CARD_GAP)+10
            self.assertEqual(frame.pixelColor(x, 204+FOUR_PANEL_SIZE[1]-5).alpha(), 180)
        for index in range(3):
            x = 60+index*(MT_CARD_WIDTH+MT_CARD_GAP)+MT_CARD_WIDTH+MT_CARD_GAP//2
            self.assertEqual(frame.pixelColor(x, 204+FOUR_PANEL_SIZE[1]-5).alpha(), 0)
        self.assertEqual(frame.pixelColor(10,10).alpha(),0)
        self.assertEqual(qualification_panel_size(self.model), (400,650))
        self.model.state['broadcast']['config']['sidebar_mode'] = 'team'
        self.assertEqual(qualification_panel_size(self.model), (400, 650))

    def test_single_conditions_default_to_dealer_and_follow_explicit_focus(self):
        from broadcast_artwork import draw_extension
        self.model.state['dealer']=2
        for focus,expected in (('',self.model.state['players'][2]['id']),('p4','p4')):
            self.model.state['broadcast']['config']['qualification_focus']=focus
            frame=QImage(400,650,QImage.Format.Format_ARGB32_Premultiplied);frame.fill(Qt.GlobalColor.transparent)
            painter=QPainter(frame)
            try:
                with patch('broadcast_artwork._focused_qualification') as draw:
                    draw_extension(painter,'qualification',self.model)
                    self.assertEqual(draw.call_args.args[2]['id'],expected)
            finally:painter.end()


if __name__ == '__main__':
    unittest.main()
