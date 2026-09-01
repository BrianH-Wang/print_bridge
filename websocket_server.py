import asyncio
import platform

import websockets

from logger import print_log
from websocket_handler import websocket_handler


async def _run(port, printers, stop_event, ready_event, ack_callback=None):
    host = "localhost" if platform.system() == "Windows" else "0.0.0.0"
    try:
        async with websockets.serve(
            lambda ws: websocket_handler(ws, printers, ack_callback),
            host=host,
            port=port,
            max_size=None,
        ):
            if ready_event is not None:
                ready_event.set()  # 绑定成功，界面据此确认服务真的起来了
            print_log(f"✅ WebSocket 监听启动: ws://{host}:{port}")
            # 轮询而非 run_in_executor(stop_event.wait)：后者会占住一个
            # 非守护线程，进程异常退出时 atexit join 不掉，端口会一直被占着
            while not stop_event.is_set():
                await asyncio.sleep(0.2)
            print_log("🛑 WebSocket 服务已停止")
    except OSError as e:
        print_log(f"❌ 无法启动 WebSocket 服务 (端口 {port}): {e}")
    except Exception as e:
        print_log(f"❌ WebSocket 服务异常: {e}")


def start_websocket_server(port, printers, stop_event, ready_event=None, ack_callback=None):
    """阻塞运行 WebSocket 服务，直到 stop_event 被置位。

    printers: printer_utils.make_printer() 的列表，第一台是默认打印机；
              空列表表示没启用自动打印。
    """
    asyncio.run(_run(port, printers, stop_event, ready_event, ack_callback))
