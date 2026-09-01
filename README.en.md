# Print Bridge (V1.0.0)

[中文](README.md) | **English**

A cross-platform (Windows / macOS / Linux) **local printing and data-relay service** with a PyQt6 GUI.

The problem it solves: a browser or web page cannot drive the local printer directly, nor can it receive callback data from third-party systems. This program runs on the local machine as a bridge — talking to the front-end page over WebSocket on one side, and driving the system printer and receiving external HTTP callbacks on the other.

## Features

The program consists of two modules that can be enabled independently via checkboxes in the UI.

> **The front end only ever opens one WebSocket** (default `ws://localhost:8765`; the port is configured in the "前端连接" card and may be anything in 1024–65535).
> Printing and data push **share that single connection**, so the WebSocket service starts as soon as *either* feature is checked —
> even with no printer involved and only "获取数据" enabled, the front end still connects and still receives data.

### 1. Auto Print 🖨

- Once the front-end page connects, it sends the PDF as a **binary frame**. The server saves it to the system temp directory under a timestamped name (so concurrent prints never overwrite each other), then sends it to the system printer:
  - Windows: `win32api.ShellExecute(..., "print", ...)`
  - macOS / Linux: `lp -d <printer name>`
- The print result is sent back to the front end as `{"type": "print_status", "data": "✅ 成功" / "❌ 失败"}`.
  If a PDF arrives while "自动打印" is unchecked, the server replies `"❌ 未启用自动打印"` and ignores it rather than attempting to print.
- The front end may first send a `{"type": "printWithTime", "timeInfo": {...}}` text message carrying timing data (`apiTime`, `prepareTime`, `startTime`). The server uses it to log API time, preparation time, and end-to-end total duration.
- The printer list is enumerated automatically (`win32print` on Windows, CUPS on macOS/Linux), with the system default printer pre-selected.

### 2. Data Retrieval 🌐 (HTTP callback receiver)

- Starts an HTTP server at `localhost:13213/callback` to receive JSON `POST`ed by external systems.
- On receipt, the payload is immediately broadcast to **all** connected front ends:
  `{"type": "external_data", "data": <original JSON>}`
- Returns a uniform JSON response to the caller: `{"success": true, "code": 200, "forwarded": <number of clients>}` on success, `{"success": false, "code": 400, "error": ...}` when parsing fails.
- A `GET` health check is also available, reporting the number of connected WebSocket clients.

### GUI

- Feature toggles, port / printer configuration, start-stop button, status indicator.
- The bottom area has two tabs: "日志输出" (log) and "接收历史" (receive history).
- Log: service threads push lines to the GUI through a `queue.Queue`; the UI flushes them in batches every 300 ms and keeps at most 2000 lines. Auto-scroll can be toggled and the log cleared with one click.
- **History and resend**: every payload received on `/callback` is recorded in a table, newest first, capped at 200 rows, with the live count in the tab title.
  - Columns are **generated from the fields actually received**: "时间" (time) on the left, "图片" (image) and "推送" (clients pushed) on the right, with one column per JSON field in between (`expressNo`, `weight`, …). A new field inserts a new column automatically; older rows leave it blank.
  - **The image column shows a thumbnail plus type and size** (e.g. `PNG · 1.0 MB`), and **double-clicking opens a full-size preview**. The type is detected from the file header — PNG / JPEG / GIF / BMP / WEBP / PDF — and a `data:image/png;base64,` prefix is handled too. Images above 5 MB are not written to disk, but their type and size are still recorded.
  - Other long values are folded to `head....tail[N字符]` inside the cell, with the full summary on hover.
  - Select a row and click "重发选中" to re-push that payload **verbatim** to every connected front end — the table shows a folded summary, but the resend uses the complete original data (full base64 included).
  - Handy for front-end debugging: no need to make the external system send again. If no client is connected, the resend reports that it was not delivered.
  - **Images live on disk, not in RAM**: each incoming image is written out as its own file; only a small thumbnail stays in memory, while all other fields are kept as before.
    Measured over 200 orders with ~1 MB images each: 207 MB on disk and **zero memory growth** (RSS actually dipped). `history.jsonl` is just 43 KB because it holds no base64.
    The line above the table shows the live figure, "共 N 条 · 图片 X MB 存在磁盘（不占内存）".

- **Persistent history**: every record is appended to disk the moment it arrives, so nothing is lost on close or crash; the most recent 200 records (thumbnails included) are reloaded on the next launch.

  ```
  logs/                      ← next to the program by default; created if missing
    history.jsonl            ← one metadata record per line (no image base64)
    images/
      20260818_115425_071713.png        ← original, read back when resending
      20260818_115425_071713_thumb.png  ← thumbnail, the only thing read at startup
  ```

  - The directory can be changed from the "接收历史" tab via "更改目录" (remembered in `QSettings`); "打开目录" reveals it in Finder/Explorer.
  - "清空历史" deletes both the records and the image files, so it asks for confirmation first.
  - Beyond 200 records the oldest entry is dropped along with its image file, so disk use stays bounded.
- **Theme: light / dark / follow system**, switchable in one click from the segmented control in the top-right. The choice is remembered via `QSettings` and restored on the next launch.
  - With "follow system", switching the OS between light and dark updates the window live — no restart needed.
  - Qt's `setColorScheme` also switches natively drawn controls (checkboxes, dropdown arrows, scrollbars), so you never get light native controls on a dark background.
  - All colors live in `PALETTES` in `theme.py`; recoloring never requires touching the stylesheet structure.

## Data Flow

```
Front-end page ──WS binary (PDF)──▶ websocket_handler ──▶ System printer (lp / ShellExecute)
               ◀──WS print_status──┘

External system ──POST JSON──▶ HTTP :13213/callback ──forward──▶ Front-end page (WS external_data)
```

## Project Structure

| File | Purpose |
| --- | --- |
| `main.py` | Entry point; starts the Qt application and main window |
| `gui.py` | PyQt6 main window: configuration, starting/stopping services, log display, theme switching |
| `theme.py` | Light/dark color tokens, stylesheet generation, follow-system resolution |
| `history_store.py` | History persistence: images as files, metadata as jsonl, payload restore for resend |
| `websocket_server.py` | Starts the WebSocket service (asyncio) with graceful shutdown via `stop_event` |
| `websocket_handler.py` | Per-connection message handling: PDF printing and timing statistics |
| `data_receiver.py` | HTTP callback service plus global WebSocket registration and forwarding |
| `printer_utils.py` | Printer enumeration, base64-to-PDF conversion, cross-platform printing |
| `logger.py` | Queue-based logging wrapper with timestamps |

## Requirements

- Python 3.13 (the project ships with a `.venv`)
- `PyQt6`
- `websockets`
- Printing support (pick one per platform; install manually):
  - Windows: `pywin32`
  - macOS / Linux: `pycups` (`import cups`)

```bash
pip install PyQt6 websockets
# Windows
pip install pywin32
# macOS / Linux
pip install pycups
```

## Running

```bash
python main.py
```

1. Check the features you need. Only "获取数据" (data retrieval) is enabled by default; "自动打印" (auto print) must be checked manually. Each config card appears once its feature is checked.
2. Set the WebSocket port; pick a printer as well if "自动打印" is checked.
3. Click "启动服务器" (Start Server). The log window shows the WebSocket and HTTP listening status.
4. Click "停止服务器" (Stop Server) to shut both services down.

## Notes and Limitations

- After starting, the app verifies the ports actually bound; if one is taken, the status bar turns red and the UI rolls back to "stopped" instead of falsely showing "running".
- WebSocket bind address: `localhost` on Windows, `0.0.0.0` on macOS / Linux.
- The data-retrieval address `localhost:13213/callback` is read-only in the UI and currently hard-coded.
- PDFs to print are written to `print_application_server/` inside the system temp directory; files older than 24 hours are cleaned up on the next print.
- Log lines fold any field longer than 60 characters into `head....tail[N字符]` so base64 images don't flood the log. **The data forwarded to the front end is still complete.**
- Printing is a blocking operation and runs on its own thread, so it never stalls WebSocket message handling.
