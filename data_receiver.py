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

ONEWAY = "oneway"       # 设备 → 本程序 → 前端，收下就回 200
TWOWAY = "twoway"       # 设备 → 本程序 → 前端 → 本程序 → 设备，要等前端回一条再应答
CONCURRENT = "concurrent"   # 多条请求同时处理，2 不必等 1 回来
SERIAL = "serial"           # 单发：一条处理完（拿到回复或超时）才轮到下一条

DEFAULT_REPLY_TIMEOUT = 10.0

# HTTP 应答表：按「场景」而不是按状态码索引 —— 同一个码可能对应两种问题
# （503 既可能是没人接、也可能是排队没轮到），分开才说得清是哪一种。
# 码和文案都可以在界面上改；{} 里的占位符由对应场景填。
RESPONSE_DEFAULTS = {
    "ok":            {"code": 200, "message": "数据已接收并处理"},
    "ok_reply":      {"code": 200, "message": "前端已处理并回复"},
    "bad_json":      {"code": 400, "message": "JSON 解析失败：{error}"},
    "too_large":     {"code": 413, "message": "请求体过大，上限 {limit} 字节"},
    "no_client":     {"code": 503, "message": "无 WebSocket 客户端连接，无法取得回复"},
    "queue_timeout": {"code": 503, "message": "单发模式排队等待超过 {timeout}s"},
    "reply_timeout": {"code": 504, "message": "等待前端回复超时（{timeout}s）"},
}

# (场景名, 什么时候会返回, 可用占位符)，界面上照这个顺序列出来
RESPONSE_LABELS = [
    ("ok",            "接收成功（单向）",   "单向模式下转发给前端后立即返回", ""),
    ("ok_reply",      "接收成功（双向）",   "双向模式下拿到前端回复后返回，body 的 data 里是回复内容", ""),
    ("bad_json",      "请求体不是合法 JSON", "解析 POST 过来的 body 失败", "{error}"),
    ("too_large",     "请求体过大",         f"body 超过 {MAX_BODY_BYTES} 字节", "{limit}"),
    ("no_client",     "没有前端连接",       "双向模式下一个 WebSocket 客户端都没连着", ""),
    ("queue_timeout", "排队超时",           "单发模式下排到超时都没轮到", "{timeout}"),
    ("reply_timeout", "等待回复超时",       "双向模式下前端在超时时间内没回 external_reply", "{timeout}"),
]

_responses = {}
_responses_lock = threading.Lock()

# {(websocket, 它所属的事件循环)}，支持多个前端同时连接
_clients = set()
_clients_lock = threading.Lock()

# 数据流配置，由 GUI 在启动服务时按当前方案下发
_flow = {
    "mode": ONEWAY,
    "timeout": DEFAULT_REPLY_TIMEOUT,
    "dispatch": CONCURRENT,
}
_flow_lock = threading.Lock()

# 单发模式下用它把请求排成一队；并发模式下不碰。
# 用叫号（ticket）而不是普通 Lock：Lock 不保证先到先得，
# 三条请求排队时完成顺序可能是 2、3、1，设备侧看到的次序就乱了。
_serial_cv = threading.Condition()
_serial_state = {"next_ticket": 0, "serving": 0, "abandoned": set()}


def _serial_acquire(deadline):
    """按到达顺序排队，超时没轮到就放弃。返回是否拿到号。"""
    with _serial_cv:
        ticket = _serial_state["next_ticket"]
        _serial_state["next_ticket"] += 1

        while ticket != _serial_state["serving"]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # 放弃的号要登记，轮到它时由前一位直接跳过，否则整队会卡死
                _serial_state["abandoned"].add(ticket)
                return False
            _serial_cv.wait(remaining)
        return True


def _serial_release():
    with _serial_cv:
        serving = _serial_state["serving"] + 1
        while serving in _serial_state["abandoned"]:
            _serial_state["abandoned"].discard(serving)
            serving += 1
        _serial_state["serving"] = serving
        _serial_cv.notify_all()

_httpd = None
_httpd_lock = threading.Lock()

# 收到的数据经队列交给 GUI 线程渲染成历史表格，HTTP 线程不直接操作控件
_history_queue = None


def init_history(history_queue):
    global _history_queue
    _history_queue = history_queue


def configure_flow(mode=ONEWAY, timeout=DEFAULT_REPLY_TIMEOUT, dispatch=CONCURRENT):
    """由 GUI 在启动服务时下发当前方案的数据流设置。"""
    with _flow_lock:
        _flow["mode"] = mode if mode in (ONEWAY, TWOWAY) else ONEWAY
        _flow["timeout"] = float(timeout) if timeout else DEFAULT_REPLY_TIMEOUT
        _flow["dispatch"] = dispatch if dispatch in (CONCURRENT, SERIAL) else CONCURRENT
    print_log(
        f"🔁 数据流: {'双向（等前端回复）' if _flow['mode'] == TWOWAY else '单向'}"
        f" · {'单发' if _flow['dispatch'] == SERIAL else '并发'}"
        + (f" · 超时 {_flow['timeout']:g}s" if _flow["mode"] == TWOWAY else "")
    )


def flow_config():
    with _flow_lock:
        return dict(_flow)


def configure_responses(mapping):
    """下发自定义的 HTTP 应答表；没配到的场景自动落回默认值。"""
    clean = {}
    for key, entry in (mapping or {}).items():
        if key not in RESPONSE_DEFAULTS or not isinstance(entry, dict):
            continue
        try:
            code = int(entry.get("code", RESPONSE_DEFAULTS[key]["code"]))
        except (TypeError, ValueError):
            continue
        if not 100 <= code <= 599:
            continue
        message = str(entry.get("message", RESPONSE_DEFAULTS[key]["message"]))
        clean[key] = {"code": code, "message": message}

    with _responses_lock:
        _responses.clear()
        _responses.update(clean)


def response_for(key, **fmt):
    """取某个场景的 (状态码, 文案)；占位符缺参数时原样返回，不让打印链路因为文案报错。"""
    with _responses_lock:
        entry = _responses.get(key)
    entry = entry or RESPONSE_DEFAULTS[key]
    try:
        message = entry["message"].format(**fmt)
    except (KeyError, IndexError, ValueError):
        message = entry["message"]
    return int(entry["code"]), message


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


def broadcast_with_ack(payload, ack_timeout=None, message_id=None):
    """推送并注册待确认。返回 (message_id, sent_clients)。

    与原 broadcast() 共存：旧调用方（带打印路径）继续用 broadcast() 不带 ack。
    ack_timeout: 双向模式下前端要等业务处理完才回，比默认 3s 长，由调用方指定。
    message_id: 双向模式要先按 id 开好等待槽再发，否则前端回得比开槽还快就接不住。
    """
    message_id = message_id or new_message_id()
    payload = {**payload, "id": message_id}
    sent = broadcast(payload)

    if sent == 0:
        return message_id, 0

    expires_at = time.monotonic() + max(ACK_TIMEOUT_SECONDS, ack_timeout or 0)
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


# --------------------------------------------------------------------------- #
# 双向数据流：等前端把处理结果回传，再拿它答复设备
# --------------------------------------------------------------------------- #

_pending_replies = {}  # message_id -> {"event": Event, "reply": ..., "from": ws_id}
_pending_replies_lock = threading.Lock()


def _open_reply_slot(message_id):
    event = threading.Event()
    with _pending_replies_lock:
        _pending_replies[message_id] = {"event": event, "reply": None, "from": None}
    return event


def _close_reply_slot(message_id):
    with _pending_replies_lock:
        return _pending_replies.pop(message_id, None)


def record_reply(ws, message_id, payload):
    """前端回传处理结果。返回 (accepted, acked, total, finalized)。

    多个前端都连着时以最先回来的那条为准，后到的丢弃；
    回复同时当作一次 ack，界面「推送」列不用前端再单发一条 ack。
    """
    with _pending_replies_lock:
        entry = _pending_replies.get(message_id)
        if entry is None:
            return False, 0, 0, False      # 已超时收摊，或本来就不是双向请求
        if entry["event"].is_set():
            return False, 0, 0, False      # 已经有前端抢先回过了
        entry["reply"] = payload
        entry["from"] = _ws_id_map.get(id(ws))
        entry["event"].set()

    acked, total, done = record_ack(ws, message_id)
    return True, acked, total, done


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

    def _reply(self, key, extra=None, **fmt):
        """所有对设备的应答都走这里，码和文案取自可配置的应答表。"""
        code, message = response_for(key, **fmt)
        body = {"success": code < 400, "code": code}
        body["message" if code < 400 else "error"] = message
        body.update(extra or {})
        self._send_json(code, body)

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
            self._reply("too_large", limit=MAX_BODY_BYTES)
            return

        body = self.rfile.read(length) if length else b""

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as e:
            print_log(f"❌ JSON 解析失败: {e}")
            self._reply("bad_json", error=str(e))
            return

        # 日志里折叠超长字段，转发给前端的仍是完整数据
        print_log(f"📥 接收数据: {shorten(data)}")

        flow = flow_config()
        # 设备最多等这么久：单发模式下排队和等回复共用这一份预算，
        # 不会因为前面排了几条就把设备晾成 2 倍、3 倍超时。
        deadline = time.monotonic() + flow["timeout"]

        if flow["dispatch"] == SERIAL:
            if not _serial_acquire(deadline):
                print_log("⏱ 单发模式排队超时，已拒绝本条")
                self._reply("queue_timeout", timeout=f"{flow['timeout']:g}")
                return
            try:
                self._dispatch(data, flow, deadline)
            finally:
                _serial_release()
        else:
            self._dispatch(data, flow, deadline)

    def _dispatch(self, data, flow, deadline):
        """把数据转给前端；双向模式下等前端回一条，再拿它答复设备。"""
        twoway = flow["mode"] == TWOWAY
        payload = {"type": "external_data", "data": data}
        event = None
        pre_id = None
        if twoway:
            payload["expectReply"] = True
            # 先开槽再广播：本机前端回得可能比这行代码还快，槽没开好就接不住了
            pre_id = new_message_id()
            event = _open_reply_slot(pre_id)

        # 双向下 ack 的有效期跟着回复超时走，否则 3s 就过期了、界面会误报未确认
        msg_id, sent = broadcast_with_ack(
            payload,
            ack_timeout=flow["timeout"] if twoway else None,
            message_id=pre_id,
        )
        if sent:
            print_log(f"📤 已推送给 {sent} 个 WebSocket 客户端: {msg_id}")
        else:
            print_log("⚠️ 无 WebSocket 客户端连接，数据未转发")

        _record_history(data, sent, msg_id)

        if not twoway:
            self._reply("ok", extra={"forwarded": sent})
            return

        if not sent:
            # 没人接，等下去也不会有回复，直接告诉设备
            _close_reply_slot(msg_id)
            self._reply("no_client", extra={"forwarded": 0})
            return

        got = event.wait(timeout=max(deadline - time.monotonic(), 0))
        entry = _close_reply_slot(msg_id)

        if not got:
            print_log(f"⏱ 等待前端回复超时 ({flow['timeout']:g}s): {msg_id}")
            self._reply("reply_timeout", extra={"forwarded": sent},
                        timeout=f"{flow['timeout']:g}")
            return

        reply = entry["reply"] if entry else None
        print_log(f"↩️ 前端回复 {entry['from'] if entry else '?'}: {shorten(reply)}")
        self._reply("ok_reply", extra={"forwarded": sent, "data": reply})


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
