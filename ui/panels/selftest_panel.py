"""自测页签：把当前方案真的跑一遍，逐条打勾。

跑的是 selftest.run —— 和命令行 tools/selftest.py 同一套。这里只负责三件事：
从主窗口拿当前配置、把进度刷到树上、让人点开看原始报文。
自测跑在后台线程，进度经队列交给定时器刷新，HTTP 线程不碰控件。
"""

import json
import queue
import threading

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import selftest
from ui.constants import LOG_POLL_MS

MARK = {True: "✅", False: "❌"}
LEVEL_MARK = {selftest.ERROR: "❌", selftest.WARN: "⚠️"}
DEFAULT_DATA = "default"   # 按通道里的样例自动生成
CUSTOM_DATA = "custom"     # 用户自己填的请求体，按通道 key 记在本机设置里
DATA_MODES = [("默认（按通道样例自动生成）", DEFAULT_DATA), ("自定义", CUSTOM_DATA)]

KIND_LABEL = {
    selftest.SINGLE: "单条", selftest.PAIR: "配对", selftest.BAD_JSON: "坏 JSON",
    selftest.MISSING: "缺字段", selftest.NO_ROUTE: "错路径", selftest.TIMEOUT: "超时",
}


class SelftestPanel(QWidget):
    """自测页签。context() 由主窗口提供，返回当前配置和运行状态。"""

    def __init__(self, settings, context, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.context = context
        self._data_keys = ()        # 数据区下拉里当前列着的通道 key
        self._loading_data = False  # 程序在回填编辑框，别当成用户改动存起来
        self.events = queue.Queue()
        self.worker = None
        self.report = None
        # 每行对应的用例和结果放在 Python 这边：塞进 item.setData 的 dict 会被
        # 转成 QVariantMap 再拷回来，拿不到原对象，按身份找不着
        self._rows = {}      # id(item) -> {"item", "case", "result"} / {"item", "lint"}
        self._by_case = {}   # id(case) -> item

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)

        tools = QHBoxLayout()
        tools.setSpacing(10)
        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        tools.addWidget(self.hint, 1)

        self.negative_check = QCheckBox("负向用例")
        self.negative_check.setChecked(True)
        self.negative_check.setToolTip("坏 JSON、缺必填字段、打错路径——对接时最常争议的就是这几个错误码")
        tools.addWidget(self.negative_check)

        self.slow_check = QCheckBox("超时用例（慢）")
        self.slow_check.setToolTip("假前端故意不回复，看设备侧的超时应答。每条双向通道要等满它配的超时秒数")
        tools.addWidget(self.slow_check)

        self.run_btn = QPushButton("开始自测")
        self.run_btn.setMinimumWidth(120)
        self.run_btn.clicked.connect(self.start)
        tools.addWidget(self.run_btn)
        box.addLayout(tools)

        # 测试数据：默认按通道样例自动生成；想换个条码、少个字段就切成自定义，
        # 改的内容按通道 key 记在本机，下次打开还在
        data_row = QHBoxLayout()
        data_row.setSpacing(8)
        data_row.addWidget(QLabel("测试数据:"))
        self.data_channel = QComboBox()
        self.data_channel.setMinimumWidth(180)
        self.data_channel.currentIndexChanged.connect(self._load_data_editor)
        data_row.addWidget(self.data_channel)
        self.data_mode = QComboBox()
        for text, mode in DATA_MODES:
            self.data_mode.addItem(text, mode)
        self.data_mode.currentIndexChanged.connect(self._on_data_mode_changed)
        data_row.addWidget(self.data_mode)
        self.data_reset = QPushButton("恢复默认")
        self.data_reset.setObjectName("secondary")
        self.data_reset.setToolTip("把这条通道的自定义数据清掉，回到按样例自动生成")
        self.data_reset.clicked.connect(self._reset_data)
        data_row.addWidget(self.data_reset)
        data_row.addStretch()
        box.addLayout(data_row)

        self.data_edit = QPlainTextEdit()
        self.data_edit.setObjectName("log")
        self.data_edit.setFixedHeight(96)
        self.data_edit.setPlaceholderText("这条通道没有样例。在通道对话框的「字段映射」页填一份，或者切成自定义直接在这里写")
        self.data_edit.textChanged.connect(self._on_data_edited)
        box.addWidget(self.data_edit)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self.tree = QTreeWidget()
        self.tree.setObjectName("selftest")
        self.tree.setHeaderLabels(["结果", "用例 / 核对项", "耗时"])
        # 子级要缩进一层，第一列得给缩进 + 图标留够，不然勾被截掉半个
        self.tree.setIndentation(18)
        self.tree.setColumnWidth(0, 96)
        self.tree.setColumnWidth(1, 640)
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.currentItemChanged.connect(self._show_detail)
        splitter.addWidget(self.tree)

        self.detail = QPlainTextEdit()
        self.detail.setObjectName("log")
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("选中一条用例，这里显示它发出去的请求、设备收到的应答和前端收到的报文")
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        box.addWidget(splitter, 1)

        self.summary = QLabel()
        self.summary.setObjectName("status")
        self.summary.setProperty("state", "idle")
        self.summary.setText("还没跑过")
        box.addWidget(self.summary)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(LOG_POLL_MS)
        self._refresh_hint()

    # ------------------------------------------------------------------ #
    # 启动 / 进度
    # ------------------------------------------------------------------ #

    def _refresh_hint(self):
        ctx = self.context()
        busy = self.worker is not None and self.worker.is_alive()
        if busy:
            text = "自测进行中…"
        elif not ctx["running"]:
            text = "先点「启动服务器」，自测打的是正在跑的服务"
        elif not ctx["enable_data"]:
            text = "没勾「获取数据」，没有 HTTP 接收可测"
        else:
            base_url, _ = selftest.endpoints(ctx)
            text = (f"按当前方案的 {len(ctx['channels'])} 条通道生成用例，真发到 {base_url}。"
                    "没有前端连着就用内置假前端；有的话只旁观。自测数据不进接收历史。")
        self.hint.setText(text)
        self.run_btn.setEnabled(not busy and ctx["running"] and ctx["enable_data"])
        self.run_btn.setText("自测中…" if busy else "开始自测")

    def start(self):
        ctx = self.context()
        if not ctx["running"] or not ctx["enable_data"]:
            return
        base_url, ws_url = selftest.endpoints(ctx)
        channels, problems = self._channels_with_data(ctx["channels"])
        if problems:
            QMessageBox.warning(self, "测试数据", "\n".join(problems))
            return
        negative, slow = self.negative_check.isChecked(), self.slow_check.isChecked()

        self.tree.clear()
        self.detail.clear()
        self._rows.clear()
        self._by_case.clear()
        self.report = None
        self._set_summary("自测进行中…", "idle")

        def work():
            try:
                selftest.run(channels, base_url, ws_url, ctx["http_responses"],
                             negative=negative, slow=slow,
                             progress=lambda event, payload: self.events.put((event, payload)))
            except Exception as e:      # 别让线程悄悄死掉，界面会一直显示"进行中"
                self.events.put(("crash", str(e)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        self._refresh_hint()

    def _tick(self):
        drained = False
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            drained = True
            handler = getattr(self, f"_on_{event}", None)
            if handler:
                handler(payload)
        # 服务启停不会通知这里，每次轮询顺手把按钮状态对一遍
        self._refresh_hint()
        self._refresh_data_channels()

    # ------------------------------------------------------------------ #
    # 测试数据
    # ------------------------------------------------------------------ #

    def _setting_key(self, kind, channel_key):
        return f"selftest/{kind}/{channel_key}"

    def _data_mode_of(self, channel_key):
        return self.settings.value(self._setting_key("mode", channel_key), DEFAULT_DATA)

    def _custom_text_of(self, channel_key):
        return self.settings.value(self._setting_key("data", channel_key), "") or ""

    def _refresh_data_channels(self):
        """通道换了（切方案、加减通道）就重排下拉，没换不动。"""
        channels = self.context()["channels"]
        keys = tuple(c["key"] for c in channels)
        if keys == self._data_keys:
            return
        self._data_keys = keys
        current = self.data_channel.currentData()
        self.data_channel.blockSignals(True)
        self.data_channel.clear()
        for channel in channels:
            label = f"{channel['key']}  {channel['label']}".strip()
            self.data_channel.addItem(label, channel["key"])
        index = self.data_channel.findData(current)
        self.data_channel.setCurrentIndex(index if index >= 0 else 0)
        self.data_channel.blockSignals(False)
        self._load_data_editor()

    def _current_channel(self):
        key = self.data_channel.currentData()
        return next((c for c in self.context()["channels"] if c["key"] == key), None)

    def _load_data_editor(self):
        channel = self._current_channel()
        self._loading_data = True
        try:
            if channel is None:
                self.data_edit.clear()
                self.data_edit.setReadOnly(True)
                return
            mode = self._data_mode_of(channel["key"])
            self.data_mode.blockSignals(True)
            self.data_mode.setCurrentIndex(self.data_mode.findData(mode))
            self.data_mode.blockSignals(False)
            sample_text = _dumps(channel["sample"]) if channel.get("sample") else ""
            if mode == CUSTOM_DATA:
                self.data_edit.setPlainText(self._custom_text_of(channel["key"]) or sample_text)
            else:
                self.data_edit.setPlainText(sample_text)
            self.data_edit.setReadOnly(mode != CUSTOM_DATA)
            self.data_reset.setEnabled(mode == CUSTOM_DATA)
        finally:
            self._loading_data = False

    def _on_data_mode_changed(self):
        channel = self._current_channel()
        if channel is None:
            return
        self.settings.setValue(self._setting_key("mode", channel["key"]), self.data_mode.currentData())
        self._load_data_editor()

    def _on_data_edited(self):
        channel = self._current_channel()
        if self._loading_data or channel is None or self.data_mode.currentData() != CUSTOM_DATA:
            return
        self.settings.setValue(self._setting_key("data", channel["key"]), self.data_edit.toPlainText())

    def _reset_data(self):
        channel = self._current_channel()
        if channel is None:
            return
        self.settings.remove(self._setting_key("data", channel["key"]))
        self.settings.setValue(self._setting_key("mode", channel["key"]), DEFAULT_DATA)
        self._load_data_editor()

    def _channels_with_data(self, channels):
        """把自定义数据套到通道的 sample 上：用例的请求体和预期值都从 sample 算，
        换掉它一处，两边自然一致。返回 (通道列表, 问题清单)。"""
        result, problems = [], []
        for channel in channels:
            channel = dict(channel)
            if self._data_mode_of(channel["key"]) == CUSTOM_DATA:
                text = self._custom_text_of(channel["key"]).strip()
                try:
                    body = json.loads(text) if text else None
                except ValueError as e:
                    problems.append(f"[{channel['key']}] 自定义数据不是合法 JSON：{e}")
                    continue
                if body is not None and not isinstance(body, dict):
                    problems.append(f"[{channel['key']}] 自定义数据最外层必须是一个 JSON 对象")
                    continue
                channel["sample"] = body
            result.append(channel)
        return result, problems

    # ------------------------------------------------------------------ #
    # 事件 → 树
    # ------------------------------------------------------------------ #

    def _on_lint(self, findings):
        root = QTreeWidgetItem(["", "静态体检", ""])
        if not findings:
            root.setText(0, "✅")
            root.setText(1, "静态体检：没发现配置问题")
        else:
            worst = selftest.ERROR if any(f["level"] == selftest.ERROR for f in findings) else selftest.WARN
            root.setText(0, LEVEL_MARK[worst])
            root.setText(1, f"静态体检：{len(findings)} 条提醒")
            for item in findings:
                who = f"[{item['channel']}] " if item["channel"] else ""
                child = QTreeWidgetItem([LEVEL_MARK[item["level"]], who + item["text"], ""])
                child.setToolTip(1, item["text"])
                root.addChild(child)
        self.tree.addTopLevelItem(root)
        root.setExpanded(bool(findings))
        self._rows[id(root)] = {"item": root, "lint": findings}

    def _on_start(self, case):
        item = QTreeWidgetItem(["⏳", f"{case['title']}（{KIND_LABEL.get(case['kind'], case['kind'])}）", ""])
        self._rows[id(item)] = {"item": item, "case": case, "result": None}
        self._by_case[id(case)] = item
        self.tree.addTopLevelItem(item)
        self.tree.scrollToItem(item)

    def _on_skip(self, case):
        item = QTreeWidgetItem(["⏭", f"{case['title']}  —— 有真前端连着，跳过", ""])
        self._rows[id(item)] = {"item": item, "case": case, "result": None}
        self.tree.addTopLevelItem(item)

    def _on_result(self, result):
        item = self._by_case.get(id(result["case"]))
        if item is None:
            return
        item.setText(0, MARK[result["ok"]])
        item.setText(2, f"{result['elapsed']:.2f}s")
        self._rows[id(item)]["result"] = result
        for check in result["checks"]:
            child = QTreeWidgetItem([MARK[check["ok"]], check["text"], ""])
            child.setToolTip(1, check["text"])
            item.addChild(child)
        item.setExpanded(not result["ok"])   # 过了的折起来，一眼只看红的

    def _on_done(self, report):
        self.report = report
        self.worker = None
        if report["error"]:
            self._set_summary(f"❌ {report['error']}", "error")
            return
        mode = ("内置假前端" if report["mode"] == selftest.MOCK
                else f"旁观真前端（{report['clients'] - 1} 个）")
        skipped = f"，跳过 {len(report['skipped'])}" if report.get("skipped") else ""
        errors = sum(1 for f in report["lint"] if f["level"] == selftest.ERROR)
        ok = not report["failed"] and not errors
        self._set_summary(
            f"{'✅' if ok else '❌'} 通过 {report['passed']} / 失败 {report['failed']}{skipped}"
            f"　·　{mode}" + (f"　·　静态体检 {errors} 处错误" if errors else ""),
            "running" if ok else "error",
        )

    def _on_crash(self, text):
        self.worker = None
        self._set_summary(f"❌ 自测线程异常：{text}", "error")

    # ------------------------------------------------------------------ #

    def _set_summary(self, text, state):
        self.summary.setText(text)
        self.summary.setProperty("state", state)
        self.summary.style().unpolish(self.summary)
        self.summary.style().polish(self.summary)

    def _show_detail(self, current, _previous):
        if current is None:
            return
        top = current if current.parent() is None else current.parent()
        data = self._rows.get(id(top)) or {}
        if "lint" in data:
            self.detail.setPlainText("\n".join(
                f"{LEVEL_MARK[f['level']]} [{f['channel']}] {f['text']}" for f in data["lint"]
            ) or "没发现问题")
            return
        case, result = data.get("case"), data.get("result")
        if not case:
            return
        lines = []
        for i, step in enumerate(case["steps"]):
            body = step["body"]
            shown = body.decode("utf-8", "replace") if isinstance(body, bytes) else _dumps_short(body)
            lines.append(f"── 请求 {i + 1}  POST {step['path']}\n{shown}")
            if result and i < len(result["answers"]) and result["answers"][i]:
                answer = result["answers"][i]
                if answer["error"]:
                    lines.append(f"── 应答 {i + 1}  请求失败：{answer['error']}")
                else:
                    lines.append(f"── 应答 {i + 1}  HTTP {answer['status']}  {answer['elapsed']:.2f}s\n"
                                 f"{_dumps_short(answer['body'])}")
        if result:
            if result["received"]:
                for j, message in enumerate(result["received"]):
                    lines.append(f"── 前端收到 {j + 1}\n{_dumps_short(message)}")
            else:
                lines.append("── 前端没收到报文")
        self.detail.setPlainText("\n\n".join(lines))


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def _dumps_short(value):
    from logger import shorten
    return json.dumps(shorten(value, limit=200, head=60, tail=40), ensure_ascii=False, indent=2)
