import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from ui.main_window import WebSocketServerGUI


def asset_path(name):
    """assets/ 下的文件：开发态在仓库里，打包后在 PyInstaller 解开的目录里。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base / "assets" / name)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setOrganizationName("PrintBridge")
    app.setApplicationName("Print Bridge")
    app.setWindowIcon(QIcon(asset_path("logo.png")))

    window = WebSocketServerGUI()
    window.show()

    sys.exit(app.exec())
