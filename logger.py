"""基于队列的日志封装：各工作线程写入队列，GUI 线程定时取出展示。

每条日志是一个 dict，界面按列摆：时间 / 功能 / 节点 / 事件 / 内容。
  功能  按写日志的模块认（接收、前端、打印……），调用处不用改
  节点  消息里的 [通道 key] 自动拆出来；也可以 print_log(..., node=) 显式给
  事件  这一行在干什么（📥 接收、📤 推送前端……）。数据链路上的显式给 event=，
        其余从消息开头的图标和第一个词里拆
  raw   出错时的原始数据（比如解析失败的报文），界面里双击能看

两种视图：
  详细  上面这些全部，排查问题用
  简约  一个设备请求一行（log_request 写的汇总：单号、结果、总耗时），
        外加服务启停、前端连断这类标了 simple 的，和不属于任何请求的错误
同一个请求里写的日志都带同一个 req（begin_request 开、end_request 关），
简约视图里双击一行就能跳到这个请求的全部详细日志。
"""

import itertools
import re
import sys
import threading
from datetime import datetime

_log_queue = None

# 超过 LOG_STR_LIMIT 的字符串在日志中折叠为「头部....尾部」
LOG_STR_LIMIT = 60
LOG_STR_HEAD = 20
LOG_STR_TAIL = 20

# 随日志带的原始数据最多留这么多字符：日志只为排错，整张 base64 图片留着没用还占内存
RAW_LIMIT = 200_000

ERROR = "error"
WARN = "warn"
INFO = "info"

REQUEST = "request"   # 简约视图里一个设备请求的汇总行

# 写日志的模块 → 「功能」列
AREAS = {
    "data_receiver": "接收",
    "pairing": "配对",
    "websocket_handler": "前端",
    "websocket_server": "前端",
    "printer_utils": "打印",
    "ui.printer_list": "打印",
    "ui.dialogs.printer": "打印",
    "ui.channel_list": "通道",
    "ui.profiles": "方案",
    "history_store": "历史",
    "ui.panels.history_panel": "历史",
    "ui.dialogs.history_search": "历史",
    "ui.updates": "更新",
}
DEFAULT_AREA = "系统"

_ERROR_MARKS = ("❌",)
_WARN_MARKS = ("⚠️", "⏱", "⏰")
# 「🧪 📥 [sorter_data] 接收数据」→ 前缀「🧪 📥 」、节点 sorter_data、正文「接收数据」
# 只认紧跟在开头图标后面的 [key]，句子中间的方括号不算
_NODE = re.compile(r"^((?:[^\w\s\[]+\s+){0,2})\[([A-Za-z0-9_.\-]+)\]\s*(.*)$", re.DOTALL)
# 开头的图标（最多两个，🧪 📥 这种）和后面的正文
_ICONS = re.compile(r"^((?:[^\w\s\[]+\s+){0,2})(.*)$", re.DOTALL)
# 没显式给事件时，正文第一个词当事件：切在空格、冒号、「、括号上，太长就不切
_FIRST_WORD = re.compile(r"[\s:：「（(]")
EVENT_MAX_CHARS = 8
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")

_context = threading.local()
_request_ids = itertools.count(1)


def init_logger(log_queue):
    """由 GUI 在启动时调用一次，之后所有线程共享同一个队列。"""
    global _log_queue
    _log_queue = log_queue


def begin_request():
    """当前线程开始处理一个设备请求，之后写的日志都挂在这个请求下。返回请求号。"""
    _context.request = next(_request_ids)
    return _context.request


def end_request():
    _context.request = None


def current_request():
    return getattr(_context, "request", None)


def shorten(value, limit=LOG_STR_LIMIT, head=LOG_STR_HEAD, tail=LOG_STR_TAIL):
    """折叠数据中的超长字符串，避免 base64 图片之类的字段把日志刷屏。

    只用于日志展示，不影响实际转发给前端的数据。dict / list 会递归处理。
    """
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return f"{value[:head]}....{value[-tail:]}[{len(value)}字符]"
    if isinstance(value, dict):
        return {k: shorten(v, limit, head, tail) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [shorten(v, limit, head, tail) for v in value]
    return value


def raw_text(data):
    """原始数据转成能看的字符串：bytes 按 UTF-8 解，解不开的字节用 � 占位。"""
    if isinstance(data, (bytes, bytearray)):
        text = bytes(data).decode("utf-8", errors="replace")
    else:
        text = str(data)
    if len(text) > RAW_LIMIT:
        text = text[:RAW_LIMIT] + f"\n\n……（共 {len(text)} 字符，只保留前 {RAW_LIMIT} 个）"
    return text


def level_of(text):
    text = text.removeprefix("🧪").lstrip()
    if text.startswith(_ERROR_MARKS):
        return ERROR
    if text.startswith(_WARN_MARKS):
        return WARN
    return INFO


def split_event(text):
    """「📂 已载入 3 条历史记录」→（「📂 已载入」,「3 条历史记录」）。

    第一个词太长或没有（比如英文开头的整句），事件就只留图标，正文不动。
    """
    icons, body = _ICONS.match(text).groups()
    icons = icons.strip()
    cut = _FIRST_WORD.search(body)
    head = body[:cut.start()] if cut else body
    # 只认中文开头的词：「Print Bridge」「HTTP 监听」这种切出来的半截英文当不了事件名
    if cut and 2 <= len(head) <= EVENT_MAX_CHARS and _HAS_CJK.search(head):
        return f"{icons} {head}".strip(), body[cut.start():].lstrip(" :：")
    return icons, body


def _base_entry(module, node, level):
    return {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "area": AREAS.get(module, DEFAULT_AREA),
        "node": node or "",
        "req": current_request(),
        "raw": None,
    }


def _attach_raw(entry, raw, error, request, saved=None):
    """出错时的现场：源数据、解析出错的位置、请求行和请求头，落了盘的话还有文件路径。"""
    entry["raw"] = None if raw is None else raw_text(raw)
    if request:
        entry["request"] = request
    if saved:
        entry["saved"] = saved
    if error is not None and hasattr(error, "pos"):
        # json.JSONDecodeError：pos 是字符下标，lineno / colno 从 1 数
        entry["error_at"] = {"pos": error.pos, "line": error.lineno, "col": error.colno}


def make_entry(message, module, node=None, raw=None, error=None, event=None, simple=False,
               request=None, saved=None):
    """拼一条日志。error 是解析异常时，带上出错位置，界面里能直接跳过去。"""
    text = str(message)
    if node is None:
        match = _NODE.match(text)
        if match:
            text = match.group(1) + match.group(3)
            node = match.group(2)
    if event is None:
        level = level_of(text)
        event, text = split_event(text)
    else:
        level = level_of(event)
    entry = _base_entry(module, node, level)
    entry.update(event=event, text=text, simple=simple)
    _attach_raw(entry, raw, error, request, saved)
    return entry


def format_line(entry):
    node = f" [{entry['node']}]" if entry["node"] else ""
    return f"[{entry['time']}] {entry['area']}{node} {entry['event']} {entry['text']}".rstrip()


def _emit(entry):
    if _log_queue is None:
        print(format_line(entry))
    else:
        _log_queue.put(entry)


def print_log(message, node=None, raw=None, error=None, event=None, simple=False, request=None,
              saved=None):
    """写一条日志；未初始化队列时回退到标准输出。

    node    节点（通道 key 等）；不给就从消息开头的 [key] 里拆
    raw     原始数据，出错时留着给人看（bytes / str 都行）
    error   对应的异常；JSON 解析错误会记下出错的行列
    event   事件列，带图标（"📥 接收"）；给了的话 message 只写事件之后的内容
    simple  简约视图里也显示（服务启停、前端连断这类使用者要知道的事）
    request 请求行和请求头（文本），和 raw 一起给人看
    saved   源数据落盘的文件路径
    """
    module = sys._getframe(1).f_globals.get("__name__", "")
    _emit(make_entry(message, module, node, raw, error, event, simple, request, saved))


def elapsed_text(entry):
    """「1309 ms（配对 1005 / 前端 303）」：总耗时，括号里是其中等配对、等前端回复各花的毫秒数。"""
    text = f"{entry['elapsed_ms']:.0f} ms"
    phases = entry.get("phases") or []
    if phases:
        text += "（" + " / ".join(f"{label} {ms:.0f}" for label, ms in phases) + "）"
    return text


def log_request(node, waybill, ok, result, elapsed_ms, note="", msg_id=None, phases=None,
                raw=None, error=None, request=None, saved=None):
    """一个设备请求处理完：简约视图里的那一行。

    result      结果列（"200 接收成功"、"504 等待回复超时"）
    elapsed_ms  从收到请求到把应答写回设备的总耗时
    phases      [(等什么, 毫秒)]：总耗时里花在排队、等配对、等前端回复上的几段
    note        说明列，一般是回给设备的 msg
    raw / error / request / saved  出错时的现场（同 print_log），简约里点「源数据」直接能看
    """
    module = sys._getframe(1).f_globals.get("__name__", "")
    entry = _base_entry(module, node, INFO if ok else ERROR)
    entry.update(
        kind=REQUEST, event="✅" if ok else "❌", waybill=waybill or "", result=result,
        elapsed_ms=elapsed_ms, phases=list(phases or []), msg_id=msg_id or "", simple=True,
        note=note,
    )
    entry["text"] = f"{waybill or '-'} · {result} · {elapsed_text(entry)}" + (f" · {note}" if note else "")
    _attach_raw(entry, raw, error, request, saved)
    _emit(entry)
