"""Sparrow 全局配置"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings


def _detect_total_memory_mb() -> int:
    """检测系统总内存(MB)，用于自动调整资源策略"""
    try:
        mem_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return int(mem_bytes / 1024 / 1024)
    except (ValueError, AttributeError):
        # macOS / Windows fallback
        try:
            import psutil
            return int(psutil.virtual_memory().total / 1024 / 1024)
        except ImportError:
            return 2048  # 保守默认 2GB


class Settings(BaseSettings):
    """应用配置，从 .env 文件或环境变量读取"""

    # 数据库
    database_url: str = "postgresql://sparrow:sparrow123@localhost:5432/sparrow"

    # 东财限流
    em_min_interval: float = 1.0
    em_batch_interval: float = 1.5
    em_daily_limit: int = 5000

    # 日志
    log_level: str = "INFO"

    # 数据存储
    data_dir: Path = Path("./data")

    # AI 客户端
    ai_api_key: str = ""
    ai_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ai_model: str = "qwen-max"

    # ── 资源控制 (可通过 .env 覆盖，不设置则自动感知) ──
    # Parquet 导出时每批从 PG 读取的行数 (0=自动)
    parquet_chunk_rows: int = 0
    # 全市场采集时的批大小 (0=自动)
    collect_batch_size: int = 0
    # SQLAlchemy 连接池大小 (0=自动)
    db_pool_size: int = 0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def total_memory_mb(self) -> int:
        return _detect_total_memory_mb()

    @property
    def is_low_memory(self) -> bool:
        """2GB 及以下视为低配"""
        return self.total_memory_mb <= 2200

    @property
    def effective_parquet_chunk_rows(self) -> int:
        """Parquet 导出每批行数: 低配 50k，高配 500k"""
        if self.parquet_chunk_rows > 0:
            return self.parquet_chunk_rows
        return 50_000 if self.is_low_memory else 500_000

    @property
    def effective_collect_batch_size(self) -> int:
        """全市场采集批大小: 低配 50，高配 200"""
        if self.collect_batch_size > 0:
            return self.collect_batch_size
        return 50 if self.is_low_memory else 200

    @property
    def effective_db_pool_size(self) -> int:
        """连接池: 低配 2+3，高配 5+10"""
        if self.db_pool_size > 0:
            return self.db_pool_size
        return 2 if self.is_low_memory else 5


settings = Settings()
