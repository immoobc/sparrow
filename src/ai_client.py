"""AI 分析客户端 — 基于阿里云百炼(DashScope) qwen-max

功能:
- 市场趋势分析（结合行情数据）
- 行业轮动解读
- 全球联动分析（美/日/韩股市对A股的影响）
- 策略执行建议

配置: .env 文件中 AI_API_KEY 和 AI_BASE_URL
"""

import json
import os
import requests

from src.config import settings
from src.logger import logger


# AI 配置（从环境变量/.env读取）
AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "qwen-max")


MARKET_SYSTEM_PROMPT = """你是一个专业的A股投资分析师，擅长：
1. 分析市场行情数据，找出趋势和拐点
2. 解读行业轮动规律，判断行业所处阶段
3. 分析全球资金流动对A股的影响（美股/日股/黄金/美债）
4. 给出简洁、可操作的投资建议

回答要求：
- 用大白话解释，假设读者是投资小白
- 给出明确的结论和建议，不要模棱两可
- 如果数据不足以得出结论，直接说"数据不够，暂时看不清"
- 控制在300字以内
"""


def ai_analyze(question: str, data_context: str = "", system_prompt: str = None) -> str:
    """
    调用AI分析市场数据。

    Args:
        question: 分析问题
        data_context: 数据上下文（JSON/文本形式的行情数据）
        system_prompt: 自定义系统提示词

    Returns:
        AI回答文本
    """
    if not AI_API_KEY:
        return "⚠️ AI功能未配置。请在 .env 文件中设置 AI_API_KEY（阿里云百炼API密钥）。"

    messages = [
        {"role": "system", "content": system_prompt or MARKET_SYSTEM_PROMPT},
    ]

    if data_context:
        user_content = f"以下是当前市场数据：\n\n{data_context}\n\n---\n\n{question}"
    else:
        user_content = question

    messages.append({"role": "user", "content": user_content})

    try:
        resp = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {AI_API_KEY}",
            },
            json={
                "model": AI_MODEL,
                "messages": messages,
                "max_tokens": 1024,
                "temperature": 0.7,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except requests.exceptions.Timeout:
        return "⚠️ AI响应超时，请稍后再试。"
    except Exception as e:
        logger.error(f"AI调用失败: {e}")
        return f"⚠️ AI调用失败: {str(e)[:100]}"


def analyze_market_overview(market_data: dict) -> str:
    """
    分析全球市场概况并给出A股操作建议。

    Args:
        market_data: {
            "us": {"name": "标普500", "change": +1.2},
            "japan": {"name": "日经225", "change": -0.5},
            "a_share": {"name": "沪深300", "change": +0.8},
            "gold": {"name": "黄金", "change": +0.3},
            "sectors": [{"name": "AI", "change": +3.2}, ...]
        }
    """
    context = json.dumps(market_data, ensure_ascii=False, indent=2)
    question = (
        "根据以上全球市场数据：\n"
        "1. 当前全球资金流向哪里？（风险资产还是避险资产）\n"
        "2. 外围市场对明天A股有什么影响？\n"
        "3. A股哪些板块可能受益/受损？\n"
        "4. 给我一个简单的操作建议（今天该不该动）"
    )
    return ai_analyze(question, context)


def analyze_sector_rotation(sector_data: list) -> str:
    """
    分析行业轮动，判断哪些行业处于什么阶段。

    Args:
        sector_data: [{name, change_today, change_5d, change_20d}, ...]
    """
    context = json.dumps(sector_data[:20], ensure_ascii=False, indent=2)
    question = (
        "根据以上行业涨跌数据：\n"
        "1. 哪些行业处于启动初期（值得关注）？\n"
        "2. 哪些行业已经过热（应该回避）？\n"
        "3. 哪些行业处于底部（未来可能启动）？\n"
        "4. 如果只能选一个行业买入，你推荐哪个？为什么？"
    )
    return ai_analyze(question, context)
