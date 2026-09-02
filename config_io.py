"""配置的导出 / 导入 / 内置模板。

通道那套东西（路径、字段转译、配对、报文模板）配一次要花些工夫，
但**只需要配一次**：对接的人配好导出成一个 json，其他门店直接导入。
不会开发的人永远不用打开那两页 ⚙。

纯函数，不依赖 Qt——界面只管选文件和弹确认。
"""

import json
from datetime import datetime

from channels import ANY_PATH, ONEWAY, TWOWAY, make_channel
from mapping import PICK, make_mapping
from pairing import ATTACH, PRIMARY, make_pair
from printer_utils import make_printer

MARKER = "print-bridge-config"
VERSION = 1

# 方案里会被导出的字段。刻意不含 name 之外的身份信息，
# 也不含日志目录这类跟本机绑死的设置。
PROFILE_KEYS = ("ws_port", "data_addr", "enable_print", "enable_data",
                "printers", "channels")


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


# --------------------------------------------------------------------------- #
# 内置模板
# --------------------------------------------------------------------------- #

_DEVICE_ENVELOPE = {
    "success": "{{__success}}",
    "code": "{{__code}}",
    "msg": "{{__message}}",
    "data": "{{__data}}",
}


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


def _sorter_profile():
    """几何智造分拣设备：图片 + 数值两条链接，按条码配对成一条。"""
    return {
        "name": "分拣设备",
        "ws_port": "8765",
        "data_addr": "0.0.0.0:13213",
        "enable_print": False,
        "enable_data": True,
        "printers": [make_printer(key="label", label="标签")],
        "channels": [
            make_channel(
                key="sorter_data", label="分拣数值", path="/callback",
                flow=TWOWAY, timeout=15,
                mapping=make_mapping(PICK, [
                    {"source": "code", "target": "expressNo", "required": True},
                    {"source": "weight", "target": "weightKg", "transforms": ["number"]},
                    {"source": "length", "target": "lengthCm", "transforms": ["number"]},
                    {"source": "width", "target": "widthCm", "transforms": ["number"]},
                    {"source": "height", "target": "heightCm", "transforms": ["number"]},
                ]),
                pair=make_pair(PRIMARY, "parcel", field="expressNo", wait=5),
                envelope=_DEVICE_ENVELOPE,
                responses={
                    "bad_json": {"code": 404, "message": "服务器接口错误"},
                    "reply_timeout": {"code": 500, "message": "服务器逻辑错误"},
                    "no_client": {"code": 500, "message": "服务器逻辑错误"},
                },
                sample={"code": "CCSL2026083100123", "weight": "12.50",
                        "height": "35.20", "width": "28.00", "length": "40.75"},
            ),
            make_channel(
                key="sorter_image", label="分拣图片", path="/upload",
                flow=ONEWAY,
                mapping=make_mapping(PICK, [
                    {"source": "code", "target": "expressNo", "required": True},
                    {"source": "file", "target": "imageBase64"},
                ]),
                pair=make_pair(ATTACH, "parcel", field="expressNo", keep=60),
                envelope=_DEVICE_ENVELOPE,
                responses={"ok": {"message": "操作成功"}},
                sample={"code": "CCSL2026083100123", "file": "iVBORw0KGgo..."},
            ),
        ],
    }


# (名字, 一句话说明, 造方案的函数)
TEMPLATES = [
    ("空白起步", "一条兜底通道：任何路径都收，原样转给前端。"
                "适合从零开始，照着填自己的接口。", _blank_profile),
    ("几何智造分拣设备", "两条链接：/callback 收数值（双向，等前端回分拣格口）、"
                        "/upload 收图片（单向）。按条码配对合成一条，"
                        "应答已按设备文档改成 success/code/msg/data。", _sorter_profile),
]


def template_names():
    return [(name, description) for name, description, _ in TEMPLATES]


def build_template(index, note=""):
    """按下标造一份可以直接导入的配置。"""
    name, description, factory = TEMPLATES[index]
    return build_export(factory(), note=note or description)
