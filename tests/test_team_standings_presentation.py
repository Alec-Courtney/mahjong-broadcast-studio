"""Team-colour reports and fixed current-four sidebars use the booked ledger."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from xml.etree import ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QTableWidgetItem
from model import Model
from event_store import EventStore
import broadcast_artwork as art
import ranking_reports as reports
import tournament

APP = QApplication.instance() or QApplication([])
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
COLORS = ['#b83b56', '#2866bd', '#3c8a5a', '#d0aa26', '#8652ab', '#238d91',
          '#cb7136', '#627b93', '#8b943f', '#b06292']


class TeamStandingsPresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'tests')
        self.model = Model(self.tmp.name); self.store = EventStore(self.model)
        self.store.create('团队赛榜单测试','team_league')
        roster = []
        for index in range(20):
            team = index//2
            roster.append(dict(id=f'player-{index}', name=f'选手{index+1:02d}', seed=index+1,
                team=('同名队' if team in (0, 1) else f'队伍{team+1}') if index<18 else '',
                team_id=f'club-{team}' if index<18 else '', color=COLORS[team],
                base_points=600-index*75., games=0))
        self.model.configure_broadcast(dict(roster=roster,
            seats=[roster[index]['id'] for index in (12, 14, 16, 19)],
            sidebar_page=7, sidebar_page_size=2, sidebar_scope='current', event_mode='team'))
        self.store.update(name='团队赛榜单测试',
            report_columns={'individual': ['rank', 'name', 'team', 'points'],
                            'team': ['rank', 'team', 'points']})

    def tearDown(self):
        APP.processEvents(); self.tmp.cleanup()

    def finish(self):
        self.model.start_match()
        while self.model.state['phase'] != 'finished':
            if self.model.state['phase'] == 'result':self.model.next_hand()
            winner = (self.model.state['dealer']+1)%4
            self.model.settle(dict(type='ron', winner=winner, loser=(winner+1)%4, han=3, fu=30), None)
        self.model.commit_match()

    def sidebar(self, mode, rows=None):
        frame = QImage(400, 650, QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(Qt.GlobalColor.transparent); painter = QPainter(frame); captions = []
        original = art._text
        def capture(*args, **kwargs):
            result = original(*args, **kwargs)
            captions.append((result.value if result is not None else str(args[1]), args[2:6]));return result
        try:
            with patch.object(art, '_text', side_effect=capture):
                art.draw_team_sidebar(painter, self.model, mode=mode, rows=rows)
        finally:painter.end()
        return frame, captions

    def test_current_four_keep_global_ranks_above_ten_and_cumulative_points(self):
        self.finish()
        all_rows = tournament.standings(self.model.state, 'individual')
        ids = {player['id'] for player in self.model.state['players']}
        expected = [row for row in all_rows if row['id'] in ids]
        self.assertEqual(len(expected), 4)
        self.assertTrue(all(row['rank']>10 for row in expected))
        image, captions = self.sidebar('individual')
        names = [value for value, rect in captions if rect[0]==68 and rect[3]==49]
        self.assertEqual(names, [row['name'] for row in expected])
        ranks = [value for value, rect in captions if rect[0]==16 and rect[3]==49]
        self.assertEqual(ranks, [str(row['rank']) for row in expected])
        points = [value for value, rect in captions if rect[0]==256 and rect[3]==49]
        self.assertEqual(points, [art._point(row['points'], True) for row in expected])
        self.assertFalse(any('/' in value for value, rect in captions))
        self.assertEqual(image.height(), 650)

    def test_unfinished_and_early_confirmed_half_do_not_enter_sidebar(self):
        before = tournament.standings(self.model.state)
        self.model.start_match(); self.model.riichi(1)
        self.model.settle(dict(type='ron', winner=1, loser=0, han=3, fu=30), None)
        self.model.commit_match()
        self.assertEqual(tournament.standings(self.model.state), before)
        rows = {row['id']:row for row in reports.individual_rows(self.model.state, self.model.event)}
        self.assertEqual(rows['player-14']['points'], before[14]['points'])
        _, captions = self.sidebar('individual')
        self.assertIn(art._point(rows['player-14']['points'], True), [value for value, rect in captions])

    def test_rotation_changes_current_four_but_preserves_full_field_numbers(self):
        self.finish()
        first = reports.individual_rows(self.model.state, self.model.event)
        seats = ['player-0', 'player-3', 'player-6', 'player-18']
        self.model.matches.start_next(); self.model.configure_broadcast({'seats': seats})
        _, captions = self.sidebar('individual')
        expected = [row for row in first if row['id'] in seats]
        self.assertEqual([value for value, rect in captions if rect[0]==68 and rect[3]==49],
                         [row['name'] for row in expected])
        for row in expected:self.assertIn(art._point(row['points'], True), [value for value, rect in captions])
        self.assertEqual(reports.individual_rows(self.model.state, self.model.event), first)

    def test_team_sidebar_has_uniform_dark_rows_and_current_red_without_team_stripes(self):
        rows = tournament.standings(self.model.state, 'team')
        self.model.state['broadcast']['config'].update(sidebar_page=0, sidebar_page_size=10, sidebar_scope='all')
        image, _ = self.sidebar('team', rows)
        current = art._current_team_ids(self.model)
        for index, row in enumerate(rows[:10]):
            y = 86+index*52
            reference = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
            reference.fill(Qt.GlobalColor.transparent); paint = QPainter(reference)
            paint.fillRect(0, 0, 1, 1, QColor('#b8081713'))
            paint.fillRect(0, 0, 1, 1, QColor('#b7a6242a' if row['id'] in current else '#bc101c19'));paint.end()
            for x in (2, 8, 390):
                self.assertEqual(image.pixelColor(x, y+3), reference.pixelColor(0, 0))
        duplicate = [row for row in rows if row['name']=='同名队']
        self.assertEqual(len(duplicate), 2)
        self.assertNotEqual(duplicate[0]['id'], duplicate[1]['id'])
        self.assertNotEqual(reports.team_row_fill(duplicate[0]), reports.team_row_fill(duplicate[1]))
        reversed_image, _ = self.sidebar('team', list(reversed(rows)))
        for index, row in enumerate(list(reversed(rows))[:10]):
            color = reversed_image.pixelColor(2, 86+index*52+3)
            self.assertEqual(color, reversed_image.pixelColor(390, 86+index*52+3))

    def test_personal_sidebar_has_no_team_background_or_color_stripes(self):
        config = self.model.state['broadcast']['config']
        config.update(sidebar_scope='all', sidebar_page=0, sidebar_page_size=10)
        image, _ = self.sidebar('individual')
        # None of these top10 are on the current table; their different team
        # colours must produce the exact same plain dark row and left margin.
        colors = [image.pixelColor(9, 86+index*52+1) for index in range(10)]
        self.assertEqual(len({color.rgba() for color in colors}), 1)
        for index in range(10):self.assertEqual(colors[index], image.pixelColor(390, 86+index*52+1))

    def test_unassigned_and_invalid_colors_are_neutral_without_merging_people(self):
        rows = reports.team_rows(self.model.state, self.model.event)
        unassigned = [row for row in rows if row['id'].startswith('unassigned:')]
        self.assertEqual(len(unassigned), 2)
        self.assertEqual({reports.row_team_color(row) for row in unassigned}, {'#66757f'})
        self.assertEqual(reports.row_team_color(dict(id='bad', team='某队', color='bad color')), '#66757f')
        self.assertEqual(reports.row_team_color(dict(id='none')), '#66757f')

    def test_signed_three_digit_points_and_long_names_are_complete(self):
        self.model.state['broadcast']['roster'][12]['name'] = '这是一位需要完整显示姓名的参赛选手'
        for index, value in zip((12, 14, 16, 19), (999.9, -999.9, 123.4, -123.4)):
            self.model.state['broadcast']['roster'][index]['base_points'] = value
        self.model.state['players'][0]['name'] = self.model.state['broadcast']['roster'][12]['name']
        before = copy.deepcopy(self.model.state)
        _, captions = self.sidebar('individual')
        values = [value for value, rect in captions]
        self.assertIn('这是一位需要完整显示姓名的参赛选手', values)
        self.assertTrue(all('…' not in value for value in values))
        for points in ('+999.9', '−999.9', '+123.4', '−123.4'):self.assertIn(points, values)
        self.assertEqual(self.model.state, before)

    def test_full_personal_pages_render_every_entrant_once_with_original_ranks(self):
        self.finish()
        config = self.model.state['broadcast']['config']
        config.update(sidebar_scope='all', sidebar_page_size=10, sidebar_auto_page=True)
        expected = tournament.standings(self.model.state)
        actual_names, actual_ranks = [], []
        before = copy.deepcopy(self.model.state)
        for page in range(2):
            config['sidebar_page'] = page
            image, captions = self.sidebar('individual')
            actual_names.extend(value for value, rect in captions if rect[0]==68 and rect[3]==49)
            actual_ranks.extend(int(value) for value, rect in captions if rect[0]==16 and rect[3]==49)
            self.assertIn(f'{page+1}/2', [value for value, rect in captions])
            self.assertEqual(image.pixelColor(390, 640).alpha(), 243)
        self.assertEqual(actual_names, [row['name'] for row in expected])
        self.assertEqual(actual_ranks, list(range(1, 21)))
        before['broadcast']['config']['sidebar_page'] = 1
        self.assertEqual(self.model.state, before)

    def test_png_and_operator_table_use_the_same_whole_row_team_tint(self):
        self.finish()
        for mode in ('individual', 'team'):
            data = reports.report(self.model.state, self.model.event, mode)
            image = reports.render_report(data)
            for index, row in enumerate(data['rows']):
                expected = reports.report_row_fill(data, row, index)
                # Short fixture names keep all headers at42px and rows at44px.
                for x in (52, 100, 240, 350):
                    self.assertEqual(image.pixelColor(x, 124+index*44+2).name(), expected)
                for key in data['columns']:
                    cell = QTableWidgetItem(); reports.style_table_item(cell, data, row, key, index)
                    self.assertEqual(cell.background().color().name(), expected)
                    negative = key=='points' and row['points']<0
                    self.assertEqual(cell.foreground().color().name(), '#b64444' if negative else '#203b34')

    def test_xlsx_colors_all_cells_and_preserves_numeric_ledgers(self):
        self.finish(); target = Path(self.tmp.name)/'standings.xlsx'
        reports.export_xlsx(self.model.state, self.model.event, target)
        with zipfile.ZipFile(target) as archive:
            self.assertIsNone(archive.testzip())
            styles = ET.fromstring(archive.read('xl/styles.xml'))
            fills = styles.findall('s:fills/s:fill', NS)
            xfs = styles.findall('s:cellXfs/s:xf', NS)
            for number, mode in enumerate(('individual', 'team'), 1):
                data = reports.report(self.model.state, self.model.event, mode)
                sheet = ET.fromstring(archive.read(f'xl/worksheets/sheet{number}.xml'))
                for index, row in enumerate(data['rows']):
                    cells = sheet.findall(f'.//s:row[@r="{index+5}"]/s:c', NS)
                    actual = []
                    for cell in cells:
                        fill = fills[int(xfs[int(cell.attrib['s'])].attrib['fillId'])]
                        actual.append(fill.find('s:patternFill/s:fgColor', NS).attrib['rgb'])
                    self.assertEqual(set(actual), {'FF'+reports.report_row_fill(data, row, index)[1:].upper()})
                    point = cells[data['columns'].index('points')]
                    self.assertAlmostEqual(float(point.find('s:v', NS).text), row['points'])
                    self.assertNotIn('t', point.attrib)


if __name__ == '__main__':unittest.main()
