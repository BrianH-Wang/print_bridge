"""界面用到的常量：尺寸、上限、下拉选项、表格列名。

放在一处便于调参——改列名 / 改上限不用翻界面代码。
"""

from PyQt6.QtCore import QSize

from channels import FIXED_200, FOLLOW_CODE
from pairing import ATTACH as PAIR_ATTACH
from pairing import ERROR as PAIR_ERROR
from pairing import NONE as PAIR_NONE
from pairing import PRIMARY as PAIR_PRIMARY
from pairing import SEND_PARTIAL as PAIR_SEND_PARTIAL
from data_receiver import CONCURRENT, ONEWAY, SERIAL, TWOWAY
from printer_utils import NETWORK_PRINTER, SYSTEM_PRINTER

DATA_ADDR = "localhost:13213/callback"
DATA_PORT = 13213     # 地址里没写端口时补的默认端口
MAX_PROFILES = 20     # 配置方案（仓库A / 仓库B…）最多保存的套数
DEFAULT_PROFILE_NAME = "默认"

# (下拉显示文本, 存进方案的值)
FLOW_CHOICES = [("单向（只转发）", ONEWAY), ("双向（等前端回复）", TWOWAY)]
DISPATCH_CHOICES = [("并发", CONCURRENT), ("单发", SERIAL)]
PRINT_TARGET_CHOICES = [
    ("系统打印机（USB / 蓝牙 / WiFi / 网口队列）", SYSTEM_PRINTER),
    ("网络直连（IP:9100 原样发送）", NETWORK_PRINTER),
]
HTTP_STATUS_CHOICES = [
    ("跟随业务 code", FOLLOW_CODE),
    ("固定 200（业务码只放 body）", FIXED_200),
]
PRINTER_COLUMNS = ["Key", "备注", "方式", "目标", "默认"]
PRINTER_TABLE_HEIGHT = 142   # 表头 + 3 行完整可见，够放小票/后厨/杯贴这类典型配置
MAX_PRINTERS = 10
PAIR_ROLE_CHOICES = [
    ("不参与配对", PAIR_NONE),
    ("主报文（等附加数据齐了再发）", PAIR_PRIMARY),
    ("附加数据（存着等主报文来取）", PAIR_ATTACH),
]
PAIR_MISSING_CHOICES = [
    ("照发，缺的字段就没有", PAIR_SEND_PARTIAL),
    ("回错误给设备，不发", PAIR_ERROR),
]
CHANNEL_COLUMNS = ["Key", "备注", "路径", "方向", "超时", "配对", "应答外壳"]
CHANNEL_TABLE_HEIGHT = 142   # 和打印机表一样高：表头 + 3 行，够放数值/图片/兜底
MIN_REPLY_TIMEOUT = 0.1
MAX_REPLY_TIMEOUT = 600.0
LOG_POLL_MS = 300
HISTORY_POLL_MS = 300   # 接收历史/ack 的轮询节奏，跟日志一致
MAX_LOG_LINES = 2000
MAX_LOG_DRAIN = 200  # 单次刷新最多取出的日志条数，避免刷屏卡住界面
MAX_HISTORY = 200    # 接收历史最多保留的条数
HISTORY_FIXED_COLUMNS = (
    ["时间"]
    + ["快递单号", "重量(kg)", "长(cm)", "宽(cm)", "高(cm)"]
    + ["图片", "推送"]
)
THUMB_SIZE = QSize(72, 40)
ROW_HEIGHT = 48
MIN_VISIBLE_ROWS = 4  # 表格初始至少能看见的行数（多了会把窗口最小高度顶起来）

# 默认要展示的数据列；启动时就创建出来，即使本次没收到也保留"-"占位。
DEFAULT_DATA_COLUMNS = ["expressNo", "weight", "length", "width", "height"]

# 数据字段名 → 中文表头；查不到的按原 key 显示。
# 外部系统的字段名不能改，这里只在显示层翻译。
FIELD_LABELS = {
    "expressNo": "快递单号",
    "weight": "重量(kg)",
    "length": "长(cm)",
    "width": "宽(cm)",
    "height": "高(cm)",
}
