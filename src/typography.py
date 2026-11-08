"""Independent desk and broadcast typography, scoped to each rendered model."""
from contextlib import contextmanager
from contextvars import ContextVar
import math
import re

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget

_overlay = ContextVar('overlay_typography', default=('', 1.))


def validate(settings):
    for key in ('console_font_family', 'overlay_font_family'):
        value = settings.get(key, '')
        if not isinstance(value, str) or len(value) > 150 or any(c in value for c in '\n\r{};'):
            raise ValueError('字体名称无效')
    for key, low, high, default in [('console_font_size', 9, 24, 14), ('settings_font_size', 9, 24, 14), ('overlay_font_scale', .6, 1.6, 1.)]:
        value = settings.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('字号设置超出范围')


@contextmanager
def overlay_scope(settings):
    token = _overlay.set((settings.get('overlay_font_family', ''), settings.get('overlay_font_scale', 1.)))
    try:
        yield
    finally:
        _overlay.reset(token)


def overlay_font(size, bold=False, fallback='Microsoft YaHei'):
    family, scale = _overlay.get()
    font = QFont(family or fallback)
    font.setPixelSize(max(1, round(size * scale)))
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


def desk_stylesheet(source, settings):
    family = settings.get('console_font_family', '').replace('"', '\\"')
    if family:
        source = re.sub(r'font-family\s*:[^;}]+', lambda _: 'font-family:"'+family+'"', source)
        source += '\nQWidget{font-family:"'+family+'"}'
    scale = settings.get('console_font_size', 14) / 12
    return re.sub(r'font-size\s*:\s*(\d+(?:\.\d+)?)px',
                  lambda m: 'font-size:'+str(max(8, round(float(m[1])*scale)))+'px', source)


def apply_desk(root, settings):
    """Scale original styles, so repeated applies never compound the size."""
    for widget in [root, *root.findChildren(QWidget)]:
        if widget is not root and widget.window() is not root.window():continue
        if widget.property('typographyPreview'):continue
        source = widget.styleSheet()
        previous = getattr(widget, '_typography_rendered_style', None)
        if source != previous:
            widget._typography_base_style = source
        base = getattr(widget, '_typography_base_style', source)
        if widget is root or 'font-size' in base or 'font-family' in base:
            rendered = desk_stylesheet(base, settings)
            if rendered != source:
                widget.setStyleSheet(rendered)
            widget._typography_rendered_style = rendered
