"""Sparrow 统一日志配置"""

import sys
from loguru import logger

from src.config import settings

# 移除默认 handler
logger.remove()

# 控制台输出
logger.add(
    sys.stderr,
    level=settings.log_level,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <cyan>{name}</cyan> - {message}",
)

# 文件输出（按天轮转）
logger.add(
    "logs/sparrow_{time:YYYY-MM-DD}.log",
    level="DEBUG",
    rotation="00:00",
    retention="30 days",
    encoding="utf-8",
)

__all__ = ["logger"]
