"""与界面无关的纯函数：格式化、地址规整、取值兜底。

单独放一个模块，方便直接跑单测，不用起 Qt。
"""

import re

from channels import (
    ANY_PATH, CONCURRENT, DEFAULT_CHANNEL_KEY, DEFAULT_REPLY_TIMEOUT, MAX_CHANNELS,
    ONEWAY, SERIAL, TWOWAY, make_channel, normalize_path,
)
from channels import KEY_PATTERN as CHANNEL_KEY_PATTERN
from mapping import (
    DEFAULT_MESSAGE_TYPE, PASSTHROUGH, STANDARD, make_mapping, make_outbound,
)
from pairing import NONE as PAIR_NONE
from pairing import make_pair
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


def sanitize_mapping(raw):
    """洗一遍字段映射；透传（等于没配）时返回 None，不往方案里存空壳。"""
    if not isinstance(raw, dict):
        return None
    clean = make_mapping(raw.get("mode"), raw.get("fields"), raw.get("on_missing"))
    # 两端字段名都空的行是用户加了没填，直接丢掉
    clean["fields"] = [f for f in clean["fields"] if f["source"] and f["target"]]
    if clean["mode"] == PASSTHROUGH or not clean["fields"]:
        return None
    return clean


def sanitize_outbound(raw):
    """洗一遍出向报文设置；等于默认行为时返回 None。"""
    if not isinstance(raw, dict):
        return None
    clean = make_outbound(raw.get("envelope"), raw.get("type"),
                          raw.get("extra"), raw.get("template"))
    is_default = (clean["envelope"] == STANDARD
                  and clean["type"] == DEFAULT_MESSAGE_TYPE
                  and not clean["extra"])
    return None if is_default else clean


def sanitize_pair(raw):
    """洗一遍配对设置；不参与配对（等于没配）时返回 None。"""
    if not isinstance(raw, dict):
        return None
    clean = make_pair(
        raw.get("role"), raw.get("group"), raw.get("field"), raw.get("value"),
        raw.get("rename"), raw.get("wait"), raw.get("keep"),
        raw.get("on_missing"), raw.get("max_pending"),
    )
    # 没有配对字段就配不了对，当作没开
    if clean["role"] == PAIR_NONE or not clean["field"]:
        return None
    return clean


def sanitize_channels(item):
    """读回方案里的接收通道；老方案（单份数据流设置）自动迁移成一条兜底通道。"""
    raw = item.get("channels")
    channels, seen_keys, seen_paths = [], set(), set()

    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("key", "")).strip()
            if not re.fullmatch(CHANNEL_KEY_PATTERN, key) or key in seen_keys:
                continue
            path = normalize_path(entry.get("path"))
            if path in seen_paths:
                continue   # 路径撞车时留先出现的那条，后面的按没配过处理
            seen_keys.add(key)
            seen_paths.add(path)
            channels.append(make_channel(
                key=key,
                label=entry.get("label", ""),
                path=path,
                flow=entry.get("flow"),
                timeout=clamp_timeout(entry.get("timeout")),
                dispatch=entry.get("dispatch"),
                envelope=entry.get("envelope"),
                http_status=entry.get("http_status"),
                responses=entry.get("responses"),
                mapping=sanitize_mapping(entry.get("mapping")),
                outbound=sanitize_outbound(entry.get("outbound")),
                sample=entry.get("sample"),
                pair=sanitize_pair(entry.get("pair")),
            ))
        if channels:
            return channels[:MAX_CHANNELS]

    # 老结构：flow_mode / reply_timeout / dispatch_mode 是一份全局设置。
    # 迁移成路径为 * 的兜底通道 —— 以前任何路径都能 POST，这样升级前后行为不变。
    return [make_channel(
        key=DEFAULT_CHANNEL_KEY,
        label="默认",
        path=ANY_PATH,
        flow=item.get("flow_mode") if item.get("flow_mode") in (ONEWAY, TWOWAY) else ONEWAY,
        timeout=clamp_timeout(item.get("reply_timeout")),
        dispatch=(item.get("dispatch_mode")
                  if item.get("dispatch_mode") in (CONCURRENT, SERIAL) else CONCURRENT),
    )]
