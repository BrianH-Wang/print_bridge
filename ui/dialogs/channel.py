"""添加 / 编辑一条接收通道：基础页够用，高级页留给要适配写死的一方时。"""

import json
import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QTabWidget,
    QVBoxLayout, QWidget,
)

from channels import (
    ANY_PATH, ENVELOPE_PRESETS, ENVELOPE_VARIABLES, KEY_PATTERN, TWOWAY,
    build_body, make_channel, normalize_path, validate_envelope,
)
from ui.constants import (
    DISPATCH_CHOICES, FLOW_CHOICES, HTTP_STATUS_CHOICES, MAX_REPLY_TIMEOUT,
    MIN_REPLY_TIMEOUT,
)

# 预览用的假场景：双向拿到前端回复那一档，最能看出 data 放在哪
PREVIEW_SCENE = ("ok_reply", 200, "操作成功", {"forwarded": 1, "data": {"route": "A"}})


class ChannelDialog(QDialog):
    """一条链接的完整配置。

    基础页只有路径和方向——不懂 HTTP 的人填这几格就能用；
    高级页管报文形状，改了才需要懂 JSON。两页的默认值都等于当前行为。
    """

    def __init__(self, parent, channel=None, taken_keys=(), taken_paths=()):
        super().__init__(parent)
        self.setWindowTitle("编辑通道" if channel else "添加通道")
        self.setMinimumWidth(600)
        current = channel or {}
        self.taken_keys = {k for k in taken_keys if k != current.get("key")}
        self.taken_paths = {p for p in taken_paths if p != current.get("path")}
        # 按通道覆盖应答码这一项还没做进界面（一期用全局那张表就够）。
        # 原样带着走，免得编辑一次通道就把手改进 json 的覆盖悄悄抹掉。
        self._responses = current.get("responses") or {}

        box = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._basic_page(current), "基础")
        tabs.addTab(self._advanced_page(current), "高级")
        box.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("取消")
        cancel.setObjectName("secondary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)

        self._on_flow_changed()
        self._on_envelope_toggled()

    # ------------------------------------------------------------------ #
    # 基础页
    # ------------------------------------------------------------------ #

    def _basic_page(self, current):
        page = QWidget()
        form = QFormLayout(page)

        self.key_input = QLineEdit(current.get("key", ""))
        self.key_input.setPlaceholderText("sorter_data / sorter_image")
        self.key_input.setToolTip("日志和历史里用它区分数据是哪条链接来的；小写字母开头")
        form.addRow("Key:", self.key_input)

        self.label_input = QLineEdit(current.get("label", ""))
        self.label_input.setPlaceholderText("分拣数值 / 分拣图片")
        form.addRow("备注（给人看）:", self.label_input)

        self.path_input = QLineEdit(current.get("path", ANY_PATH))
        self.path_input.setPlaceholderText("/callback")
        self.path_input.setToolTip(
            "设备 POST 过来的路径。填 * 表示兜底：没被别的通道认领的路径都归它。\n"
            "路径不区分大小写，结尾的 / 和 ?参数 会自动忽略。"
        )
        form.addRow("路径:", self.path_input)

        self.flow_combo = QComboBox()
        for text, value in FLOW_CHOICES:
            self.flow_combo.addItem(text, value)
        self.flow_combo.setCurrentIndex(
            max(self.flow_combo.findData(current.get("flow")), 0)
        )
        self.flow_combo.currentIndexChanged.connect(self._on_flow_changed)
        form.addRow("数据流:", self.flow_combo)

        timeout_row = QWidget()
        timeout_box = QHBoxLayout(timeout_row)
        timeout_box.setContentsMargins(0, 0, 0, 0)
        timeout_box.setSpacing(6)
        self.timeout_input = QLineEdit(f"{float(current.get('timeout') or 10):g}")
        self.timeout_input.setFixedWidth(72)
        self.timeout_hint = QLabel("秒　·　只有双向才会等")
        self.timeout_hint.setObjectName("hint")
        timeout_box.addWidget(self.timeout_input)
        timeout_box.addWidget(self.timeout_hint)
        timeout_box.addStretch()
        form.addRow("超时:", timeout_row)

        self.dispatch_combo = QComboBox()
        for text, value in DISPATCH_CHOICES:
            self.dispatch_combo.addItem(text, value)
        self.dispatch_combo.setCurrentIndex(
            max(self.dispatch_combo.findData(current.get("dispatch")), 0)
        )
        self.dispatch_combo.setToolTip(
            "并发：多条请求同时处理。\n单发：一条处理完才轮到下一条，本通道单独排队。"
        )
        form.addRow("分发:", self.dispatch_combo)

        tip = QLabel("单向 = 收下就回；双向 = 等前端回一条，再拿它答复设备。")
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        form.addRow(tip)
        return page

    def _on_flow_changed(self):
        twoway = self.flow_combo.currentData() == TWOWAY
        self.timeout_input.setEnabled(twoway)
        self.timeout_hint.setText("秒" if twoway else "秒　·　单向不会等，这项不生效")

    # ------------------------------------------------------------------ #
    # 高级页
    # ------------------------------------------------------------------ #

    def _advanced_page(self, current):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(10)

        warn = QLabel(
            "⚠️ 这里的设置需要了解 HTTP 和 JSON。基础页已经够用，不确定就别动这里。"
            "全部保持默认时，行为和不配置完全一样。"
        )
        warn.setObjectName("hint")
        warn.setWordWrap(True)
        box.addWidget(warn)

        form = QFormLayout()
        self.http_status_combo = QComboBox()
        for text, value in HTTP_STATUS_CHOICES:
            self.http_status_combo.addItem(text, value)
        self.http_status_combo.setCurrentIndex(
            max(self.http_status_combo.findData(current.get("http_status")), 0)
        )
        self.http_status_combo.setToolTip(
            "有些设备把非 200 的 HTTP 状态码一律当成网络故障、压根不看 body，\n"
            "这种就选「固定 200」，把业务码留在 body 里。"
        )
        form.addRow("HTTP 状态码:", self.http_status_combo)
        box.addLayout(form)

        self.envelope_check = QCheckBox("自定义应答外壳（回给设备的 body 形状）")
        self.envelope_check.setChecked(bool(current.get("envelope")))
        self.envelope_check.toggled.connect(self._on_envelope_toggled)
        box.addWidget(self.envelope_check)

        preset_row = QWidget()
        preset_box = QHBoxLayout(preset_row)
        preset_box.setContentsMargins(0, 0, 0, 0)
        preset_box.setSpacing(6)
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("选一个常用形状…", None)
        for text, template in ENVELOPE_PRESETS:
            self.preset_combo.addItem(text, template)
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        preset_box.addWidget(self.preset_combo, 1)
        box.addWidget(preset_row)

        self.envelope_edit = QPlainTextEdit()
        self.envelope_edit.setPlaceholderText('{\n  "code": "{{__code}}",\n  "msg": "{{__message}}"\n}')
        if current.get("envelope"):
            self.envelope_edit.setPlainText(
                json.dumps(current["envelope"], ensure_ascii=False, indent=2)
            )
        self.envelope_edit.setFixedHeight(150)
        self.envelope_edit.textChanged.connect(self._refresh_preview)
        box.addWidget(self.envelope_edit)

        variables = "　".join(f"{{{{{name}}}}}" for name in ENVELOPE_VARIABLES)
        vars_label = QLabel(f"可用变量：{variables}")
        vars_label.setObjectName("hint")
        vars_label.setWordWrap(True)
        vars_label.setToolTip("\n".join(f"{{{{{k}}}}}  {v}" for k, v in ENVELOPE_VARIABLES.items()))
        box.addWidget(vars_label)

        box.addWidget(QLabel("预览（双向拿到前端回复那一档）:"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(120)
        box.addWidget(self.preview)

        self.preview_problem = QLabel()
        self.preview_problem.setObjectName("hint")
        self.preview_problem.setWordWrap(True)
        box.addWidget(self.preview_problem)
        box.addStretch()
        return page

    def _apply_preset(self, index):
        template = self.preset_combo.itemData(index)
        if template is None:
            return
        self.envelope_check.setChecked(True)
        self.envelope_edit.setPlainText(json.dumps(template, ensure_ascii=False, indent=2))
        # 选完就弹回提示项，免得下拉一直显示着某个预设、看着像是"当前用的是它"
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(0)
        self.preset_combo.blockSignals(False)

    def _on_envelope_toggled(self):
        on = self.envelope_check.isChecked()
        for widget in (self.preset_combo, self.envelope_edit):
            widget.setEnabled(on)
        self._refresh_preview()

    def _parse_envelope(self):
        """返回 (模板, 问题清单)；没开自定义时模板为 None。"""
        if not self.envelope_check.isChecked():
            return None, []
        text = self.envelope_edit.toPlainText().strip()
        if not text:
            return None, ["模板是空的，留空请把上面的勾去掉"]
        try:
            template = json.loads(text)
        except ValueError as e:
            return None, [f"不是合法的 JSON：{e}"]
        problems = validate_envelope(template)
        return (template if not problems else None), problems

    def _refresh_preview(self):
        template, problems = self._parse_envelope()
        scene, code, message, extra = PREVIEW_SCENE
        preview_channel = {
            "key": self.key_input.text().strip() or "channel",
            "envelope": template,
        }
        if problems:
            self.preview.setPlainText("")
            self.preview_problem.setText("❌ " + "　·　".join(problems))
            return

        body = build_body(preview_channel, scene, code, message, extra)
        self.preview.setPlainText(json.dumps(body, ensure_ascii=False, indent=2))
        self.preview_problem.setText(
            "" if template else "未自定义：用内置形状，和不配置时完全一样"
        )

    # ------------------------------------------------------------------ #

    def accept(self):
        key = self.key_input.text().strip()
        if not re.fullmatch(KEY_PATTERN, key):
            QMessageBox.warning(
                self, "错误", "Key 用小写字母开头，只能含小写字母、数字、_ 和 -，最长 20 位"
            )
            return
        if key in self.taken_keys:
            QMessageBox.warning(self, "错误", f"已经有一条叫 {key} 的通道了")
            return

        path = normalize_path(self.path_input.text())
        if path in self.taken_paths:
            where = "兜底通道" if path == ANY_PATH else f"路径 {path}"
            QMessageBox.warning(self, "错误", f"已经有通道占用了{where}")
            return

        try:
            timeout = float(self.timeout_input.text().strip())
        except ValueError:
            timeout = -1
        if not MIN_REPLY_TIMEOUT <= timeout <= MAX_REPLY_TIMEOUT:
            QMessageBox.warning(
                self, "错误", f"超时要在 {MIN_REPLY_TIMEOUT:g}-{MAX_REPLY_TIMEOUT:g} 秒之间"
            )
            return

        _, problems = self._parse_envelope()
        if problems:
            QMessageBox.warning(self, "错误", "应答外壳有问题：\n\n" + "\n".join(problems))
            return

        super().accept()

    def values(self):
        template, _ = self._parse_envelope()
        return make_channel(
            key=self.key_input.text().strip(),
            label=self.label_input.text().strip(),
            path=normalize_path(self.path_input.text()),
            flow=self.flow_combo.currentData(),
            timeout=float(self.timeout_input.text().strip()),
            dispatch=self.dispatch_combo.currentData(),
            envelope=template,
            http_status=self.http_status_combo.currentData(),
            responses=self._responses,
        )
