"""接收历史的磁盘存储。

图片单独存成文件，元数据按行追加到 history.jsonl。
这样内存里只留缩略图，1MB 的原图不常驻；重发时再从磁盘读回来还原成 base64。
"""

import base64
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

HISTORY_FILE = "history.jsonl"
IMAGE_DIR = "images"
THUMB_SUFFIX = "_thumb.png"

# 解析失败的报文落盘到这里，程序重启了也能翻；只留最近这么多份，不让它无限长
BAD_REQUEST_DIR = "bad_requests"
BAD_REQUEST_KEEP = 500

# 单张图片上限，超过则不落盘（仍会记录元数据并告警）
MAX_IMAGE_BYTES = 5 * 1024 * 1024

_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "PNG", "png"),
    (b"\xff\xd8\xff", "JPEG", "jpg"),
    (b"GIF87a", "GIF", "gif"),
    (b"GIF89a", "GIF", "gif"),
    (b"BM", "BMP", "bmp"),
    (b"%PDF", "PDF", "pdf"),
]


APP_DIR_NAME = "Print Bridge"


def default_dir():
    """默认存储目录：系统的用户数据目录。

    不能放程序旁边——打包后程序常装在 /Applications、Program Files 这类
    只读位置，写不进去；macOS 上更是会写进 .app 包体内部，破坏签名，
    而且替换程序时历史连图片一起没了。

    开发态和打包态用同一个位置，省得"开发时好好的，打包就出事"。
    """
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / APP_DIR_NAME
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = (Path(local) if local else home / "AppData" / "Local") / APP_DIR_NAME
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = (Path(xdg) if xdg else home / ".local" / "share") / "print-bridge"
    return str(base)


def legacy_dir():
    """老版本的位置：程序所在目录下的 logs。只用于一次性迁移。"""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    return str(base / "logs")


def migrate_legacy(target=None):
    """把老位置的历史搬到新位置。返回搬过去的路径，没搬返回 None。

    只在新位置还没有数据时搬，绝不覆盖；搬不动（没权限之类）就当没发生，
    大不了从空历史开始，不该为这个拦住程序启动。
    """
    source = Path(legacy_dir())
    destination = Path(target or default_dir())
    if not source.exists() or source.resolve() == destination.resolve():
        return None
    if not (source / HISTORY_FILE).exists() and not (source / IMAGE_DIR).exists():
        return None                      # 老目录是空的，没什么可搬
    if (destination / HISTORY_FILE).exists():
        return None                      # 新位置已经有数据了，别覆盖

    try:
        destination.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            shutil.move(str(item), str(destination / item.name))
        source.rmdir()                   # 搬空了才删得掉，删不掉说明还有别的东西，留着
    except OSError:
        return None
    return str(destination)


def sniff(data):
    """按文件头识别类型，返回 (类型名, 扩展名)；识别不出返回 (None, None)。"""
    for magic, name, ext in _SIGNATURES:
        if data.startswith(magic):
            return name, ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP", "webp"
    return None, None


def _strip_data_url(value):
    """去掉 data:image/png;base64, 这类前缀。"""
    if value.startswith("data:") and "," in value:
        return value.split(",", 1)[1]
    return value


def find_image_field(payload):
    """找出 payload 里第一个 base64 图片字段，返回 (字段名, 原始字节, 类型, 扩展名)。"""
    if not isinstance(payload, dict):
        return None
    for key, value in payload.items():
        if not isinstance(value, str) or len(value) < 64:
            continue
        try:
            raw = base64.b64decode(_strip_data_url(value), validate=True)
        except Exception:
            continue
        name, ext = sniff(raw)
        if name:
            return key, raw, name, ext
    return None


def save_bad_request(directory, node, body, error, request_text):
    """把一份解析失败的报文写成能直接打开看的 .txt，返回文件路径。

    内容：时间、通道、错误和出错位置、请求行与请求头、源数据原文。
    源数据不是合法 UTF-8 时，另存一份同名 .bin 留原始字节（.txt 里解不开的字节是 �）。
    """
    folder = Path(directory) / BAD_REQUEST_DIR
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    stem = f"{now:%Y%m%d-%H%M%S}-{now.microsecond // 1000:03d}_{node or 'unknown'}"
    # 同一毫秒同一通道来两条（并发模式下可能）：后一条加序号，别把前一条盖了
    base, n = stem, 1
    while (folder / f"{stem}.txt").exists():
        n += 1
        stem = f"{base}-{n}"

    try:
        text = body.decode("utf-8")
        exact = True
    except UnicodeDecodeError:
        text = body.decode("utf-8", errors="replace")
        exact = False

    lines = [
        f"时间: {now:%Y-%m-%d %H:%M:%S}.{now.microsecond // 1000:03d}",
        f"通道: {node or '-'}",
        f"错误: {error}",
    ]
    if hasattr(error, "lineno"):
        lines.append(f"位置: 第 {error.lineno} 行第 {error.colno} 列（第 {error.pos} 个字符）")
    if not exact:
        lines.append(f"注意: 不是合法 UTF-8，原始字节另存在 {stem}.bin")
    lines += ["", "----- 请求 -----", request_text,
              "", f"----- 源数据（{len(body)} 字节）-----", text if body else "（空）"]
    path = folder / f"{stem}.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    if not exact:
        (folder / f"{stem}.bin").write_bytes(body)

    # 文件名以时间开头，按名字排就是按时间排
    reports = sorted(folder.glob("*.txt"))
    for old in reports[:-BAD_REQUEST_KEEP]:
        old.unlink(missing_ok=True)
        old.with_suffix(".bin").unlink(missing_ok=True)
    return path


class HistoryStore:
    def __init__(self, directory=None):
        self.set_directory(directory or default_dir())

    # ------------------------------------------------------------------ #
    # 目录
    # ------------------------------------------------------------------ #

    def set_directory(self, directory):
        self.directory = Path(directory)
        self.image_dir = self.directory / IMAGE_DIR
        self.history_path = self.directory / HISTORY_FILE
        self.ensure()

    def ensure(self):
        """目录不存在就建，打包后首次运行也能直接用。"""
        self.image_dir.mkdir(parents=True, exist_ok=True)

    def image_path(self, record):
        image = record.get("image") or {}
        return self.image_dir / image["file"] if image.get("file") else None

    def thumb_path(self, record):
        image = record.get("image") or {}
        if not image.get("file"):
            return None
        return self.image_dir / (Path(image["file"]).stem + THUMB_SUFFIX)

    # ------------------------------------------------------------------ #
    # 读写
    # ------------------------------------------------------------------ #

    def build_record(self, payload, forwarded, when=None, msg_id=None, channel=None):
        """图片写入磁盘，其余字段原样保留在元数据里。

        channel: 数据是从哪条接收通道进来的，多条链接时用它区分来源。
        """
        when = when or datetime.now()
        fields = dict(payload) if isinstance(payload, dict) else {"data": payload}

        image = None
        found = find_image_field(payload)
        if found:
            key, raw, name, ext = found
            fields.pop(key, None)  # 图片不进元数据文件
            image = {"field": key, "type": name, "bytes": len(raw), "file": None}
            if len(raw) <= MAX_IMAGE_BYTES:
                self.ensure()
                filename = f"{when:%Y%m%d_%H%M%S_%f}.{ext}"
                (self.image_dir / filename).write_bytes(raw)
                image["file"] = filename

        record = {
            "time": when.strftime("%Y-%m-%d %H:%M:%S"),
            "fields": fields,
            "image": image,
            "forwarded": forwarded,
        }
        if msg_id:
            record["msg_id"] = msg_id
        if channel:
            record["channel"] = channel
        return record

    def append(self, record):
        self.ensure()
        with open(self.history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def rewrite(self, records):
        """records 为新到旧；文件里按旧到新存放。"""
        self.ensure()
        with open(self.history_path, "w", encoding="utf-8") as f:
            for record in reversed(records):
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load(self, limit):
        """读回最近 limit 条，返回新到旧的列表。"""
        if not self.history_path.exists():
            return []
        records = []
        with open(self.history_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue  # 跳过写坏的行
        return list(reversed(records[-limit:]))

    def delete_files(self, record):
        for path in (self.image_path(record), self.thumb_path(record)):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def clear(self):
        try:
            self.history_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            shutil.rmtree(self.image_dir, ignore_errors=True)
        except OSError:
            pass
        self.ensure()

    def disk_bytes(self, records):
        return sum((r.get("image") or {}).get("bytes", 0) for r in records)

    # ------------------------------------------------------------------ #
    # 重发
    # ------------------------------------------------------------------ #

    def restore_payload(self, record):
        """还原成当初收到的完整 payload（把图片读回来重新 base64）。"""
        payload = dict(record.get("fields") or {})
        image = record.get("image")
        if not image:
            return payload
        path = self.image_path(record)
        if path is None or not path.exists():
            return None  # 图片文件已丢失
        payload[image["field"]] = base64.b64encode(path.read_bytes()).decode()
        return payload
