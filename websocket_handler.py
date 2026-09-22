import asyncio
import base64
import json
from datetime import datetime

import websockets

from data_receiver import (
    record_ack, record_reply, register_websocket, unregister_websocket,
)
from logger import print_log, shorten
from printer_utils import describe_printer, print_job


async def _send(websocket, msg_type, data, **extra):
    payload = {"type": msg_type, "data": data, **extra}
    await websocket.send(json.dumps(payload, ensure_ascii=False))


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


def _handle_text_message(websocket, data, ack_callback):
    """处理 print 之外的文本消息，返回本次打印要用的 timeInfo（没有则 None）。

    ack_callback: (message_id, acked, total, done) -> None，UI 线程用它刷新「推送」列。
    """
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

    if msg_type == "external_reply":
        # 双向数据流：前端把处理结果回传，HTTP 那侧正阻塞着等它答复设备
        msg_id = data.get("id")
        if not msg_id:
            print_log("⚠️ external_reply 消息缺少 id")
            return None
        reply = data.get("data")
        if reply is None:
            # 前端没把结果放进 data：常见是直接铺在顶层，兜底把 type/id 之外的字段当回复
            rest = {k: v for k, v in data.items() if k not in ("type", "id", "data")}
            if rest:
                reply = rest
                print_log(f"⚠️ external_reply 缺少 data 字段，已把顶层其余字段当作回复 id={msg_id}")
            else:
                print_log(f"⚠️ external_reply 的 data 为空，原始消息: "
                          f"{json.dumps(shorten(data), ensure_ascii=False)}")
        shown = json.dumps(shorten(reply), ensure_ascii=False)
        accepted, acked, total, done = record_reply(websocket, msg_id, reply)
        if not accepted:
            print_log(f"⚠️ 回复到得太晚或重复，已忽略 id={msg_id}: {shown}")
            return None
        print_log(f"↩️ 收到前端回复 id={msg_id}: {shown}")
        if ack_callback is not None and total:
            ack_callback(msg_id, acked, total, done)
        return None

    print_log(f"⚠️ 未知消息类型: {msg_type}")
    return None


def _decode_job(data):
    """把 print 消息里的内容还原成要发给打印机的字节。返回 (字节, 错误说明)。"""
    payload = data.get("data")
    if payload is None:
        return None, "缺少 data"

    fmt = (data.get("format") or "base64").lower()
    if fmt == "text":
        return str(payload).encode("utf-8"), None
    if fmt == "base64":
        try:
            return base64.b64decode(payload), None
        except Exception as e:
            return None, f"base64 解码失败：{e}"
    return None, f"不支持的 format: {fmt}"


async def _run_print(websocket, printer, payload, time_info, job_id):
    """真正把一份数据打出去，并把结果回执给前端。"""
    print_log(f"🖨 打印任务 {job_id or '-'} → {describe_printer(printer)}，{len(payload)} 字节")
    success = await asyncio.to_thread(print_job, printer, payload)
    try:
        await _send(
            websocket, "print_status", "✅ 成功" if success else "❌ 失败",
            printer=printer["key"], jobId=job_id,
        )
    except websockets.exceptions.ConnectionClosed:
        pass  # 打完了但前端已经走了，结果只记在日志里
    _log_total_duration(time_info)


async def _reject_print(websocket, reason, printer_key=None, job_id=None):
    print_log(f"⚠️ {reason}")
    try:
        await _send(websocket, "print_status", f"❌ {reason}",
                    printer=printer_key, jobId=job_id)
    except websockets.exceptions.ConnectionClosed:
        pass


async def websocket_handler(websocket, printers, ack_callback=None):
    """printers: printer_utils.make_printer() 的列表，空列表表示没启用自动打印。

    第一台是默认打印机（前端不指定 printer 时用它）。
    """
    print_log("🌐 WebSocket 客户端已连接")
    register_websocket(websocket)

    registry = {p["key"]: p for p in (printers or [])}
    default_printer = (printers or [None])[0]
    time_info = None  # 最近一次收到的时间信息，供下一次打印统计使用

    # 打印任务放到后台跑：三台打印机的任务要能同时进行，
    # 否则在 async for 里逐条 await，后厨那张打完才轮到杯贴。
    jobs = set()

    def spawn(coro):
        task = asyncio.create_task(coro)
        jobs.add(task)
        task.add_done_callback(jobs.discard)

    try:
        await _send(websocket, "connection", "WebSocket 连接成功！")

        async for message in websocket:
            if isinstance(message, bytes):
                # 老格式：裸二进制帧，打到默认打印机
                print_log("📥 收到打印数据（二进制帧）")
                if default_printer is None:
                    spawn(_reject_print(websocket, "未启用自动打印"))
                else:
                    spawn(_run_print(websocket, default_printer, message, time_info, None))
                time_info = None  # 一份时间信息只对应一次打印
                continue

            if not isinstance(message, str):
                print_log("⚠️ 未知消息格式")
                continue

            try:
                data = json.loads(message)
            except Exception as e:
                print_log(f"❌ JSON 解析错误: {e}")
                continue
            if not isinstance(data, dict):
                print_log("⚠️ 非法 JSON 消息")
                continue

            if data.get("type") == "print":
                job_id = data.get("jobId")
                key = data.get("printer") or (default_printer or {}).get("key")
                printer = registry.get(key)
                if printer is None:
                    known = "、".join(registry) or "无"
                    spawn(_reject_print(
                        websocket,
                        f"未启用自动打印" if not registry else f"没有名为 {key} 的打印机（可用：{known}）",
                        key, job_id,
                    ))
                    continue

                payload, error = _decode_job(data)
                if error:
                    spawn(_reject_print(websocket, error, key, job_id))
                    continue

                spawn(_run_print(websocket, printer, payload, data.get("timeInfo"), job_id))
                continue

            print_log("📥 收到消息")
            time_info = _handle_text_message(websocket, data, ack_callback) or time_info

    except websockets.exceptions.ConnectionClosed:
        print_log("❌ WebSocket 连接断开")
    except Exception as e:
        print_log(f"❌ WebSocket 错误: {e}")
    finally:
        unregister_websocket(websocket)
        if jobs:
            # 已经在打的别腰斩，等它打完再收工
            await asyncio.gather(*jobs, return_exceptions=True)
