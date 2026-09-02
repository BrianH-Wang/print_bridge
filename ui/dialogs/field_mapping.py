"""「字段映射」页：设备的字段名 → 前端要的字段名。

单独一个模块而不是塞进 channel.py：这一页自带表格、样例、预览三块，
和通道那几个输入框不是一个量级，混在一起两边都难改。
"""

import json

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QTableWidget, QVBoxLayout, QWidget,
)

from mapping import (
    COMMON_TRANSFORMS, MERGE, ON_DEFAULT, ON_ERROR, ON_SKIP, PASSTHROUGH, PICK,
    flatten_keys, make_mapping, map_fields,
)

MODE_CHOICES = [
    ("原样透传（不改动）", PASSTHROUGH),
    ("只保留下面映射的字段", PICK),
    ("原样透传 + 追加映射的字段", MERGE),
]
MISSING_CHOICES = [
    ("回错误给设备", ON_ERROR),
    ("跳过该字段", ON_SKIP),
    ("用默认值", ON_DEFAULT),
]
COLUMNS = ["来源字段（设备发的）", "目标字段（给前端的）", "转换", "必填", "默认值"]
TABLE_HEIGHT = 190

PLACEHOLDER_SAMPLE = ('把设备实际发来的一条 JSON 粘在这里，\n'
                      '下面就能实时看到转换成什么样子。')


class FieldMappingPage(QWidget):
    """模式 + 字段表 + 样例 + 实时预览。"""

    changed = pyqtSignal()

    def __init__(self, parent=None, mapping=None, sample=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setSpacing(10)

        tip = QLabel(
            "设备的字段名和前端要的字段名对不上时，在这里翻译。"
            "不配置就是原样转发，和不填一样。"
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        box.addWidget(tip)

        mode_row = QWidget()
        mode_box = QHBoxLayout(mode_row)
        mode_box.setContentsMargins(0, 0, 0, 0)
        mode_box.setSpacing(8)
        mode_box.addWidget(QLabel("模式:"))
        self.mode_combo = QComboBox()
        for text, value in MODE_CHOICES:
            self.mode_combo.addItem(text, value)
        self.mode_combo.setCurrentIndex(
            max(self.mode_combo.findData((mapping or {}).get("mode", PASSTHROUGH)), 0)
        )
        self.mode_combo.currentIndexChanged.connect(self._on_changed)
        mode_box.addWidget(self.mode_combo)
        mode_box.addSpacing(12)
        mode_box.addWidget(QLabel("必填字段缺失时:"))
        self.missing_combo = QComboBox()
        for text, value in MISSING_CHOICES:
            self.missing_combo.addItem(text, value)
        self.missing_combo.setCurrentIndex(
            max(self.missing_combo.findData((mapping or {}).get("on_missing", ON_ERROR)), 0)
        )
        self.missing_combo.currentIndexChanged.connect(self._on_changed)
        mode_box.addWidget(self.missing_combo)
        mode_box.addStretch()
        box.addWidget(mode_row)

        table_row = QWidget()
        table_box = QHBoxLayout(table_row)
        table_box.setContentsMargins(0, 0, 0, 0)
        table_box.setSpacing(12)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setObjectName("history")
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setFixedHeight(TABLE_HEIGHT)
        table_box.addWidget(self.table, 1)

        side = QVBoxLayout()
        side.setSpacing(6)
        self.buttons = {}
        for text, tip_text, slot, style in (
            ("添加", "加一行映射", self.add_row, ""),
            ("删除", "删掉选中那行", self.remove_row, "secondary"),
            ("按样例填充", "把样例里的字段全列出来，改名就行", self.fill_from_sample, "secondary"),
        ):
            button = QPushButton(text)
            button.setObjectName(style)
            button.setToolTip(tip_text)
            button.clicked.connect(slot)
            side.addWidget(button)
            self.buttons[text] = button
        side.addStretch()
        side_box = QWidget()
        side_box.setLayout(side)
        table_box.addWidget(side_box)
        box.addWidget(table_row)

        box.addWidget(QLabel("样例（设备实际发来的一条数据）:"))
        self.sample_edit = QPlainTextEdit()
        self.sample_edit.setPlaceholderText(PLACEHOLDER_SAMPLE)
        self.sample_edit.setFixedHeight(96)
        if sample:
            self.sample_edit.setPlainText(json.dumps(sample, ensure_ascii=False, indent=2))
        self.sample_edit.textChanged.connect(self._on_changed)
        box.addWidget(self.sample_edit)

        box.addWidget(QLabel("预览（前端将收到的 data）:"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(96)
        box.addWidget(self.preview)

        self.problem = QLabel()
        self.problem.setObjectName("hint")
        self.problem.setWordWrap(True)
        box.addWidget(self.problem)

        for field in (mapping or {}).get("fields") or []:
            self._append_row(field)
        self._on_changed()

    # ------------------------------------------------------------------ #

    def _append_row(self, field=None):
        field = field or {}
        row = self.table.rowCount()
        self.table.insertRow(row)

        source = QLineEdit(field.get("source", ""))
        source.setPlaceholderText("code")
        source.textChanged.connect(self._on_changed)
        self.table.setCellWidget(row, 0, source)

        target = QLineEdit(field.get("target", ""))
        target.setPlaceholderText("expressNo")
        target.textChanged.connect(self._on_changed)
        self.table.setCellWidget(row, 1, target)

        transform = QComboBox()
        for text, value in COMMON_TRANSFORMS:
            transform.addItem(text, value)
        existing = (field.get("transforms") or [""])[0]
        # 手改 json 配了带参数的算子（prefix / lookup 之类）时下拉里没有，
        # 补一项显示出来，免得一打开对话框就把人家的配置改没了
        if existing and transform.findData(existing) < 0:
            transform.addItem(f"（自定义）{existing}", existing)
        transform.setCurrentIndex(max(transform.findData(existing), 0))
        transform.currentIndexChanged.connect(self._on_changed)
        self.table.setCellWidget(row, 2, transform)

        required = QCheckBox()
        required.setChecked(bool(field.get("required")))
        required.setToolTip("勾上后，设备没发这个字段就按上面的策略处理")
        required.stateChanged.connect(self._on_changed)
        holder = QWidget()
        holder_box = QHBoxLayout(holder)
        holder_box.setContentsMargins(0, 0, 0, 0)
        holder_box.addWidget(required, 0, Qt.AlignmentFlag.AlignCenter)
        self.table.setCellWidget(row, 3, holder)

        default = QLineEdit(str(field.get("default", "") or ""))
        default.setFixedWidth(90)
        default.textChanged.connect(self._on_changed)
        self.table.setCellWidget(row, 4, default)

        self.table.setRowHeight(row, 34)
        return row

    def add_row(self):
        self.table.selectRow(self._append_row())
        self._on_changed()

    def remove_row(self):
        row = self.table.currentRow()
        if 0 <= row < self.table.rowCount():
            self.table.removeRow(row)
            self._on_changed()

    def fill_from_sample(self):
        """把样例里的字段全列成映射行——不用自己琢磨路径怎么写。"""
        sample = self.sample()
        if not isinstance(sample, dict):
            self.problem.setText("❌ 先在下面贴一条合法的 JSON 样例")
            return
        existing = {self._cell_text(r, 0) for r in range(self.table.rowCount())}
        added = 0
        for path in flatten_keys(sample):
            if path in existing:
                continue
            self._append_row({"source": path, "target": path})
            added += 1
        self._on_changed()
        if not added:
            self.problem.setText("样例里的字段都已经在表里了")

    # ------------------------------------------------------------------ #

    def _cell_text(self, row, column):
        widget = self.table.cellWidget(row, column)
        return widget.text().strip() if widget else ""

    def sample(self):
        text = self.sample_edit.toPlainText().strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    def values(self):
        fields = []
        for row in range(self.table.rowCount()):
            source = self._cell_text(row, 0)
            target = self._cell_text(row, 1)
            if not source and not target:
                continue
            transform = self.table.cellWidget(row, 2).currentData()
            checkbox = self.table.cellWidget(row, 3).findChild(QCheckBox)
            fields.append({
                "source": source,
                "target": target or source,
                "transforms": [transform] if transform else [],
                "required": bool(checkbox and checkbox.isChecked()),
                "default": self._cell_text(row, 4),
            })
        return make_mapping(self.mode_combo.currentData(), fields,
                            self.missing_combo.currentData())

    def target_fields(self):
        """映射后会出现的字段名，供出向报文模板校验用。"""
        mapping = self.values()
        if mapping["mode"] == PASSTHROUGH:
            sample = self.sample()
            return list(sample) if isinstance(sample, dict) else []
        names = [f["target"] for f in mapping["fields"] if f["target"]]
        if mapping["mode"] == MERGE:
            sample = self.sample()
            if isinstance(sample, dict):
                names += [k for k in sample if k not in names]
        return names

    def _on_changed(self):
        mapping = self.values()
        active = mapping["mode"] != PASSTHROUGH
        for widget in (self.table, self.missing_combo,
                       self.buttons["添加"], self.buttons["删除"],
                       self.buttons["按样例填充"]):
            widget.setEnabled(active)

        sample = self.sample()
        if sample is None:
            self.preview.setPlainText("")
            self.problem.setText(
                "" if not self.sample_edit.toPlainText().strip()
                else "❌ 样例不是合法的 JSON"
            )
            self.changed.emit()
            return

        result, problems = map_fields(sample, mapping)
        self.preview.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        self.problem.setText("❌ " + "　·　".join(problems) if problems else "")
        self.changed.emit()

    def problems(self):
        sample = self.sample()
        if sample is None:
            return ["样例不是合法的 JSON"] if self.sample_edit.toPlainText().strip() else []
        return map_fields(sample, self.values())[1]
