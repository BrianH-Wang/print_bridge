"""接收历史的高级筛选条件（时间段 / 推送数 / 图片有无）。"""

from PyQt6.QtCore import QDate, QDateTime
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QSpinBox, QWidget,
)


class AdvancedSearchDialog(QDialog):
    """历史搜索：时间段、推送数区间、图片有无。"""

    def __init__(self, parent, existing):
        super().__init__(parent)
        self.setWindowTitle("历史搜索")
        self.setMinimumWidth(420)

        form = QFormLayout(self)

        # 时间段（默认勾上"不限"，关闭日期输入）
        now = QDate.currentDate()
        self.start_check = QCheckBox("开始时间")
        self.start_check.setChecked(existing["start"] is not None)
        self.start_edit = QDateTimeEdit(
            existing["start"] or QDateTime(now.addDays(-1), QDateTime.currentDateTime().time())
        )
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setEnabled(self.start_check.isChecked())
        self.start_check.toggled.connect(self.start_edit.setEnabled)

        self.end_check = QCheckBox("结束时间")
        self.end_check.setChecked(existing["end"] is not None)
        self.end_edit = QDateTimeEdit(
            existing["end"] or QDateTime.currentDateTime()
        )
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setEnabled(self.end_check.isChecked())
        self.end_check.toggled.connect(self.end_edit.setEnabled)

        time_row = QHBoxLayout()
        time_row.addWidget(self.start_check)
        time_row.addWidget(self.start_edit)
        time_row.addWidget(self.end_check)
        time_row.addWidget(self.end_edit)
        time_widget = QWidget()
        time_widget.setLayout(time_row)
        form.addRow("时间段:", time_widget)

        # 推送数区间
        self.pushed_min = QSpinBox()
        self.pushed_min.setRange(0, 9999)
        self.pushed_min.setValue(existing["pushed_min"])
        self.pushed_max = QSpinBox()
        self.pushed_max.setRange(0, 9999)
        self.pushed_max.setValue(min(existing["pushed_max"], 9999))
        push_row = QHBoxLayout()
        push_row.addWidget(QLabel("最少:"))
        push_row.addWidget(self.pushed_min)
        push_row.addSpacing(8)
        push_row.addWidget(QLabel("最多:"))
        push_row.addWidget(self.pushed_max)
        push_widget = QWidget()
        push_widget.setLayout(push_row)
        form.addRow("已推送次数:", push_widget)

        # 图片有无
        self.image_combo = QComboBox()
        for label, value in [("不限", "any"), ("有图片", "yes"), ("无图片", "no")]:
            self.image_combo.addItem(label, value)
        idx = self.image_combo.findData(existing["has_image"])
        if idx >= 0:
            self.image_combo.setCurrentIndex(idx)
        form.addRow("图片:", self.image_combo)

        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.RestoreDefaults
            | QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).setText("清空条件")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self.reset
        )
        form.addRow(buttons)

    def reset(self):
        self.start_check.setChecked(False)
        self.end_check.setChecked(False)
        self.pushed_min.setValue(0)
        self.pushed_max.setValue(9999)
        self.image_combo.setCurrentIndex(0)

    def values(self):
        return {
            "start": self.start_edit.dateTime().toPyDateTime() if self.start_check.isChecked() else None,
            "end": self.end_edit.dateTime().toPyDateTime() if self.end_check.isChecked() else None,
            "pushed_min": self.pushed_min.value(),
            "pushed_max": self.pushed_max.value(),
            "has_image": self.image_combo.currentData(),
        }
