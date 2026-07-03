"""板块采集器 — 行业板块/概念板块 行情+成分（数据源：东财push2）"""

from datetime import date, timedelta

from sqlalchemy import text

from src.datasource.eastmoney_source import em_get
from src.logger import logger
from src.storage.database import SessionLocal

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def collect_sector_daily(trade_date: date = None) -> int:
    """
    采集行业板块日行情（涨跌幅/上涨下跌家数/领涨股）。

    Returns:
        写入条数
    """
    if trade_date is None:
        trade_date = date.today()

    logger.info(f"采集行业板块行情 [{trade_date}]...")

    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "200", "po": "1", "np": "1",
        "fltt": "2", "invt": "2",
        "fs": "m:90+t:2",  # 东财行业板块
        "fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141",
    }
    headers = {"User-Agent": UA}

    try:
        r = em_get(url, params=params, headers=headers, timeout=15)
        d = r.json()
    except Exception as e:
        logger.error(f"行业板块请求失败: {e}")
        return 0

    items = d.get("data", {}).get("diff", [])
    if not items:
        logger.info("无行业板块数据")
        return 0

    db = SessionLocal()
    count = 0
    try:
        for item in items:
            sector_code = item.get("f12", "")
            sector_name = item.get("f14", "")
            if not sector_code:
                continue

            # 先确保 sector_info 有记录
            stmt_info = text("""
                INSERT INTO sector_info (sector_code, sector_name, sector_type)
                VALUES (:code, :name, 'industry')
                ON CONFLICT (sector_code) DO UPDATE SET
                    sector_name = EXCLUDED.sector_name
            """)
            db.execute(stmt_info, {"code": sector_code, "name": sector_name})

            # 写入板块日行情
            stmt = text("""
                INSERT INTO sector_daily
                    (sector_code, trade_date, change_pct, up_count, down_count,
                     leader_code, leader_name, leader_pct)
                VALUES
                    (:sector_code, :trade_date, :change_pct, :up_count, :down_count,
                     :leader_code, :leader_name, :leader_pct)
                ON CONFLICT (sector_code, trade_date) DO UPDATE SET
                    change_pct = EXCLUDED.change_pct,
                    up_count = EXCLUDED.up_count,
                    down_count = EXCLUDED.down_count,
                    leader_code = EXCLUDED.leader_code,
                    leader_name = EXCLUDED.leader_name,
                    leader_pct = EXCLUDED.leader_pct
            """)
            db.execute(stmt, {
                "sector_code": sector_code,
                "trade_date": trade_date,
                "change_pct": item.get("f3", 0),
                "up_count": item.get("f104", 0),
                "down_count": item.get("f105", 0),
                "leader_code": str(item.get("f140", ""))[:6],
                "leader_name": item.get("f128", ""),
                "leader_pct": item.get("f136", 0),
            })
            count += 1

        db.commit()
        logger.info(f"行业板块行情写入: {count} 条")
    except Exception as e:
        db.rollback()
        logger.error(f"行业板块写入失败: {e}")
    finally:
        db.close()

    return count


def collect_stock_sectors(code: str) -> int:
    """
    采集单只股票所属的板块/概念。

    Returns:
        写入条数
    """
    market_code = 1 if code.startswith("6") else 0
    url = "https://push2.eastmoney.com/api/qt/slist/get"
    params = {
        "fltt": "2", "invt": "2",
        "secid": f"{market_code}.{code}",
        "spt": "3", "pi": "0", "pz": "200", "po": "1",
        "fields": "f12,f14,f3,f128",
    }
    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}

    try:
        r = em_get(url, params=params, headers=headers, timeout=15)
        d = r.json()
    except Exception as e:
        logger.warning(f"[{code}] 板块归属请求失败: {e}")
        return 0

    diff = (d.get("data") or {}).get("diff") or {}
    items = diff.values() if isinstance(diff, dict) else diff

    db = SessionLocal()
    count = 0
    try:
        for it in items:
            sector_code = it.get("f12", "")
            sector_name = it.get("f14", "")
            if not sector_code:
                continue

            # 更新板块信息
            stmt_info = text("""
                INSERT INTO sector_info (sector_code, sector_name)
                VALUES (:code, :name)
                ON CONFLICT (sector_code) DO UPDATE SET
                    sector_name = EXCLUDED.sector_name
            """)
            db.execute(stmt_info, {"code": sector_code, "name": sector_name})

            # 写入成分股映射
            is_leader = (it.get("f128", "") == code)
            stmt = text("""
                INSERT INTO sector_component (sector_code, code, is_leader)
                VALUES (:sector_code, :code, :is_leader)
                ON CONFLICT (sector_code, code) DO UPDATE SET
                    is_leader = EXCLUDED.is_leader,
                    updated_at = NOW()
            """)
            db.execute(stmt, {
                "sector_code": sector_code,
                "code": code,
                "is_leader": is_leader,
            })
            count += 1

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[{code}] 板块成分写入失败: {e}")
        return 0
    finally:
        db.close()

    return count


def _get_all_sector_codes() -> list[dict]:
    """从 sector_info 表获取所有行业板块代码，若无则先采集一次今日数据"""
    db = SessionLocal()
    try:
        result = db.execute(text(
            "SELECT sector_code, sector_name FROM sector_info WHERE sector_type = 'industry'"
        ))
        rows = result.fetchall()
        if rows:
            return [{"code": r[0], "name": r[1]} for r in rows]
    finally:
        db.close()

    # 没有板块列表，先跑一次今日采集来注册板块
    logger.info("sector_info 为空，先采集一次今日行情来注册板块列表...")
    collect_sector_daily()

    db = SessionLocal()
    try:
        result = db.execute(text(
            "SELECT sector_code, sector_name FROM sector_info WHERE sector_type = 'industry'"
        ))
        return [{"code": r[0], "name": r[1]} for r in result.fetchall()]
    finally:
        db.close()


def backfill_sector_history(days: int = 90) -> dict:
    """
    回填行业板块历史日K线（数据源：东财 push2his）。

    通过东财的板块历史K线接口，拉取每个行业板块最近 N 天的日涨跌幅，
    写入 sector_daily 表。

    Args:
        days: 回填天数（默认90天，约3个月）

    Returns:
        {"sectors": 板块数, "records": 总写入条数}
    """
    import time as _time

    sectors = _get_all_sector_codes()
    if not sectors:
        logger.error("无法获取行业板块列表")
        return {"sectors": 0, "records": 0}

    logger.info(f"开始回填 {len(sectors)} 个行业板块的历史K线（{days}天）...")

    db = SessionLocal()
    total_records = 0

    try:
        for i, sector in enumerate(sectors):
            sector_code = sector["code"]
            sector_name = sector["name"]

            # 东财板块历史K线接口
            # secid: 90.板块代码 (90=行业板块市场)
            url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            params = {
                "secid": f"90.{sector_code}",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",  # 日K线
                "fqt": "1",
                "beg": (date.today() - timedelta(days=days + 30)).strftime("%Y%m%d"),
                "end": "20500101",
                "lmt": str(days + 30),
            }

            try:
                r = em_get(url, params=params, headers={"User-Agent": UA}, timeout=15)
                data = r.json()
            except Exception as e:
                logger.warning(f"  [{sector_name}] 历史K线请求失败: {e}")
                _time.sleep(2)  # 失败后多等一会
                continue

            klines = (data.get("data") or {}).get("klines") or []
            if not klines:
                continue

            count = 0
            for kline_str in klines:
                # 格式: "2026-06-25,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率"
                parts = kline_str.split(",")
                if len(parts) < 9:
                    continue

                try:
                    trade_date = parts[0]
                    change_pct = float(parts[8]) if parts[8] else 0
                    turnover = float(parts[6]) if parts[6] else 0
                except (ValueError, IndexError):
                    continue

                stmt = text("""
                    INSERT INTO sector_daily (sector_code, trade_date, change_pct, turnover)
                    VALUES (:sector_code, :trade_date, :change_pct, :turnover)
                    ON CONFLICT (sector_code, trade_date) DO UPDATE SET
                        change_pct = EXCLUDED.change_pct,
                        turnover = COALESCE(EXCLUDED.turnover, sector_daily.turnover)
                """)
                db.execute(stmt, {
                    "sector_code": sector_code,
                    "trade_date": trade_date,
                    "change_pct": change_pct,
                    "turnover": turnover,
                })
                count += 1

            db.commit()
            total_records += count

            # 每个请求后暂停，避免限流
            _time.sleep(1.5)

            if (i + 1) % 20 == 0:
                logger.info(f"  进度: {i+1}/{len(sectors)} 个板块, 累计 {total_records} 条")
                _time.sleep(2)  # 批间额外暂停

        logger.info(f"行业板块历史回填完成: {len(sectors)}个板块, {total_records}条记录")
    except Exception as e:
        db.rollback()
        logger.error(f"行业板块历史回填异常: {e}")
    finally:
        db.close()

    return {"sectors": len(sectors), "records": total_records}
