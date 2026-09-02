"""接收历史页签：表格、筛选、重发、图片缩略图与落盘。

这一块自成一体——记录、存储目录、ack 都归它管，
主窗口只把它放进 tab，并接 countChanged 改页签标题。
"""

import queue

from datetime import datetime

from PyQt6.QtCore import QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QDesktopServices, QGuiApplication, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMenu, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from data_receiver import broadcast, expire_pending_acks, init_history, pending_ack_snapshot
from history_store import HistoryStore, default_dir
from logger import print_log, shorten
from ui.constants import (
    DEFAULT_DATA_COLUMNS, FIELD_LABELS, HISTORY_FIXED_COLUMNS, HISTORY_POLL_MS,
    MAX_HISTORY, MIN_VISIBLE_ROWS, ROW_HEIGHT, THUMB_SIZE,
)
from ui.dialogs import AdvancedSearchDialog, HistorySearchDialog, ImagePreviewDialog
from ui.helpers import human_bytes


class HistoryPanel(QWidget):
    """接收历史页签。countChanged(条数) 供主窗口刷新页签标题。"""

    countChanged = pyqtSignal(int)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.store = HistoryStore(settings.value("history_dir") or default_dir())

        self.history_queue = queue.Queue()
        init_history(self.history_queue)
        # ack 来自 websocket 的 asyncio 线程，不能直接改 GUI：
        # 工作线程只入队，定时器在主线程里统一处理。
        self.ack_queue = queue.Queue()

        self.data_columns = list(DEFAULT_DATA_COLUMNS)  # 默认列：快递单号/重量/长/宽/高
        self.records = []           # 全部记录，新的在前
        self.filtered_records = []  # 当前表格里实际显示的记录（过滤后）
        self.filter_query = ""      # 当前过滤关键字（单号模糊）

        box = QVBoxLayout(self)
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

        self._update_history_summary()

        # 历史和 ack 都靠轮询落地，跟日志一个节奏
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.drain)
        self._timer.start(HISTORY_POLL_MS)

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
                entry.get("channel"),
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
        self.countChanged.emit(total)   # 页签标题由主窗口按这个数字改
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

    # ------------------------------------------------------------------ #
    # 队列消费：历史与 ack 都从工作线程来，统一在这里落到界面
    # ------------------------------------------------------------------ #

    def drain(self):
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
