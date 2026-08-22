"""基于队列的日志封装：各工作线程写入队列，GUI 线程定时取出展示。"""

from datetime import datetime

_log_queue = None

# 超过 LOG_STR_LIMIT 的字符串在日志中折叠为「头部....尾部」
LOG_STR_LIMIT = 60
LOG_STR_HEAD = 20
LOG_STR_TAIL = 20


def init_logger(log_queue):
    """由 GUI 在启动时调用一次，之后所有线程共享同一个队列。"""
    global _log_queue
    _log_queue = log_queue


def shorten(value, limit=LOG_STR_LIMIT, head=LOG_STR_HEAD, tail=LOG_STR_TAIL):
    """折叠数据中的超长字符串，避免 base64 图片之类的字段把日志刷屏。

    只用于日志展示，不影响实际转发给前端的数据。dict / list 会递归处理。
    """
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return f"{value[:head]}....{value[-tail:]}[{len(value)}字符]"
    if isinstance(value, dict):
        return {k: shorten(v, limit, head, tail) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [shorten(v, limit, head, tail) for v in value]
    return value


def print_log(message):
    """写一条带时间戳的日志；未初始化队列时回退到标准输出。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    if _log_queue is None:
        print(line)
    else:
        _log_queue.put(line)
