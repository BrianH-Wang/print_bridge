"""接收通道列表组件：表格 + 增删改。

和 PrinterListWidget 同构：自己持有 channels，改动后发 changed 信号，
主窗口据此判断当前方案有没有未保存的修改。
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from channels import ANY_PATH, MAX_CHANNELS, TWOWAY, describe_channel, make_channel
from logger import print_log
from ui.constants import CHANNEL_COLUMNS, CHANNEL_TABLE_HEIGHT
from ui.dialogs.channel import ChannelDialog


class ChannelListWidget(QWidget):
    """一条或多条接收链接：每条一个路径，各自决定单向还是双向。"""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.channels = []
        self.editable = True    # 服务运行中不让改

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        self.table = QTableWidget(0, len(CHANNEL_COLUMNS))
        self.table.setObjectName("history")
        self.table.setHorizontalHeaderLabels(CHANNEL_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.doubleClicked.connect(lambda _: self.edit())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        header = self.table.horizontalHeader()
        for column in range(len(CHANNEL_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)   # 路径最值得占地方
        self.table.setFixedHeight(CHANNEL_TABLE_HEIGHT)
        grid.addWidget(self.table, 0, 0, 1, 2)

        side = QVBoxLayout()
        side.setSpacing(6)
        self.buttons = {}
        for text, tip, slot, style in (
            ("添加", "加一条接收链接", self.add, ""),
            ("编辑", "改选中这条的配置（双击表格行也行）", self.edit, "secondary"),
            ("删除", "移除选中的通道", self.remove, "secondary"),
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
        hint_row.setFixedHeight(20)
        hint_layout = QHBoxLayout(hint_row)
        hint_layout.setContentsMargins(0, 0, 0, 0)
        hint_layout.setSpacing(0)
        hint_layout.addWidget(self.hint)
        grid.addWidget(hint_row, 1, 0, 1, 3)

        self.render()

    def set_channels(self, channels):
        self.channels = [dict(c) for c in channels]
        self.render()

    def set_editable(self, editable):
        self.editable = editable
        self._update_buttons()

    def render(self):
        table = self.table
        table.setRowCount(len(self.channels))
        for row, channel in enumerate(self.channels):
            twoway = channel["flow"] == TWOWAY
            cells = [
                channel["key"],
                channel.get("label", ""),
                "* （兜底）" if channel["path"] == ANY_PATH else channel["path"],
                "双向" if twoway else "单向",
                f"{channel['timeout']:g}s" if twoway else "-",
                "自定义" if channel.get("envelope") else "默认",
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 2 and channel["path"] == ANY_PATH:
                    item.setToolTip("没被别的通道认领的路径都归它")
                table.setItem(row, column, item)
        table.resizeRowsToContents()
        if self.channels and self._selected_row() is None:
            table.selectRow(0)
        self._update_buttons()
        self._update_hint()
        self.changed.emit()

    def _update_hint(self):
        if not self.channels:
            self.hint.setText("⚠️ 还没有配置通道，点「添加」加一条")
            self.table.setToolTip("")
            return

        has_fallback = any(c["path"] == ANY_PATH for c in self.channels)
        paths = "、".join(c["path"] for c in self.channels if c["path"] != ANY_PATH)
        text = f"共 {len(self.channels)} 条"
        if paths:
            text += f"　·　{paths}"
        text += "　·　其余路径走兜底通道" if has_fallback else "　·　⚠️ 没有兜底通道，其余路径一律 404"
        self.hint.setText(text)
        self.table.setToolTip(
            "设备 POST 到对应路径；转发给前端的报文里带 channel 字段，\n"
            "前端可以据此区分数据是哪条链接来的。"
        )

    def _update_buttons(self):
        row = self.table.currentRow()
        has_row = 0 <= row < len(self.channels)
        self.buttons["添加"].setEnabled(self.editable)
        for text in ("编辑", "删除"):
            self.buttons[text].setEnabled(self.editable and has_row)

    def _selected_row(self):
        row = self.table.currentRow()
        return row if 0 <= row < len(self.channels) else None

    def _taken(self, skip=None):
        keys = [c["key"] for i, c in enumerate(self.channels) if i != skip]
        paths = [c["path"] for i, c in enumerate(self.channels) if i != skip]
        return keys, paths

    def add(self):
        if len(self.channels) >= MAX_CHANNELS:
            QMessageBox.warning(self, "错误", f"最多配置 {MAX_CHANNELS} 条通道")
            return
        keys, paths = self._taken()
        dialog = ChannelDialog(self, None, keys, paths)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.channels.append(dialog.values())
        self.render()
        self.table.selectRow(len(self.channels) - 1)
        print_log(f"➕ 已添加通道 {describe_channel(self.channels[-1])}")

    def edit(self):
        row = self._selected_row()
        if row is None or not self.editable:
            return
        keys, paths = self._taken(skip=row)
        dialog = ChannelDialog(self, self.channels[row], keys, paths)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.channels[row] = dialog.values()
        self.render()
        self.table.selectRow(row)
        print_log(f"✏️ 通道已更新 {describe_channel(self.channels[row])}")

    def remove(self):
        row = self._selected_row()
        if row is None:
            return
        channel = self.channels[row]
        answer = QMessageBox.question(
            self, "删除通道", f"确定删除通道「{channel['key']}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.channels.pop(row)
        self.render()
        print_log(f"🗑 已删除通道 {channel['key']}")

    def validate(self):
        """启动前检一遍；不合法时弹窗并返回 None。

        没有兜底通道是允许的（设备只往固定路径发时更严谨），只提醒一次。
        """
        if not self.channels:
            QMessageBox.warning(
                self, "错误", "还没有配置接收通道，请先在「获取数据配置」里添加一条"
            )
            return None
        return [dict(c) for c in self.channels]

    @staticmethod
    def default_channels():
        """新方案的初始通道：一条兜底，行为等同以前的单地址模式。"""
        return [make_channel(key="default", label="默认", path=ANY_PATH)]
