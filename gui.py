"""兼容入口：界面已拆到 ui/ 包，这里只转发主窗口类。

新代码请直接 from ui.main_window import WebSocketServerGUI。
"""

from ui.main_window import WebSocketServerGUI

__all__ = ["WebSocketServerGUI"]
