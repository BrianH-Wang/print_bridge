"""页签面板：日志、接收历史、自测，各自独立成组件。"""

from ui.panels.history_panel import HistoryPanel
from ui.panels.log_panel import LogPanel
from ui.panels.selftest_panel import SelftestPanel

__all__ = ["HistoryPanel", "LogPanel", "SelftestPanel"]
