"""配置自测的命令行入口：一条命令把一份配置真的跑一遍。

    python tools/selftest.py --config configs/几何智造分拣设备.json

没有服务在跑就自己在进程里起一个（WebSocket + HTTP，套上这份配置）；
已经有 Print Bridge 在跑就直接打它 —— 这时预期值按文件算、实际表现按
界面里当前的配置来，两边不一致的话报出来的就是这份差别。

    --list          只列用例，不跑
    --no-negative   不跑坏 JSON / 缺字段 / 错路径这些负向用例
    --slow          加跑"前端不回复"的超时用例（每条要等满 timeout 秒）
    --base / --ws   不按配置里的端口，指定要打的地址
"""

import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import selftest                                            # noqa: E402
from config_io import load_channels, parse_import          # noqa: E402

LEVEL_MARK = {selftest.ERROR: "❌", selftest.WARN: "⚠️"}


def serve(profile, channels, http_responses):
    """进程内起一套服务，返回一个 stop() 函数。"""
    from data_receiver import (
        configure_channels, configure_responses, start_http_server, stop_http_server,
    )
    from websocket_server import start_websocket_server

    stop_event, ws_ready, http_ready = threading.Event(), threading.Event(), threading.Event()
    threading.Thread(target=start_websocket_server,
                     args=(int(profile["ws_port"]), [], stop_event, ws_ready), daemon=True).start()
    configure_responses(http_responses)
    configure_channels(channels)
    threading.Thread(target=start_http_server, args=(profile["data_addr"], http_ready),
                     daemon=True).start()
    if not (ws_ready.wait(5) and http_ready.wait(5)):
        print("❌ 起不来服务，端口可能被占着；看上面的日志")
        sys.exit(2)

    def stop():
        stop_event.set()
        stop_http_server()
        time.sleep(0.3)
    return stop


def on_progress(event, payload):
    if event == "lint":
        if payload:
            print("── 静态体检 ──")
            for item in payload:
                who = f"[{item['channel']}] " if item["channel"] else ""
                print(f"{LEVEL_MARK[item['level']]} {who}{item['text']}")
        else:
            print("── 静态体检 ── ✅ 没发现问题")
        print()
    elif event == "start":
        print(f"▶ {payload['title']}", flush=True)
    elif event == "skip":
        print(f"⏭ {payload['title']}  —— 有真前端连着，跳过\n")
    elif event == "result":
        for check in payload["checks"]:
            print(f"   {'✅' if check['ok'] else '❌'} {check['text']}")
        print(f"   {'通过' if payload['ok'] else '失败'}  {payload['elapsed']:.2f}s\n")


def main():
    p = argparse.ArgumentParser(description="按一份配置文件自测 Print Bridge")
    p.add_argument("--config", required=True, help="「导出配置」生成的 json")
    p.add_argument("--base", help="HTTP 地址，默认按配置里的端口打本机")
    p.add_argument("--ws", help="WebSocket 地址，默认按配置里的端口连本机")
    p.add_argument("--list", action="store_true", help="只列用例，不跑")
    p.add_argument("--no-negative", action="store_true", help="不跑负向用例")
    p.add_argument("--slow", action="store_true", help="加跑前端不回复的超时用例")
    p.add_argument("--json", action="store_true", help="结果按 JSON 输出，给脚本用")
    args = p.parse_args()

    profile, http_responses, problems = parse_import(Path(args.config).read_text(encoding="utf-8"))
    if problems:
        for text in problems:
            print(f"❌ {text}")
        sys.exit(2)
    channels = load_channels(profile)
    base_url, ws_url = selftest.endpoints(profile)
    base_url, ws_url = args.base or base_url, args.ws or ws_url

    cases = selftest.plan(channels, negative=not args.no_negative, slow=args.slow)
    if args.list:
        for case in cases:
            print(f"· [{case['kind']}] {case['title']}")
        return

    print(f"🎯 方案「{profile.get('name')}」  {len(channels)} 条通道  HTTP {base_url}  WS {ws_url}")
    _, error = selftest.probe(base_url)
    stop = None
    if error:
        print("ℹ️ 没有服务在跑，进程内起一套")
        stop = serve(profile, channels, http_responses)
    else:
        print("ℹ️ 打的是已经在跑的服务；预期按配置文件算，实际按它当前的配置来")
    print()

    try:
        report = selftest.run(channels, base_url, ws_url, http_responses, cases,
                              progress=None if args.json else on_progress)
    finally:
        if stop:
            stop()

    if args.json:
        slim = {k: v for k, v in report.items() if k != "results"}
        slim["results"] = [{"title": r["case"]["title"], "kind": r["case"]["kind"], "ok": r["ok"],
                            "elapsed": r["elapsed"], "checks": r["checks"]} for r in report["results"]]
        print(json.dumps(slim, ensure_ascii=False, indent=2))
    elif report["error"]:
        print(f"❌ {report['error']}")
    else:
        mode = "内置假前端" if report["mode"] == selftest.MOCK else f"旁观真前端（{report['clients'] - 1} 个）"
        print("=" * 60)
        print(f"{'✅' if not report['failed'] else '❌'} 通过 {report['passed']} / 失败 {report['failed']}  ·  {mode}")

    errors = sum(1 for f in report["lint"] if f["level"] == selftest.ERROR)
    sys.exit(1 if report["error"] or report["failed"] or errors else 0)


if __name__ == "__main__":
    main()
