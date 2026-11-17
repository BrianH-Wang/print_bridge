"""双击日志行弹出的详情：完整内容，以及出错时的源数据（标出解析失败的位置）。"""

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication, QTextCursor
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from logger import REQUEST, elapsed_text

LEVEL_LABELS = {"error": "错误", "warn": "警告", "info": "信息"}


class LogDetailDialog(QDialog):
    """open_request：传了就多一个「看这个请求的详细日志」按钮（简约日志里打开时用）。"""

    def __init__(self, entry, parent=None, open_request=None):
        super().__init__(parent)
        self.setWindowTitle("日志详情")
        self.setMinimumSize(680, 300 if entry["raw"] is None else 600)
        self.entry = entry

        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(6)
        rows = [
            ("时间", entry["time"]),
            ("级别", LEVEL_LABELS.get(entry["level"], entry["level"])),
            ("功能", entry["area"]),
            ("节点", entry["node"] or "—"),
            ("事件", entry["event"] or "—"),
        ]
        if entry.get("kind") == REQUEST:
            rows += [
                ("单号", entry["waybill"] or "—"),
                ("结果", entry["result"]),
                ("总耗时", f"{elapsed_text(entry)}　收到请求 → 应答写回设备"),
            ]
        if entry.get("saved"):
            rows.append(("已存到", entry["saved"]))
        for label, value in rows:
            value_label = QLabel(value)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value_label.setWordWrap(True)   # 落盘路径可能很长，折行，别把窗口撑宽
            form.addRow(f"{label}:", value_label)
        box.addLayout(form)

        box.addWidget(QLabel("内容:"))
        text = QPlainTextEdit(entry["note"] if entry.get("kind") == REQUEST else entry["text"])
        text.setObjectName("log")
        text.setReadOnly(True)
        text.setMaximumHeight(110 if entry["raw"] is not None else 16777215)
        box.addWidget(text, 0 if entry["raw"] is not None else 1)

        if entry.get("request"):
            box.addWidget(QLabel("请求（请求行、来源、请求头）:"))
            request = QPlainTextEdit(entry["request"])
            request.setObjectName("log")
            request.setReadOnly(True)
            request.setMaximumHeight(130)
            box.addWidget(request)

        self.raw_view = None
        if entry["raw"] is not None:
            where = entry.get("error_at") if entry["raw"] else None
            caption = f"源数据（{len(entry['raw'])} 字符）"
            if where:
                caption += f"　·　解析在第 {where['line']} 行第 {where['col']} 列出错，已选中那个字符"
            box.addWidget(QLabel(caption + ":"))

            self.raw_view = QPlainTextEdit(entry["raw"] or "（空：对方没有发请求体，看上面的请求头）")
            self.raw_view.setObjectName("log")
            self.raw_view.setReadOnly(True)
            self.raw_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            box.addWidget(self.raw_view, 1)
            if where:
                self._select(where["pos"])

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        close.setText("关闭")
        close.setObjectName("secondary")
        buttons.rejected.connect(self.reject)
        if entry["raw"] is not None:
            copy_btn = QPushButton("复制源数据")
            copy_btn.clicked.connect(lambda: self._copy_raw(copy_btn))
            buttons.addButton(copy_btn, QDialogButtonBox.ButtonRole.ActionRole)
        if entry.get("saved"):
            folder_btn = QPushButton("打开所在文件夹")
            folder_btn.setObjectName("secondary")
            folder_btn.clicked.connect(lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(Path(entry["saved"]).parent))))
            buttons.addButton(folder_btn, QDialogButtonBox.ButtonRole.ActionRole)
        if open_request is not None:
            steps_btn = QPushButton("看这个请求的详细日志")
            steps_btn.setObjectName("secondary")
            steps_btn.clicked.connect(lambda: (self.accept(), open_request(entry)))
            buttons.addButton(steps_btn, QDialogButtonBox.ButtonRole.ActionRole)
        box.addWidget(buttons)

    def _copy_raw(self, button):
        """复制后按钮字变成「已复制 ✓」一会儿，告诉人确实复制上了。"""
        QGuiApplication.clipboard().setText(self.entry["raw"])
        button.setText("已复制 ✓")
        # 计时器挂在对话框上：没到点就关了窗口，计时器跟着没了，不会去碰已删掉的按钮
        timer = QTimer(self, singleShot=True, interval=1500)
        timer.timeout.connect(lambda: button.setText("复制源数据"))
        timer.start()

    def _select(self, pos):
        """选中出错的那个字符并滚过去；位置在末尾（比如报文被截断）就停在最后。"""
        # json 报的是 Python 字符下标，Qt 按 UTF-16 数，emoji 之类会占两格，得换算
        pos = len(self.entry["raw"][:pos].encode("utf-16-le")) // 2
        cursor = self.raw_view.textCursor()
        length = len(self.raw_view.toPlainText())
        cursor.setPosition(min(pos, length))
        if pos < length:
            cursor.movePosition(QTextCursor.MoveOperation.NextCharacter,
                                QTextCursor.MoveMode.KeepAnchor)
        self.raw_view.setTextCursor(cursor)
        self.raw_view.centerCursor()
