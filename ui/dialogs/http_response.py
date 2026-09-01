"""维护 HTTP 应答表：哪种情况回什么状态码和说明。"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from data_receiver import RESPONSE_DEFAULTS, RESPONSE_LABELS

ROW_HEIGHT = 44          # 容得下输入框 + 上下留白，两行之间不贴住
CODE_COLUMN_WIDTH = 96   # 三位数 + 微调箭头
CELL_MARGIN = (8, 6, 8, 6)   # 单元格内控件的四周留白


class HttpResponseDialog(QDialog):
    """维护 HTTP 应答表：每个场景对应什么状态码、回给设备什么说明。"""

    COLUMNS = ["场景", "什么时候返回", "HTTP 码", "返回给设备的说明"]

    def __init__(self, parent, current):
        super().__init__(parent)
        self.setWindowTitle("HTTP 响应配置")
        self.setMinimumSize(980, 480)

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
        self.table.setWordWrap(False)
        # 表格本身不吃焦点：否则打开就有个虚线框落在第一格上，像是选中了什么
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # 行高定死：默认行高只比输入框高一点点，两行的输入框会贴在一起
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(2, CODE_COLUMN_WIDTH)
        # 说明列是主要编辑区，给它比「什么时候返回」更大的比例
        self.table.setColumnWidth(1, 260)

        self.code_boxes, self.message_edits = {}, {}
        for row, (key, name, when, placeholder) in enumerate(RESPONSE_LABELS):
            label = QTableWidgetItem(name)
            label.setFlags(Qt.ItemFlag.ItemIsEnabled)     # 场景名是固定的，不给改
            self.table.setItem(row, 0, label)

            hint_text = when + (f"　可用 {placeholder}" if placeholder else "")
            hint = QTableWidgetItem(hint_text)
            hint.setFlags(Qt.ItemFlag.ItemIsEnabled)
            hint.setToolTip(hint_text)          # 列窄时截断，悬停看全文
            self.table.setItem(row, 1, hint)

            spin = QSpinBox()
            spin.setRange(100, 599)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setCellWidget(row, 2, self._cell(spin))
            self.code_boxes[key] = spin

            edit = QLineEdit()
            edit.setPlaceholderText(f"留空则用默认：{RESPONSE_DEFAULTS[key]['message']}")
            self.table.setCellWidget(row, 3, self._cell(edit))
            self.message_edits[key] = edit

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

    @staticmethod
    def _cell(widget):
        """给单元格里的控件套一层留白，别让输入框贴着格子边线。"""
        box = QWidget()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(*CELL_MARGIN)
        layout.setSpacing(0)
        layout.addWidget(widget)
        return box

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
