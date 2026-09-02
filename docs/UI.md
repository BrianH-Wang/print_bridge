# 界面元素对照表

改 UI 时用这份文档定位：**直接说编号**（如「A4 的按钮太挤」「E6 提示文案改一下」），
或说名称（如「配置方案卡的保存按钮」）。编号只在这份文档里用，代码里不出现。

界面代码在 `ui/` 包里（`gui.py` 只剩一行转发，保持老 import 可用），配色和样式在 `theme.py`。
表里的「代码位置」是稳定锚点，行号会随改动漂移，以模块名 + 方法名为准。

```
ui/
  main_window.py      主窗口：拼装各配置卡与页签（H / A / B / C / D外壳 / E / F / G / T）
  constants.py        尺寸、上限、下拉选项、表格列名
  helpers.py          纯函数：human_bytes / normalize_addr / clamp_* / sanitize_printers / sanitize_channels
  printer_list.py     D 区打印机列表组件（表格 + 增删改 + 设为默认）
  channel_list.py     E 区接收通道列表组件（表格 + 增删改）
  profiles.py         A 区配置方案（mixin：要读写各张卡片上的控件）
  server_control.py   F 启停服务与各项校验（mixin）
  panels/
    log_panel.py      L 日志页签（自带队列与定时器）
    history_panel.py  Y 接收历史页签（记录、筛选、重发、ack 都归它）
  dialogs/
    image_preview.py  printer.py  http_response.py
    channel.py        field_mapping.py             ← 通道对话框与它的「字段映射」页
    advanced_search.py  history_search.py          ← 弹窗一个模块一个
```

界面之外有两个纯函数模型层，都不依赖 Qt、可以直接单测：

- `channels.py` —— 一条链接**怎么收**：路由、回给设备的应答外壳
- `mapping.py` —— 收到之后**怎么翻译、发给前端什么形状**：路径取值、转换算子、字段映射、出向报文
- `pairing.py` —— 两条链接**怎么合成一条**：暂存区、等待与收集、合并规则、过期与上限

界面只负责填表，两边的校验函数（`validate_envelope` / `validate_outbound` / `map_fields`）
同时供对话框做实时预览和运行时使用，不会出现"预览通过但运行时报错"。

---

## 整体布局

```
┌─ 主窗口 (WebSocketServerGUI, 最小 900×620, 默认 1080×820) ────────────┐
│  H1 标题「Print Bridge」            H2 主题  H3 [跟随系统|白天|黑夜]  │
├──────────────────────────────────────────────────────────────────────┤
│  F [启动服务器]   G 状态：就绪，等待启动        ← 常驻，不随页签切换  │
├──────────────────────────────────────────────────────────────────────┤
│  T [配置 ⚙] [日志输出 📋] [接收历史 📜]                                │
├──────────────────────────────────────────────────────────────────────┤
│  「配置 ⚙」页签内容（整体可上下滚动）：                                │
│  A  配置方案  📁                                                      │
│     A1 当前方案[下拉]  A2保存 A3新建 A4重命名 A5删除 A6设为默认        │
│     A7 提示行：当前方案「仓库B」（默认方案） · 已保存                  │
├──────────────────────────────────────────────────────────────────────┤
│  B  功能选择  🛠     B1 ☐自动打印   B2 ☑获取数据                      │
├──────────────────────────────────────────────────────────────────────┤
│  C  前端连接  🔌     C1 WebSocket 端口 (1024-65535): [8765]           │
│     C2 提示行：前端连接 ws://localhost:8765 ——…                       │
├──────────────────────────────────────────────────────────────────────┤
│  D  自动打印配置  🖨                                  ← 勾 B1 才显示  │
│     D1 打印机表格 Key|备注|方式|目标|默认★     D2添加  D3编辑         │
│                                                D4删除  D5设为默认     │
│     D6 提示行：前端发 {"type":"print","printer":"receipt"} 指定打给谁  │
├──────────────────────────────────────────────────────────────────────┤
│  E  获取数据配置  🌐  E1 监听地址[输入框]  E2 HTTP响应…← 勾 B2 才显示 │
│     E3 通道表格 Key|备注|路径|方向|超时|应答外壳  E4添加  E5编辑       │
│                                                  E6删除               │
│     E7 提示行：设备 POST 到 http://127.0.0.1:13213/… · 3 条通道按路径分流│
└──────────────────────────────────────────────────────────────────────┘
```

配置区独立成一个页签，日志和接收历史各占满整个窗口——日常盯的是它们，
配置一次设好就不动。卡片顺序由 `main_window._build_config_page()` 决定；
A–E 五张卡都是 `QGroupBox`，标题文字里的 emoji 前有两个空格（`"配置方案  📁"`），
是为了和标题拉开距离。

配置页套了一层 `QScrollArea`：五张卡直接堆在主界面上会把窗口最小高度顶到 1400+，
笔记本屏幕放不下。现在窗口能缩到 620 高，配置放不下就自己滚。

启停按钮和状态条在页签之上常驻（`_build_action_bar()`），
在「配置」页也能随手启停、也看得到服务状态。首次运行默认停在「配置」，
之后默认停在「日志输出」。

---

## H 顶栏 — `main_window._build_header()` / `_build_theme_switch()`

| 编号 | 名称 | 界面文字 | 控件 | 备注 |
| --- | --- | --- | --- | --- |
| H1 | 标题 | Print Bridge | 局部变量 `title` | 样式 `QLabel#title` |
| H2 | 主题标签 | 主题 | 局部变量 `theme_label` | 样式 `QLabel#hint` |
| H3 | 主题分段控件 | 跟随系统 / 白天 / 黑夜 | `self.theme_group` | 容器 `QWidget#segment`，按钮 `QPushButton#seg`；选项来自 `theme.THEME_CHOICES` |

## A 配置方案卡 — `main_window._build_profile_card()` + `ui/profiles.py`

| 编号 | 名称 | 界面文字 | 控件 | 触发的方法 |
| --- | --- | --- | --- | --- |
| A0 | 卡片本体 | 配置方案  📁 | 局部变量 `card` | — |
| A1 | 方案下拉框 | 当前方案: | `self.profile_combo` | `on_profile_selected()` |
| A2 | 保存按钮 | 保存 | `self.profile_buttons[0]` | `save_profile()` |
| A3 | 新建按钮 | 新建 | `self.profile_buttons[1]` | `new_profile()` |
| A4 | 重命名按钮 | 重命名 | `self.profile_buttons[2]` | `rename_profile()` |
| A5 | 删除按钮 | 删除 | `self.profile_buttons[3]` | `delete_profile()` |
| A6 | 设为默认按钮 | 设为默认 | `self.profile_buttons[4]` | `set_default_profile()` |
| A7 | 方案提示行 | ★ 默认 · 已保存 / 有未保存的修改 | `self.profile_hint` | `_update_profile_hint()` |

按钮的文字 / 提示 / 样式 / 回调集中在 `_build_profile_card()` 里的 `buttons` 列表，
加减按钮改这一处即可（栅格列号和 hint 跨列数会跟着 `len(buttons)` 走）。
默认方案在 A1 里显示为 `★ 名称`，真名存在 item 的 `data` 里。

## B 功能选择卡 — `main_window._build_function_card()`

| 编号 | 名称 | 界面文字 | 控件 |
| --- | --- | --- | --- |
| B1 | 自动打印开关 | 自动打印 | `self.print_checkbox` |
| B2 | 获取数据开关 | 获取数据 | `self.data_checkbox` |

这张卡只有一行内容，用 `objectName="compact"` 走小一号的上下内边距
（样式表 `QGroupBox#compact`），否则一行复选框会撑出一张空卡。

勾选状态控制 D / E 两张卡的显隐（`toggle_modules()`）。只勾 B1 = 只打印，HTTP 服务不启动。
运行中这两个复选框不用 `setEnabled(False)`——macOS 上禁用后的勾选标记几乎看不见，改为屏蔽鼠标事件。

## C 前端连接卡 — `main_window._build_connection_card()`

| 编号 | 名称 | 界面文字 | 控件 | 备注 |
| --- | --- | --- | --- | --- |
| C1 | 端口输入框 | WebSocket 端口 (1024-65535): | `self.port_input` | 校验在 `server_control._validate_port()` |
| C2 | 连接提示行 | 前端连接 ws://localhost:8765 —— … | `self.ws_hint` | `_update_ws_hint()` |

## D 自动打印配置卡 — `ui/printer_list.py`（外壳在 `main_window._build_print_card()`）

| 编号 | 名称 | 界面文字 | 控件 | 备注 |
| --- | --- | --- | --- | --- |
| D0 | 卡片本体 | 自动打印配置  🖨 | `self.print_group` 包着 `self.printer_list` | 勾 B1 才显示 |
| D1 | 打印机表格 | Key / 备注 / 方式 / 目标 / 默认 | `printer_list.table` | 只读，双击一行 = 编辑；渲染在 `render()`，列名 `PRINTER_COLUMNS` |
| D2 | 添加按钮 | 添加 | `printer_list.buttons["添加"]` | `add()` → 打开 P6，上限 `MAX_PRINTERS` |
| D3 | 编辑按钮 | 编辑 | `printer_list.buttons["编辑"]` | `edit()` |
| D4 | 删除按钮 | 删除 | `printer_list.buttons["删除"]` | `remove()`，弹窗确认 |
| D5 | 设为默认按钮 | 设为默认 | `printer_list.buttons["设为默认"]` | `make_default()`：把该行挪到第一行 |
| D6 | 提示行 | 共 N 台 · 前端用 printer 指定打给谁… | `printer_list.hint` | `_update_hint()`；完整报文写法在表格的 tooltip 里 |

- **第一行就是默认打印机**（「默认」列显示 ★），前端不指定 `printer` 时用它——
  单一事实来源，不额外存一个 default key。
- 组件改动后发 `changed` 信号，主窗口据此刷新 A7 的「未保存」状态。
- 打印机列表随方案保存在 `printers` 数组；启动时由 `printer_list.validate()` 校验后
  整个列表传给 `start_websocket_server()`。
- 表格超过 3 行就滚动，高度由 `PRINTER_TABLE_HEIGHT` 定死，不挤压下面的卡片。
- 渲染后默认选中第一行，否则右侧三个按钮一直灰着，看不出这块能操作。

## E 获取数据配置卡 — `main_window._build_data_card()`

| 编号 | 名称 | 界面文字 | 控件 | 备注 |
| --- | --- | --- | --- | --- |
| E0 | 卡片本体 | 获取数据配置  🌐 | `self.data_group` | 勾 B2 才显示 |
| E1 | 监听地址输入框 | 监听地址: | `self.addr_input` | 只决定 host:port；路径部分仅作示例，收不收由 E3 的通道表说了算 |
| E2 | HTTP 响应按钮 | HTTP 响应… | `self.http_resp_btn` | 打开 P5；运行中也能点，改完立即生效 |
| E3 | 通道表格 | Key / 备注 / 路径 / 方向 / 超时 / 配对 / 应答外壳 | `channel_list.table` | 只读，双击一行 = 编辑；渲染在 `render()`，列名 `CHANNEL_COLUMNS` |
| E4 | 添加按钮 | 添加 | `channel_list.buttons["添加"]` | `add()` → 打开 P7，上限 `MAX_CHANNELS` |
| E5 | 编辑按钮 | 编辑 | `channel_list.buttons["编辑"]` | `edit()` |
| E6 | 删除按钮 | 删除 | `channel_list.buttons["删除"]` | `remove()`，弹窗确认 |
| E7 | 提示行 | 设备 POST 到 http://… · N 条通道按路径分流 | `self.data_hint` | `_update_data_hint()`；地址不合法显示 ⚠️。没有兜底通道时 `channel_list.hint` 会警告其余路径一律 404 |

单向/双向、超时、并发**不再是卡片上的全局设置**，下沉到每条通道自己身上了：
图片接口要立刻回、数值接口要等前端回复，一份全局设置盖不住这种组合。
通道列表属于「配置方案」，启动服务时由 `configure_channels()` 下发给 `data_receiver`；
路由和应答外壳的实现都在 `channels.py`。

E2 打开的应答表是**全局**的（不进方案），存 `QSettings` 的 `http_responses`，由 `configure_responses()` 下发。
通道可以按场景覆盖其中几条（`channel["responses"]`），三层叠加顺序是
**通道覆盖 > 全局表 > 出厂默认**，见 `data_receiver.response_for()`。

## F / G 启动按钮与状态条 — `main_window._build_action_bar()` + `ui/server_control.py`

| 编号 | 名称 | 界面文字 | 控件 | 备注 |
| --- | --- | --- | --- | --- |
| F | 启动/停止按钮 | 启动服务器 / 停止服务器 | `self.start_btn` | 运行中换 `objectName="danger"` 变红，见 `start_server()` / `stop_server()` |
| G | 状态条 | 状态：… | `self.status_label` | `_set_status(text, state)`，state 取 `idle/running/stopped/error`，配色见 `QLabel#status[state="…"]` |

## T 页签区 — `main_window.init_ui()`

| 编号 | 名称 | 界面文字 | 控件 |
| --- | --- | --- | --- |
| T1 | 配置页签 | 配置 ⚙ | `_build_config_page()` 返回的 `QScrollArea`，第 `self.config_tab_index` 页 |
| T2 | 日志页签 | 日志输出 📋 | `self.log_panel` |
| T3 | 历史页签 | 接收历史 📜（带条数） | `self.history`，第 `self.history_tab_index` 页；标题由 `HistoryPanel.countChanged` 驱动 |

## L 日志页签 — `ui/panels/log_panel.py`

| 编号 | 名称 | 界面文字 | 控件 |
| --- | --- | --- | --- |
| L1 | 自动滚动开关 | 自动滚动 | `log_panel.autoscroll_checkbox` |
| L2 | 清空按钮 | 清空 | 局部变量 `clear_btn` |
| L3 | 日志窗口 | — | `log_panel.log_window`（样式 `QPlainTextEdit#log`，等宽字体） |

面板自己持有 `log_queue` 和定时器，每 `LOG_POLL_MS` 批量刷一次，单次最多取 `MAX_LOG_DRAIN` 条。

## Y 接收历史页签 — `ui/panels/history_panel.py`

| 编号 | 名称 | 界面文字 | 控件 |
| --- | --- | --- | --- |
| Y1 | 存储目录标签 | 存储目录：… | `history.store_label` |
| Y2 | 修改目录按钮 | 修改目录 | `history.change_dir_btn` |
| Y3 | 打开目录按钮 | 打开目录 | `history.open_dir_btn` |
| Y4 | 重发按钮 | 重发选中 | `history.resend_btn` |
| Y5 | 搜索输入框 | 按单号 / expressNo 模糊搜索 | `history.search_input` |
| Y6 | 搜索按钮 | 搜索 | `history.search_btn` |
| Y7 | 高级搜索按钮 | 历史搜索 | `history.advanced_btn`（打开 P3） |
| Y8 | 重置按钮 | 重置 | `history.reset_filter_btn` |
| Y9 | 筛选摘要行 | 共 N 条 · 图片 X MB… | `history.filter_summary` |
| Y10 | 历史表格 | — | `history.history_table`（样式 `QTableWidget#history`） |

- 记录、存储目录、ack 队列都归这个面板，主窗口只接 `countChanged` 改页签标题。
- 表格列：`HISTORY_FIXED_COLUMNS`（时间 + 固定字段 + 图片 + 推送），
  新字段自动插列见 `_ensure_columns()`；行高 `ROW_HEIGHT`，缩略图 `THUMB_SIZE`。

## P 弹窗 — `ui/dialogs/`

| 编号 | 名称 | 类（模块） | 从哪打开 |
| --- | --- | --- | --- |
| P1 | 图片预览 | `ImagePreviewDialog`（`image_preview.py`） | 双击 Y10 的图片列 |
| P2 | 历史筛选条件 | `AdvancedSearchDialog`（`advanced_search.py`） | 历史筛选 |
| P3 | 历史搜索 | `HistorySearchDialog`（`history_search.py`） | Y7 |
| P4 | 确认与错误提示 | `QMessageBox` | 切换方案有未保存修改、删除、校验失败等 |
| P5 | HTTP 响应配置 | `HttpResponseDialog`（`http_response.py`） | E2，8 个场景各自的状态码与说明，带「全部恢复默认」；行高 `ROW_HEIGHT`、列宽 `WHEN_COLUMN_WIDTH`/`CODE_COLUMN_WIDTH`、单元格留白 `CELL_MARGIN`；状态码按 2xx/4xx/5xx 上色（`_paint_code()`）；状态码框不显示微调箭头，高度与其它输入框统一为 `INPUT_HEIGHT` |
| P6 | 添加 / 编辑打印机 | `PrinterDialog`（`printer.py`） | D2 / D3，填 key、备注、方式、队列或 IP:端口，带「测试连接」 |
| P7 | 添加 / 编辑通道 | `ChannelDialog`（`channel.py`） | E4 / E5，四页，见下表 |

P7 的四页按**数据流经的顺序**排，不是按难度排——照着从左到右走一遍就是一条数据的一生：

| 页 | 管什么 | 门槛 | 代码 |
| --- | --- | --- | --- |
| 基础 | key、备注、路径、单双向、超时、并发 | 低 | `_basic_page()` |
| 字段映射 | 模式 + 字段表（来源\|目标\|转换\|必填\|默认值）+ 样例 + 实时预览 | 中，会看 JSON 就行 | `FieldMappingPage`（`field_mapping.py`） |
| 配对 | 角色（不参与/主报文/附加数据）、配对组、配对字段，两侧各自的参数 | 中 | `_pair_page()` |
| 发给前端 ⚙ | 消息 type、固定附加字段、整条报文自定义模板 | 高 | `_outbound_page()` |
| 回给设备 ⚙ | HTTP 状态码策略、应答外壳模板 | 高 | `_advanced_page()` |

配对页选「附加数据」时，基础页的**数据流会被锁成单向并置灰**——
附加通道收下就回、不等前端，让人以为能选双向只会误导（`_on_pair_role_changed()`）。
跨通道的搭配校验在 `ChannelListWidget._pair_problem()`，启动前跑，
只有主报文没附加、只有附加没主报文、一组多条主报文、两边配对字段不一致都会拦下。

- 标题里的 ⚙ 是给人的信号：**这两页要懂 JSON**，前两页不用。
- 铁律：**所有项的默认值 = 当前行为**，全默认时和不配置完全一样。
- 三处模板都带**实时预览**，改坏了当场在预览下方标红且存不进去：
  应答外壳 `channels.validate_envelope()`、出向报文 `mapping.validate_outbound()`、
  字段映射按样例试算 `mapping.map_fields()`。
- 字段映射的问题**只警告不拦**（点确定时问一句）——样例常常不全，
  真到运行时缺字段会按「必填字段缺失时」的策略处理。
- 「按样例填充」把样例里的字段全列成映射行（`mapping.flatten_keys()`），
  用户只要改右边的目标名，不用自己琢磨 `a.b[0].c` 怎么写。
- 样例本身跟着通道一起存（`channel["sample"]`），只用于预览，不参与运行时。

`HistorySearchDialog` 通过 `self.panel` 拿记录、图片和重发能力，parent 必须是 `HistoryPanel`。

---

## 样式与配色 — `theme.py`

- 颜色全部走 `PALETTES[light|dark]` 里的 token，样式表 `_TEMPLATE` 只引用 token 名，
  **改颜色不用动样式表结构**。
- 常用选择器：`QGroupBox`（卡片）、`QGroupBox#compact`（只有一行内容的卡）、`QLabel#hint`（灰色提示行）、`QLabel#title`、
  `QLabel#status[state="…"]`、`QPushButton`（主按钮）/`#secondary`/`#danger`/`#seg`、
  `QLineEdit, QComboBox`（输入控件）、`QTableWidget#history`、`QPlainTextEdit#log`、
  `QTabBar::tab`。
- 语义色：`run_fg`（2xx 绿）/ `warn_fg`（4xx 橙）/ `err_fg`（5xx 红），
  按主题取色用 `theme.active_palette(app, mode)`。
- 下拉箭头是 `_arrow_icon_path()` 按主题色现画的 PNG（Qt 样式表不支持 data: URI），
  改箭头形状去 `_arrow_pixmap()`，改大小去 `ARROW_SIZE`。

## 改动落点速查

| 想改什么 | 去哪 |
| --- | --- |
| 某个控件的文字 / 提示气泡 | `main_window` 里对应的 `_build_*_card()`，或对应的组件模块 |
| 卡片顺序、卡片之间的间距 | `main_window._build_config_page()`（`col.setSpacing`）——卡片自己不再留底部空白 |
| 卡片内的行距、列宽 | 各 `_build_*` 里的 `QGridLayout` 参数 |
| 卡内行距（输入行↔提示行） | 各卡 `grid.setVerticalSpacing(8)`；`hint_row` 只占一行文字高（20px） |
| 窗口尺寸 | `main_window.init_ui()` 的 `setMinimumSize` / `resize`（配置区可滚动，不必为它留高度） |
| 尺寸 / 上限 / 下拉选项 | `ui/constants.py` |
| 与界面无关的取值、校验、格式化 | `ui/helpers.py`（可直接单测，不用起 Qt） |
| 颜色 | `theme.py` 的 `PALETTES` |
| 圆角、边框、字重等结构性样式 | `theme.py` 的 `_TEMPLATE` |
| 运行中哪些控件要禁用 | `ui/server_control.py` 的 `_set_inputs_enabled()` |
| 打印机表格的行为 | `ui/printer_list.py` |
| 接收历史的行为 | `ui/panels/history_panel.py` |

> 改了界面结构（新增/删除控件、改编号涉及的顺序）请顺手更新这份文档，
> 否则下次按编号定位会对不上。
