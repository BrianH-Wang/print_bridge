"""HTTP 回调接收服务，并维护已连接的 WebSocket 客户端，用于把外部数据转发给前端。"""

import asyncio
import json
import secrets
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from logger import print_log, shorten

DEFAULT_PORT = 13213
MAX_BODY_BYTES = 16 * 1024 * 1024

# {(websocket, 它所属的事件循环)}，支持多个前端同时连接
_clients = set()
_clients_lock = threading.Lock()

_httpd = None
_httpd_lock = threading.Lock()

# 收到的数据经队列交给 GUI 线程渲染成历史表格，HTTP 线程不直接操作控件
_history_queue = None


def init_history(history_queue):
    global _history_queue
    _history_queue = history_queue


def _record_history(data, forwarded, msg_id=None):
    if _history_queue is not None:
        _history_queue.put({
            "time": datetime.now(),
            "data": data,
            "forwarded": forwarded,
            "msg_id": msg_id,
        })


# --------------------------------------------------------------------------- #
# WebSocket 客户端注册
# --------------------------------------------------------------------------- #

def register_websocket(ws):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        print_log("⚠️ 注册 WebSocket 时没有运行中的事件循环")
        return
    with _clients_lock:
        _clients.add((ws, loop))
    _ws_identifier(ws)


def unregister_websocket(ws):
    wid = _ws_id_map.pop(id(ws), None)
    with _clients_lock:
        for entry in [e for e in _clients if e[0] is ws]:
            _clients.discard(entry)


def client_count():
    with _clients_lock:
        return len(_clients)


def _on_send_done(future):
    try:
        future.result()
    except Exception as e:
        print_log(f"⚠️ WebSocket 发送失败: {e}")


def broadcast(payload):
    """线程安全地把 payload 推送给所有已连接的前端，返回推送客户端数。"""
    message = json.dumps(payload, ensure_ascii=False)
    with _clients_lock:
        targets = list(_clients)

    sent = 0
    for ws, loop in targets:
        try:
            future = asyncio.run_coroutine_threadsafe(ws.send(message), loop)
        except RuntimeError:
            unregister_websocket(ws)  # 事件循环已关闭
            continue
        future.add_done_callback(_on_send_done)
        sent += 1
    return sent


# --------------------------------------------------------------------------- #
# 送达确认：每条 external_data 带 id，前端用 ack 回执
# --------------------------------------------------------------------------- #

_pending_acks = {}  # message_id -> {total, acked: [ws_id], expires_at}
_pending_acks_lock = threading.Lock()

ACK_TIMEOUT_SECONDS = 3.0

_ws_id_map = {}  # id(ws) -> "c1" 之类
_ws_id_counter = [0]


def _ws_identifier(ws):
    wid = _ws_id_map.get(id(ws))
    if wid is None:
        _ws_id_counter[0] += 1
        wid = f"c{_ws_id_counter[0]}"
        _ws_id_map[id(ws)] = wid
    return wid


def new_message_id():
    return f"{int(time.time() * 1000) % 10**12:012d}{secrets.token_hex(2)}"


def broadcast_with_ack(payload):
    """推送并注册待确认。返回 (message_id, sent_clients)。

    与原 broadcast() 共存：旧调用方（带打印路径）继续用 broadcast() 不带 ack。
    """
    message_id = new_message_id()
    payload = {**payload, "id": message_id}
    sent = broadcast(payload)

    if sent == 0:
        return message_id, 0

    expires_at = time.monotonic() + ACK_TIMEOUT_SECONDS
    with _pending_acks_lock:
        _pending_acks[message_id] = {
            "total": sent,
            "acked": [],
            "expires_at": expires_at,
        }
    return message_id, sent


def record_ack(ws, message_id):
    """前端 ack 回执。返回 (acked_count, total, finalized)。"""
    wid = _ws_id_map.get(id(ws))
    if wid is None:
        return 0, 0, False

    with _pending_acks_lock:
        entry = _pending_acks.get(message_id)
        if entry is None:
            return 0, 0, False
        if wid in entry["acked"]:
            return len(entry["acked"]), entry["total"], False
        entry["acked"].append(wid)
        acked = len(entry["acked"])
        total = entry["total"]
        if acked >= total:
            del _pending_acks[message_id]
            return acked, total, True
        return acked, total, False


def pending_ack_snapshot():
    with _pending_acks_lock:
        return {
            mid: {
                "acked": list(entry["acked"]),
                "total": entry["total"],
                "expires_at": entry["expires_at"],
            }
            for mid, entry in _pending_acks.items()
        }


def expire_pending_acks():
    """把超时的等待项移除，返回 (message_id, acked, total) 列表。"""
    now = time.monotonic()
    expired = []
    with _pending_acks_lock:
        for mid, entry in list(_pending_acks.items()):
            if entry["expires_at"] <= now:
                expired.append((mid, len(entry["acked"]), entry["total"]))
                del _pending_acks[mid]
    return expired


# --------------------------------------------------------------------------- #
# HTTP 服务
# --------------------------------------------------------------------------- #

class _CallbackHandler(BaseHTTPRequestHandler):
    server_version = "PrintAppServer/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # 默认实现会写 stderr，这里交由业务日志统一输出

    def log_error(self, fmt, *args):
        print_log(f"⚠️ HTTP {fmt % args}")

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send_json(200, {
            "success": True,
            "code": 200,
            "clients": client_count(),
        })

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0

        if length > MAX_BODY_BYTES:
            self._send_json(413, {"success": False, "code": 413, "error": "请求体过大"})
            return

        body = self.rfile.read(length) if length else b""

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as e:
            print_log(f"❌ JSON 解析失败: {e}")
            self._send_json(400, {"success": False, "code": 400, "error": str(e)})
            return

        # 日志里折叠超长字段，转发给前端的仍是完整数据
        print_log(f"📥 接收数据: {shorten(data)}")
        msg_id, sent = broadcast_with_ack({"type": "external_data", "data": data})
        if sent:
            print_log(f"📤 已推送给 {sent} 个 WebSocket 客户端: {msg_id}")
        else:
            print_log("⚠️ 无 WebSocket 客户端连接，数据未转发")

        _record_history(data, sent, msg_id)

        self._send_json(200, {
            "success": True,
            "code": 200,
            "message": "数据已接收并处理",
            "forwarded": sent,
        })


def _parse_addr(addr):
    """把 'http://localhost:13213/callback' 之类的地址解析成 (host, port)。"""
    addr = addr.strip()
    for prefix in ("http://", "https://"):
        if addr.startswith(prefix):
            addr = addr[len(prefix):]
    addr = addr.split("/")[0]
    if ":" in addr:
        host, _, port = addr.partition(":")
        return host or "localhost", int(port)
    return addr or "localhost", DEFAULT_PORT


def start_http_server(addr, ready_event=None):
    """阻塞运行 HTTP 服务，直到 stop_http_server() 被调用。"""
    global _httpd
    try:
        host, port = _parse_addr(addr)
        httpd = ThreadingHTTPServer((host, port), _CallbackHandler)
    except Exception as e:
        print_log(f"❌ 无法启动 HTTP 服务: {e}")
        return

    with _httpd_lock:
        _httpd = httpd

    if ready_event is not None:
        ready_event.set()  # 绑定成功，界面据此确认服务真的起来了
    print_log(f"🌐 HTTP 监听启动: http://{host}:{port}")
    try:
        httpd.serve_forever(poll_interval=0.2)
    finally:
        httpd.server_close()  # 释放监听端口，否则重启时会提示端口占用
        with _httpd_lock:
            if _httpd is httpd:
                _httpd = None
        print_log("🛑 HTTP 服务已停止")


def stop_http_server():
    with _httpd_lock:
        httpd = _httpd
    if httpd is not None:
        httpd.shutdown()
