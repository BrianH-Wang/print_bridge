import base64
import os
import platform
import subprocess
import tempfile
import time
from datetime import datetime

from logger import print_log

# 待打印的 PDF 落在系统临时目录下，避免污染桌面，也避免并发打印互相覆盖
PRINT_DIR = os.path.join(tempfile.gettempdir(), "print_bridge")

# 打印后保留一段时间便于排查，超过该时长的旧文件在下次写入时清理
_KEEP_SECONDS = 24 * 3600


def get_desktop_path():
    return os.path.join(os.path.expanduser("~"), "Desktop")


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
            import cups
            conn = cups.Connection()
            printers = list(conn.getPrinters().keys())
            default_printer = conn.getDefault()
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
