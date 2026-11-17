"""HTTP 回调接收服务，并维护已连接的 WebSocket 客户端，用于把外部数据转发给前端。"""

import asyncio
import json
import re
import secrets
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from channels import (
    ANY_PATH, CONCURRENT, DEFAULT_REPLY_TIMEOUT, MAX_BODY_BYTES, ONEWAY,
    RESPONSE_DEFAULTS, RESPONSE_LABELS, SERIAL, TWOWAY,
    build_body, describe_channel, http_status_for, make_channel, resolve_response, route,
)
from history_store import save_bad_request
from logger import begin_request, end_request, log_request, print_log, shorten
from mapping import build_outbound, check_files, map_fields
from pairing import (
    ATTACH, PRIMARY, attach_channels, clear_pairs, describe_pair, expire_pairs,
    pair_of, role_of, store_attach, take_attaches,
)
from pairing import ERROR as PAIR_ERROR
from pairing import merge as merge_attaches

DEFAULT_PORT = 13213

# 常量和应答表都下沉到 channels.py 了（本模块要 import 它来路由，反过来就成环）。
# 这里 re-export 一份，`from data_receiver import TWOWAY` 这类老写法照常能用。
__all__ = [
    "ANY_PATH", "CONCURRENT", "DEFAULT_PORT", "DEFAULT_REPLY_TIMEOUT", "MAX_BODY_BYTES",
    "ONEWAY", "RESPONSE_DEFAULTS", "RESPONSE_LABELS", "SERIAL", "TWOWAY",
]

_responses = {}
_responses_lock = threading.Lock()

# {(websocket, 它所属的事件循环)}，支持多个前端同时连接
_clients = set()
_clients_lock = threading.Lock()

# 接收通道，由 GUI 在启动服务时按当前方案下发。
# 至少留一条兜底通道，否则任何 POST 都会 404 —— 老方案升级上来就是这一条。
_channels = [make_channel()]
_channels_lock = threading.Lock()

# 单发模式下用它把请求排成一队；并发模式下不碰。
# 用叫号（ticket）而不是普通 Lock：Lock 不保证先到先得，
# 三条请求排队时完成顺序可能是 2、3、1，设备侧看到的次序就乱了。
#
# 每条通道各一条队：图片通道慢的时候，不该把数值通道也堵死。
_serial_states = {}
_serial_guard = threading.Lock()


def _serial_state_for(key):
    with _serial_guard:
        state = _serial_states.get(key)
        if state is None:
            state = {"cv": threading.Condition(), "next_ticket": 0,
                     "serving": 0, "abandoned": set()}
            _serial_states[key] = state
        return state


def _serial_acquire(state, deadline):
    """按到达顺序排队，超时没轮到就放弃。返回是否拿到号。"""
    with state["cv"]:
        ticket = state["next_ticket"]
        state["next_ticket"] += 1

        while ticket != state["serving"]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # 放弃的号要登记，轮到它时由前一位直接跳过，否则整队会卡死
                state["abandoned"].add(ticket)
                return False
            state["cv"].wait(remaining)
        return True


def _serial_release(state):
    with state["cv"]:
        serving = state["serving"] + 1
        while serving in state["abandoned"]:
            state["abandoned"].discard(serving)
            serving += 1
        state["serving"] = serving
        state["cv"].notify_all()

_httpd = None
_httpd_lock = threading.Lock()

# 收到的数据经队列交给 GUI 线程渲染成历史表格，HTTP 线程不直接操作控件
_history_queue = None


# 坏报文落盘的目录（跟着历史存储目录走）；没设就只进日志不落盘
_bad_request_dir = None


def init_history(history_queue):
    global _history_queue
    _history_queue = history_queue


def set_bad_request_dir(directory):
    global _bad_request_dir
    _bad_request_dir = directory


def configure_channels(channels):
    """由 GUI 在启动服务时下发当前方案的接收通道。"""
    clean = [c for c in (channels or []) if isinstance(c, dict) and c.get("path")]
    if not clean:
        clean = [make_channel()]
    with _channels_lock:
        _channels[:] = clean
        _serial_states.clear()   # 通道换了，旧队列的号作废，留着会让新请求等一个永不到来的号
    clear_pairs()                # 组和配对字段可能都变了，旧的暂存数据对不上

    print_log(f"🔁 接收通道 {len(clean)} 条：")
    for channel in clean:
        extra = "自定义应答外壳" if channel.get("envelope") else "默认应答外壳"
        print_log(f"   · {describe_channel(channel)}"
                  f" · {'单发' if channel['dispatch'] == SERIAL else '并发'}"
                  + (f" · 超时 {channel['timeout']:g}s" if channel["flow"] == TWOWAY else "")
                  + f" · {extra}"
                  + (f" · 配对 {describe_pair(channel)}" if channel.get("pair") else ""))


def configure_flow(mode=ONEWAY, timeout=DEFAULT_REPLY_TIMEOUT, dispatch=CONCURRENT):
    """老接口：把单份数据流设置包成一条兜底通道。

    只剩没迁移到通道的调用方（和测试）在用，行为和以前一致：
    任何路径都接，全走这一套设置。
    """
    configure_channels([make_channel(
        path=ANY_PATH, flow=mode, timeout=timeout, dispatch=dispatch,
    )])


def channel_list():
    with _channels_lock:
        return list(_channels)


def channel_for(path):
    with _channels_lock:
        return route(_channels, path)


def channel_for_key(key):
    """按 key 找通道，找不到返回 None。

    历史重发要用：记录里存的是当初那条通道的 key，通道后来被删掉或改名时
    返回 None，调用方按「没有通道」处理（标准形状、不转译），即老行为。
    """
    if not key:
        return None
    with _channels_lock:
        for channel in _channels:
            if channel["key"] == key:
                return channel
    return None


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


def response_for(key, channel=None, **fmt):
    """取某个场景的 (状态码, 文案)：通道覆盖 > 全局应答表 > 出厂默认。"""
    with _responses_lock:
        overrides = {k: dict(v) for k, v in _responses.items()}
    return resolve_response(key, channel, overrides, **fmt)


def _record_history(data, forwarded, msg_id=None, channel=None):
    if _history_queue is not None:
        _history_queue.put({
            "time": datetime.now(),
            "data": data,
            "forwarded": forwarded,
            "msg_id": msg_id,
            "channel": channel,
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
        print_log(str(e), event="⚠️ 发送失败")


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


def broadcast_with_ack(payload, ack_timeout=None, message_id=None, inject_id=True):
    """推送并注册待确认。返回 (message_id, sent_clients)。

    与原 broadcast() 共存：旧调用方（带打印路径）继续用 broadcast() 不带 ack。
    ack_timeout: 双向模式下前端要等业务处理完才回，比默认 3s 长，由调用方指定。
    message_id: 双向模式要先按 id 开好等待槽再发，否则前端回得比开槽还快就接不住。
    inject_id:  报文里还没有 id 时由这里补一个。走 mapping.build_outbound 的
                调用方自己已经把 id 放到位了（自定义模板下 id 未必叫 id），传 False。
    """
    message_id = message_id or new_message_id()
    if inject_id:
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

# 自测发来的请求带这个头：照常走完整条链路，但不进接收历史、日志上打 🧪 标记
SELFTEST_HEADER = "X-Print-Bridge-Selftest"

# 简约日志里「单号」列从这些字段里取，转译前后的名字都认
WAYBILL_KEYS = ("shipmentCode", "expressNo", "code", "waybillNo", "orderNo")
_RESULT_LABELS = {key: label for key, label, *_ in RESPONSE_LABELS}
_OK_REPLIES = ("ok", "ok_reply")


def _waybill_of(data):
    if isinstance(data, dict):
        for key in WAYBILL_KEYS:
            value = data.get(key)
            if value not in (None, "") and not isinstance(value, (dict, list)):
                return str(value)
    return ""


_WAYBILL_IN_TEXT = re.compile(
    r'"(?:' + "|".join(WAYBILL_KEYS) + r')"\s*:\s*"?([^",}\s]+)')


def _waybill_in_text(body):
    """解析不了的报文里也试着把单号抠出来，简约日志按单号还能搜到它。"""
    match = _WAYBILL_IN_TEXT.search(body.decode("utf-8", errors="replace"))
    return match.group(1) if match else ""


def _note_of(body):
    """回给设备的那句话：各家外壳叫法不一样，挨个找；没有就把回复本身缩短了放上去。

    原样透传前端回复时（比如只有 route 的分拣结果）没有 msg，这时内容本身就是使用者想看的。
    """
    if isinstance(body, dict):
        for key in ("msg", "message", "error"):
            if body.get(key):
                return str(body[key])
    return json.dumps(shorten(body), ensure_ascii=False)


class _CallbackHandler(BaseHTTPRequestHandler):
    server_version = "PrintAppServer/1.0"
    protocol_version = "HTTP/1.1"
    selftest = False

    def log_message(self, fmt, *args):
        pass  # 默认实现会写 stderr，这里交由业务日志统一输出

    def log_error(self, fmt, *args):
        print_log(fmt % args, event="⚠️ HTTP")

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reply(self, key, extra=None, channel=None, **fmt):
        """所有对设备的应答都走这里。

        码和文案取自可配置的应答表；body 的形状交给通道的应答外壳
        （没配就是内置形状，和以前完全一样）。
        """
        code, message = response_for(key, channel, **fmt)
        body = build_body(channel, key, code, message, extra)
        status = http_status_for(channel, code)
        node = channel["key"] if channel else None
        self._send_json(status, body)

        # 总耗时：从收到请求到应答写回设备，排队、等配对、等前端回复全算在里面
        elapsed_ms = (time.monotonic() - self._started) * 1000
        phases = "".join(f" · {label} {ms:.0f}ms" for label, ms in self._phases)
        print_log(f"HTTP {status}（{key}）· 耗时 {elapsed_ms:.0f}ms{phases} · "
                  f"{json.dumps(shorten(body), ensure_ascii=False)}",
                  node=node, event="📨 回给设备")
        result = f"{code} {_RESULT_LABELS.get(key, key)}"
        raw, error = self._bad_body or (None, None)
        log_request(node, self._waybill, key in _OK_REPLIES,
                    ("🧪 " if self.selftest else "") + result, elapsed_ms,
                    note=_note_of(body), msg_id=self._msg_id, phases=self._phases,
                    raw=raw, error=error, request=self._request_text() if raw is not None else None,
                    saved=self._saved)

    def _phase(self, label, since):
        """记一段等待花了多久（排队、等配对、等前端回复），简约日志的耗时列里拆开给人看。"""
        ms = (time.monotonic() - since) * 1000
        if ms >= 1:   # 没排队、图早就到了这种 0ms 的不列，免得刷屏
            self._phases.append((label, ms))

    def _read_body(self):
        """读请求体：认 Content-Length，也认分块传输（chunked）。超过上限返回 None。

        有的设备 / HTTP 库不带 Content-Length 而是分块发，只认长度的话读到的就是空 body。
        """
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            chunks, total = [], 0
            while True:
                size_line = self.rfile.readline(65537)
                size = int(size_line.split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    # 跳过 trailer，直到空行
                    while self.rfile.readline(65537) not in (b"\r\n", b"\n", b""):
                        pass
                    return b"".join(chunks)
                total += size
                if total > MAX_BODY_BYTES:
                    return None
                chunks.append(self.rfile.read(size))
                self.rfile.readline(65537)   # 块尾的 CRLF
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length > MAX_BODY_BYTES:
            return None
        return self.rfile.read(length) if length else b""

    def _request_text(self):
        """请求行 + 来源 + 请求头，出错时和源数据一起给人看。"""
        host, port = self.client_address[:2]
        lines = [f"{self.command} {self.path}  ←  {host}:{port}"]
        lines += [f"{name}: {value}" for name, value in self.headers.items()]
        return "\n".join(lines)

    def do_GET(self):
        self._send_json(200, {
            "success": True,
            "code": 200,
            "clients": client_count(),
            "channels": [c["key"] for c in channel_list()],
        })

    def do_POST(self):
        # 每个请求一个号：这一路写的详细日志都挂在它下面，简约日志里双击能调出来
        self._started = time.monotonic()
        self._waybill = ""
        self._msg_id = None
        self._bad_body = None   # JSON 解析失败时的 (body, 异常)，简约日志那一行也带上
        self._saved = None      # 坏报文落盘的文件
        self._phases = []       # [(等什么, 毫秒)]
        begin_request()
        try:
            self._handle_post()
        finally:
            end_request()

    def _handle_post(self):
        self.selftest = self.headers.get(SELFTEST_HEADER) == "1"
        # 先定通道：应答外壳、超时、单双向全看它，连报错都要按它的形状回
        channel = channel_for(self.path)
        if channel is None:
            print_log(f"{self.path} 没有匹配的通道，已拒绝", event="⚠️ 无匹配通道")
            self._reply("no_route", path=self.path)
            return

        body = self._read_body()
        if body is None:
            self._reply("too_large", channel=channel, limit=MAX_BODY_BYTES)
            return

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as e:
            error = str(e)
            if not body:
                # 空 body 时 json 只会说 "Expecting value"，看不出是对方压根没发东西
                error = (f"请求体是空的（Content-Length: {self.headers.get('Content-Length', '无')}，"
                         f"Content-Type: {self.headers.get('Content-Type', '无')}）")
            # 源报文跟着日志走：日志里双击能看到设备到底发了什么、错在第几行第几列
            self._bad_body = (body, e)
            self._waybill = _waybill_in_text(body)
            request = self._request_text()
            self._saved = self._save_bad(channel["key"], body, error, e, request)
            print_log(error, node=channel["key"], raw=body, error=e, event="❌ JSON 解析失败",
                      request=request, saved=self._saved)
            self._reply("bad_json", channel=channel, error=error)
            return

        self._waybill = _waybill_of(data)

        # 日志里折叠超长字段，转发给前端的仍是完整数据
        tag = "🧪 " if self.selftest else ""
        print_log(shorten(data), node=channel["key"], event=f"{tag}📥 接收")

        # 设备最多等这么久：单发模式下排队和等回复共用这一份预算，
        # 不会因为前面排了几条就把设备晾成 2 倍、3 倍超时。
        deadline = time.monotonic() + channel["timeout"]

        if channel["dispatch"] == SERIAL:
            state = _serial_state_for(channel["key"])
            since = time.monotonic()
            acquired = _serial_acquire(state, deadline)
            self._phase("排队", since)
            if not acquired:
                print_log("单发模式排队超时，已拒绝本条", node=channel["key"], event="⏱ 排队超时")
                self._reply("queue_timeout", channel=channel,
                            timeout=f"{channel['timeout']:g}")
                return
            try:
                # 轮到了但预算已经用光：别再推给前端然后立刻放弃 —— 那样数据发出去了
                # 却没人等它的回复，设备还会收到一个牛头不对马嘴的「等待回复超时」。
                # 单向没有等待这回事，照常发。
                if channel["flow"] == TWOWAY and time.monotonic() >= deadline:
                    print_log("排到时预算已用完，未推送", node=channel["key"], event="⏱ 排队超时")
                    self._reply("queue_timeout", channel=channel,
                                timeout=f"{channel['timeout']:g}")
                    return
                self._dispatch(data, channel, deadline)
            finally:
                _serial_release(state)
        else:
            self._dispatch(data, channel, deadline)

    def _save_bad(self, node, body, message, error, request):
        """坏报文写到磁盘，重启后也能翻。写不进去只记一笔，不耽误回设备。"""
        if _bad_request_dir is None or self.selftest:
            return None
        # 空 body 时用自己的说明（json 只会说 Expecting value），位置信息还从异常里拿
        if body:
            message = error
        try:
            return str(save_bad_request(_bad_request_dir, node, body, message, request))
        except OSError as e:
            print_log(f"坏报文没存下来：{e}", node=node, event="⚠️ 落盘失败")
            return None

    def _hold_for_pairing(self, data, channel):
        """附加通道：转译后存进暂存区，收下就回，不广播给前端。

        它的数据要等主报文来了一起发。这条路径天生是单向的——所以配对里
        「先到的那条怎么应答」根本不是问题。
        """
        pair = pair_of(channel)
        mapped, problems = map_fields(data, channel.get("mapping"))
        self._waybill = _waybill_of(mapped) or self._waybill
        if problems:
            print_log('；'.join(problems), node=channel["key"], event="❌ 转译失败")
            self._reply("map_failed", channel=channel, error="；".join(problems))
            return

        key = mapped.get(pair["field"]) if isinstance(mapped, dict) else None
        if key in (None, ""):
            message = f"配对字段 {pair['field']} 取不到值"
            print_log(message, node=channel["key"], event="❌ 配对失败")
            self._reply("map_failed", channel=channel, error=message)
            return

        total = store_attach(pair["group"], str(key), channel["key"], mapped,
                             pair["keep"], pair["max_pending"])
        print_log(f"{pair['field']}={key}（暂存区 {total} 条）", node=channel["key"],
                  event="🧩 暂存待配对")
        if not self.selftest:
            _record_history(data, 0, None, channel["key"])
        self._reply("ok", extra={"forwarded": 0}, channel=channel)

    def _collect_pair(self, channel, mapped, deadline):
        """主报文：等同组的附加数据齐了再走。返回 (要并进去的字段, 该不该继续)。"""
        pair = pair_of(channel)
        wanted = attach_channels(channel_list(), pair["group"])
        if not wanted:
            return {}, True   # 组里没有附加通道，没什么可等的

        key = mapped.get(pair["field"]) if isinstance(mapped, dict) else None
        if key in (None, ""):
            message = f"配对字段 {pair['field']} 取不到值"
            print_log(message, node=channel["key"], event="❌ 配对失败")
            self._reply("map_failed", channel=channel, error=message)
            return {}, False

        # 等图和等前端回复共用同一份预算：设备最多等它配的那个秒数，
        # 不会因为先等了 5 秒图就把总时长变成 wait + timeout
        since = time.monotonic()
        wait_until = min(since + pair["wait"], deadline)
        found, missing = take_attaches(pair["group"], str(key),
                                       [c["key"] for c in wanted], wait_until)
        self._phase("配对", since)

        if missing:
            names = "、".join(missing)
            if pair["on_missing"] == PAIR_ERROR:
                print_log(f"等不到 {names}，已拒绝本条", node=channel["key"], event="⏱ 配对超时")
                self._reply("pair_timeout", channel=channel,
                            missing=names, timeout=f"{pair['wait']:g}")
                return {}, False
            print_log(f"等不到 {names}，按缺项照发", node=channel["key"], event="⚠️ 配对缺项")

        specs = {c["key"]: pair_of(c) for c in wanted}
        extra = merge_attaches(mapped, found, specs)
        if extra:
            print_log(f"{len(found)} 份附加数据：{'、'.join(extra)}", node=channel["key"],
                      event="🧩 已合并")
        return extra, True

    def _dispatch(self, data, channel, deadline):
        """转译字段、组装报文、推给前端；双向模式下等前端回一条再答复设备。"""
        # 文件校验放在最前面：图片过大 / 解不开的请求连暂存区都不该进
        bad = check_files(data, channel.get("mapping"))
        if bad:
            scene, error, limit = bad
            print_log(str(error), node=channel["key"], event="❌ 文件校验")
            self._reply(scene, channel=channel, error=error, limit=limit)
            return

        if role_of(channel) == ATTACH:
            self._hold_for_pairing(data, channel)
            return

        twoway = channel["flow"] == TWOWAY
        extra_fields = None

        if role_of(channel) == PRIMARY:
            # 先按本通道的映射算一遍，才知道配对键的值是多少
            mapped, problems = map_fields(data, channel.get("mapping"))
            self._waybill = _waybill_of(mapped) or self._waybill
            if problems:
                print_log('；'.join(problems), node=channel["key"], event="❌ 转译失败")
                self._reply("map_failed", channel=channel, error="；".join(problems))
                return
            extra_fields, keep_going = self._collect_pair(channel, mapped, deadline)
            if not keep_going:
                return

        # id 提前生成：自定义报文模板里要用 {{__id}} 把它嵌进前端认识的字段里，
        # 双向还要先按这个 id 开好等待槽——本机前端回得可能比广播这行还快。
        msg_id = new_message_id()
        self._msg_id = msg_id
        payload, problems = build_outbound(channel, data, msg_id,
                                           expect_reply=twoway,
                                           extra_fields=extra_fields)

        if problems:
            # 转译不过就别往前端发半成品，直接告诉设备哪里不对
            print_log('；'.join(problems), node=channel["key"], event="❌ 转译失败")
            self._reply("map_failed", channel=channel, error="；".join(problems))
            return

        event = _open_reply_slot(msg_id) if twoway else None

        # 双向下 ack 的有效期跟着回复超时走，否则 3s 就过期了、界面会误报未确认
        msg_id, sent = broadcast_with_ack(
            payload,
            ack_timeout=channel["timeout"] if twoway else None,
            message_id=msg_id,
            inject_id=False,   # build_outbound 已经把 id 放到该放的位置了
        )
        if sent:
            print_log(f"{sent} 个前端 · {msg_id}", node=channel["key"], event="📤 推送前端")
        else:
            print_log("没有前端连着，数据未转发", node=channel["key"], event="⚠️ 无前端")

        if not self.selftest:
            _record_history(data, sent, msg_id, channel["key"])

        if not twoway:
            self._reply("ok", extra={"forwarded": sent}, channel=channel)
            return

        if not sent:
            # 没人接，等下去也不会有回复，直接告诉设备
            _close_reply_slot(msg_id)
            self._reply("no_client", extra={"forwarded": 0}, channel=channel)
            return

        since = time.monotonic()
        got = event.wait(timeout=max(deadline - time.monotonic(), 0))
        self._phase("前端", since)
        entry = _close_reply_slot(msg_id)

        if not got:
            print_log(f"{channel['timeout']:g}s 内没等到 · {msg_id}", node=channel["key"],
                      event="⏱ 回复超时")
            self._reply("reply_timeout", extra={"forwarded": sent}, channel=channel,
                        timeout=f"{channel['timeout']:g}")
            return

        reply = entry["reply"] if entry else None
        print_log(f"用前端 {entry['from'] if entry else '?'} 的回复答复设备 · {msg_id}",
                  node=channel["key"], event="✅ 采用回复")
        self._reply("ok_reply", extra={"forwarded": sent, "data": reply}, channel=channel)


def parse_addr(addr):
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
        host, port = parse_addr(addr)
        httpd = ThreadingHTTPServer((host, port), _CallbackHandler)
    except Exception as e:
        print_log(f"HTTP 服务: {e}", event="❌ 启动失败", simple=True)
        return

    with _httpd_lock:
        _httpd = httpd

    if ready_event is not None:
        ready_event.set()  # 绑定成功，界面据此确认服务真的起来了
    print_log(f"HTTP 监听 http://{host}:{port}", event="🌐 服务启动", simple=True)
    try:
        httpd.serve_forever(poll_interval=0.2)
    finally:
        httpd.server_close()  # 释放监听端口，否则重启时会提示端口占用
        with _httpd_lock:
            if _httpd is httpd:
                _httpd = None
        print_log("HTTP 服务已停止", event="🛑 服务停止", simple=True)


def stop_http_server():
    with _httpd_lock:
        httpd = _httpd
    if httpd is not None:
        httpd.shutdown()
