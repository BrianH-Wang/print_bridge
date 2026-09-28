"""接收通道：一条链接的完整描述，以及它的路由和应答外壳。

和 printer_utils 一样是纯数据 + 纯函数，不依赖 Qt、不依赖运行中的服务，
可以直接单测。data_receiver 在运行时按 path 挑出通道，照它的设置处理这一条请求。

为什么单独一个模块：ONEWAY/TWOWAY 这些常量原本在 data_receiver 里，
但 data_receiver 要 import 本模块来路由，反过来本模块又要用那些常量，
会绕成环。常量下沉到这里，data_receiver 再 re-export 出去，老的
`from data_receiver import TWOWAY` 全部照常能用。
"""

import re
from urllib.parse import unquote

MAX_BODY_BYTES = 16 * 1024 * 1024

ONEWAY = "oneway"       # 设备 → 本程序 → 前端，收下就回 200
TWOWAY = "twoway"       # 设备 → 本程序 → 前端 → 本程序 → 设备，要等前端回一条再应答
CONCURRENT = "concurrent"   # 多条请求同时处理，2 不必等 1 回来
SERIAL = "serial"           # 单发：一条处理完（拿到回复或超时）才轮到下一条

DEFAULT_REPLY_TIMEOUT = 10.0

DEFAULT_CHANNEL_KEY = "default"
KEY_PATTERN = r"[a-z][a-z0-9_-]{0,19}"   # 和打印机 key 一个规矩，好打好记
MAX_CHANNELS = 12

# 兜底通道的路径写成 *：任何没被别的通道认领的路径都归它。
# 老方案迁移过来就是这一条，所以升级前后「任意路径都能 POST」的行为不变。
ANY_PATH = "*"

# HTTP 状态码策略
FOLLOW_CODE = "follow_code"   # HTTP 状态码 = 业务 code（今天的行为）
FIXED_200 = "fixed_200"       # 永远回 HTTP 200，业务 code 只放在 body 里
                              # ——有些设备把非 200 一律当网络故障，压根不看 body

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
    "no_route":      {"code": 404, "message": "没有匹配该路径的通道：{path}"},
    "map_failed":    {"code": 400, "message": "字段转译失败：{error}"},
    "pair_timeout":  {"code": 504, "message": "等不到配对数据（{missing}），超时 {timeout}s"},
    "file_too_large": {"code": 413, "message": "文件过大，解码后不能超过 {limit} 字节"},
    "file_invalid":   {"code": 415, "message": "文件解析失败：{error}"},
}

# (场景 key, 场景名, 什么时候会返回, 可用占位符)，界面上照这个顺序列出来
RESPONSE_LABELS = [
    ("ok",            "接收成功（单向）",   "单向模式下转发给前端后立即返回", ""),
    ("ok_reply",      "接收成功（双向）",   "双向模式下拿到前端回复后返回，body 的 data 里是回复内容", ""),
    ("bad_json",      "请求体不是合法 JSON", "解析 POST 过来的 body 失败", "{error}"),
    ("too_large",     "请求体过大",         f"body 超过 {MAX_BODY_BYTES} 字节", "{limit}"),
    ("no_client",     "没有前端连接",       "双向模式下一个 WebSocket 客户端都没连着", ""),
    ("queue_timeout", "排队超时",           "单发模式下排到超时都没轮到", "{timeout}"),
    ("reply_timeout", "等待回复超时",       "双向模式下前端在超时时间内没回 external_reply", "{timeout}"),
    ("no_route",      "路径没有对应通道",   "POST 的路径没有匹配的通道，也没有兜底通道", "{path}"),
    ("map_failed",    "字段转译失败",       "必填字段缺失或转换算子出错，数据没有转发给前端", "{error}"),
    ("pair_timeout",  "配对超时",           "主报文在等待时间内没等到附加数据，且设了「回错误」", "{missing} {timeout}"),
    ("file_too_large", "文件过大",          "字段映射里配了文件校验，base64 解码后超过上限", "{limit}"),
    ("file_invalid",   "文件解析失败",      "配了文件校验的字段为空、不是合法 base64、或格式不在允许范围", "{error}"),
]


# --------------------------------------------------------------------------- #
# 通道模型
# --------------------------------------------------------------------------- #

def normalize_path(path):
    """把用户填的路径整理成 '/callback' 这样；'*' 原样保留。

    去掉 query、补上开头的 /、去掉结尾的 /（根路径除外）。
    用户填 'callback'、'/callback/'、'/callback?x=1' 都归一到同一条。

    还要解百分号编码：路径里有中文时设备发过来的是 /%E5%88%86%E6%8B%A3，
    而用户在界面上填的是 /分拣，不解码就永远匹配不上。
    """
    text = str(path or "").strip()
    if text == ANY_PATH:
        return ANY_PATH
    text = text.split("?", 1)[0].split("#", 1)[0].strip()
    text = unquote(text)
    if not text:
        return ANY_PATH
    if not text.startswith("/"):
        text = "/" + text
    if len(text) > 1:
        text = text.rstrip("/") or "/"
    return text


def make_channel(key=DEFAULT_CHANNEL_KEY, label="", path=ANY_PATH, flow=ONEWAY,
                 timeout=DEFAULT_REPLY_TIMEOUT, dispatch=CONCURRENT,
                 envelope=None, http_status=FOLLOW_CODE, responses=None,
                 mapping=None, outbound=None, sample=None, pair=None,
                 reply_sample=None, reply_raw=False):
    """一条接收通道的完整描述。

    key       英文标识，日志和历史里用它区分是哪条链接来的
    label     中文备注，只给人看
    path      匹配的路径；ANY_PATH 表示兜底
    envelope  回给设备的 body 模板；None = 用内置形状（也就是今天的行为）
    reply_raw 双向拿到前端回复时，body 就是前端回的那份内容本身、不套任何外壳；
              其余场景（单向、没人接、超时、报错）照旧走 envelope / 内置形状
    responses 只放「和全局表不一样」的那几个场景，其余自动落回全局
    mapping   发给前端前的字段转译；None = 原样透传
    outbound  发给前端的报文形状；None = 标准形状
    sample    设备发来的一条样例，只用于界面上的实时预览，不参与运行时
    pair      在配对合流里的角色；None = 不参与（见 pairing.py）
    reply_sample  前端会回的一条样例（双向才有意义），自测时内置假前端照它回；
                  跟着方案导出，换台机器也能一键自测

    mapping / outbound 这里只原样收着不做规整：规整逻辑在 mapping.py，
    而 mapping.py 要 import 本模块的 render_template，反过来 import 会成环。
    读方案时由 ui.helpers.sanitize_channels 洗一遍，运行时 mapping.py
    自己对缺项做兜底。
    """
    return {
        "key": (str(key or DEFAULT_CHANNEL_KEY)).strip(),
        "label": (str(label or "")).strip(),
        "path": normalize_path(path),
        "flow": flow if flow in (ONEWAY, TWOWAY) else ONEWAY,
        "timeout": float(timeout or DEFAULT_REPLY_TIMEOUT),
        "dispatch": dispatch if dispatch in (CONCURRENT, SERIAL) else CONCURRENT,
        "envelope": envelope if isinstance(envelope, dict) and envelope else None,
        "http_status": http_status if http_status in (FOLLOW_CODE, FIXED_200) else FOLLOW_CODE,
        "responses": sanitize_responses(responses),
        "mapping": mapping if isinstance(mapping, dict) and mapping else None,
        "outbound": outbound if isinstance(outbound, dict) and outbound else None,
        "sample": sample if isinstance(sample, dict) and sample else None,
        "pair": pair if isinstance(pair, dict) and pair else None,
        "reply_sample": reply_sample if isinstance(reply_sample, dict) and reply_sample else None,
        "reply_raw": bool(reply_raw),
    }


def describe_channel(channel):
    label = f" {channel['label']}" if channel.get("label") else ""
    where = "任意路径" if channel["path"] == ANY_PATH else channel["path"]
    how = "双向" if channel["flow"] == TWOWAY else "单向"
    return f"{channel['key']}{label} ← {where}（{how}）"


def sanitize_responses(mapping):
    """洗一遍应答覆盖表：认识的场景、码在 100-599 之间的才留下。"""
    clean = {}
    for key, entry in (mapping or {}).items():
        if key not in RESPONSE_DEFAULTS or not isinstance(entry, dict):
            continue
        item = {}
        if "code" in entry:
            try:
                code = int(entry["code"])
            except (TypeError, ValueError):
                continue
            if not 100 <= code <= 599:
                continue
            item["code"] = code
        if "message" in entry:
            item["message"] = str(entry["message"])
        if item:
            clean[key] = item
    return clean


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #

def route(channels, path):
    """按路径挑通道：先找精确匹配，再退到兜底通道，都没有返回 None。

    路径不区分大小写 —— 用户在设备上填 /Callback、在这里填 /callback
    是同一个意思，为这点差别回 404 只会浪费两边的时间。
    """
    wanted = normalize_path(path)
    fallback = None
    for channel in channels or []:
        if channel["path"] == ANY_PATH:
            fallback = fallback or channel
        elif channel["path"].lower() == wanted.lower():
            return channel
    return fallback


def duplicate_paths(channels):
    """找出重复的路径（含多条兜底），给界面做保存前校验。"""
    seen, dupes = set(), []
    for channel in channels or []:
        path = channel["path"]
        if path in seen:
            dupes.append(path)
        seen.add(path)
    return dupes


# --------------------------------------------------------------------------- #
# 应答外壳
# --------------------------------------------------------------------------- #

# 模板里能用的内置变量，界面上要照着这份清单给提示
ENVELOPE_VARIABLES = {
    "__success":   "布尔值，业务是否成功（code < 400）",
    "__code":      "数字，业务状态码",
    "__message":   "字符串，提示信息",
    "__data":      "对象，双向模式下前端回的内容；没有时为 null",
    "__forwarded": "数字，这条数据推送给了几个前端",
    "__scene":     "字符串，命中的场景 key，如 ok_reply",
    "__channel":   "字符串，命中的通道 key",
}

# 常见的应答外壳，界面上给个下拉直接套用 —— 对接方照着设备文档挑一个，
# 比对着空白模板从头写靠谱得多。
ENVELOPE_PRESETS = [
    ("success / code / msg / data（几何智造等常见形状）", {
        "success": "{{__success}}",
        "code": "{{__code}}",
        "msg": "{{__message}}",
        "data": "{{__data}}",
    }),
    ("code / message / result", {
        "code": "{{__code}}",
        "message": "{{__message}}",
        "result": "{{__data}}",
    }),
    ("扁平：只回状态和说明", {
        "code": "{{__code}}",
        "msg": "{{__message}}",
    }),
]

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _render_string(text, variables):
    """整串就是一个占位符时按原类型替换，否则当字符串拼接。

    这个区别很要紧：`"code": "{{__code}}"` 要出 200 这个数字，
    出成 "200" 字符串的话，按文档做严格类型校验的设备会直接判失败。
    """
    whole = _PLACEHOLDER.fullmatch(text.strip())
    if whole:
        name = whole.group(1)
        return variables[name] if name in variables else text

    def substitute(match):
        name = match.group(1)
        if name not in variables:
            return match.group(0)   # 不认识的占位符原样留着，方便看出是哪里写错了
        value = variables[name]
        return "" if value is None else str(value)

    return _PLACEHOLDER.sub(substitute, text)


def render_template(template, variables):
    """按变量表渲染模板，dict / list 递归进去。"""
    if isinstance(template, dict):
        return {key: render_template(value, variables) for key, value in template.items()}
    if isinstance(template, list):
        return [render_template(value, variables) for value in template]
    if isinstance(template, str):
        return _render_string(template, variables)
    return template


def legacy_body(code, message, extra):
    """内置的应答形状，也就是没配 envelope 时的行为。

    成功用 message、失败用 error 这个键名之差是历史遗留：
    模板表达不了「键名随状态码变」，所以这条单独留在代码里，
    正好也让「不配 = 和今天完全一样」这条铁律站得住。
    """
    body = {"success": code < 400, "code": code}
    body["message" if code < 400 else "error"] = message
    body.update(extra or {})
    return body


def build_body(channel, scene, code, message, extra=None):
    """组装回给设备的 body。

    优先级：reply_raw 且真拿到了前端回复 → 原样回前端那份；
    否则通道配了 envelope 就走模板，再否则用内置形状。
    """
    extra = extra or {}
    channel = channel or {}
    if channel.get("reply_raw") and scene == "ok_reply" and extra.get("data") is not None:
        return extra["data"]

    envelope = channel.get("envelope")
    if not envelope:
        return legacy_body(code, message, extra)

    variables = {
        "__success": code < 400,
        "__code": code,
        "__message": message,
        "__data": extra.get("data"),
        "__forwarded": extra.get("forwarded", 0),
        "__scene": scene,
        "__channel": (channel or {}).get("key", ""),
    }
    return render_template(envelope, variables)


def resolve_response(key, channel=None, overrides=None, **fmt):
    """取某个场景的 (状态码, 文案)。

    三层叠加：通道自己的覆盖 > 全局应答表（overrides）> 出厂默认。
    占位符缺参数时原样返回，不让打印链路因为文案报错。
    运行时由 data_receiver 传入它保存的全局表；自测算预期值时传配置文件里那份。
    """
    entry = dict(RESPONSE_DEFAULTS[key])
    entry.update((overrides or {}).get(key) or {})
    entry.update(((channel or {}).get("responses") or {}).get(key) or {})

    try:
        message = entry["message"].format(**fmt)
    except (KeyError, IndexError, ValueError):
        message = entry["message"]
    return int(entry["code"]), message


def http_status_for(channel, code):
    """真正写进 HTTP 状态行的码。"""
    if (channel or {}).get("http_status") == FIXED_200:
        return 200
    return code


def validate_envelope(template):
    """保存前校验应答外壳模板，返回问题清单（空 = 没问题）。"""
    problems = []
    if not isinstance(template, dict):
        return ["最外层必须是一个 JSON 对象"]
    if not template:
        return ["模板是空的，留空请直接关掉自定义"]

    used = set()

    def walk(node, where):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{where}.{key}" if where else key)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{where}[{i}]")
        elif isinstance(node, str):
            for name in _PLACEHOLDER.findall(node):
                used.add(name)
                if name not in ENVELOPE_VARIABLES:
                    problems.append(f"{where}: 没有 {{{{{name}}}}} 这个变量")

    walk(template, "")

    if "__code" not in used:
        problems.append("模板里没有用到 {{__code}}，设备将拿不到业务状态码")
    if "__message" not in used:
        problems.append("模板里没有用到 {{__message}}，出错时设备看不到原因")
    return problems
