"""主窗口：把各张配置卡和两个页签拼起来，并接住启动/停止。

真正的功能都在旁边的模块里：
  ui/printer_list.py  打印机列表组件
  ui/panels/          日志、接收历史两个页签
  ui/profiles.py      配置方案（mixin）
  ui/server_control.py 启停服务（mixin）
  ui/dialogs/         各种弹窗
"""

import json
import threading

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget,
)

from data_receiver import (
    DEFAULT_REPLY_TIMEOUT, RESPONSE_DEFAULTS, RESPONSE_LABELS, configure_responses,
)
from logger import print_log
from printer_utils import DEFAULT_RAW_PORT, NETWORK_PRINTER, SYSTEM_PRINTER
from theme import SYSTEM, THEME_CHOICES, apply_theme
from ui.constants import (
    DATA_ADDR, DISPATCH_CHOICES, FLOW_CHOICES, MAX_REPLY_TIMEOUT, MIN_REPLY_TIMEOUT,
    SERIAL, TWOWAY,
)
from ui.dialogs import HttpResponseDialog
from ui.helpers import clamp_timeout, normalize_addr
from ui.panels import HistoryPanel, LogPanel
from ui.printer_list import PrinterListWidget
from ui.profiles import ProfileMixin
from ui.server_control import ServerMixin


class WebSocketServerGUI(ProfileMixin, ServerMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("PrintBridge", "PrintBridge")
        # 默认跟随系统；有用户选择后记住
        self.theme_mode = self.settings.value("theme", SYSTEM)
        if self.theme_mode not in {mode for _, mode in THEME_CHOICES}:
            self.theme_mode = SYSTEM
        # HTTP 应答表是全局的（错误语义跟仓库无关），不进方案
        self.http_responses = self._load_responses()

        # 配置方案：一个仓库一套（端口 / 打印机 / 数据地址 / 功能开关），可命名、可切换
        self._applying_profile = False   # 程序在回填输入框，别把它当成用户改动
        self._ui_ready = False           # 建界面途中控件还不齐，别去读它们
        self._applied_config = None      # 上一次落到界面上的配置，用来判断"改过没有"
        self._first_run = not self.settings.value("profiles")
        self.profiles = self._load_profiles()
        names = [p["name"] for p in self.profiles]
        self.default_profile = self.settings.value("default_profile")
        if self.default_profile not in names:
            self.default_profile = names[0]
        # 启动载入默认方案（不是上次用的那套）——用户明确指定的才是默认
        self.current_profile = self.default_profile
        self.current_addr = self.profiles[names.index(self.current_profile)]["data_addr"]

        self.ws_ready = threading.Event()
        self.http_ready = threading.Event()

        self.ws_thread = None
        self.http_thread = None
        self.ws_stop_event = threading.Event()
        self.running = False

        self.init_ui()
        self._apply_theme()

        # 跟随系统时，系统在浅色/深色之间切换后实时跟着变
        QApplication.instance().styleHints().colorSchemeChanged.connect(
            self._on_system_scheme_changed
        )

        # 日志与接收历史两个面板各自带定时器消费自己的队列，主窗口不再管
        print_log("🚀 Print Bridge 已就绪")

    # ------------------------------------------------------------------ #
    # 界面构建
    # ------------------------------------------------------------------ #

    def init_ui(self):
        self.setObjectName("root")
        self.setWindowTitle("Print Bridge (V1.0.0)")
        # 配置区已经挪进可滚动的页签，窗口不必再为它留高度：
        # 只要容纳顶栏 + 启停条 + 页签头 + 表格几行即可，笔记本屏幕也放得下。
        self.setMinimumSize(900, 620)
        self.resize(1080, 820)

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)

        layout.addLayout(self._build_header())
        # 启停和状态常驻：在任何页签下都要能一眼看到服务跑没跑、能随手停
        layout.addLayout(self._build_action_bar())

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)      # macOS 下默认会居中并压缩页签，这里改为左对齐
        self.tabs.tabBar().setExpanding(False)
        self.log_panel = LogPanel(self)
        self.history = HistoryPanel(self.settings, self)
        self.history.countChanged.connect(self._update_history_tab_title)
        self.config_tab_index = self.tabs.addTab(self._build_config_page(), "配置 ⚙")
        self.tabs.addTab(self.log_panel, "日志输出 📋")
        self.history_tab_index = self.tabs.addTab(self.history, "接收历史 📜")
        layout.addWidget(self.tabs, 1)

        self.setLayout(layout)
        self.toggle_modules()

        # 控件都建好了才能回填：载入默认方案，并让下拉框显示 ★ 标记
        # （建卡片途中 refresh_printers() 等会发信号，_ui_ready 之前一律不去读输入框）
        self._ui_ready = True
        self._refresh_profile_combo()
        self._apply_profile(self.profiles[self._profile_index(self.current_profile)])

        # 头一次用的人先落在「配置」上；配过之后日常关心的是日志
        self.tabs.setCurrentIndex(
            self.config_tab_index if self._first_run else self.tabs.indexOf(self.log_panel)
        )

    def _build_action_bar(self):
        """启动/停止 + 状态，常驻在页签上方。"""
        row = QHBoxLayout()
        row.setSpacing(10)

        self.start_btn = QPushButton("启动服务器")
        self.start_btn.setMinimumWidth(160)
        self.start_btn.clicked.connect(self.toggle_server)
        row.addWidget(self.start_btn)

        self.status_label = QLabel("状态：就绪")
        self.status_label.setObjectName("status")
        row.addWidget(self.status_label, 1)
        self._set_status("就绪，等待启动", "idle")
        return row

    def _build_config_page(self):
        """「配置」页签：五张卡纵向堆叠，装进滚动区。

        套 QScrollArea 是为了让窗口能缩小——卡片加到五张之后，
        直接堆在主界面上会把窗口最小高度顶到 1400 多，笔记本屏放不下。
        """
        col = QVBoxLayout()
        col.setContentsMargins(0, 4, 6, 4)   # 右边 6px 留给滚动条，卡片不贴边
        col.setSpacing(8)
        # 连接/数据卡有两行（输入框+提示），最小高度不写死：
        # 卡片被压扁后 hint 会压到输入框上，交给控件自身的 minimumSizeHint 实时计算。
        for card in (self._build_profile_card(), self._build_function_card(),
                     self._build_connection_card(), self._build_print_card(),
                     self._build_data_card()):
            col.addWidget(card)
        col.addStretch()

        page = QWidget()
        page.setObjectName("configPage")
        page.setLayout(col)

        area = QScrollArea()
        area.setObjectName("configArea")
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(page)
        return area

    def _build_header(self):
        row = QHBoxLayout()

        title = QLabel("Print Bridge")
        title.setObjectName("title")
        row.addWidget(title)
        row.addStretch()

        theme_label = QLabel("主题")
        theme_label.setObjectName("hint")
        row.addWidget(theme_label)
        row.addWidget(self._build_theme_switch())

        return row

    def _build_theme_switch(self):
        """分段控件而非下拉框：与界面的扁平按钮风格一致，且一次点击即可切换。"""
        container = QWidget()
        container.setObjectName("segment")

        box = QHBoxLayout(container)
        box.setContentsMargins(3, 3, 3, 3)
        box.setSpacing(2)

        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)
        for label, mode in THEME_CHOICES:
            button = QPushButton(label)
            button.setObjectName("seg")
            button.setCheckable(True)
            button.setChecked(mode == self.theme_mode)
            button.setProperty("mode", mode)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # 避免启动时抢到焦点框
            self.theme_group.addButton(button)
            box.addWidget(button)

        self.theme_group.buttonClicked.connect(
            lambda button: self.set_theme(button.property("mode"))
        )
        return container

    def _build_profile_card(self):
        """一个仓库一套配置：选中即整套切换，★ 那套开机自动载入。"""
        card = QGroupBox("配置方案  📁")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(0)     # hint 间距由 hint_row 容器 margin 接管

        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumHeight(28)   # 与 LineEdit 对齐，避免被 group title 压扁
        self.profile_combo.setToolTip("切换后端口 / 打印机 / 数据地址会整套换成这一方案")
        self.profile_combo.currentIndexChanged.connect(self.on_profile_selected)

        buttons = [
            ("保存", "把当前界面上的配置写回这一方案", self.save_profile, ""),
            ("新建", "用当前配置另存为一套新方案（例如 仓库A）", self.new_profile, "secondary"),
            ("重命名", "给当前方案改个名字", self.rename_profile, "secondary"),
            ("删除", "删除当前方案", self.delete_profile, "secondary"),
            ("设为默认", "启动程序时自动载入这一方案", self.set_default_profile, "secondary"),
        ]
        grid.addWidget(QLabel("当前方案:"), 0, 0)
        grid.addWidget(self.profile_combo, 0, 1)
        grid.setColumnStretch(1, 1)

        self.profile_buttons = []
        for column, (text, tip, slot, style) in enumerate(buttons, start=2):
            button = QPushButton(text)
            button.setObjectName(style)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            grid.addWidget(button, 0, column)
            self.profile_buttons.append(button)

        self.profile_hint = QLabel()
        self.profile_hint.setObjectName("hint")
        self.profile_hint.setMinimumHeight(20)   # 容纳一行 hint 文字，避免被 grid 拉伸/裁切
        hint_row = QWidget()
        hint_row.setFixedHeight(26)         # 6(顶间距) + 20(文字)，定死高度，不随外部空间伸缩
        hint_layout = QHBoxLayout(hint_row)
        hint_layout.setContentsMargins(0, 6, 0, 0)   # ← 顶间距定死 6px
        hint_layout.setSpacing(0)
        hint_layout.addWidget(self.profile_hint)
        grid.addWidget(hint_row, 1, 0, 1, 2 + len(buttons))

        # 底部留 8px 呼吸，与其余几张卡结构对齐
        bottom_spacer = QWidget()
        bottom_spacer.setMinimumHeight(16)
        bottom_layout = QHBoxLayout(bottom_spacer)
        bottom_layout.setContentsMargins(0, 8, 0, 0)
        bottom_layout.setSpacing(0)
        grid.addWidget(bottom_spacer, 2, 0, 1, 2 + len(buttons))

        card.setLayout(grid)
        return card

    def _build_function_card(self):
        card = QGroupBox("功能选择  🛠")
        row = QHBoxLayout()
        self.print_checkbox = QCheckBox("自动打印")
        self.data_checkbox = QCheckBox("获取数据")
        self.print_checkbox.setChecked(False)  # 自动打印默认不启用
        self.data_checkbox.setChecked(True)
        self.print_checkbox.stateChanged.connect(self.toggle_modules)
        self.data_checkbox.stateChanged.connect(self.toggle_modules)
        self.print_checkbox.stateChanged.connect(self._update_profile_hint)
        self.data_checkbox.stateChanged.connect(self._update_profile_hint)
        row.addWidget(self.print_checkbox)
        row.addWidget(self.data_checkbox)
        row.addStretch()
        card.setLayout(row)
        return card

    def _build_connection_card(self):
        """WebSocket 是前端的通信通道，两个功能都依赖它，因此独立成卡片、始终显示。"""
        card = QGroupBox("前端连接  🔌")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(0)     # hint 间距由 hint_row 容器 margin 接管

        self.port_input = QLineEdit("8765")
        self.port_input.setPlaceholderText("默认端口 8765")
        self.port_input.textChanged.connect(self._update_ws_hint)
        self.port_input.textChanged.connect(self._update_profile_hint)

        grid.addWidget(QLabel("WebSocket 端口 (1024-65535):"), 0, 0)
        grid.addWidget(self.port_input, 0, 1)
        grid.setColumnStretch(1, 1)

        self.ws_hint = QLabel()
        self.ws_hint.setObjectName("hint")
        self.ws_hint.setMinimumHeight(20)   # 容纳一行 hint 文字，避免被 grid 拉伸裁切
        # 用 QWidget 容器包 hint，强制给一个 6px 的顶部间距，避免与输入框贴边
        hint_row = QWidget()
        hint_row.setFixedHeight(26)         # 6(顶间距) + 20(文字)，定死高度，不随外部空间伸缩
        hint_layout = QHBoxLayout(hint_row)
        hint_layout.setContentsMargins(0, 6, 0, 0)   # ← 顶间距定死 6px
        hint_layout.setSpacing(0)
        hint_layout.addWidget(self.ws_hint)
        grid.addWidget(hint_row, 1, 0, 1, 2)

        card.setLayout(grid)
        self._update_ws_hint()
        return card

    def _build_print_card(self):
        """打印机列表本身是独立组件，这里只套一层卡片外壳。"""
        self.print_group = QGroupBox("自动打印配置  🖨")
        box = QVBoxLayout(self.print_group)
        box.setContentsMargins(0, 0, 0, 0)
        self.printer_list = PrinterListWidget(self.print_group)
        self.printer_list.changed.connect(self._update_profile_hint)
        box.addWidget(self.printer_list)
        return self.print_group

    def _build_data_card(self):
        self.data_group = QGroupBox("获取数据配置  🌐")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(0)     # hint 间距由 hint_row 容器 margin 接管

        # 地址可自由填写；在不同仓库之间整套切换交给顶部的「配置方案」
        self.addr_input = QLineEdit(self.current_addr)
        self.addr_input.setPlaceholderText(f"默认 {DATA_ADDR}")
        self.addr_input.setToolTip("host:port/path，http:// 前缀和端口可省略")
        self.addr_input.textChanged.connect(self._update_data_hint)
        self.addr_input.textChanged.connect(self._update_profile_hint)

        grid.addWidget(QLabel("数据获取地址:"), 0, 0)
        grid.addWidget(self.addr_input, 0, 1, 1, 4)
        grid.setColumnStretch(1, 1)

        # 数据流：单向只管转发；双向要等前端回一条，再拿它答复设备
        self.flow_combo = QComboBox()
        self.flow_combo.setMinimumHeight(28)   # 与 LineEdit 对齐，避免被 group title 压扁
        for label, mode in FLOW_CHOICES:
            self.flow_combo.addItem(label, mode)
        self.flow_combo.setToolTip(
            "单向：设备 → 本程序 → 前端，收下就回 200\n"
            "双向：设备 → 本程序 → 前端 → 本程序 → 设备，用前端的回复答复设备"
        )
        self.flow_combo.currentIndexChanged.connect(self._on_flow_changed)

        self.timeout_input = QLineEdit(str(DEFAULT_REPLY_TIMEOUT))
        self.timeout_input.setFixedWidth(64)
        self.timeout_input.setToolTip(
            "双向模式下设备最多等多久（秒）；单发模式下排队和等回复共用这份预算"
        )
        self.timeout_input.textChanged.connect(self._update_data_hint)
        self.timeout_input.textChanged.connect(self._update_profile_hint)
        self.timeout_label = QLabel("超时(秒):")

        self.dispatch_combo = QComboBox()
        self.dispatch_combo.setMinimumHeight(28)
        for label, mode in DISPATCH_CHOICES:
            self.dispatch_combo.addItem(label, mode)
        self.dispatch_combo.setToolTip(
            "并发：多条请求同时处理，第 2 条不必等第 1 条回来\n"
            "单发：排队串行，一条处理完才轮到下一条"
        )
        self.dispatch_combo.currentIndexChanged.connect(self._on_flow_changed)

        self.http_resp_btn = QPushButton("HTTP 响应…")
        self.http_resp_btn.setObjectName("secondary")
        self.http_resp_btn.setToolTip("维护各种情况下返回给设备的 HTTP 码和说明")
        self.http_resp_btn.clicked.connect(self.open_http_responses)

        # 这一行四个控件各按内容取宽度，剩下的空间留给右侧按钮前的空白，
        # 不像放在 grid 里那样让第一个下拉把整行都吃掉
        self.flow_combo.setFixedWidth(200)
        self.dispatch_combo.setFixedWidth(96)
        flow_row = QWidget()
        flow_box = QHBoxLayout(flow_row)
        flow_box.setContentsMargins(0, 0, 0, 0)
        flow_box.setSpacing(12)
        flow_box.addWidget(self.flow_combo)
        flow_box.addWidget(self.timeout_label)
        flow_box.addWidget(self.timeout_input)
        flow_box.addWidget(self.dispatch_combo)
        flow_box.addStretch()
        flow_box.addWidget(self.http_resp_btn)

        grid.addWidget(QLabel("数据流:"), 1, 0)
        grid.addWidget(flow_row, 1, 1, 1, 4)

        self.data_hint = QLabel()
        self.data_hint.setObjectName("hint")
        self.data_hint.setMinimumHeight(20)   # 容纳一行 hint 文字，避免被 grid 拉伸/裁切
        # 用 QWidget 容器包 hint，给一个固定的 6px 顶间距，避免与输入框贴边
        hint_row = QWidget()
        hint_row.setFixedHeight(26)         # 6(顶间距) + 20(文字)，定死高度，不随外部空间伸缩
        hint_layout = QHBoxLayout(hint_row)
        hint_layout.setContentsMargins(0, 6, 0, 0)   # ← 顶间距定死 6px
        hint_layout.setSpacing(0)
        hint_layout.addWidget(self.data_hint)
        grid.addWidget(hint_row, 2, 0, 1, 5)

        # 底部留 8px 呼吸，跟启动按钮之间留出空间，三张卡结构对齐
        bottom_spacer = QWidget()
        bottom_spacer.setMinimumHeight(16)
        bottom_layout = QHBoxLayout(bottom_spacer)
        bottom_layout.setContentsMargins(0, 8, 0, 0)
        bottom_layout.setSpacing(0)
        grid.addWidget(bottom_spacer, 3, 0, 1, 5)
        self._on_flow_changed()

        self.data_group.setLayout(grid)
        return self.data_group

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #

    def toggle_modules(self):
        self.print_group.setVisible(self.print_checkbox.isChecked())
        self.data_group.setVisible(self.data_checkbox.isChecked())

    def _update_ws_hint(self):
        port = self.port_input.text().strip() or "8765"
        self.ws_hint.setText(
            f"前端连接 ws://localhost:{port} —— 打印和数据推送共用这一条连接"
        )

    def _update_data_hint(self):
        addr = normalize_addr(self.addr_input.text())
        if addr is None:
            self.data_hint.setText("⚠️ 地址格式不正确，示例：localhost:13213/callback")
            return

        twoway = self.flow_combo.currentData() == TWOWAY
        parts = [f"POST JSON 到 http://{addr}"]
        parts.append(
            f"双向，等前端回复，超时 {self.timeout_input.text().strip()}s"
            if twoway else "单向，收下即回 200"
        )
        parts.append("单发排队" if self.dispatch_combo.currentData() == SERIAL else "并发")
        self.data_hint.setText("　·　".join(parts))
        # 完整语义放 tooltip，提示行只留关键信息
        self.data_hint.setToolTip(
            "双向：推给前端的消息带 expectReply，前端回 external_reply 后才答复设备；"
            "超时返回 504。单向：转发完立即回 200。"
        )

    # ---- HTTP 应答表 ---- #

    def _load_responses(self):
        """从 QSettings 读回自定义应答；坏了或缺项都落回默认值。"""
        raw = self.settings.value("http_responses")
        try:
            stored = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            print_log("⚠️ HTTP 响应配置读取失败，已回到默认值")
            stored = {}

        responses = {}
        for key, default in RESPONSE_DEFAULTS.items():
            entry = stored.get(key) if isinstance(stored, dict) else None
            if not isinstance(entry, dict):
                responses[key] = dict(default)
                continue
            try:
                code = int(entry.get("code", default["code"]))
            except (TypeError, ValueError):
                code = default["code"]
            responses[key] = {
                "code": code if 100 <= code <= 599 else default["code"],
                "message": str(entry.get("message") or default["message"]),
            }
        return responses

    def open_http_responses(self):
        dialog = HttpResponseDialog(self, self.http_responses)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.http_responses = dialog.values()
        self.settings.setValue(
            "http_responses", json.dumps(self.http_responses, ensure_ascii=False)
        )
        changed = [
            name for key, name, *_ in RESPONSE_LABELS
            if self.http_responses[key] != RESPONSE_DEFAULTS[key]
        ]
        print_log(
            f"📝 HTTP 响应已更新（{len(changed)} 项与默认不同："
            f"{'、'.join(changed) if changed else '全部为默认'}）"
        )
        if self.running:
            # 服务已经在跑，直接下发，不必重启
            configure_responses(self.http_responses)
            print_log("↻ 已即时生效")

    def _on_flow_changed(self):
        """单向时不需要等回复；超时只在双向或单发排队时才起作用。"""
        twoway = self.flow_combo.currentData() == TWOWAY
        serial = self.dispatch_combo.currentData() == SERIAL
        useful = twoway or serial
        self.timeout_input.setEnabled(useful and not self.running)
        self.timeout_label.setEnabled(useful)
        self._update_data_hint()
        self._update_profile_hint()

    def _validate_timeout(self):
        text = self.timeout_input.text().strip()
        try:
            timeout = float(text)
        except ValueError:
            QMessageBox.warning(self, "错误", "超时时间要填数字（秒）")
            return None
        if not MIN_REPLY_TIMEOUT <= timeout <= MAX_REPLY_TIMEOUT:
            QMessageBox.warning(
                self, "错误",
                f"超时时间要在 {MIN_REPLY_TIMEOUT:g}-{MAX_REPLY_TIMEOUT:g} 秒之间",
            )
            return None
        return timeout

    def _validate_addr(self):
        addr = normalize_addr(self.addr_input.text())
        if addr is None:
            QMessageBox.warning(
                self, "错误", "数据获取地址格式不正确，示例：localhost:13213/callback"
            )
        return addr

    def _apply_theme(self):
        self.setStyleSheet(apply_theme(QApplication.instance(), self.theme_mode))
        # 样式表会改变卡片的内容高度（groupbox 的 margin-top / padding），
        # 换完样式重算一次最小高度，否则首帧的最小高按未套样式的尺寸算，偏小。

    def set_theme(self, mode):
        self.theme_mode = mode
        self.settings.setValue("theme", mode)
        for button in self.theme_group.buttons():
            button.setChecked(button.property("mode") == mode)
        self._apply_theme()

    def _on_system_scheme_changed(self, _scheme):
        if self.theme_mode == SYSTEM:
            self._apply_theme()

    def _set_status(self, text, state):
        self.status_label.setText(f"状态：{text}")
        self.status_label.setProperty("state", state)
        # 动态属性变化后需要重新应用样式表
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _update_history_tab_title(self, total):
        self.tabs.setTabText(
            self.history_tab_index,
            f"接收历史 📜（{total}）" if total else "接收历史 📜",
        )

    def closeEvent(self, event):
        if self.running:
            self.stop_server()
        # 方案本身在保存时已经落盘；这里只记一下最后停在哪套，供下次排查用
        self.settings.setValue("current_profile", self.current_profile)
        event.accept()
