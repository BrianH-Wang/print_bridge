"""与界面无关的纯函数：格式化、地址规整、取值兜底。

单独放一个模块，方便直接跑单测，不用起 Qt。
"""

import re

from data_receiver import DEFAULT_REPLY_TIMEOUT
from printer_utils import (
    DEFAULT_PRINTER_KEY, DEFAULT_RAW_PORT, KEY_PATTERN, SYSTEM_PRINTER, make_printer,
)
from ui.constants import (
    DATA_ADDR, DATA_PORT, MAX_PRINTERS, MAX_REPLY_TIMEOUT, MIN_REPLY_TIMEOUT,
)


def human_bytes(size):
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def normalize_addr(text):
    """把用户填的地址整理成 'host:port/path'，非法时返回 None。

    允许省略 http:// 前缀和端口（补 DATA_PORT），路径原样保留 ——
    HTTP 服务对任意路径都接收 POST，路径只影响前端/外部系统怎么写这条链接。
    """
    addr = (text or "").strip()
    for prefix in ("http://", "https://"):
        if addr.lower().startswith(prefix):
            addr = addr[len(prefix):]
            break
    addr = addr.strip().rstrip("/")
    if not addr:
        return None

    hostport, _, path = addr.partition("/")
    host, colon, port_text = hostport.partition(":")
    host = host.strip() or "localhost"
    if not re.fullmatch(r"[A-Za-z0-9._-]+", host):
        return None

    if colon:
        if not port_text.strip().isdigit():
            return None
        port = int(port_text)
        if not 1 <= port <= 65535:
            return None
    else:
        port = DATA_PORT

    normalized = f"{host}:{port}"
    if path:
        normalized += f"/{path.strip()}"
    return normalized


def clamp_port(value):
    """方案里的打印机端口可能是手改过的 json，落回合法区间。"""
    try:
        port = int(value)
    except (TypeError, ValueError):
        return DEFAULT_RAW_PORT
    return port if 1 <= port <= 65535 else DEFAULT_RAW_PORT


def clamp_timeout(value):
    """方案里的超时值可能是手改过的 json，落到合法区间再用。"""
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return DEFAULT_REPLY_TIMEOUT
    return min(max(timeout, MIN_REPLY_TIMEOUT), MAX_REPLY_TIMEOUT)


def sanitize_printers(item):
    """读回方案里的打印机列表；老方案（单打印机字段）自动迁移成一台。"""
    raw = item.get("printers")
    printers, seen = [], set()

    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("key", "")).strip()
            if not re.fullmatch(KEY_PATTERN, key) or key in seen:
                continue
            seen.add(key)
            printers.append(make_printer(
                key=key,
                label=entry.get("label", ""),
                kind=entry.get("kind"),
                queue=entry.get("queue", ""),
                host=entry.get("host", ""),
                port=clamp_port(entry.get("port")),
            ))
        return printers[:MAX_PRINTERS]

    # 旧结构：printer / print_target / printer_host / printer_port 四个字段一台机
    legacy_queue = str(item.get("printer", "")).strip()
    legacy_host = str(item.get("printer_host", "")).strip()
    if not legacy_queue and not legacy_host:
        return []
    return [make_printer(
        key=DEFAULT_PRINTER_KEY,
        kind=item.get("print_target", SYSTEM_PRINTER),
        queue=legacy_queue,
        host=legacy_host,
        port=clamp_port(item.get("printer_port")),
    )]
