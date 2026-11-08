"""Start the real application with only the base dependency packages visible."""
from importlib.util import find_spec
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASE = ('PySide6', 'shiboken6', 'mahjong')
OPTIONAL = ('numpy', 'cv2', 'onnxruntime', 'SpoutGL')


def manual_probe(ui_only, package_paths):
    from importlib.machinery import PathFinder

    class BasePackageFinder:
        @staticmethod
        def find_spec(name, path=None, target=None):
            if name in BASE:
                return PathFinder.find_spec(name, package_paths)

    # -I -S keeps the installed site-packages and .pth files off sys.path.
    sys.meta_path.insert(0, BasePackageFinder())
    sys.path.insert(0, str(ROOT))
    assert all(find_spec(name) is None for name in OPTIONAL)

    import run
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from recognition_config import default_recognition_config
    from recognition_runtime import MISSING_RECOGNITION
    from settings_dialog import SettingsDialog

    errors = []
    completed = []
    original_create_model = run.create_model

    def create_hidden_output(*args, **kwargs):
        model = original_create_model(*args, **kwargs)
        model.settings.update(show_overlays=False, topmost=False)
        return model

    class ManualConsole(run.Console):
        def show(self):
            super().show()
            QTimer.singleShot(0, self.exercise_manual_workflow)

        def exercise_manual_workflow(self):
            try:
                assist = self.inline_automation
                assert not assist.workers and not assist._freshness_timer.isActive()
                assert all(hand.isHidden() for hand in self.recognized_hands)
                assert self.target_buttons['hand'].isHidden()

                self.select(0)
                self.add_wait_draft('1z')
                self.submit_waits()
                assert self.model.state['players'][0]['waits'] == ['1z']
                self.model.set_score(0, 28000)
                self.manager.set_overlays_visible(True)
                output = self.manager.capture_windows['round']
                assert output.isVisible() and not output.grab().isNull()
                self.manager.set_overlays_visible(False)

                # Saved capture bindings can arrive via another device's
                # settings file even when this device has only the base app.
                config = default_recognition_config()
                config['sources'][0].update(enabled=True, kind='camera', path='0')
                self.model.setting('recognition_assist', config)
                assert not assist.workers and not assist._freshness_timer.isActive()

                if ui_only:
                    assert self.model.event_store is None
                    editor = SettingsDialog(self)
                else:
                    store = self.model.event_store
                    first = self.model.event['id']
                    store.create('手动导播检查', 'team_league')
                    store.open(first)
                    assert self.model.state['players'][0]['score'] == 28000
                    workspace = self.open_event_settings('output')
                    editor = workspace.output_editor
                editor.focus_recognition(0)
                assert not editor.recognition_runtime_note.isHidden()
                assert editor.recognition_preview.status.text() == MISSING_RECOGNITION
                assert editor._recognition_scanner is None
                editor._start_recognition_preview(0)
                assert editor.recognition_preview_session.worker is None
                assert editor.recognition_preview.status.text() == MISSING_RECOGNITION
                editor.show_melds.setChecked(False)
                assert editor.apply()
                assert self.model.settings['show_melds'] is False
                assert self.model.settings['recognition_assist'] == config
                if ui_only:
                    editor.close()
                    editor.deleteLater()

                self.model.draw()
                assert self.model.state['phase'] == 'result'
                assert not assist.workers
                assert not {'tile_recognition', 'recognition_worker', *OPTIONAL}.intersection(sys.modules)
                assert self.close()
                completed.append(True)
            except BaseException:
                errors.append(traceback.format_exc())
                QApplication.exit(1)

    run.create_model = create_hidden_output
    run.Console = ManualConsole
    with tempfile.TemporaryDirectory(prefix='tmp-manual-only-', dir=ROOT/'tests') as directory:
        sys.argv = ['run.py', '--data-dir', directory]
        if ui_only:
            sys.argv.append('--ui-only')
        result = run.main()
    if errors:
        raise AssertionError(errors[0])
    assert result == 0 and completed


class ManualStartupTests(unittest.TestCase):
    def test_event_and_standalone_desks_work_without_vision_dependencies(self):
        package_paths = sorted({str(Path(find_spec(name).origin).parent.parent) for name in BASE})
        for mode in ('event', 'standalone'):
            with self.subTest(mode=mode):
                process = subprocess.run(
                    [sys.executable, '-I', '-S', str(Path(__file__).resolve()),
                     '--manual-probe', mode, *package_paths],
                    cwd=ROOT, env=dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONIOENCODING='utf-8'),
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
                )
                self.assertEqual(process.returncode, 0, (process.stdout + process.stderr)[-6000:])


if __name__ == '__main__':
    if sys.argv[1:2] == ['--manual-probe']:
        manual_probe(sys.argv[2] == 'standalone', sys.argv[3:])
    else:
        unittest.main()
