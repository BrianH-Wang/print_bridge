"""模拟设备页签：自己填一份 JSON，像真设备那样 POST 到本机服务，看它回什么。

和「自测」的区别：自测按配置自动生成用例、自动核对；这里不核对，
发什么、发几次、先发哪条全由人定——拿设备方给的真实报文复现问题时用它。

前端那一侧：
  · 有真前端连着就交给它回复，这里只看设备收到的应答
  · 没有前端时可以让内置假前端按「前端回复」里填的内容回，双向通道才有结果可看
每次发送各走一个线程，配对通道可以先发主报文、不等它回来再发附加数据。
"""

import json
import queue
import threading
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QVBoxLayout, QWidget,
)

import selftest
from channels import ANY_PATH, TWOWAY
from pairing import ATTACH, NONE, pair_of, role_of
from ui.constants import LOG_POLL_MS

# 几何智造分拣设备的一组真实报文：数值和图片条码相同，能配上对。
# 编辑框按路径取这里的默认值，没有的路径才退回通道配置里的样例。
# 兜底通道（路径 *）什么都收，默认按数值接口 /callback 发
DEVICE_CODE = "CC28092600002"
DEVICE_IMAGE = (  # 132x132 PNG
    "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"
)
DEVICE_DEFAULTS = {
    "/callback": {"code": DEVICE_CODE, "weight": "12.50", "height": "35.20",
                  "width": "28.00", "length": "40.75"},
    "/upload": {"code": DEVICE_CODE, "file": DEVICE_IMAGE},
}


class SimulatePanel(QWidget):
    """模拟设备页签。context() 和自测页签共用主窗口那一份。"""

    def __init__(self, settings, context, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.context = context
        self._keys = ()
        self._loading = False       # 程序在回填编辑框，别当成用户改动存起来
        self.events = queue.Queue()
        self.pending = 0            # 还没回来的请求数
        self.frontend = None        # 内置假前端，没有真前端时才起
        self._frontend_lock = threading.Lock()   # 连发几条时别各起一个假前端
        self._replies = {}          # 双向通道 key -> 假前端该回的内容，发送时登记
        self._last_reply = None     # 报文里看不出通道（自定义模板）时回最近登记的那份
        self._seq = 0

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)

        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        box.addWidget(self.hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("通道:"))
        self.channel_combo = QComboBox()
        self.channel_combo.setMinimumWidth(180)
        self.channel_combo.currentIndexChanged.connect(self._load_editors)
        row.addWidget(self.channel_combo)
        row.addWidget(QLabel("路径:"))
        self.path_edit = QLineEdit()
        self.path_edit.setToolTip("默认是通道的路径，可以改成别的，比如故意打一个没配过的路径")
        row.addWidget(self.path_edit, 1)
        self.sample_btn = QPushButton("填入样例")
        self.sample_btn.setObjectName("secondary")
        self.sample_btn.setToolTip("把编辑框换回默认数据（分拣设备的真实报文，或通道配置里的样例）和回复样例")
        self.sample_btn.clicked.connect(self._fill_sample)
        row.addWidget(self.sample_btn)
        box.addLayout(row)

        opts = QHBoxLayout()
        opts.setSpacing(12)
        self.mock_check = QCheckBox("没有前端时用内置假前端回复")
        self.mock_check.setChecked(self.settings.value("simulate/mock", True, type=bool))
        self.mock_check.setToolTip("不勾的话，没有前端连着时双向通道只能等到超时")
        self.mock_check.toggled.connect(self._on_mock_toggled)
        opts.addWidget(self.mock_check)
        self.tag_check = QCheckBox("标记为自测（不进接收历史）")
        self.tag_check.setChecked(self.settings.value("simulate/tagged", False, type=bool))
        self.tag_check.setToolTip("不勾就和真设备一模一样：记日志、进接收历史")
        self.tag_check.toggled.connect(lambda on: self.settings.setValue("simulate/tagged", on))
        opts.addWidget(self.tag_check)
        self.pair_check = QCheckBox("连同配对通道一起发")
        self.pair_check.setChecked(self.settings.value("simulate/pair", True, type=bool))
        self.pair_check.setToolTip("当前通道在配对组里时，组里其他通道（如 /callback 配 /upload）"
                                   "也按各自编辑框里的内容发出去，和真设备一样各走各的连接")
        self.pair_check.toggled.connect(self._on_pair_toggled)
        opts.addWidget(self.pair_check)
        opts.addWidget(QLabel("间隔"))
        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setRange(0, 30)
        self.gap_spin.setDecimals(1)
        self.gap_spin.setSingleStep(0.1)
        self.gap_spin.setSuffix(" 秒")
        self.gap_spin.setValue(self.settings.value("simulate/gap", 0.5, type=float))
        self.gap_spin.setToolTip("当前通道先发，隔这么久再发下一条；0 就是同时发")
        self.gap_spin.valueChanged.connect(lambda v: self.settings.setValue("simulate/gap", v))
        opts.addWidget(self.gap_spin)
        opts.addStretch()
        self.clear_btn = QPushButton("清空记录")
        self.clear_btn.setObjectName("secondary")
        self.clear_btn.clicked.connect(lambda: self.output.clear())
        opts.addWidget(self.clear_btn)
        self.send_btn = QPushButton("发送")
        self.send_btn.setMinimumWidth(120)
        self.send_btn.clicked.connect(self.send)
        opts.addWidget(self.send_btn)
        box.addLayout(opts)

        editors = QSplitter(Qt.Orientation.Horizontal)
        editors.addWidget(self._labeled("设备发送的请求体（JSON）", "body_edit",
                                        "这条通道没有样例，在这里直接写设备要发的 JSON"))
        editors.addWidget(self._labeled("前端回复（假前端用，仅双向通道）", "reply_edit",
                                        "没填就回 {\"selftest\": \"ok\"}"))
        editors.setStretchFactor(0, 3)
        editors.setStretchFactor(1, 2)
        self.body_edit.textChanged.connect(lambda: self._save_text("data", self.body_edit))
        self.reply_edit.textChanged.connect(lambda: self._save_text("reply", self.reply_edit))

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(editors)
        self.output = QPlainTextEdit()
        self.output.setObjectName("log")
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("每发一次，这里记一笔：发了什么、设备收到的 HTTP 应答、前端收到的报文")
        splitter.addWidget(self.output)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        box.addWidget(splitter, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(LOG_POLL_MS)
        self._refresh()

    def _labeled(self, title, attr, placeholder):
        wrap = QWidget()
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        label = QLabel(title)
        label.setObjectName("hint")
        col.addWidget(label)
        edit = QPlainTextEdit()
        edit.setObjectName("log")
        edit.setPlaceholderText(placeholder)
        col.addWidget(edit, 1)
        setattr(self, attr, edit)
        return wrap

    # ------------------------------------------------------------------ #
    # 通道 / 编辑框
    # ------------------------------------------------------------------ #

    def _current_channel(self):
        key = self.channel_combo.currentData()
        return next((c for c in self.context()["channels"] if c["key"] == key), None)

    def _setting_key(self, kind, channel_key):
        return f"simulate/{kind}/{channel_key}"

    def _refresh_channels(self):
        """通道换了（切方案、加减通道）就重排下拉，没换不动。"""
        channels = self.context()["channels"]
        keys = tuple(c["key"] for c in channels)
        if keys == self._keys:
            return
        self._keys = keys
        current = self.channel_combo.currentData()
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        for channel in channels:
            self.channel_combo.addItem(f"{channel['key']}  {channel['label']}".strip(), channel["key"])
        index = self.channel_combo.findData(current)
        self.channel_combo.setCurrentIndex(index if index >= 0 else 0)
        self.channel_combo.blockSignals(False)
        self._load_editors()

    def _load_editors(self):
        channel = self._current_channel()
        self._loading = True
        try:
            if channel is None:
                self.path_edit.clear()
                self.body_edit.clear()
                self.reply_edit.clear()
                return
            self.path_edit.setText(_send_path(channel))
            body, reply = self._sample_texts(channel)
            self.body_edit.setPlainText(self.settings.value(self._setting_key("data", channel["key"]), "") or body)
            self.reply_edit.setPlainText(self.settings.value(self._setting_key("reply", channel["key"]), "") or reply)
            self.reply_edit.setEnabled(self._twoway(channel))
        finally:
            self._loading = False

    def _sample_texts(self, channel):
        sample = DEVICE_DEFAULTS.get(_send_path(channel)) or channel.get("sample")
        body = _dumps(sample) if sample else ""
        reply = _dumps(channel.get("reply_sample") or selftest.DEFAULT_REPLY)
        return body, reply

    def _fill_sample(self):
        channel = self._current_channel()
        if channel is None:
            return
        self.settings.remove(self._setting_key("data", channel["key"]))
        self.settings.remove(self._setting_key("reply", channel["key"]))
        self._load_editors()

    def _save_text(self, kind, edit):
        channel = self._current_channel()
        if self._loading or channel is None:
            return
        self.settings.setValue(self._setting_key(kind, channel["key"]), edit.toPlainText())

    @staticmethod
    def _twoway(channel):
        return channel["flow"] == TWOWAY and role_of(channel) != ATTACH

    # ------------------------------------------------------------------ #
    # 发送
    # ------------------------------------------------------------------ #

    def _refresh(self):
        ctx = self.context()
        if not ctx["running"]:
            text = "先点「启动服务器」，模拟设备打的是正在跑的服务"
        elif not ctx["enable_data"]:
            text = "没勾「获取数据」，没有 HTTP 接收可打"
        else:
            base_url, _ = selftest.endpoints(ctx)
            text = (f"像真设备一样把下面的 JSON POST 到 {base_url}，不做核对，只把来回的报文记下来。"
                    "配对通道勾上「连同配对通道一起发」就整组发；也可以一条条手动发，前一条还在等，下一条照样能发。")
            if self.pending:
                text += f"　（{self.pending} 条等应答中）"
        self.hint.setText(text)
        ok = ctx["running"] and ctx["enable_data"]
        channel = self._current_channel()
        self.send_btn.setEnabled(ok and channel is not None)
        partners = self._partners(channel) if channel is not None and self.pair_check.isChecked() else []
        self.send_btn.setText(f"一起发送（{len(partners) + 1} 条）" if partners else "发送")
        self.gap_spin.setEnabled(bool(partners))
        if not ctx["running"]:
            self._stop_frontend()

    def send(self):
        ctx = self.context()
        channel = self._current_channel()
        if channel is None or not ctx["running"] or not ctx["enable_data"]:
            return
        path = self.path_edit.text().strip() or "/"
        if not path.startswith("/"):
            path = "/" + path
        jobs = [(channel, path, self.body_edit.toPlainText(), self.reply_edit.toPlainText())]
        if self.pair_check.isChecked():
            for other in self._partners(channel):
                body_text, reply_text = self._texts_of(other)
                jobs.append((other, _send_path(other), body_text, reply_text))

        # 先全部解析一遍，有一条不对就一条都不发，免得只发出去半组
        parsed = []
        for job_channel, job_path, body_text, reply_text in jobs:
            who = f"[{job_channel['key']}] " if len(jobs) > 1 else ""
            try:
                body = json.loads(body_text)
            except ValueError as e:
                QMessageBox.warning(self, "请求体", f"{who}不是合法 JSON：{e}")
                return
            reply = None
            if self._twoway(job_channel):
                text = reply_text.strip()
                try:
                    reply = json.loads(text) if text else selftest.DEFAULT_REPLY
                except ValueError as e:
                    QMessageBox.warning(self, "前端回复", f"{who}不是合法 JSON：{e}")
                    return
            parsed.append((job_channel, job_path, body, reply))

        gap = self.gap_spin.value()
        for i, (job_channel, job_path, body, reply) in enumerate(parsed):
            self._fire(ctx, job_channel, job_path, body, reply, delay=gap * i)
        self._refresh()

    def _partners(self, channel):
        """同一配对组里的其他通道，按方案里的顺序。"""
        if role_of(channel) == NONE:
            return []
        group = pair_of(channel)["group"]
        return [c for c in self.context()["channels"]
                if c["key"] != channel["key"] and role_of(c) != NONE and pair_of(c)["group"] == group]

    def _texts_of(self, channel):
        """某条通道编辑框里会显示的内容：改过的用改过的，没改过用默认。"""
        body, reply = self._sample_texts(channel)
        return (self.settings.value(self._setting_key("data", channel["key"]), "") or body,
                self.settings.value(self._setting_key("reply", channel["key"]), "") or reply)

    def _fire(self, ctx, channel, path, body, reply, delay=0.0):
        """发一条，各走各的线程；delay 秒之后才真正发出去。"""
        if reply is not None:
            with self._frontend_lock:
                self._replies[channel["key"]] = reply
                self._last_reply = reply
        base_url, ws_url = selftest.endpoints(ctx)
        timeout = channel["timeout"] + 10
        tagged = self.tag_check.isChecked()
        use_mock = self.mock_check.isChecked()
        self._seq += 1
        seq = self._seq
        self.pending += 1
        when = f"（{delay:g}s 后发）" if delay else ""
        self._append(f"#{seq}  {time.strftime('%H:%M:%S')}  POST {path}（{channel['key']}）{when}\n"
                     f"{_dumps_short(body)}")

        def work():
            try:
                if delay:
                    time.sleep(delay)
                frontend, mark = None, 0
                if use_mock:
                    frontend = self._ensure_frontend(base_url, ws_url)
                    if frontend is not None:
                        mark = len(frontend.since(0))
                answer = selftest._post(base_url, path, body, timeout, tagged=tagged)
                received = None
                if frontend is not None:
                    time.sleep(0.3)   # 前端那边的报文比 HTTP 应答晚一点点落地
                    received = frontend.since(mark)
                self.events.put(("answer", (seq, answer, received)))
            except Exception as e:
                self.events.put(("answer", (seq, {"error": str(e)}, None)))

        threading.Thread(target=work, daemon=True).start()

    def _on_pair_toggled(self, on):
        self.settings.setValue("simulate/pair", on)
        self._refresh()

    def _ensure_frontend(self, base_url, ws_url):
        """没有真前端时起一个假前端并一直留着；有了真前端就让位。在工作线程里调。"""
        with self._frontend_lock:
            return self._ensure_frontend_locked(base_url, ws_url)

    def _ensure_frontend_locked(self, base_url, ws_url):
        clients, error = selftest.probe(base_url)
        if error:
            return None
        frontend = self.frontend
        alive = frontend is not None and not frontend.closed.is_set()
        if alive and clients <= 1:
            return frontend
        if alive:                       # 真前端连上来了，别跟它抢着回
            self._stop_frontend()
            return None
        if clients:
            return None
        frontend = selftest._Frontend(ws_url, selftest.MOCK)
        frontend.reply_for = self._reply_for
        if not frontend.start():
            return None
        self.frontend = frontend
        return frontend

    def _reply_for(self, message):
        """假前端收到一条报文时问这里：回什么、算不算双向。在假前端线程里调。"""
        with self._frontend_lock:
            key = message.get("channel")
            if key in self._replies:
                return self._replies[key], True
            if message.get("expectReply") is True or (key is None and self._last_reply is not None):
                return self._last_reply or selftest.DEFAULT_REPLY, True
            return None, False

    def _stop_frontend(self):
        frontend, self.frontend = self.frontend, None
        if frontend is not None:
            threading.Thread(target=frontend.stop, daemon=True).start()

    def _on_mock_toggled(self, on):
        self.settings.setValue("simulate/mock", on)
        if not on:
            self._stop_frontend()

    # ------------------------------------------------------------------ #

    def _tick(self):
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "answer":
                self._show_answer(*payload)
        self._refresh()
        self._refresh_channels()

    def _show_answer(self, seq, answer, received):
        self.pending -= 1
        if answer.get("error"):
            lines = [f"#{seq}  ❌ 请求失败：{answer['error']}"]
        else:
            lines = [f"#{seq}  ← HTTP {answer['status']}  {answer['elapsed']:.2f}s\n{_dumps_short(answer['body'])}"]
        if received is None:
            if self.mock_check.isChecked():
                lines.append(f"#{seq}  （有真前端连着，前端收到什么请看它那边）")
        elif received:
            for j, message in enumerate(received, 1):
                lines.append(f"#{seq}  前端收到 {j}\n{_dumps_short(message)}")
        else:
            lines.append(f"#{seq}  前端没收到报文")
        self._append("\n".join(lines))

    def _append(self, text):
        self.output.appendPlainText(text + "\n")
        bar = self.output.verticalScrollBar()
        bar.setValue(bar.maximum())


def _send_path(channel):
    """编辑框里默认填的路径：兜底通道没有固定路径，按设备的数值接口发。"""
    return "/callback" if channel["path"] == ANY_PATH else channel["path"]


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def _dumps_short(value):
    from logger import shorten
    return json.dumps(shorten(value, limit=200, head=60, tail=40), ensure_ascii=False, indent=2)
