"""Selected-half team totals, preserved historical identities and exact PNG export."""
import copy
from datetime import datetime
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from PySide6.QtCore import QDateTime, QEvent, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QDialog, QWidget
from event_history import EventHistoryDialog
from event_results import prepare_result, record_result
from event_store import EventStore
from event_workspace import EventWorkspaceDialog
from model import Model
from match_record_time import MatchRecordTimeDialog
from team_match_preview import TeamMatchReportDialog
from team_match_report import build_report, date_label, export_png, render_report

APP = QApplication.instance() or QApplication([])
TIME = '2026-11-10T20:00:00+08:00'


def half(identity, points=(50, 10, -20, -40), teams=('A', 'B', 'C', 'D')):
    return dict(id=identity, committed=True, title=identity, hands=[], recorded_at=TIME,
        seats=['p1', 'p2', 'p3', 'p4'], results=[
            dict(id=f'p{i+1}', name=f'选手{i+1}', team=team, team_id=team,
                 color='#ffbf00', points=point, rank=i+1, score=40000-i*10000)
            for i, (team, point) in enumerate(zip(teams, points))])


class TeamMatchReportTests(unittest.TestCase):
    def setUp(self):
        self.matches = [half('first'), half('second', (30, 10, -15, -25)),
                        half('third', (-50, 40, 20, -10))]
        self.state = dict(id='live', phase='playing', broadcast=dict(matches=self.matches, teams={}))
        self.event = dict(id='event', kind='team_league', name='队伍结果测试',
                          team_baselines={'A': dict(points=9999)})

    def test_nonconsecutive_selection_deduplicates_and_keeps_original_numbers_without_baselines(self):
        before = copy.deepcopy((self.state, self.event))
        report = build_report(self.state, self.event, ['third', 'first', 'first'])
        self.assertEqual([(h['id'], h['number']) for h in report['halves']], [('first', 1), ('third', 3)])
        self.assertEqual({t['id']: t['points'] for t in report['teams']}, dict(A=0, B=50, C=0, D=-50))
        self.assertEqual((self.state, self.event), before)

    def test_daily_numbers_use_all_confirmed_records_in_time_order_before_selection(self):
        times = ['2026-11-09T20:00:00', '2026-11-10T22:00:00', '2026-11-10T18:00:00',
                 '2026-11-10T20:00:00', '2026-11-10T21:00:00', '2026-11-11T18:00:00']
        matches = [dict(half(str(i)), recorded_at=datetime.fromisoformat(time).astimezone().isoformat())
                   for i, time in enumerate(times)]
        # An unfinished entry earlier that day must not consume a number.
        matches.append(dict(half('pending'), committed=False, recorded_at=matches[2]['recorded_at']))
        self.state['broadcast']['matches'] = matches
        report = build_report(self.state, self.event, ['1', '3', '5'])
        self.assertEqual([(h['id'], h['number']) for h in report['halves']], [('3', 2), ('1', 4), ('5', 1)])
        self.assertEqual([h['date'] for h in report['halves']], ['11/10（周二）', '11/10（周二）', '11/11（周三）'])
        self.assertEqual(report['teams'][0]['points'], 150)

    def test_same_month_day_in_different_years_has_separate_numbers(self):
        for match, time in zip(self.matches, ['2025-11-10T18:00:00', '2026-11-10T18:00:00', '2026-11-10T19:00:00']):
            match['recorded_at'] = datetime.fromisoformat(time).astimezone().isoformat()
        report = build_report(self.state, self.event, ['first', 'third'])
        self.assertEqual([h['number'] for h in report['halves']], [1, 2])

    def test_player_and_team_penalties_are_deducted_once_and_voided_entries_ignored(self):
        self.matches[0]['penalties'] = [
            dict(scope='player', target='p1', points=12.5),
            dict(scope='team', target='A', points=4.5),
            dict(scope='team', target='A', points=99, voided_at=TIME)]
        report = build_report(self.state, self.event, ['first'])
        cell = report['halves'][0]['cells']['A']
        self.assertEqual(cell['players'][0]['score'], 40000)
        self.assertEqual(cell['players'][0]['points'], 37.5)
        self.assertEqual(cell['penalty'], 4.5)
        self.assertEqual(cell['points'], 33)
        self.assertEqual(report['teams'][0]['points'], 33)

    def test_transfer_keeps_old_team_and_fixed_columns_include_the_union(self):
        self.matches[2]['results'][0].update(team='E新队', team_id='E')
        self.state['broadcast']['teams']['A'] = dict(name='A更名队', color='#123456')
        report = build_report(self.state, self.event, ['first', 'third'])
        self.assertEqual([t['id'] for t in report['teams']], ['A', 'B', 'C', 'D', 'E'])
        self.assertEqual(report['teams'][0]['name'], 'A更名队')
        self.assertEqual(report['teams'][0]['color'], '#123456')
        self.assertEqual(report['teams'][0]['points'], 50)
        self.assertEqual(report['teams'][-1]['points'], -50)
        self.assertNotIn('E', report['halves'][0]['cells'])
        self.assertNotIn('A', report['halves'][1]['cells'])
        self.assertFalse(render_report(report).isNull())

    def test_multiple_same_team_players_keep_seat_order_and_one_team_penalty(self):
        match = half('same', teams=('A', 'A', 'B', 'C'))
        match['results'].reverse()  # Archive rank order must not change the recorded seats.
        match['penalties'] = [dict(scope='team', target='A', points=5)]
        self.state['broadcast']['matches'] = [match]
        report = build_report(self.state, self.event, ['same'])
        cell = report['halves'][0]['cells']['A']
        self.assertEqual([p['id'] for p in cell['players']], ['p1', 'p2'])
        self.assertEqual(cell['points'], 55)
        self.assertEqual(report['teams'][0]['points'], 55)
        self.assertFalse(render_report(report).isNull())

    def test_unassigned_players_are_separate_and_missing_legacy_fields_render(self):
        for entry in self.matches[0]['results'][:2]:
            entry.update(team='', team_id='', color='invalid')
            del entry['score']
        del self.matches[0]['recorded_at']
        report = build_report(self.state, self.event, ['first'])
        self.assertEqual([t['id'] for t in report['teams'][:2]], ['unassigned:p1', 'unassigned:p2'])
        self.assertEqual(report['teams'][0]['color'], '#66757f')
        self.assertEqual(report['halves'][0]['date'], '')
        self.assertFalse(render_report(report).isNull())

    def test_unconfirmed_deleted_and_live_unfinished_records_cannot_be_selected(self):
        self.matches[1]['committed'] = False
        self.state['id'] = 'third'
        for selection in ([], ['deleted'], ['second'], ['third']):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                build_report(self.state, self.event, selection)
        self.state['phase'] = 'finished'
        self.assertEqual(len(build_report(self.state, self.event, ['third'])['halves']), 1)
        self.event['kind'] = 'individual_league'
        with self.assertRaises(ValueError):
            build_report(self.state, self.event, ['first'])

    def test_date_uses_system_record_date_and_export_roundtrips_complete_image(self):
        value = '2026-11-09T17:00:00+00:00'
        day = datetime.fromisoformat(value).astimezone()
        self.assertEqual(date_label(dict(recorded_at=value)), f"{day.month}/{day.day}（周{'一二三四五六日'[day.weekday()]}）")
        self.assertEqual(date_label(dict(recorded_at='bad')), '')
        image = render_report(build_report(self.state, self.event, ['first', 'third']))
        self.assertGreater(image.height(), 700)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as directory:
            path = Path(directory) / 'report.png'
            export_png(image, path)
            self.assertEqual(QImage(str(path)).convertToFormat(image.format()), image)


class TeamMatchReportWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tmp-team-report-', dir=ROOT / 'tests')
        self.model = Model(self.tmp.name)
        self.store = EventStore(self.model)
        self.store.create('选择半庄测试', 'team_league')
        roster = copy.deepcopy(self.model.state['broadcast']['roster'])
        for i, player in enumerate(roster):
            player.update(team=f'队伍{i+1}', team_id=f'team-{i}', color='#2866bd')
        self.model.set_roster(roster)
        self.records = []
        for scores in ([41000, 29000, 19000, 11000], [11000, 41000, 29000, 19000]):
            match = prepare_result(self.model, ['p1', 'p2', 'p3', 'p4'], scores)
            record_result(self.store, match)
            self.records.append(match)
        self.owner = QWidget()
        self.owner.model = self.model
        self.windows = []

    def tearDown(self):
        for window in self.windows[::-1]:
            if isinstance(window, EventWorkspaceDialog):
                window._allow_close = True
            window.close()
        self.owner.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.tmp.cleanup()

    def history(self):
        window = EventHistoryDialog(self.owner, self.model.state, self.model.event, select_matches=True)
        self.windows.append(window)
        return window

    def test_history_checks_preserve_detail_selection_and_data(self):
        before = copy.deepcopy((self.model.state, self.model.event, self.model.undo))
        session = (self.model.path / 'session.json').read_bytes()
        history = self.history()
        self.assertFalse(history.generate_button.isEnabled())
        history.match_table.item(1, 0).setCheckState(Qt.CheckState.Checked)
        history.match_table.selectRow(0)
        self.assertEqual(history.selected_match_ids(), [self.records[0]['id']])
        self.assertTrue(history.generate_button.isEnabled())
        history.check_matches(True)
        self.assertEqual(len(history.selected_match_ids()), 2)
        self.assertEqual(history.selection_count.text(), '已选 2 / 2 场')
        history.check_matches(False)
        self.assertFalse(history.generate_button.isEnabled())
        self.assertEqual(history.match_table.currentRow(), 0)
        self.assertEqual((self.model.state, self.model.event, self.model.undo), before)
        self.assertEqual((self.model.path / 'session.json').read_bytes(), session)

    def test_preview_exports_exact_pending_title_image_without_touching_results(self):
        before = copy.deepcopy((self.model.state, self.model.event, self.model.undo))
        history = self.history()
        preview = TeamMatchReportDialog(history, [r['id'] for r in self.records])
        self.windows.append(preview)
        preview.show()
        APP.processEvents()
        preview.title.setText('当天队伍结果')
        path = Path(self.tmp.name) / 'proof'
        with patch('team_match_preview.QFileDialog.getSaveFileName', return_value=(str(path), 'PNG (*.png)')):
            preview.export()
        self.assertEqual(preview.image, QImage(str(path) + '.png').convertToFormat(preview.image.format()))
        self.assertEqual(preview.data['title'], '当天队伍结果')
        self.assertIn('已导出', preview.status.text())
        preview.fit()
        preview.actual_size()
        self.assertFalse(preview.fit_mode)
        self.assertEqual(preview.zoom.value(), 100)
        self.assertEqual((self.model.state, self.model.event, self.model.undo), before)

    def test_generate_opens_preview_for_checked_halves(self):
        history = self.history()
        history.match_table.item(1, 0).setCheckState(Qt.CheckState.Checked)
        captured = []
        def execute(dialog):
            captured.append(dialog)
            return QDialog.DialogCode.Rejected
        with patch.object(TeamMatchReportDialog, 'exec', new=execute):
            history.generate_button.click()
        self.assertEqual([h['id'] for h in captured[0].data['halves']], [self.records[0]['id']])

    def test_edit_time_keeps_checks_and_refreshes_report_with_new_date(self):
        history = self.history()
        history.check_matches(True)
        selected = history.selected_match_ids()
        edited = history.matches[0]['id']
        def execute(dialog):
            dialog.time.setDateTime(QDateTime(datetime(2026, 11, 9, 18, 30, 15)))
            dialog.save_button.click()
            return dialog.result()
        with patch.object(MatchRecordTimeDialog, 'exec', new=execute):
            history.edit_time_button.click()
        self.assertEqual(history.selected_match_ids(), selected)
        self.assertEqual(history.match_table.currentRow(), 0)
        self.assertIn('2026-11-09 18:30', history.record_time.text())
        self.assertIn('已保存', history.export_error.text())
        preview = TeamMatchReportDialog(history, selected)
        self.windows.append(preview)
        self.assertEqual(preview.data['halves'][0]['id'], edited)
        self.assertEqual([h['number'] for h in preview.data['halves']], [1, 1])
        self.assertEqual(preview.data['halves'][0]['date'], '11/9（周一）')

    def test_time_editor_cancel_and_event_change_do_not_write(self):
        history = self.history()
        before = copy.deepcopy(self.model.state)
        with patch.object(MatchRecordTimeDialog, 'exec', return_value=QDialog.DialogCode.Rejected):
            history.edit_time_button.click()
        self.assertEqual(self.model.state, before)
        editor = MatchRecordTimeDialog(history, self.model, history.matches[0])
        self.windows.append(editor)
        self.store.create('切换后的赛事', 'team_league')
        self.assertFalse(history.edit_time_button.isEnabled())
        editor.save_button.click()
        self.assertIn('赛事已切换', editor.error.text())
        self.assertEqual(self.model.state['broadcast']['matches'], [])

    def test_reports_button_uses_history_selector_and_is_team_only(self):
        workspace = EventWorkspaceDialog(self.owner)
        self.windows.append(workspace)
        self.assertFalse(workspace.team_results_button.isHidden())
        captured = []
        def execute(dialog):
            captured.append(dialog)
            return QDialog.DialogCode.Rejected
        with patch('event_workspace.EventHistoryDialog.exec', new=execute):
            workspace.team_results_button.click()
        selector = captured[0]
        self.assertIsInstance(selector, EventHistoryDialog)
        self.assertTrue(selector.selection_mode)
        self.store.create('个人赛', 'individual_league')
        workspace.refresh_reports()
        self.assertTrue(workspace.team_results_button.isHidden())


if __name__ == '__main__':
    unittest.main()
