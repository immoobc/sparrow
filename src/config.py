"""Sparrow 全局配置"""

from pathlib import Path
from pydantic_settings import BaseSettings


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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
