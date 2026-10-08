"""挑一份内置模板：可以存成文件慢慢改，也可以直接套用。"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from config_io import TEMPLATES, build_template, dumps

SAVE = "save"      # 存成文件
APPLY = "apply"    # 直接套用成一套新方案


class TemplateDialog(QDialog):
    """左边选模板，下面看它长什么样，再决定存文件还是直接用。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("配置模板")
        self.setMinimumSize(680, 560)
        self.action = None

        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)

        tip = QLabel(
            "模板是一份配好的方案。「直接套用」会新建一套方案并切过去；"
            "「保存为文件」则存成 json，改完再用「导入配置」装回来。"
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        box.addWidget(tip)

        self.list = QListWidget()
        self.list.setFixedHeight(120)
        for name, description, _ in TEMPLATES:
            item = QListWidgetItem(f"{name}　—　{description}")
            item.setToolTip(description)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.list.currentRowChanged.connect(self._refresh)
        box.addWidget(self.list)

        box.addWidget(QLabel("内容预览:"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        box.addWidget(self.preview, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("取消")
        cancel.setObjectName("secondary")

        save_btn = QPushButton("保存为文件")
        save_btn.setObjectName("secondary")
        save_btn.clicked.connect(lambda: self._finish(SAVE))
        buttons.addButton(save_btn, QDialogButtonBox.ButtonRole.ActionRole)

        apply_btn = QPushButton("直接套用")
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(lambda: self._finish(APPLY))
        buttons.addButton(apply_btn, QDialogButtonBox.ButtonRole.AcceptRole)

        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)

        self._refresh()

    def _refresh(self):
        self.preview.setPlainText(dumps(self.payload()))
        # 光标回到开头：预览很长，滚在中间看不出这是什么
        self.preview.moveCursor(self.preview.textCursor().MoveOperation.Start)

    def _finish(self, action):
        self.action = action
        self.accept()

    def index(self):
        return max(self.list.currentRow(), 0)

    def name(self):
        return TEMPLATES[self.index()][0]

    def payload(self):
        return build_template(self.index())
