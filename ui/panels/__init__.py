"""页签面板：日志、接收历史、自测、模拟设备，各自独立成组件。"""

from ui.panels.history_panel import HistoryPanel
from ui.panels.log_panel import LogPanel
from ui.panels.selftest_panel import SelftestPanel
from ui.panels.simulate_panel import SimulatePanel

__all__ = ["HistoryPanel", "LogPanel", "SelftestPanel", "SimulatePanel"]
