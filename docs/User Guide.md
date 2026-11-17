# Print Bridge User Guide

An operating manual for end users. No programming knowledge needed; just follow along.
中文版：[使用说明](使用说明.md).

---

## 1. What this program does

A web page in a browser **cannot talk to a printer directly**, and **cannot receive data pushed by an outside system**.
Print Bridge runs on this computer and fills both gaps:

```
            ┌──────────────────────────────────┐
Upstream ──▶ │  Print Bridge                    │ ──▶ Browser page
(device /    │  receive, translate fields,      │
 system)     │  merge, forward                  │ ◀── Print commands
            └──────────────┬───────────────────┘
                           └──────▶ Printer
```

It does two jobs, and you can enable either one on its own:

| Feature | What it does |
| --- | --- |
| **Receive data** | An upstream system posts data to this machine; the program forwards it to the browser page |
| **Auto print** | The browser page sends print content; the program hands it to the printer |

> The program only relays **inside this computer**. No business data is sent to the internet.
> The only outgoing request is the **update check**: it asks GitHub whether a newer version exists and sends no data (see [9. Updating](#9-updating)).

---

## 2. First launch

### Starting

Double-click the program icon. Nothing else needs installing.

**If the operating system blocks it:**

- **macOS** says "the developer cannot be verified" / "Apple could not verify it is free of malware"
  (the program has no Apple developer signature, so this always happens the first time; nothing is wrong with it)
  1. Double-click it once; when it is blocked, click "Done";
  2. Open **System Settings → Privacy & Security**, scroll to the bottom and click **"Open Anyway"** next to Print Bridge, then enter your login password;
  3. Double-click the program again and click "Open" in the dialog.

  You only need to do this once, and again after installing each new version.
  (On macOS 14 and earlier you can also **Control-click** the icon in Finder → "Open".)
- **macOS** says "is damaged and can't be opened. You should move it to the Trash"
  → This is the quarantine flag added by the download, not real damage. Open Terminal, paste the line below, press Return, then double-click the program:
  ```bash
  xattr -dr com.apple.quarantine "/Applications/Print Bridge.app"
  ```
  (If the program isn't in Applications, use its actual path; or type `xattr -dr com.apple.quarantine ` and drag the program icon into the Terminal window.)
- **Windows** says "Windows protected your PC"
  → Click "More info" → "Run anyway".
- On first launch the **firewall asks whether to allow network access** → you must click "Allow",
  otherwise upstream cannot reach the program and the browser cannot connect.

### The window

The **Start / Stop button** and the **status bar** stay at the top, visible and usable from every tab.

Below them are four tabs:

| Tab | When to look at it |
| --- | --- |
| **Settings ⚙** | Set once when installing; rarely touched afterwards |
| **Log 📋** | Watch this day to day; every send and receive is recorded |
| **History 📜** | Look up past data, view images, resend to the page |
| **Self-test 🧪** | Click once after configuring; you know whether pairing works without waiting for the real device |

The top-right corner switches **Light / Dark / Follow system**. The choice is remembered.

---

## 3. Up and running in five minutes

The easiest path is to **start from a template** rather than a blank form.

### 1. Create a configuration

"Settings" tab → "Profiles" card → "Profile: **Template**"

Pick a template in the dialog, check the preview below it, and click "**Apply**".
This creates a new profile and switches to it.

> If your integration partner gave you a `.json` config file, click "**Import**" and choose it instead. Same result.
> The `configs` folder next to the program also ships with ready-made config files, and the import dialog opens there by default.

### 2. Check three things

| Where | What to check |
| --- | --- |
| Features | Tick "Receive data" to receive, "Auto print" to print |
| Front-end connection | The WebSocket port. Tell the person writing the page which one to use |
| Receive settings | The listen address. Tell upstream where to send |

### 3. Click "Start server"

When the button turns red and the status bar says "Running", it is up.

Switch to "Log". You should see something like:

```
✅ WebSocket 监听启动: ws://0.0.0.0:8765
🔁 接收通道 2 条：
   · order_data 主数据 ← /data（双向）· 并发 · 超时 2s · 自定义应答外壳
🌐 HTTP 监听启动: http://0.0.0.0:13213
```

(Log lines are in Chinese. The icons at the start of each line tell you what kind of event it is; see section 6.)

### 4. Confirm it really works

- When the browser page connects, the log prints `🌐 WebSocket 客户端已连接` (client connected)
- When upstream sends data, the log prints `📥 [channel] 接收数据: ...` (data received),
  and the count in the "History" tab title goes up by one

If you see both, everything is connected.

---

## 4. Configure as needed

Everything below is in the "Settings" tab. **Remember to click "Save"** when done,
otherwise the changes are lost when you switch profiles.
The hint line says either "Saved" or "Unsaved changes".

> Most settings are locked while the server is running. Click "Stop server" first.

### 4.1 Front-end port

The person writing the page needs this address: `ws://<this machine's IP>:<port>`. The default port is 8765.
The hint line shows the full address; copy it to them.

### 4.2 Adding a printer

"Auto print settings" → "Add".

| Field | Meaning |
| --- | --- |
| **Key** | An English name the page uses to pick a printer, e.g. `receipt`, `kitchen` |
| **Note** | Free text, for your own reference |
| **Type** | "System printer" = a queue already installed on this computer (USB / Bluetooth / Wi-Fi / Ethernet all count)<br>"Direct network" = connect straight to the printer's IP, bypassing the system queue |
| **Target** | For system printers, pick the queue name; for direct network, enter `IP:port` (usually 9100). "Test connection" is available |

**The first printer is the default** (marked ★ in the table). It is used when the page does not name one.
To change the default, select another row and click "Set as default".

### 4.3 Adding a receive channel

"Receive settings" → "Add". Each upstream endpoint gets one **channel**.

On the **Basic** page fill in:

| Field | Meaning |
| --- | --- |
| **Key** | An English name. The page uses it to tell which channel data came from |
| **Path** | The part after `/` in the upstream URL, e.g. `/data`.<br>`*` means **catch-all**: any path no other channel claims goes here |
| **Flow** | **One-way** = reply as soon as data is received, do not wait for the page<br>**Two-way** = wait for the page to return a result, then answer upstream with it |
| **Timeout** | For two-way, how long upstream will wait at most |

Then look at the lower half, **"How to use this channel"**. It contains a ready-made command
you can send to your integration partner so they can test against it.

> **When to use two-way?** When upstream needs a result computed by the business system before it can continue
> (a number, a status). If it only needs to drop data in, use one-way.

### 4.4 When field names don't match

Upstream sends `no`, the page wants `orderNo`, and neither side can change. Translate it on the "**Field mapping**" page.

**The quickest way:**

1. Ask your partner to send one real record and **paste it into the "Sample" box** below
2. Click "**Fill from sample**". Every field is listed automatically
3. Edit only the "Target field" column, using the names the page wants
4. Set the mode to "**Keep only the mapped fields**"
5. Check the **preview** below. The right-hand side is what the page will receive

The "Transform" column is optional. Common ones:

| Transform | When to use |
| --- | --- |
| Trim whitespace | Upstream sometimes adds stray spaces |
| To number | Upstream sends weight as text `"12.50"`, the page wants the number `12.5` |
| Uppercase / lowercase | Order numbers arrive in mixed case |
| ms / s timestamp → date-time | Upstream sends `1767225600000`, the page wants `2026-01-01 12:00:00` |

If "Required" is ticked and upstream omits that field, the policy above applies (default: return an error upstream and do not forward).

### 4.5 Merging two endpoints into one record

Upstream splits one event across two endpoints (for example an **image** on one and the **main data** on another),
linked by the same business number. The page wants one complete record, not two unrelated messages.

Set the "**Pairing**" page on both channels:

| Channel | Role | Also fill in |
| --- | --- | --- |
| The main-data one | **Primary** | How long to wait for the attachment (default 1 s), and whether to forward anyway or return an error when it never arrives |
| The attachment one | **Attachment** | How long to hold it (default 60 s) |

Both channels must use the same "**Pair group**" and the same "**Pair field**".

> ⚠️ **The pair field is the name *after* field mapping.**
> If the two channels originally use different names (`no` on one, `orderNo` on the other),
> map them to the same name on each channel's "Field mapping" page first.

Either one may arrive first. The channel set to "Attachment" is automatically locked to one-way:
it acknowledges on receipt and is not pushed to the page on its own.

Clicking "Start server" checks that pairing is complete. If half is missing you are told exactly what.

### 4.6 The reply to upstream has the wrong format

Upstream's API document dictates the field names and status codes in the reply. If the defaults don't match:

- **Change status codes and messages**: "Receive settings" → "HTTP responses" button, edit per scenario.
  This is global, can be changed while running, and takes effect as soon as you click OK.
- **Change the whole reply shape** (for example the field must be `msg`, not `message`):
  the channel's "**Reply to device ⚙**" page → tick "Custom response envelope" → pick one from the "Common shapes" dropdown.
- **Upstream wants exactly what the page returned, with no wrapper**: on the same page tick
  "When a two-way reply arrives from the front end, return it to the device as-is".
  This only affects the case where the page actually replied. No page connected, timeout, and error cases
  still use the format above. The `📨 回给设备` log line shows exactly what upstream received,
  so you can confirm the change at a glance.

> The two ⚙ pages require reading JSON. **If unsure, leave them alone.** The defaults are safe.
> Usually the integration partner configures these once, exports the profile, and you import it.
> Whoever does the integration should read the [Advanced Guide](Advanced%20Guide.md), which explains every item.

---

## 5. Sharing the configuration / moving to another computer

Second row of the "Profiles" card:

| Button | When to use |
| --- | --- |
| **Export** | Save the current configuration as a `.json` to hand to a colleague or keep as a backup |
| **Import** | Load someone else's `.json`. Opens in the `configs` folder next to the program by default |
| **Template** | Start from a built-in ready-made configuration |

- Export saves **what you see on screen right now**, not the last saved state.
- If an imported profile has the same name as an existing one, you are asked whether to **overwrite**, **save as new**, or **cancel**. Nothing is overwritten silently.
- The export includes: ports, listen address, printers, every channel (with field mapping and pairing), and reply-format settings.

> This is the easiest way to roll out: **have a technical person set up one machine, export, and import on all the others.**
> Nobody else needs to open the ⚙ pages.

**Multiple profiles**: one computer can hold several profiles (say "Warehouse A" and "Warehouse B").
Pick one from the dropdown and the whole setup switches. Click "Set as default" on the one you use most;
it loads automatically at startup (marked ★).

---

## 6. Day-to-day use

### Log

Every send and receive is recorded here. When something goes wrong, look here first. The switch at the top left picks one of two views, and the program remembers your choice:

**简约 Simple** (for everyday use): **one row per request**: Time / Channel / Tracking no. / Result / Time taken / Note.
Search for a tracking number and you see at once whether that parcel arrived, whether it succeeded and how long it took.

- **Time taken** is the **total time** from receiving the upstream request to writing the reply back (waiting for the image and for the front end's reply are included).
  When there was waiting, the brackets break it down in milliseconds, e.g. `1307 ms（配对 1005 / 前端 302）`:
  "配对" is waiting for the attachment (image), "前端" is waiting for the page to reply, "排队" is queueing behind earlier requests in serial mode.
  Whichever part is large is where the time went.
- **Result** is the code returned upstream and what it means, e.g. `200 接收成功（双向）`, `504 等待回复超时`. Failed rows are red.
- **Note** is the message returned upstream (`msg`); when the front end's sorting result is passed through as-is and has no `msg`, the reply itself is shown.
- Besides requests it shows server start / stop, front end connect / disconnect, print jobs, and errors that don't belong to a request.

**详细 Detailed** (for troubleshooting): every step: Time / Area / Node / **Event** / Content.
"Event" is its own column, so receive, waiting for pairing, merged, pushed to front end, front-end reply, replied to device … line up vertically and are easy to scan.
"Area" is which part of the program is working (接收 receive, 前端 front end, 打印 print, 配对 pairing …), "Node" is the channel (e.g. `sorter_data`).

**The "操作" (Actions) buttons at the right end of each row** show what you can do with that row:

| Button | What it does |
| --- | --- |
| **详细** (Detailed) | (request rows) switch to Detailed, showing only the steps of this request |
| **源数据** (Source data) | (only when something went wrong, in blue) the raw data received and the request headers |
| **详情** (Details) | the full content of this row |
| **复制单号** (Copy tracking no.) | (request rows with a tracking number) copy the tracking number; a "已复制单号 xxx" (copied) notice appears below the table |

Double-clicking a row does the same as its first button (rows with an error open the source data directly); the right-click menu has the same options.

**From Simple to Detailed**: click "详细" on a request to switch to Detailed showing only the lines for that request
(including the front-end reply and acknowledgements); a bar above says which request it is. Click "显示全部" (Show all) to go back.

Common events:

| Event | Meaning |
| --- | --- |
| `🌐 前端连接` / `🔌 前端断开` | The browser page connected / disconnected |
| `📥 接收` | Upstream sent data |
| `🧩 暂存待配对` / `🧩 已合并` | An attachment (e.g. the image) arrived first and is waiting / main data and attachment merged into one |
| `📤 推送前端` | Forwarded to the page(s), with how many |
| `↩️ 前端回复` / `✅ 采用回复` | Two-way mode: the page returned its result / that reply is used to answer upstream |
| `📨 回给设备` | Status code, time taken and body returned upstream. This is exactly what upstream received |
| `⏱ 回复超时` / `⚠️ 配对缺项` | The front end didn't reply in time / the attachment never came, forwarded without it |
| `🖨 打印任务` | Printing |
| Starts with `⚠️` / `❌` | Something went wrong; the content says why |

**Searching**: type a tracking number, channel name or any word from an error into the search box; both the content and the source data are searched.
The level drop-down shows only errors (or errors and warnings); in Detailed you can also show only one area.
Right-click a row for "搜单号" (search this tracking number) or "只看节点 xxx" (only this node).

**Full content and source data**: click "详情" (Details) on any row to see its full content.
When upstream sends something that isn't valid JSON, the Simple row reads `400 请求体不是合法 JSON` in red.
**Click "源数据" (Source data) on that row** (or double-click it) to see:

- **exactly what was received**, with **the offending character selected** and its line and column, so a missing quote or extra comma is obvious;
- **the request line, the sender's address and the request headers** (Content-Type, Content-Length …). If the body is empty, the headers tell you whether anything was sent and in what format;
- the tracking number, if one can be recognised in the body, so searching by tracking number still finds it.

"复制源数据" (Copy source data) copies it so you can send it to the other side; "看这个请求的详细日志" (see this request's detailed log) shows every step it went through.

**Bad requests are saved to disk**: the log is gone when the program closes, so every body that fails to parse is also saved
in the `bad_requests` folder inside the History storage directory ("打开目录" (Open folder) on the History tab takes you there).
Each one is a `.txt` named by time and channel, e.g. `20261117-134737-138_sorter_data.txt`, containing the time, channel, error and its position, request headers and the exact body received, ready to send to the other side.
If the body isn't valid UTF-8, a `.bin` with the same name keeps the original bytes. Only the latest 500 are kept; older ones are deleted automatically.
The source data window shows where it was saved; "打开所在文件夹" (Open folder) takes you there.

To copy whole rows, select them and press `Ctrl+C` (`⌘C` on Mac), or right-click "复制选中行" (Copy selected rows).
Turn off "Auto-scroll" to stop and read, or clear the screen with one click (this only clears the display, not the history).
The log keeps only the latest 2000 lines and is gone when the program closes; for records you need to keep, use History.

### History

Every received record is kept here, newest first, **up to 200 entries**.

- Columns are generated from the fields received. A new field adds a new column automatically.
- **Images show as thumbnails**. Double-click to enlarge.
- Select a row and click "**Resend selected**" to push that record to the page again,
  so you don't have to ask upstream to resend while debugging the page.
- "**Open folder**" opens the storage location in Finder / Explorer.
  "**Change folder**" stores it somewhere else.

History survives restarting the program. Past 200 entries, the oldest are deleted together with their images, so disk use stays bounded.

> There is **no "Clear history" button**. To wipe everything, switch to a new storage folder or delete the folder directly.

### Self-test

After configuring channels and clicking "Start server", switch to the "Self-test" tab and click "**Start self-test**".
The program plays the upstream device, sends real requests using each channel's sample, and ticks off each check:

- A **static check** first: is the pair field the post-mapping name, is the response envelope missing fields,
  do the sample order numbers on both channels match. These are the easiest to misconfigure and the hardest to spot by eye.
- Then **test cases**: one request per channel; each pair group is sent in both arrival orders to confirm it merges into one;
  then some bad JSON, missing fields, and wrong paths to check the error codes.
- If no page is connected, a **built-in mock page** stands in, and two-way channels are answered with the
  "Front-end reply sample" from the channel settings. If a page is connected, the program only observes, and the whole chain is tested.

Passed items are collapsed, failed ones expanded. Click any item to see the request sent, the reply received,
and the message the page got. Self-test data **does not enter History**, and its log lines carry 🧪.

"Timeout cases" are unticked by default: they make the mock page deliberately not reply, so every two-way channel waits out its full timeout.

**Test data** is generated from each channel's sample by default. To try a different order number or drop a field,
select the channel in the "Test data" row, switch to "Custom", and edit the JSON below. Edits are remembered per channel on this machine
and are still there next time; "Restore default" clears them. The expected results are recomputed from the custom data.

---

## 7. Troubleshooting

### The server won't start

| Symptom | Cause | Fix |
| --- | --- | --- |
| Log says `❌ 无法启动…端口被占用` (port in use) | Another program holds the port | Pick a different port and tell your partner |
| Status bar goes back to "Stopped" after Start | Same as above, or a bad address | Read the last log line for the specific reason |
| Dialog: "No receive channel configured" | "Receive data" is ticked but there are no channels | Add one, or use a template |
| Dialog: pair group is missing half | Only Primary or only Attachment is set | Set the other channel as prompted, or set both back to "Not paired" |

### Upstream can't get through

| Symptom | Cause | Fix |
| --- | --- | --- |
| Upstream reports connection failure | Server not running / firewall / wrong address | Check the status bar says "Running"; a listen address of `0.0.0.0` lets other machines on the LAN reach it |
| Upstream gets `404` | The path it posts to has no channel | Add a channel with that path, or a catch-all channel with path `*` |
| Upstream gets `400` field translation failed | It omitted a required field | The log names the missing field; check the spelling, or untick "Required" |
| Upstream gets `503` no client connected | Two-way mode with no page connected | Open the page first |
| Upstream gets `504` reply timeout | The page received it but didn't reply, or was too slow | Ask the page developer to confirm it replies; or raise the timeout |
| Upstream gets `504` pairing timeout | The other endpoint's data never came | See below |

### Pairing didn't work (page gets two messages, or fields are missing)

Check in order:

1. Do both channels have the same "**Pair group**"?
2. Do both have the same "**Pair field**" name? It must be the **post-mapping** name.
3. Does that field **have a value** in both records? (Visible in the preview on the "Field mapping" page)
4. Does upstream **send both on the same connection in sequence**?
   If it sends the main data, waits for the reply, and only then sends the attachment, the attachment can never go out.
   Ask your partner to **send the attachment first**, or use separate connections for the two requests.

### Nothing prints

| Symptom | Fix |
| --- | --- |
| The printer dropdown is empty | Install the printer in the system settings first, then click "Refresh" |
| Start warns "queue does not exist on this machine" | The computer or printer name changed. Edit that printer and pick the queue again |
| Log shows a print failure | Read the reason that follows; for direct network printers, "Test connection" confirms reachability |

---

## 8. Where settings and data live

### Settings

They follow the **computer login account**, not the program folder, so **upgrading or replacing the program does not lose them**.

| OS | Location |
| --- | --- |
| macOS | `~/Library/Preferences/com.printbridge.PrintBridge.plist` |
| Windows | Registry `HKEY_CURRENT_USER\Software\PrintBridge\PrintBridge` |
| Linux | `~/.config/PrintBridge/PrintBridge.conf` |

**The easiest backup is clicking "Export" and keeping the `.json`.** You never need to dig through the locations above.
On a new computer, just "Import".

### History and images

Stored in the system's user-data folder by default. Like the settings, **not inside the program folder**, so replacing the program keeps them:

| OS | Location |
| --- | --- |
| macOS | `~/Library/Application Support/Print Bridge/` |
| Windows | `%LOCALAPPDATA%\Print Bridge\` |
| Linux | `~/.local/share/print-bridge/` |

```
history.jsonl        one record per line (without images)
images/              original images and thumbnails
```

The top of the "History" tab shows the current location. "**Open folder**" opens it directly,
"**Change folder**" moves it elsewhere (a shared drive, for example).

> When upgrading from an old version, history that used to live in the program folder is **moved to the new location automatically**.
> The log records it with a line starting `📦 历史记录已从程序目录迁移到 …`.

---

## 9. Updating

### How you find out about a new version

A few seconds after the program opens it **checks once automatically**. If there is a new version, a window lists what changed and the button at the top right turns into "新版本 vX.Y.Z" (New version).
If there is nothing new, or this computer is offline, you won't be bothered.

You can also click **检查更新** (Check for updates) at the top right at any time.

The window has three buttons:

| Button | What it does |
| --- | --- |
| **下载新版本** (Download) | Downloads the package for your system in the browser |
| **跳过这个版本** (Skip this version) | No more automatic reminders for this version; newer ones are still announced |
| **稍后** (Later) | Ignore it for now; you'll be reminded next time the program opens |

### How to install it

The program does not replace itself. After downloading, install it the same way as the first time:

- **macOS**: quit Print Bridge, open the downloaded `.dmg`, drag Print Bridge into Applications and choose "Replace".
  Then allow it once using the macOS steps in [Starting](#starting).
- **Windows**: quit Print Bridge, unzip the downloaded `.zip` and replace the old program folder with the new one.

**Configurations, history and images are not stored in the program folder** (see [section 8](#8-where-settings-and-data-live)), so replacing the program loses nothing.

### What changed in this update

The first time you open a new version, a "已更新到 vX.Y.Z" (Updated to) window lists what changed, including any versions you skipped.
To see it again later, click **更新日志** (Changelog) at the top right; every version's changes are there.

---

## Appendix: glossary

| Term | Meaning |
| --- | --- |
| **Upstream** | Whoever sends data to this program (a device, another system) |
| **Front end / page** | The web page in the browser |
| **Channel** | The configuration for one receive endpoint. One upstream endpoint, one channel |
| **Profile** | A complete bundle of settings. One computer can hold several and switch with one click |
| **One-way / two-way** | Reply on receipt / wait for the page's result and answer upstream with it |
| **Pairing** | Merging data from two endpoints into one record by a shared business number |
| **Field mapping** | Translating upstream field names into the ones the page wants |
| **Response envelope** | The format of the body returned to upstream |
