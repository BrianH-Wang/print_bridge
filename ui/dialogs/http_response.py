"""维护 HTTP 应答表：哪种情况回什么状态码和说明。"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel, QLineEdit,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from data_receiver import RESPONSE_DEFAULTS, RESPONSE_LABELS


class HttpResponseDialog(QDialog):
    """维护 HTTP 应答表：每个场景对应什么状态码、回给设备什么说明。"""

    COLUMNS = ["场景", "什么时候返回", "HTTP 码", "返回给设备的说明"]

    def __init__(self, parent, current):
        super().__init__(parent)
        self.setWindowTitle("HTTP 响应配置")
        self.setMinimumSize(860, 420)

        box = QVBoxLayout(self)
        box.setSpacing(8)

        tip = QLabel(
            "码和说明都可以改；说明里 {error} / {timeout} / {limit} 这类占位符会在返回时替换成实际值，"
            "删掉也不影响。点「确定」即刻生效，服务运行中也不用重启。"
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        box.addWidget(tip)

        self.table = QTableWidget(len(RESPONSE_LABELS), len(self.COLUMNS))
        self.table.setObjectName("history")
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        self.code_boxes, self.message_edits = {}, {}
        for row, (key, name, when, placeholder) in enumerate(RESPONSE_LABELS):
            label = QTableWidgetItem(name)
            label.setFlags(Qt.ItemFlag.ItemIsEnabled)     # 场景名是固定的，不给改
            self.table.setItem(row, 0, label)

            hint = QTableWidgetItem(when + (f"　可用 {placeholder}" if placeholder else ""))
            hint.setFlags(Qt.ItemFlag.ItemIsEnabled)
            hint.setToolTip(when)
            self.table.setItem(row, 1, hint)

            spin = QSpinBox()
            spin.setRange(100, 599)
            self.table.setCellWidget(row, 2, spin)
            self.code_boxes[key] = spin

            edit = QLineEdit()
            self.table.setCellWidget(row, 3, edit)
            self.message_edits[key] = edit

        self.table.resizeRowsToContents()
        box.addWidget(self.table, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.RestoreDefaults
            | QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        reset_btn = buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        reset_btn.setText("全部恢复默认")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        reset_btn.clicked.connect(self.restore_defaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)

        self._fill(current)

    def _fill(self, mapping):
        for key, *_ in RESPONSE_LABELS:
            entry = (mapping or {}).get(key) or RESPONSE_DEFAULTS[key]
            self.code_boxes[key].setValue(int(entry.get("code", RESPONSE_DEFAULTS[key]["code"])))
            self.message_edits[key].setText(
                str(entry.get("message", RESPONSE_DEFAULTS[key]["message"]))
            )

    def restore_defaults(self):
        """一键把所有场景改回出厂设置。"""
        self._fill(RESPONSE_DEFAULTS)

    def values(self):
        return {
            key: {
                "code": self.code_boxes[key].value(),
                "message": self.message_edits[key].text().strip()
                or RESPONSE_DEFAULTS[key]["message"],   # 留空当作没改
            }
            for key, *_ in RESPONSE_LABELS
        }
