"""日志页签：把各线程塞进队列的日志批量刷到界面上。

自己持有队列和定时器，主窗口只管把它放进 tab。
"""

import queue

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from logger import init_logger
from ui.constants import LOG_POLL_MS, MAX_LOG_DRAIN, MAX_LOG_LINES


class LogPanel(QWidget):
    """日志输出页签。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.log_queue = queue.Queue()
        init_logger(self.log_queue)

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch()

        self.autoscroll_checkbox = QCheckBox("自动滚动")
        self.autoscroll_checkbox.setChecked(True)
        row.addWidget(self.autoscroll_checkbox)

        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("secondary")
        clear_btn.clicked.connect(lambda: self.log_window.clear())
        row.addWidget(clear_btn)
        box.addLayout(row)

        self.log_window = QPlainTextEdit()
        self.log_window.setObjectName("log")
        self.log_window.setReadOnly(True)
        self.log_window.setMaximumBlockCount(MAX_LOG_LINES)
        box.addWidget(self.log_window)

        # 界面每 LOG_POLL_MS 批量刷一次，避免刷屏时一条一条重绘卡住主线程
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.drain)
        self._timer.start(LOG_POLL_MS)

    def drain(self):
        """把队列里的日志取出来追加显示；单次最多取 MAX_LOG_DRAIN 条。"""
        lines = []
        for _ in range(MAX_LOG_DRAIN):
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if not lines:
            return

        scrollbar = self.log_window.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        previous = scrollbar.value()

        self.log_window.appendPlainText("\n".join(lines))

        if self.autoscroll_checkbox.isChecked() or at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(previous)
