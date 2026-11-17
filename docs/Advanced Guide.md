# Print Bridge Advanced Guide

For **the person doing the integration**: the device's API and the front-end page's API are both fixed and neither side can change, and you need Print Bridge to make them fit. Assumes you can read HTTP requests and JSON.

Day-to-day operation (starting, importing profiles, reading the log) is in the [User Guide](User%20Guide.md). 中文版：[高级配置说明](高级配置说明.md).

---

## 1. Three things to know first

### 1. Advanced settings are configured once

```
Template / Import        Most people stop here: apply a ready-made profile, fill in nothing
    ↓
Basic / Field mapping / Pairing   Enough to read JSON: change paths, rename fields, tick pairing
    ↓
The two ⚙ pages          Needs HTTP + JSON: change message shapes, reply envelopes, write templates
```

The integrator sets it up on one machine → "Profiles" card → "**Export**" to a `.json` → every other machine "**Import**"s it. Nobody else ever opens a ⚙ page.

### 2. The iron rule: all defaults = exactly as if nothing were configured

Every item on the ⚙ pages defaults to the program's original behaviour. Unticked, empty, or default means unchanged. Change only the one item you have confirmed you need, and leave the rest.

### 3. Have these two things ready

| What | Used for |
| --- | --- |
| **One real record as the device actually sends it** (ask the device side for one, or copy it from the log / History) | Paste into the "Sample" box; every preview and the self-test rely on it |
| **The "response" section of the device's API document** | Decides whether the "Reply to device ⚙" page needs changing, and to what |

For the front-end side you also need to know what shape it **receives** and what shape it **replies** with; see section 4.

---

## 2. Channel basics

One channel = one device endpoint. "Receive settings" → "Add", "Basic" page:

| Item | Rule |
| --- | --- |
| **Key** | Starts with a lowercase letter, up to 20 characters. It appears as the `channel` field in what the front end receives |
| **Path** | The part after `/` in the device's POST URL. **Case-insensitive**; a trailing `/` and `?query` are ignored: the device posting `/Callback/` matches a channel set to `/callback` |
| **Path `*`** | Catch-all: any path no other channel claims. Without a catch-all, unmatched paths get 404 |
| **Flow** | One-way: reply to the device right after forwarding. Two-way: wait for the front end's reply and answer the device with it |
| **Timeout** | Two-way only, 0.1–600 s. No reply in time → 504 |
| **Dispatch** | Concurrent: requests are handled at the same time. Serial: this channel queues, one finishes before the next starts; other channels are unaffected |

**There is only one timeout budget.** On a two-way channel, queueing, waiting for pairing data, and waiting for the front-end reply all share the timeout you set. The device never waits longer than that. Queue not reached → 503; pairing data missing → per the pairing setting; front end silent → 504.

The lower half of "Basic", "**How to use this channel**", generates a `curl` command and a behaviour summary from your settings in real time. Send it to the device side as-is.

---

## 3. Field mapping: device field names → the names the page wants

The channel dialog's "Field mapping" page. The device sends `code`, the page wants `expressNo`: translate here.

### 3.1 Three modes

| Mode | What the page gets |
| --- | --- |
| **Pass through (unchanged)** | Exactly what the device sent. Default, same as not configured |
| **Keep only the mapped fields** | Only the fields listed in the table, renamed and transformed; everything else dropped |
| **Pass through + append mapped fields** | All original fields, plus the mapped ones. A renamed field's original name is removed so the page never sees both `code` and `expressNo` |

### 3.2 Each row of the table

| Column | Fill in |
| --- | --- |
| **Source field** | The device's field. Nested paths work: `order.id`, `items[0].sku`, `a.b[2].c` |
| **Target field** | The name the page wants. Empty = same as the source |
| **Transform** | See below; optional |
| **Required** | If ticked and the device omits the field, the "When a required field is missing" policy applies |
| **Default** | Used when the field is missing and the policy is "Use default" |

"**Fill from sample**" lists every field in the sample as a row; you only edit the target column. Nested objects are flattened three levels deep.

### 3.3 Transforms

Available in the dropdown:

| Transform | Effect | When |
| --- | --- | --- |
| Trim whitespace | `" A01 "` → `"A01"` | |
| Uppercase / lowercase | `"sf123"` → `"SF123"` | |
| To number | `"12.50"` → `12.5`, `"12.0"` → `12` | The device sends numbers as text |
| To text | `12` → `"12"` | |
| base64 decode to text | Decodes to UTF-8 text | The device base64-wraps a JSON string |
| ms / s timestamp → date-time | `1767225600000` → `"2026-01-01 12:00:00"` | |

Only by editing the config file (they take parameters):

| Transform | Syntax | Effect |
| --- | --- | --- |
| Prefix | `{"op": "prefix", "value": "SF"}` | `"123"` → `"SF123"` |
| Suffix | `{"op": "suffix", "value": "-A"}` | `"123"` → `"123-A"` |
| Replace | `{"op": "replace", "from": "-", "to": ""}` | `"12-34"` → `"1234"` |
| Lookup | `{"op": "lookup", "table": {"1": "East", "2": "South"}, "default": "Other"}` | `"1"` → `"East"`; unknown keys use `default`, or stay unchanged if no `default` |
| To boolean | `"bool"` | `"0"` / `"false"` / `""` → `false`, anything else → `true` |

Where to edit is in section 7. Parameterised transforms show in the UI as "(custom) …" and survive reopening and saving. But **the UI shows and saves only the first transform per row**. To chain transforms (trim, then to number) you must edit the file, and afterwards must not save that channel's field mapping from the UI.

### 3.4 When a required field is missing

| Policy | Behaviour |
| --- | --- |
| **Return an error to the device** (default) | 400 "field translation failed: missing required field xxx"; **nothing is forwarded to the page** |
| **Skip the field** | The page's data simply lacks that field |
| **Use default** | The value in the "Default" column is inserted |

A field that is **not** marked required ignores this policy when missing: with a default it uses the default, without one it is simply skipped, so a device omitting an optional field never gets the whole record rejected.

A transform error (say "To number" meets `"abc"`) is always treated as an error, and the log names the field.

### 3.5 File checks: 413 for oversized, 415 for unreadable

Device documents often say "the image, base64-decoded, must be under 2 MB and only jpg / png / pdf", with a separate error code for each case.
Add a `check` to the field (config file only for now; location in section 7):

```json
{ "source": "file", "target": "imageBase64", "required": true,
  "check": { "type": "file", "max_bytes": 2097152, "formats": ["jpg", "png", "pdf"] } }
```

| Item | Meaning |
| --- | --- |
| `max_bytes` | Size limit in bytes **after** base64 decoding; 0 or absent = unlimited |
| `formats` | Allowed formats, detected from the file header, not from what the device claims. Known: `jpg` `png` `gif` `bmp` `webp` `pdf`; absent = any |

The check runs **before** field mapping. A failing request is not forwarded and never enters the pairing hold area; the device gets an immediate reply:

| Case | Scenario | Default code |
| --- | --- | --- |
| Decoded size over `max_bytes` | File too large `file_too_large` | 413 |
| Field missing or empty, not valid base64, or format not in `formats` | File unreadable `file_invalid` | 415 |

Codes and messages are changed in the "HTTP responses" table or the channel's `responses` (6.5). A `data:image/png;base64,` prefix is stripped automatically.
In the UI, hovering the source field of a checked row shows the condition, and renaming and saving keeps it. A sample that fails the check turns the preview red, and the self-test's static check blocks it.

### 3.6 Reading the preview

At the bottom, left is the sample, right is the `data` the page will receive. Any edit recomputes immediately; failing rows are described in red below. The preview is the runtime result; there is no second code path.

---

## 4. Send to front end ⚙: the whole message the page receives

### 4.1 Default shape

With this page untouched, the page receives:

```json
{
  "type": "external_data",
  "id": "788164362335fcf5",
  "channel": "sorter_data",
  "channelLabel": "Sorter data",
  "expectReply": true,
  "data": { "expressNo": "CCSL2026083100123", "weight": 12.5 }
}
```

| Field | Meaning |
| --- | --- |
| `type` | Message type, default `external_data` |
| `id` | This message's id. **On two-way channels the front end must send it back unchanged** |
| `channel` / `channelLabel` | Channel key and note, so the page can tell which endpoint the data came from. No note, no `channelLabel` |
| `expectReply` | Present only on two-way channels, always `true`: tells the page it must reply |
| `data` | The data after field mapping; fields merged in by pairing are here too |

### 4.2 What the front end must reply

**This part is not configurable.** Whatever shape you send to the page, what it sends back to Print Bridge is always:

```json
{ "type": "external_reply", "id": "788164362335fcf5", "data": { "route": "A02" } }
```

`id` returned unchanged, `data` is what goes back to the device. On one-way channels the page may reply `{"type": "ack", "id": "..."}` to confirm receipt; not replying is fine. On two-way channels `external_reply` doubles as the ack. With several pages connected, the first reply wins.

### 4.3 Three levels of change, lightest first

| Change | When |
| --- | --- |
| **Message type** | The page already has a protocol keyed on `type` and the name differs |
| **Fixed extra fields** | Every message must carry some constants, e.g. `{"source": "Warehouse A"}`. A JSON object, merged into the top level |
| **Write the whole message** | The page does not even accept the outer `type / id / data`. Tick it and the standard shape is gone; the template decides everything |

### 4.4 Writing the template

```json
{
  "cmd": "newParcel",
  "seq": "{{__id}}",
  "expressNo": "{{expressNo}}",
  "img": "{{imageBase64}}",
  "from": "{{__label}}"
}
```

Two kinds of placeholders:

| Placeholder | Value |
| --- | --- |
| `{{fieldName}}` | A field **after mapping**, e.g. `{{expressNo}}`. In pass-through mode, the device's original names |
| `{{__id}}` | The message id. **Must appear**, otherwise replies cannot be matched; saving is blocked without it |
| `{{__channel}}` / `{{__label}}` / `{{__path}}` | Channel key, note, and the path the device posted to |
| `{{__data}}` | The whole mapped `data` object |

**Type rule**: when a value is **exactly one placeholder**, it is substituted with its original type, so `"weight": "{{weight}}"` yields the number `12.5`. A placeholder inside other text (`"No. {{__id}}"`) is string-joined. An unknown placeholder is left as-is so you can spot the typo.

The bottom of the page previews the whole message live. Invalid JSON, a missing `{{__id}}`, or a reference to a field the mapping does not produce is shown in red and cannot be saved.

### 4.5 Front-end reply sample

"Front-end reply sample" at the bottom of the same page matters only for two-way channels: the built-in mock page answers with it during self-test, and the "Reply to device ⚙" preview uses it. Enter a `data` object the real page would return, e.g. `{"route": "A"}`.

---

## 5. Pairing: merge two endpoints into one record

The device splits one event across two endpoints (an image on `/upload`, values on `/callback`) linked by a shared business number. The page wants one complete record with the image. Configure the "Pairing" page on both channels.

### 5.1 Roles

| Role | Behaviour |
| --- | --- |
| **Not paired** (default) | Each forwarded on its own |
| **Primary** | Waits for the group's attachments, then sends the merged result to the page. Usually the values channel |
| **Attachment** | Stored in a holding area and **answered 200 immediately, not pushed to the page**; the primary collects it. Usually the image channel. Its flow is locked to one-way |

A pair group has **exactly one primary**; attachments may be several. Either may arrive first.

### 5.2 Fill in on both

| Item | Rule |
| --- | --- |
| **Pair group** | Only channels in the same group pair with each other. With a single group any name works, as long as both match |
| **Pair field** | The field that links the two records, **using its post-mapping name**. If the raw names differ (`code` on one, `no` on the other), map both to the same name first |

### 5.3 The primary channel

| Item | Default | Meaning |
| --- | --- | --- |
| **Wait for attachments up to** | 1 s | Shares the timeout budget with waiting for the front-end reply; see section 2 |
| **If they never arrive** | Send anyway | The page still gets the main data, just without the missing fields. Or "Return an error": 504 pairing timeout, nothing sent to the page |

### 5.4 The attachment channel

| Item | Default | Meaning |
| --- | --- | --- |
| **Merge which field** | empty | Empty = merge everything except the pair field into the primary; a name = merge only that field |
| **Merge as** | empty | Empty keeps the name. Set it to rename, e.g. merge `file` as `imageBase64` |
| **Hold for** | 60 s | Discarded if nobody collects it in time; max 3600 s. The holding area keeps at most 200 entries by default, oldest dropped first |

Merging happens after field mapping and before message assembly, so an outbound template can use `{{imageBase64}}` even though it came from the other channel.

### 5.5 Checked when you click "Start server"

A primary with no attachment, an attachment with no primary, several primaries in one group, or mismatched pair fields are all blocked with an explanation. The self-test's static check also trial-pairs the two channels' samples and warns if their business numbers differ.

### 5.6 The one trap you cannot configure around

If the device sends **sequentially on one connection**: main data first, blocks for the response, and only then sends the image, the image can never leave and pairing always times out. Such a device must **send the image first**, or use a separate connection per request.

---

## 6. Reply to device ⚙: the response the device receives

### 6.1 Default shape

```json
{ "success": true, "code": 200, "message": "前端已处理并回复", "forwarded": 1, "data": { "route": "A02" } }
```

On errors `message` becomes `error` and `success` is `false`. `data` exists only when a two-way reply came back.

### 6.2 HTTP status code policy

| Option | Behaviour |
| --- | --- |
| **Follow the business code** (default) | The HTTP status line equals the business code. A timeout is HTTP 504 |
| **Fixed 200** | Always HTTP 200; the business code lives only in the body. For devices that treat any non-200 as a network failure and never read the body |

### 6.3 Return the front-end reply as-is

Tick "When a two-way reply arrives from the front end, return it to the device as-is" and the device receives **exactly the `data` object from the page's `external_reply`**, without the `success / code / message` wrapper.

It only affects the "reply received" case. One-way, no page connected, timeout, and bad JSON have no front-end data to return and still use the envelope or default shape below. Whatever wrapper the page itself put inside `data` is passed through untouched; the program cannot strip it.

### 6.4 Custom response envelope

When the device's API document fixes the field names (`msg` not `message`, no extra fields allowed), tick "Custom response envelope" and write a template. The "Common shapes" dropdown offers three ready-made ones:

```json
{ "success": "{{__success}}", "code": "{{__code}}", "msg": "{{__message}}", "data": "{{__data}}" }
```

| Variable | Value |
| --- | --- |
| `{{__success}}` | Boolean, business code < 400 |
| `{{__code}}` | Number, the business status code |
| `{{__message}}` | The message text from the response table (6.5) |
| `{{__data}}` | The front-end reply on two-way; `null` otherwise |
| `{{__forwarded}}` | How many pages this record was pushed to |
| `{{__scene}}` | The scenario key, e.g. `ok_reply`, `reply_timeout` |
| `{{__channel}}` | Channel key |

Same type rule as outbound templates: a value that is exactly one placeholder keeps its type, so `"code": "{{__code}}"` yields the number `200`. Fields not in the template do not appear; omit `forwarded` and the device never sees it. The template must use `{{__code}}` and `{{__message}}` or it cannot be saved. The preview shows the "two-way reply received" case.

### 6.5 Status codes and messages: per scenario

The "**HTTP responses**" button next to "Receive settings" opens one global table. Changes apply the moment you click OK, no restart:

| Scenario | Default code | When | Placeholders |
| --- | --- | --- | --- |
| Received (one-way) | 200 | Right after forwarding on a one-way channel | |
| Received (two-way) | 200 | After the front-end reply arrives | |
| Body is not valid JSON | 400 | Body failed to parse | `{error}` |
| Body too large | 413 | Body over 16 MB | `{limit}` |
| No front end connected | 503 | Two-way with no page connected | |
| Queue timeout | 503 | Serial mode, turn never came within the timeout | `{timeout}` |
| Reply timeout | 504 | Two-way, page did not reply in time | `{timeout}` |
| No channel for path | 404 | Unmatched path and no catch-all | `{path}` |
| Field translation failed | 400 | Required field missing or transform error; nothing forwarded | `{error}` |
| Pairing timeout | 504 | Primary never got its attachments and "Return an error" is set | `{missing}` `{timeout}` |
| File too large | 413 | A field with a file check decoded to more than the limit | `{limit}` |
| File unreadable | 415 | A checked field is empty, not base64, or the wrong format | `{error}` |

Precedence: **the channel's own override > this global table > factory default**. Per-channel overrides can currently only be set by editing `responses` in the config file; see section 7. Codes must be 100–599; unknown scenarios are ignored.

---

## 7. What it looks like in the config file

An exported `.json`, one channel's full structure (advanced parts only):

```jsonc
{
  "key": "sorter_data",
  "label": "Sorter data",
  "path": "/callback",
  "flow": "twoway",                 // oneway / twoway
  "timeout": 3.0,
  "dispatch": "concurrent",         // concurrent / serial
  "mapping": {
    "mode": "pick",                 // passthrough / pick / merge
    "on_missing": "error",          // error / skip / default
    "fields": [
      { "source": "code",   "target": "expressNo", "transforms": [],         "required": true,  "default": "" },
      { "source": "weight", "target": "weight",    "transforms": ["number"], "required": false, "default": "" },
      { "source": "no",     "target": "orderNo",   "transforms": ["trim", {"op": "prefix", "value": "SF"}] },
      { "source": "file",   "target": "imageBase64", "required": true,
        "check": { "type": "file", "max_bytes": 2097152, "formats": ["jpg", "png", "pdf"] } }
    ]
  },
  "outbound": {
    "envelope": "custom",           // standard / custom
    "type": "external_data",
    "extra": { "source": "Warehouse A" },
    "template": { "cmd": "newParcel", "seq": "{{__id}}", "expressNo": "{{expressNo}}" }
  },
  "pair": {
    "role": "primary",              // none / primary / attach
    "group": "parcel",
    "field": "expressNo",
    "value": "", "rename": "",      // attachment channels only
    "wait": 1.0, "keep": 60.0,
    "on_missing": "send_partial",   // send_partial / error
    "max_pending": 200
  },
  "http_status": "follow_code",     // follow_code / fixed_200
  "reply_raw": false,
  "envelope": { "success": "{{__success}}", "code": "{{__code}}", "msg": "{{__message}}", "data": "{{__data}}" },
  "responses": {                    // per-channel response overrides, only the scenarios you change
    "reply_timeout": { "code": 500, "message": "Server logic error" },
    "ok":            { "message": "OK" }
  },
  "sample":       { "code": "CCSL2026083100123", "weight": "12.50" },
  "reply_sample": { "route": "A" }
}
```

File-only settings with no UI: parameterised and chained transforms (`transforms`), file checks (`check`), per-channel response overrides (`responses`), the holding-area limit (`max_pending`). Edit, then "Import" it back. Bad values never crash the program; they fall back to defaults.

---

## 8. A worked example: the Geometry Sorter

The built-in templates "几何智造分拣设备" (with image) and "几何智造分拣设备（无图片）" (without) are configured straight from the device document
[对接几何智造分拣设备文档](对接几何智造分拣设备文档.md) (Chinese) and can be read as a reference answer:

| Document requirement | Configuration |
| --- | --- |
| Every response is `success / code / msg / data` | Both channels use a custom response envelope |
| Success `msg` is "操作成功" | Channel `responses` override the `ok` / `ok_reply` text |
| The document's 404 "服务器接口错误" / 500 "服务器逻辑错误" | Those are returned by the front-end page. Print Bridge's own error replies are **not overridden**; they keep the default messages with the actual reason: 400 "字段转译失败：缺少必填字段 code", 504 "等待前端回复超时（3s）" and so on, so the cause is visible at a glance |
| `/upload` image ≤ 2 MB, jpg/png/pdf only, 413 / 415 | `file` field carries a `check`; `file_too_large` → 413 "文件超过 2M 限制：…", `file_invalid` → 415 "文件解析失败：…", the reason follows the colon |
| `/callback` returns the sorting result (`route` …) on success | Comes from the front-end page's reply, so this channel is **two-way** with a 3 s timeout (waiting up to 1 s for the image, leaving the front end at least 2 s), and "return the front-end reply as-is" is ticked: the page's reply goes to the device unchanged, without another `success/code/msg` wrapper |
| `/upload` `data` is always `null` | A one-way channel has no front-end reply, so `{{__data}}` is `null` |

## 9. Verifying a change

1. **Look at the previews.** Field mapping, Send to front end, and Reply to device each have a live preview, and the preview is the runtime result.
2. **Run the self-test.** With the server started, open the "Self-test" tab and click "Start self-test". The static check verifies the pair field is a post-mapping name and the envelope has its required fields; the cases send real requests from the samples and compare status codes and body shapes.
3. **Read the log.** Three lines, one per stage:

| Log line | What to check |
| --- | --- |
| `📥 [channel] 接收数据` | The raw data the device sent |
| `📤 已推送给 N 个 WebSocket 客户端` | Forwarded to the page |
| `📨 回给设备 HTTP 200（ok_reply）: {...}` | **The exact status code and body the device received.** After changing the envelope, this line tells you whether it took effect |

4. **Simulate the device from a terminal.** Copy the `curl` command from "How to use this channel" on the Basic page and run it; what the terminal prints is what the device gets.

---

## Appendix: placeholder cheat sheet

| Where | Placeholders |
| --- | --- |
| Outbound template (to the page) | `{{mappedField}}` `{{__id}}` (required) `{{__channel}}` `{{__label}}` `{{__path}}` `{{__data}}` |
| Response envelope (to the device) | `{{__success}}` `{{__code}}` (required) `{{__message}}` (required) `{{__data}}` `{{__forwarded}}` `{{__scene}}` `{{__channel}}` |
| Response table messages | `{error}` `{timeout}` `{limit}` `{path}` `{missing}` (single braces, per scenario) |

Template placeholders use **double braces**; response-message placeholders use **single braces**. Don't mix them.
