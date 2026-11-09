"""Settings drafts and navigation to the existing management windows."""
import copy
from pathlib import Path
import sys

from PySide6.QtCore import Qt, Slot, QSignalBlocker
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFontComboBox, QFormLayout, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QScrollArea, QStackedWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QMenu, QFileDialog, QGroupBox, QApplication, QSizePolicy, QAbstractSpinBox,
)
from desk_theme import apply_theme, palette
from i18n import LANGUAGES, tr
from ui_widgets import button, choice, decimal, integer, note, row, selected, WindowScaleSpinBox
from settings_window import SettingsWindow, pending_spin_edits
from settings_drafts import merge_draft as _merge_draft
from recognition_settings import RecognitionSettingsPage


def number(value, low, high, step=1):
    widget = integer(low, high, step)
    widget.setValue(value)
    return widget


class SettingsDialog(RecognitionSettingsPage, SettingsWindow):
    """Local forms are drafts; linked management windows apply their own changes."""
    def __init__(self, main, *, embedded_in=None):
        super().__init__(main)
        self.main = main
        self.model = main.model
        self._event_editor = embedded_in is not None
        if self._event_editor:
            self.setParent(embedded_in, Qt.WindowType.Widget)
        self.event_id = (self.model.event or {}).get('id')
        self._context = self._context_key()
        self._context_invalid = False
        self.setWindowTitle('画面与设备' if self._event_editor else '播出设置')
        root = QVBoxLayout(self)
        if self._event_editor:
            root.setContentsMargins(4,4,4,4)
        else:
            self.setMinimumSize(760, 540)
            screen = main.screen() or QApplication.primaryScreen()
            available = screen.availableGeometry() if screen else None
            self.resize(min(1000, available.width()-48) if available else 1000,
                        min(720, available.height()-64) if available else 720)
            title = QLabel('工作台设置')
            title.setObjectName('sectionTitle')
            root.addWidget(title)
            root.addWidget(note('主题、语言和操作界面字体跨赛事保留。字号即时保存，其余设置点击保存后生效。'))
        body = QHBoxLayout()
        sidebar = QVBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索设置')
        self.search.setClearButtonEnabled(True)
        sidebar.addWidget(self.search)
        self.nav = QTreeWidget()
        self.nav.setHeaderHidden(True)
        self.nav.setFixedWidth(192)
        self.nav.setIndentation(14)
        sidebar.addWidget(self.nav, 1)
        body.addLayout(sidebar)
        self.pages = QStackedWidget()
        self.pages.setMinimumWidth(0)
        self.pages.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)
        self.groups = [QTreeWidgetItem(self.nav, [title]) for title in ('常用设置', '高级设置', '管理入口')]
        self.entries = []
        self.entry_buttons = {}
        if not self._event_editor:
            self._appearance()
        self._broadcast()
        self._card_style()
        self.card_editor.editing_style.currentIndexChanged.connect(self._card_style_selected)
        self.player_style.currentIndexChanged.connect(self._broadcast_style_selected)
        self._windows()
        self._fonts()
        self._recognition()
        self._management_pages()
        refresh_card_fonts = lambda *_: self.card_editor.set_typography(
            self.overlay_font.currentFont().family(), self.overlay_font_scale.value()/100)
        self.overlay_font.currentFontChanged.connect(refresh_card_fonts)
        self.overlay_font_scale.valueChanged.connect(refresh_card_fonts)
        refresh_card_fonts()
        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.addWidget(note('没有匹配的设置，请尝试“字体”“立直”“窗口”等关键词。'))
        empty_layout.addStretch()
        self.pages.addWidget(self.empty_page)
        self.nav.expandAll()
        self.nav.currentItemChanged.connect(self._select)
        self.search.textChanged.connect(self._filter)
        self.nav.setCurrentItem(self.entries[0][0])
        self.error = QLabel()
        self.error.setObjectName('error')
        self.error.setWordWrap(True)
        self.error.setMinimumHeight(0)
        root.addWidget(self.error)
        self.save_status = QLabel()
        self.save_status.setObjectName('success')
        root.addWidget(self.save_status)
        self.save_button = button('保存', self.apply)
        self.save_button.setObjectName('primary')
        self.reload_button = button('重新读取', self.reload_drafts)
        self.reload_button.setToolTip('丢弃本窗口未保存的草稿，读取当前赛事的最新设置。')
        if self._event_editor:
            root.addLayout(row(self.reload_button, 1, self.save_button))
        else:
            root.addLayout(row(self.reload_button, 1, button('关闭', self.close), self.save_button))
        self._capture_baseline()
        self.track_settings('settings', self._pending_values, self.apply)
        self.model.changed.connect(self._watch_context)
        self.model.display_changed.connect(self._watch_context)
        self.finished.connect(self._shutdown_recognition_preview)
        self.finished.connect(lambda *_: self.main.manager.clear_capture_preview(self))
        self.destroyed.connect(self.recognition_preview_session.close)
        for widget in self.findChildren(QWidget):
            for name in ('textEdited', 'valueChanged', 'toggled', 'currentIndexChanged'):
                signal = getattr(widget, name, None)
                if signal is not None:signal.connect(self.save_status.clear)
        self._loading_drafts = False
        if not self._event_editor:
            self.settings_font_size.valueChanged.connect(
                lambda value: self._save_live_font_size('settings_font_size', value))
            self.console_font_size.valueChanged.connect(
                lambda value: self._save_live_font_size('console_font_size', value))
        apply_theme(self, self.model.settings)

    def _page(self, title, group, keywords, description, preview=None):
        index = self.pages.count()
        entry = QTreeWidgetItem(self.groups[group], [title])
        entry.setData(0, Qt.ItemDataRole.UserRole, index)
        self.entries.append((entry, title + ' ' + keywords))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        widget = QWidget()
        widget.setObjectName('settingsPage')
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        page = scroll
        header = layout
        if preview is not None:
            page = QWidget()
            page.setObjectName('settingsPage')
            header = QVBoxLayout(page)
            header.setContentsMargins(12, 8, 12, 8)
            header.setSpacing(8)
            layout.setContentsMargins(0, 0, 8, 0)
        heading = QLabel(title)
        heading.setObjectName('sectionTitle')
        header.addWidget(heading)
        header.addWidget(note(description))
        scroll.setWidget(widget)
        if preview is not None:
            header.addWidget(preview)
            header.addWidget(scroll, 1)
        self.pages.addWidget(page)
        return layout

    def _select(self, item, previous=None):
        if item is None: return
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if index is None:
            for i in range(item.childCount()):
                if not item.child(i).isHidden():
                    self.nav.setCurrentItem(item.child(i))
                    return
        else:
            self.pages.setCurrentIndex(index)

    def _filter(self, text):
        text = text.strip().casefold()
        for item, keywords in self.entries:
            item.setHidden(bool(text and text not in (keywords + ' ' + tr(item.text(0))).casefold()))
        for group in self.groups:
            group.setHidden(all(group.child(i).isHidden() for i in range(group.childCount())))
        visible = [item for item, _ in self.entries if not item.isHidden()]
        title_match = next((item for item in visible if text and text in tr(item.text(0)).casefold()), None)
        if not visible:
            self.pages.setCurrentWidget(self.empty_page)
        elif title_match is not None or self.nav.currentItem() not in visible or self.pages.currentWidget() is self.empty_page:
            preferred = title_match or visible[0]
            self.nav.setCurrentItem(preferred)
            self._select(preferred)

    def _appearance(self):
        layout = self._page('外观与工作区', 0, '主题 深色 浅色 拖动 布局 停靠 设置 字体 字号',
                            '后台主题独立于播出画面。导播面板的位置随当前赛事保存。')
        self.theme = choice([('深色', 'dark'), ('浅色', 'light')])
        selected(self.theme, self.model.settings.get('console_theme', 'dark'))
        layout.addLayout(row('后台主题', self.theme, 1))
        self.language = choice(LANGUAGES)
        self.language.setProperty('i18nLiteral', True)
        selected(self.language, self.model.settings.get('language', 'zh_CN'))
        layout.addLayout(row('界面语言', self.language, 1))
        self.settings_font_size = number(self.model.settings.get('settings_font_size', 14), 9, 24)
        self.settings_font_size.setSuffix(' px')
        layout.addLayout(row('设置页面字号', self.settings_font_size, 1))
        layout.addWidget(note('调整后立即生效并保存，所有已打开的设置和管理窗口同步更新。'))
        self.theme_preview = QLabel('导播台  ·  选手 01  ·  25,000\n清晰的输入、稳定的布局')
        self.theme_preview.setMinimumHeight(110)
        self.theme_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.theme_preview.setProperty('typographyPreview', True)
        layout.addWidget(self.theme_preview)
        self.theme.currentIndexChanged.connect(self._preview_theme)
        self._preview_theme()
        self.reset_desk = QCheckBox('保存时恢复导播面板的默认排列')
        self.reset_desk.setEnabled(hasattr(self.main, 'workspace'))
        layout.addWidget(self.reset_desk)
        layout.addWidget(note('拖动面板标题可换位或拖出独立窗口；双击标题可浮动或回到主窗口。关闭的面板可从“面板”菜单重新打开。'))
        layout.addStretch()


    def _preview_theme(self, *_):
        c = palette({'console_theme': self.theme.currentData()})
        self.theme_preview.setStyleSheet('background:'+c['panel']+';color:'+c['text']+';border:1px solid '+c['border']+';border-radius:8px;font-size:17px')

    def _broadcast(self):
        layout = self._page('播出显示', 0, '副露 立直棒 扣点 结算 背景 置顶 动画 速度 终局 停留',
                            '这里控制播出方式；真实点棒账目始终按实际支付保存。')
        self.player_style = choice([('M.League', 'mleague'), ('最高位战（保留版）', 'saikouisen')])
        selected(self.player_style, self.model.settings.get('theme', 'mleague'))
        layout.addLayout(row('选手卡样式', self.player_style, 1))
        self.show_melds = QCheckBox('显示副露区')
        self.show_melds.setChecked(self.model.settings.get('show_melds', True))
        layout.addWidget(self.show_melds)
        layout.addWidget(note('关闭只隐藏副露，已录入的牌保留。收支动画会下移靠近选手卡，不再预留副露行。'))
        if not self._event_editor:
            self.riichi_payment = choice([('立即扣点', 'immediate'), ('局末单列', 'deferred')])
            selected(self.riichi_payment, self.model.settings.get('riichi_payment_mode', 'immediate'))
            layout.addLayout(row('立直棒', self.riichi_payment, 1))
            layout.addWidget(note('局末单列：上行显示立直棒收支，下行显示通常和牌收支。自己的立直棒在自己和牌时抵消。'))
        self.bg = choice([('透明', 'transparent'), ('纯绿', 'green'), ('纯蓝', 'blue')])
        selected(self.bg, self.model.settings.get('capture_bg', 'transparent'))
        self.topmost = QCheckBox('播出窗口置顶')
        self.topmost.setChecked(self.model.settings.get('topmost', False))
        layout.addLayout(row('捕获背景', self.bg, 1))
        layout.addWidget(self.topmost)
        if not self._event_editor:
            self.speed = decimal(.1, 10, 2)
            self.speed.setValue(self.model.settings.get('speed', 1.))
            self.speed.setSingleStep(.25)
            self.speed.setSuffix(' ×')
            self.final_scores_hold = decimal(1, 60, 1)
            self.final_scores_hold.setValue(self.model.settings.get('final_scores_hold', 4.))
            self.final_scores_hold.setSuffix(' 秒')
            layout.addLayout(row('动画速度', self.speed, 1))
            self.final_scores_hold.setParent(self);self.final_scores_hold.hide()
            layout.addWidget(note('动画速度用于通常播出；赛后两页的完整停留时间在“播出详细配置”中单独设置。'))
        layout.addStretch()

    def _windows(self):
        from overlays import WINDOW_LABELS,FULLSCREEN_PARTS
        layout = self._page('播出窗口', 1, '位置 比例 缩放 百分比 大小 尺寸 坐标 四家 显示',
                            '尺寸按百分比调整，100% 为该窗口的原始设计尺寸，宽高同比缩放。位置坐标单独调整。')
        self.split_fullscreen=QCheckBox('将全屏内容拆分为独立窗口（开场、赛后、走势、统计、四人条件、立直）')
        self.split_fullscreen.setChecked(self.model.settings.get('split_fullscreen_windows',False))
        layout.addWidget(self.split_fullscreen)
        layout.addWidget(note('默认合并到“全屏节目”，空闲时透明隐藏；修改拆分方式后点击保存。四家积分／排名直接在选手 UI 中切换。'))
        grid = QGridLayout()
        # Adopt every row control before toggling visibility. An unparented
        # control shown here becomes a temporary native top-level window.
        layout.addLayout(grid)
        for col, title in enumerate(['窗口', '显示', 'X', 'Y', '尺寸比例', '预览']):
            grid.addWidget(QLabel(title), 0, col)
        self.values = []
        self.capture_rows={}
        for r, (key, window) in enumerate(self.main.manager.layout_windows.items(), 1):
            title = WINDOW_LABELS[key]
            show = QCheckBox()
            show.setChecked(self.main.manager.capture_enabled(key))
            show.clicked.connect(lambda visible, target=key:self.main.manager.preview_capture(self,target,visible))
            x = number(window.x(), -20000, 20000)
            y = number(window.y(), -20000, 20000)
            for coordinate in (x, y):
                coordinate.setMinimumWidth(0)
                coordinate.setMaximumWidth(132)
                coordinate.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            low,high=120,4000
            if key=='players':
                cards=[self.main.manager.windows[f'player{i}'].width() for i in range(4)]
                low,high=round(window.width()*120/min(cards)),round(window.width()*2400/max(cards))
            elif key in ('fullscreen','opening','postmatch'):low,high=320,3840
            width = WindowScaleSpinBox(window.width(), self.main.manager.reference_width(key),low,high)
            width.setMinimumWidth(0)
            width.setMaximumWidth(180)
            width.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            for col, widget in enumerate([QLabel(title), show, x, y, width]): grid.addWidget(widget, r, col)
            self.values.append((key, show, x, y, width))
            locate=button('定位',lambda checked=False,target=key:self.main.manager.preview_capture(self,target))
            locate.setToolTip('临时显示定位画面，OBS 也能捕获；关闭设置后结束')
            grid.addWidget(locate,r,5)
            self.capture_rows[key]=[grid.itemAtPosition(r,column).widget() for column in range(6)]
        def update_rows():
            split=self.split_fullscreen.isChecked()
            for key,widgets in self.capture_rows.items():
                shown=(split if key in FULLSCREEN_PARTS else not split if key=='fullscreen' else True)
                for widget in widgets:widget.setVisible(shown)
        self.split_fullscreen.toggled.connect(update_rows)
        update_rows()
        self.reset_windows = QCheckBox('保存时恢复 1920×1080 参考布局')
        layout.addWidget(self.reset_windows)
        layout.addWidget(note('四家联动位置与间距也可从主界面“四家布局”调整。'))
        layout.addWidget(note('局况／宝牌以 2 倍像素高清绘制，尺寸比例仍控制原来的布局大小。新导出的 OBS 场景自动按 50% 放置；已有来源请在 OBS 中重新调整一次大小，并选择 Lanczos 缩放滤镜。'))
        layout.addWidget(note('开场、赛后连播、点数走势、技术统计、四人并排条件和立直特效默认共用“全屏节目”。侧边展示栏保留队伍排名、个人排名与单人条件，切换内容或排名范围不会改变窗口尺寸。'))
        layout.addWidget(note('OBS“窗口捕获”请选择 Windows 10（1903 及以上），自动／BitBlt 可能捕获为黑屏。定位预览会进入 OBS，正常隐藏时窗口保留透明空画面。'))
        layout.addLayout(row(button('导出 OBS 窗口捕获场景',self.export_obs_scene),1))
        layout.addStretch()

    def export_obs_scene(self):
        from obs_capture import export_scene_collection
        path,_=QFileDialog.getSaveFileName(self,'导出 OBS 场景','麻将直播_OBS.json','OBS 场景 (*.json)')
        if path:
            try:
                export_scene_collection(self.main.manager,path)
                self.save_status.setText('OBS 场景已导出；在 OBS“场景集合 → 导入”中选择该文件')
            except Exception as error:self.error.setText(str(error))

    def _card_style(self):
        from card_style_editor import CardStyleEditor
        self.card_editor = CardStyleEditor(self.model, getattr(self.main, 'selected', 0), self)
        layout = self._page('选手卡样式', 0, 'ML 最高位战 渐变 颜色 字体 字号 字间距 行距 队名 姓名 积分',
                            '调整两种卡片各自的文字与配色。预览不会影响正在播出的画面，保存后生效。',
                            preview=self.card_editor.preview)
        layout.addWidget(self.card_editor)
        layout.addStretch()

    def _card_style_selected(self, *_):
        selected(self.player_style, self.card_editor.editing_style.currentData())

    def _broadcast_style_selected(self, *_):
        selected(self.card_editor.editing_style, self.player_style.currentData())

    def _fonts(self):
        import artwork
        layout = self._page('字体与字号', 1, '字体 导播台 播出 UI 比例',
                            '后台字体和播出字体分别设置，牌面大小与窗口尺寸保持独立。')
        self.overlay_font = QFontComboBox()
        self.overlay_font.setProperty('i18nLiteral', True)
        self.overlay_font.setCurrentFont(QFont(self.model.settings.get('overlay_font_family') or artwork.FONT))
        self.overlay_font_scale = number(round(self.model.settings.get('overlay_font_scale', 1.)*100), 60, 160, 5)
        self.overlay_font_scale.setSuffix(' %')
        form = QFormLayout()
        if not self._event_editor:
            self.console_font = QFontComboBox()
            self.console_font.setProperty('i18nLiteral', True)
            self.console_font.setCurrentFont(QFont(self.model.settings.get('console_font_family') or artwork.FONT))
            self.console_font_size = number(self.model.settings.get('console_font_size', 14), 9, 24)
            self.console_font_size.setSuffix(' px')
            form.addRow('导播台字体', self.console_font)
            form.addRow('导播台字号', self.console_font_size)
        form.addRow('播出 UI 字体', self.overlay_font)
        form.addRow('播出字号比例', self.overlay_font_scale)
        layout.addLayout(form)
        if not self._event_editor:
            layout.addWidget(note('导播台字号即时生效并保存；字体和播出字号比例点击保存后生效。'))
        self.font_preview = QLabel('东 1 局 · 立直 · 25,000')
        self.font_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.font_preview.setMinimumHeight(100)
        self.font_preview.setProperty('typographyPreview', True)
        self.font_preview.setProperty('i18nLiteral', True)
        layout.addWidget(self.font_preview)
        self.overlay_font.currentFontChanged.connect(self.preview_font)
        self.overlay_font_scale.valueChanged.connect(self.preview_font)
        self.preview_font()
        layout.addStretch()

    def preview_font(self, *_):
        font = self.overlay_font.currentFont()
        font.setPixelSize(round(28*self.overlay_font_scale.value()/100))
        self.font_preview.setFont(font)
        self.font_preview.setStyleSheet('font-family:"'+font.family()+'";font-size:'+str(font.pixelSize())+'px')


    def _entry(self, layout, key, title, description, callback):
        control = button(title, callback)
        control.setMinimumWidth(164)
        control.setMinimumHeight(34)
        control.setObjectName('settingsEntry')
        self.entry_buttons[key] = control
        layout.addLayout(row(control, note(description)))
        return control

    def _management_pages(self):
        if not self._event_editor:
            self._standalone_broadcast_links()
        layout = self._page('布局与快捷键', 2, '组件 面板 布局 窗口 位置 四家 间距 快捷键 按键',
                            '安排当前工作区的面板和捕获窗口，设置常用快捷键。')
        self._entry(layout, 'player_layout', '四家布局', '四家位置、间距、尺寸和联动调整。',
                    lambda: self._open_existing(self.main.open_player_layout))
        self._entry(layout, 'panels', '组件与导播布局', '显示隐藏的导播面板、锁定或恢复默认排列。', self._open_panel_menu)
        self._entry(layout, 'shortcuts', '快捷键', '查看、更改或恢复操作快捷键。',
                    lambda: self._open_existing(self.main.shortcuts.open_dialog))
        if self._event_editor:
            self.reset_desk = QCheckBox('保存时恢复导播面板的默认排列')
            layout.addWidget(self.reset_desk)
        layout.addWidget(note('要拖动导播面板，请关闭设置后操作面板标题；面板位置随当前赛事保存。'))
        layout.addStretch()

    def _standalone_broadcast_links(self):
        layout = self._page('播出配置', 2, '播出 详细 设置 术语 解释 管理 数据 导入 JSON CSV 导出 侧栏 方案 赛后 音乐 logo',
                            '管理详细播出、晋级展示、术语库、数据导入导出及侧栏方案。')
        for key, title, detail, index in (
            ('broadcast', '播出详细配置', '侧栏、赛后动画、背景音乐、右上角标识和立直特效。', 0),
            ('qualification', '晋级条件配置', '晋级目标、对局规划与条件展示。', 2),
            ('glossary', '术语库管理', '检索、收藏、编辑并播出术语字幕。', 3),
            ('data', '数据导入与导出', '导入 JSON，导出数据备份与 CSV 表格。', 4),
        ):
            self._entry(layout, key, title, detail, lambda _=False, page=index: self._open_broadcast_page(page))
        self._entry(layout, 'sidebar_presets', '侧栏方案管理', '编辑、排序、切换侧栏展示方案。',
                    lambda: self._open_existing(self.main.open_sidebar_presets,
                                                lambda: getattr(self.main, 'sidebar_presets_dialog', None)))
        layout.addStretch()

    def _go_to_page(self, title):
        self.search.clear()
        item = next(item for item, keywords in self.entries if keywords.startswith(title + ' '))
        self.nav.setCurrentItem(item)
        self._select(item)

    def _open_existing(self, opener, target=None):
        """A nested modal loop keeps both this draft and the existing child usable.

        Hiding an exec()-opened QDialog ends its loop, so keep this window open.
        Existing modal helpers already wait. Persistent show()-based windows need
        their own modal loop while settings is the application's modal window.
        """
        self.error.clear()
        try:
            opener()
            dialog = target() if target is not None else None
            if isinstance(dialog, QDialog) and dialog.isVisible():
                # exec() changes the modal flag, but an already shown native
                # window only joins Qt's modal stack when it is shown again.
                dialog.hide()
                if isinstance(dialog, SettingsWindow):dialog.set_transient_owner(self)
                dialog.exec()
        except Exception as error:
            self.error.setText(str(error))
        finally:
            self._watch_context()
            self.raise_()
            self.activateWindow()

    def _open_event_page(self, index):
        self._open_existing(lambda: self.main.open_event_workspace(index),
                            lambda: getattr(self.main, 'event_workspace', None))

    def _open_broadcast_page(self, index):
        def open_page():
            self.main.open_broadcast()
            self.main.broadcast_panel().tabs.setCurrentIndex(index)
        self._open_existing(open_page, self.main.broadcast_panel)

    def _open_panel_menu(self):
        # Reuse the same QActions, including their checked state and callbacks.
        menu = QMenu(self)
        menu.addActions(self.main.panel_menu.actions())
        control = self.entry_buttons['panels']
        menu.exec(control.mapToGlobal(control.rect().bottomLeft()))
        menu.deleteLater()
        self._watch_context()

    def _context_key(self):
        return ((self.model.event or {}).get('id'), str(self.model.path))

    def _watch_context(self, *_):
        if self._context_key() != self._context or (not self._event_editor and _ and _[-1] == 'import'):
            self._context_invalid = True
        if self._context_invalid:
            self.save_button.setEnabled(False)
            self.error.setText(tr('当前赛事已切换，旧草稿不能保存。点击“重新读取”加载当前赛事，或取消关闭。'))

    def _draft_settings(self):
        values = dict(
            capture_bg=self.bg.currentData(), topmost=self.topmost.isChecked(),
            split_fullscreen_windows=self.split_fullscreen.isChecked(),
            show_melds=self.show_melds.isChecked(), theme=self.player_style.currentData(),
            overlay_font_family=self.overlay_font.currentFont().family(), overlay_font_scale=self.overlay_font_scale.value()/100,
            recognition_assist=self._recognition_value(),
            **self.card_editor.values(),
        )
        if not self._event_editor:
            values.update(console_theme=self.theme.currentData(), language=self.language.currentData(),
                          settings_font_size=self.settings_font_size.value(),
                          riichi_payment_mode=self.riichi_payment.currentData(), speed=self.speed.value(),
                          final_scores_hold=self.final_scores_hold.value(),
                          console_font_family=self.console_font.currentFont().family(),
                          console_font_size=self.console_font_size.value())
        return values

    def _live_settings(self):
        from app_settings import default_settings
        import artwork
        settings = default_settings()
        settings.update(language='zh_CN', console_font_family=artwork.FONT, console_font_size=14,
                        overlay_font_family=artwork.FONT, overlay_font_scale=1.)
        settings.update(copy.deepcopy(self.model.settings))
        from recognition_config import normalize_recognition_config
        settings['recognition_assist'] = normalize_recognition_config(settings.get('recognition_assist'))
        for key in ('console_font_family', 'overlay_font_family'):
            settings[key] = settings.get(key) or artwork.FONT
        return settings



    def _draft_windows(self):
        return {key: dict(visible=show.isChecked(), x=x.value(), y=y.value(), width=width.pixel_width())
                for key, show, x, y, width in self.values}

    def _live_windows(self):
        manager = self.main.manager
        return {key: dict(visible=manager.capture_enabled(key), x=window.x(), y=window.y(), width=window.width())
                for key, window in manager.layout_windows.items()}

    def _capture_baseline(self):
        self._baseline_settings = self._live_settings()
        self._baseline_draft = self._draft_settings()
        self._baseline_windows = self._live_windows()
        self._baseline_window_draft = self._draft_windows()
        self.mark_saved()

    def _pending_values(self):
        return (self._draft_settings(), self._draft_windows(),
                self.reset_windows.isChecked(), self.reset_desk.isChecked(),
                pending_spin_edits(self))

    def reload_drafts(self):
        """Discard this form's drafts, including after opening another event."""
        from card_style import normalize_card_style
        settings = self._live_settings()
        self._loading_drafts = True
        self._load_recognition(settings['recognition_assist'])
        for widget, key in ((self.player_style, 'theme'), (self.bg, 'capture_bg')):
            selected(widget, settings[key])
        for widget, key in ((self.show_melds, 'show_melds'), (self.topmost, 'topmost'),(self.split_fullscreen,'split_fullscreen_windows')):
            widget.setChecked(settings[key])
        if not self._event_editor:
            for widget, key in ((self.theme, 'console_theme'), (self.language, 'language'),
                                (self.riichi_payment, 'riichi_payment_mode')):
                selected(widget, settings[key])
            for widget, key in ((self.speed, 'speed'), (self.final_scores_hold, 'final_scores_hold'),
                                (self.console_font_size, 'console_font_size'), (self.settings_font_size, 'settings_font_size')):
                widget.setValue(settings[key])
            self.console_font.setCurrentFont(QFont(settings['console_font_family']))
        self.overlay_font_scale.setValue(round(settings['overlay_font_scale']*100))
        self.overlay_font.setCurrentFont(QFont(settings['overlay_font_family']))
        editor = self.card_editor
        editor.styles = {
            'mleague': normalize_card_style(settings['player_card_style'], 'mleague'),
            'saikouisen': normalize_card_style(settings['saikouisen_card_style'], 'saikouisen'),
        }
        selected(editor.editing_style, settings['theme'])
        editor.editing_theme = settings['theme']
        blocker = QSignalBlocker(editor.seat)
        editor.seat.clear()
        for index, player in enumerate(self.model.state['players']):
            editor.seat.addItem(player['name'], index)
        editor.seat.setCurrentIndex(min(3, max(0, getattr(self.main, 'selected', 0))))
        del blocker
        editor.load_all()
        windows = self._live_windows()
        for key, show, x, y, width in self.values:
            values = windows[key]
            show.setChecked(values['visible'])
            for control, field in ((x, 'x'), (y, 'y')):
                control.setValue(values[field])
            width.set_pixel_width(values['width'], self.main.manager.reference_width(key))
        self.reset_windows.setChecked(False)
        self.reset_desk.setChecked(False)
        self.event_id = (self.model.event or {}).get('id')
        self._context = self._context_key()
        self._context_invalid = False
        self.save_button.setEnabled(True)
        self.error.clear()
        self._capture_baseline()
        self._loading_drafts = False
        apply_theme(self, settings)

    def _save_live_font_size(self, key, value):
        """Save just this preference without committing any other form drafts."""
        if self._loading_drafts:
            return
        self._watch_context()
        if self._context_invalid:
            return
        previous = self.model.settings.get(key, 14)
        try:
            candidate = copy.deepcopy(self.model.settings)
            candidate[key] = value
            self.model.save_preferences(candidate)
        except Exception as error:
            control = getattr(self, key)
            blocker = QSignalBlocker(control)
            control.setValue(previous)
            del blocker
            self.error.setText(str(error))
            return
        self._baseline_settings[key] = value
        self._baseline_draft[key] = value
        # Keep unrelated drafts dirty; changing the size is already saved.
        baseline = self._settings_sections['settings']['baseline']
        baseline[0][key] = value
        self.error.clear()
        self.save_status.setText(tr('字号已即时保存'))

    def discard_settings(self):
        self.reload_drafts()

    def hideEvent(self, event):
        if self._event_editor:
            player = getattr(self, '_recognition_preview_player', None)
            if player is not None:
                self._stop_recognition_preview(player)
            self.main.manager.clear_capture_preview(self)
        super().hideEvent(event)

    def reset(self):
        self.reset_windows.setChecked(True)

    def apply(self):
        self.save_status.clear()
        self._watch_context()
        if self._context_invalid:
            return
        try:
            for control in self.findChildren(QAbstractSpinBox):
                if not control.hasAcceptableInput():raise ValueError('数字尚未填写完整，请检查后再保存')
                control.interpretText()
            # Validate before any write; a partially typed colour remains editable.
            settings, setting_keys, setting_conflicts = _merge_draft(
                self._baseline_draft, self._draft_settings(), self._baseline_settings, self._live_settings())
            live_windows = self._live_windows()
            windows, window_keys, window_conflicts = _merge_draft(
                self._baseline_window_draft, self._draft_windows(), self._baseline_windows, live_windows)
            unresolved_windows = [path for path in window_conflicts
                                  if path[-1] == 'visible' or not self.reset_windows.isChecked()]
            if setting_conflicts or unresolved_windows:
                self.error.setText(tr('同一设置已在其他窗口修改。为避免覆盖，请点击“重新读取”后再修改并保存。'))
                return False
            if not (setting_keys or window_keys or self.reset_windows.isChecked() or self.reset_desk.isChecked()):
                self._capture_baseline()
                self.error.clear();self.save_status.setText(tr('设置已保存'))
                return True
            manager = self.main.manager
            original_settings = self.model.settings
            original_enabled = manager.enabled.copy()
            original_transitions=manager.extension_transitions.copy()
            original_layout = manager.display_configuration.snapshot()
            self.model.settings = copy.deepcopy(original_settings)
            try:
                self.model.settings.update({key: settings[key] for key in setting_keys})
                if 'theme' in setting_keys:self.model.settings['player_style_version'] = 1
                for key in window_keys:
                    values, live = windows[key], live_windows[key]
                    manager.draft_capture_visibility(key,values['visible'])
                    if not self.reset_windows.isChecked() and any(values[field] != live[field] for field in ('x', 'y', 'width')):
                        window = manager.layout_windows[key]
                        if key=='players':manager.resize_player_capture(values['x'],values['y'],values['width'])
                        else:window.setGeometry(values['x'], values['y'], values['width'],round(values['width']*window.base[1]/window.base[0]))
                if any(windows[key]['visible'] != live_windows[key]['visible'] for key in window_keys):
                    self.model.settings['visible'] = manager.enabled.copy()
                manager.normalize_fullscreen_selection()
                for key in ('opening','postmatch'):
                    if key in window_keys:
                        window=manager.layout_windows[key]
                        self.model.settings['windows'][key]=[window.x(),window.y(),window.width(),window.height()]
                if self.reset_windows.isChecked():manager.reset_geometry(persist=False)
                else:manager.save_geometry(persist=False)
                candidate = self.model.settings
                self.model.settings = original_settings
                self.model.save_preferences(candidate)
                manager.accept_geometry()
            except Exception:
                self.model.settings = original_settings
                manager.enabled = original_enabled
                manager.extension_transitions=original_transitions
                manager.display_configuration.restore(original_layout)
                raise
            if self.reset_desk.isChecked() and hasattr(self.main, 'workspace'):self.main.workspace.reset_layout()
            self.reset_windows.setChecked(False)
            self.reset_desk.setChecked(False)
            for key, show, x, y, width in self.values:
                current = self._live_windows()[key]
                show.setChecked(current['visible'])
                for control, name in ((x, 'x'), (y, 'y')):control.setValue(current[name])
                width.set_pixel_width(current['width'], manager.reference_width(key))
            self._capture_baseline()
            apply_theme(self, self.model.settings)
            self.error.clear();self.save_status.setText(tr('设置已保存'))
            return True
        except Exception as error:
            self.error.setText(str(error))
            return False
