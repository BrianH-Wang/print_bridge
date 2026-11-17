"""看一段 Markdown 说明：更新日志、「这次更新了什么」、新版本提示都用它。"""

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QTextBrowser, QVBoxLayout

DOWNLOAD = "download"   # 新版本提示里点了「下载新版本」
SKIP = "skip"           # 点了「跳过这个版本」


class NotesDialog(QDialog):
    """上面一行提示，中间渲染 Markdown，下面按钮。

    update=True 时是新版本提示：多出「下载新版本」「跳过这个版本」，关闭按钮叫「稍后」。
    """

    def __init__(self, title, markdown, intro="", update=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 460)
        self.action = None

        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)

        if intro:
            tip = QLabel(intro)
            tip.setWordWrap(True)
            box.addWidget(tip)

        browser = QTextBrowser()
        browser.setObjectName("notes")
        browser.setOpenExternalLinks(True)
        browser.setMarkdown(markdown or "（暂无内容）")
        box.addWidget(browser, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        if update:
            close.setText("稍后")
            close.setObjectName("secondary")

            skip_btn = QPushButton("跳过这个版本")
            skip_btn.setObjectName("secondary")
            skip_btn.setToolTip("这一版不再自动提醒，有更新的版本时照常提醒")
            skip_btn.clicked.connect(lambda: self._finish(SKIP))
            buttons.addButton(skip_btn, QDialogButtonBox.ButtonRole.ActionRole)

            download_btn = QPushButton("下载新版本")
            download_btn.setDefault(True)
            download_btn.clicked.connect(lambda: self._finish(DOWNLOAD))
            buttons.addButton(download_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        else:
            close.setText("知道了")
            close.setDefault(True)

        box.addWidget(buttons)

    def _finish(self, action):
        self.action = action
        self.accept()
