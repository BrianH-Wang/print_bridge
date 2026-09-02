"""模拟前端浏览器：连上 print_bridge 的 WebSocket，收到数据就回一条分拣结果。

浏览器还没开发好之前，用它顶上——否则双向通道永远等不到回复，
设备侧只会看到 504，分不清是我们的问题还是前端没接。

    python tools/mock_frontend.py --port 8765
    python tools/mock_frontend.py --port 8765 --route B --delay 2
"""

import argparse
import asyncio
import json

import websockets


def build_reply(incoming, args):
    """按几何智造文档 1.3.1 的 data 对象回一份分拣结果。"""
    reply = {
        "route": args.route,
        "totalCount": args.total,
        "currentCount": args.current,
        "mainOrderNo": f"MO{incoming.get('code', '')}",
    }
    if args.pdf:
        # 真实场景这里是标签 PDF 的 base64，模拟时给一个最小合法 PDF
        reply["pdfBase64"] = "JVBERi0xLjQKJcOkw7zDtsOfCg=="
    return reply


def unwrap(message):
    """从收到的报文里取出业务数据，兼容 {type,data} 外壳和平铺两种形状。"""
    if not isinstance(message, dict):
        return {}
    data = message.get("data")
    return data if isinstance(data, dict) else message


async def run(args):
    url = f"ws://{args.host}:{args.port}"
    async with websockets.connect(url, max_size=None) as ws:
        print(f"✅ 已连上 {url}，等待数据…（Ctrl+C 退出）")
        async for raw in ws:
            if isinstance(raw, bytes):
                print(f"📥 二进制帧 {len(raw)} 字节（打印数据）")
                continue

            try:
                message = json.loads(raw)
            except Exception:
                print(f"📥 非 JSON 文本: {raw[:120]}")
                continue

            msg_type = message.get("type")
            msg_id = message.get("id")
            body = unwrap(message)
            preview = {k: (f"<{len(v)} 字符>" if isinstance(v, str) and len(v) > 60 else v)
                       for k, v in body.items()} if isinstance(body, dict) else body
            print(f"📥 type={msg_type} id={msg_id} {json.dumps(preview, ensure_ascii=False)}")

            # 有没有图片字段——用来验证「配对合流」到底有没有生效。
            # 字段名可能被转译改过，所以按常见名字都试一遍。
            if isinstance(body, dict) and msg_type != "connection":
                for key in ("file", "image", "imageBase64", "img", "pdfBase64"):
                    value = body.get(key)
                    if isinstance(value, str) and value:
                        print(f"   🖼 带图片字段 {key}（{len(value)} 字符）")
                        break
                else:
                    print("   ⚠️ 没有图片字段")

            if not msg_id:
                continue

            if message.get("expectReply"):
                if args.delay:
                    print(f"   ⏳ 假装处理 {args.delay}s…")
                    await asyncio.sleep(args.delay)
                if args.no_reply:
                    print("   🚫 --no-reply：故意不回，用来看设备侧的超时表现")
                    continue
                reply = build_reply(body, args)
                await ws.send(json.dumps(
                    {"type": "external_reply", "id": msg_id, "data": reply},
                    ensure_ascii=False,
                ))
                print(f"   ↩️ 已回复 route={reply['route']}")
            else:
                await ws.send(json.dumps({"type": "ack", "id": msg_id}))
                print("   📬 已 ack")


def main():
    p = argparse.ArgumentParser(description="模拟前端浏览器（WebSocket 客户端）")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=8765, help="print_bridge 的 WebSocket 端口")
    p.add_argument("--route", default="A", help="回给设备的分拣格口代码")
    p.add_argument("--total", type=int, default=100, help="回给设备的总件数")
    p.add_argument("--current", type=int, default=1, help="回给设备的当前过机件数")
    p.add_argument("--pdf", action="store_true", help="回复里带 pdfBase64")
    p.add_argument("--delay", type=float, default=0, help="回复前假装处理几秒")
    p.add_argument("--no-reply", action="store_true", help="收到也不回，测超时")
    args = p.parse_args()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n👋 已退出")


if __name__ == "__main__":
    main()
