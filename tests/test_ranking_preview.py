"""The large preview stays in sync with the ledger without changing it."""
import copy
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget
from event_store import EventStore
from event_workspace import EventWorkspaceDialog
from model import Model
from panel_typography import draw_single_line
import ranking_reports as reports

APP = QApplication.instance() or QApplication([])


class RankingPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tmp-report-preview-', dir=ROOT/'tests')
        self.model = Model(self.tmp.name)
        self.store = EventStore(self.model)
        self.store.create('彩色预览测试', 'team_league')
        roster = [dict(id=f'p-{index}', name=f'选手{index+1:02}', seed=index+1,
                       team=f'队伍{index//4+1}', team_id=f'team-{index//4}',
                       color=['#b83b56', '#2866bd', '#3c8a5a'][index//4 % 3],
                       base_points=90.-index*5, games=0) for index in range(70)]
        self.model.configure_broadcast({'roster': roster, 'seats': [p['id'] for p in roster[:4]]})
        self.store.update(report_columns={'individual': ['rank', 'name', 'team', 'points'],
                                          'team': ['rank', 'team', 'points', 'cutoff_gap']})
        self.main = QWidget()
        self.main.model = self.model
        self.main.manager = SimpleNamespace(save_geometry=lambda: None)
        self.workspace = EventWorkspaceDialog(self.main)
        self.workspace.report_preview_button.click()
        self.preview = self.workspace.report_preview
        self.preview.resize(1250, 850)
        APP.processEvents()

    def tearDown(self):
        self.workspace._allow_close = True
        self.workspace.close()
        self.workspace.deleteLater()
        self.main.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.tmp.cleanup()

    def test_paged_colour_preview_matches_export_and_never_changes_scores(self):
        before = copy.deepcopy((self.model.state, self.model.settings, self.model.event))
        self.preview.next.click()
        self.assertEqual(self.workspace.report_page, 2)
        self.assertIn('2 / 2', self.workspace.report_page_label.text())
        self.assertIn('2 / 2', self.preview.page_label.text())
        self.assertEqual(self.preview.image, reports.render_report(self.workspace.report_data, 36, 36))
        exported = reports.export_png(self.workspace.report_data, Path(self.tmp.name)/'report.png')
        self.assertEqual(exported, [Path(self.tmp.name)/'report.png'])
        image = QImage(str(exported[0])).convertToFormat(self.preview.image.format())
        self.assertEqual(image, reports.render_report(self.workspace.report_data, limit=None))
        self.assertGreater(image.height(), reports.render_report(self.workspace.report_data).height())
        self.assertEqual(list(Path(self.tmp.name).glob('report*.png')), exported)
        self.preview.set_zoom(150)
        self.assertEqual(self.preview.canvas.width(), round(self.preview.image.width()*1.5))
        self.assertEqual(self.preview.canvas.height(), round(self.preview.image.height()*1.5))
        self.assertEqual((self.model.state, self.model.settings, self.model.event), before)

    def test_team_switch_clamps_pages_and_preserves_export_palette(self):
        self.workspace.report_next.click()
        self.assertEqual(self.preview.rendered_page, 2)
        self.preview.mode_requested.emit('team')
        self.assertEqual(self.workspace.report_mode.currentData(), 'team')
        self.assertEqual(self.preview.mode.currentData(), 'team')
        self.assertIn('1 / 1', self.preview.page_label.text())
        self.assertEqual(self.preview.rendered_page, 1)
        self.assertFalse(self.preview.previous.isEnabled())
        self.assertFalse(self.preview.next.isEnabled())
        self.assertEqual(self.preview.image, reports.render_report(self.workspace.report_data))
        fills = {reports.report_row_fill(self.workspace.report_data, row, index)
                 for index, row in enumerate(self.workspace.report_data['rows'])}
        self.assertGreater(len(fills), 1)
        self.preview.fit('width')
        self.assertLessEqual(self.preview.canvas.width(), self.preview.scroll.viewport().width()+10)
        self.preview.set_zoom(100)
        self.assertEqual(self.preview.canvas.size(), self.preview.image.size())

    def test_names_are_centered_in_both_colour_previews(self):
        for mode in ('individual', 'team'):
            with self.subTest(mode=mode):
                data = reports.report(self.model.state, self.model.event, mode)
                entry = data['rows'][0]
                labels = [entry[key] for key in ('name', 'team') if key in data['columns']]
                with patch('panel_typography.draw_single_line', wraps=draw_single_line) as draw:
                    reports.render_report(data, limit=1)
                for label in labels:
                    calls = [call for call in draw.call_args_list if call.args[1] == label]
                    self.assertTrue(calls)
                    self.assertTrue(all(call.args[4] & Qt.AlignmentFlag.AlignHCenter for call in calls))

    def test_confirmed_refresh_and_event_switch_reuse_the_existing_preview(self):
        self.model.state['broadcast']['roster'][0]['base_points'] = 999
        self.preview.refresh_requested.emit()
        self.assertEqual(self.preview.data['rows'][0]['points'], 999)
        self.workspace.preview_report()
        self.assertIs(self.workspace.report_preview, self.preview)
        self.store.create('下一场个人赛', 'individual_league')
        APP.processEvents()
        self.assertEqual(self.preview.mode.count(), 1)
        self.assertEqual(self.preview.data['event']['name'], '下一场个人赛')
        self.assertIn('1 / 1', self.preview.page_label.text())
        self.assertEqual(self.preview.image, reports.render_report(self.workspace.report_data))

    def test_team_quota_does_not_highlight_individual_places(self):
        event = copy.deepcopy(self.model.event)
        event['qualification_count'] = 2
        data = reports.report(self.model.state, event, 'individual')
        for index, row in enumerate(data['rows']):
            self.assertEqual(reports.report_row_fill(data, row, index), reports.team_row_fill(row))

    def test_visible_preview_refreshes_when_another_workspace_page_is_open(self):
        self.workspace.show()
        self.workspace.nav.setCurrentRow(2)
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        roster[0]['name'] = '修改后的选手姓名'
        self.model.set_roster(roster)
        APP.processEvents()
        self.assertEqual(self.workspace.pages.currentIndex(), 2)
        self.assertTrue(self.preview.isVisible())
        self.assertEqual(self.preview.data['rows'][0]['name'], '修改后的选手姓名')
        self.workspace.nav.setCurrentRow(1)
        self.store.update(name='修改后的赛事名称')
        APP.processEvents()
        self.assertEqual(self.preview.data['event']['name'], '修改后的赛事名称')
        self.assertEqual(self.preview.image, reports.render_report(self.workspace.report_data, 0, 36))

    def test_preview_reuses_the_report_until_its_saved_inputs_change(self):
        with patch.object(self.workspace,'populate_report_table',wraps=self.workspace.populate_report_table) as populate, \
             patch.object(reports,'render_report',wraps=reports.render_report) as render:
            self.workspace.preview_report()
            self.model.display_changed.emit()
            self.model.set_score(0,26000)
            populate.assert_not_called()
            render.assert_not_called()
            self.store.update(name='已保存的新赛事名称')
            populate.assert_called_once()
            render.assert_called_once()
            self.assertEqual(self.preview.data['event']['name'],'已保存的新赛事名称')

    def test_short_column_sets_fill_the_canvas_instead_of_leaving_a_blank_strip(self):
        data = reports.report(self.model.state, self.model.event, 'team')
        image = reports.render_report(data)
        self.assertEqual(image.pixelColor(image.width()-40, 126).name(),
                         reports.report_row_fill(data, data['rows'][0], 0))


if __name__ == '__main__':
    unittest.main()
