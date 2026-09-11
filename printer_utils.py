import base64
import os
import platform
import socket
import subprocess
import threading
import tempfile
import time
from datetime import datetime

from logger import print_log

# 待打印的 PDF 落在系统临时目录下，避免污染桌面，也避免并发打印互相覆盖
PRINT_DIR = os.path.join(tempfile.gettempdir(), "print_bridge")

# 打印后保留一段时间便于排查，超过该时长的旧文件在下次写入时清理
_KEEP_SECONDS = 24 * 3600

SYSTEM_PRINTER = "system"     # 交给系统打印队列（USB / 蓝牙 / WiFi / 网口装好的队列都算）
NETWORK_PRINTER = "network"   # 直接连打印机的 RAW 端口，把字节原样发过去
DEFAULT_RAW_PORT = 9100
DEFAULT_RAW_TIMEOUT = 10.0


DEFAULT_PRINTER_KEY = "default"
KEY_PATTERN = r"[a-z][a-z0-9_-]{0,19}"   # 前端要照着写，限制成好打的字符


def make_printer(key=DEFAULT_PRINTER_KEY, label="", kind=SYSTEM_PRINTER, queue="",
                 host="", port=DEFAULT_RAW_PORT, timeout=DEFAULT_RAW_TIMEOUT):
    """一台打印机的完整描述。key 是前端用来指定打给谁的英文标识，label 只给人看。"""
    return {
        "key": (key or DEFAULT_PRINTER_KEY).strip(),
        "label": (label or "").strip(),
        "kind": kind if kind in (SYSTEM_PRINTER, NETWORK_PRINTER) else SYSTEM_PRINTER,
        "queue": (queue or "").strip(),
        "host": (host or "").strip(),
        "port": int(port or DEFAULT_RAW_PORT),
        "timeout": float(timeout or DEFAULT_RAW_TIMEOUT),
    }


def describe_printer(printer):
    if not printer:
        return "未启用"
    where = (f"{printer['host']}:{printer['port']}（网络直连）"
             if printer["kind"] == NETWORK_PRINTER
             else f"{printer['queue']}（系统队列）")
    label = f" {printer['label']}" if printer.get("label") else ""
    return f"{printer['key']}{label} → {where}"


def printer_destination(printer):
    """只描述"打到哪"，用于表格里的目标列。"""
    if printer["kind"] == NETWORK_PRINTER:
        return f"{printer['host']}:{printer['port']}"
    return printer["queue"] or "（未选择）"


# 同一台打印机上的任务串行，不同打印机之间互不影响：
# 后厨机卡住不该把小票机也堵死。
_printer_locks = {}
_printer_locks_guard = threading.Lock()


def _printer_lock(key):
    with _printer_locks_guard:
        lock = _printer_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _printer_locks[key] = lock
        return lock


def print_job(printer, data):
    """把一份打印数据送到指定打印机，成功返回 True。阻塞，调用方放线程里执行。"""
    with _printer_lock(printer["key"]):
        if printer["kind"] == NETWORK_PRINTER:
            return send_to_printer_socket(
                data, printer["host"], printer["port"], printer["timeout"]
            )

        try:
            path = save_pdf_bytes(data)
        except Exception as e:
            print_log(f"❌ 保存打印文件失败: {e}")
            return False
        return print_pdf_file(path, printer["queue"])


def probe_printer_socket(host, port, timeout=3.0):
    """测试能不能连上打印机的 RAW 端口，返回 (是否连通, 说明)。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, f"{host}:{port} 连接成功"
    except OSError as e:
        return False, f"{host}:{port} 连不上：{e}"


def send_to_printer_socket(data, host, port, timeout=DEFAULT_RAW_TIMEOUT):
    """把字节原样写给网络打印机（JetDirect / RAW 9100），成功返回 True。

    原样透传：前端发 ZPL/TSPL 指令就发指令，发 PDF 就发 PDF ——
    这条链路不做任何转码，打印机认不认得这份数据由前端保证。
    """
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(data)
            try:
                sock.shutdown(socket.SHUT_WR)  # 告诉打印机数据发完了，别干等
            except OSError:
                pass
        print_log(f"✅ 已发送 {len(data)} 字节到 {host}:{port}")
        return True
    except OSError as e:
        print_log(f"❌ 发送到 {host}:{port} 失败: {e}")
        return False


def get_desktop_path():
    return os.path.join(os.path.expanduser("~"), "Desktop")


def _printers_via_lpstat():
    """没有 pycups 时退而用 CUPS 的命令行工具枚举。

    打印本身走的就是 `lp -d`（见 print_pdf_file），装了 CUPS 就有 lpstat，
    所以这条兜底路几乎总是通的。没有它的话，pycups 装失败就等于
    列不出队列 → 下拉是空的 → 启动校验直接拦下，明明 lp 能打却用不了。
    """
    printers, default_printer = [], None
    try:
        listed = subprocess.run(["lpstat", "-p"], capture_output=True,
                                text=True, timeout=10)
        for line in listed.stdout.splitlines():
            # "printer HP_LaserJet is idle.  enabled since ..."
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "printer":
                printers.append(parts[1])
    except (OSError, subprocess.SubprocessError):
        return [], None

    try:
        shown = subprocess.run(["lpstat", "-d"], capture_output=True,
                               text=True, timeout=10)
        # "system default destination: HP_LaserJet"，没有默认时是另一句话
        _, sep, name = shown.stdout.partition(":")
        if sep and name.strip():
            default_printer = name.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return printers, default_printer


def get_available_printers():
    system = platform.system()
    printers, default_printer = [], None
    try:
        if system == "Windows":
            import win32print
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            printers = [p[2] for p in win32print.EnumPrinters(flags)]
            default_printer = win32print.GetDefaultPrinter()
        elif system in ("Darwin", "Linux"):
            try:
                import cups
            except ImportError:
                printers, default_printer = _printers_via_lpstat()
                if printers:
                    print_log(f"ℹ️ 未安装 pycups，已改用 lpstat 枚举到 {len(printers)} 台打印机")
                else:
                    print_log("⚠️ 未安装 pycups，lpstat 也没找到打印机；"
                              "系统打印将不可用（网络直连 IP:9100 不受影响）")
            else:
                conn = cups.Connection()
                printers = list(conn.getPrinters().keys())
                # getDefault() 给的是**用户**的默认打印机，没单独设过就是 None；
                # 系统默认要问 lpstat -d。不补这一下，界面上就不会预选任何一台。
                default_printer = conn.getDefault() or _printers_via_lpstat()[1]
    except Exception as e:
        print_log(f"⚠️ 获取打印机失败: {e}")
    return sorted(printers), default_printer


def _cleanup_old_prints():
    try:
        deadline = time.time() - _KEEP_SECONDS
        for name in os.listdir(PRINT_DIR):
            path = os.path.join(PRINT_DIR, name)
            if os.path.isfile(path) and os.path.getmtime(path) < deadline:
                os.remove(path)
    except OSError:
        pass  # 清理失败不影响打印


def save_pdf_bytes(data, prefix="print_document"):
    """把 PDF 字节写入临时目录，返回文件路径。"""
    os.makedirs(PRINT_DIR, exist_ok=True)
    _cleanup_old_prints()
    filename = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.pdf"
    path = os.path.join(PRINT_DIR, filename)
    with open(path, "wb") as f:
        f.write(data)
    return path


def save_base64_pdf(base64_data, prefix="print_document"):
    return save_pdf_bytes(base64.b64decode(base64_data), prefix)


def print_pdf_file(path, printer_name):
    """跨平台打印，成功返回 True。该函数会阻塞，调用方应放到线程中执行。"""
    if not printer_name:
        print_log("❌ 未选择打印机")
        return False
    if not os.path.isfile(path):
        print_log(f"❌ 待打印文件不存在: {path}")
        return False

    system = platform.system()
    try:
        if system == "Windows":
            try:
                import win32api
            except ImportError:
                print_log("❌ 未安装 pywin32，请运行 pip install pywin32")
                return False

            print_log(f"📤 发送 PDF 到打印机: {printer_name} (Windows)")
            win32api.ShellExecute(0, "print", path, f'/d:"{printer_name}"', ".", 0)
        else:
            print_log(f"📤 发送 PDF 到打印机: {printer_name} (Linux/macOS)")
            result = subprocess.run(
                ["lp", "-d", printer_name, path],
                capture_output=True,
                timeout=60,
            )
            if result.returncode != 0:
                print_log(f"❌ 打印失败: {result.stderr.decode(errors='replace').strip()}")
                return False

        print_log("✅ 打印完成")
        return True

    except subprocess.TimeoutExpired:
        print_log("❌ 打印超时，打印机可能未响应")
        return False
    except Exception as e:
        print_log(f"❌ 打印失败: {e}")
        return False
