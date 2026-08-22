import queue
import threading

from datetime import datetime

from PyQt6.QtCore import QDate, QDateTime, QMargins, QSettings, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QGuiApplication, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
    QDateEdit, QDateTimeEdit, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from data_receiver import (
    broadcast, init_history, start_http_server, stop_http_server,
)
from logger import init_logger, print_log, shorten
from history_store import HistoryStore, default_dir
from printer_utils import get_available_printers
from theme import SYSTEM, THEME_CHOICES, apply_theme
from websocket_server import start_websocket_server

DATA_ADDR = "localhost:13213/callback"
LOG_POLL_MS = 300
MAX_LOG_LINES = 2000
MAX_LOG_DRAIN = 200  # 单次刷新最多取出的日志条数，避免刷屏卡住界面
MAX_HISTORY = 200    # 接收历史最多保留的条数
HISTORY_FIXED_COLUMNS = (
    ["时间"]
    + ["快递单号", "重量(kg)", "长(cm)", "宽(cm)", "高(cm)"]
    + ["图片", "推送"]
)
THUMB_SIZE = QSize(72, 40)
ROW_HEIGHT = 48
MIN_VISIBLE_ROWS = 7  # 表格初始至少能看见的行数

# 默认要展示的数据列；启动时就创建出来，即使本次没收到也保留"-"占位。
DEFAULT_DATA_COLUMNS = ["expressNo", "weight", "length", "width", "height"]

# 数据字段名 → 中文表头；查不到的按原 key 显示。
# 外部系统的字段名不能改，这里只在显示层翻译。
FIELD_LABELS = {
    "expressNo": "快递单号",
    "weight": "重量(kg)",
    "length": "长(cm)",
    "width": "宽(cm)",
    "height": "高(cm)",
}


def human_bytes(size):
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


class ImagePreviewDialog(QDialog):
    """双击图片列时弹出的大图预览。"""

    def __init__(self, path, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(360, 280)

        pixmap = QPixmap(str(path))
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if pixmap.isNull():
            label.setText("无法读取图片文件")
        else:
            screen = self.screen().availableGeometry()
            limit = pixmap.size().boundedTo(
                QSize(int(screen.width() * 0.8), int(screen.height() * 0.8))
            )
            label.setPixmap(pixmap.scaled(
                limit,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
            self.resize(label.pixmap().size().grownBy(QMargins(24, 24, 24, 24)))

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(label)

        box = QVBoxLayout(self)
        box.setContentsMargins(8, 8, 8, 8)
        box.addWidget(area)


class AdvancedSearchDialog(QDialog):
    """历史搜索：时间段、推送数区间、图片有无。"""

    def __init__(self, parent, existing):
        super().__init__(parent)
        self.setWindowTitle("历史搜索")
        self.setMinimumWidth(420)

        form = QFormLayout(self)

        # 时间段（默认勾上"不限"，关闭日期输入）
        now = QDate.currentDate()
        self.start_check = QCheckBox("开始时间")
        self.start_check.setChecked(existing["start"] is not None)
        self.start_edit = QDateTimeEdit(
            existing["start"] or QDateTime(now.addDays(-1), QDateTime.currentDateTime().time())
        )
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setEnabled(self.start_check.isChecked())
        self.start_check.toggled.connect(self.start_edit.setEnabled)

        self.end_check = QCheckBox("结束时间")
        self.end_check.setChecked(existing["end"] is not None)
        self.end_edit = QDateTimeEdit(
            existing["end"] or QDateTime.currentDateTime()
        )
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setEnabled(self.end_check.isChecked())
        self.end_check.toggled.connect(self.end_edit.setEnabled)

        time_row = QHBoxLayout()
        time_row.addWidget(self.start_check)
        time_row.addWidget(self.start_edit)
        time_row.addWidget(self.end_check)
        time_row.addWidget(self.end_edit)
        time_widget = QWidget()
        time_widget.setLayout(time_row)
        form.addRow("时间段:", time_widget)

        # 推送数区间
        self.pushed_min = QSpinBox()
        self.pushed_min.setRange(0, 9999)
        self.pushed_min.setValue(existing["pushed_min"])
        self.pushed_max = QSpinBox()
        self.pushed_max.setRange(0, 9999)
        self.pushed_max.setValue(min(existing["pushed_max"], 9999))
        push_row = QHBoxLayout()
        push_row.addWidget(QLabel("最少:"))
        push_row.addWidget(self.pushed_min)
        push_row.addSpacing(8)
        push_row.addWidget(QLabel("最多:"))
        push_row.addWidget(self.pushed_max)
        push_widget = QWidget()
        push_widget.setLayout(push_row)
        form.addRow("已推送次数:", push_widget)

        # 图片有无
        self.image_combo = QComboBox()
        for label, value in [("不限", "any"), ("有图片", "yes"), ("无图片", "no")]:
            self.image_combo.addItem(label, value)
        idx = self.image_combo.findData(existing["has_image"])
        if idx >= 0:
            self.image_combo.setCurrentIndex(idx)
        form.addRow("图片:", self.image_combo)

        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.RestoreDefaults
            | QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).setText("清空条件")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self.reset
        )
        form.addRow(buttons)

    def reset(self):
        self.start_check.setChecked(False)
        self.end_check.setChecked(False)
        self.pushed_min.setValue(0)
        self.pushed_max.setValue(9999)
        self.image_combo.setCurrentIndex(0)

    def values(self):
        return {
            "start": self.start_edit.dateTime().toPyDateTime() if self.start_check.isChecked() else None,
            "end": self.end_edit.dateTime().toPyDateTime() if self.end_check.isChecked() else None,
            "pushed_min": self.pushed_min.value(),
            "pushed_max": self.pushed_max.value(),
            "has_image": self.image_combo.currentData(),
        }


class HistorySearchDialog(QDialog):
    """历史搜索窗口：单号/时间段/推送数/图片条件 + 结果表格，支持选中重发。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent_window = parent  # WebSocketServerGUI
        self.setWindowTitle("历史搜索")
        self.resize(960, 600)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        # ---- 顶部表单：单号模糊 + 时间段 + 推送数 + 图片 ---- #
        form_row = QHBoxLayout()
        form_row.setSpacing(8)

        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText("按单号 / expressNo 模糊搜索")
        self.query_input.setClearButtonEnabled(True)
        self.query_input.returnPressed.connect(self._run_search)
        form_row.addWidget(self.query_input, 2)

        now = QDate.currentDate()
        self.start_check = QCheckBox("开始")
        self.start_edit = QDateTimeEdit(
            QDateTime(now.addDays(-1), QDateTime.currentDateTime().time())
        )
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setEnabled(False)
        self.start_check.toggled.connect(self.start_edit.setEnabled)
        form_row.addWidget(self.start_check)
        form_row.addWidget(self.start_edit)

        self.end_check = QCheckBox("结束")
        self.end_edit = QDateTimeEdit(QDateTime.currentDateTime())
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setEnabled(False)
        self.end_check.toggled.connect(self.end_edit.setEnabled)
        form_row.addWidget(self.end_check)
        form_row.addWidget(self.end_edit)

        self.pushed_min = QSpinBox()
        self.pushed_min.setRange(0, 9999)
        self.pushed_min.setValue(0)
        form_row.addWidget(QLabel("推送"))
        form_row.addWidget(self.pushed_min)
        self.pushed_max = QSpinBox()
        self.pushed_max.setRange(0, 9999)
        self.pushed_max.setValue(9999)
        form_row.addWidget(QLabel("–"))
        form_row.addWidget(self.pushed_max)

        self.image_combo = QComboBox()
        for label, value in [("不限图片", "any"), ("有图片", "yes"), ("无图片", "no")]:
            self.image_combo.addItem(label, value)
        form_row.addWidget(self.image_combo)

        outer.addLayout(form_row)

        # ---- 按钮：紧贴表单行右侧，单独一行，避免横向被挤换行 ---- #
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.search_btn = QPushButton("搜索")
        self.search_btn.clicked.connect(self._run_search)
        action_row.addWidget(self.search_btn)

        self.reset_btn = QPushButton("重置条件")
        self.reset_btn.setObjectName("secondary")
        self.reset_btn.clicked.connect(self._reset_form)
        action_row.addWidget(self.reset_btn)

        self.resend_btn = QPushButton("重发选中")
        self.resend_btn.setEnabled(False)
        self.resend_btn.clicked.connect(self._resend_selected)
        action_row.addWidget(self.resend_btn)

        outer.addLayout(action_row)

        # ---- 状态 ---- #
        self.summary_label = QLabel()
        self.summary_label.setObjectName("hint")
        outer.addWidget(self.summary_label)

        # ---- 结果表格 ---- #
        self.result_table = QTableWidget(0, len(HISTORY_FIXED_COLUMNS))
        self.result_table.setObjectName("history")
        self.result_table.setHorizontalHeaderLabels(HISTORY_FIXED_COLUMNS)
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.setAlternatingRowColors(True)
        self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.result_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.result_table.setIconSize(THUMB_SIZE)
        self.result_table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.result_table.itemSelectionChanged.connect(self._on_selection_changed)
        self.result_table.doubleClicked.connect(self._on_double_click)
        self.result_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.result_table.customContextMenuRequested.connect(self._on_context_menu)

        self.data_columns = list(DEFAULT_DATA_COLUMNS)
        for key in self.parent_window.data_columns:
            self._ensure_column(key)

        outer.addWidget(self.result_table, 1)

        # ---- 底部 ---- #
        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        outer.addLayout(bottom)

        self._results = []
        self._disk_loaded = False   # 首次搜索时才从磁盘读，避免窗口打开就 IO
        self._update_summary(0, 0)

    def _collect_records(self):
        """合并内存里的本次记录 + 磁盘上的历史，按 msg_id 去重。
        主表只展示本次运行后的数据，所以搜索窗口必须自己读磁盘，否则
        启动前累积的几百条历史就搜不到。
        """
        in_memory = list(self.parent_window.records)
        if not self._disk_loaded:
            try:
                # limit 与主表上限对齐；磁盘记录一般 200 条内
                disk_records = self.parent_window.store.load(MAX_HISTORY)
            except Exception as e:
                print_log(f"⚠️ 读取历史失败: {e}")
                disk_records = []
            self._disk_loaded = True
            self._disk_records = disk_records
        else:
            disk_records = getattr(self, "_disk_records", [])

        in_memory_ids = {r.get("msg_id") for r in in_memory if r.get("msg_id")}
        merged = list(in_memory)
        for r in disk_records:
            mid = r.get("msg_id")
            if mid and mid in in_memory_ids:
                continue
            merged.append(r)
        return merged

    # ---- 列 ---- #
    def _image_column(self):
        return 1 + len(self.data_columns)

    def _sent_column(self):
        return 2 + len(self.data_columns)

    def _ensure_column(self, key):
        if key in self.data_columns:
            return
        index = 1 + len(self.data_columns)
        self.result_table.insertColumn(index)
        self.result_table.setHorizontalHeaderItem(
            index, QTableWidgetItem(FIELD_LABELS.get(key, str(key)))
        )
        self.data_columns.append(key)
        header = self.result_table.horizontalHeader()
        for column in range(self.result_table.columnCount()):
            mode = QHeaderView.ResizeMode.Stretch if column == self._image_column() \
                else QHeaderView.ResizeMode.ResizeToContents
            header.setSectionResizeMode(column, mode)

    # ---- 过滤 ---- #
    def _conditions(self):
        return {
            "query": self.query_input.text().strip(),
            "start": self.start_edit.dateTime().toPyDateTime() if self.start_check.isChecked() else None,
            "end": self.end_edit.dateTime().toPyDateTime() if self.end_check.isChecked() else None,
            "pushed_min": self.pushed_min.value(),
            "pushed_max": self.pushed_max.value(),
            "has_image": self.image_combo.currentData(),
        }

    def _matches(self, record, cond):
        fields = record.get("fields") or {}
        if cond["query"]:
            haystack = " ".join([
                str(fields.get("expressNo", "")),
                str(fields.get("orderNo", "")),
                str(fields.get("trackingNo", "")),
                str(record.get("time", "")),
            ])
            if cond["query"].lower() not in haystack.lower():
                return False

        if cond["start"] or cond["end"]:
            try:
                t = datetime.strptime(record["time"], "%Y-%m-%d %H:%M:%S")
            except (KeyError, ValueError):
                return False
            if cond["start"] and t < cond["start"]:
                return False
            if cond["end"] and t > cond["end"]:
                return False

        pushed = record.get("forwarded", 0) or 0
        if pushed < cond["pushed_min"] or pushed > cond["pushed_max"]:
            return False

        has_image = bool(record.get("image") and record["image"].get("file"))
        if cond["has_image"] == "yes" and not has_image:
            return False
        if cond["has_image"] == "no" and has_image:
            return False

        return True

    def _run_search(self):
        cond = self._conditions()
        all_records = self._collect_records()
        results = [r for r in all_records if self._matches(r, cond)]
        self._render(results)
        self._update_summary(len(results), len(all_records))

    def _render(self, results):
        self._results = results
        self.result_table.setRowCount(0)
        for record in reversed(results):
            self._insert_row(record)

    def _insert_row(self, record):
        fields = record.get("fields") or {}
        for key in fields.keys():
            self._ensure_column(key)

        row = self.result_table.rowCount()
        self.result_table.insertRow(row)

        self.result_table.setItem(row, 0, QTableWidgetItem(record.get("time", "")))

        for index, key in enumerate(self.data_columns, start=1):
            value = fields.get(key)
            if value is None:
                text = "-"
                item = QTableWidgetItem(text)
                item.setToolTip("本次未收到该字段")
                item.setForeground(Qt.GlobalColor.lightGray)
            else:
                text = str(shorten(value))
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if key == "expressNo":
                    item.setData(Qt.ItemDataRole.UserRole + 1, str(value))
                    item.setToolTip(f"{text}\n右键复制单号")
            self.result_table.setItem(row, index, item)

        image = record.get("image")
        image_item = QTableWidgetItem()
        if image:
            image_item.setText(f" {image['type']} · {human_bytes(image['bytes'])}")
            if image.get("file"):
                thumb = self.parent_window._thumbnail(record)
                if thumb is not None:
                    image_item.setIcon(QIcon(thumb))
                image_item.setToolTip("双击查看大图")
            else:
                image_item.setToolTip("图片超过 5MB 未保存")
        self.result_table.setItem(row, self._image_column(), image_item)

        sent_text, _ = self.parent_window._format_sent(record)
        self.result_table.setItem(row, self._sent_column(), QTableWidgetItem(sent_text))

    def _update_summary(self, shown, total):
        if total == 0:
            self.summary_label.setText("暂无记录")
            return
        self.summary_label.setText(f"匹配 {shown} 条 / 共 {total} 条")

    def _reset_form(self):
        self.query_input.clear()
        self.start_check.setChecked(False)
        self.end_check.setChecked(False)
        self.pushed_min.setValue(0)
        self.pushed_max.setValue(9999)
        self.image_combo.setCurrentIndex(0)

    # ---- 选中 / 重发 / 图片 ---- #
    def _on_selection_changed(self):
        self.resend_btn.setEnabled(bool(self.result_table.selectedItems()))

    def _record_at(self, row):
        if 0 <= row < len(self._results):
            return self._results[row]
        return None

    def _resend_selected(self):
        record = self._record_at(self.result_table.currentRow())
        if record is None:
            return
        self.parent_window._resend_record(record)

    def _on_double_click(self, index):
        if index.column() != self._image_column():
            return
        record = self._record_at(index.row())
        if record is None:
            return
        path = self.parent_window.store.image_path(record)
        if path is None or not path.exists():
            QMessageBox.information(self, "提示", "图片文件不存在或未保存")
            return
        image = record["image"]
        title = f"{image['type']} · {human_bytes(image['bytes'])} · {record.get('time','')}"
        ImagePreviewDialog(path, title, self).exec()

    def _on_context_menu(self, pos):
        index = self.result_table.indexAt(pos)
        if not index.isValid():
            return
        menu = QMenu(self.result_table)

        if index.column() == 1:  # 快递单号列
            item = self.result_table.item(index.row(), index.column())
            raw = item.data(Qt.ItemDataRole.UserRole + 1) if item else None
            if raw:
                act = menu.addAction("复制快递单号")
                act.triggered.connect(lambda: self.parent_window._copy_text(raw))
                menu.addSeparator()

        resend_act = menu.addAction("重发选中")
        resend_act.triggered.connect(self._resend_selected)
        menu.exec(self.result_table.viewport().mapToGlobal(pos))


class WebSocketServerGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.log_queue = queue.Queue()
        init_logger(self.log_queue)

        self.history_queue = queue.Queue()
        init_history(self.history_queue)
        # ack 来自 websocket asyncio 线程，不能直接改 GUI。
        # 工作线程只入队，timerEvent() 在主线程里统一处理。
        self.ack_queue = queue.Queue()
        self.data_columns = list(DEFAULT_DATA_COLUMNS)  # 默认列：快递单号/重量/长/宽/高
        self.records = []        # 全部记录，新的在前
        self.filtered_records = []  # 当前表格里实际显示的记录（过滤后）
        self.filter_query = ""   # 当前过滤关键字（单号模糊）
        # 高级搜索已搬到独立的 HistorySearchDialog，不再影响主表过滤

        self.settings = QSettings("PrintBridge", "PrintBridge")
        # 默认跟随系统；有用户选择后记住
        self.theme_mode = self.settings.value("theme", SYSTEM)
        if self.theme_mode not in {mode for _, mode in THEME_CHOICES}:
            self.theme_mode = SYSTEM
        self.store = HistoryStore(self.settings.value("history_dir") or default_dir())

        self.ws_ready = threading.Event()
        self.http_ready = threading.Event()

        self.ws_thread = None
        self.http_thread = None
        self.ws_stop_event = threading.Event()
        self.running = False

        self.init_ui()
        self._apply_theme()
        # 不再加载 jsonl 里的旧历史——只显示本次运行之后到达的数据
        self._update_history_summary()

        # 跟随系统时，系统在浅色/深色之间切换后实时跟着变
        QApplication.instance().styleHints().colorSchemeChanged.connect(
            self._on_system_scheme_changed
        )

        self.startTimer(LOG_POLL_MS)
        print_log("🚀 Print Bridge 已就绪")

    # ------------------------------------------------------------------ #
    # 界面构建
    # ------------------------------------------------------------------ #

    def init_ui(self):
        self.setObjectName("root")
        self.setWindowTitle("Print Bridge (V1.0.0)")
        # 最小高度需要容纳 4 张配置卡 + 启动按钮 + 状态 + tabs 页头 + 表格 7 行。
        self.setMinimumSize(880, 880)
        self.resize(1080, 880)

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)

        layout.addLayout(self._build_header())

        # 单列纵向堆叠：状态卡和配置共享同一列，
        # 配置项不会被右侧固定宽度压扁。
        layout.addLayout(self._build_left_column(), 0)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)      # macOS 下默认会居中并压缩页签，这里改为左对齐
        self.tabs.tabBar().setExpanding(False)
        self.tabs.addTab(self._build_log_panel(), "日志输出 📋")
        self.history_tab_index = self.tabs.addTab(
            self._build_history_panel(), "接收历史 📜"
        )
        layout.addWidget(self.tabs, 1)

        self.setLayout(layout)
        self.toggle_modules()

    def _build_left_column(self):
        col = QVBoxLayout()
        col.setSpacing(8)
        function_card = self._build_function_card()
        connection_card = self._build_connection_card()
        print_card = self._build_print_card()
        data_card = self._build_data_card()
        # 连接/数据卡有两行（输入框+提示），最小高度不写死：
        # 这里取值时样式表还没应用（_apply_theme 在 init_ui 之后），算出来会偏小，
        # 卡片被压扁后 hint 会压到输入框上。交给控件自身的 minimumSizeHint 实时计算。
        col.addWidget(function_card)
        col.addWidget(connection_card)
        col.addWidget(print_card)
        col.addWidget(data_card)

        self.start_btn = QPushButton("启动服务器")
        self.start_btn.clicked.connect(self.toggle_server)
        col.addWidget(self.start_btn)

        self.status_label = QLabel("状态：就绪")
        self.status_label.setObjectName("status")
        col.addWidget(self.status_label)
        self._set_status("就绪，等待启动", "idle")
        return col

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

    def _build_function_card(self):
        card = QGroupBox("功能选择  🛠")
        row = QHBoxLayout()
        self.print_checkbox = QCheckBox("自动打印")
        self.data_checkbox = QCheckBox("获取数据")
        self.print_checkbox.setChecked(False)  # 自动打印默认不启用
        self.data_checkbox.setChecked(True)
        self.print_checkbox.stateChanged.connect(self.toggle_modules)
        self.data_checkbox.stateChanged.connect(self.toggle_modules)
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

        grid.addWidget(QLabel("WebSocket 端口 (8000-65535):"), 0, 0)
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
        self.print_group = QGroupBox("自动打印配置  🖨")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(3)

        self.printer_combo = QComboBox()
        self.printer_combo.setMinimumHeight(28)   # 与 LineEdit 对齐，避免被 group title 压扁
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setObjectName("secondary")
        self.refresh_btn.setToolTip("重新枚举系统打印机")
        self.refresh_btn.clicked.connect(lambda: self.refresh_printers())

        grid.addWidget(QLabel("选择打印机:"), 0, 0)
        grid.addWidget(self.printer_combo, 0, 1)
        grid.addWidget(self.refresh_btn, 0, 2)
        grid.setColumnStretch(1, 1)

        # 底部留 8px 呼吸，跟 connection/data 卡结构对齐，避免和数据卡标题贴边
        spacer_row = QWidget()
        spacer_row.setMinimumHeight(16)
        spacer_layout = QHBoxLayout(spacer_row)
        spacer_layout.setContentsMargins(0, 8, 0, 0)
        spacer_layout.setSpacing(0)
        grid.addWidget(spacer_row, 1, 0, 1, 3)

        self.print_group.setLayout(grid)
        self.refresh_printers(quiet=True)
        return self.print_group

    def _build_data_card(self):
        self.data_group = QGroupBox("获取数据配置  🌐")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(0)     # hint 间距由 hint_row 容器 margin 接管

        self.addr_input = QLineEdit(DATA_ADDR)
        self.addr_input.setDisabled(True)

        grid.addWidget(QLabel("数据获取地址:"), 0, 0)
        grid.addWidget(self.addr_input, 0, 1)
        grid.setColumnStretch(1, 1)

        hint = QLabel(f"外部系统 POST JSON 到 http://{DATA_ADDR}")
        hint.setObjectName("hint")
        hint.setMinimumHeight(20)   # 容纳一行 hint 文字，避免被 grid 拉伸/裁切
        # 用 QWidget 容器包 hint，给一个固定的 6px 顶间距，避免与输入框贴边
        hint_row = QWidget()
        hint_row.setFixedHeight(26)         # 6(顶间距) + 20(文字)，定死高度，不随外部空间伸缩
        hint_layout = QHBoxLayout(hint_row)
        hint_layout.setContentsMargins(0, 6, 0, 0)   # ← 顶间距定死 6px
        hint_layout.setSpacing(0)
        hint_layout.addWidget(hint)
        grid.addWidget(hint_row, 1, 0, 1, 2)

        # 底部留 8px 呼吸，跟启动按钮之间留出空间，三张卡结构对齐
        bottom_spacer = QWidget()
        bottom_spacer.setMinimumHeight(16)
        bottom_layout = QHBoxLayout(bottom_spacer)
        bottom_layout.setContentsMargins(0, 8, 0, 0)
        bottom_layout.setSpacing(0)
        grid.addWidget(bottom_spacer, 2, 0, 1, 2)

        self.data_group.setLayout(grid)
        return self.data_group

    def _build_history_panel(self):
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)

        # 顶部工具行：左侧存储目录，右上依次「修改目录」「打开目录」「重发选中」
        # 重发按当前选中的单条记录推送（仅支持单个）
        tools = QHBoxLayout()
        tools.setSpacing(8)

        self.store_label = QLabel()
        self.store_label.setObjectName("hint")
        self.store_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        tools.addWidget(self.store_label, 1)

        self.change_dir_btn = QPushButton("修改目录")
        self.change_dir_btn.setObjectName("secondary")
        self.change_dir_btn.setToolTip("选择新的历史存储目录")
        self.change_dir_btn.clicked.connect(self.change_store_dir)
        tools.addWidget(self.change_dir_btn)

        self.open_dir_btn = QPushButton("打开目录")
        self.open_dir_btn.setObjectName("secondary")
        self.open_dir_btn.setToolTip("在系统文件管理器中打开当前历史存储目录")
        self.open_dir_btn.clicked.connect(self.open_store_dir)
        tools.addWidget(self.open_dir_btn)

        self.resend_btn = QPushButton("重发选中")
        self.resend_btn.setToolTip("把选中的这条数据重新推送给所有已连接的前端（单条）")
        self.resend_btn.setEnabled(False)
        self.resend_btn.clicked.connect(self.resend_selected)
        tools.addWidget(self.resend_btn)
        box.addLayout(tools)
        self._update_store_label()

        # 搜索工具行：单号模糊搜索 + 时间段筛选（弹出"历史搜索"对话框）
        search = QHBoxLayout()
        search.addWidget(QLabel("搜索"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("按单号 / expressNo 模糊搜索")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.returnPressed.connect(self._apply_filter)  # 回车也触发
        search.addWidget(self.search_input, 1)

        self.search_btn = QPushButton("搜索")
        self.search_btn.clicked.connect(self._apply_filter)
        search.addWidget(self.search_btn)

        self.advanced_btn = QPushButton("历史搜索")
        self.advanced_btn.setObjectName("secondary")
        self.advanced_btn.setToolTip("按时间段、推送数、图片有无筛选")
        self.advanced_btn.clicked.connect(self.open_advanced_search)
        search.addWidget(self.advanced_btn)

        self.reset_filter_btn = QPushButton("重置")
        self.reset_filter_btn.setObjectName("secondary")
        self.reset_filter_btn.clicked.connect(self.reset_filter)
        search.addWidget(self.reset_filter_btn)
        box.addLayout(search)

        self.filter_summary = QLabel()
        self.filter_summary.setObjectName("hint")
        box.addWidget(self.filter_summary)

        self.history_table = QTableWidget(0, len(HISTORY_FIXED_COLUMNS))
        self.history_table.setObjectName("history")
        self.history_table.setHorizontalHeaderLabels(HISTORY_FIXED_COLUMNS)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.setWordWrap(False)
        self.history_table.itemSelectionChanged.connect(self._on_history_selection)
        self.history_table.doubleClicked.connect(self._on_history_double_click)
        self.history_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history_table.customContextMenuRequested.connect(self._on_history_context_menu)
        self.history_table.setIconSize(THUMB_SIZE)
        self.history_table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        # 表格默认至少能容纳 7 行数据，不至于空白一行就被裁短。
        header_h = self.history_table.horizontalHeader().height() or 28
        self.history_table.setMinimumHeight(MIN_VISIBLE_ROWS * ROW_HEIGHT + header_h + 2)

        # 固定列按内容自适应；「图片」列跟随窗口伸缩（窗口变宽时图片被放大）
        header = self.history_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # 时间
        # 中间数据列：默认按内容，但「图片」列拉伸，吃掉剩余宽度
        # 实时把列模式交给 _update_resize_modes 控制
        box.addWidget(self.history_table)

        return panel

    def _build_log_panel(self):
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch()

        self.autoscroll_checkbox = QCheckBox("自动滚动")
        self.autoscroll_checkbox.setChecked(True)
        row.addWidget(self.autoscroll_checkbox)

        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("secondary")
        clear_btn.clicked.connect(lambda: self.log_window.clear())
        row.addWidget(clear_btn)
        box.addLayout(row)

        self.log_window = QPlainTextEdit()
        self.log_window.setObjectName("log")
        self.log_window.setReadOnly(True)
        self.log_window.setMaximumBlockCount(MAX_LOG_LINES)
        box.addWidget(self.log_window)

        return panel

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #

    def toggle_modules(self):
        self.print_group.setVisible(self.print_checkbox.isChecked())
        self.data_group.setVisible(self.data_checkbox.isChecked())
        self._sync_min_height()

    def _sync_min_height(self):
        """最小高度按布局实际需要计算。

        写死的数字撑不住时，卡片会被压扁，hint 会被挤到和输入框重叠，
        而且压扁与否随自动打印卡的显隐变化。
        """
        self.layout().activate()
        self.setMinimumHeight(self.layout().minimumSize().height())
        if self.height() < self.minimumHeight():
            self.resize(self.width(), self.minimumHeight())

    def _update_ws_hint(self):
        port = self.port_input.text().strip() or "8765"
        self.ws_hint.setText(
            f"前端连接 ws://localhost:{port} —— 打印和数据推送共用这一条连接"
        )

    def refresh_printers(self, quiet=False):
        current = self.printer_combo.currentText()
        printers, default_printer = get_available_printers()
        self.printer_combo.clear()
        self.printer_combo.addItems(printers)

        target = current if current in printers else default_printer
        if target:
            self.printer_combo.setCurrentText(target)
        if not quiet:
            print_log(f"🖨 检测到 {len(printers)} 台打印机")

    # ---- 接收历史 ---- #

    def _ensure_columns(self, keys):
        """数据里出现新字段时，在「图片」列之前插入对应的列。"""
        for key in keys:
            if key in self.data_columns:
                continue
            index = 1 + len(self.data_columns)
            self.history_table.insertColumn(index)
            self.history_table.setHorizontalHeaderItem(
                index, QTableWidgetItem(FIELD_LABELS.get(key, str(key)))
            )
            self.data_columns.append(key)
        self._update_resize_modes()

    def _update_resize_modes(self):
        """除图片/推送外按内容自适应；图片列拉伸，吃掉窗口余宽。"""
        header = self.history_table.horizontalHeader()
        for column in range(self.history_table.columnCount()):
            if column == self._image_column():
                mode = QHeaderView.ResizeMode.Stretch
            else:
                mode = QHeaderView.ResizeMode.ResizeToContents
            header.setSectionResizeMode(column, mode)

    def _image_column(self):
        return 1 + len(self.data_columns)

    def _sent_column(self):
        return 2 + len(self.data_columns)

    def _thumbnail(self, record):
        """缩略图存成小文件，启动时直接读它，不必再解码 1MB 原图。"""
        thumb_path = self.store.thumb_path(record)
        if thumb_path is None:
            return None
        if thumb_path.exists():
            pixmap = QPixmap(str(thumb_path))
            if not pixmap.isNull():
                return pixmap

        source = self.store.image_path(record)
        if source is None or not source.exists():
            return None
        pixmap = QPixmap(str(source))
        if pixmap.isNull():
            return None
        thumb = pixmap.scaled(
            THUMB_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        thumb.save(str(thumb_path), "PNG")
        return thumb

    def _insert_row(self, record, row=0):
        fields = record.get("fields") or {}
        self._ensure_columns(fields.keys())
        self.history_table.insertRow(row)

        self.history_table.setItem(row, 0, QTableWidgetItem(record.get("time", "")))

        for index, key in enumerate(self.data_columns, start=1):
            value = fields.get(key)
            if value is None:
                # 默认列但该记录没收到这个字段，留空提示
                text = "-"
                item = QTableWidgetItem(text)
                item.setToolTip("本次未收到该字段")
                item.setForeground(Qt.GlobalColor.lightGray)
            else:
                text = str(shorten(value))
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                # 快递单号列：把原值存进 UserRole，右键菜单按这个复制
                if key == "expressNo":
                    item.setData(Qt.ItemDataRole.UserRole + 1, str(value))
                    item.setToolTip(f"{text}\n右键复制单号")
            self.history_table.setItem(row, index, item)

        image = record.get("image")
        image_item = QTableWidgetItem()
        if image:
            image_item.setText(f" {image['type']} · {human_bytes(image['bytes'])}")
            if image.get("file"):
                thumb = self._thumbnail(record)
                if thumb is not None:
                    image_item.setIcon(QIcon(thumb))
                image_item.setToolTip("双击查看大图")
            else:
                image_item.setToolTip("图片超过 5MB 未保存")
        self.history_table.setItem(row, self._image_column(), image_item)

        # 推送列：老格式记录（无 msg_id）显示纯数字；新格式显示「已确认/总数」
        sent_text, sent_state = self._format_sent(record)
        sent_item = QTableWidgetItem(sent_text)
        sent_item.setData(Qt.ItemDataRole.UserRole, record.get("msg_id"))
        sent_item.setToolTip(self._sent_tooltip(record))
        if sent_state == "done":
            sent_item.setForeground(Qt.GlobalColor.darkGreen)
        elif sent_state == "partial":
            sent_item.setForeground(Qt.GlobalColor.darkYellow)
        elif sent_state == "fail":
            sent_item.setForeground(Qt.GlobalColor.red)
        self.history_table.setItem(row, self._sent_column(), sent_item)

    def _add_history_row(self, entry):
        """收到新数据：图片落盘，元数据追加到 history.jsonl。"""
        try:
            record = self.store.build_record(
                entry["data"], entry["forwarded"], entry["time"], entry.get("msg_id"),
            )
        except OSError as e:
            print_log(f"❌ 写入历史失败: {e}")
            return

        # 推送列初始按已发送的客户端数显示，等待 ack 到达再更新
        if record.get("msg_id"):
            total = entry["forwarded"]
            record["ack_count"] = 0
            record["ack_total"] = total
            record["ack_done"] = False

        self.records.insert(0, record)
        if len(self.records) > MAX_HISTORY:
            dropped = self.records.pop()
            self.store.delete_files(dropped)
            self.store.rewrite(self.records)   # 已丢弃的记录同步从文件里移除

        if self._matches_filter(record):
            self._insert_row(record, 0)
            self.filtered_records.insert(0, record)

        self.store.append(record)  # jsonl 始终追加；过滤掉的也保留
        self._update_history_summary()

    def _load_history(self):
        """启动时把上次的历史读回来。"""
        try:
            self.records = self.store.load(MAX_HISTORY)
        except OSError as e:
            print_log(f"⚠️ 读取历史失败: {e}")
            self.records = []

        self._render_table()
        self._update_history_summary()
        if self.records:
            print_log(f"📂 已载入 {len(self.records)} 条历史记录: {self.store.directory}")

    def _render_table(self):
        """按当前过滤条件重新渲染整个表格。"""
        self.history_table.setRowCount(0)
        self.history_table.setColumnCount(len(HISTORY_FIXED_COLUMNS))
        self.history_table.setHorizontalHeaderLabels(HISTORY_FIXED_COLUMNS)
        # 重置列时保留默认列：快递单号/重量/长/宽/高
        self.data_columns = list(DEFAULT_DATA_COLUMNS)
        self.filtered_records = [
            record for record in self.records if self._matches_filter(record)
        ]
        for record in reversed(self.filtered_records):
            self._insert_row(record, 0)

    def _format_sent(self, record):
        """根据 ack 进度返回 (cell_text, state)。state 用于选颜色。"""
        msg_id = record.get("msg_id")
        total = record.get("ack_total", record.get("forwarded", 0))
        if not msg_id:
            return str(record.get("forwarded", 0)), "plain"

        acked = record.get("ack_count", 0)
        if record.get("ack_done"):
            return f"✅ {acked}/{total}", "done"
        if acked == 0:
            return f"⏳ 0/{total}", "partial"
        return f"⏳ {acked}/{total}", "partial"

    def _sent_tooltip(self, record):
        msg_id = record.get("msg_id")
        if not msg_id:
            return "未启用送达确认（旧记录）"
        total = record.get("ack_total", 0)
        acked = record.get("ack_count", 0)
        return f"消息 id: {msg_id}\n已确认 {acked}/{total} 个前端"

    def _on_ack(self, msg_id, acked, total, done):
        """主线程入口：在 timerEvent 里从队列取出后再调用本方法。"""
        record = None
        for r in self.records:
            if r.get("msg_id") == msg_id:
                record = r
                break
        if record is None:
            return
        record["ack_count"] = acked
        record["ack_total"] = total
        record["ack_done"] = done
        if done:
            record["ack_expired"] = False
        try:
            row = self.filtered_records.index(record)
        except ValueError:
            return
        item = self.history_table.item(row, self._sent_column())
        if item:
            text, state = self._format_sent(record)
            item.setText(text)
            item.setToolTip(self._sent_tooltip(record))

    def enqueue_ack(self, msg_id, acked, total, done):
        """工作线程调用：只入队，不操作 GUI。"""
        self.ack_queue.put((msg_id, acked, total, done))

    def _expire_pending_acks(self):
        """定时把超时未收齐的 ack 定格到当前进度。"""
        from data_receiver import expire_pending_acks
        for msg_id, acked, total in expire_pending_acks():
            for r in self.records:
                if r.get("msg_id") == msg_id and not r.get("ack_done"):
                    r["ack_count"] = acked
                    r["ack_total"] = total
                    r["ack_expired"] = True
                    try:
                        row = self.filtered_records.index(r)
                    except ValueError:
                        continue
                    item = self.history_table.item(row, self._sent_column())
                    if item:
                        text, _ = self._format_sent(r)
                        item.setText(text)
                        item.setForeground(Qt.GlobalColor.red)
                    break
            print_log(f"⏰ ack 超时 ({acked}/{total}) id={msg_id}")

    def _matches_filter(self, record):
        # 1) 单号模糊
        if self.filter_query:
            fields = record.get("fields") or {}
            haystack = " ".join([
                str(fields.get("expressNo", "")),
                str(fields.get("orderNo", "")),
                str(fields.get("trackingNo", "")),
                str(record.get("time", "")),
            ])
            if self.filter_query.lower() not in haystack.lower():
                return False

        # 高级搜索已搬到独立的 HistorySearchDialog，
        # 这里只保留单号模糊，主表不会被搜索条件污染。
        return True

    def _record_time(self, record):
        try:
            return datetime.strptime(record["time"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, ValueError):
            return datetime.min

    def _apply_filter(self, _query=None):
        self.filter_query = self.search_input.text().strip()
        self._render_table()
        self._update_history_summary()

    def reset_filter(self):
        self.search_input.clear()
        self._apply_filter()

    def open_advanced_search(self):
        # 弹出独立的搜索窗口；不在主表上做全局过滤，
        # 避免打开窗口后主表被搜索条件污染。
        HistorySearchDialog(self).exec()

    def _update_history_summary(self):
        total = len(self.records)
        shown = len(self.filtered_records)
        self.tabs.setTabText(
            self.history_tab_index,
            f"接收历史 📜（{total}）" if total else "接收历史 📜",
        )
        if not total:
            self.filter_summary.setText("")
            return

        disk = self.store.disk_bytes(self.records)
        parts = [f"共 {total} 条 · 图片 {human_bytes(disk)} 存在磁盘（不占内存） · 上限 {MAX_HISTORY} 条"]
        if self.filter_query:
            parts.append(f"过滤后 {shown} 条")
        self.filter_summary.setText("  ·  ".join(parts))

    def _update_store_label(self):
        self.store_label.setText(f"存储目录：{self.store.directory}")
        self.store_label.setToolTip(str(self.store.directory))

    def change_store_dir(self):
        directory = QFileDialog.getExistingDirectory(
            self, "选择历史存储目录", str(self.store.directory)
        )
        if not directory:
            return
        self.store.set_directory(directory)
        self.settings.setValue("history_dir", directory)
        self._update_store_label()

        # 换目录后重新载入该目录下的历史
        self._load_history()
        print_log(f"📁 历史存储目录已切换: {directory}")

    def open_store_dir(self):
        self.store.ensure()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.directory)))

    def _on_history_selection(self):
        self.resend_btn.setEnabled(bool(self.history_table.selectedItems()))

    def _on_history_double_click(self, index):
        """双击图片列看大图；其余列不做动作，避免误触重发。"""
        if index.column() != self._image_column():
            return
        record = self._record_at(index.row())
        if record is None:
            return
        path = self.store.image_path(record)
        if path is None or not path.exists():
            QMessageBox.information(self, "提示", "图片文件不存在或未保存")
            return
        image = record["image"]

    def _on_history_context_menu(self, pos):
        """右键菜单：快递单号支持复制；其他列给「重发选中」入口。"""
        index = self.history_table.indexAt(pos)
        if not index.isValid():
            return
        menu = QMenu(self.history_table)

        # 快递单号列：单元格存了 raw 单号，复制走原值而非折叠摘要
        if index.column() == 1:  # 默认第 1 列就是 expressNo
            item = self.history_table.item(index.row(), index.column())
            raw = item.data(Qt.ItemDataRole.UserRole + 1) if item else None
            if raw:
                act = menu.addAction("复制快递单号")
                act.triggered.connect(lambda: self._copy_text(raw))
                menu.addSeparator()

        resend_act = menu.addAction("重发选中")
        resend_act.triggered.connect(self.resend_selected)
        menu.exec(self.history_table.viewport().mapToGlobal(pos))

    def _copy_text(self, text):
        QGuiApplication.clipboard().setText(text)
        print_log(f"📋 已复制到剪贴板: {text}")
        title = f"{image['type']} · {human_bytes(image['bytes'])} · {record.get('time','')}"
        ImagePreviewDialog(path, title, self).exec()

    def _record_at(self, row):
        """按当前显示的行号取记录；过滤后才是关键路径。"""
        if 0 <= row < len(self.filtered_records):
            return self.filtered_records[row]
        return None

    def resend_selected(self):
        record = self._record_at(self.history_table.currentRow())
        if record is None:
            return
        self._resend_record(record)

    def _resend_record(self, record):
        """按记录重发：搜索窗口和主窗口共用。"""
        payload = self.store.restore_payload(record)
        if payload is None:
            QMessageBox.warning(self, "错误", "图片文件已丢失，无法还原这条数据")
            return

        sent = broadcast({"type": "external_data", "data": payload})
        if sent:
            print_log(f"🔁 重发数据给 {sent} 个客户端: {shorten(payload)}")
            record["forwarded"] = sent
            self._refresh_sent_column(record)
        else:
            print_log("⚠️ 无 WebSocket 客户端连接，重发未送达")

    def _refresh_sent_column(self, record):
        """根据 record 在 filtered_records 里找到对应行并刷新「推送」单元格。"""
        try:
            row = self.filtered_records.index(record)
        except ValueError:
            return
        self.history_table.item(row, self._sent_column()).setText(str(record["forwarded"]))

    def clear_history(self):
        """保留为空方法以兼容旧设置 / 日志中曾出现的「清空历史」字样；
        界面已不提供入口，调用方删除此逻辑。
        """
        pass
        print_log("🗑 历史记录已清空")

    def _apply_theme(self):
        self.setStyleSheet(apply_theme(QApplication.instance(), self.theme_mode))
        # 样式表会改变卡片的内容高度（groupbox 的 margin-top / padding），
        # 换完样式重算一次最小高度，否则首帧的最小高按未套样式的尺寸算，偏小。
        self._sync_min_height()

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

    def _set_inputs_enabled(self, enabled):
        for widget in (self.port_input, self.printer_combo, self.refresh_btn):
            widget.setEnabled(enabled)

        # 复选框不用 setEnabled(False)：macOS 上禁用后的勾选标记几乎看不见，
        # 运行时就分辨不出启用了哪些功能。这里只屏蔽交互，保持正常外观。
        for box in (self.print_checkbox, self.data_checkbox):
            box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not enabled)
            box.setFocusPolicy(
                Qt.FocusPolicy.StrongFocus if enabled else Qt.FocusPolicy.NoFocus
            )

    def toggle_server(self):
        if self.running:
            self.stop_server()
        else:
            self.start_server()

    def _validate_port(self):
        text = self.port_input.text().strip()
        if not text.isdigit():
            QMessageBox.warning(self, "错误", "请输入有效的端口号")
            return None
        port = int(text)
        if not 8000 <= port <= 65535:
            QMessageBox.warning(self, "错误", "端口号必须在 8000-65535 之间")
            return None
        return port

    def start_server(self):
        enable_print = self.print_checkbox.isChecked()
        enable_data = self.data_checkbox.isChecked()

        if not (enable_print or enable_data):
            QMessageBox.warning(self, "错误", "请至少勾选一项功能")
            return

        # 先完成全部校验，避免任何一项不合法时留下已启动的服务
        port = self._validate_port()
        if port is None:
            return
        if enable_print and not self.printer_combo.currentText():
            QMessageBox.warning(self, "错误", "未检测到可用打印机，请检查后点击“刷新”")
            return

        # WebSocket 是前端的通信通道，只要启用了任一功能就要跑：
        # 「自动打印」靠它收 PDF，「获取数据」靠它把外部数据推给前端。
        # 未启用自动打印时传空打印机名，收到 PDF 会被明确拒绝而不是尝试打印。
        printer_name = self.printer_combo.currentText() if enable_print else ""
        self.ws_stop_event.clear()
        self.ws_ready.clear()
        self.http_ready.clear()

        self.ws_thread = threading.Thread(
            target=start_websocket_server,
            args=(port, printer_name, self.ws_stop_event, self.ws_ready, self.enqueue_ack),
            daemon=True,
        )
        self.ws_thread.start()

        if enable_data:
            self.http_thread = threading.Thread(
                target=start_http_server,
                args=(self.addr_input.text(), self.http_ready),
                daemon=True,
            )
            self.http_thread.start()

        # 端口被占用时线程里只会打日志，这里回查一次绑定结果，避免界面假装在运行
        QTimer.singleShot(1200, lambda: self._verify_started(enable_data))

        self.running = True
        self._set_inputs_enabled(False)
        self.start_btn.setText("停止服务器")
        self.start_btn.setObjectName("danger")
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)
        self._set_status("服务运行中", "running")

    def _verify_started(self, enable_data):
        """确认端口真的绑上了；没绑上就回滚，不要显示成运行中。"""
        if not self.running:
            return

        failed = []
        if not self.ws_ready.is_set():
            failed.append("WebSocket")
        if enable_data and not self.http_ready.is_set():
            failed.append("HTTP")
        if not failed:
            return

        print_log(f"❌ {' 和 '.join(failed)} 服务启动失败，已回滚（端口可能被占用）")
        self.stop_server()
        self._set_status(
            f"{' / '.join(failed)} 启动失败，端口可能被占用，详见日志", "error"
        )

    def stop_server(self):
        if self.http_thread:
            stop_http_server()
            self.http_thread.join(timeout=3)
            self.http_thread = None

        if self.ws_thread:
            self.ws_stop_event.set()
            self.ws_thread.join(timeout=3)
            self.ws_thread = None

        self.running = False
        self._set_inputs_enabled(True)
        self.start_btn.setText("启动服务器")
        self.start_btn.setObjectName("")
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)
        self._set_status("服务已停止", "stopped")

    # ------------------------------------------------------------------ #
    # 日志刷新与退出
    # ------------------------------------------------------------------ #

    def timerEvent(self, event):
        while True:
            try:
                self._add_history_row(self.history_queue.get_nowait())
            except queue.Empty:
                break

        while True:
            try:
                msg_id, acked, total, done = self.ack_queue.get_nowait()
            except queue.Empty:
                break
            self._on_ack(msg_id, acked, total, done)

        self._expire_pending_acks()

        lines = []
        for _ in range(MAX_LOG_DRAIN):
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if not lines:
            return

        scrollbar = self.log_window.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        previous = scrollbar.value()

        self.log_window.appendPlainText("\n".join(lines))

        if self.autoscroll_checkbox.isChecked() or at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(previous)

    def closeEvent(self, event):
        if self.running:
            self.stop_server()
        event.accept()
