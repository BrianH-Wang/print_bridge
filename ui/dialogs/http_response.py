"""维护 HTTP 应答表：哪种情况回什么状态码和说明。"""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from data_receiver import RESPONSE_DEFAULTS, RESPONSE_LABELS
from theme import SYSTEM, active_palette

# 表格给单元格控件的可用高度 = 行高 - 8（Qt 的固定内缩）。
# 控件最小高度：说明框 34px（内容 20 + padding 12 + 边框 2）、
# 状态码框带上下箭头要 39px。行高不够时边框会被行边界裁掉一截。
ROW_HEIGHT = 50          # 50 - 8 = 42 ≥ 39，留 3px 余量
CODE_COLUMN_WIDTH = 96   # 三位数 + 微调箭头
WHEN_COLUMN_WIDTH = 300
CELL_MARGIN = (10, 0, 10, 0)   # 只留左右；上下留白靠行高，再加 margin 就得把行撑到 63


class HttpResponseDialog(QDialog):
    """维护 HTTP 应答表：每个场景对应什么状态码、回给设备什么说明。"""

    COLUMNS = ["场景", "什么时候返回", "HTTP 码", "返回给设备的说明"]

    def __init__(self, parent, current):
        super().__init__(parent)
        self.setWindowTitle("HTTP 响应配置")
        self.setMinimumSize(980, 500)
        # 状态码按段上色，取当前生效的那套配色
        self.colors = active_palette(
            QApplication.instance(), getattr(parent, "theme_mode", SYSTEM)
        )

        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(12)

        tip = QLabel(
            "改完点「确定」即刻生效，服务运行中也不用重启。"
            "说明里 {error} / {timeout} / {limit} 会在返回时替换成实际值。"
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
        # 输入框自己有边框，再叠一层网格线就是双重描边；用隔行底色区分行即可
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        # 表格本身不吃焦点：否则打开就有个虚线框落在第一格上，像是选中了什么
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)

        header = self.table.horizontalHeader()
        # 表头跟着内容左对齐，别一个居中一个靠左
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(1, WHEN_COLUMN_WIDTH)
        header.resizeSection(2, CODE_COLUMN_WIDTH)

        self.code_boxes, self.message_edits = {}, {}
        for row, (key, name, when, placeholder) in enumerate(RESPONSE_LABELS):
            self.table.setItem(row, 0, self._text_item(name))
            self.table.setItem(row, 1, self._text_item(when, muted=True))

            spin = QSpinBox()
            spin.setRange(100, 599)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin.valueChanged.connect(
                lambda value, target=spin: self._paint_code(target, value)
            )
            self.table.setCellWidget(row, 2, self._cell(spin))
            self.code_boxes[key] = spin

            edit = QLineEdit()
            # 占位符说明从表格列挪到这里：那一列只讲"什么时候返回"，不混编辑提示
            hints = ["留空即用默认值"]
            if placeholder:
                hints.append(f"可用 {placeholder}")
            edit.setPlaceholderText("　·　".join(hints))
            edit.setToolTip(f"默认：{RESPONSE_DEFAULTS[key]['message']}")
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
        # 恢复默认会覆盖用户改动，别做得比「确定」还抢眼
        reset_btn.setObjectName("secondary")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_btn.setText("取消")
        cancel_btn.setObjectName("secondary")
        reset_btn.clicked.connect(self.restore_defaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)

        self._fill(current)
        # 焦点落在第一条说明上：不然会停在某个状态码的微调框里，
        # 蓝色焦点框落在一个没人想改的格子上，很扎眼
        first = self.message_edits[RESPONSE_LABELS[0][0]]
        first.setFocus()
        # 显示后 Qt 会在 focus-in 时再选一次，deselect 得排到事件循环里才生效，
        # 否则一上来整行反白，像是要被覆盖掉
        QTimer.singleShot(0, lambda: (first.deselect(),
                                      first.setCursorPosition(len(first.text()))))

    # ------------------------------------------------------------------ #
    # 单元格外观
    # ------------------------------------------------------------------ #

    def _text_item(self, text, muted=False):
        """只读文本格：左对齐、垂直居中，超长时挂 tooltip。"""
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        item.setToolTip(text)
        if muted:
            item.setForeground(QColor(self.colors["muted"]))
        return item

    @staticmethod
    def _cell(widget):
        """给单元格里的控件套一层留白，别让输入框贴着行边缘。"""
        box = QWidget()
        box.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)  # 露出隔行底色
        layout = QHBoxLayout(box)
        layout.setContentsMargins(*CELL_MARGIN)
        layout.setSpacing(0)
        layout.addWidget(widget)
        return box

    def _paint_code(self, spin, value):
        """2xx 绿、4xx 橙、5xx 红——一眼扫出哪几档是失败。"""
        token = "run_fg" if value < 300 else ("warn_fg" if value < 500 else "err_fg")
        spin.setStyleSheet(f"color: {self.colors[token]}; font-weight: 600;")

    # ------------------------------------------------------------------ #
    # 取值 / 回填
    # ------------------------------------------------------------------ #

    def _fill(self, mapping):
        for key, *_ in RESPONSE_LABELS:
            entry = (mapping or {}).get(key) or RESPONSE_DEFAULTS[key]
            code = int(entry.get("code", RESPONSE_DEFAULTS[key]["code"]))
            self.code_boxes[key].setValue(code)
            self._paint_code(self.code_boxes[key], code)   # 值没变时不发信号，这里补一次
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
