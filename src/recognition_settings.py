"""Recognition settings page, device scanning and preview handoff lifecycle.

The host supplies the shared settings navigation and model; this page keeps
optional recognition resources out of the general settings form lifecycle.
"""
import copy
from pathlib import Path
from PySide6.QtCore import Qt, Slot, QSignalBlocker
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QScrollArea, QStackedWidget, QVBoxLayout, QWidget, QFileDialog, QGroupBox,
    QApplication, QSizePolicy, QMenu,
)
from i18n import tr
from app_paths import LOCAL_RECOGNITION_SAMPLES
from recognition_runtime import MISSING_RECOGNITION, recognition_available, recognition_unavailable_reason
from ui_widgets import button, choice, decimal, integer, note, row, selected


_OFFLINE_SAMPLES = {
    'image': ('ML 实图样例', LOCAL_RECOGNITION_SAMPLES / 'mleague-hand-hd.jpg',
              (250 / 724, 105 / 407, 375 / 724, 170 / 407)),
    'video': ('构造听牌回放', LOCAL_RECOGNITION_SAMPLES / 'samples' / 'constructed-tenpai-replay.avi',
              (0, 0, 1, 1)),
}


class RecognitionSettingsPage:
    def _recognition(self):
        from recognition_config import normalize_recognition_config
        from recognition_source_editor import RecognitionSourceEditor, RecognitionPreviewSession
        from recognized_hand import RecognizedHandWidget
        config = normalize_recognition_config(self.model.settings.get('recognition_assist'))
        layout = self._page('牌面识别', 0, '识别 自动化 摄像头 OBS Spout 手牌 视频 回放 辅助填写 YOLO',
                            '四路来源绑定桌上固定位置。选中一个位置，连接画面后拖框选择暗手牌。')
        layout.setSpacing(8)
        self.recognition_runtime_note = note(MISSING_RECOGNITION)
        layout.addWidget(self.recognition_runtime_note)
        self.recognition_entry = self.entries[-1][0]
        self.recognition_scroll = self.pages.widget(self.pages.count() - 1)
        self.recognition_preview = RecognitionSourceEditor([p['name'] for p in self.model.state['players']])
        self.recognition_preview.hand.hide()  # All four hands stay visible in the overview.
        self.recognition_preview_session = RecognitionPreviewSession(self)
        self.recognition_preview_session.frame_received.connect(self._recognition_diagnostic_frame)
        self.recognition_preview_session.state_changed.connect(self._recognition_preview_state)
        self.recognition_preview.start_requested.connect(self._start_recognition_preview)
        self.recognition_preview.stop_requested.connect(self._stop_recognition_preview)
        self.recognition_preview.selected_player_changed.connect(self._select_recognition_preview)
        self.recognition_preview.roi_changed.connect(self._drag_recognition_roi)
        self.recognition_preview.refresh_devices_requested.connect(self._refresh_recognition_devices)
        self._recognition_devices = []
        self._recognition_senders = []
        self._recognition_scanner = None
        self._recognition_scanned = False
        self._recognition_spout_scanned = False
        self._recognition_preview_player = None
        self._recognition_preview_request = None
        self._recognition_handoff = None
        self._recognition_paused = set()
        self._recognition_preview_closed = False
        self._recognition_loading = False
        self._recognition_preview_error = ''
        self.recognition_sources = []
        overview = QGridLayout()
        overview.setSpacing(6)
        self.recognition_source_stack = QStackedWidget()
        self.recognition_roi_stack = QStackedWidget()
        for player in range(4):
            owner = self.model.state['players'][player]['name']
            group = QGroupBox(f'固定位置 {player + 1} · {owner}')
            group.setProperty('fixedSeatIndex', player)
            group.setStyleSheet('QGroupBox{margin-top:8px;padding:4px 6px 3px;}')
            body = QVBoxLayout(group)
            body.setContentsMargins(8, 6, 8, 4)
            body.setSpacing(2)
            enabled = QCheckBox('启用')
            preview_button = button('设置画面', lambda checked=False, index=player: self._select_recognition_source(index))
            preview_button.setCheckable(True)
            preview_button.setStyleSheet('QPushButton{min-height:18px;padding:0 5px;}')
            body.addLayout(row(enabled, 1, preview_button))
            result = RecognizedHandWidget(player)
            result.source_button.hide()
            result.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            result.set_observation([], status='尚未接入')
            body.addWidget(result)
            overview.addWidget(group, player // 2, player % 2)

            editor = QWidget()
            editor_layout = QVBoxLayout(editor)
            editor_layout.setContentsMargins(0, 0, 0, 0)
            editor_layout.setSpacing(5)
            kind = choice([('OBS 来源 · Spout', 'spout'), ('独立摄像头', 'camera'), ('图片', 'image'), ('视频回放', 'video')])
            path = QLineEdit()
            path.setMinimumWidth(0)
            device = choice([('请刷新摄像头设备', None)])
            device.setProperty('i18nLiteral', True)
            device.setMinimumWidth(0)
            sender = choice([])
            sender.setEditable(True)
            sender.setInsertPolicy(sender.InsertPolicy.NoInsert)
            sender.setProperty('i18nLiteral', True)
            sender.setMinimumWidth(0)
            sender.lineEdit().setPlaceholderText('刷新或填写 OBS 中该来源的 Spout 名称')
            browse = button('选择文件', lambda checked=False, index=player: self._choose_recognition_source(index))
            line = QHBoxLayout()
            line.addWidget(kind)
            for field in (path, device, sender):
                line.addWidget(field, 1)
            line.addWidget(browse)
            editor_layout.addLayout(line)
            hint = note('')
            editor_layout.addWidget(hint)
            self.recognition_source_stack.addWidget(editor)

            detail = QWidget()
            details = QVBoxLayout(detail)
            details.setContentsMargins(0, 0, 0, 0)
            area = QGridLayout()
            area.setHorizontalSpacing(6)
            roi = []
            for column, label in enumerate(('左', '上', '宽', '高')):
                field = decimal(0, 100, 2)
                field.setSuffix('%')
                field.setSingleStep(1)
                field.setMinimumWidth(0)
                area.addWidget(QLabel(label), 0, column * 2)
                area.addWidget(field, 0, column * 2 + 1)
                roi.append(field)
            details.addLayout(area)
            samples = button('离线样例', lambda: None)
            menu = QMenu(samples)
            for sample_kind, (label, sample_path, _) in _OFFLINE_SAMPLES.items():
                if sample_path.is_file():
                    menu.addAction(label, lambda checked=False, index=player, source=sample_kind:
                                   self._recognition_sample(index, source))
            samples.setMenu(menu)
            reset_roi = button('恢复全画面', lambda checked=False, index=player: self._drag_recognition_roi(index, (0., 0., 1., 1.)))
            details.addLayout(row(samples, reset_roi, 1))
            samples.setVisible(bool(menu.actions()))
            self.recognition_roi_stack.addWidget(detail)
            self.recognition_sources.append(dict(group=group, enabled=enabled, kind=kind, path=path,
                browse=browse, samples=samples, preview_button=preview_button, roi=roi, device=device,
                sender=sender, hint=hint, device_data={}, result=result))
            kind.currentIndexChanged.connect(lambda _, index=player: self._recognition_source_controls(index))
            device.currentIndexChanged.connect(lambda _, index=player: self._recognition_device_selected(index))
            sender.currentTextChanged.connect(lambda text, index=player: self._recognition_sender_selected(index, text))
            path.textChanged.connect(lambda _, index=player: self._recognition_source_edited(index))
            for field in roi:
                field.valueChanged.connect(lambda _, index=player: self._recognition_roi_controls(index))
        layout.addLayout(overview)
        self.recognition_preview.layout().insertWidget(1, self.recognition_source_stack)
        layout.addWidget(self.recognition_preview, 1)
        self.recognition_advanced_toggle = button('识别参数与精确裁切', lambda: None)
        self.recognition_advanced_toggle.setCheckable(True)
        self.recognition_advanced_toggle.setToolTip('通常保持默认即可；手牌区域直接在预览画面中拖选。')
        layout.addWidget(self.recognition_advanced_toggle)
        self.recognition_advanced = QWidget()
        advanced = QVBoxLayout(self.recognition_advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        self.recognition_model = choice([('Medium · 较准确', 'medium'), ('Small · 较快', 'small'), ('Nano · 更快', 'nano')])
        self.recognition_confidence = decimal(.1, .99, 2)
        self.recognition_confidence.setSingleStep(.05)
        advanced.addLayout(row('模型', self.recognition_model, '最低可信度', self.recognition_confidence, 1))
        advanced.addWidget(self.recognition_roi_stack)
        advanced.addWidget(note('裁框只影响识别；副露、明宝牌从导播台读取。离线构造回放仅用于流程检查。'))
        self.recognition_api_button = button('复制自动化接口连接', self._copy_recognition_api)
        self.recognition_api_button.setToolTip('为其他识别程序复制本机接口地址与访问令牌；只生成草稿，由导播确认提交。')
        advanced.addLayout(row(self.recognition_api_button, 1))
        self.recognition_advanced.hide()
        self.recognition_advanced_toggle.toggled.connect(self.recognition_advanced.setVisible)
        layout.addWidget(self.recognition_advanced)
        self._load_recognition(config)
        coordinator = getattr(self.main, 'inline_automation', None)
        if coordinator is not None:
            coordinator.preview_received.connect(self._recognition_saved_frame)
            coordinator.source_state_changed.connect(self._recognition_saved_state)
            for player, frame in getattr(coordinator, 'latest_previews', {}).items():
                self._recognition_saved_frame(player, *frame)

    def _select_recognition_source(self, player):
        self.recognition_preview.player.setCurrentIndex(player)
        self._select_recognition_preview(player)

    def _copy_recognition_api(self):
        coordinator = getattr(self.main, 'inline_automation', None)
        if coordinator is None:
            self.recognition_preview.status.setText('当前工作台没有启用自动化接口。')
            return
        try:
            server = coordinator.service.server
            url = server.start()
            QApplication.clipboard().setText(f'URL: {url}\nAuthorization: Bearer {server.token}')
            self.recognition_preview.status.setText('已复制本机接口地址与令牌；外部程序提交的结果仍由导播确认。')
        except Exception as error:
            self.recognition_preview.status.setText(f'无法开启自动化接口：{error}')

    def _recognition_source_controls(self, player):
        controls = self.recognition_sources[player]
        kind = controls['kind'].currentData()
        camera, spout = kind == 'camera', kind == 'spout'
        controls['browse'].setEnabled(not (camera or spout))
        controls['browse'].setVisible(not (camera or spout))
        controls['path'].setVisible(not (camera or spout))
        controls['device'].setVisible(camera)
        controls['sender'].setVisible(spout)
        controls['path'].setPlaceholderText('本地图片或视频路径')
        controls['hint'].setText(
            'OBS 每路摄像头添加独立 Spout 滤镜，并开启 Continuous filter broadcast；切镜时四路持续识别。' if spout else
            '摄像头若已被 OBS 占用，请在 OBS 中输出 Spout；单个 OBS 虚拟摄像头只能传送一路画面。' if camera else
            '用本地画面核对识别与裁框，保存后开始辅助填写。')
        if not self._recognition_loading:
            if camera:
                if not controls['path'].text().strip().isdecimal():
                    controls['path'].setText(str(controls['device_data']['index']) if 'index' in controls['device_data'] else '')
                self._fill_recognition_devices(player)
            elif spout:
                controls['path'].setText(controls['sender'].currentText())
                self._fill_recognition_senders(player)
        self._recognition_source_edited(player)

    def _load_recognition(self, config):
        self.recognition_runtime_note.setVisible(not recognition_available())
        self._recognition_loading = True
        selected(self.recognition_model, config['model'])
        self.recognition_confidence.setValue(config['confidence'])
        for player, (controls, source) in enumerate(zip(self.recognition_sources, config['sources'])):
            controls['group'].setTitle(f"固定位置 {player + 1} · {self.model.state['players'][player]['name']}")
            controls['enabled'].setChecked(source['enabled'])
            controls['device_data'] = {key: source[key] for key in ('identifier', 'name', 'index', 'backend') if key in source}
            selected(controls['kind'], source['kind'])
            controls['path'].setText(source['path'])
            for field, value in zip(controls['roi'], source['roi']):
                field.setValue(value * 100)
            self._recognition_source_controls(player)
            self._fill_recognition_devices(player)
            self._fill_recognition_senders(player)
        self._recognition_loading = False
        self._select_recognition_preview(self.recognition_preview.player.currentIndex())

    def _recognition_value(self):
        from recognition_config import normalize_recognition_config
        return normalize_recognition_config(dict(model=self.recognition_model.currentData(),
            confidence=self.recognition_confidence.value(), sources=[self._recognition_source_value(player)
                for player in range(4)]), strict=True)

    def _recognition_source_value(self, player):
        controls = self.recognition_sources[player]
        result = dict(enabled=controls['enabled'].isChecked(), kind=controls['kind'].currentData(),
            path=controls['path'].text(), roi=[field.value() / 100 for field in controls['roi']])
        if result['kind'] == 'camera':
            result.update(controls['device_data'])
        return result

    def _fill_recognition_devices(self, player):
        controls = self.recognition_sources[player]
        combo = controls['device']
        desired = controls['device_data']
        identity = desired.get('identifier')
        blocker = QSignalBlocker(combo)
        combo.clear()
        match = -1
        for device in self._recognition_devices:
            data = device.to_dict() if hasattr(device, 'to_dict') else dict(device)
            combo.addItem(data['name'], data)
            if identity and identity == data['identifier']:
                match = combo.count() - 1
            elif not identity and str(data['index']) == controls['path'].text().strip():
                match = combo.count() - 1
        if match < 0 and identity:
            combo.insertItem(0, desired.get('name', '已保存设备') + '（未连接）', dict(desired))
            match = 0
        elif match < 0 and controls['path'].text().strip() and controls['kind'].currentData() == 'camera':
            combo.insertItem(0, '已保存摄像头 ' + controls['path'].text().strip() + '（未确认设备）', dict(desired))
            match = 0
        if not combo.count():
            combo.addItem('未发现摄像头，连接设备后刷新' if self._recognition_scanned else '请刷新摄像头设备', None)
        combo.setCurrentIndex(match if match >= 0 else 0)
        del blocker
        if controls['kind'].currentData() == 'camera' and combo.currentData() and combo.currentData().get('identifier') and any(
                (device.identifier if hasattr(device, 'identifier') else device['identifier']) == combo.currentData()['identifier']
                for device in self._recognition_devices):
            self._recognition_device_selected(player)

    def _recognition_device_selected(self, player):
        controls = self.recognition_sources[player]
        if controls['kind'].currentData() != 'camera':
            return
        data = controls['device'].currentData()
        if isinstance(data, dict) and data.get('identifier'):
            controls['device_data'] = dict(data)
            controls['path'].setText(str(data['index']))
            self._recognition_source_edited(player)

    def _fill_recognition_senders(self, player):
        controls = self.recognition_sources[player]
        combo = controls['sender']
        desired = controls['path'].text().strip() if controls['kind'].currentData() == 'spout' else combo.currentText()
        blocker = QSignalBlocker(combo)
        combo.clear()
        combo.addItems(self._recognition_senders)
        # Never replace a fixed position's missing sender with another live feed.
        combo.setCurrentIndex(-1)
        combo.setEditText(desired)
        combo.setToolTip('该来源暂未发送；名称保持不变，恢复后自动重连。' if desired and self._recognition_spout_scanned and desired not in self._recognition_senders else '与 OBS 的 Spout 滤镜名称完全一致；每个位置使用独立名称。')
        del blocker

    def _recognition_sender_selected(self, player, text):
        controls = self.recognition_sources[player]
        if controls['kind'].currentData() == 'spout' and not self._recognition_loading:
            controls['path'].setText(text)

    @staticmethod
    def _same_capture_source(first, second):
        if first.get('kind') != second.get('kind'):
            return False
        if first.get('kind') == 'camera' and first.get('identifier') and second.get('identifier'):
            return first['identifier'] == second['identifier']
        return str(first.get('path', '')).strip() == str(second.get('path', '')).strip()

    def _recognition_source_edited(self, player):
        if self._recognition_loading:
            return
        source = self._recognition_source_value(player)
        previous = self.recognition_preview_session._source
        if self._recognition_preview_player == player:
            if previous and self._same_capture_source(previous, source):
                return
            self._stop_recognition_preview(player)
        self.recognition_preview.invalidate(player)
        self.recognition_sources[player]['result'].set_observation([], status='来源已修改，连接预览后核对', stale=True)

    def _refresh_recognition_devices(self):
        if self._recognition_scanner and self._recognition_scanner.isRunning():
            return
        kind = self.recognition_sources[self.recognition_preview.player.currentIndex()]['kind'].currentData()
        reason = recognition_unavailable_reason(kind)
        if reason:
            self.recognition_preview.status.setText(tr(reason))
            return
        from recognition_source_editor import CameraScanWorker, SpoutScanWorker, retain_worker
        spout = kind == 'spout'
        self._recognition_scanner = SpoutScanWorker() if spout else CameraScanWorker()
        retain_worker(self._recognition_scanner)
        self._recognition_scanner.devices_ready.connect(self._recognition_senders_ready if spout else self._recognition_devices_ready, Qt.ConnectionType.QueuedConnection)
        self._recognition_scanner.failed.connect(self._recognition_devices_failed, Qt.ConnectionType.QueuedConnection)
        self._recognition_scanner.finished.connect(self._recognition_scan_finished, Qt.ConnectionType.QueuedConnection)
        self.recognition_preview.refresh_button.setEnabled(False)
        self.recognition_preview.status.setText('正在查找 OBS / Spout 来源…' if spout else '正在读取摄像头设备名称…')
        self._recognition_scanner.start()

    @Slot(object)
    def _recognition_devices_ready(self, devices):
        if self._recognition_preview_closed:
            return
        self._recognition_devices = sorted(devices, key=lambda device: 0 if 'obs' in str(
            device.name if hasattr(device, 'name') else device.get('name', '')).lower() else 1)
        self._recognition_scanned = True
        for player in range(4):
            self._fill_recognition_devices(player)
        self.recognition_preview.refresh_button.setEnabled(True)
        self.recognition_preview.status.setText(f'发现 {len(devices)} 个摄像头设备；选择后连接预览。')

    @Slot(str)
    def _recognition_devices_failed(self, message):
        if not self._recognition_preview_closed:
            self.recognition_preview.refresh_button.setEnabled(True)
            self.recognition_preview.status.setText(message)

    @Slot(object)
    def _recognition_senders_ready(self, senders):
        if self._recognition_preview_closed:
            return
        self._recognition_senders = sorted(set(str(sender) for sender in senders))
        self._recognition_spout_scanned = True
        for player in range(4):
            self._fill_recognition_senders(player)
        self.recognition_preview.refresh_button.setEnabled(True)
        self.recognition_preview.status.setText(f'发现 {len(self._recognition_senders)} 路 Spout 来源；按固定位置选择对应名称。' if senders else '尚未发现 Spout 来源。请在 OBS 每路摄像头上启用 Spout 滤镜和持续发送。')

    @Slot()
    def _recognition_scan_finished(self):
        if self.sender() is self._recognition_scanner:
            self._recognition_scanner = None

    def _select_recognition_preview(self, player):
        if not self.recognition_sources:
            return
        if self._recognition_preview_player is not None and self._recognition_preview_player != player:
            self._stop_recognition_preview(self._recognition_preview_player)
        controls = self.recognition_sources[player]
        self.recognition_source_stack.setCurrentIndex(player)
        self.recognition_roi_stack.setCurrentIndex(player)
        for index, source in enumerate(self.recognition_sources):
            source['preview_button'].setChecked(index == player)
        self.recognition_preview.preview.set_roi([field.value() / 100 for field in controls['roi']])

    def _drag_recognition_roi(self, player, roi):
        controls = self.recognition_sources[player]
        blockers = [QSignalBlocker(field) for field in controls['roi']]
        for field, value in zip(controls['roi'], roi):
            field.setValue(value * 100)
        del blockers
        self._recognition_roi_controls(player)

    def _recognition_roi_controls(self, player):
        if self._recognition_loading:
            return
        roi = tuple(field.value() / 100 for field in self.recognition_sources[player]['roi'])
        x, y, width, height = roi
        if width <= 0 or height <= 0 or x + width > 1.000000001 or y + height > 1.000000001:
            return
        if self.recognition_preview.player.currentIndex() == player:
            self.recognition_preview.preview.set_roi(roi)
        self.recognition_preview_session.set_roi(player, roi)

    def _start_recognition_preview(self, player):
        try:
            self._recognition_preview_error = ''
            from recognition_config import default_recognition_config, normalize_recognition_config
            source = self._recognition_source_value(player)
            source['enabled'] = True
            config = default_recognition_config()
            config['sources'][player] = source
            source = normalize_recognition_config(config, strict=True)['sources'][player]
            if self.recognition_preview_session.worker_factory is None:
                reason = recognition_unavailable_reason(source['kind'])
                if reason:
                    raise ValueError(tr(reason))
            if source['kind'] == 'camera' and self._recognition_scanned and not self._recognition_devices:
                raise ValueError('未发现摄像头，请连接设备后刷新设备列表')
            if self._recognition_preview_player is not None and self._recognition_preview_player != player:
                self._stop_recognition_preview(self._recognition_preview_player)
            self._recognition_preview_player = player
            self._recognition_preview_request = (player, source, self.recognition_model.currentData(), self.recognition_confidence.value())
            coordinator = getattr(self.main, 'inline_automation', None)
            parallel = bool(getattr(coordinator, 'supports_parallel_preview', False))
            old = coordinator.workers.get(player) if coordinator is not None and not parallel else None
            if coordinator is not None and not parallel:
                coordinator.pause_for_preview(player)
                self._recognition_paused.add(player)
            self.recognition_preview.set_running(player, True, '正在连接设备预览…')
            if old is not None and old.isRunning():
                self._recognition_handoff = old
                old.finished.connect(self._launch_recognition_preview, Qt.ConnectionType.QueuedConnection)
            else:
                self._launch_recognition_preview()
        except Exception as error:
            self.recognition_preview.set_running(player, False, str(error))

    def _preview_recognition_source(self, player):
        self.recognition_preview.player.setCurrentIndex(player)
        self._start_recognition_preview(player)
        self.recognition_scroll.ensureWidgetVisible(self.recognition_preview)

    @Slot()
    def _launch_recognition_preview(self):
        if not self._recognition_preview_request or self._recognition_preview_closed:
            return
        if self._recognition_handoff is not None and self.sender() is not self._recognition_handoff:
            return
        player, source, model, confidence = self._recognition_preview_request
        self._recognition_preview_request = None
        self._recognition_handoff = None
        self.recognition_preview_session.start(player, source, model=model, confidence=confidence)

    def _stop_recognition_preview(self, player):
        self._recognition_preview_request = None
        self.recognition_preview_session.stop()
        self.recognition_preview.set_running(player, False, '预览已停止；未保存的区域没有用于导播辅助。')
        if self.recognition_sources[player]['kind'].currentData() != 'image':
            self.recognition_preview.mark_stale(player, '预览已停止')
            hand = self.recognition_sources[player]['result']
            hand.set_observation(hand.tiles, status='预览已停止', stale=True)
        self._recognition_preview_player = None
        self._resume_recognition_sources()
        self._recognition_handoff = None

    def _resume_recognition_sources(self):
        coordinator = getattr(self.main, 'inline_automation', None)
        if coordinator is None or not self._recognition_paused:
            return
        players = tuple(self._recognition_paused)
        self._recognition_paused.clear()
        candidates = [self.recognition_preview_session.worker, self._recognition_handoff]
        workers = [worker for worker in candidates if worker is not None]
        resumed = False
        def resume():
            nonlocal resumed
            if resumed:
                return
            for worker in workers:
                try:
                    if worker.isRunning():
                        return
                except RuntimeError:
                    pass
            resumed = True
            for player in players:
                try:
                    coordinator.resume_after_preview(player)
                except RuntimeError:
                    pass
        for worker in workers:
            try:
                if worker.isRunning():
                    worker.finished.connect(resume, Qt.ConnectionType.QueuedConnection)
            except RuntimeError:
                pass  # The coordinator may already have released a finished worker.
        resume()

    @Slot(int, object, object)
    def _recognition_diagnostic_frame(self, player, image, result):
        if not self._recognition_preview_closed:
            self.recognition_preview.show_frame(player, image, result)
            self._set_recognition_result(player, result)

    @Slot(int, object, object)
    def _recognition_saved_frame(self, player, image, result):
        if not self._recognition_preview_closed:
            from recognition_config import normalize_recognition_config
            live = normalize_recognition_config(self.model.settings.get('recognition_assist'))['sources'][player]
            draft = self._recognition_source_value(player)
            if not self._same_capture_source(live, draft) or any(abs(a - b) > .0001 for a, b in zip(live['roi'], draft['roi'])):
                return
            self._set_recognition_result(player, result)
            if self._recognition_preview_player is None:
                self.recognition_preview.show_frame(player, image, result)
                if self._recognition_preview_error:
                    self.recognition_preview.status.setText(self._recognition_preview_error)

    def _set_recognition_result(self, player, result):
        confidence = min((d.confidence for d in result.detections), default=None)
        self.recognition_sources[player]['result'].set_observation([d.tile for d in result.detections],
            source=result.provider or result.source, confidence=confidence,
            status=f'{len(result.tiles)} 张 · {result.elapsed_ms:.0f} ms / 帧')

    @Slot(int, str)
    def _recognition_preview_state(self, player, message):
        if not self._recognition_preview_closed:
            source = self.recognition_preview_session._source or {}
            if message and (message != '预览已停止' or source.get('kind') != 'image'):
                self.recognition_preview.mark_stale(player, message)
                hand = self.recognition_sources[player]['result']
                hand.set_observation(hand.tiles, status=message, stale=True)
            if not self.recognition_preview_session.running and message != '预览已停止':
                self._recognition_preview_error = message
                self._recognition_preview_player = None
                self._resume_recognition_sources()
            self.recognition_preview.set_running(player,
                self.recognition_preview_session.running or player in self._recognition_paused, message)

    @Slot(int, str)
    def _recognition_saved_state(self, player, message):
        if not self._recognition_preview_closed:
            from recognition_config import normalize_recognition_config
            live = normalize_recognition_config(self.model.settings.get('recognition_assist'))['sources'][player]
            draft = self._recognition_source_value(player)
            if (self._recognition_preview_player == player
                    or not self._same_capture_source(live, draft)
                    or any(abs(a - b) > .0001 for a, b in zip(live['roi'], draft['roi']))):
                return
            self.recognition_sources[player]['result'].setToolTip(message)
            frame_summary = ' 张 · ' in message and message.endswith(' ms')
            if message and not frame_summary and message != '已连接 · 正在识别':
                hand = self.recognition_sources[player]['result']
                hand.set_observation(hand.tiles, status=message, stale=True)
                if self._recognition_preview_player != player:
                    self.recognition_preview.mark_stale(player, message)

    def _shutdown_recognition_preview(self, *_):
        if self._recognition_preview_closed:
            return
        self._recognition_preview_closed = True
        self._recognition_preview_request = None
        self.recognition_preview_session.close()
        self._resume_recognition_sources()
        from recognition_source_editor import drain_worker
        drain_worker(self._recognition_scanner)

    def _choose_recognition_source(self, player):
        controls = self.recognition_sources[player]
        path, _ = QFileDialog.getOpenFileName(self, '选择手牌画面', controls['path'].text(),
            '图片与视频 (*.png *.jpg *.jpeg *.webp *.bmp *.mp4 *.mkv *.avi *.mov);;所有文件 (*)')
        if path:
            controls['path'].setText(path)
            selected(controls['kind'], 'image' if Path(path).suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.bmp') else 'video')

    def _recognition_sample(self, player, kind):
        _, path, roi = _OFFLINE_SAMPLES[kind]
        if not path.is_file():
            self.recognition_preview.status.setText(tr('本机样例文件不存在，请选择自己的图片或视频。'))
            return
        controls = self.recognition_sources[player]
        selected(controls['kind'], kind)
        controls['path'].setText(str(path))
        controls['enabled'].setChecked(True)
        for field, value in zip(controls['roi'], roi):
            field.setValue(value * 100)

    def focus_recognition(self, player=None):
        self.search.clear()
        self.nav.setCurrentItem(self.recognition_entry)
        self._select(self.recognition_entry)
        spout = self.recognition_sources[player if player is not None else self.recognition_preview.player.currentIndex()]['kind'].currentData() == 'spout'
        if player is not None:
            self.recognition_preview.player.setCurrentIndex(player)
        if not (self._recognition_spout_scanned if spout else self._recognition_scanned):
            self._refresh_recognition_devices()
        if type(player) is int and 0 <= player < 4:
            self.recognition_preview.player.setCurrentIndex(player)
            self.recognition_scroll.ensureWidgetVisible(self.recognition_preview)
