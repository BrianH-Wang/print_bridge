"""模拟设备的「连发顺序」：先发 A，隔几秒发 B，再隔几秒发 C……

每一步发的是那条通道在模拟设备编辑框里的内容。一步一行：通道 + 隔多久发，
第一步立即发。同一条通道可以出现多次。
"""

from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QVBoxLayout,
)

DEFAULT_DELAY = 0.5
MAX_DELAY = 600


def describe_chain(steps, wait_answer=False):
    """「sorter_data → 0.5s → sorter_image」，一行看懂顺序。"""
    if not steps:
        return "（没有步骤）"
    after = "应答后 " if wait_answer else ""
    parts = [steps[0]["key"]]
    for step in steps[1:]:
        parts.append(f"{after}{step['delay']:g}s → {step['key']}")
    return " → ".join(parts)


class SendChainDialog(QDialog):
    """channels：当前方案的通道；steps：[{"key", "delay"}]，delay 是距上一步的秒数。"""

    def __init__(self, channels, steps, wait_answer=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("连发顺序")
        self.setMinimumSize(560, 420)
        self.channels = channels

        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)

        intro = QLabel("按顺序一条接一条发：第一步立即发，后面每一步隔「间隔」秒再发。"
                       "每一步发的是那条通道在模拟设备编辑框里的内容，同一条通道可以出现多次。")
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        box.addWidget(intro)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["通道", "间隔（距上一步）"])
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 160)
        box.addWidget(self.table, 1)

        buttons_row = QHBoxLayout()
        for text, slot in (("添加一步", self._add), ("删除", self._remove),
                           ("上移", lambda: self._move(-1)), ("下移", lambda: self._move(1))):
            button = QPushButton(text)
            button.setObjectName("secondary")
            button.clicked.connect(slot)
            buttons_row.addWidget(button)
        buttons_row.addStretch()
        box.addLayout(buttons_row)

        self.wait_check = QCheckBox("等上一条收到应答，再开始算间隔")
        self.wait_check.setToolTip(
            "不勾：按时间点发，上一条还在等前端回复也照发（设备用多个连接同时发）。\n"
            "勾上：上一条的应答回来了才开始计时（设备用同一个连接、一条发完等回应再发下一条）")
        self.wait_check.setChecked(wait_answer)
        self.wait_check.toggled.connect(self._update_summary)
        box.addWidget(self.wait_check)

        self.summary = QLabel()
        self.summary.setObjectName("hint")
        self.summary.setWordWrap(True)
        box.addWidget(self.summary)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("取消")
        cancel.setObjectName("secondary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)

        for step in steps:
            self._insert(self.table.rowCount(), step["key"], step["delay"])
        if not steps and channels:
            self._insert(0, channels[0]["key"], 0)
        self._refresh_first_row()

    # ------------------------------------------------------------------ #

    def _insert(self, row, key, delay):
        self.table.insertRow(row)
        combo = QComboBox()
        for channel in self.channels:
            combo.addItem(f"{channel['key']}  {channel['label']}  {channel['path']}".strip(),
                          channel["key"])
        combo.setCurrentIndex(max(combo.findData(key), 0))
        combo.currentIndexChanged.connect(self._update_summary)
        self.table.setCellWidget(row, 0, combo)

        spin = QDoubleSpinBox()
        spin.setRange(0, MAX_DELAY)
        spin.setDecimals(1)
        spin.setSingleStep(0.5)
        spin.setSuffix(" 秒")
        spin.setValue(delay)
        spin.valueChanged.connect(self._update_summary)
        self.table.setCellWidget(row, 1, spin)

    def _row_values(self, row):
        return (self.table.cellWidget(row, 0).currentData(),
                self.table.cellWidget(row, 1).value())

    def _refresh_first_row(self):
        """第一步总是立即发，间隔框锁住，免得以为它会等。"""
        for row in range(self.table.rowCount()):
            spin = self.table.cellWidget(row, 1)
            first = row == 0
            spin.setEnabled(not first)
            spin.setSpecialValueText("立即" if first else "")
            if first:
                spin.setValue(0)
        self._update_summary()

    def _add(self):
        # 默认接着加下一条通道：A 后面自然是 B
        row = self.table.rowCount()
        keys = [c["key"] for c in self.channels]
        if not keys:
            return
        last = self._row_values(row - 1)[0] if row else None
        key = keys[(keys.index(last) + 1) % len(keys)] if last in keys else keys[0]
        self._insert(row, key, DEFAULT_DELAY)
        self.table.selectRow(row)
        self._refresh_first_row()

    def _remove(self):
        row = self.table.currentRow()
        if row < 0 or self.table.rowCount() <= 1:
            return   # 至少留一步
        self.table.removeRow(row)
        self._refresh_first_row()

    def _move(self, offset):
        row = self.table.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self.table.rowCount():
            return
        values = [self._row_values(r) for r in range(self.table.rowCount())]
        values[row], values[target] = values[target], values[row]
        # 间隔跟着位置走：挪的是「发哪条」，第几步隔多久不变
        delays = [d for _, d in [self._row_values(r) for r in range(self.table.rowCount())]]
        for r, (key, _) in enumerate(values):
            self.table.cellWidget(r, 0).setCurrentIndex(
                self.table.cellWidget(r, 0).findData(key))
            self.table.cellWidget(r, 1).setValue(delays[r])
        self.table.selectRow(target)
        self._refresh_first_row()

    def _update_summary(self):
        self.summary.setText("顺序：" + describe_chain(self.steps(), self.wait_check.isChecked()))

    # ------------------------------------------------------------------ #

    def steps(self):
        out = []
        for row in range(self.table.rowCount()):
            key, delay = self._row_values(row)
            if key:
                out.append({"key": key, "delay": 0 if row == 0 else delay})
        return out

    def wait_answer(self):
        return self.wait_check.isChecked()
