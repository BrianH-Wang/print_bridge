import asyncio
import json
from datetime import datetime

import websockets

from data_receiver import record_ack, register_websocket, unregister_websocket
from logger import print_log
from printer_utils import print_pdf_file, save_pdf_bytes


async def _send(websocket, msg_type, data):
    await websocket.send(json.dumps({"type": msg_type, "data": data}, ensure_ascii=False))


def _log_total_duration(time_info):
    """根据前端上报的 startTime 统计端到端耗时；缺失或非法时安静跳过。"""
    start_ms = (time_info or {}).get("startTime")
    if not start_ms:
        return
    try:
        start_dt = datetime.fromtimestamp(start_ms / 1000)
    except (TypeError, ValueError, OSError):
        print_log(f"⚠️ 无法解析 startTime: {start_ms}")
        return
    total = (datetime.now() - start_dt).total_seconds() * 1000
    print_log(f"📊 总耗时: {total:.1f} 毫秒")


def _handle_text_message(websocket, message, ack_callback):
    """处理文本消息，返回本次打印要用的 timeInfo（没有则返回 None）。

    ack_callback: (message_id: str) -> None，UI 线程会传进来用于刷新「推送」列。
    """
    try:
        data = json.loads(message)
    except Exception as e:
        print_log(f"❌ JSON 解析错误: {e}")
        return None

    if not isinstance(data, dict):
        print_log("⚠️ 非法 JSON 消息")
        return None

    msg_type = data.get("type")
    if msg_type == "printWithTime":
        time_info = data.get("timeInfo") or {}
        print_log(f"🕒 接口花费时间: {time_info.get('apiTime', '未知')} 毫秒")
        print_log(f"🕒 准备耗时: {time_info.get('prepareTime', '未知')} 毫秒")
        return time_info

    if msg_type == "ack":
        msg_id = data.get("id") or data.get("data", {}).get("id") if isinstance(data.get("data"), dict) else None
        # 旧格式 {type: ack, id: xxx} 和嵌套都兼容
        if not msg_id:
            msg_id = data.get("id")
        if not msg_id:
            print_log("⚠️ ack 消息缺少 id")
            return None
        acked, total, done = record_ack(websocket, msg_id)
        print_log(f"📬 收到 ack ({acked}/{total}) id={msg_id} {'✅ 完成' if done else '⏳ 等待'}")
        if ack_callback is not None:
            ack_callback(msg_id, acked, total, done)
        return None

    print_log(f"⚠️ 未知消息类型: {msg_type}")
    return None


async def _handle_print(websocket, payload, printer_name, time_info):
    if not printer_name:
        print_log("⚠️ 收到 PDF，但「自动打印」未启用，已忽略")
        await _send(websocket, "print_status", "❌ 未启用自动打印")
        return

    try:
        path = save_pdf_bytes(payload)
    except Exception as e:
        print_log(f"❌ 保存 PDF 失败: {e}")
        await _send(websocket, "print_status", "❌ 打印失败")
        return

    # 打印是阻塞调用，放到线程中执行，避免卡住事件循环
    success = await asyncio.to_thread(print_pdf_file, path, printer_name)
    await _send(websocket, "print_status", "✅ 成功" if success else "❌ 失败")
    _log_total_duration(time_info)


async def websocket_handler(websocket, printer_name, ack_callback=None):
    print_log("🌐 WebSocket 客户端已连接")
    register_websocket(websocket)

    time_info = None  # 最近一次收到的时间信息，供下一次打印统计使用

    try:
        await _send(websocket, "connection", "WebSocket 连接成功！")

        async for message in websocket:
            print_log("📥 收到消息")

            if isinstance(message, bytes):
                await _handle_print(websocket, message, printer_name, time_info)
                time_info = None  # 一份时间信息只对应一次打印
            elif isinstance(message, str):
                time_info = _handle_text_message(websocket, message, ack_callback) or time_info
            else:
                print_log("⚠️ 未知消息格式")

    except websockets.exceptions.ConnectionClosed:
        print_log("❌ WebSocket 连接断开")
    except Exception as e:
        print_log(f"❌ WebSocket 错误: {e}")
    finally:
        unregister_websocket(websocket)
