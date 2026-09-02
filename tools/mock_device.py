"""模拟几何智造分拣设备：按对接文档往 print_bridge 打 /upload 和 /callback。

顺便逐条校验返回结构是否符合文档（success / code / msg / data），
不符合的地方直接列出来——这份清单就是待适配项。

    python tools/mock_device.py                     # 先传图，再推数据
    python tools/mock_device.py --order data-first  # 反过来，验证先到后到都能配上
    python tools/mock_device.py --no-image          # 只推数据，看没有图片时的表现
    python tools/mock_device.py --image ./label.jpg # 用真实图片
"""

import argparse
import base64
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# 文档 2.4.1 里那张 1x1 PNG
TINY_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
            "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

SAMPLE = {
    "code": "CCSL2026083100123",
    "weight": "12.50",
    "height": "35.20",
    "width": "28.00",
    "length": "40.75",
}


def post(base, path, payload, timeout):
    """发一条 POST，返回 (HTTP 状态码, 响应体 str, 耗时秒)。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}", data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), time.monotonic() - started
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), time.monotonic() - started
    except Exception as e:
        return None, f"<请求失败: {e}>", time.monotonic() - started


def check_envelope(raw, expect_data_object):
    """按文档 0. 通用说明校验返回结构，返回问题清单。"""
    problems = []
    try:
        body = json.loads(raw)
    except Exception:
        return [f"返回不是合法 JSON: {raw[:120]}"], None

    if not isinstance(body, dict):
        return ["返回不是 JSON 对象"], None

    if "success" not in body:
        problems.append("缺少必选字段 success")
    elif not isinstance(body["success"], bool):
        problems.append(f"success 应为布尔值，实际是 {type(body['success']).__name__}")

    if "code" not in body:
        problems.append("缺少必选字段 code")
    elif not isinstance(body["code"], int):
        problems.append(f"code 应为数字，实际是 {type(body['code']).__name__}")

    if "msg" not in body:
        found = [k for k in ("message", "error", "msg") if k in body]
        problems.append(f"缺少必选字段 msg（实际用的是 {found or '无'}）")

    if expect_data_object and not isinstance(body.get("data"), dict):
        problems.append(f"data 应为分拣结果对象，实际是 {body.get('data')!r}")

    return problems, body


def show(title, path, status, raw, seconds, expect_data_object):
    print(f"\n── {title}  POST {path} ──")
    print(f"HTTP {status}   耗时 {seconds:.2f}s")
    try:
        pretty = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except Exception:
        pretty = raw
    print(pretty[:800])

    problems, body = check_envelope(raw, expect_data_object)
    if problems:
        print("❌ 与文档不符：")
        for p in problems:
            print(f"   · {p}")
    else:
        print("✅ 返回结构符合文档")
    return problems, body


def main():
    p = argparse.ArgumentParser(description="模拟几何智造分拣设备")
    p.add_argument("--base", default="http://127.0.0.1:13213", help="print_bridge 的 HTTP 地址")
    p.add_argument("--code", default=SAMPLE["code"], help="条码值，两个接口靠它配对")
    p.add_argument("--image", help="真实图片路径，不给则用文档里那张 1x1 PNG")
    p.add_argument("--order", choices=["image-first", "data-first"], default="image-first",
                   help="两条请求的先后顺序")
    p.add_argument("--gap", type=float, default=0.3, help="两条请求之间隔几秒")
    p.add_argument("--parallel", action="store_true",
                   help="两条请求各走各的连接（真实设备通常如此）。\n"
                        "配对模式下 data-first 必须用它：/callback 会阻塞等图片，"
                        "顺序发的话永远轮不到发图那一步")
    p.add_argument("--no-image", action="store_true", help="只推数据，不传图")
    p.add_argument("--timeout", type=float, default=30, help="等待响应的秒数")
    args = p.parse_args()

    if args.image:
        file_b64 = base64.b64encode(Path(args.image).read_bytes()).decode()
        print(f"🖼 使用 {args.image}（base64 {len(file_b64)} 字符）")
    else:
        file_b64 = TINY_PNG

    data_payload = {**SAMPLE, "code": args.code}
    upload_payload = {"code": args.code, "file": file_b64}

    steps = []
    if not args.no_image:
        steps.append(("上传接口", "/upload", upload_payload, False))
    steps.append(("数据推送接口", "/callback", data_payload, True))
    if args.order == "data-first":
        steps.reverse()

    how = "并发" if args.parallel else "顺序"
    print(f"🎯 目标 {args.base}   条码 {args.code}   顺序 {args.order}（{how}）")

    all_problems, results = [], []
    if args.parallel and len(steps) > 1:
        # 每条各起一个线程：第二条不必等第一条回来。配对模式下 /callback
        # 会阻塞着等图片，顺序发的话那张图永远发不出去。
        answers = [None] * len(steps)

        def fire(index, delay):
            time.sleep(delay)
            title, path, payload, _ = steps[index]
            answers[index] = post(args.base, path, payload, args.timeout)

        threads = [threading.Thread(target=fire, args=(i, i * args.gap))
                   for i in range(len(steps))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for i, (title, path, payload, expect_data) in enumerate(steps):
            status, raw, seconds = answers[i]
            problems, body = show(title, path, status, raw, seconds, expect_data)
            all_problems += [f"{path}: {x}" for x in problems]
            results.append((path, body))
    else:
        for i, (title, path, payload, expect_data) in enumerate(steps):
            if i:
                time.sleep(args.gap)
            status, raw, seconds = post(args.base, path, payload, args.timeout)
            problems, body = show(title, path, status, raw, seconds, expect_data)
            all_problems += [f"{path}: {x}" for x in problems]
            results.append((path, body))

    print("\n" + "=" * 60)
    for path, body in results:
        if path == "/callback" and isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, dict) and data.get("route"):
                print(f"✅ 拿到分拣结果 route={data['route']}")
            else:
                print("❌ 没拿到 route——设备不知道该把包裹分到哪个格口")

    if all_problems:
        print(f"\n❌ 共 {len(all_problems)} 处与文档不符：")
        for x in all_problems:
            print(f"   · {x}")
        sys.exit(1)
    print("\n✅ 全部符合文档")


if __name__ == "__main__":
    main()
