"""启动 / 停止服务：校验各项配置，拉起 WebSocket 与 HTTP 线程。

同样是挂在主窗口上的 mixin——它要读遍所有卡片的输入。
"""

import threading

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QMessageBox

from data_receiver import configure_flow, configure_responses, start_http_server, stop_http_server
from logger import print_log
from printer_utils import describe_printer
from websocket_server import start_websocket_server


class ServerMixin:
    """需要宿主提供：各卡片控件、printer_list、history、http_responses。"""

    def _set_inputs_enabled(self, enabled):
        for widget in (
            self.port_input,
            self.addr_input, self.profile_combo, *self.profile_buttons,
            self.flow_combo, self.dispatch_combo, self.timeout_input,
        ):
            widget.setEnabled(enabled)
        # 超时框、打印机那组控件还要看当前模式/选中行，由它们自己决定最终状态
        self._on_flow_changed()
        self.printer_list.set_editable(enabled)

        # 复选框不用 setEnabled(False)：macOS 上禁用后的勾选标记几乎看不见，
        # 运行时就分辨不出启用了哪些功能。这里只屏蔽交互，保持正常外观。
        for box in (self.print_checkbox, self.data_checkbox):
            box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not enabled)
            box.setFocusPolicy(
                Qt.FocusPolicy.StrongFocus if enabled else Qt.FocusPolicy.NoFocus
            )

    def toggle_server(self):
        if self.running:
            self.stop_server()
        else:
            self.start_server()

    def _validate_port(self):
        text = self.port_input.text().strip()
        if not text.isdigit():
            QMessageBox.warning(self, "错误", "请输入有效的端口号")
            return None
        port = int(text)
        if not 1024 <= port <= 65535:
            QMessageBox.warning(self, "错误", "端口号必须在 1024-65535 之间")
            return None
        return port

    def start_server(self):
        enable_print = self.print_checkbox.isChecked()
        enable_data = self.data_checkbox.isChecked()

        if not (enable_print or enable_data):
            QMessageBox.warning(self, "错误", "请至少勾选一项功能")
            return

        # 先完成全部校验，避免任何一项不合法时留下已启动的服务
        port = self._validate_port()
        if port is None:
            return
        addr = self._validate_addr() if enable_data else None
        if enable_data and addr is None:
            return
        timeout = self._validate_timeout() if enable_data else None
        if enable_data and timeout is None:
            return
        printers = self.printer_list.validate() if enable_print else []
        if enable_print and printers is None:
            return

        # WebSocket 是前端的通信通道，只要启用了任一功能就要跑：
        # 「自动打印」靠它收打印数据，「获取数据」靠它把外部数据推给前端。
        # 未启用自动打印时传 None，收到打印数据会被明确拒绝而不是真去打印。
        for printer in printers or []:
            print_log(f"🖨 打印目标: {describe_printer(printer)}")
        self.ws_stop_event.clear()
        self.ws_ready.clear()
        self.http_ready.clear()

        self.ws_thread = threading.Thread(
            target=start_websocket_server,
            args=(port, printers, self.ws_stop_event, self.ws_ready, self.history.enqueue_ack),
            daemon=True,
        )
        self.ws_thread.start()

        if enable_data:
            self.addr_input.setText(addr)   # 回写规整后的写法，所见即所用
            configure_responses(self.http_responses)
            configure_flow(
                mode=self.flow_combo.currentData(),
                timeout=timeout,
                dispatch=self.dispatch_combo.currentData(),
            )
            self.http_thread = threading.Thread(
                target=start_http_server,
                args=(addr, self.http_ready),
                daemon=True,
            )
            self.http_thread.start()

        # 端口被占用时线程里只会打日志，这里回查一次绑定结果，避免界面假装在运行
        QTimer.singleShot(1200, lambda: self._verify_started(enable_data))

        self.running = True
        self._set_inputs_enabled(False)
        self.start_btn.setText("停止服务器")
        self.start_btn.setObjectName("danger")
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)
        self._set_status("服务运行中", "running")

    def _verify_started(self, enable_data):
        """确认端口真的绑上了；没绑上就回滚，不要显示成运行中。"""
        if not self.running:
            return

        failed = []
        if not self.ws_ready.is_set():
            failed.append("WebSocket")
        if enable_data and not self.http_ready.is_set():
            failed.append("HTTP")
        if not failed:
            return

        print_log(f"❌ {' 和 '.join(failed)} 服务启动失败，已回滚（端口可能被占用）")
        self.stop_server()
        self._set_status(
            f"{' / '.join(failed)} 启动失败，端口可能被占用，详见日志", "error"
        )

    def stop_server(self):
        if self.http_thread:
            stop_http_server()
            self.http_thread.join(timeout=3)
            self.http_thread = None

        if self.ws_thread:
            self.ws_stop_event.set()
            self.ws_thread.join(timeout=3)
            self.ws_thread = None

        self.running = False
        self._set_inputs_enabled(True)
        self.start_btn.setText("启动服务器")
        self.start_btn.setObjectName("")
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)
        self._set_status("服务已停止", "stopped")
