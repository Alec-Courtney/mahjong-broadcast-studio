"""Small daily acceptance suite; use --full after broad model/storage changes."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'src')]

DAILY = (
    'test_team_match_report',
    'test_hand_history',
    'test_manual_without_recognition',
    'test_hand_calculation',
    'test_automation_api',
    'test_inline_automation',
    'test_recognized_hand',
    'test_four_seat_capture',
    'test_spout_capture',
    'test_recognition_settings',
    'test_recognition_source_editor',
    'test_live_console_accessibility',
    'test_quantity_controls',
    'test_player_wait_controls',
    'test_desk_meld_preview',
    'test_obs_capture_surfaces',
    'test_event_workspaces.EventTests.test_two_events_restore_complete_settings_and_clear_undo',
    'test_tournament_model.TournamentModelTests.test_replace_settlement_undo_redo_reload',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true', help='检查剩余的全部跨功能回归')
    args = parser.parse_args()
    loader = unittest.TestLoader()
    suite = (loader.discover(str(ROOT / 'tests'), pattern='test_*.py') if args.full
             else loader.loadTestsFromNames(DAILY))
    print(('完整检查' if args.full else '日常关键检查') + f'：{suite.countTestCases()} 项', flush=True)
    return 0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
