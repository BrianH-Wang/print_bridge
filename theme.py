"""主题支持：白天 / 黑夜 / 跟随系统。

样式表由一套颜色 token 生成，两套主题共用同一份布局规则，
新增颜色只需在 PALETTES 里补一项，不必改样式表结构。
"""

from PyQt6.QtCore import Qt

LIGHT = "light"
DARK = "dark"
SYSTEM = "system"

# (下拉框显示文本, 模式)
THEME_CHOICES = [
    ("跟随系统", SYSTEM),
    ("白天", LIGHT),
    ("黑夜", DARK),
]

PALETTES = {
    LIGHT: {
        "bg": "#f6f7f9",
        "surface": "#ffffff",
        "border": "#e2e5ea",
        "text": "#39414d",
        "muted": "#8b939f",
        "input_bg": "#ffffff",
        "input_border": "#d9dde4",
        "text_arrow": "#5a626d",
        "disabled_bg": "#f1f2f5",
        "disabled_text": "#9aa1ac",
        "primary": "#3d7eff",
        "primary_hover": "#2f6ce8",
        "primary_pressed": "#2a5ec8",
        "danger": "#e2504a",
        "danger_hover": "#cf443e",
        "secondary_bg": "#ebedf1",
        "secondary_hover": "#dfe2e8",
        "secondary_text": "#4a515b",
        "seg_track": "#e9ebef",
        "seg_fg": "#5a626d",
        "seg_active_bg": "#ffffff",
        "seg_active_fg": "#22262c",
        "log_bg": "#fbfcfd",
        "log_text": "#2f353d",
        "log_border": "#e2e5ea",
        "table_header_bg": "#f1f3f6",
        "table_alt": "#fafbfc",
        "table_sel_bg": "#dce9ff",
        "table_sel_fg": "#17376f",
        "idle_bg": "#eef1f5",
        "idle_fg": "#4a515b",
        "run_bg": "#e3f6ea",
        "run_fg": "#1d7a45",
        "stop_bg": "#f1f2f5",
        "stop_fg": "#6b7280",
        "err_bg": "#fdecec",
        "err_fg": "#b3312b",
    },
    DARK: {
        "bg": "#16191d",
        "surface": "#1e2228",
        "border": "#2c3239",
        "text": "#d8dde3",
        "muted": "#838d99",
        "input_bg": "#23282f",
        "input_border": "#363d46",
        "text_arrow": "#98a2ae",
        "disabled_bg": "#1b1f24",
        "disabled_text": "#5f6771",
        "primary": "#4b86ff",
        "primary_hover": "#5d93ff",
        "primary_pressed": "#3a72e0",
        "danger": "#e35a54",
        "danger_hover": "#ef6a63",
        "secondary_bg": "#2a3038",
        "secondary_hover": "#343b45",
        "secondary_text": "#c3cad3",
        "seg_track": "#23282f",
        "seg_fg": "#98a2ae",
        "seg_active_bg": "#3a424c",
        "seg_active_fg": "#ffffff",
        "log_bg": "#121519",
        "log_text": "#d6dae0",
        "log_border": "#262c33",
        "table_header_bg": "#23282f",
        "table_alt": "#1b1f25",
        "table_sel_bg": "#2f4a78",
        "table_sel_fg": "#e6efff",
        "idle_bg": "#262c33",
        "idle_fg": "#b6bec8",
        "run_bg": "#1b3a2a",
        "run_fg": "#5fd694",
        "stop_bg": "#262c33",
        "stop_fg": "#99a3af",
        "err_bg": "#3d2225",
        "err_fg": "#ff8a84",
    },
}

_TEMPLATE = """
QWidget#root {{ background: {bg}; }}

QLabel#title {{ font-size: 15px; font-weight: 700; color: {text}; }}

QGroupBox {{
    font-weight: 600;
    border: 1px solid {border};
    border-radius: 10px;
    margin-top: 14px;
    padding: 10px 18px 10px 18px;     /* 左右 padding 增到 18px，避免 label 贴边 */
    background: {surface};
    color: {text};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: {text};
}}

QLabel {{ color: {text}; }}
QLabel#hint {{ color: {muted}; }}

QLineEdit, QComboBox {{
    border: 1px solid {input_border};
    border-radius: 6px;
    padding: 6px 8px;
    background: {input_bg};
    color: {text};
    min-height: 20px;
}}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {primary}; }}
QLineEdit:disabled, QComboBox:disabled {{
    background: {disabled_bg};
    color: {disabled_text};
}}
QComboBox QAbstractItemView {{
    background: {surface};
    color: {text};
    border: 1px solid {border};
    selection-background-color: {primary};
    selection-color: #ffffff;
    padding: 4px;
    outline: none;
}}
/* 下拉箭头：用内嵌 SVG 自绘三角形，跨平台一致。 */
QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{
    border: none;
    border-left: 1px solid {input_border};
    width: 24px;
    subcontrol-position: top right;
}}
QComboBox::drop-down:hover {{ background: {secondary_hover}; }}
QComboBox::down-arrow {{
    /* 深色三角，箭头向下 */
    image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='6'><path d='M0 0 L10 0 L5 6 Z' fill='{text_arrow}'/></svg>");
    width: 10px; height: 6px;
}}

QPushButton {{
    border: none;
    border-radius: 8px;
    padding: 10px 16px;
    background: {primary};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton:hover {{ background: {primary_hover}; }}
QPushButton:pressed {{ background: {primary_pressed}; }}

QPushButton#danger {{ background: {danger}; }}
QPushButton#danger:hover {{ background: {danger_hover}; }}

QPushButton#secondary {{
    background: {secondary_bg};
    color: {secondary_text};
    font-weight: 500;
    padding: 10px 16px;          /* 与主按钮同高，避免混排时上下错位 */
}}
QPushButton#secondary:hover {{ background: {secondary_hover}; }}
QPushButton#secondary:disabled {{ background: {disabled_bg}; color: {disabled_text}; }}

/* 主题切换：分段控件，沿用扁平按钮的视觉语言 */
QWidget#segment {{ background: {seg_track}; border-radius: 9px; }}
QPushButton#seg {{
    background: transparent;
    color: {seg_fg};
    font-weight: 500;
    font-size: 12px;
    padding: 5px 12px;
    border-radius: 6px;
}}
QPushButton#seg:hover {{ color: {seg_active_fg}; }}
QPushButton#seg:checked {{
    background: {seg_active_bg};
    color: {seg_active_fg};
    font-weight: 600;
}}

QCheckBox {{ color: {text}; spacing: 8px; }}

QLabel#status {{ border-radius: 6px; padding: 9px 12px; font-weight: 600; }}
QLabel#status[state="idle"]    {{ background: {idle_bg}; color: {idle_fg}; }}
QLabel#status[state="running"] {{ background: {run_bg};  color: {run_fg}; }}
QLabel#status[state="stopped"] {{ background: {stop_bg}; color: {stop_fg}; }}
QLabel#status[state="error"]   {{ background: {err_bg};  color: {err_fg}; }}

QTableWidget#history {{
    background: {surface};
    alternate-background-color: {table_alt};
    color: {text};
    border: 1px solid {border};
    border-radius: 8px;
    gridline-color: {border};
    font-size: 12px;
}}
QTableWidget#history::item {{ padding: 4px 6px; }}
QTableWidget#history::item:selected {{
    background: {table_sel_bg};
    color: {table_sel_fg};
}}
QHeaderView::section {{
    background: {table_header_bg};
    color: {muted};
    border: none;
    border-bottom: 1px solid {border};
    padding: 6px;
    font-weight: 600;
}}
QTableCornerButton::section {{ background: {table_header_bg}; border: none; }}

QTabWidget::pane {{
    border: none;
    border-top: 1px solid {border};
    margin-top: 0;
}}
QTabBar {{
    qproperty-drawBase: 0;        /* 去掉默认下划线 */
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {muted};
    min-width: 130px;
    padding: 7px 14px;
    margin-right: 4px;
    border: 1px solid transparent;
    border-radius: 8px;
    font-weight: 600;
}}
QTabBar::tab:hover {{ color: {text}; background: {secondary_bg}; }}
QTabBar::tab:selected {{
    background: {primary};
    color: #ffffff;
    border-color: {primary};
}}

QPlainTextEdit#log {{
    background: {log_bg};
    color: {log_text};
    border: 1px solid {log_border};
    border-radius: 8px;
    padding: 8px;
    font-family: Menlo, Consolas, "Courier New", monospace;
    font-size: 12px;
}}
"""


def build_stylesheet(mode):
    """按解析后的主题（light / dark）生成样式表。"""
    return _TEMPLATE.format(**PALETTES[mode])


def resolve(app, mode):
    """把 system 解析成实际生效的 light / dark。"""
    if mode in (LIGHT, DARK):
        return mode
    return DARK if app.styleHints().colorScheme() == Qt.ColorScheme.Dark else LIGHT


def apply_theme(app, mode):
    """切换原生控件配色方案，返回对应的样式表。

    setColorScheme 让复选框、下拉箭头、滚动条等原生绘制的部件
    也跟着切换，避免深色背景上出现浅色原生控件。
    """
    style_hints = app.styleHints()
    if mode == LIGHT:
        style_hints.setColorScheme(Qt.ColorScheme.Light)
    elif mode == DARK:
        style_hints.setColorScheme(Qt.ColorScheme.Dark)
    else:
        style_hints.unsetColorScheme()

    return build_stylesheet(resolve(app, mode))
