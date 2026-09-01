"""历史搜索窗口：按单号 / 时间段 / 推送数 / 图片筛选，并可重发。"""

from datetime import datetime

from PyQt6.QtCore import QDate, QDateTime, Qt
from PyQt6.QtGui import QAction, QGuiApplication, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateTimeEdit, QDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from data_receiver import broadcast
from logger import print_log, shorten
from ui.constants import (
    DEFAULT_DATA_COLUMNS, FIELD_LABELS, HISTORY_FIXED_COLUMNS, MAX_HISTORY,
    MIN_VISIBLE_ROWS, ROW_HEIGHT, THUMB_SIZE,
)
from ui.dialogs.image_preview import ImagePreviewDialog
from ui.helpers import human_bytes


class HistorySearchDialog(QDialog):
    """历史搜索窗口：单号/时间段/推送数/图片条件 + 结果表格，支持选中重发。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.panel = parent          # HistoryPanel，记录、图片和重发都问它要
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
        for key in self.panel.data_columns:
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
        in_memory = list(self.panel.records)
        if not self._disk_loaded:
            try:
                # limit 与主表上限对齐；磁盘记录一般 200 条内
                disk_records = self.panel.store.load(MAX_HISTORY)
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
                thumb = self.panel._thumbnail(record)
                if thumb is not None:
                    image_item.setIcon(QIcon(thumb))
                image_item.setToolTip("双击查看大图")
            else:
                image_item.setToolTip("图片超过 5MB 未保存")
        self.result_table.setItem(row, self._image_column(), image_item)

        sent_text, _ = self.panel._format_sent(record)
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
        self.panel._resend_record(record)

    def _on_double_click(self, index):
        if index.column() != self._image_column():
            return
        record = self._record_at(index.row())
        if record is None:
            return
        path = self.panel.store.image_path(record)
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
                act.triggered.connect(lambda: self.panel._copy_text(raw))
                menu.addSeparator()

        resend_act = menu.addAction("重发选中")
        resend_act.triggered.connect(self._resend_selected)
        menu.exec(self.result_table.viewport().mapToGlobal(pos))
