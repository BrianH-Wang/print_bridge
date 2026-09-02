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
    ANY_PATH, ENVELOPE_PRESETS, ENVELOPE_VARIABLES, KEY_PATTERN, ONEWAY, TWOWAY,
    build_body, make_channel, normalize_path, validate_envelope,
)
from pairing import ATTACH as PAIR_ATTACH
from pairing import DEFAULT_GROUP as PAIR_DEFAULT_GROUP
from pairing import NONE as PAIR_NONE
from pairing import PRIMARY as PAIR_PRIMARY
from pairing import make_pair
from mapping import (
    CUSTOM, DEFAULT_MESSAGE_TYPE, OUTBOUND_VARIABLES, STANDARD, build_outbound,
    make_outbound, validate_outbound,
)
from ui.constants import (
    DISPATCH_CHOICES, FLOW_CHOICES, HTTP_STATUS_CHOICES, MAX_REPLY_TIMEOUT,
    MIN_REPLY_TIMEOUT, PAIR_MISSING_CHOICES, PAIR_ROLE_CHOICES, SERIAL,
)
from ui.dialogs.field_mapping import FieldMappingPage
from ui.helpers import sanitize_mapping, sanitize_outbound

# 预览用的假场景：双向拿到前端回复那一档，最能看出 data 放在哪
PREVIEW_SCENE = ("ok_reply", 200, "操作成功", {"forwarded": 1, "data": {"route": "A"}})


class ChannelDialog(QDialog):
    """一条链接的完整配置。

    基础页只有路径和方向——不懂 HTTP 的人填这几格就能用；
    高级页管报文形状，改了才需要懂 JSON。两页的默认值都等于当前行为。
    """

    def __init__(self, parent, channel=None, taken_keys=(), taken_paths=(), base_addr=""):
        super().__init__(parent)
        self.setWindowTitle("编辑通道" if channel else "添加通道")
        self.setMinimumWidth(680)
        # 监听地址只用来在基础页拼一条能直接复制去跑的 curl，不参与保存
        self.base_addr = base_addr or ""
        current = channel or {}
        self.taken_keys = {k for k in taken_keys if k != current.get("key")}
        self.taken_paths = {p for p in taken_paths if p != current.get("path")}
        # 按通道覆盖应答码这一项还没做进界面（一期用全局那张表就够）。
        # 原样带着走，免得编辑一次通道就把手改进 json 的覆盖悄悄抹掉。
        self._responses = current.get("responses") or {}

        box = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._basic_page(current), "基础")
        self.mapping_page = FieldMappingPage(
            self, current.get("mapping"), current.get("sample"),
        )
        self.mapping_page.changed.connect(self._refresh_outbound_preview)
        tabs.addTab(self.mapping_page, "字段映射")
        tabs.addTab(self._pair_page(current), "配对")
        # 两页高级设置按「去向」分，各自短一屏；⚙ 是给人的信号：这里要懂 JSON
        tabs.addTab(self._outbound_page(current), "发给前端 ⚙")
        tabs.addTab(self._advanced_page(current), "回给设备 ⚙")
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

        self._on_pair_role_changed()   # 里面会调 _on_flow_changed → 刷新基础页说明
        self._refresh_basic_summary()
        self._on_flow_changed()
        self._on_envelope_toggled()
        self._on_outbound_changed()

    # ------------------------------------------------------------------ #
    # 基础页
    # ------------------------------------------------------------------ #

    def _basic_page(self, current):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setSpacing(12)

        holder = QWidget()
        form = QFormLayout(holder)
        form.setContentsMargins(0, 0, 0, 0)
        # 输入框跟着对话框一起变宽：不然只有内容那么点宽度，
        # 连 "sorter_data / sorter_image" 这种占位符都显示不全
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        outer.addWidget(holder)

        self.key_input = QLineEdit(current.get("key", ""))
        self.key_input.setPlaceholderText("sorter_data / sorter_image")
        self.key_input.setToolTip("日志和历史里用它区分数据是哪条链接来的；小写字母开头")
        self.key_input.textChanged.connect(self._refresh_basic_summary)
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
        self.path_input.textChanged.connect(self._refresh_basic_summary)
        form.addRow("路径:", self.path_input)

        self.flow_combo = QComboBox()
        for text, value in FLOW_CHOICES:
            self.flow_combo.addItem(text, value)
        self.flow_combo.setCurrentIndex(
            max(self.flow_combo.findData(current.get("flow")), 0)
        )
        self.flow_combo.currentIndexChanged.connect(self._on_flow_changed)
        # 输入框拉满是为了显示得下长路径，下拉里只有几个字，拉满反而空
        self.flow_combo.setMaximumWidth(260)
        form.addRow("数据流:", self.flow_combo)

        timeout_row = QWidget()
        timeout_box = QHBoxLayout(timeout_row)
        timeout_box.setContentsMargins(0, 0, 0, 0)
        timeout_box.setSpacing(6)
        self.timeout_input = QLineEdit(f"{float(current.get('timeout') or 10):g}")
        self.timeout_input.setFixedWidth(72)
        self.timeout_input.textChanged.connect(self._refresh_basic_summary)
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
        self.dispatch_combo.currentIndexChanged.connect(self._refresh_basic_summary)
        self.dispatch_combo.setMaximumWidth(140)
        form.addRow("分发:", self.dispatch_combo)

        # 下半页原本是一大片空白（对话框高度是被「字段映射」页撑起来的）。
        # 拿来放一段实时说明：这条通道配成现在这样，设备该怎么调、会得到什么。
        outer.addWidget(QLabel("这条通道怎么用:"))
        self.basic_summary = QPlainTextEdit()
        self.basic_summary.setReadOnly(True)
        outer.addWidget(self.basic_summary, 1)
        return page

    def _refresh_basic_summary(self):
        if not hasattr(self, "basic_summary"):
            return   # 还在拼界面，控件没建全

        key = self.key_input.text().strip() or "（未填 Key）"
        path = normalize_path(self.path_input.text())
        twoway = self.flow_combo.currentData() == TWOWAY
        serial = self.dispatch_combo.currentData() == SERIAL
        base = (self.base_addr or "localhost:13213").split("/")[0]
        url = f"http://{base}{'/任意路径' if path == ANY_PATH else path}"

        lines = ["设备这样调用：", "",
                 f"  curl -X POST {url} \\",
                 '       -H "Content-Type: application/json" \\',
                 """       -d '{"code":"…"}'""", ""]

        role = getattr(self, "pair_role_combo", None)
        pair_role = role.currentData() if role is not None else PAIR_NONE

        if pair_role == PAIR_ATTACH:
            # 附加通道根本不推前端，别在这儿说"转给前端"跟下面的说明打架
            lines += ["收下就回 200，存进配对暂存区等主报文来取。"]
        elif twoway:
            timeout = self.timeout_input.text().strip() or "10"
            lines += [
                f"收到后转给前端，然后等前端回一条，最多等 {timeout} 秒，",
                "拿到回复才答复设备；等不到回 504。",
            ]
        else:
            lines += ["收到后转给前端，立刻回 200，不等前端。"]

        if serial and pair_role != PAIR_ATTACH:
            lines.append("本通道单发排队：一条处理完才轮到下一条（别的通道不受影响）。")

        if path == ANY_PATH:
            lines += ["", "路径填的是 *（兜底）：没被别的通道认领的路径都归它。"]

        if pair_role == PAIR_ATTACH:
            lines += ["", "配对里的「附加数据」：不会单独推给前端，所以固定单向。",
                      "它的内容会被并进同组主报文里一起发出去。"]
        elif pair_role == PAIR_PRIMARY:
            lines += ["", "配对里的「主报文」：先等同组的附加数据齐了，再把合并后的结果发给前端。"]
            lines += [f'前端收到的报文里带 "channel": "{key}"，据此区分数据来自哪条链接。']
        else:
            lines += ["", f'前端收到的报文里带 "channel": "{key}"，据此区分数据来自哪条链接。']

        self.basic_summary.setPlainText("\n".join(lines))

    def _on_flow_changed(self):
        twoway = self.flow_combo.currentData() == TWOWAY
        self.timeout_input.setEnabled(twoway)
        self.timeout_hint.setText("秒" if twoway else "秒　·　单向不会等，这项不生效")
        self._refresh_basic_summary()

    # ------------------------------------------------------------------ #
    # 配对合流
    # ------------------------------------------------------------------ #

    def _pair_page(self, current):
        pair = current.get("pair") or make_pair()
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(10)

        tip = QLabel(
            "两条链接的数据靠一个业务号合成一条再发给前端。"
            "比如分拣设备的图片走 /upload、数值走 /callback，同一个条码算一件。"
            "不用配对就保持「不参与」。"
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        box.addWidget(tip)

        form = QFormLayout()
        self.pair_role_combo = QComboBox()
        for text, value in PAIR_ROLE_CHOICES:
            self.pair_role_combo.addItem(text, value)
        self.pair_role_combo.setCurrentIndex(
            max(self.pair_role_combo.findData(pair.get("role")), 0)
        )
        self.pair_role_combo.currentIndexChanged.connect(self._on_pair_role_changed)
        form.addRow("角色:", self.pair_role_combo)

        self.pair_group_input = QLineEdit(pair.get("group", PAIR_DEFAULT_GROUP))
        self.pair_group_input.setPlaceholderText(PAIR_DEFAULT_GROUP)
        self.pair_group_input.setToolTip("同组的通道之间才互相配对；只有一组时随便填个名字")
        form.addRow("配对组:", self.pair_group_input)

        self.pair_field_input = QLineEdit(pair.get("field", ""))
        self.pair_field_input.setPlaceholderText("expressNo")
        self.pair_field_input.setToolTip(
            "配对键的字段名，取的是**「字段映射」页转译之后**的名字。\n"
            "两条通道源字段名不一样时（一边 code、一边 orderNo），\n"
            "各自先在映射里改成一致，这里填同一个就行。"
        )
        form.addRow("配对字段:", self.pair_field_input)
        box.addLayout(form)

        # ---- 主报文那一侧 ---- #
        self.primary_box = QWidget()
        primary_form = QFormLayout(self.primary_box)
        primary_form.setContentsMargins(0, 0, 0, 0)

        wait_row = QWidget()
        wait_layout = QHBoxLayout(wait_row)
        wait_layout.setContentsMargins(0, 0, 0, 0)
        wait_layout.setSpacing(6)
        self.pair_wait_input = QLineEdit(f"{float(pair.get('wait', 5)):g}")
        self.pair_wait_input.setFixedWidth(72)
        wait_hint = QLabel("秒　·　和「等前端回复」共用同一份超时预算")
        wait_hint.setObjectName("hint")
        wait_layout.addWidget(self.pair_wait_input)
        wait_layout.addWidget(wait_hint)
        wait_layout.addStretch()
        primary_form.addRow("最多等附加数据:", wait_row)

        self.pair_missing_combo = QComboBox()
        for text, value in PAIR_MISSING_CHOICES:
            self.pair_missing_combo.addItem(text, value)
        self.pair_missing_combo.setCurrentIndex(
            max(self.pair_missing_combo.findData(pair.get("on_missing")), 0)
        )
        primary_form.addRow("等不到时:", self.pair_missing_combo)
        box.addWidget(self.primary_box)

        # ---- 附加数据那一侧 ---- #
        self.attach_box = QWidget()
        attach_form = QFormLayout(self.attach_box)
        attach_form.setContentsMargins(0, 0, 0, 0)

        self.pair_value_input = QLineEdit(pair.get("value", ""))
        self.pair_value_input.setPlaceholderText("留空 = 除配对键外的字段全并进去")
        attach_form.addRow("并入哪个字段:", self.pair_value_input)

        self.pair_rename_input = QLineEdit(pair.get("rename", ""))
        self.pair_rename_input.setPlaceholderText("留空 = 保持原名")
        attach_form.addRow("并进去叫什么:", self.pair_rename_input)

        keep_row = QWidget()
        keep_layout = QHBoxLayout(keep_row)
        keep_layout.setContentsMargins(0, 0, 0, 0)
        keep_layout.setSpacing(6)
        self.pair_keep_input = QLineEdit(f"{float(pair.get('keep', 60)):g}")
        self.pair_keep_input.setFixedWidth(72)
        keep_hint = QLabel("秒　·　到点没人来取就清掉，图片不常驻内存")
        keep_hint.setObjectName("hint")
        keep_layout.addWidget(self.pair_keep_input)
        keep_layout.addWidget(keep_hint)
        keep_layout.addStretch()
        attach_form.addRow("暂存多久:", keep_row)
        box.addWidget(self.attach_box)

        self.pair_hint = QLabel()
        self.pair_hint.setObjectName("hint")
        self.pair_hint.setWordWrap(True)
        box.addWidget(self.pair_hint)
        box.addStretch()
        return page

    def _on_pair_role_changed(self):
        role = self.pair_role_combo.currentData()
        active = role != PAIR_NONE
        for widget in (self.pair_group_input, self.pair_field_input):
            widget.setEnabled(active)
        self.primary_box.setVisible(role == PAIR_PRIMARY)
        self.attach_box.setVisible(role == PAIR_ATTACH)

        if role == PAIR_ATTACH:
            # 附加通道天生是单向的：收下存起来立刻回，等主报文来取。
            # 允许选双向只会让人以为它会等前端，实际不会。
            self.flow_combo.setCurrentIndex(max(self.flow_combo.findData(ONEWAY), 0))
            self.flow_combo.setEnabled(False)
            self.flow_combo.setToolTip("附加数据通道收下就回，不等前端——所以固定单向")
            self.pair_hint.setText(
                "这条通道收到数据后不会推给前端，而是存进暂存区等主报文来取。"
            )
        else:
            self.flow_combo.setEnabled(True)
            self.flow_combo.setToolTip("")
            self.pair_hint.setText(
                "这条通道会先等同组的附加数据齐了，再把合并后的结果发给前端。"
                if role == PAIR_PRIMARY else ""
            )
        self._on_flow_changed()

    def _pair_values(self):
        """返回 (配对设置, 问题清单)。"""
        role = self.pair_role_combo.currentData()
        if role == PAIR_NONE:
            return None, []

        problems = []
        field = self.pair_field_input.text().strip()
        if not field:
            problems.append("配对字段不能为空，否则两条数据没法配对")

        def number(text, name, low, high, fallback):
            try:
                value = float(text)
            except ValueError:
                problems.append(f"{name}要填数字（秒）")
                return fallback
            if not low <= value <= high:
                problems.append(f"{name}要在 {low:g}-{high:g} 秒之间")
                return fallback
            return value

        wait = number(self.pair_wait_input.text().strip(), "等待时间", 0, 600, 5.0)
        keep = number(self.pair_keep_input.text().strip(), "暂存时间", 1, 3600, 60.0)

        return make_pair(
            role=role,
            group=self.pair_group_input.text().strip(),
            field=field,
            value=self.pair_value_input.text().strip(),
            rename=self.pair_rename_input.text().strip(),
            wait=wait,
            keep=keep,
            on_missing=self.pair_missing_combo.currentData(),
        ), problems

    # ------------------------------------------------------------------ #
    # 发给前端的报文
    # ------------------------------------------------------------------ #

    def _outbound_page(self, current):
        outbound = current.get("outbound") or make_outbound()
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(10)
        box.addWidget(self._warning(
            "改这里是为了迁就已经写死的前端接口。字段名改名在「字段映射」页，"
            "这一页管的是外面那层报文长什么样。"
        ))

        form = QFormLayout()
        self.type_input = QLineEdit(outbound.get("type", DEFAULT_MESSAGE_TYPE))
        self.type_input.setPlaceholderText(DEFAULT_MESSAGE_TYPE)
        self.type_input.setToolTip("前端靠 type 分辨这是哪种消息；默认 external_data")
        self.type_input.textChanged.connect(self._refresh_outbound_preview)
        form.addRow("消息 type:", self.type_input)

        self.extra_input = QLineEdit(
            json.dumps(outbound.get("extra") or {}, ensure_ascii=False)
            if outbound.get("extra") else ""
        )
        self.extra_input.setPlaceholderText('{"source": "仓库A"}')
        self.extra_input.setToolTip("固定附加字段，每条报文都带上；留空表示不加")
        self.extra_input.textChanged.connect(self._refresh_outbound_preview)
        form.addRow("固定附加字段:", self.extra_input)
        box.addLayout(form)

        self.outbound_check = QCheckBox("整条报文自己写（前端接口已写死、连外层都对不上时用）")
        self.outbound_check.setChecked(outbound.get("envelope") == CUSTOM)
        self.outbound_check.toggled.connect(self._on_outbound_changed)
        box.addWidget(self.outbound_check)

        self.outbound_edit = QPlainTextEdit()
        self.outbound_edit.setPlaceholderText(
            '{\n  "cmd": "newParcel",\n  "seq": "{{__id}}",\n  "expressNo": "{{expressNo}}"\n}'
        )
        if outbound.get("template"):
            self.outbound_edit.setPlainText(
                json.dumps(outbound["template"], ensure_ascii=False, indent=2)
            )
        self.outbound_edit.setFixedHeight(130)
        self.outbound_edit.textChanged.connect(self._refresh_outbound_preview)
        box.addWidget(self.outbound_edit)

        hint = QLabel(
            "可用：映射后的字段名（如 {{expressNo}}）　+　"
            + "　".join(f"{{{{{name}}}}}" for name in OUTBOUND_VARIABLES)
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        hint.setToolTip("\n".join(f"{{{{{k}}}}}  {v}" for k, v in OUTBOUND_VARIABLES.items()))
        box.addWidget(hint)

        box.addWidget(QLabel("预览（前端将收到的整条报文）:"))
        self.outbound_preview = QPlainTextEdit()
        self.outbound_preview.setReadOnly(True)
        self.outbound_preview.setFixedHeight(130)
        box.addWidget(self.outbound_preview)

        self.outbound_problem = QLabel()
        self.outbound_problem.setObjectName("hint")
        self.outbound_problem.setWordWrap(True)
        box.addWidget(self.outbound_problem)
        box.addStretch()
        return page

    def _on_outbound_changed(self):
        self.outbound_edit.setEnabled(self.outbound_check.isChecked())
        self._refresh_outbound_preview()

    def _parse_extra(self):
        text = self.extra_input.text().strip()
        if not text:
            return {}, []
        try:
            extra = json.loads(text)
        except ValueError as e:
            return {}, [f"固定附加字段不是合法 JSON：{e}"]
        if not isinstance(extra, dict):
            return {}, ["固定附加字段要是一个 JSON 对象"]
        return extra, []

    def _parse_outbound(self):
        """返回 (出向设置, 问题清单)。"""
        extra, problems = self._parse_extra()
        template = None
        envelope = STANDARD

        if self.outbound_check.isChecked():
            envelope = CUSTOM
            text = self.outbound_edit.toPlainText().strip()
            if not text:
                problems.append("模板是空的，留空请把上面的勾去掉")
            else:
                try:
                    template = json.loads(text)
                except ValueError as e:
                    problems.append(f"报文模板不是合法 JSON：{e}")

        outbound = make_outbound(envelope, self.type_input.text().strip(), extra, template)
        if envelope == CUSTOM and template is not None:
            problems += validate_outbound(outbound, self.mapping_page.target_fields())
        return outbound, problems

    def _refresh_outbound_preview(self):
        outbound, problems = self._parse_outbound()
        if problems:
            self.outbound_preview.setPlainText("")
            self.outbound_problem.setText("❌ " + "　·　".join(problems))
            return

        sample = self.mapping_page.sample()
        if sample is None:
            self.outbound_preview.setPlainText("")
            self.outbound_problem.setText("在「字段映射」页贴一条样例，这里就能看到实际效果")
            return

        preview_channel = {
            "key": self.key_input.text().strip() or "channel",
            "label": self.label_input.text().strip(),
            "path": normalize_path(self.path_input.text()),
            "mapping": self.mapping_page.values(),
            "outbound": outbound,
        }
        payload, map_problems = build_outbound(
            preview_channel, sample, "示例消息id",
            expect_reply=self.flow_combo.currentData() == TWOWAY,
        )
        self.outbound_preview.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))
        self.outbound_problem.setText(
            "❌ 字段映射有问题：" + "　·　".join(map_problems) if map_problems else ""
        )

    # ------------------------------------------------------------------ #
    # 回给设备的应答
    # ------------------------------------------------------------------ #

    @staticmethod
    def _warning(text):
        warn = QLabel("⚠️ " + text + " 全部保持默认时，行为和不配置完全一样。")
        warn.setObjectName("hint")
        warn.setWordWrap(True)
        return warn

    def _advanced_page(self, current):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(10)
        box.addWidget(self._warning(
            "这里的设置需要了解 HTTP 和 JSON，用来迁就已经写死的设备接口。"
        ))

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
        self.preset_combo.addItem("选一个常用形状", None)
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

        _, problems = self._parse_outbound()
        if problems:
            QMessageBox.warning(self, "错误", "发给前端的报文有问题：\n\n" + "\n".join(problems))
            return

        _, problems = self._pair_values()
        if problems:
            QMessageBox.warning(self, "错误", "配对设置有问题：\n\n" + "\n".join(problems))
            return

        problems = self.mapping_page.problems()
        if problems:
            # 映射问题只按样例算出来，样例本身可能不全 —— 提醒但不拦，
            # 真到运行时缺字段会按「必填字段缺失时」的策略处理
            answer = QMessageBox.question(
                self, "字段映射",
                "按当前样例试算，字段映射有这些问题：\n\n" + "\n".join(problems)
                + "\n\n样例不全时这是正常的。仍然保存？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        super().accept()

    def values(self):
        template, _ = self._parse_envelope()
        outbound, _ = self._parse_outbound()
        pair, _ = self._pair_values()
        mapping = self.mapping_page.values()
        sample = self.mapping_page.sample()

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
            mapping=sanitize_mapping(mapping),
            outbound=sanitize_outbound(outbound),
            # 样例跟着通道存：下次打开对话框预览立刻能用，
            # 不用再去翻设备文档重新粘一遍
            sample=sample,
            pair=pair,
        )
