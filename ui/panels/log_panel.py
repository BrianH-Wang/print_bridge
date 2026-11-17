"""日志页签：把各线程塞进队列的日志批量刷到表格里，可搜索、按级别 / 功能筛。

两种视图，右上角切换（记住上次选的）：
  简约  一个设备请求一行：时间 / 通道 / 单号 / 结果 / 耗时 / 说明。使用者平时看这个，
        按单号一搜就知道这件货成没成、花了多久（耗时后面括号里拆出等配对、等前端各占多少）。外加服务启停、前端连断、打印和零散的错误。
  详细  每一步都有：时间 / 功能 / 节点 / 事件 / 内容。排查问题用。
        事件单独一列，「接收、推送前端、回给设备……」竖着对齐，一眼扫到要找的那步。

每行最右边是「操作」按钮，能做什么一眼看得到，不用猜双击、右键：
  详细    （请求行）切到详细视图，只显示这个请求的那几行（含前端回复、ack）
  源数据  （出错时带了原始报文的行，比如 JSON 解析失败）看对方发来的原文和请求头
  详情    其余的行：看完整内容
  复制单号（有单号的请求行）单号放进剪贴板
复制成功在表格下方弹一个提示，一会儿自己消失。
双击、右键菜单照旧能用，做的是同样的事。

自己持有队列和定时器，主窗口只管把它放进 tab。
"""

import queue

from PyQt6.QtCore import (
    QAbstractTableModel, QEvent, QModelIndex, QRect, QRectF, QSortFilterProxyModel, Qt, QTimer,
)
from PyQt6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QPushButton, QStyledItemDelegate, QTableView, QToolTip, QVBoxLayout, QWidget,
)

from logger import ERROR, REQUEST, WARN, elapsed_text, format_line, init_logger
from ui.constants import LOG_POLL_MS, MAX_LOG_DRAIN, MAX_LOG_LINES
from ui.dialogs.log_detail import LogDetailDialog

SIMPLE = "simple"
DETAIL = "detail"
MODES = [("简约", SIMPLE), ("详细", DETAIL)]

# (表头, 取值字段, 初始宽度)；宽度 None 的那一列拉满
ACTIONS_WIDTH = 170   # 最多三个按钮：详细 / 源数据 / 复制单号
COLUMNS = {
    SIMPLE: [("时间", "time", 120), ("通道", "node", 110), ("单号", "waybill", 150),
             ("结果", "result", 180), ("耗时", "elapsed", 220), ("说明", "text", None),
             ("操作", "actions", ACTIONS_WIDTH)],
    DETAIL: [("时间", "time", 120), ("功能", "area", 56), ("节点", "node", 110),
             ("事件", "event", 120), ("内容", "text", None), ("操作", "actions", ACTIONS_WIDTH)],
}

# 窗口窄时这几列按比例往回收，最窄收到这么宽；说明 / 内容列至少留出 TEXT_SHARE 的宽度。
# 单号不收：使用者就是靠它找货的，截成「CC2809…」就没用了。
# 耗时收窄后括号里的拆分会被截掉，鼠标停上去看全的
MIN_WIDTHS = {"node": 90, "result": 140, "elapsed": 70, "area": 44, "event": 96}
TEXT_SHARE = 0.3

STEPS, RAW, DETAIL_ACTION, COPY = "steps", "raw", "detail", "copy"
ACTION_TIPS = {
    STEPS: "切到详细视图，只看这个请求经过的每一步",
    RAW: "看对方发来的原文（标出出错位置）和请求头",
    DETAIL_ACTION: "看这一行的完整内容",
    COPY: "复制这个请求的单号",
}


def actions_for(entry):
    """这一行有哪些操作，[(动作, 按钮字)]。"""
    actions = []
    if entry.get("kind") == REQUEST:
        actions.append((STEPS, "详细"))
    if entry["raw"] is not None:
        actions.append((RAW, "源数据"))
    elif entry.get("kind") != REQUEST:
        actions.append((DETAIL_ACTION, "详情"))
    if entry.get("waybill"):
        actions.append((COPY, "复制单号"))
    return actions

ALL = ""
TOAST_MS = 1500
LEVEL_CHOICES = [("全部级别", ALL), ("只看错误", ERROR), ("错误和警告", WARN)]

SEARCH_DEBOUNCE_MS = 200   # 边打字边筛，停一下再刷，别每个字都把两千行过一遍


def in_simple_view(entry):
    """简约视图收哪些：请求汇总、标了 simple 的，和不属于任何请求的错误（请求里的错误汇总行已经说了）。"""
    return (entry.get("kind") == REQUEST or entry.get("simple")
            or (entry["level"] == ERROR and entry.get("req") is None))


class LogModel(QAbstractTableModel):
    """日志条目表，最多留 MAX_LOG_LINES 条，旧的从头上丢。两种视图共用一份数据，只是列不同。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries = []
        self.colors = {}
        self.mode = SIMPLE

    def columns(self):
        return COLUMNS[self.mode]

    def set_mode(self, mode):
        self.beginResetModel()
        self.mode = mode
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns())

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns()[section][0]
        return None

    def _cell(self, entry, field):
        request = entry.get("kind") == REQUEST
        if field == "time":
            return entry["time"][5:]          # 月-日 时:分:秒，年份放提示里
        if field == "elapsed":
            return elapsed_text(entry) if request else ""
        if field == "result":
            # 不是请求的那几行（服务启停、前端连断……）把事件放在结果列，一样对得齐
            return entry["result"] if request else entry["event"]
        if field == "text":
            return entry["note"] if request else entry["text"]
        if field == "actions":
            return ""                          # 按钮由 ActionDelegate 画
        return entry.get(field, "")

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        entry = self.entries[index.row()]
        field = self.columns()[index.column()][1]
        if role == Qt.ItemDataRole.DisplayRole:
            return self._cell(entry, field)
        if role == Qt.ItemDataRole.ToolTipRole:
            if field == "time":
                return entry["time"]
            if field == "text":
                return self._cell(entry, field)[:2000]
            if field in ("node", "result", "event"):
                return self._cell(entry, field) or None   # 窗口窄时这几列会截断，悬停看全的
            if field == "elapsed" and entry.get("kind") == REQUEST:
                return (f"{elapsed_text(entry)}\n从收到请求到应答写回设备的总时间；"
                        "括号里是其中花在排队、等配对、等前端回复上的毫秒数")
            return None
        if role == Qt.ItemDataRole.ForegroundRole and entry["level"] in self.colors:
            return self.colors[entry["level"]]
        return None

    def append(self, entries):
        overflow = len(self.entries) + len(entries) - MAX_LOG_LINES
        if overflow > 0:
            entries = entries[-MAX_LOG_LINES:]
            drop = min(overflow, len(self.entries))
            if drop:
                self.beginRemoveRows(QModelIndex(), 0, drop - 1)
                del self.entries[:drop]
                self.endRemoveRows()
        start = len(self.entries)
        self.beginInsertRows(QModelIndex(), start, start + len(entries) - 1)
        self.entries.extend(entries)
        self.endInsertRows()

    def clear(self):
        self.beginResetModel()
        self.entries.clear()
        self.endResetModel()

    def set_colors(self, colors):
        self.colors = {
            ERROR: QColor(colors["err_fg"]),
            WARN: QColor(colors["warn_fg"]),
        }
        if self.entries:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self.entries) - 1, self.columnCount() - 1))


class ActionDelegate(QStyledItemDelegate):
    """「操作」列：每行画几个小按钮，点了交给面板去做。

    不用 setIndexWidget 塞真按钮：两千行就是几千个控件，滚动和刷新都会卡。
    """

    PAD_X = 8       # 按钮里文字左右的留白
    GAP = 4         # 按钮之间
    HEIGHT = 20

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.hover = None    # (行, 第几个按钮)
        self.colors = {}

    def _buttons(self, rect, entry, metrics):
        """[(动作, 按钮字, 矩形)]，从单元格左边排起。"""
        x = rect.left() + 6
        y = rect.top() + (rect.height() - self.HEIGHT) // 2
        out = []
        for action, label in actions_for(entry):
            width = metrics.horizontalAdvance(label) + self.PAD_X * 2
            out.append((action, label, QRect(x, y, width, self.HEIGHT)))
            x += width + self.GAP
        return out

    def paint(self, painter, option, index):
        super().paint(painter, option, index)    # 先画底色、选中态
        entry = self.panel.entry_at(index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, (action, label, rect) in enumerate(self._buttons(option.rect, entry, option.fontMetrics)):
            hovered = self.hover == (index.row(), i)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.colors.get("hover" if hovered else "bg", QColor("#ebedf1")))
            painter.drawRoundedRect(QRectF(rect), 5, 5)
            # 源数据是出了错才有的，用强调色，一眼能看到
            painter.setPen(self.colors.get("accent" if action == RAW else "fg", QColor("#4a515b")))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()

    def _hit(self, option, index, pos):
        entry = self.panel.entry_at(index)
        for i, (action, _, rect) in enumerate(self._buttons(option.rect, entry, option.fontMetrics)):
            if rect.contains(pos):
                return i, action
        return None, None

    def editorEvent(self, event, model, option, index):
        kind = event.type()
        if kind == QEvent.Type.MouseMove:
            i, action = self._hit(option, index, event.position().toPoint())
            hover = (index.row(), i) if i is not None else None
            if hover != self.hover:
                self.hover = hover
                self.panel.table.viewport().update()
            viewport = self.panel.table.viewport()
            if action:
                viewport.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                viewport.unsetCursor()
            return False
        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            # 按在按钮上就别让表格把它当成选行 / 双击
            return self._hit(option, index, event.position().toPoint())[1] is not None
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            _, action = self._hit(option, index, event.position().toPoint())
            if action:
                self.panel.run_action(action, self.panel.entry_at(index))
                return True
        return False

    def helpEvent(self, event, view, option, index):
        _, action = self._hit(option, index, event.pos())
        if action:
            QToolTip.showText(event.globalPos(), ACTION_TIPS[action], view)
            return True
        QToolTip.hideText()
        return False

    def clear_hover(self):
        if self.hover is not None:
            self.hover = None
            self.panel.table.viewport().update()
        self.panel.table.viewport().unsetCursor()


class LogFilter(QSortFilterProxyModel):
    """视图（简约 / 详细）+ 只看某个请求 + 关键字 + 级别 + 功能。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.keyword = ""
        self.level = ALL
        self.area = ALL
        self.focus = None   # 详细视图里只看一个请求：那条汇总行

    def set_filter(self, keyword, level, area):
        self.keyword = keyword.strip().lower()
        self.level = level
        self.area = area
        self.invalidateFilter()

    def set_focus(self, request_entry):
        self.focus = request_entry
        self.invalidateFilter()

    def _in_focus(self, entry):
        # 同一个 HTTP 请求线程里写的都带同一个 req；前端回复、ack 在别的线程，靠消息 id 认
        if entry.get("req") is not None and entry["req"] == self.focus.get("req"):
            return True
        msg_id = self.focus.get("msg_id")
        return bool(msg_id) and msg_id in entry["text"]

    def filterAcceptsRow(self, row, parent):
        entry = self.sourceModel().entries[row]
        if self.sourceModel().mode == SIMPLE:
            if not in_simple_view(entry):
                return False
        else:
            if entry.get("kind") == REQUEST:
                return False
            if self.focus is not None and not self._in_focus(entry):
                return False
            if self.area and entry["area"] != self.area:
                return False
        if self.level == ERROR and entry["level"] != ERROR:
            return False
        if self.level == WARN and entry["level"] not in (ERROR, WARN):
            return False
        if self.keyword:
            haystack = " ".join(str(entry.get(k) or "") for k in (
                "time", "area", "node", "event", "text", "waybill", "result", "raw",
            )).lower()
            return self.keyword in haystack
        return True


class LogPanel(QWidget):
    """日志输出页签。"""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.log_queue = queue.Queue()
        init_logger(self.log_queue)

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._build_mode_switch())
        row.addSpacing(4)
        row.addWidget(QLabel("搜索"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("单号、通道、报错……内容和源数据都会搜")
        self.search_input.setClearButtonEnabled(True)
        row.addWidget(self.search_input, 1)

        self.level_combo = QComboBox()
        for label, value in LEVEL_CHOICES:
            self.level_combo.addItem(label, value)
        row.addWidget(self.level_combo)

        self.area_combo = QComboBox()
        self.area_combo.addItem("全部功能", ALL)
        self.area_combo.setToolTip("只看某一块功能的日志")
        row.addWidget(self.area_combo)

        self.autoscroll_checkbox = QCheckBox("自动滚动")
        self.autoscroll_checkbox.setChecked(True)
        row.addWidget(self.autoscroll_checkbox)

        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("secondary")
        clear_btn.clicked.connect(self.clear)
        row.addWidget(clear_btn)
        box.addLayout(row)

        # 只看某个请求时出现的提示条
        self.focus_bar = QWidget()
        focus_row = QHBoxLayout(self.focus_bar)
        focus_row.setContentsMargins(0, 0, 0, 0)
        focus_row.setSpacing(8)
        self.focus_label = QLabel()
        focus_row.addWidget(self.focus_label, 1)
        show_all = QPushButton("显示全部")
        show_all.setObjectName("secondary")
        show_all.clicked.connect(lambda: self.set_focus(None))
        focus_row.addWidget(show_all)
        self.focus_bar.hide()
        box.addWidget(self.focus_bar)

        self.model = LogModel(self)
        self.proxy = LogFilter(self)
        self.proxy.setSourceModel(self.model)

        self.table = QTableView()
        self.table.setObjectName("log")
        self.table.setModel(self.proxy)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setHighlightSections(False)
        self.table.doubleClicked.connect(self._on_double_click)
        self.action_delegate = ActionDelegate(self)
        self.table.setMouseTracking(True)              # 悬停到按钮上要变手形、亮一下
        self.table.viewport().installEventFilter(self)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        QShortcut(QKeySequence.StandardKey.Copy, self.table, activated=self.copy_selected)
        box.addWidget(self.table, 1)

        # 复制成功的提示：浮在表格底部中间，不占布局
        self.toast = QLabel(self.table)
        self.toast.setObjectName("toast")
        self.toast.hide()
        self._toast_timer = QTimer(self, singleShot=True, interval=TOAST_MS,
                                   timeout=self.toast.hide)

        self.count_label = QLabel()
        self.count_label.setObjectName("hint")
        box.addWidget(self.count_label)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._apply_filter)
        self.search_input.textChanged.connect(self._search_timer.start)
        self.level_combo.currentIndexChanged.connect(self._apply_filter)
        self.area_combo.currentIndexChanged.connect(self._apply_filter)

        saved = self.settings.value("log_mode", SIMPLE)
        self.set_mode(saved if saved in (SIMPLE, DETAIL) else SIMPLE)

        # 界面每 LOG_POLL_MS 批量刷一次，避免刷屏时一条一条重绘卡住主线程
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.drain)
        self._timer.start(LOG_POLL_MS)

    def _build_mode_switch(self):
        """和主题切换一个样子的分段控件。"""
        container = QWidget()
        container.setObjectName("segment")
        seg = QHBoxLayout(container)
        seg.setContentsMargins(3, 3, 3, 3)
        seg.setSpacing(2)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for label, mode in MODES:
            button = QPushButton(label)
            button.setObjectName("seg")
            button.setCheckable(True)
            button.setProperty("mode", mode)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setToolTip("简约：一个请求一行，平时看这个" if mode == SIMPLE
                              else "详细：每一步都有，排查问题用")
            self.mode_group.addButton(button)
            seg.addWidget(button)
        self.mode_group.buttonClicked.connect(lambda b: self.set_mode(b.property("mode")))
        return container

    def set_mode(self, mode):
        self.settings.setValue("log_mode", mode)
        for button in self.mode_group.buttons():
            button.setChecked(button.property("mode") == mode)
        if mode == SIMPLE:
            self.proxy.focus = None
            self.focus_bar.hide()
        self.area_combo.setVisible(mode == DETAIL)   # 简约视图里大多是「接收」，按功能筛没意义
        self.model.set_mode(mode)
        self._apply_columns()
        self._apply_filter()

    def eventFilter(self, watched, event):
        if watched is self.table.viewport():
            if event.type() == QEvent.Type.Leave:
                self.action_delegate.clear_hover()
            elif event.type() == QEvent.Type.Resize:
                self._fit_columns()
        return super().eventFilter(watched, event)

    def _apply_columns(self):
        header = self.table.horizontalHeader()
        for column in range(self.model.columnCount()):
            self.table.setItemDelegateForColumn(column, None)
        for column, (_, field, width) in enumerate(self.model.columns()):
            if field == "actions":
                self.table.setItemDelegateForColumn(column, self.action_delegate)
            if width is None:
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        self._fit_columns()

    def _fit_columns(self):
        """按窗口宽度定列宽：放得下就用默认宽度；放不下就把能收的列按比例收窄，
        给说明 / 内容列留出至少 TEXT_SHARE，别让最有用的那列被挤成几个字。

        只在窗口大小变化、切视图时重算，用户手动拖过的列宽在这之间保持不动。
        """
        columns = self.model.columns()
        fixed = sum(w for _, f, w in columns if w is not None and f not in MIN_WIDTHS)
        flexible = {f: w for _, f, w in columns if f in MIN_WIDTHS}
        room = self.table.viewport().width() * (1 - TEXT_SHARE) - fixed
        total = sum(flexible.values())
        if total > room:
            floor = sum(MIN_WIDTHS[f] for f in flexible)
            # 默认宽度和最窄之间按同一个比例取
            ratio = max(room - floor, 0) / max(total - floor, 1)
            flexible = {f: int(MIN_WIDTHS[f] + (w - MIN_WIDTHS[f]) * ratio)
                        for f, w in flexible.items()}
        for column, (_, field, width) in enumerate(columns):
            if width is not None:
                self.table.setColumnWidth(column, flexible.get(field, width))

    def set_focus(self, request_entry):
        """详细视图里只看一个请求；None 回到全部。"""
        self.proxy.set_focus(request_entry)
        if request_entry is None:
            self.focus_bar.hide()
        else:
            self.focus_label.setText(
                f"只看这一个请求：{request_entry['time'][5:]}　{request_entry['node'] or '-'}　"
                f"单号 {request_entry['waybill'] or '-'}　{request_entry['result']}　"
                f"{elapsed_text(request_entry)}"
            )
            self.focus_bar.show()
        self._update_count()

    def set_colors(self, colors):
        """主题切换时由主窗口调：错误 / 警告行的字色、操作按钮的颜色跟着主题走。"""
        self.model.set_colors(colors)
        self.action_delegate.colors = {
            "bg": QColor(colors["secondary_bg"]),
            "hover": QColor(colors["secondary_hover"]),
            "fg": QColor(colors["secondary_text"]),
            "accent": QColor(colors["primary"]),
        }

    def drain(self):
        """把队列里的日志取出来追加显示；单次最多取 MAX_LOG_DRAIN 条。"""
        entries = []
        for _ in range(MAX_LOG_DRAIN):
            try:
                entries.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if not entries:
            return

        scrollbar = self.table.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4

        for entry in entries:
            if self.area_combo.findData(entry["area"]) < 0:
                self.area_combo.addItem(entry["area"], entry["area"])
        self.model.append(entries)
        self._update_count()

        if self.autoscroll_checkbox.isChecked() or at_bottom:
            self.table.scrollToBottom()

    def clear(self):
        self.model.clear()
        self.set_focus(None)

    def _apply_filter(self):
        self.proxy.set_filter(self.search_input.text(), self.level_combo.currentData(),
                              self.area_combo.currentData())
        self._update_count()
        if self.autoscroll_checkbox.isChecked():
            self.table.scrollToBottom()

    def _update_count(self):
        shown = self.proxy.rowCount()
        if self.model.mode == SIMPLE:
            total = sum(1 for e in self.model.entries if in_simple_view(e))
            tip = "点「详细」看一个请求经过的每一步"
        else:
            total = sum(1 for e in self.model.entries if e.get("kind") != REQUEST)
            tip = "点「详情」「源数据」看完整内容"
        self.count_label.setText(
            (f"筛出 {shown} / {total} 条" if shown != total else f"共 {total} 条")
            + f"　·　{tip}，Ctrl+C 复制选中行　·　只保留最近 {MAX_LOG_LINES} 条"
        )

    def entry_at(self, proxy_index):
        return self.model.entries[self.proxy.mapToSource(proxy_index).row()]

    def run_action(self, action, entry):
        """操作列的按钮、双击、右键菜单最后都走这里。"""
        if action == STEPS:
            self.show_request(entry)
        elif action == RAW:
            LogDetailDialog(entry, self, open_request=self.show_request
                            if entry.get("kind") == REQUEST else None).exec()
        elif action == DETAIL_ACTION:
            LogDetailDialog(entry, self).exec()
        elif action == COPY:
            self.copy_text(entry["waybill"], f"已复制单号 {entry['waybill']}")

    def copy_text(self, text, message):
        QGuiApplication.clipboard().setText(text)
        self.show_toast(message)

    def show_toast(self, message):
        self.toast.setText(message)
        self.toast.adjustSize()
        area = self.table.viewport().geometry()   # 表格坐标里的内容区，避开表头
        self.toast.move(area.center().x() - self.toast.width() // 2,
                        area.bottom() - self.toast.height() - 16)
        self.toast.raise_()
        self.toast.show()
        self._toast_timer.start()

    def _selected_entries(self):
        rows = sorted(self.table.selectionModel().selectedRows(), key=lambda i: i.row())
        return [self.entry_at(index) for index in rows]

    def copy_selected(self):
        entries = self._selected_entries()
        if entries:
            self.copy_text("\n".join(format_line(e) for e in entries), f"已复制 {len(entries)} 行")

    def show_request(self, entry):
        """简约 → 详细，只留这个请求。搜索、级别先清掉，不然可能把它自己的行也筛没了。"""
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self.level_combo.setCurrentIndex(0)
        self.area_combo.setCurrentIndex(0)
        self.set_mode(DETAIL)
        self.set_focus(entry)

    def _on_double_click(self, index):
        """双击 = 这一行操作列里最靠前的那个按钮。

        坏报文的请求行除外：使用者最想看的是对方发了什么，直接给源数据，要看每一步再点「详细」。
        """
        entry = self.entry_at(index)
        actions = [action for action, _ in actions_for(entry)]
        self.run_action(RAW if RAW in actions else actions[0], entry)

    def _context_menu(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        entry = self.entry_at(index)
        menu = QMenu(self)
        for action, label in actions_for(entry):
            menu.addAction(label, lambda a=action: self.run_action(a, entry))
        menu.addAction("复制选中行", self.copy_selected)
        if entry["raw"] is not None:
            menu.addAction("复制源数据", lambda: self.copy_text(entry["raw"], "已复制源数据"))
        if entry.get("waybill"):
            menu.addAction(f"搜单号 {entry['waybill']}",
                           lambda: self.search_input.setText(entry["waybill"]))
        if entry["node"]:
            menu.addAction(f"只看节点 {entry['node']}",
                           lambda: self.search_input.setText(entry["node"]))
        menu.exec(self.table.viewport().mapToGlobal(pos))
