"""Physical output stays consistent across different Windows monitor scales."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtWidgets import QApplication, QWidget
from capture_surface import CaptureDpiMonitor, capture_framebuffer_size, capture_transform, dpi_scaled_geometry, reference_canvas, reference_width
from obs_capture import scene_collection

APP = QApplication.instance() or QApplication([])


class Screen(QObject):
    logicalDotsPerInchChanged = Signal(float)
    physicalDotsPerInchChanged = Signal(float)

    def __init__(self, x=0, y=0, ratio=1.):
        super().__init__()
        self.ratio = ratio
        self.bounds = QRect(x, y, round(2560 / ratio), round(1440 / ratio))

    def geometry(self): return self.bounds
    def devicePixelRatio(self): return self.ratio


def capture(screen, x, y, *, density=1, native_ratio=None, width=600, height=300):
    return SimpleNamespace(screen=lambda: screen, x=lambda: x, y=lambda: y,
        width=lambda: width, height=lambda: height, devicePixelRatioF=lambda: screen.ratio if native_ratio is None else native_ratio,
        capture_pixel_ratio=density, windowTitle=lambda: '麻将UI · 测试', base=(438, 340))


class MonitorWindow(QWidget):
    def __init__(self, screen):
        super().__init__()
        self.monitor_screen = screen

    def screen(self): return self.monitor_screen


class CaptureDpiTests(unittest.TestCase):
    def test_reference_canvas_is_1920_by_1080_physical_pixels_on_its_monitor(self):
        for ratio in (1., 1.25, 1.5, 1.75, 2.):
            screen = Screen(2560, -400, ratio)
            canvas = reference_canvas(screen)
            self.assertEqual((canvas.x(), canvas.y()), (2560, -400))
            self.assertAlmostEqual(canvas.width() * ratio, 1920, delta=1)
            self.assertAlmostEqual(canvas.height() * ratio, 1080, delta=1)
            self.assertEqual(reference_canvas(capture(screen, 2600, 0)), canvas)

    def test_scale_reference_uses_current_monitor_even_while_native_dpr_is_pending(self):
        window = capture(Screen(2560, 0, 1.5), 2600, 30, native_ratio=1.)
        self.assertEqual(reference_width(window), 292)

    def test_obs_sources_use_their_own_screen_origin_and_pixel_ratio(self):
        first = capture(Screen(0, 0, 2.), 80, 60)
        second = capture(Screen(2560, 0, 1.5), 2660, 80)
        above_left = capture(Screen(-1920, -200, 1.25), -1820, -150)
        manager = SimpleNamespace(capture_windows={'first': first, 'second': second, 'above-left': above_left})
        data = scene_collection(manager, identity=lambda _: 'test:QtWindow:python.exe')
        items = data['sources'][-1]['settings']['items']
        self.assertEqual([item['pos'] for item in items], [dict(x=160, y=120), dict(x=150, y=120), dict(x=125, y=63)])
        self.assertTrue(all(item['scale'] == dict(x=1., y=1.) for item in items))
        self.assertEqual((data['base_res_cx'], data['base_res_cy']), (1920, 1080))

    def test_obs_positions_snap_to_whole_pixels_in_both_directions(self):
        screen = Screen(2560, -400, 1.5)
        first = capture_transform(capture(screen, 2587, -395))
        self.assertEqual(first['pos'], dict(x=41, y=8))
        second = capture_transform(capture(screen, 2559, -403))
        self.assertEqual(second['pos'], dict(x=-2, y=-5))
        self.assertTrue(all(type(value) is int for value in (*first['pos'].values(), *second['pos'].values())))

    def test_hud_double_density_only_changes_size_not_scene_position(self):
        screen = Screen(2560, -400, 1.5)
        regular = capture(screen, 2660, -320, width=400)
        hud = capture(screen, 2660, -320, density=2, width=800)
        first, second = capture_transform(regular), capture_transform(hud)
        self.assertEqual(first['pos'], second['pos'])
        self.assertEqual(second['scale'], dict(x=.5, y=.5))
        self.assertEqual(regular.width() * screen.ratio * first['scale']['x'], hud.width() * screen.ratio * second['scale']['x'])

    def test_full_hd_modules_keep_design_pixel_sizes_and_scene_edges_at_every_scale(self):
        # These are capture sizes, so the HUD is 2x its 619px output footprint.
        designs = ((0, 710, 1920, 340, 1), (40, 8, 1238, 242, 2), (0, 840, 1920, 220, 1))
        for origin, ratio in (((0, 0), 1.), ((0, 0), 1.25), ((0, 0), 1.5), ((2560, -400), 1.), ((2560, -400), 1.25), ((-1920, 200), 1.5)):
            screen = Screen(*origin, ratio)
            for x, y, width, height, density in designs:
                window = capture(screen, origin[0] + round(x / ratio), origin[1] + round(y / ratio), density=density, width=round(width / ratio))
                transform = capture_transform(window)
                output_width = window.width() * ratio * transform['scale']['x']
                output_height = round(height / ratio) * ratio * transform['scale']['y']
                with self.subTest(origin=origin, ratio=ratio, width=width, density=density):
                    self.assertAlmostEqual(output_width, width / density, delta=1)
                    self.assertAlmostEqual(output_height, height / density, delta=1)
                    self.assertGreaterEqual(transform['pos']['x'], 0)
                    self.assertGreaterEqual(transform['pos']['y'], 0)
                    self.assertLessEqual(transform['pos']['x'] + output_width, 1921)
                    self.assertLessEqual(transform['pos']['y'] + output_height, 1081)

    def test_export_corrects_a_pending_native_dpr_change_without_resizing_output(self):
        window = capture(Screen(2560, 0, 1.5), 2660, 80, density=2, native_ratio=2., width=800)
        transform = capture_transform(window)
        self.assertEqual(transform['pos'], dict(x=150., y=120.))
        self.assertEqual(transform['scale'], dict(x=.375, y=.375))
        self.assertEqual(window.width() * window.devicePixelRatioF() * transform['scale']['x'], 600.)

    def test_output_pixel_metadata_restores_exact_sizes_after_fractional_dpi_rounding(self):
        for ratio in (1., 1.25, 1.5):
            screen = Screen(2560, -400, ratio)
            for width, height, density in ((1920, 340, 1), (1238, 242, 2), (1920, 220, 1)):
                window = capture(screen, 2600, -320, density=density, width=round(width / ratio), height=round(height / ratio))
                window.output_physical_size = (width / density, height / density)
                transform = capture_transform(window)
                framebuffer = capture_framebuffer_size(window)
                with self.subTest(ratio=ratio, width=width, density=density):
                    self.assertAlmostEqual(framebuffer[0] * transform['scale']['x'], width / density)
                    self.assertAlmostEqual(framebuffer[1] * transform['scale']['y'], height / density)

    def test_callable_output_size_follows_custom_scale_and_does_not_repeat_hud_density(self):
        window = capture(Screen(ratio=1.5), 40, 8, density=2, width=826, height=162)
        target = [619., 121.]
        window.output_physical_size = lambda: target
        first = capture_transform(window)
        self.assertAlmostEqual(1239 * first['scale']['x'], 619.)
        self.assertAlmostEqual(243 * first['scale']['y'], 121.)
        target[:] = (928.5, 181.5)
        second = capture_transform(window)
        self.assertAlmostEqual(1239 * second['scale']['x'], 928.5)
        self.assertAlmostEqual(243 * second['scale']['y'], 181.5)

    def test_native_framebuffer_helper_matches_qt_capture_allocation(self):
        window = QWidget()
        try:
            for width, height in ((495, 97), (826, 162), (1280, 147)):
                window.resize(width, height)
                self.assertEqual(capture_framebuffer_size(window), window.grab().size().toTuple())
        finally:
            window.close()

    def test_invalid_output_pixel_sizes_are_rejected(self):
        window = capture(Screen(), 0, 0)
        for value in ((0, 200), (1920, -1), (float('nan'), 220), (1920, float('inf')), '1920x220'):
            window.output_physical_size = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                capture_transform(window)

    def test_dpi_monitor_keeps_physical_dimensions_once_and_preserves_dragged_origin(self):
        first, second = Screen(ratio=1.), Screen(2560, 0, ratio=1.5)
        window = MonitorWindow(first)
        window.setGeometry(60, 80, 600, 300)
        changes = []

        def changed(target, old, new):
            changes.append((old, new))
            target.setGeometry(dpi_scaled_geometry(target.geometry(), old, new))

        monitor = CaptureDpiMonitor(window, changed)
        try:
            window.monitor_screen = second
            window.move(2660, 80)
            monitor._schedule()
            APP.processEvents()
            APP.processEvents()
            self.assertEqual(window.geometry(), QRect(2660, 80, 400, 200))
            self.assertEqual(changes, [(1., 1.5)])
            second.logicalDotsPerInchChanged.emit(144.)
            APP.processEvents()
            self.assertEqual(changes, [(1., 1.5)])
            second.ratio = 2.
            second.logicalDotsPerInchChanged.emit(192.)
            APP.processEvents()
            self.assertEqual(window.geometry(), QRect(2660, 80, 300, 150))
            self.assertEqual(changes, [(1., 1.5), (1.5, 2.)])
        finally:
            monitor.dispose()
            window.close()

    def test_rebase_accepts_restored_layout_without_rescaling_it(self):
        first, second = Screen(ratio=1.), Screen(2560, 0, ratio=2.)
        window = MonitorWindow(first)
        changes = []
        monitor = CaptureDpiMonitor(window, lambda *args: changes.append(args))
        try:
            window.monitor_screen = second
            window.setGeometry(2600, 60, 400, 225)
            monitor.rebase()
            second.logicalDotsPerInchChanged.emit(192.)
            APP.processEvents()
            self.assertEqual(changes, [])
            self.assertEqual(window.geometry(), QRect(2600, 60, 400, 225))
        finally:
            monitor.dispose()
            window.close()


if __name__ == '__main__':
    unittest.main()
