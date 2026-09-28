"""配置自测：把当前方案真的跑一遍，逐项核对表现是不是配置里说的那样。

配好通道之后最常见的问题不是"程序有 bug"，而是"配错了"：配对键写成了
转译前的名字、应答外壳少了 msg、两条通道的样例条码对不上……这些在
对话框的静态预览里看不出来，非得真发一条请求才暴露。

两层：
  lint()  静态体检，不发请求，专抓上面那类配置错误
  run()   动态用例，往本机监听地址真实地发 HTTP —— 和设备走的是同一条路，
          端口没绑上、body 超限这类问题只有真发才测得到

前端那一侧：
  · 没有前端连着时，起一个内置假前端连上自己，按通道的 reply_sample 回
  · 有真前端连着时只旁观不回复，测的是整条链路，包括浏览器那边的逻辑
  · 两者绝不同时回复 —— 广播会同时到两边，谁先回算谁的，结果就不可信了

预期值不用人填：用 map_fields / build_outbound / build_body 从配置里算出来，
和对话框预览是同一套函数，所以不会出现"预览对了自测不对"。

不依赖 Qt。界面页签和 tools/selftest.py 命令行调的都是这里。
"""

import asyncio
import json
import re
import threading
import time
import urllib.error
import urllib.request

import websockets

from channels import (
    ANY_PATH, TWOWAY, build_body, duplicate_paths, http_status_for,
    resolve_response, validate_envelope,
)
from data_receiver import SELFTEST_HEADER, parse_addr
from mapping import check_files, ON_ERROR, PASSTHROUGH, build_outbound, map_fields, validate_outbound
from pairing import ATTACH, NONE, PRIMARY, attach_channels, pair_of, role_of
from pairing import merge as merge_attaches

# 预期值里的通配符：消息 id 是服务端随机生成的，错误文案里嵌着解析器的原话，
# 这两样没法事先算出来，只核对"有这么一格"
ANY_ID = "«id»"
ANY_TEXT = "«text»"
ANY_OBJECT = "«object»"     # 旁观真前端时它回什么不知道，只核对"回了一个对象"

NO_ROUTE_PATH = "/__print_bridge_selftest_no_such_path__"
DEFAULT_REPLY = {"selftest": "ok"}   # 双向通道没配 reply_sample 时假前端回这个
GAP = 0.3                             # 并发用例里两条请求错开的秒数
SETTLE = 1.0                          # 请求回来后再等前端那边的报文落地的秒数

ERROR = "error"
WARN = "warn"


# --------------------------------------------------------------------------- #
# 静态体检
# --------------------------------------------------------------------------- #

def _finding(level, channel, text):
    return {"level": level, "channel": (channel or {}).get("key", ""), "text": text}


def _mapped_sample(channel):
    """样例过一遍映射后的结果；没样例返回 None。"""
    sample = channel.get("sample")
    if not isinstance(sample, dict):
        return None, []
    mapped, problems = map_fields(sample, channel.get("mapping"))
    return (mapped if isinstance(mapped, dict) else {}), problems


def lint(channels):
    """不发请求，只看配置。返回问题清单，每条 {level, channel, text}。"""
    findings = []
    channels = list(channels or [])
    if not channels:
        return [_finding(ERROR, None, "一条通道都没有，任何 POST 都会被拒绝")]

    for path in duplicate_paths(channels):
        findings.append(_finding(ERROR, None, f"路径 {path} 配了不止一条通道，只有第一条会生效"))

    mapped_keys = {}
    for channel in channels:
        key = channel["key"]
        mapped, problems = _mapped_sample(channel)
        if mapped is None:
            findings.append(_finding(WARN, channel, "没有样例，动态用例会跳过这条通道"))
        else:
            mapped_keys[key] = set(mapped)
            for text in problems:
                findings.append(_finding(WARN, channel, f"样例过映射有问题：{text}"))

        if channel.get("envelope"):
            for text in validate_envelope(channel["envelope"]):
                findings.append(_finding(ERROR, channel, f"应答外壳：{text}"))
        if channel.get("outbound"):
            for text in validate_outbound(channel["outbound"], mapped_keys.get(key, ())):
                findings.append(_finding(ERROR, channel, f"发给前端的报文：{text}"))

        role = role_of(channel)
        if role != NONE:
            field = pair_of(channel).get("field")
            if not field:
                findings.append(_finding(ERROR, channel, "参与配对但没填配对字段"))
            elif key in mapped_keys and field not in mapped_keys[key]:
                findings.append(_finding(
                    ERROR, channel,
                    f"配对字段 {field} 不在转译后的字段里（有的是：{'、'.join(sorted(mapped_keys[key])) or '无'}）。"
                    "配对键取的是映射之后的名字",
                ))
            if role == ATTACH and channel["flow"] == TWOWAY:
                findings.append(_finding(WARN, channel, "附加通道收下就回，设成双向没有意义"))

        if channel["flow"] == TWOWAY and role != ATTACH and not channel.get("reply_sample"):
            findings.append(_finding(WARN, channel, "双向通道没配回复样例，自测时假前端只能回一个占位对象"))

        if isinstance(channel.get("sample"), dict):
            bad = check_files(channel["sample"], channel.get("mapping"))
            if bad:
                findings.append(_finding(
                    ERROR, channel,
                    f"样例过不了文件校验（{bad[1]}），正常用例会被当成 {bad[0]} 拒掉。"
                    "把样例里的文件换成一张真实的小图片"))

    # 配对组：主报文和附加数据要成对，样例里的配对键值也要对得上
    groups = {}
    for channel in channels:
        if role_of(channel) != NONE:
            groups.setdefault(pair_of(channel)["group"], []).append(channel)
    for group, members in groups.items():
        primaries = [c for c in members if role_of(c) == PRIMARY]
        attaches = [c for c in members if role_of(c) == ATTACH]
        if attaches and not primaries:
            findings.append(_finding(
                ERROR, attaches[0], f"配对组 {group} 只有附加通道没有主报文，数据永远到不了前端"))
        if primaries and not attaches:
            findings.append(_finding(WARN, primaries[0], f"配对组 {group} 只有主报文没有附加通道，配对不起作用"))
        if len(primaries) > 1:
            findings.append(_finding(ERROR, primaries[1], f"配对组 {group} 有 {len(primaries)} 条主报文，一组只能有一条"))
        if primaries and attaches:
            primary = primaries[0]
            p_mapped, _ = _mapped_sample(primary)
            p_value = (p_mapped or {}).get(pair_of(primary)["field"])
            for attach in attaches:
                a_mapped, _ = _mapped_sample(attach)
                a_value = (a_mapped or {}).get(pair_of(attach)["field"])
                if p_value is not None and a_value is not None and str(p_value) != str(a_value):
                    findings.append(_finding(
                        ERROR, attach,
                        f"样例里的配对键值和主报文对不上（{a_value!r} ≠ {p_value!r}），动态用例必然配不上"))
    return findings


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #

SINGLE = "single"          # 一条通道发一条
PAIR = "pair"              # 一组配对通道全发，验证合成一条
BAD_JSON = "bad_json"
MISSING = "missing"        # 缺必填字段
NO_ROUTE = "no_route"
TIMEOUT = "timeout"        # 前端不回，看设备侧超时应答

POSITIVE_KINDS = (SINGLE, PAIR)
SLOW_KINDS = (TIMEOUT,)


def _step(channel, body, delay=0.0):
    return {"channel": channel, "path": channel["path"] if channel else NO_ROUTE_PATH,
            "body": body, "delay": delay}


def _case(kind, title, steps, channel=None, parallel=False, reply=True):
    return {"kind": kind, "title": title, "steps": steps, "parallel": parallel,
            "channel": (channel or {}).get("key", ""), "reply": reply}


def _post_path(channel):
    """兜底通道的路径是 *，真发时随便挑一个路径它都接。"""
    return "/selftest" if channel["path"] == ANY_PATH else channel["path"]


def _required_top_level_sources(channel):
    mapping = channel.get("mapping") or {}
    if mapping.get("mode", PASSTHROUGH) == PASSTHROUGH or mapping.get("on_missing", ON_ERROR) != ON_ERROR:
        return []
    return [f["source"] for f in mapping.get("fields") or []
            if f.get("required") and f.get("source")
            and "." not in f["source"] and "[" not in f["source"]]


def plan(channels, negative=True, slow=False):
    """按配置生成用例清单。正向在前、负向在后、慢的最后。"""
    channels = list(channels or [])
    cases = []

    def with_path(channel):
        return {**channel, "path": _post_path(channel)}

    singles = [c for c in channels if role_of(c) == NONE and c.get("sample")]
    for channel in singles:
        cases.append(_case(SINGLE, f"{channel['key']}：发一条样例",
                           [_step(with_path(channel), channel["sample"])], channel))

    groups = {}
    for channel in channels:
        if role_of(channel) != NONE:
            groups.setdefault(pair_of(channel)["group"], []).append(channel)
    for group, members in groups.items():
        primary = next((c for c in members if role_of(c) == PRIMARY), None)
        attaches = [c for c in members if role_of(c) == ATTACH and c.get("sample")]
        if primary is None or not primary.get("sample"):
            continue
        if not attaches:
            cases.append(_case(SINGLE, f"{primary['key']}：发一条样例（组里没有附加通道）",
                               [_step(with_path(primary), primary["sample"])], primary))
            continue
        attach_steps = [_step(with_path(a), a["sample"]) for a in attaches]
        cases.append(_case(
            PAIR, f"配对组 {group}：附加数据先到，再来主报文",
            attach_steps + [_step(with_path(primary), primary["sample"])], primary))
        cases.append(_case(
            PAIR, f"配对组 {group}：主报文先到（并发），附加数据随后",
            [_step(with_path(primary), primary["sample"])]
            + [_step(a["channel"], a["body"], GAP * (i + 1)) for i, a in enumerate(attach_steps)],
            primary, parallel=True))

    if negative:
        for channel in channels:
            cases.append(_case(BAD_JSON, f"{channel['key']}：请求体不是合法 JSON",
                               [_step(with_path(channel), b"{bad json")], channel))
            for source in _required_top_level_sources(channel):
                if isinstance(channel.get("sample"), dict) and source in channel["sample"]:
                    body = {k: v for k, v in channel["sample"].items() if k != source}
                    case = _case(MISSING, f"{channel['key']}：缺必填字段 {source}",
                                 [_step(with_path(channel), body)], channel)
                    # 缺的是带文件校验的字段时，先被文件校验拦下，回的是「文件解析失败」而不是转译失败
                    case["missing_source"] = source
                    cases.append(case)
        if not any(c["path"] == ANY_PATH for c in channels):
            cases.append(_case(NO_ROUTE, "打一个没配过的路径", [_step(None, {"probe": 1})]))

    if slow:
        for case in [c for c in cases if c["kind"] in POSITIVE_KINDS and not c["parallel"]]:
            channel = next((c for c in channels if c["key"] == case["channel"]), None)
            if channel and channel["flow"] == TWOWAY:
                cases.append(_case(
                    TIMEOUT, f"{channel['key']}：前端不回复，等满 {channel['timeout']:g}s",
                    case["steps"], channel, reply=False))
    return cases


# --------------------------------------------------------------------------- #
# 内置假前端 / 旁观者
# --------------------------------------------------------------------------- #

MOCK = "mock"          # 没有别的前端：由它回复
OBSERVE = "observe"    # 有真前端：只记录，不回复也不 ack

_HEX16 = re.compile(r"^[0-9a-f]{16}$")


def _find_id(message):
    """标准报文 id 在固定一格；自定义模板里 {{__id}} 藏在哪不知道，按形状找。"""
    if isinstance(message.get("id"), str):
        return message["id"]
    found = []

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and _HEX16.match(node):
            found.append(node)

    walk(message)
    return found[0] if len(found) == 1 else None


class _Frontend:
    """连到本机 WebSocket 的一个客户端，跑在自己的线程里。"""

    def __init__(self, ws_url, mode):
        self.ws_url = ws_url
        self.mode = mode
        self.received = []          # 收到的每条 JSON 报文（不含 connection）
        self.lock = threading.Lock()
        self.connected = threading.Event()
        self.closed = threading.Event()
        self.error = None
        self.reply_with = None      # 当前用例要回的内容；None = 不回
        self.reply_twoway = False
        self._loop = None
        self._ws = None
        self._thread = threading.Thread(target=self._main, daemon=True)

    def start(self):
        self._thread.start()
        return self.connected.wait(5) and self.error is None

    def stop(self):
        if self._loop is not None and self._ws is not None:
            self._loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._ws.close()))
        self.closed.wait(3)

    def begin(self, reply_with, twoway):
        with self.lock:
            self.reply_with = reply_with
            self.reply_twoway = twoway
            return len(self.received)

    def since(self, index):
        with self.lock:
            return list(self.received[index:])

    def _main(self):
        try:
            asyncio.run(self._run())
        except Exception as e:      # 连不上、握手失败都落到这里
            self.error = str(e)
            self.connected.set()
        finally:
            self.closed.set()

    async def _run(self):
        self._loop = asyncio.get_running_loop()
        async with websockets.connect(self.ws_url, max_size=None) as ws:
            self._ws = ws
            self.connected.set()
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(message, dict) or message.get("type") == "connection":
                    continue
                with self.lock:
                    self.received.append(message)
                    reply_with, twoway = self.reply_with, self.reply_twoway
                if self.mode != MOCK:
                    continue
                msg_id = _find_id(message)
                if not msg_id:
                    continue
                if twoway:
                    if reply_with is not None:
                        await ws.send(json.dumps(
                            {"type": "external_reply", "id": msg_id, "data": reply_with},
                            ensure_ascii=False))
                else:
                    await ws.send(json.dumps({"type": "ack", "id": msg_id}))


# --------------------------------------------------------------------------- #
# 比对
# --------------------------------------------------------------------------- #

def _wild_regex(expected):
    parts = re.split(f"({re.escape(ANY_ID)}|{re.escape(ANY_TEXT)})", expected)
    return re.compile("".join(".+" if p in (ANY_ID, ANY_TEXT) else re.escape(p) for p in parts) + r"\Z",
                      re.S)


def _show(value):
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 80 else text[:38] + "…" + text[-38:]


def diff(expected, actual, where=""):
    """递归比对，返回不一致的地方（空 = 一致）。expected 里可以带通配符。"""
    label = where or "根"
    if expected == ANY_OBJECT:
        return [] if isinstance(actual, dict) else [f"{label}：期望一个对象，实际 {_show(actual)}"]
    if isinstance(expected, str) and (ANY_ID in expected or ANY_TEXT in expected):
        if isinstance(actual, str) and _wild_regex(expected).match(actual):
            return []
        return [f"{label}：期望形如 {expected}，实际 {_show(actual)}"]
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{label}：期望对象，实际 {_show(actual)}"]
        problems = []
        for key in expected.keys() - actual.keys():
            problems.append(f"{label}：少了字段 {key}")
        for key in actual.keys() - expected.keys():
            problems.append(f"{label}：多了字段 {key} = {_show(actual[key])}")
        for key in expected.keys() & actual.keys():
            problems += diff(expected[key], actual[key], f"{where}.{key}" if where else key)
        return problems
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return [f"{label}：期望 {_show(expected)}，实际 {_show(actual)}"]
        problems = []
        for i, (e, a) in enumerate(zip(expected, actual)):
            problems += diff(e, a, f"{where}[{i}]")
        return problems
    if isinstance(expected, bool) or isinstance(actual, bool):
        same = expected is actual
    else:
        same = expected == actual
    return [] if same else [f"{label}：期望 {_show(expected)}，实际 {_show(actual)}"]


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #

def endpoints(profile):
    """从方案里算出自测要打的两个地址：本机 HTTP 和本机 WebSocket。"""
    host, port = parse_addr(str(profile.get("data_addr") or ""))
    if host in ("", "0.0.0.0", "localhost", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{port}", f"ws://127.0.0.1:{int(profile.get('ws_port') or 8765)}"


def _post(base_url, path, body, timeout):
    if not isinstance(body, bytes):
        body = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base_url + path, data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", SELFTEST_HEADER: "1"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except Exception as e:
        return {"status": None, "body": None, "error": str(e), "elapsed": time.monotonic() - started}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except ValueError:
        parsed = raw.decode("utf-8", "replace")
    return {"status": status, "body": parsed, "error": None, "elapsed": time.monotonic() - started}


def probe(base_url):
    """GET 监听地址，返回 (已连接的前端数, 错误文本)。"""
    try:
        with urllib.request.urlopen(base_url + "/", timeout=3) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        return int(info.get("clients", 0)), None
    except Exception as e:
        return 0, str(e)


def _expected_reply(channel, step, scene, http_responses, clients, reply, **fmt):
    code, message = resolve_response(scene, channel, http_responses, **fmt)
    if fmt.get("_wild"):
        message = ANY_TEXT
    extra = None
    if scene == "ok":
        extra = {"forwarded": 0 if role_of(channel) == ATTACH else clients}
    elif scene in ("ok_reply", "reply_timeout"):
        extra = {"forwarded": clients}
        if scene == "ok_reply":
            extra["data"] = reply
    return http_status_for(channel, code), build_body(channel, scene, code, message, extra)


def _check(results, ok, text):
    results.append({"ok": bool(ok), "text": text})
    return ok


def _run_case(case, ctx):
    frontend, base_url, http_responses, clients, channels = (
        ctx["frontend"], ctx["base_url"], ctx["http_responses"], ctx["clients"], ctx["channels"])
    kind = case["kind"]
    channel = next((c for c in channels if c["key"] == case["channel"]), None)
    twoway = bool(channel and channel["flow"] == TWOWAY and role_of(channel) != ATTACH)

    if frontend.mode == MOCK:
        reply = channel.get("reply_sample") or DEFAULT_REPLY if channel else None
    else:
        reply = ANY_OBJECT
    mark = frontend.begin(reply if (case["reply"] and frontend.mode == MOCK) else None, twoway)

    # 发请求：顺序或并发
    timeout = (channel["timeout"] if channel else 10) + 10
    answers = [None] * len(case["steps"])

    def fire(i):
        step = case["steps"][i]
        if step["delay"]:
            time.sleep(step["delay"])
        answers[i] = _post(base_url, step["path"], step["body"], timeout)

    started = time.monotonic()
    if case["parallel"]:
        threads = [threading.Thread(target=fire, args=(i,)) for i in range(len(case["steps"]))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    else:
        for i in range(len(case["steps"])):
            fire(i)
    elapsed = time.monotonic() - started

    # 前端那边的报文比 HTTP 应答晚一点点落地，等它一下
    want_received = 1 if kind in (SINGLE, PAIR, TIMEOUT) else 0
    deadline = time.monotonic() + SETTLE
    while len(frontend.since(mark)) < want_received and time.monotonic() < deadline:
        time.sleep(0.05)
    if want_received:
        time.sleep(0.2)   # 再给一点余量，好抓到"多发了一条"
    received = frontend.since(mark)

    checks = []
    for step, answer in zip(case["steps"], answers):
        who = step["channel"]["key"] if step["channel"] else "未知路径"
        if answer["error"]:
            _check(checks, False, f"POST {step['path']}（{who}）请求失败：{answer['error']}")
            continue
        if kind in (SINGLE, PAIR, TIMEOUT):
            if role_of(step["channel"]) == ATTACH:
                scene = "ok"
            elif kind == TIMEOUT:
                scene = "reply_timeout"
            else:
                scene = "ok_reply" if twoway else "ok"
            fmt = {"timeout": f"{step['channel']['timeout']:g}"} if scene == "reply_timeout" else {}
        elif kind == BAD_JSON:
            scene, fmt = "bad_json", {"_wild": True}
        elif kind == MISSING:
            checked = {f.get("source") for f in ((step["channel"].get("mapping") or {}).get("fields") or [])
                       if f.get("check")}
            scene = "file_invalid" if case.get("missing_source") in checked else "map_failed"
            fmt = {"_wild": True}
        else:
            scene, fmt = "no_route", {"path": step["path"]}
        status, body = _expected_reply(step["channel"], step, scene, http_responses, clients, reply, **fmt)
        _check(checks, answer["status"] == status,
               f"POST {step['path']}（{who}）HTTP 状态码 {answer['status']}，期望 {status}（场景 {scene}）")
        problems = diff(body, answer["body"])
        _check(checks, not problems,
               f"应答 body 符合「{scene}」的形状" if not problems
               else f"应答 body 与预期不符：" + "；".join(problems[:4]))
        if scene in ("ok_reply", "reply_timeout"):
            budget = step["channel"]["timeout"]
            _check(checks, answer["elapsed"] <= budget + 1.0,
                   f"耗时 {answer['elapsed']:.2f}s，预算 {budget:g}s")

    # 前端收到的报文
    if kind in (SINGLE, PAIR, TIMEOUT):
        _check(checks, len(received) == 1,
               f"前端收到 {len(received)} 条报文，期望 1 条" + ("（配对没合并成一条）" if len(received) > 1 else ""))
        if received and channel:
            # 用例里带了附加通道的步骤（配对用例，以及照它复制出来的超时用例），
            # 前端收到的那条就该并进附加字段
            primary_mapped, _ = _mapped_sample(channel)
            attaches = [s["channel"] for s in case["steps"] if role_of(s["channel"]) == ATTACH]
            extra = None
            if attaches:
                found = {a["key"]: _mapped_sample(a)[0] for a in attaches}
                extra = merge_attaches(primary_mapped, found, {a["key"]: pair_of(a) for a in attaches})
            expected, _ = build_outbound(channel, channel["sample"], ANY_ID,
                                         expect_reply=twoway, extra_fields=extra)
            problems = diff(expected, received[0])
            _check(checks, not problems,
                   "前端收到的报文和配置算出来的一致" if not problems
                   else "前端收到的报文与预期不符：" + "；".join(problems[:4]))
    else:
        _check(checks, not received, f"前端没收到报文" if not received
               else f"出错的请求不该转发，但前端收到了 {len(received)} 条")

    return {"case": case, "ok": all(c["ok"] for c in checks), "checks": checks,
            "elapsed": elapsed, "answers": answers, "received": received}


def run(channels, base_url, ws_url, http_responses=None, cases=None,
        negative=True, slow=False, progress=None):
    """跑一遍。返回报告：{mode, clients, lint, results, passed, failed, error}。

    progress(event, payload) 可选，事件依次是 lint / start / result / done，
    界面用它边跑边刷新，命令行用它逐条打印。
    """
    channels = list(channels or [])
    http_responses = http_responses or {}
    report = {"mode": None, "clients": 0, "lint": lint(channels), "results": [],
              "passed": 0, "failed": 0, "skipped": [], "error": None}
    if progress:
        progress("lint", report["lint"])

    clients, error = probe(base_url)
    if error:
        report["error"] = f"连不上 {base_url}：{error}。服务是不是没启动？"
        return report

    frontend = _Frontend(ws_url, MOCK if clients == 0 else OBSERVE)
    if not frontend.start():
        report["error"] = f"连不上 {ws_url}：{frontend.error or '超时'}"
        return report
    report["mode"] = frontend.mode
    report["clients"] = clients + 1

    try:
        # 服务端记的客户端数 = 真前端 + 我们这一个；ok 应答里的 forwarded 按它算
        ctx = {"frontend": frontend, "base_url": base_url, "channels": channels,
               "http_responses": http_responses, "clients": clients + 1}
        for case in (cases if cases is not None else plan(channels, negative, slow)):
            if case["kind"] in SLOW_KINDS and frontend.mode == OBSERVE:
                # 真前端会回复，"不回复"这种事只有假前端演得出来
                report["skipped"].append(case)
                if progress:
                    progress("skip", case)
                continue
            if progress:
                progress("start", case)
            result = _run_case(case, ctx)
            report["results"].append(result)
            report["passed" if result["ok"] else "failed"] += 1
            if progress:
                progress("result", result)
    finally:
        frontend.stop()

    if progress:
        progress("done", report)
    return report
