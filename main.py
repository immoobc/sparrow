"""
Sparrow 量化数据基座 — 主入口

用法:
    python main.py init             # 初始化数据库(建表)
    python main.py stock-list       # 采集全市场股票列表
    python main.py calendar         # 生成交易日历
    python main.py backfill         # 回填全历史日K线
    python main.py daily            # 每日增量采集(K线)
    python main.py valuation        # 采集估值数据
    python main.py fund-flow        # 采集资金流向(全市场,耗时长)
    python main.py northbound       # 采集北向资金
    python main.py dragon-tiger     # 采集龙虎榜
    python main.py hot-stocks       # 采集强势股归因
    python main.py sector           # 采集行业板块
    python main.py lockup           # 采集解禁日历
    python main.py check            # 数据质量检查
    python main.py status           # 查看数据库状态
    python main.py scheduler        # 启动定时调度器
    python main.py smoke-test       # 数据源连通性测试
    python main.py app              # 启动 Streamlit Web 界面
    python main.py signal           # 生成今日实盘交易信号
    python main.py auto-update      # 自动更新(采集+缓存,用于定时任务)
    python main.py cron-install     # 安装系统定时任务(每天自动更新)
"""

import sys

from src.logger import logger


def _get_stock_codes() -> list[str]:
    """获取全市场活跃股票代码"""
    from sqlalchemy import text
    from src.storage.database import SessionLocal

    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT code FROM stock_basic WHERE is_active = TRUE ORDER BY code")
        )
        return [row[0].strip() for row in result]
    finally:
        db.close()


def cmd_init():
    """初始化数据库"""
    from scripts.setup_db import main as setup_main
    setup_main()


def cmd_stock_list():
    """采集股票列表"""
    from src.collector.stock_list_collector import collect_stock_list
    count = collect_stock_list()
    logger.info(f"采集完成: {count} 只股票")


def cmd_calendar():
    """生成交易日历"""
    from src.collector.calendar_collector import generate_trade_calendar
    count = generate_trade_calendar()
    logger.info(f"交易日历: {count} 天")


def cmd_backfill():
    """回填历史K线"""
    from src.collector.daily_kline_collector import backfill_all
    codes = _get_stock_codes()
    if not codes:
        logger.error("stock_basic 为空，请先执行: python main.py stock-list")
        return
    result = backfill_all(codes, skip_existing=True)
    logger.info(f"回填结果: {result}")


def cmd_daily():
    """每日增量采集K线"""
    from src.collector.daily_kline_collector import collect_daily_all
    codes = _get_stock_codes()
    if not codes:
        logger.error("stock_basic 为空")
        return
    result = collect_daily_all(codes)
    logger.info(f"每日采集结果: {result}")


def cmd_valuation():
    """采集估值数据"""
    from src.collector.valuation_collector import collect_valuation_daily
    codes = _get_stock_codes()
    if not codes:
        logger.error("stock_basic 为空")
        return
    result = collect_valuation_daily(codes)
    logger.info(f"估值采集结果: {result}")


def cmd_fund_flow():
    """采集资金流向"""
    from src.collector.fund_flow_collector import collect_fund_flow_batch
    codes = _get_stock_codes()
    if not codes:
        logger.error("stock_basic 为空")
        return
    logger.warning(f"全市场资金流采集预计耗时 ~{len(codes) * 1.5 / 60:.0f} 分钟")
    result = collect_fund_flow_batch(codes)
    logger.info(f"资金流结果: {result}")


def cmd_northbound():
    """采集北向资金"""
    from src.collector.northbound_collector import collect_northbound_daily
    collect_northbound_daily()


def cmd_dragon_tiger():
    """采集龙虎榜"""
    from src.collector.dragon_tiger_collector import collect_daily_dragon_tiger
    result = collect_daily_dragon_tiger()
    logger.info(f"龙虎榜: {result}")


def cmd_hot_stocks():
    """采集强势股归因"""
    from src.collector.hot_stocks_collector import collect_hot_stocks
    count = collect_hot_stocks()
    logger.info(f"强势股: {count} 条")


def cmd_sector():
    """采集行业板块"""
    from src.collector.sector_collector import collect_sector_daily
    count = collect_sector_daily()
    logger.info(f"行业板块: {count} 条")


def cmd_sector_backfill():
    """回填行业板块历史K线"""
    from src.collector.sector_collector import backfill_sector_history
    days = 90
    if len(sys.argv) > 2:
        try:
            days = int(sys.argv[2])
        except ValueError:
            pass
    result = backfill_sector_history(days=days)
    logger.info(f"行业板块历史回填: {result['sectors']}个板块, {result['records']}条记录")


def cmd_lockup():
    """采集解禁日历"""
    from src.collector.event_collector import collect_lockup_schedule
    count = collect_lockup_schedule()
    logger.info(f"解禁日历: {count} 条")


def cmd_check():
    """数据质量检查"""
    from src.quality.checker import check_daily_completeness, check_data_freshness
    check_daily_completeness()
    check_data_freshness()


def cmd_status():
    """查看数据库状态"""
    from sqlalchemy import text
    from src.storage.database import SessionLocal
    from src.storage.cache import get_cache_info

    db = SessionLocal()
    try:
        tables = [
            ("stock_basic", "股票列表"),
            ("trade_calendar", "交易日历"),
            ("stock_daily", "日K线"),
            ("valuation_daily", "估值"),
            ("fund_flow_daily", "资金流"),
            ("northbound_flow", "北向资金"),
            ("dragon_tiger_record", "龙虎榜"),
            ("hot_stocks", "强势股"),
            ("sector_daily", "行业板块"),
            ("margin_trading", "融资融券"),
            ("lockup_schedule", "解禁日历"),
            ("block_trade", "大宗交易"),
        ]
        print("\n" + "=" * 55)
        print("  Sparrow 数据库状态")
        print("=" * 55)

        for table, desc in tables:
            try:
                result = db.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.scalar()
                print(f"  {desc:8s} | {table:25s} | {count:>12,} 条")
            except Exception:
                print(f"  {desc:8s} | {table:25s} | 表不存在")

        # 日K线日期范围
        try:
            result = db.execute(
                text("SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT code) FROM stock_daily")
            )
            row = result.fetchone()
            if row and row[0]:
                print(f"\n  日K线: {row[0]} ~ {row[1]} | {row[2]} 只股票")
        except Exception:
            pass

        print("=" * 55 + "\n")

        # Parquet 缓存状态
        cache = get_cache_info()
        if cache.get("exists"):
            print(f"  Parquet 缓存: {cache['files']}个文件, {cache['size_mb']}MB, 最新{cache['latest_date']}")
            print(f"  路径: {cache['path']}")
        else:
            print("  Parquet 缓存: 未创建 (执行 python main.py export-cache)")
        print()
    finally:
        db.close()


def cmd_scheduler():
    """启动定时调度器"""
    from src.collector.scheduler import main as scheduler_main
    scheduler_main()


def cmd_smoke_test():
    """数据源连通性测试"""
    import subprocess
    subprocess.run([sys.executable, "-m", "scripts.smoke_test"])


def cmd_export_cache():
    """导出 Parquet 缓存（加速回测）"""
    from src.storage.cache import export_to_parquet
    result = export_to_parquet()
    logger.info(f"导出结果: {result}")


def cmd_signal():
    """生成今日交易信号"""
    from src.strategy.live_signal import generate_live_signal, print_operation_plan
    signal = generate_live_signal(capital=100000)
    print_operation_plan(signal)


def cmd_index_backfill():
    """回填所有常见指数的历史K线"""
    from src.collector.index_collector import backfill_index_all
    result = backfill_index_all()
    logger.info(f"指数回填结果: {result}")


def cmd_auto_update():
    """自动更新: 采集当日K线 + 指数 + 刷新缓存（用于crontab定时执行）"""
    from datetime import date
    logger.info(f"自动更新开始 [{date.today()}]")
    from src.collector.one_click_update import one_click_update
    from src.collector.index_collector import collect_index_daily
    result = one_click_update(full_market=True)
    # 顺带更新指数
    try:
        collect_index_daily()
    except Exception as e:
        logger.warning(f"指数更新失败(不影响主流程): {e}")
    logger.info(f"自动更新完成: {result.get('elapsed', '?')}秒")


def cmd_cron_install():
    """安装系统定时任务(macOS launchd / crontab)"""
    import os
    from pathlib import Path

    project_dir = Path(__file__).parent.resolve()
    python_path = sys.executable
    script = f"{python_path} {project_dir}/main.py auto-update"

    # 生成 crontab 条目: 每个工作日 16:00 运行
    cron_line = f"0 16 * * 1-5 cd {project_dir} && {script} >> {project_dir}/logs/cron.log 2>&1"

    print("\n" + "=" * 60)
    print("  Sparrow 自动更新安装指南")
    print("=" * 60)
    print()
    print("方式1: crontab (推荐)")
    print("-" * 40)
    print("运行以下命令添加定时任务:")
    print()
    print(f"  (crontab -l 2>/dev/null; echo '{cron_line}') | crontab -")
    print()
    print("这会在每个工作日16:00自动采集当日数据并刷新缓存。")
    print()
    print("方式2: macOS launchd")
    print("-" * 40)

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.sparrow.auto-update</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{project_dir}/main.py</string>
        <string>auto-update</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{project_dir}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>16</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{project_dir}/logs/cron.log</string>
    <key>StandardErrorPath</key>
    <string>{project_dir}/logs/cron.log</string>
</dict>
</plist>"""

    plist_path = Path("~/Library/LaunchAgents/com.sparrow.auto-update.plist").expanduser()
    print(f"将以下内容保存为 {plist_path}:")
    print(plist_content)
    print()
    print("然后运行:")
    print(f"  launchctl load {plist_path}")
    print()
    print("验证:")
    print(f"  launchctl list | grep sparrow")
    print()
    print("=" * 60)

    # 自动安装 crontab
    response = input("\n是否立即安装 crontab 定时任务? (y/N): ")
    if response.lower() == "y":
        import subprocess
        # 获取现有 crontab
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        current = existing.stdout if existing.returncode == 0 else ""
        # 检查是否已安装
        if "sparrow" in current.lower() or "auto-update" in current:
            print("已存在 Sparrow 定时任务，跳过。")
        else:
            new_cron = current.rstrip() + "\n" + cron_line + "\n"
            proc = subprocess.run(["crontab", "-"], input=new_cron, capture_output=True, text=True)
            if proc.returncode == 0:
                print("✅ 定时任务安装成功！每个工作日16:00自动更新。")
            else:
                print(f"❌ 安装失败: {proc.stderr}")
    else:
        print("跳过。你可以稍后手动安装。")


def cmd_app():
    """启动 Streamlit Web 界面"""
    import subprocess
    subprocess.run([sys.executable, "-m", "streamlit", "run", "app.py"])


COMMANDS = {
    "init": cmd_init,
    "stock-list": cmd_stock_list,
    "calendar": cmd_calendar,
    "backfill": cmd_backfill,
    "daily": cmd_daily,
    "valuation": cmd_valuation,
    "fund-flow": cmd_fund_flow,
    "northbound": cmd_northbound,
    "dragon-tiger": cmd_dragon_tiger,
    "hot-stocks": cmd_hot_stocks,
    "sector": cmd_sector,
    "sector-backfill": cmd_sector_backfill,
    "lockup": cmd_lockup,
    "check": cmd_check,
    "status": cmd_status,
    "scheduler": cmd_scheduler,
    "smoke-test": cmd_smoke_test,
    "export-cache": cmd_export_cache,
    "signal": cmd_signal,
    "index-backfill": cmd_index_backfill,
    "auto-update": cmd_auto_update,
    "cron-install": cmd_cron_install,
    "app": cmd_app,
}


def main():
    if len(sys.argv) < 2:
        # 无参数时默认启动 Streamlit Web 界面
        cmd_app()
        return

    if sys.argv[1] not in COMMANDS:
        print(__doc__)
        print(f"可用命令: {', '.join(COMMANDS.keys())}\n")
        sys.exit(1)

    cmd = sys.argv[1]
    logger.info(f"执行: {cmd}")
    COMMANDS[cmd]()


if __name__ == "__main__":
    main()
