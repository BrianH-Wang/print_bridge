"""配对合流：两条链接的数据按一个业务号合成一条再发给前端。

典型场景是分拣设备：图片走 /upload、数值走 /callback，两条各发各的，
靠同一个条码配对。前端要的是"一条带图的记录"，不是两条对不上的消息。

不对称是关键：
  · 附加通道（图片）是单向的，收下存进暂存区、立刻回 200 就完事，
    不必挂起——所以没有"先到的那条怎么应答"这个难题。
  · 主通道（数值）才需要等：先等附加数据齐，再等前端回复。
    这两段等待**共用一份超时预算**，否则设备实际要等 wait + timeout。

自带锁，不依赖 Qt，可以脱离界面单测。
"""

import threading
import time

from logger import print_log

NONE = "none"        # 不参与配对（默认）
PRIMARY = "primary"  # 主报文：等附加数据齐了再发给前端
ATTACH = "attach"    # 附加数据：收下存着，等主报文来取

SEND_PARTIAL = "send_partial"   # 等不到就照发，缺的字段就没有
ERROR = "error"                 # 等不到就回错给设备，不发

DEFAULT_GROUP = "default"
DEFAULT_WAIT = 1.0     # 和等前端回复共用超时预算，留短点，别把前端的时间吃光
DEFAULT_KEEP = 60.0
DEFAULT_MAX_PENDING = 200
MAX_KEEP = 3600.0


def make_pair(role=NONE, group=DEFAULT_GROUP, field="", value="", rename="",
              wait=DEFAULT_WAIT, keep=DEFAULT_KEEP, on_missing=SEND_PARTIAL,
              max_pending=DEFAULT_MAX_PENDING):
    """一条通道在配对里的角色。

    group   配对组名；同组的通道之间才互相配对
    field   配对键在**转译之后**的字段名 —— 两条通道源字段名不同时，
            各自先在「字段映射」里改成一致，这里就不用再纠结叫什么
    value   附加通道要并进主报文的字段；留空 = 除配对键外全并进去
    rename  并进去之后叫什么；留空 = 保持原名
    wait    主通道最多等附加数据多久
    keep    附加数据在暂存区留多久
    """
    return {
        "role": role if role in (NONE, PRIMARY, ATTACH) else NONE,
        "group": (str(group or DEFAULT_GROUP)).strip() or DEFAULT_GROUP,
        "field": (str(field or "")).strip(),
        "value": (str(value or "")).strip(),
        "rename": (str(rename or "")).strip(),
        "wait": max(0.0, float(wait if wait is not None else DEFAULT_WAIT)),
        "keep": min(max(1.0, float(keep or DEFAULT_KEEP)), MAX_KEEP),
        "on_missing": on_missing if on_missing in (SEND_PARTIAL, ERROR) else SEND_PARTIAL,
        "max_pending": max(1, int(max_pending or DEFAULT_MAX_PENDING)),
    }


def pair_of(channel):
    return (channel or {}).get("pair") or None


def role_of(channel):
    return (pair_of(channel) or {}).get("role", NONE)


def attach_channels(channels, group):
    """同组里所有附加通道，按配置顺序——主报文要等的就是这些。"""
    return [c for c in channels or []
            if role_of(c) == ATTACH and pair_of(c).get("group") == group]


def describe_pair(channel):
    pair = pair_of(channel)
    if not pair or pair["role"] == NONE:
        return "不参与"
    role = "主报文" if pair["role"] == PRIMARY else "附加数据"
    return f"{role}·{pair['group']}·{pair['field'] or '?'}"


# --------------------------------------------------------------------------- #
# 暂存区
# --------------------------------------------------------------------------- #

# {(group, key): {channel_key: {"data": …, "expires_at": …}}}
_buffer = {}
# 一把条件变量管全部：主报文在这上面等，附加数据进来就 notify_all。
# 量级是"一台设备一秒几条"，不值得为每个 key 各建一个 Event。
_cv = threading.Condition()


def _sweep_locked(now=None):
    """清掉过期条目。调用方必须已持有 _cv。"""
    now = now or time.monotonic()
    dropped = 0
    for slot_key in list(_buffer):
        items = _buffer[slot_key]
        for channel_key in list(items):
            if items[channel_key]["expires_at"] <= now:
                del items[channel_key]
                dropped += 1
        if not items:
            del _buffer[slot_key]
    return dropped


def _enforce_limit_locked(max_pending):
    """暂存条数超上限时丢最早过期的那些 —— 只发图不发数值时不至于把内存吃光。"""
    total = sum(len(items) for items in _buffer.values())
    if total <= max_pending:
        return 0

    everything = [
        (entry["expires_at"], slot_key, channel_key)
        for slot_key, items in _buffer.items()
        for channel_key, entry in items.items()
    ]
    everything.sort()
    dropped = 0
    for _, slot_key, channel_key in everything[: total - max_pending]:
        items = _buffer.get(slot_key) or {}
        if items.pop(channel_key, None) is not None:
            dropped += 1
        if not items:
            _buffer.pop(slot_key, None)
    return dropped


def store_attach(group, key, channel_key, data, keep=DEFAULT_KEEP,
                 max_pending=DEFAULT_MAX_PENDING):
    """存一条附加数据，并叫醒正在等它的主报文。返回暂存区当前条数。"""
    with _cv:
        _sweep_locked()
        slot = _buffer.setdefault((group, key), {})
        # 同一个配对键重复到达：后到的覆盖先到的。设备重发、网络重试都会造成，
        # 保留新的比保留旧的合理。
        slot[channel_key] = {"data": data, "expires_at": time.monotonic() + keep}
        dropped = _enforce_limit_locked(max_pending)
        _cv.notify_all()
        total = sum(len(items) for items in _buffer.values())

    if dropped:
        print_log(f"⚠️ 配对暂存区超过 {max_pending} 条，已丢弃最旧的 {dropped} 条")
    return total


def take_attaches(group, key, wanted, deadline):
    """等齐 wanted 里的附加数据，然后取走。返回 (已拿到的 dict, 还缺的列表)。

    取走而不是留着：一条附加数据只配一次，留着会让下一个同号的主报文
    配到一份陈年旧图。
    """
    wanted = list(wanted or [])
    if not wanted:
        return {}, []

    with _cv:
        while True:
            _sweep_locked()
            slot = _buffer.get((group, key)) or {}
            missing = [k for k in wanted if k not in slot]
            if not missing:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            _cv.wait(remaining)

        slot = _buffer.get((group, key)) or {}
        found = {k: slot.pop(k)["data"] for k in wanted if k in slot}
        if not slot:
            _buffer.pop((group, key), None)

    return found, [k for k in wanted if k not in found]


def merge(primary_data, attaches, specs):
    """把附加数据并进主报文。

    specs: {通道 key: 那条通道的 pair 配置}，决定并哪个字段、并进去叫什么。
    返回要追加到主报文上的字段（不改原 dict）。
    """
    extra = {}
    for channel_key, data in (attaches or {}).items():
        spec = (specs or {}).get(channel_key) or {}
        if not isinstance(data, dict):
            # 附加数据不是对象时只能整份塞进去，名字取 rename，没有就用通道 key
            extra[spec.get("rename") or channel_key] = data
            continue

        field = spec.get("value")
        if field:
            if field in data:
                extra[spec.get("rename") or field] = data[field]
            continue

        # 没指定字段：除配对键外全并进来。配对键两边本来就一样，
        # 再并一次只会覆盖主报文里同名的那个，没意义。
        pair_field = spec.get("field")
        for name, value in data.items():
            if name != pair_field:
                extra[name] = value
    return extra


def expire_pairs():
    """定时清理入口：界面那边的轮询顺手叫一次。返回清掉几条。"""
    with _cv:
        return _sweep_locked()


def pending_count():
    with _cv:
        return sum(len(items) for items in _buffer.values())


def clear_pairs():
    """通道配置变了就清空：旧的暂存数据对不上新的组和字段。"""
    with _cv:
        _buffer.clear()
        _cv.notify_all()
