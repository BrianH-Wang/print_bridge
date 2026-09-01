"""添加 / 编辑一台打印机。"""

import re

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QWidget,
)

from logger import print_log
from printer_utils import (
    DEFAULT_RAW_PORT, KEY_PATTERN, NETWORK_PRINTER, SYSTEM_PRINTER,
    get_available_printers, make_printer, probe_printer_socket,
)
from ui.constants import PRINT_TARGET_CHOICES


class PrinterDialog(QDialog):
    """添加 / 编辑一台打印机：英文 key 给前端用，中文备注给人看。"""

    def __init__(self, parent, printer=None, taken_keys=()):
        super().__init__(parent)
        self.setWindowTitle("编辑打印机" if printer else "添加打印机")
        self.setMinimumWidth(460)
        self.taken_keys = {k for k in taken_keys if k != (printer or {}).get("key")}

        form = QFormLayout(self)

        self.key_input = QLineEdit((printer or {}).get("key", ""))
        self.key_input.setPlaceholderText("receipt / kitchen / cup")
        self.key_input.setToolTip("前端在 print 消息里用这个名字指定打给谁；小写字母开头")
        form.addRow("Key（前端用）:", self.key_input)

        self.label_input = QLineEdit((printer or {}).get("label", ""))
        self.label_input.setPlaceholderText("小票 / 后厨 / 杯贴")
        form.addRow("备注（给人看）:", self.label_input)

        self.kind_combo = QComboBox()
        for text, kind in PRINT_TARGET_CHOICES:
            self.kind_combo.addItem(text, kind)
        self.kind_combo.setCurrentIndex(
            max(self.kind_combo.findData((printer or {}).get("kind", SYSTEM_PRINTER)), 0)
        )
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        form.addRow("方式:", self.kind_combo)

        queue_row = QWidget()
        queue_box = QHBoxLayout(queue_row)
        queue_box.setContentsMargins(0, 0, 0, 0)
        queue_box.setSpacing(6)
        self.queue_combo = QComboBox()
        self.refresh_queue_btn = QPushButton("刷新")
        self.refresh_queue_btn.setObjectName("secondary")
        self.refresh_queue_btn.clicked.connect(self._refresh_queues)
        queue_box.addWidget(self.queue_combo, 1)
        queue_box.addWidget(self.refresh_queue_btn)
        form.addRow("系统队列:", queue_row)
        self._refresh_queues((printer or {}).get("queue", ""))

        addr_row = QWidget()
        addr_box = QHBoxLayout(addr_row)
        addr_box.setContentsMargins(0, 0, 0, 0)
        addr_box.setSpacing(6)
        self.host_input = QLineEdit((printer or {}).get("host", ""))
        self.host_input.setPlaceholderText("192.168.1.51")
        self.port_input = QLineEdit(str((printer or {}).get("port", DEFAULT_RAW_PORT)))
        self.port_input.setFixedWidth(64)
        self.test_btn = QPushButton("测试连接")
        self.test_btn.setObjectName("secondary")
        self.test_btn.clicked.connect(self._test)
        addr_box.addWidget(self.host_input, 1)
        addr_box.addWidget(QLabel(":"))
        addr_box.addWidget(self.port_input)
        addr_box.addWidget(self.test_btn)
        form.addRow("打印机地址:", addr_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._on_kind_changed()

    def _refresh_queues(self, keep=None):
        target = keep if isinstance(keep, str) else self.queue_combo.currentText()
        queues, default_queue = get_available_printers()
        self.queue_combo.clear()
        self.queue_combo.addItems(queues)
        if target and target not in queues:
            self.queue_combo.addItem(target)   # 本机没有也别把方案里存的名字弄丢
        pick = target or default_queue
        if pick:
            self.queue_combo.setCurrentText(pick)

    def _on_kind_changed(self):
        network = self.kind_combo.currentData() == NETWORK_PRINTER
        self.queue_combo.setEnabled(not network)
        self.refresh_queue_btn.setEnabled(not network)
        for widget in (self.host_input, self.port_input, self.test_btn):
            widget.setEnabled(network)

    def _test(self):
        host = self.host_input.text().strip()
        port = self.port_input.text().strip()
        if not host or not port.isdigit():
            QMessageBox.warning(self, "错误", "先填好打印机 IP 和端口")
            return
        ok, message = probe_printer_socket(host, int(port))
        print_log(("✅ " if ok else "❌ ") + message)
        (QMessageBox.information if ok else QMessageBox.warning)(self, "测试连接", message)

    def accept(self):
        key = self.key_input.text().strip()
        if not re.fullmatch(KEY_PATTERN, key):
            QMessageBox.warning(
                self, "错误", "Key 用小写字母开头，只能含小写字母、数字、_ 和 -，最长 20 位"
            )
            return
        if key in self.taken_keys:
            QMessageBox.warning(self, "错误", f"已经有一台叫 {key} 的打印机了")
            return

        if self.kind_combo.currentData() == NETWORK_PRINTER:
            if not re.fullmatch(r"[A-Za-z0-9._-]+", self.host_input.text().strip()):
                QMessageBox.warning(self, "错误", "打印机 IP / 主机名不正确，例如 192.168.1.51")
                return
            port = self.port_input.text().strip()
            if not port.isdigit() or not 1 <= int(port) <= 65535:
                QMessageBox.warning(self, "错误", "打印机端口要在 1-65535 之间，一般是 9100")
                return
        elif not self.queue_combo.currentText():
            QMessageBox.warning(self, "错误", "没有可用的系统打印队列，请先在系统里装好再刷新")
            return

        super().accept()

    def values(self):
        # 只留与当前方式相关的那一半：别把禁用着的控件里的残值也存进去，
        # 否则以后改方式时会静默用上一个从没选过的目标。
        network = self.kind_combo.currentData() == NETWORK_PRINTER
        return make_printer(
            key=self.key_input.text().strip(),
            label=self.label_input.text().strip(),
            kind=self.kind_combo.currentData(),
            queue="" if network else self.queue_combo.currentText(),
            host=self.host_input.text().strip() if network else "",
            port=int(self.port_input.text().strip() or DEFAULT_RAW_PORT),
        )
