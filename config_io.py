"""配置的导出 / 导入 / 内置模板。

通道那套东西（路径、字段转译、配对、报文模板）配一次要花些工夫，
但**只需要配一次**：对接的人配好导出成一个 json，其他门店直接导入。
不会开发的人永远不用打开那两页 ⚙。

纯函数，不依赖 Qt——界面只管选文件和弹确认。
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from channels import ANY_PATH, ONEWAY, TWOWAY, make_channel
from mapping import PICK, make_mapping, make_outbound
from pairing import ATTACH, PRIMARY, make_pair
from printer_utils import make_printer

MARKER = "print-bridge-config"
VERSION = 1

# 方案里会被导出的字段。刻意不含 name 之外的身份信息，
# 也不含日志目录这类跟本机绑死的设置。
PROFILE_KEYS = ("ws_port", "data_addr", "enable_print", "enable_data",
                "printers", "channels")


CONFIGS_DIR_NAME = "configs"


def bundled_configs_dir():
    """随程序一起发出去的现成配置文件夹（configs/）；找不到返回 None。

    开发态就是仓库根目录下的 configs/；打包后放在可执行文件旁边
    （Windows / Linux 的 dist/print_bridge/configs），macOS 是 .app 旁边
    （DMG 里和 .app 并排）。
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        candidates = [
            exe.parent,                      # Windows / Linux：exe 所在目录
            exe.parent.parent.parent.parent, # macOS：Print Bridge.app/Contents/MacOS 往上到 .app 旁边
        ]
    else:
        candidates = [Path(__file__).resolve().parent]
    for base in candidates:
        folder = base / CONFIGS_DIR_NAME
        if folder.is_dir():
            return folder
    return None


def build_export(profile, http_responses=None, note=""):
    """把一套方案打包成可以发给别人的结构。"""
    return {
        MARKER: VERSION,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": note or "",
        "profile": {
            "name": profile.get("name", ""),
            **{key: profile.get(key) for key in PROFILE_KEYS},
        },
        # 应答表本来是全局的，但对接一台设备时它和通道是一整套，
        # 分开发过去等于让人只装了一半
        "http_responses": http_responses or {},
    }


def dumps(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2)


def parse_import(text):
    """解析导入文件，返回 (方案 dict, 应答表 dict, 问题清单)。

    只做结构层面的检查；字段值的规整交给 ui.helpers 那几个 sanitize_*，
    免得两处各写一套校验、慢慢就对不上了。
    """
    try:
        payload = json.loads(text)
    except ValueError as e:
        return None, None, [f"不是合法的 JSON：{e}"]

    if not isinstance(payload, dict):
        return None, None, ["文件内容不是一个 JSON 对象"]

    if MARKER not in payload:
        return None, None, [
            "这不像 Print Bridge 导出的配置文件"
            f"（缺少 {MARKER} 标记）。请用「导出配置」生成的文件，或从「模板」开始。"
        ]

    try:
        version = int(payload[MARKER])
    except (TypeError, ValueError):
        return None, None, [f"版本号不是数字：{payload[MARKER]!r}"]
    if version > VERSION:
        return None, None, [
            f"这个文件是更新版本（v{version}）的程序导出的，当前程序只认到 v{VERSION}。"
            "请先升级 Print Bridge。"
        ]

    profile = payload.get("profile")
    if not isinstance(profile, dict):
        return None, None, ["文件里没有 profile 这一段"]
    if not str(profile.get("name", "")).strip():
        return None, None, ["方案没有名字"]

    responses = payload.get("http_responses")
    return profile, (responses if isinstance(responses, dict) else {}), []


def load_channels(profile):
    """把方案里的通道规整成 make_channel 的形状，给不起界面的地方用（自测命令行）。

    界面走的是 ui.helpers.sanitize_channels，它多做了 key 校验、超时钳位、
    路径撞车这些针对"人手填"的检查；配置文件是程序导出的，形状本来就对，
    这里只补默认值。
    """
    channels = []
    for entry in profile.get("channels") or []:
        if not isinstance(entry, dict) or not entry.get("key"):
            continue
        mapping = entry.get("mapping")
        outbound = entry.get("outbound")
        pair = entry.get("pair")
        channels.append(make_channel(
            key=entry["key"], label=entry.get("label"), path=entry.get("path"),
            flow=entry.get("flow"), timeout=entry.get("timeout"), dispatch=entry.get("dispatch"),
            envelope=entry.get("envelope"), http_status=entry.get("http_status"),
            responses=entry.get("responses"),
            mapping=make_mapping(mapping.get("mode"), mapping.get("fields"),
                                 mapping.get("on_missing")) if isinstance(mapping, dict) else None,
            outbound=make_outbound(outbound.get("envelope"), outbound.get("type"),
                                   outbound.get("extra"), outbound.get("template"))
            if isinstance(outbound, dict) else None,
            sample=entry.get("sample"),
            pair=make_pair(**{k: v for k, v in pair.items()
                              if k in ("role", "group", "field", "value", "rename", "wait",
                                       "keep", "on_missing", "max_pending")})
            if isinstance(pair, dict) else None,
            reply_sample=entry.get("reply_sample"),
            reply_raw=entry.get("reply_raw"),
        ))
    return channels


# --------------------------------------------------------------------------- #
# 内置模板
# --------------------------------------------------------------------------- #

_DEVICE_ENVELOPE = {
    "success": "{{__success}}",
    "code": "{{__code}}",
    "msg": "{{__message}}",
    "data": "{{__data}}",
}

# 应答文案：成功按设备文档回「操作成功」；出错的场景不覆盖，用程序默认的带原因的文案
# （缺哪个字段、JSON 哪里错、等了几秒）。设备文档里的 404「服务器接口错误」/ 500「服务器逻辑错误」
# 是前端页面那一侧回的，不是这里的事。
_CALLBACK_RESPONSES = {
    "ok": {"message": "操作成功"},
    "ok_reply": {"message": "操作成功"},
}
# 上传接口按文档多两档：413 文件过大、415 解析失败，码照文档、文案带上原因
_UPLOAD_RESPONSES = {
    "ok": {"message": "操作成功"},
    "file_too_large": {"code": 413, "message": "文件超过 2M 限制：{error}"},
    "file_invalid": {"code": 415, "message": "文件解析失败：{error}"},
}
_UPLOAD_FILE_CHECK = {"type": "file", "max_bytes": 2 * 1024 * 1024, "formats": ["jpg", "png", "pdf"]}
# 文档 2.4.1 里那张 1x1 PNG，样例和自测都用它
_TINY_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
             "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _blank_profile():
    return {
        "name": "新方案",
        "ws_port": "8765",
        "data_addr": "0.0.0.0:13213",
        "enable_print": False,
        "enable_data": True,
        "printers": [],
        "channels": [make_channel(
            key="default", label="默认", path=ANY_PATH, flow=ONEWAY,
            sample={"code": "示例条码", "weight": "12.50"},
        )],
    }


def _sorter_data_channel(pair=None):
    """几何智造的 /callback：条码 + 重量体积，双向，等前端回分拣格口。

    设备文档 1.3 要求 data 里带 route，这个值只有前端算得出来，所以必须双向。
    前端回复本身就是设备要的完整应答（success/code/msg/data），所以开 reply_raw 原样透传；
    没人接、超时这些没有前端回复的场景仍按下面的外壳和应答表回。
    有图片接口的方案把它设成配对主报文（pair），没有的就单独一条。
    """
    return make_channel(
        key="sorter_data", label="分拣数值", path="/callback",
        flow=TWOWAY, timeout=15,
        # 前端回的已经是 success/code/msg/data 的完整应答，原样交给设备，不再套一层
        reply_raw=True,
        mapping=make_mapping(PICK, [
            {"source": "code", "target": "expressNo", "required": True},
            {"source": "weight", "target": "weight", "transforms": ["number"]},
            {"source": "length", "target": "length", "transforms": ["number"]},
            {"source": "width", "target": "width", "transforms": ["number"]},
            {"source": "height", "target": "height", "transforms": ["number"]},
        ]),
        pair=pair,
        envelope=_DEVICE_ENVELOPE,
        responses=_CALLBACK_RESPONSES,
        sample={"code": "CCSL2026083100123", "weight": "12.50",
                "height": "35.20", "width": "28.00", "length": "40.75"},
        # 前端按设备文档 1.3.1 回的分拣结果，自测时假前端照这个回
        reply_sample={"route": "A", "totalCount": 100, "currentCount": 1,
                      "mainOrderNo": "MO2026083100123"},
    )


def _sorter_profile():
    """几何智造分拣设备：图片 + 数值两条链接，按条码配对成一条。"""
    return {
        "name": "双向获取数据",
        "ws_port": "8765",
        "data_addr": "0.0.0.0:13213",
        "enable_print": False,
        "enable_data": True,
        "printers": [make_printer(key="label", label="标签")],
        "channels": [
            _sorter_data_channel(pair=make_pair(PRIMARY, "parcel", field="expressNo", wait=5)),
            make_channel(
                key="sorter_image", label="分拣图片", path="/upload",
                flow=ONEWAY,
                mapping=make_mapping(PICK, [
                    {"source": "code", "target": "expressNo", "required": True},
                    {"source": "file", "target": "imageBase64", "required": True,
                     "check": _UPLOAD_FILE_CHECK},
                ]),
                pair=make_pair(ATTACH, "parcel", field="expressNo", keep=60),
                envelope=_DEVICE_ENVELOPE,
                responses=_UPLOAD_RESPONSES,
                sample={"code": "CCSL2026083100123", "file": _TINY_PNG},
            ),
        ],
    }


def _sorter_no_image_profile():
    """几何智造分拣设备（无图片）：只有 /callback 一条，不配对，收到就推。"""
    return {
        "name": "双向获取数据（无图片）",
        "ws_port": "8765",
        "data_addr": "0.0.0.0:13213",
        "enable_print": False,
        "enable_data": True,
        "printers": [make_printer(key="label", label="标签")],
        "channels": [_sorter_data_channel()],
    }


# (名字, 一句话说明, 造方案的函数)
TEMPLATES = [
    ("空白起步", "一条兜底通道：任何路径都收，原样转给前端。"
                "适合从零开始，照着填自己的接口。", _blank_profile),
    ("几何智造分拣设备", "两条链接：/callback 收数值（双向，等前端回分拣格口）、"
                        "/upload 收图片（单向）。按条码配对合成一条。"
                        "应答外壳按设备文档：success/code/msg/data，成功回「操作成功」，"
                        "出错带具体原因；图片超 2M 回 413、解不开回 415。", _sorter_profile),
    ("几何智造分拣设备（无图片）", "只有 /callback 一条（双向，等前端回分拣格口），不配对、收到就推。"
                                "应答外壳按设备文档：success/code/msg/data，成功回「操作成功」，"
                                "出错带具体原因。设备不传图或前端不要图时用这份。",
     _sorter_no_image_profile),
]


def template_names():
    return [(name, description) for name, description, _ in TEMPLATES]


def build_template(index, note=""):
    """按下标造一份可以直接导入的配置。"""
    name, description, factory = TEMPLATES[index]
    return build_export(factory(), note=note or description)
