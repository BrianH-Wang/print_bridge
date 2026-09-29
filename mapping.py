"""字段转译与出向报文组装：设备的字段名 → 前端要的字段名。

中间件的活儿：设备接口写死了叫 code，前端接口写死了叫 expressNo，
两边都改不动，就由这里翻译。和 channels.py 一样是纯函数，不依赖 Qt。

不提供脚本框（Python / JS 代码块）——方案是会被导出、复制到别的门店、
发在群里的，里面存一段可执行代码等于谁拿到配置谁就能在这台连着打印机的
电脑上执行任意代码。覆盖不了的需求就往 TRANSFORMS 里加算子。
"""

import base64
import re
from datetime import datetime

from channels import render_template

# 取不到值和"取到了 None"是两回事：前者该走必填/默认值策略，后者是合法值
MISSING = object()

PASSTHROUGH = "passthrough"   # 整个 body 原样进 data（默认，等于不配置）
PICK = "pick"                 # 只保留映射到的字段
MERGE = "merge"               # 原字段全留，再追加映射出来的

ON_ERROR = "error"     # 必填缺失就回错，不往前端发
ON_SKIP = "skip"       # 缺了就不要这个字段
ON_DEFAULT = "default"  # 缺了用默认值

STANDARD = "standard"   # {"type":…,"id":…,"channel":…,"data":…}，今天的形状
CUSTOM = "custom"       # 整条报文由模板说了算

DEFAULT_MESSAGE_TYPE = "external_data"


# --------------------------------------------------------------------------- #
# 路径取值
# --------------------------------------------------------------------------- #

_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def get_path(data, path):
    """按 'a.b[0].c' 取值；路径上任何一环不存在都返回 MISSING。"""
    current = data
    for name, index in _TOKEN.findall(str(path or "")):
        if name:
            if not isinstance(current, dict) or name not in current:
                return MISSING
            current = current[name]
        else:
            if not isinstance(current, (list, tuple)):
                return MISSING
            position = int(index)
            if position >= len(current):
                return MISSING
            current = current[position]
    return current


def flatten_keys(data, prefix="", depth=3):
    """把样例摊平成可选字段清单，界面上给用户点选，不用他自己写路径。

    只下钻 depth 层：再深的嵌套让用户手写路径，免得一份大 JSON 摊出几百行。
    """
    found = []
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, (dict, list)) and depth > 1:
                found += flatten_keys(value, path, depth - 1)
            else:
                found.append(path)
    elif isinstance(data, list) and data and depth > 1:
        found += flatten_keys(data[0], f"{prefix}[0]", depth - 1)
    return found


# --------------------------------------------------------------------------- #
# 转换算子
# --------------------------------------------------------------------------- #

def _to_number(value):
    if isinstance(value, bool):
        return int(value)
    number = float(value)
    # 12.0 这种回成 12：设备发的整数别在中间变成小数
    return int(number) if number.is_integer() else number


def _date_from(value, unit):
    seconds = float(value) / (1000.0 if unit == "ms" else 1.0)
    return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")


# 名字 → (中文说明, 实现)。界面上的下拉直接照这份清单生成。
TRANSFORMS = {
    "trim":          ("去掉首尾空格", lambda v, o: str(v).strip()),
    "upper":         ("转大写", lambda v, o: str(v).upper()),
    "lower":         ("转小写", lambda v, o: str(v).lower()),
    "number":        ("转数字", lambda v, o: _to_number(v)),
    "string":        ("转文本", lambda v, o: str(v)),
    "bool":          ("转布尔", lambda v, o: bool(v) and str(v).lower() not in ("0", "false", "")),
    "base64_decode": ("base64 解码成文本", lambda v, o: base64.b64decode(str(v)).decode("utf-8")),
    "date_ms":       ("毫秒时间戳 → 日期时间", lambda v, o: _date_from(v, "ms")),
    "date_s":        ("秒时间戳 → 日期时间", lambda v, o: _date_from(v, "s")),
    "prefix":        ("加前缀", lambda v, o: f"{o.get('value', '')}{v}"),
    "suffix":        ("加后缀", lambda v, o: f"{v}{o.get('value', '')}"),
    "replace":       ("替换文本", lambda v, o: str(v).replace(o.get("from", ""), o.get("to", ""))),
    "lookup":        ("查表替换", lambda v, o: (o.get("table") or {}).get(
                          str(v), o.get("default", v))),
}

# 界面下拉里露出来的常用算子；其余的（prefix/suffix/replace/lookup 带参数）
# 走手改 json，属于高级用法
COMMON_TRANSFORMS = [
    ("（不转换）", ""),
    ("去掉首尾空格", "trim"),
    ("转大写", "upper"),
    ("转小写", "lower"),
    ("转数字", "number"),
    ("转文本", "string"),
    ("base64 解码成文本", "base64_decode"),
    ("毫秒时间戳 → 日期时间", "date_ms"),
    ("秒时间戳 → 日期时间", "date_s"),
]


def apply_transforms(value, transforms):
    """依次套用算子。返回 (值, 出错说明)；出错时值原样返回。"""
    for step in transforms or []:
        if isinstance(step, str):
            name, options = step, {}
        elif isinstance(step, dict):
            name, options = step.get("op", ""), step
        else:
            continue
        entry = TRANSFORMS.get(name)
        if entry is None:
            return value, f"不认识的转换：{name}"
        try:
            value = entry[1](value, options)
        except Exception as e:
            return value, f"{entry[0]}失败：{e}"
    return value, None


# --------------------------------------------------------------------------- #
# 字段映射
# --------------------------------------------------------------------------- #

def make_field(source="", target="", transforms=None, required=False, default="",
               check=None):
    """一条字段映射：从 source 取值，改名成 target，中间过一遍 transforms。

    check  可选的内容校验，目前只有文件一种：
           {"type": "file", "max_bytes": 2097152, "formats": ["jpg", "png", "pdf"]}
           校验在转译之前跑，不过就按 file_too_large / file_invalid 场景回设备。
    """
    return {
        "source": str(source or "").strip(),
        "target": str(target or "").strip() or str(source or "").strip(),
        "transforms": [t for t in (transforms or []) if t],
        "required": bool(required),
        "default": default,
        "check": make_check(check),
    }


FILE_CHECK = "file"
FILE_FORMATS = ("jpg", "png", "gif", "bmp", "webp", "pdf")   # sniff 认得的类型


def make_check(raw):
    """规整一条内容校验；不是认识的形状就当没配。"""
    if not isinstance(raw, dict) or raw.get("type") != FILE_CHECK:
        return None
    try:
        max_bytes = int(raw.get("max_bytes") or 0)
    except (TypeError, ValueError):
        max_bytes = 0
    formats = [str(f).lower().lstrip(".") for f in (raw.get("formats") or []) if f]
    formats = ["jpg" if f == "jpeg" else f for f in formats]
    return {"type": FILE_CHECK, "max_bytes": max(0, max_bytes),
            "formats": [f for f in formats if f in FILE_FORMATS]}


def check_files(data, mapping):
    """跑一遍字段上的文件校验。返回 (场景 key, 说明, 上限字节数)；全过返回 None。

    设备文档通常把「文件过大」和「文件解析失败」分成两个错误码，
    所以这里也分两个场景：file_too_large 和 file_invalid。
    字段缺失、为空也算解析失败——文档里「file 为空」就归 415 那一档。
    """
    from history_store import sniff   # 纯函数，按文件头认类型；放这里 import 避免启动时绕一圈

    for field in (mapping or {}).get("fields") or []:
        check = field.get("check")
        if not check or check.get("type") != FILE_CHECK:
            continue
        name = field.get("source", "")
        value = get_path(data, name) if isinstance(data, dict) else MISSING
        if value is MISSING or value in (None, ""):
            return "file_invalid", f"{name} 为空", check["max_bytes"]
        if not isinstance(value, str):
            return "file_invalid", f"{name} 不是 base64 字符串", check["max_bytes"]
        text = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
        try:
            raw = base64.b64decode(text, validate=True)
        except (ValueError, TypeError):
            return "file_invalid", f"{name} 不是合法的 base64", check["max_bytes"]
        if not raw:
            return "file_invalid", f"{name} 为空", check["max_bytes"]
        if check["max_bytes"] and len(raw) > check["max_bytes"]:
            return "file_too_large", f"{name} 解码后 {len(raw)} 字节", check["max_bytes"]
        if check["formats"]:
            _, ext = sniff(raw)
            if ext not in check["formats"]:
                return ("file_invalid",
                        f"{name} 的格式不是 {'/'.join(check['formats'])}（识别为 {ext or '未知'}）",
                        check["max_bytes"])
    return None


def make_mapping(mode=PASSTHROUGH, fields=None, on_missing=ON_ERROR):
    return {
        "mode": mode if mode in (PASSTHROUGH, PICK, MERGE) else PASSTHROUGH,
        "on_missing": on_missing if on_missing in (ON_ERROR, ON_SKIP, ON_DEFAULT) else ON_ERROR,
        "fields": [make_field(**f) if not isinstance(f, dict) else make_field(
            f.get("source"), f.get("target"), f.get("transforms"),
            f.get("required"), f.get("default"), f.get("check"),
        ) for f in (fields or []) if f],
    }


def map_fields(data, mapping):
    """按配置把设备的数据转成前端要的形状。

    返回 (结果, 问题清单)。问题非空时调用方按 on_missing 决定是回错还是照发——
    这里只负责算，不负责决定怎么处置。
    """
    mapping = mapping or {}
    mode = mapping.get("mode", PASSTHROUGH)
    fields = mapping.get("fields") or []

    if mode == PASSTHROUGH or not fields:
        return data, []

    if not isinstance(data, dict):
        return data, ["数据不是 JSON 对象，无法按字段映射"]

    result = dict(data) if mode == MERGE else {}
    problems = []
    on_missing = mapping.get("on_missing", ON_ERROR)

    for field in fields:
        source, target = field.get("source"), field.get("target")
        if not source or not target:
            continue

        value = get_path(data, source)
        if value is MISSING:
            default = field.get("default", "")
            if field.get("required") and on_missing == ON_ERROR:
                problems.append(f"缺少必填字段 {source}")
                continue
            # 没配默认值就整个跳过这个字段：硬塞一个空串进去，后面「转数字」必然报错，
            # 设备少发一个可选字段就会连累整条数据被拒
            if on_missing == ON_SKIP or default in ("", None):
                continue
            value = default

        value, error = apply_transforms(value, field.get("transforms"))
        if error:
            problems.append(f"{source}：{error}")
            continue
        result[target] = value

        # merge 模式下改了名就把原名去掉，不然前端会同时看到 code 和 expressNo，
        # 分不清该认哪个
        if mode == MERGE and source != target and "." not in source and "[" not in source:
            result.pop(source, None)

    return result, problems


# --------------------------------------------------------------------------- #
# 出向报文
# --------------------------------------------------------------------------- #

# 模板里除了映射后的字段，还能用这几个内置变量
OUTBOUND_VARIABLES = {
    "__id":      "字符串，本条消息的 id；双向模式下前端要原样带回",
    "__channel": "字符串，命中的通道 key",
    "__label":   "字符串，通道的中文备注",
    "__path":    "字符串，设备 POST 的路径",
    "__data":    "对象，映射之后的完整数据",
}


def make_outbound(envelope=STANDARD, message_type=DEFAULT_MESSAGE_TYPE,
                  extra=None, template=None):
    """发给前端的报文怎么组装。默认值等于今天的行为。"""
    return {
        "envelope": envelope if envelope in (STANDARD, CUSTOM) else STANDARD,
        "type": str(message_type or DEFAULT_MESSAGE_TYPE).strip() or DEFAULT_MESSAGE_TYPE,
        "extra": extra if isinstance(extra, dict) else {},
        "template": template if isinstance(template, dict) and template else None,
    }


def build_outbound(channel, data, message_id, expect_reply=False, extra_fields=None):
    """组装推给前端的整条报文。返回 (报文, 问题清单)。

    extra_fields: 配对合流来的字段，转译之后、组装之前并进去 ——
    这样模板里写 {{imageBase64}} 也能取到从另一条通道并过来的值。
    """
    channel = channel or {}
    mapped, problems = map_fields(data, channel.get("mapping"))
    if extra_fields and isinstance(mapped, dict):
        mapped = {**mapped, **extra_fields}
    outbound = channel.get("outbound") or make_outbound()

    variables = {
        "__id": message_id,
        "__channel": channel.get("key", ""),
        "__label": channel.get("label", ""),
        "__path": channel.get("path", ""),
        "__data": mapped,
    }
    if isinstance(mapped, dict):
        # 映射后的字段直接当变量用：模板里写 {{expressNo}} 就取到它
        variables = {**mapped, **variables}

    if outbound.get("envelope") == CUSTOM and outbound.get("template"):
        payload = render_template(outbound["template"], variables)
        if not isinstance(payload, dict):
            payload = {"data": payload}
        return payload, problems

    # 标准形状里 id 是固定的一格；自定义模板下 id 藏在哪由 {{__id}} 决定，
    # 就不能再额外塞一个 id 进去——那会多出一个前端接口里没有的字段
    payload = {
        "type": outbound.get("type") or DEFAULT_MESSAGE_TYPE,
        "id": message_id,
        "channel": channel.get("key", ""),
        "data": mapped,
    }
    if channel.get("label"):
        payload["channelLabel"] = channel["label"]
    if expect_reply:
        payload["expectReply"] = True
    payload.update(outbound.get("extra") or {})
    return payload, problems


def validate_outbound(outbound, known_fields=()):
    """保存前校验出向报文设置，返回问题清单（空 = 没问题）。"""
    problems = []
    if (outbound or {}).get("envelope") != CUSTOM:
        return problems

    template = (outbound or {}).get("template")
    if not isinstance(template, dict) or not template:
        return ["自定义报文的模板是空的，留空请改回「标准」"]

    used = set()

    def walk(node, where):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{where}.{key}" if where else key)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{where}[{i}]")
        elif isinstance(node, str):
            for name in re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", node):
                used.add(name)
                if name.startswith("__") and name not in OUTBOUND_VARIABLES:
                    problems.append(f"{where}: 没有 {{{{{name}}}}} 这个内置变量")
                elif known_fields and not name.startswith("__") and name not in known_fields:
                    problems.append(f"{where}: 映射结果里没有 {name} 这个字段")

    walk(template, "")

    # id 丢了就再也对不上号：双向模式收不到回复，界面上的送达确认也会一直显示未确认
    if "__id" not in used:
        problems.append("模板里必须有 {{__id}}，否则前端的回复和 ack 都对不上号")
    return problems
