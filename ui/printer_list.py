"""打印机列表组件：表格 + 增删改 + 设为默认。

自己持有 printers（第一台就是默认打印机），改动后发 changed 信号，
主窗口据此判断当前方案有没有未保存的修改。
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from logger import print_log
from printer_utils import (
    NETWORK_PRINTER, SYSTEM_PRINTER, describe_printer, get_available_printers,
    printer_destination,
)
from ui.constants import MAX_PRINTERS, PRINTER_COLUMNS, PRINTER_TABLE_HEIGHT
from ui.dialogs import PrinterDialog


class PrinterListWidget(QWidget):
    """一台或多台打印机：每台一个英文 key，前端在 print 消息里点名打给谁。"""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.printers = []
        self.editable = True    # 服务运行中不让改

        grid = QGridLayout(self)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(3)

        self.table = QTableWidget(0, len(PRINTER_COLUMNS))
        self.table.setObjectName("history")
        self.table.setHorizontalHeaderLabels(PRINTER_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.doubleClicked.connect(lambda _: self.edit())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        # 定死约 3 行高：多了挤掉下面的卡片，少了看不全一单三张的典型配置
        self.table.setFixedHeight(PRINTER_TABLE_HEIGHT)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(self.table, 0, 0, 1, 2)

        side = QVBoxLayout()
        side.setSpacing(6)
        self.buttons = {}
        for text, tip, slot, style in (
            ("添加", "加一台打印机", self.add, ""),
            ("编辑", "改选中这台的配置（双击表格行也行）", self.edit, "secondary"),
            ("删除", "移除选中的打印机", self.remove, "secondary"),
            ("设为默认", "移到第一行；前端不指定 printer 时用它", self.make_default, "secondary"),
        ):
            button = QPushButton(text)
            button.setObjectName(style)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            side.addWidget(button)
            self.buttons[text] = button
        side.addStretch()
        side_box = QWidget()
        side_box.setLayout(side)
        grid.addWidget(side_box, 0, 2)
        grid.setColumnStretch(1, 1)

        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self.hint.setMinimumHeight(20)
        hint_row = QWidget()
        hint_row.setFixedHeight(26)
        hint_layout = QHBoxLayout(hint_row)
        hint_layout.setContentsMargins(0, 6, 0, 0)
        hint_layout.setSpacing(0)
        hint_layout.addWidget(self.hint)
        grid.addWidget(hint_row, 1, 0, 1, 3)

        # 底部留 8px 呼吸，跟 connection/data 卡结构对齐，避免和数据卡标题贴边
        spacer_row = QWidget()
        spacer_row.setMinimumHeight(16)
        spacer_layout = QHBoxLayout(spacer_row)
        spacer_layout.setContentsMargins(0, 8, 0, 0)
        spacer_layout.setSpacing(0)
        grid.addWidget(spacer_row, 2, 0, 1, 3)

        self.render()

    def set_printers(self, printers):
        self.printers = [dict(p) for p in printers]
        self.render()

    def set_editable(self, editable):
        self.editable = editable
        self._update_buttons()

    def render(self):
        """把 self.printers 画进表格；第一行是默认打印机，标 ★。"""
        table = self.table
        table.setRowCount(len(self.printers))
        for row, printer in enumerate(self.printers):
            kind = "网络直连" if printer["kind"] == NETWORK_PRINTER else "系统队列"
            cells = [
                printer["key"], printer.get("label", ""), kind,
                printer_destination(printer), "★" if row == 0 else "",
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 4:
                    item.setToolTip("默认打印机：前端不指定 printer 时用它")
                table.setItem(row, column, item)
        table.resizeRowsToContents()
        self._update_buttons()
        self._update_hint()
        self.changed.emit()

    def _update_hint(self):
        if not self.printers:
            self.hint.setText("⚠️ 还没有配置打印机，点「添加」加一台")
            return
        default = self.printers[0]
        names = "、".join(p["key"] for p in self.printers)
        self.hint.setText(
            f'前端发 {{"type":"print","printer":"{default["key"]}",...}} 指定打给谁'
            f"　·　共 {len(self.printers)} 台（{names}），不指定时打到 ★ {default['key']}"
        )

    def _update_buttons(self):
        row = self.table.currentRow()
        editable = self.editable
        has_row = 0 <= row < len(self.printers)
        self.buttons["添加"].setEnabled(editable)
        for text in ("编辑", "删除"):
            self.buttons[text].setEnabled(editable and has_row)
        # 已经是第一行就没必要再"设为默认"
        self.buttons["设为默认"].setEnabled(editable and has_row and row > 0)

    def _selected_row(self):
        row = self.table.currentRow()
        return row if 0 <= row < len(self.printers) else None

    def add(self):
        if len(self.printers) >= MAX_PRINTERS:
            QMessageBox.warning(self, "错误", f"最多配置 {MAX_PRINTERS} 台打印机")
            return
        dialog = PrinterDialog(self, None, [p["key"] for p in self.printers])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.printers.append(dialog.values())
        self.render()
        self.table.selectRow(len(self.printers) - 1)
        print_log(f"➕ 已添加打印机 {describe_printer(self.printers[-1])}")

    def edit(self):
        row = self._selected_row()
        if row is None or not self.editable:
            return
        dialog = PrinterDialog(self, self.printers[row], [p["key"] for p in self.printers])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.printers[row] = dialog.values()
        self.render()
        self.table.selectRow(row)
        print_log(f"✏️ 打印机已更新 {describe_printer(self.printers[row])}")

    def remove(self):
        row = self._selected_row()
        if row is None:
            return
        printer = self.printers[row]
        answer = QMessageBox.question(
            self, "删除打印机", f"确定删除打印机「{printer['key']}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.printers.pop(row)
        self.render()
        print_log(f"🗑 已删除打印机 {printer['key']}")

    def make_default(self):
        """默认打印机就是第一行，所以"设为默认"= 挪到最前面。"""
        row = self._selected_row()
        if row is None or row == 0:
            return
        self.printers.insert(0, self.printers.pop(row))
        self.render()
        self.table.selectRow(0)
        print_log(f"★ 默认打印机改为 {self.printers[0]['key']}")

    def validate(self):
        """启动前把打印机列表检一遍；不合法时弹窗并返回 None。"""
        if not self.printers:
            QMessageBox.warning(self, "错误", "还没有配置打印机，请先在「自动打印配置」里添加一台")
            return None

        available, _ = get_available_printers()
        for printer in self.printers:
            if printer["kind"] == NETWORK_PRINTER:
                continue
            if not printer["queue"]:
                QMessageBox.warning(self, "错误", f"打印机「{printer['key']}」没有选系统队列")
                return None
            if printer["queue"] not in available:
                QMessageBox.warning(
                    self, "错误",
                    f"打印机「{printer['key']}」用的队列 {printer['queue']} 在本机不存在，"
                    "请编辑它重新选一个",
                )
                return None
        return list(self.printers)
