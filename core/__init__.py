# -*- coding: utf-8 -*-
"""core 包：基础工具模块（路径、日志、通用函数）"""

from .app_paths import get_app_dir
from .conn_logger import ConnLogger, conn_logger, qt_message_handler
from .utils import (
    classify_conn_error, natural_sort_key, safe_close_transport,
    RETRYABLE_KEYWORDS, RETRY_MAX, RETRY_DELAY,
)
