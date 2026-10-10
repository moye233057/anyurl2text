import logging
import json
import contextvars
import threading
import uuid

# 创建上下文变量
mid_var = contextvars.ContextVar('mid', default='main')

class RequestIdFilter(logging.Filter):
    def filter(self, record):
        """
        从contextvars中获取mid值
        如果 contextvars 中没有值，尝试从当前线程属性获取
        如果当前线程属性中没有值，则使用 main 作为默认值
        作用: 保证一个请求的生命周期的日志都有相同的mid值
        """
        mid = mid_var.get()
        if mid == 'main':
            # 如果 contextvars 中没有值，尝试从当前线程属性获取
            current_thread = threading.current_thread()
            if hasattr(current_thread, '_mid'):
                mid = getattr(current_thread, '_mid')
        record.mid = mid
        return True

class TidStreamHandler(logging.StreamHandler):
    def emit(self, record):
        # 为每个日志生成唯一的tid
        record.tid = str(uuid.uuid4())
        super().emit(record)

class JsonFormatter(logging.Formatter):

    def format(self, record):
        log_record = {
            'timestamp': self.formatTime(record, '%Y-%m-%d %H:%M:%S'),
            'mid': record.mid,
            'level': record.levelname,
            'message': record.getMessage(),
            'module': record.module,
            'funcName': record.funcName,
            'lineno': record.lineno,
            'pid': record.process,
            'processName': record.processName,
            'tid': record.tid
        }
        return json.dumps(log_record, ensure_ascii=False)

class UvicornAccessMetricsFilter(logging.Filter):
    def filter(self, record):
        """
        仅过滤 uvicorn.access 的 /metrics 访问日志，不影响其他接口访问日志。
        Uvicorn 会在访问日志 record 上附加 request_line，例如: 'GET /metrics HTTP/1.1'
        兼容性处理：若无 request_line，则回退到 getMessage。
        """
        request_line = getattr(record, 'request_line', None)
        text = request_line if isinstance(request_line, str) else record.getMessage()
        # 只对 /metrics 或 /metrics/ 命中，避免误伤其他包含 metrics 的路径
        if '"' in text:
            # 形如: "GET /metrics HTTP/1.1"
            try:
                parts = text.split('"')
                core = parts[1] if len(parts) > 1 else text
            except Exception:
                core = text
        else:
            core = text
        return not (" GET /metrics " in f" {core} " or " GET /metrics/ " in f" {core} ")

def setup_logger(name, log_file):
    # 获取logger，如果已存在则直接返回
    logger = logging.getLogger(name)

    # 如果logger已经有处理器，说明已经设置过，直接返回
    if logger.handlers:
        return logger

    # 当前服务只使用 info/error 两类业务日志，logger 级别收敛到 INFO。
    logger.setLevel(logging.INFO)

    # 防止日志传播到根记录器，避免重复输出
    logger.propagate = False

    # 添加自定义过滤器
    logger.addFilter(RequestIdFilter())

    # 创建控制台处理器（使用TidStreamHandler确保每个日志都有唯一tid）
    console_handler = TidStreamHandler()
    console_handler.setLevel(logging.INFO)

    # 创建并设置JSON格式化器
    json_formatter = JsonFormatter()
    console_handler.setFormatter(json_formatter)

    # 只添加控制台处理器
    logger.addHandler(console_handler)

    # 为 uvicorn.access 添加仅过滤 /metrics 的过滤器
    uvicorn_access_logger = logging.getLogger('uvicorn.access')
    uvicorn_access_logger.addFilter(UvicornAccessMetricsFilter())

    return logger

logger = setup_logger("http_server_log","application.log")
