"""
Sparrow 投资助手 — 可视化面板 V2

启动: streamlit run app.py
功能: 市场温度 / 持仓分析 / 策略研究 / 使用指南
"""

import sys
import json
import time
import platform
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from src.config import settings

st.set_page_config(page_title="Sparrow 投资助手", page_icon="🐦", layout="wide")

# ── 内置后台调度器（随 Streamlit 自动启动）──────────────────
@st.cache_resource
def _start_background_scheduler():
    """启动后台调度器，随 Streamlit 进程运行。每个工作日收盘后自动采集数据。"""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger

        scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

        def _bg_auto_update():
            """后台自动更新任务"""
            from src.logger import logger
            from datetime import date as _date
            import pandas as pd

            today = _date.today()
            # 跳过周末
            if today.weekday() >= 5:
                return

            # 检查数据是否已经是今天的
            try:
                from src.storage.cache import get_cache_info
                cache = get_cache_info()
                if cache.get("exists"):
                    latest = cache.get("latest_date")
                    if latest and pd.Timestamp(latest).date() >= today:
                        return  # 数据已是今天的，跳过
            except Exception:
                pass

            try:
                from src.collector.one_click_update import one_click_update
                from src.collector.index_collector import collect_index_daily
                from src.collector.sector_collector import collect_sector_daily
                logger.info("[调度器] 自动更新开始")
                one_click_update(full_market=False)
                try:
                    collect_index_daily()
                except Exception:
                    pass
                try:
                    collect_sector_daily()
                except Exception:
                    pass
                logger.info("[调度器] 自动更新完成")
            except Exception as e:
                logger.error(f"[调度器] 自动更新异常: {e}")

        # 调度频率: 低配服务器每2小时检查，高配每30分钟
        from src.config import settings as _sched_settings
        check_interval = 120 if _sched_settings.is_low_memory else 30

        scheduler.add_job(
            _bg_auto_update,
            IntervalTrigger(minutes=check_interval),
            id="auto_update_interval",
            name="定时检查更新",
            replace_existing=True,
        )

        # 工作日 15:10 强制更新一次（确保收盘数据入库）
        scheduler.add_job(
            _bg_auto_update,
            CronTrigger(day_of_week="mon-fri", hour=15, minute=10),
            id="auto_update_close",
            name="收盘后更新",
            replace_existing=True,
        )

        scheduler.start()
        return scheduler
    except ImportError:
        return None
    except Exception:
        return None

_scheduler = _start_background_scheduler()

# ── 启动时检测数据状态（仅检测，不触发更新） ──────────────────
@st.cache_data(ttl=3600)
def _check_data_freshness():
    """只检测数据是否过期，不执行更新（更新由后台调度器负责）"""
    from src.storage.cache import get_cache_info
    from datetime import date
    import pandas as pd

    cache = get_cache_info()
    if not cache.get("exists"):
        return None

    latest = cache.get("latest_date")
    if not latest:
        return None

    today = date.today()
    latest_date = pd.Timestamp(latest).date()
    days_stale = (today - latest_date).days

    if days_stale <= 1:
        return "fresh"
    return "stale"

_data_status = _check_data_freshness()

# ── 侧边栏 ──────────────────────────────────────────────
st.sidebar.title("🐦 Sparrow")
st.sidebar.caption("麻雀虽小，五脏俱全")

# 调度器状态
if _scheduler and _scheduler.running:
    st.sidebar.caption("⏰ 自动更新已启用（每30分钟检查）")
else:
    st.sidebar.caption("⚠️ 自动更新未运行")

# 一键更新按钮
if st.sidebar.button("🔄 一键更新数据", type="primary", key="update_btn"):
    with st.sidebar:
        if platform.system().lower() == "linux":
            # 生产环境(Linux): 数据由后台调度器自动更新，前端不做操作
            st.success("✅ 数据由后台调度器自动采集，无需手动更新")
        else:
            # 开发环境(macOS/Windows): 真实拉取数据
            progress = st.progress(0, text="准备更新...")
            try:
                progress.progress(5, text="阶段1/5: 连接数据源...")
                from src.collector.one_click_update import one_click_update, update_watchlist
                from src.collector.index_collector import collect_index_daily
                from src.collector.sector_collector import collect_sector_daily, backfill_sector_history
                from src.storage.cache import export_to_parquet

                progress.progress(10, text="阶段1/5: 采集关注列表最新K线...")
                watchlist_result = update_watchlist()
                progress.progress(25, text="阶段2/5: 采集指数K线...")

                try:
                    collect_index_daily()
                except Exception:
                    pass

                progress.progress(35, text="阶段3/5: 采集行业板块...")
                try:
                    collect_sector_daily()
                    # 检测行业数据是否不足，自动回填历史
                    from src.advisor.sector_analyzer import load_sector_data_from_db
                    sector_df = load_sector_data_from_db()
                    if sector_df.empty or sector_df["trade_date"].nunique() < 20:
                        progress.progress(45, text="阶段3/5: 行业数据不足，回填历史(约2分钟)...")
                        backfill_sector_history(days=90)
                except Exception:
                    pass

                progress.progress(65, text="阶段4/5: 刷新Parquet缓存...")
                cache_result = export_to_parquet()
                progress.progress(95, text="阶段5/5: 清理缓存...")

                st.cache_data.clear()
                progress.progress(100, text="✅ 全部完成!")
                time.sleep(0.5)
                progress.empty()
                st.success(f"✅ 更新完成 (K线{watchlist_result['total_rows']}条, 缓存{cache_result['rows']:,}条)")
            except Exception as e:
                progress.empty()
                st.error(f"更新失败: {e}")

# 数据状态
from src.storage.cache import get_cache_info
cache_info = get_cache_info()
if cache_info.get("exists"):
    status_icon = "✅" if _data_status == "fresh" else "🔄" if _data_status == "updated" else "📁"
    st.sidebar.caption(f"{status_icon} 数据最新: {cache_info.get('latest_date', 'N/A')}")
    if _data_status == "updated":
        st.sidebar.caption("(刚刚自动更新)")
else:
    st.sidebar.warning("缓存未创建")

st.sidebar.divider()
page = st.sidebar.radio(
    "导航", ["🌡️ 市场温度", "🌍 全球联动", "💼 我的持仓", "🏭 行业分析", "📈 策略交易", "📖 使用指南"]
)

# 术语速查
with st.sidebar.expander("📖 术语速查"):
    st.markdown("""
    **年化收益** — 平均每年赚多少%
    **最大回撤** — 从最高点跌到最低点的幅度
    **夏普比率** — 收益/风险比(>1=好)
    **超额收益** — 比市场多赚的部分
    **RSI** — 超买超卖指标(>70过热,<30过冷)
    **MA20** — 过去20天收盘价平均值
    **均线** — 过去N天的平均股价
    **量比** — 今日交易量/近期平均量
    **止损** — 亏到设定比例自动卖出
    **止盈** — 赚到设定比例自动卖出
    **仓位** — 用了多少比例的钱买股票
    **回测** — 用历史数据验证策略
    **因子** — 预测股票涨跌的统计指标
    **IC/ICIR** — 因子预测准确度
    """)


# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════

@st.cache_data(ttl=1800, max_entries=2)
def cached_load_daily(start, end, codes=None):
    from src.storage.cache import load_daily
    return load_daily(start, end, codes=codes)


# ══════════════════════════════════════════════════════════════
# 页面1: 市场温度
# ══════════════════════════════════════════════════════════════

if page == "🌡️ 市场温度":
    st.title("🌡️ 市场温度计")
    st.caption("综合估值、趋势、成交量，判断当前市场处于什么位置。")

    # 温度区间说明
    st.markdown("""
    <div style="background:#f0f4f8; padding:12px 16px; border-radius:8px; margin-bottom:16px; border-left:4px solid #1f77b4;">
        <p style="margin:0 0 8px 0; color:#1a1a2e; font-weight:600;">📖 温度是什么意思？</p>
        <p style="margin:0; color:#2a2a4a; font-size:0.95em;">
        温度 = <strong>当前市场在近几年历史中的百分位</strong>。<br/>
        比如温度27° = 当前市场比历史上73%的时候都便宜/冷清。<br/>
        27°和28°的区别很小（像气温一样），不要纠结个位数变化，看大区间：
        </p>
        <table style="width:100%; margin-top:8px; color:#2a2a4a; font-size:0.9em; border-collapse:collapse;">
            <tr style="border-bottom:1px solid #ddd;">
                <td style="padding:4px;">🟢 <strong>0~30°</strong></td>
                <td style="padding:4px;">极低温=市场在历史底部区域，大部分股票很便宜 → <strong>可以大胆买</strong></td>
            </tr>
            <tr style="border-bottom:1px solid #ddd;">
                <td style="padding:4px;">🟡 <strong>30~55°</strong></td>
                <td style="padding:4px;">中低温=正常偏低，不贵也不便宜 → <strong>正常定投</strong></td>
            </tr>
            <tr style="border-bottom:1px solid #ddd;">
                <td style="padding:4px;">🟠 <strong>55~75°</strong></td>
                <td style="padding:4px;">中高温=偏贵了，比历史多数时候都高 → <strong>减少买入</strong></td>
            </tr>
            <tr>
                <td style="padding:4px;">🔴 <strong>75~100°</strong></td>
                <td style="padding:4px;">极高温=市场在历史顶部区域，非常贵 → <strong>停止买入/考虑卖出</strong></td>
            </tr>
        </table>
    </div>
    """, unsafe_allow_html=True)

    try:
        from src.advisor.market_thermometer import (
            get_market_temperature,
            calc_valuation_temperature,
            calc_momentum_temperature,
            calc_volume_temperature,
        )

        @st.cache_data(ttl=1800, max_entries=1)
        def _cached_temperature():
            return get_market_temperature()

        with st.spinner("计算中..."):
            temp = _cached_temperature()
    except Exception as e:
        st.error(f"计算失败: {e}。请点击侧边栏「一键更新数据」。")
        st.stop()

    overall = temp["overall"]
    # 颜色和背景 (优化对比度: 深色文字 + 浅色背景)
    if overall < 30:
        color, bg, text_color, emoji = "#1a7a32", "#e6f4ea", "#1a3d22", "🟢"
    elif overall < 55:
        color, bg, text_color, emoji = "#b8860b", "#fef9e7", "#5c4300", "🟡"
    elif overall < 75:
        color, bg, text_color, emoji = "#c65102", "#fff0e0", "#6b2d00", "🟠"
    else:
        color, bg, text_color, emoji = "#b71c1c", "#fdecea", "#5c0e0e", "🔴"

    # 主卡片 (确保文字清晰可读)
    st.markdown(f"""
    <div style="background:{bg}; padding:24px; border-radius:12px; text-align:center; margin-bottom:20px; border: 1px solid {color}30;">
        <div style="font-size:4em; font-weight:bold; color:{color};">{emoji} {overall:.0f}°</div>
        <div style="font-size:1.5em; margin:8px 0; font-weight:600; color:{text_color};">{temp['action']}</div>
        <div style="font-size:1.1em; color:{text_color};">{temp['signal']}</div>
        <div style="color:#444; margin-top:8px; font-weight:500;">数据截至: {temp['date']}</div>
    </div>
    """, unsafe_allow_html=True)

    # 三维度详情
    st.subheader("三维度拆解")
    col1, col2, col3 = st.columns(3)

    with col1:
        v = temp["valuation"]
        st.markdown("#### 📐 估值维度 (权重50%)")
        st.metric("温度", f"{v['temperature']:.0f}°", help="估值温度：衡量当前股价相对于历史是贵还是便宜。\n\n0°=极度便宜(历史最低区域)\n100°=极度昂贵(历史最高区域)\n\n低温=股票普遍便宜，适合买入")
        st.markdown(f"""
        **计算方法**: 全市场股价 / 250日均线 的中位数，在近3.5年历史中的百分位。

        - 当前比值: **{v.get('current_ratio', 'N/A')}**
        - 历史最低: {v.get('history_min', 'N/A')}
        - 历史中位: {v.get('history_median', 'N/A')}
        - 历史最高: {v.get('history_max', 'N/A')}

        **解读**: 比值<1说明大部分股票在年线以下（偏便宜），>1说明在年线以上（偏贵）。
        """)

    with col2:
        m = temp["momentum"]
        st.markdown("#### 📈 趋势维度 (权重30%)")
        st.metric("温度", f"{m['temperature']:.0f}°", help="趋势温度：衡量多少股票处于上涨趋势中。\n\n0°=几乎所有股票都在跌\n100°=几乎所有股票都在涨\n\n高温=市场普涨（可能过热），低温=市场普跌（可能是底部）")
        st.markdown(f"""
        **计算方法**: 站在20日均线上方的股票占比，在历史中的百分位。

        - 当前占比: **{m.get('above_ma20_pct', 'N/A')}%**

        **解读**: >60%=市场普涨（强势），<40%=多数股票在下跌（弱势）。
        当前{m.get('above_ma20_pct', 50):.0f}%的股票站在均线上方。
        """)

    with col3:
        vol = temp["volume"]
        st.markdown("#### 📊 成交量维度 (权重20%)")
        st.metric("温度", f"{vol['temperature']:.0f}°", help="成交量温度：衡量当前市场交易活跃程度。\n\n0°=市场冷清，没人交易\n100°=市场火爆，交易疯狂\n\n极高温度通常出现在市场顶部（全民炒股），极低通常出现在底部（无人问津）")
        st.markdown(f"""
        **计算方法**: 当日全市场成交额 vs 60日均量的比值，在历史中的百分位。

        - 量比: **{vol.get('volume_ratio', 'N/A')}**
        - 当日成交: ~{vol.get('current_amount_yi', 'N/A')}亿

        **解读**: 量比>1.5=市场活跃/情绪高涨，<0.7=市场冷淡/观望。
        """)

    # 综合公式
    st.divider()
    st.subheader("计算公式")
    st.markdown(f"""
    ```
    综合温度 = 估值温度×50% + 趋势温度×30% + 成交量温度×20%
             = {v['temperature']:.0f} × 0.5 + {m['temperature']:.0f} × 0.3 + {vol['temperature']:.0f} × 0.2
             = {overall:.1f}°
    ```
    """)

    st.markdown("""
    **为什么这么分配权重？**
    - 估值权重最大(50%): 长期看，买得便宜是收益的最大来源
    - 趋势其次(30%): 右侧确认，避免"接飞刀"
    - 成交量最小(20%): 辅助验证，不独立做决策依据
    """)

    # 主要指数近期表现
    st.divider()
    st.subheader("📊 主要指数近况")
    try:
        from src.collector.index_collector import load_index_daily, INDEX_LIST
        index_metrics = []
        for code, market, name in INDEX_LIST[:5]:  # 展示前5个主要指数
            idx_df = load_index_daily(code, start_date=(date.today() - timedelta(days=60)).isoformat())
            if idx_df.empty or len(idx_df) < 20:
                continue
            latest_close = float(idx_df["close"].iloc[-1])
            ma20 = float(idx_df["close"].tail(20).mean())
            ret_20d = (latest_close / float(idx_df["close"].iloc[-20]) - 1) * 100 if len(idx_df) >= 20 else 0
            above_ma = "📈 均线上方" if latest_close > ma20 else "📉 均线下方"
            index_metrics.append({
                "指数": name,
                "最新点位": f"{latest_close:.0f}",
                "20日涨跌": f"{ret_20d:+.1f}%",
                "vs 20日均线": above_ma,
            })
        if index_metrics:
            st.dataframe(pd.DataFrame(index_metrics), width="stretch", hide_index=True)
    except Exception:
        pass  # 指数数据未采集时静默

    # ── 板块冷热分层 ──
    st.divider()
    st.subheader("🔥 板块冷热图")
    st.caption("同一个市场里，不同板块温度差异巨大。有些过热，有些冰冷——精准到板块才有操作价值。")

    try:
        from src.advisor.sector_analyzer import load_sector_data_from_db
        sector_df = load_sector_data_from_db()
        if not sector_df.empty:
            latest_date = sector_df["trade_date"].max()
            today_sectors = sector_df[sector_df["trade_date"] == latest_date].sort_values("change_pct", ascending=False)

            if not today_sectors.empty:
                # 把行业按涨跌分成几个温度区间
                hot = today_sectors[today_sectors["change_pct"] > 2]
                warm = today_sectors[(today_sectors["change_pct"] > 0) & (today_sectors["change_pct"] <= 2)]
                cool = today_sectors[(today_sectors["change_pct"] > -2) & (today_sectors["change_pct"] <= 0)]
                cold = today_sectors[today_sectors["change_pct"] <= -2]

                col_h, col_w, col_c, col_d = st.columns(4)
                with col_h:
                    st.markdown(f"**🔴 过热({len(hot)}个)**")
                    for _, r in hot.head(5).iterrows():
                        st.caption(f"{r['sector_name']} +{r['change_pct']:.1f}%")
                with col_w:
                    st.markdown(f"**🟡 温热({len(warm)}个)**")
                    for _, r in warm.head(5).iterrows():
                        st.caption(f"{r['sector_name']} +{r['change_pct']:.1f}%")
                with col_c:
                    st.markdown(f"**🟢 偏冷({len(cool)}个)**")
                    for _, r in cool.head(5).iterrows():
                        st.caption(f"{r['sector_name']} {r['change_pct']:.1f}%")
                with col_d:
                    st.markdown(f"**🧊 冰冷({len(cold)}个)**")
                    for _, r in cold.head(5).iterrows():
                        st.caption(f"{r['sector_name']} {r['change_pct']:.1f}%")

                st.markdown(f"""
                > 📊 今日({str(latest_date)[:10]}): 过热{len(hot)}个 | 温热{len(warm)}个 | 偏冷{len(cool)}个 | 冰冷{len(cold)}个 — **市场不是铁板一块，总有热点。**
                """)
        else:
            st.info("💡 板块数据需要积累。点击「一键更新数据」采集行业行情，多更新几天后这里会显示板块冷热分布。")
    except Exception:
        st.info("💡 板块分析需要行业数据，请确保数据库运行并点击「一键更新数据」。")


# ══════════════════════════════════════════════════════════════
# 页面: 全球联动
# ══════════════════════════════════════════════════════════════

elif page == "🌍 全球联动":
    st.title("🌍 全球资产联动")
    st.caption("一眼看清全球资金流向：美股/日韩/黄金/A股各板块的涨跌全景图 + AI分析")

    # 自动刷新控制
    auto_refresh = st.toggle("⏱️ 盘中自动刷新", value=False, key="global_auto_refresh")
    if auto_refresh:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=60000, limit=None, key="global_autorefresh_timer")
        st.caption("🔄 每60秒自动刷新行情数据")

    from src.advisor.global_market import fetch_global_quotes, get_global_treemap_data, get_sector_treemap_data, detect_significant_moves

    # ── 异动检测 ──
    quotes = fetch_global_quotes()
    significant = detect_significant_moves(quotes)

    if significant:
        st.subheader("⚡ 异动提醒")
        badges_html = ""
        for move in sorted(significant, key=lambda x: abs(x["change_pct"]), reverse=True):
            badge = "🔴" if move["direction"] == "down" else "🟢"
            bg_color = "#fdecea" if move["direction"] == "down" else "#e6f4ea"
            text_color = "#b71c1c" if move["direction"] == "down" else "#1b5e20"
            badges_html += (
                f'<span style="display:inline-block; margin:4px 8px 4px 0; padding:6px 12px; '
                f'border-radius:8px; background:{bg_color}; color:{text_color}; '
                f'font-size:1.05em;">'
                f'{badge} <strong>{move["name"]}</strong> {move["change_pct"]:+.2f}%</span>'
            )
        st.markdown(badges_html, unsafe_allow_html=True)
        st.divider()

    # ── 全球资产 Treemap ──
    st.subheader("🗺️ 全球资产涨跌全景")
    st.markdown("方块颜色：🔴红=涨 🟢绿=跌 | 一眼看出资金去了哪里")

    global_data = get_global_treemap_data()

    if global_data:
        # Treemap
        import plotly.express as px

        # Build a lookup of significant move names for badge display
        significant_names = {m["name"]: m for m in significant} if significant else {}

        df_global = pd.DataFrame(global_data)
        # 颜色: 红涨绿跌
        fig_tree = px.treemap(
            df_global, path=["category", "name"], values="value",
            color="change_pct",
            color_continuous_scale=[[0, "#2ca02c"], [0.5, "#f5f5f5"], [1, "#d62728"]],
            color_continuous_midpoint=0,
            custom_data=["change_pct", "price"],
        )
        fig_tree.update_traces(
            texttemplate="<b>%{label}</b><br>%{customdata[0]:+.2f}%",
            textfont_size=14,
            textfont_color="#1a1a2e",
        )
        fig_tree.update_layout(height=400, margin=dict(t=30, l=0, r=0, b=0))
        st.plotly_chart(fig_tree, width="stretch")

        # 涨跌明细表
        with st.expander("📋 全球资产明细"):
            detail_data = []
            for d in global_data:
                name = d["name"]
                # Add alert badge next to asset name if it has a significant move
                if name in significant_names:
                    badge = "🔴" if significant_names[name]["direction"] == "down" else "🟢"
                    display_name = f"{badge} {name}"
                else:
                    display_name = name
                detail_data.append({
                    "资产": display_name,
                    "分类": d["category"],
                    "涨跌幅": f"{d['change_pct']:+.2f}%",
                    "价格": f"{d['price']:.2f}",
                })
            st.dataframe(pd.DataFrame(detail_data), width="stretch", hide_index=True)
    else:
        st.warning("无法获取全球行情数据。可能是网络问题或非交易时段。")

    st.divider()

    # ── A股行业 Treemap ──
    st.subheader("🏭 A股行业涨跌全景")
    sector_data = get_sector_treemap_data()

    if sector_data:
        df_sector = pd.DataFrame(sector_data)
        fig_sector_tree = px.treemap(
            df_sector, path=["category", "name"], values="value",
            color="change_pct",
            color_continuous_scale=[[0, "#2ca02c"], [0.5, "#f5f5f5"], [1, "#d62728"]],
            color_continuous_midpoint=0,
            custom_data=["change_pct"],
        )
        fig_sector_tree.update_traces(
            texttemplate="<b>%{label}</b><br>%{customdata[0]:+.1f}%",
            textfont_size=11,
            textfont_color="#1a1a2e",
        )
        fig_sector_tree.update_layout(height=500, margin=dict(t=30, l=0, r=0, b=0))
        st.plotly_chart(fig_sector_tree, width="stretch")
    else:
        st.info("💡 行业数据需要先采集。请确保PostgreSQL运行后点击「一键更新数据」。")

    st.divider()

    # ── AI 分析 ──
    st.subheader("🤖 AI 市场解读")
    st.caption("基于全球行情数据，AI给出当前市场的简要分析和操作建议")

    if st.button("🧠 生成AI分析", type="primary"):
        from src.ai_client import analyze_market_overview

        # 组装数据: 包含所有全球指数的价格和涨跌幅（满足 Requirement 2.5）
        quotes = fetch_global_quotes()
        market_data = {}
        for index_name, q in quotes.items():
            market_data[index_name] = {
                "price": q["price"],
                "change_pct": q["change_pct"],
            }
        if sector_data:
            market_data["热门行业"] = [{"name": s["name"], "change": s["change_pct"]}
                                     for s in sorted(sector_data, key=lambda x: -x["change_pct"])[:5]]

        with st.spinner("AI正在分析全球市场..."):
            analysis = analyze_market_overview(market_data)

        st.markdown(f"""
        <div style="background:#f0f4f8; padding:16px; border-radius:10px; border-left:4px solid #1f77b4;">
            <p style="color:#1a1a2e; white-space:pre-wrap;">{analysis}</p>
        </div>
        """, unsafe_allow_html=True)

    # ── 科普 ──
    with st.expander("📖 全球联动怎么看？", expanded=False):
        st.markdown("""
        ### 时区与开市时间

        | 市场 | 开市时间(北京) | 对A股影响 |
        |------|--------------|----------|
        | A股 | 9:30-15:00 | — |
        | 日韩 | 8:00-14:00 | 盘中同步联动 |
        | 美股 | 21:30-次日4:00 | 影响A股次日开盘 |

        ### 联动规律

        - **美股大跌 → A股次日大概率低开**（情绪传染）
        - **黄金暴涨 → 避险情绪升温 → A股可能承压**（资金从股市流向避险资产）
        - **日韩暴跌 → A股盘中跟跌概率高**（亚太资金共振）
        - **美元走强 → 外资流出A股**（汇率压力）

        ### 怎么用这个页面

        1. 每天早上9点前看一眼：昨晚美股怎样？→ 判断今天A股开盘情绪
        2. 看资金流向：红色多(全球普涨)=做多信心强；绿色多=避险观望
        3. 看行业热力图：找出今天最热/最冷的行业
        """)


# ══════════════════════════════════════════════════════════════
# 页面2: 我的持仓
# ══════════════════════════════════════════════════════════════

elif page == "💼 我的持仓":
    st.title("💼 我的持仓分析")

    # 自动刷新控制
    auto_refresh_port = st.toggle("⏱️ 盘中自动刷新", value=False, key="port_auto_refresh")
    if auto_refresh_port:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=60000, limit=None, key="port_autorefresh_timer")
        st.caption("🔄 每60秒自动刷新实时价格")

    from src.advisor.portfolio_tracker import load_portfolio, get_etf_analysis
    from src.datasource.tencent_source import fetch_realtime_quotes

    portfolio = load_portfolio()
    positions = portfolio.get("positions", [])

    # 拉取场内持仓的实时行情
    etf_codes = [p["code"] for p in positions if p["market"] == "场内"]
    realtime_quotes = {}
    if etf_codes:
        try:
            realtime_quotes = fetch_realtime_quotes(etf_codes)
        except Exception:
            pass

    # 持仓表（含实时价格）
    st.subheader("当前持仓")
    pos_display = []
    for p in positions:
        row = {
            "名称": p["name"],
            "代码": p["code"],
            "类型": p["type"],
            "市场": p["market"],
            "投入(元)": p["cost"],
        }
        # 场内持仓显示实时价格和涨跌
        if p["code"] in realtime_quotes:
            q = realtime_quotes[p["code"]]
            row["实时价格"] = f"¥{q['price']:.3f}"
            row["今日涨跌"] = f"{q['change_pct']:+.2f}%"
        else:
            row["实时价格"] = "-"
            row["今日涨跌"] = "-"
        pos_display.append(row)
    st.dataframe(pd.DataFrame(pos_display), width="stretch", hide_index=True)

    # 实时行情时间提示
    if realtime_quotes:
        st.caption("💡 价格为腾讯财经实时行情，盘中约3秒延迟，收盘后为当日收盘价。")

    st.divider()

    # 获取市场温度(用于场外基金建议)
    try:
        from src.advisor.market_thermometer import get_market_temperature

        @st.cache_data(ttl=1800, max_entries=1)
        def _cached_temperature_for_portfolio():
            return get_market_temperature()

        temp = _cached_temperature_for_portfolio()
        market_temp = temp["overall"]
    except Exception:
        market_temp = 50  # 默认中性

    # ── 逐一分析每个持仓 ──
    for pos in positions:
        code = pos["code"]
        name = pos["name"]
        pos_type = pos["type"]
        market = pos["market"]

        st.subheader(f"🔍 {name} ({code})")

        if market == "场内":
            # 场内ETF: 有K线数据，做完整技术分析
            try:
                analysis = get_etf_analysis(code)
            except Exception as e:
                st.warning(f"分析失败: {e}，请点击「一键更新数据」")
                continue

            if "error" in analysis:
                st.warning(f"{analysis['error']}，请点击「一键更新数据」")
                continue

            rsi = analysis["rsi"]
            ret_20d = analysis["ret_20d"]
            above_ma = analysis["above_ma_count"]
            price = analysis["price"]

            # 如果有实时价格，用实时价格覆盖
            rt = realtime_quotes.get(code)
            if rt and rt["price"] > 0:
                realtime_price = rt["price"]
                realtime_change = rt["change_pct"]
            else:
                realtime_price = price
                realtime_change = None

            # 操作建议卡片
            action = analysis["action"]
            reason = analysis["reason"]
            if "加仓" in action or "定投" in action:
                card_color, card_text = "#e6f4ea", "#1a3d22"
            elif "减仓" in action or "暂停" in action:
                card_color, card_text = "#fdecea", "#5c0e0e"
            else:
                card_color, card_text = "#f0f4f8", "#2a2a4a"

            st.markdown(f"""
            <div style="background:{card_color}; padding:12px 16px; border-radius:8px; margin-bottom:12px; border:1px solid {card_text}20;">
                <strong style="color:{card_text};">{action}</strong>
                <span style="color:{card_text}; margin-left:8px;">{reason}</span>
                <span style="color:#666; margin-left:12px; font-size:0.85em;">数据: {analysis['date']}</span>
            </div>
            """, unsafe_allow_html=True)

            # 指标面板
            c1, c2, c3, c4, c5, c6 = st.columns(6)
            price_delta = f"{realtime_change:+.2f}%" if realtime_change is not None else None
            c1.metric("实时价", f"¥{realtime_price:.3f}", delta=price_delta, help="腾讯实时行情，盘中~3秒延迟")
            c2.metric("RSI(14)", f"{rsi:.1f}", help="相对强弱指标：<30=超卖(可能反弹), >70=超买(可能回调)")
            c3.metric("20日涨幅", f"{ret_20d:+.1f}%", help="过去20个交易日的累计涨跌幅")
            c4.metric("量比", f"{analysis['vol_ratio']:.2f}", help="今日成交量÷近期平均量。>1.5=活跃, <0.7=冷清")
            c5.metric("均线站上", f"{above_ma}/4条", help="价格站在几条均线上方(MA5/20/60/120)")
            c6.metric("趋势", analysis["position"], help="综合均线位置判断的趋势状态")

            # K线图
            kline_df = analysis.get("kline_df")
            if kline_df is not None and not kline_df.empty:
                with st.expander("📈 K线走势图", expanded=False):
                    fig = make_subplots(
                        rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.7, 0.3], vertical_spacing=0.03,
                        subplot_titles=("价格与均线", "RSI(14)")
                    )
                    fig.add_trace(go.Scatter(
                        x=kline_df["trade_date"], y=kline_df["close"],
                        name="收盘价", line=dict(color="#1f77b4", width=2)
                    ), row=1, col=1)
                    for ma, clr, nm in [("ma5","#ff7f0e","MA5"), ("ma20","#2ca02c","MA20"), ("ma60","#d62728","MA60"), ("ma120","#9467bd","MA120")]:
                        if ma in kline_df.columns:
                            fig.add_trace(go.Scatter(
                                x=kline_df["trade_date"], y=kline_df[ma],
                                name=nm, line=dict(color=clr, width=1, dash="dot")
                            ), row=1, col=1)
                    if "rsi" in kline_df.columns:
                        fig.add_trace(go.Scatter(
                            x=kline_df["trade_date"], y=kline_df["rsi"],
                            name="RSI", line=dict(color="#8c564b", width=1.5)
                        ), row=2, col=1)
                        fig.add_hline(y=70, line_dash="dash", line_color="red", row=2, col=1)
                        fig.add_hline(y=30, line_dash="dash", line_color="green", row=2, col=1)
                    fig.update_layout(height=400, legend=dict(orientation="h", y=1.05), margin=dict(t=30))
                    st.plotly_chart(fig, width="stretch")

        else:
            # 场外基金: 无K线数据，基于市场温度给建议
            st.markdown(f"""
            <div style="background:#f0f4f8; padding:12px; border-radius:8px; margin-bottom:8px; border-left:3px solid #1f77b4;">
                <p style="margin:0; color:#4a4a6a; font-size:0.9em;">
                ℹ️ 场外基金无法获取实时K线数据，以下建议基于A股整体市场温度({market_temp:.0f}°)。
                </p>
            </div>
            """, unsafe_allow_html=True)

            # 根据市场温度给场外基金建议
            if market_temp < 30:
                fund_action = "📗 加倍定投"
                fund_color, fund_text = "#e6f4ea", "#1a3d22"
                fund_detail = f"市场温度极低({market_temp:.0f}°)，属于历史低位区域。建议这次定投金额加倍(比如平时投500，这次投1000)。"
            elif market_temp < 50:
                fund_action = "📒 正常定投"
                fund_color, fund_text = "#fef9e7", "#5c4300"
                fund_detail = f"市场温度适中偏低({market_temp:.0f}°)，按原定计划正常定投即可。"
            elif market_temp < 70:
                fund_action = "📒 减半定投"
                fund_color, fund_text = "#fef9e7", "#5c4300"
                fund_detail = f"市场温度偏高({market_temp:.0f}°)，可以把定投金额减半，留子弹等回调。"
            else:
                fund_action = "📕 暂停定投"
                fund_color, fund_text = "#fdecea", "#5c0e0e"
                fund_detail = f"市场温度过高({market_temp:.0f}°)，市场可能过热。建议暂停定投，观望为主。"

            st.markdown(f"""
            <div style="background:{fund_color}; padding:12px 16px; border-radius:8px; margin-bottom:12px; border:1px solid {fund_text}20;">
                <strong style="color:{fund_text};">{fund_action}</strong>
                <p style="margin:4px 0 0 0; color:{fund_text}; font-size:0.95em;">{fund_detail}</p>
            </div>
            """, unsafe_allow_html=True)

            # 基金信息
            col1, col2 = st.columns(2)
            with col1:
                st.metric("投入成本", f"¥{pos['cost']:,}", help="你在这只基金上已经投入的总金额")
            with col2:
                st.metric("投资逻辑", pos["note"], help="当初买入这只基金的理由")

        st.divider()

    # 底部提醒
    st.caption("⚠️ 以上分析基于技术面（价格/成交量/均线），不包含基本面判断。基本面信息需要你自行结合判断。工具提供数据参考，不构成投资建议。")


# ══════════════════════════════════════════════════════════════
# 页面: 行业分析
# ══════════════════════════════════════════════════════════════

elif page == "🏭 行业分析":
    st.title("🏭 行业轮动分析")
    st.caption("月频视角：哪些行业正在启动？哪些在衰退？帮你决定下个月该布局什么方向。")

    # 行业分析科普
    with st.expander("📖 月频操作者怎么用行业轮动？", expanded=False):
        st.markdown("""
        ### 核心理念：顺着行业动量做

        A股行业轮动周期通常 **3-6个月**。你不需要每天看，只需要每月判断一次：

        **1. 动量评分**（本页核心指标）
        - 评分 > 65分 = 行业处于上升通道，值得参与
        - 评分 35~65 = 中性区间，观望
        - 评分 < 35分 = 下降通道，回避

        **2. 加速度**（判断拐点）
        - 正加速 = 涨得越来越快（好事）
        - 负加速 = 动量在衰减（可能快到顶了）
        - 底部区域 + 加速度转正 = 可能要启动了

        **3. 你的操作节奏**
        - 每月初看一次本页面
        - 找到"建议关注"的行业，对应买 ETF
        - "建议规避"的行业，不碰或减仓
        - 不追涨、不抄底，跟着动量走
        """)

    # 加载行业数据
    try:
        from src.advisor.sector_analyzer import (
            load_sector_data_from_db, get_hot_sectors, analyze_sector_phase,
            get_sector_momentum_score, get_sector_rotation_signals
        )
        sector_df = load_sector_data_from_db()
    except Exception as e:
        sector_df = pd.DataFrame()

    if sector_df.empty:
        st.warning("⚠️ 行业数据不足。请点击「一键更新数据」采集最新行业行情。多次更新可积累历史数据。")
        st.info("💡 行业数据需要积累至少20天才能进行有效的动量分析。建议设置自动更新(python main.py cron-install)。")
    else:
        n_days = sector_df["trade_date"].nunique()
        latest_date = sector_df["trade_date"].max()
        st.caption(f"数据: {n_days}天行业行情 | 最新: {str(latest_date)[:10]} | 共{sector_df['sector_code'].nunique()}个行业")

        # ══════════════════════════════════════
        # 第一部分: 行业轮动信号（核心决策面板）
        # ══════════════════════════════════════
        if n_days >= 20:
            signals = get_sector_rotation_signals(sector_df)

            # 总结
            st.markdown(f"""
            <div style="background:#f0f4f8; padding:14px 18px; border-radius:10px; border-left:4px solid #1f77b4; margin-bottom:16px;">
                <p style="margin:0; font-size:1.05em; color:#1a1a2e;"><strong>📋 本期判断：</strong>{signals['summary']}</p>
            </div>
            """, unsafe_allow_html=True)

            col_buy, col_avoid, col_watch = st.columns(3)

            with col_buy:
                st.markdown("#### 🟢 建议关注")
                st.caption("动量加速 + 上升趋势确认")
                if signals["buy_candidates"]:
                    for s in signals["buy_candidates"]:
                        st.markdown(f"""
                        <div style="background:#e6f4ea; padding:10px 14px; border-radius:8px; margin-bottom:8px; border-left:3px solid #1b5e20;">
                            <p style="margin:0; color:#1b5e20; font-weight:600;">{s['sector_name']}</p>
                            <p style="margin:2px 0 0 0; color:#2e7d32; font-size:0.85em;">
                                动量 {s['momentum_score']:.0f}分 | 20日 {s['ret_20d']:+.1f}% | 加速 {s['acceleration']:+.1f}%
                            </p>
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.info("暂无明确买入信号")

            with col_avoid:
                st.markdown("#### 🔴 建议规避")
                st.caption("动量衰减 / 尾期过热")
                if signals["avoid"]:
                    for s in signals["avoid"]:
                        st.markdown(f"""
                        <div style="background:#fdecea; padding:10px 14px; border-radius:8px; margin-bottom:8px; border-left:3px solid #b71c1c;">
                            <p style="margin:0; color:#b71c1c; font-weight:600;">{s['sector_name']}</p>
                            <p style="margin:2px 0 0 0; color:#c62828; font-size:0.85em;">
                                动量 {s['momentum_score']:.0f}分 | 20日 {s['ret_20d']:+.1f}% | {s['phase']}
                            </p>
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.info("暂无明确规避信号")

            with col_watch:
                st.markdown("#### 🟡 底部观察")
                st.caption("跌幅收窄，可能即将启动")
                if signals["watch"]:
                    for s in signals["watch"]:
                        st.markdown(f"""
                        <div style="background:#fff8e1; padding:10px 14px; border-radius:8px; margin-bottom:8px; border-left:3px solid #f57f17;">
                            <p style="margin:0; color:#e65100; font-weight:600;">{s['sector_name']}</p>
                            <p style="margin:2px 0 0 0; color:#bf360c; font-size:0.85em;">
                                动量 {s['momentum_score']:.0f}分 | 60日 {s['ret_60d']:+.1f}% | 加速度转正
                            </p>
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.info("暂无底部启动信号")

            st.divider()

        # ══════════════════════════════════════
        # 第二部分: 全行业动量排名
        # ══════════════════════════════════════
        st.subheader("📊 全行业动量排名")
        st.caption("按综合动量评分排序 — 评分越高说明行业上涨趋势越强")

        if n_days >= 20:
            momentum_data = get_sector_momentum_score(sector_df)
            if momentum_data:
                display_data = []
                for s in momentum_data[:20]:  # 显示前20
                    # 动量评分可视化
                    score = s["momentum_score"]
                    if score >= 65:
                        score_label = f"🟢 {score:.0f}"
                    elif score >= 35:
                        score_label = f"🟡 {score:.0f}"
                    else:
                        score_label = f"🔴 {score:.0f}"

                    display_data.append({
                        "行业": s["sector_name"],
                        "动量评分": score_label,
                        "5日": f"{s['ret_5d']:+.1f}%",
                        "20日": f"{s['ret_20d']:+.1f}%",
                        "60日": f"{s['ret_60d']:+.1f}%",
                        "加速度": f"{s['acceleration']:+.1f}%",
                        "胜率": f"{s['win_rate_20d']:.0f}%",
                        "阶段": s["phase"],
                    })
                st.dataframe(pd.DataFrame(display_data), width="stretch", hide_index=True, height=500)

                # 动量分布图
                st.divider()
                st.subheader("📈 行业动量分布")
                st.caption("横轴=60日收益（中期趋势），纵轴=20日收益（短期动量），气泡大小=动量评分")

                import plotly.express as px
                scatter_df = pd.DataFrame(momentum_data[:30])
                fig_scatter = px.scatter(
                    scatter_df, x="ret_60d", y="ret_20d",
                    size="momentum_score", color="acceleration",
                    color_continuous_scale=[[0, "#d62728"], [0.5, "#f5f5f5"], [1, "#2ca02c"]],
                    color_continuous_midpoint=0,
                    hover_name="sector_name",
                    hover_data={"ret_5d": ":.1f", "momentum_score": ":.0f", "win_rate_20d": ":.0f"},
                    labels={"ret_60d": "60日收益%", "ret_20d": "20日收益%", "acceleration": "加速度"},
                )
                fig_scatter.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
                fig_scatter.add_vline(x=0, line_dash="dash", line_color="gray", opacity=0.5)
                fig_scatter.update_layout(
                    height=450,
                    margin=dict(t=30, l=50, r=20, b=50),
                    plot_bgcolor="#fafafa",
                )
                # 添加象限标注
                fig_scatter.add_annotation(x=scatter_df["ret_60d"].max() * 0.7, y=scatter_df["ret_20d"].max() * 0.8,
                                          text="强势加速区", showarrow=False, font=dict(color="green", size=11))
                fig_scatter.add_annotation(x=scatter_df["ret_60d"].min() * 0.7, y=scatter_df["ret_20d"].min() * 0.8,
                                          text="弱势下跌区", showarrow=False, font=dict(color="red", size=11))
                st.plotly_chart(fig_scatter, width="stretch")
        else:
            st.info(f"当前仅{n_days}天数据，需积累至少20天才能计算动量评分。")

        # ══════════════════════════════════════
        # 第三部分: 行业阶段全景（保留原有功能但降低优先级）
        # ══════════════════════════════════════
        st.divider()
        with st.expander("📋 行业生命周期阶段一览（展开查看）", expanded=False):
            if n_days >= 20:
                hot_sectors = get_hot_sectors(sector_df, top_n=30)
                if hot_sectors:
                    phase_data = []
                    for s in hot_sectors:
                        phase_data.append({
                            "行业": s["sector_name"],
                            "20日涨跌": f"{s['ret_20d']:+.1f}%",
                            "60日涨跌": f"{s['ret_60d']:+.1f}%",
                            "阶段": s["phase"],
                            "建议": s["suggestion"],
                        })
                    st.dataframe(pd.DataFrame(phase_data), width="stretch", hide_index=True)
                else:
                    st.info("数据积累中...")
            else:
                st.info("需至少20天数据。")

        # 今日快照（折叠，供参考）
        today_df = sector_df[sector_df["trade_date"] == latest_date].sort_values("change_pct", ascending=False)
        with st.expander("📅 今日行业涨跌快照（日频参考）", expanded=False):
            if not today_df.empty:
                col_top, col_bottom = st.columns(2)
                with col_top:
                    st.markdown("**涨幅前10**")
                    top10 = today_df.head(10)[["sector_name", "change_pct", "leader_name"]].copy()
                    top10.columns = ["行业", "涨跌%", "龙头"]
                    top10["涨跌%"] = top10["涨跌%"].apply(lambda x: f"{x:+.2f}%")
                    st.dataframe(top10, width="stretch", hide_index=True)
                with col_bottom:
                    st.markdown("**跌幅前10**")
                    bottom10 = today_df.tail(10)[["sector_name", "change_pct", "leader_name"]].copy()
                    bottom10.columns = ["行业", "涨跌%", "龙头"]
                    bottom10["涨跌%"] = bottom10["涨跌%"].apply(lambda x: f"{x:+.2f}%")
                    st.dataframe(bottom10, width="stretch", hide_index=True)


# ══════════════════════════════════════════════════════════════
# 页面3: 策略交易 (回测+实盘信号+模拟交易)
# ══════════════════════════════════════════════════════════════

elif page == "📈 策略交易":
    st.title("📈 策略交易")
    tab1, tab2, tab3 = st.tabs(["策略回测", "实盘信号", "模拟交易"])

    with tab1:
        st.caption("这是一个真正面向赚钱的策略——择时+多因子选股+风控。不是学术研究，而是模拟真实交易。")

        # ── 策略说明 ──
        st.markdown("""
        <div style="background:#f0f4f8; padding:16px; border-radius:10px; border-left:4px solid #1f77b4; margin-bottom:20px;">
            <h4 style="margin:0 0 8px 0; color:#1a1a2e;">💡 V3策略: 满仓选股 + 个股止损</h4>
            <p style="color:#2a2a4a; margin:0;">
            V2策略的问题是"择时伤alpha"——轻仓踏空比满仓亏损更伤收益。<br/>
            V3的核心转变：<br/>
            ① <strong>始终高仓位(95%)</strong>：不做择时判断，全力靠选股赚钱<br/>
            ② <strong>个股止损(-20%)</strong>：单只股票跌20%立刻卖掉，换入新股<br/>
            ③ <strong>暴跌熔断</strong>：仅在全市场单日暴跌>4%时临时减仓（极少触发）
            </p>
        </div>
        """, unsafe_allow_html=True)
    
        # 自适应权重说明
        with st.expander("🧠 策略核心逻辑详解", expanded=False):
            st.markdown("""
            ### 为什么"满仓+止损"比"择时"更好？
    
            | 方案 | 年化收益 | 最大回撤 | 问题 |
            |------|---------|---------|------|
            | 满仓无止损 | +10% | -60% | 回撤太大，受不了 |
            | **满仓+个股止损** | **+37%** | **-32%** | ✅ 最优平衡 |
            | 择时+选股(V2) | +6% | -48% | 择时踏空，反而亏更多 |
    
            ### 个股止损的原理
    
            不是"整体仓位砍半"，而是**只卖亏钱的那只股票**：
            - 30只持仓中，如果3只跌了20% → 只卖这3只
            - 卖出的钱立刻买入因子评分最高的替补股
            - 这样保持了高仓位，但把烂股及时清掉
    
            ### 自适应因子权重
    
            | 市场状态 | 小市值 | 动量 | 反转 | 低波 | 缩量 |
            |---------|--------|------|------|------|------|
            | 🟢 牛市 | 30% | **30%** | 10% | 10% | 20% |
            | 🟡 震荡 | 30% | 15% | **25%** | 15% | 15% |
            | 🔴 熊市 | 25% | 5% | **30%** | **30%** | 10% |
    
            注意：这里市场状态只影响选什么股票，**不影响仓位**。仓位始终95%。
            """)
    
        from src.strategy.smart_strategy import (
            SmartStrategyConfig, run_smart_backtest, run_smart_backtest_with_validation, ADAPTIVE_WEIGHTS
        )
    
        # ── 参数设置 ──
        with st.expander("⚙️ 策略参数（默认值已优化，不懂可以不改）", expanded=False):
            col1, col2, col3 = st.columns(3)
            with col1:
                hold_days = st.selectbox("调仓频率", [10, 20, 40], index=1, key="smart_hz",
                                         help="每隔多少天换一次股票。20天≈1个月。")
                top_n = st.selectbox("持仓股数", [20, 30, 50], index=1, key="smart_topn",
                                     help="同时持有多少只股票。")
                start_year = st.selectbox("回测起始年", list(range(1995, 2025)), index=20, key="smart_sy",
                                          help="越早=验证越充分但耗时越长。数据从1990年就有，但1995年前股票太少(<100只)建议从1995开始。")
            with col2:
                stock_stop_loss = st.selectbox("个股止损线", [-10, -15, -20, -25, -30], index=2, key="smart_sl",
                                               help="单只股票跌多少就卖掉", format_func=lambda x: f"{x}%")
                stock_take_profit = st.selectbox("个股止盈线", [30, 50, 80, 100], index=1, key="smart_tp",
                                                 help="单只股票涨多少就锁定利润", format_func=lambda x: f"+{x}%")
                crash_threshold = st.selectbox("暴跌熔断线", [-3, -4, -5, -6], index=1, key="smart_crash",
                                               help="全市场单日跌幅超过此值时临时减仓", format_func=lambda x: f"{x}%")
            with col3:
                adaptive_mode = st.toggle("🧠 自适应因子权重", value=True, key="smart_adaptive",
                                          help="根据牛/熊/震荡自动调整因子配比")
    
        config = SmartStrategyConfig(
            hold_days=hold_days,
            top_n=top_n,
            stock_stop_loss=stock_stop_loss / 100,
            stock_take_profit=stock_take_profit / 100,
            crash_threshold=crash_threshold / 100,
            adaptive_weights=adaptive_mode,
        )
    
        # ── 执行回测 ──
        from datetime import date as _date
        years_to_test = _date.today().year - start_year
        est_time = max(3, years_to_test * 2)  # 粗估每年约2秒

        @st.cache_data(ttl=3600, max_entries=1, show_spinner=False)
        def _cached_backtest(_start_year, _hold_days, _top_n, _stop_loss, _take_profit, _crash, _adaptive):
            """回测结果缓存：同样的参数1小时内不重复计算"""
            load_start = f"{_start_year - 1}-01-01"
            df = cached_load_daily(load_start, None)
            df_bt = df[df["trade_date"] >= f"{_start_year}-01-01"].copy()
            _config = SmartStrategyConfig(
                hold_days=_hold_days, top_n=_top_n,
                stock_stop_loss=_stop_loss, stock_take_profit=_take_profit,
                crash_threshold=_crash, adaptive_weights=_adaptive,
            )
            return run_smart_backtest_with_validation(df_bt, _config)

        try:
            with st.spinner(f"运行智能策略回测（{start_year}年至今，约{years_to_test}年数据，预计{est_time}秒内完成）..."):
                bt_result, validation = _cached_backtest(
                    start_year, hold_days, top_n,
                    stock_stop_loss / 100, stock_take_profit / 100,
                    crash_threshold / 100, adaptive_mode,
                )
        except FileNotFoundError:
            st.error("⚠️ 数据未准备好，请点击左侧「一键更新数据」按钮")
            st.stop()
        except Exception as e:
            st.error(f"回测出错: {e}")
            import traceback
            st.code(traceback.format_exc())
            st.stop()
    
        if not bt_result.strategy_nav or len(bt_result.strategy_nav) < 10:
            st.warning("回测数据不足，请检查数据或调整参数。")
            st.stop()
    
        # ── 数据校验结果 ──
        if not validation.passed:
            st.error(f"⚠️ 回测校验未通过 ({validation.checks_passed}/{validation.checks_run} 项通过)")
            for err in validation.errors:
                st.markdown(f"- ❌ {err}")
            st.stop()
        elif validation.warnings:
            with st.expander(f"🔍 数据校验: {validation.checks_passed}/{validation.checks_run} 项通过 (有{len(validation.warnings)}条提示)", expanded=False):
                for w in validation.warnings:
                    st.caption(f"⚠️ {w}")
        else:
            st.caption(f"🔍 数据校验: {validation.checks_passed}/{validation.checks_run} 项全部通过 ✅")
    
        # ── 核心结论 ──
        st.markdown("---")
        st.subheader("📋 回测成绩单")
    
        # 判断策略好不好
        if bt_result.excess_return > 5:
            grade_color, grade_border, grade_icon = "#e6f4ea", "#1a7a32", "🏆"
            grade_text = "跑赢市场"
        elif bt_result.excess_return > 0:
            grade_color, grade_border, grade_icon = "#fef9e7", "#b8860b", "✅"
            grade_text = "略微跑赢"
        else:
            grade_color, grade_border, grade_icon = "#fdecea", "#b71c1c", "⚠️"
            grade_text = "未能跑赢"
    
        st.markdown(f"""
        <div style="background:{grade_color}; padding:16px; border-radius:10px; border:1px solid {grade_border}30; margin-bottom:16px;">
            <h3 style="margin:0 0 8px 0; color:#1a1a2e;">{grade_icon} {grade_text}市场 — 超额年化 {bt_result.excess_return:+.1f}%</h3>
            <p style="margin:0; color:#2a2a4a;">
                策略年化 <strong>{bt_result.annual_return:+.1f}%</strong> vs 全市场等权 <strong>{bt_result.benchmark_annual_return:+.1f}%</strong>
                ｜最大回撤 <strong>{bt_result.max_drawdown:.1f}%</strong>
                ｜夏普比率 <strong>{bt_result.sharpe_ratio:.2f}</strong>
            </p>
        </div>
        """, unsafe_allow_html=True)
    
        # 指标卡片
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("策略年化", f"{bt_result.annual_return:+.1f}%",
                  delta=f"超额{bt_result.excess_return:+.1f}%",
                  help="年化收益率：把总收益折算成每年的平均收益。\n\n例如：3年赚了60%，年化≈17%。\n\n'超额'=策略收益 - 全市场平均收益（超额为正说明策略有价值）")
        c2.metric("最大回撤", f"{bt_result.max_drawdown:.1f}%",
                  help="最大回撤：从最高点到最低点的最大跌幅。\n\n例如：-32%意味着如果你在最高点买入，最惨时会亏32%。\n\n这是衡量'你最多要忍受多大亏损'的指标。越小越好。")
        c3.metric("夏普比率", f"{bt_result.sharpe_ratio:.2f}",
                  help="夏普比率(Sharpe Ratio)：每承受1单位风险能获得多少收益。\n\n• >1.5=优秀\n• 1.0-1.5=很好\n• 0.5-1.0=还行\n• <0.5=不太好\n\n简单说：数字越大=赚钱效率越高(收益高且波动小)")
        c4.metric("月度胜率", f"{bt_result.win_rate:.0f}%",
                  help="月度胜率：赚钱的月份占比。\n\n例如：61%意味着每100个月有61个月是赚钱的。\n\n>55%就算不错了，没有策略能做到100%。")
        c5.metric("个股止损", f"{bt_result.stop_loss_count}次",
                  help="在整个回测期间，有多少只股票触发了止损被卖出。\n\n止损=某只股票跌超20%时自动卖掉。\n\n次数多说明市场波动大，但也说明风控在起作用。")
        c6.metric("回测年数", f"{bt_result.backtest_years}年",
                  help="回测使用了多少年的历史数据来验证策略。\n\n年数越长，验证越充分，结果越可信。\n\n一般认为>5年的回测才有参考价值。")

        # ── 小白看得懂的大白话解读 ──
        annual_ret = bt_result.annual_return
        max_dd = bt_result.max_drawdown
        sharpe = bt_result.sharpe_ratio
        win_r = bt_result.win_rate

        # 计算具体金额示例（以1万元为基准）
        annual_end = 10000 * (1 + annual_ret / 100)
        dd_loss = 10000 * (1 + max_dd / 100)  # max_dd is negative

        # 夏普比率评语
        if sharpe >= 1.5:
            sharpe_comment = "非常优秀，赚钱效率很高"
        elif sharpe >= 1.0:
            sharpe_comment = "不错，收益风险比健康"
        elif sharpe >= 0.5:
            sharpe_comment = "一般，波动偏大"
        else:
            sharpe_comment = "较差，承受风险多但收益少"

        st.markdown(f"""
        <div style="background:#f8f9fa; padding:14px 18px; border-radius:10px; margin-top:12px; border-left:4px solid #6c63ff;">
            <p style="margin:0 0 6px 0; font-weight:600; color:#1a1a2e;">📖 大白话解读（假设你投了1万块）</p>
            <table style="width:100%; border-collapse:collapse; color:#2a2a4a; font-size:0.92em;">
                <tr style="border-bottom:1px solid #eee;">
                    <td style="padding:6px 4px;"><strong>年化收益 {annual_ret:+.1f}%</strong></td>
                    <td style="padding:6px 4px;">→ 投入1万，一年后变成 <strong>{annual_end:,.0f}元</strong>（{'赚' if annual_ret > 0 else '亏'}{abs(annual_end - 10000):,.0f}元）</td>
                </tr>
                <tr style="border-bottom:1px solid #eee;">
                    <td style="padding:6px 4px;"><strong>最大回撤 {max_dd:.1f}%</strong></td>
                    <td style="padding:6px 4px;">→ 最惨的时候从高点下跌{abs(max_dd):.0f}%，1万变成 <strong>{dd_loss:,.0f}元</strong>（但后来涨回来了）</td>
                </tr>
                <tr style="border-bottom:1px solid #eee;">
                    <td style="padding:6px 4px;"><strong>夏普比率 {sharpe:.2f}</strong></td>
                    <td style="padding:6px 4px;">→ 每承担1份风险获得{sharpe:.1f}份收益 — <strong>{sharpe_comment}</strong>（>1就算不错）</td>
                </tr>
                <tr>
                    <td style="padding:6px 4px;"><strong>月度胜率 {win_r:.0f}%</strong></td>
                    <td style="padding:6px 4px;">→ 每10个月有{win_r / 10:.0f}个月在赚钱，{'多数时候是正收益' if win_r > 55 else '赢面和输面差不多'}</td>
                </tr>
            </table>
        </div>
        """, unsafe_allow_html=True)

        st.divider()
    
        # ── 第一张图: 净值对比 (最重要的图) ──
        st.subheader("📈 策略 vs 大盘指数 — 谁赚得多？")
        st.markdown("""
        下面这张图是**最核心的**——蓝线是策略的表现，和真实的大盘指数对比。
    
        - 🔵 **蓝色粗线** = 策略净值（你用这个策略能赚多少）
        - 🔴 **红色线** = 沪深300指数（大盘蓝筹代表，如果你定投沪深300ETF）
        - 🟠 **橙色线** = 中证500指数（中盘股代表）
        - ⚫ **灰色虚线** = 全市场等权（所有股票平均）
        - 🟡 **黄色细线** = 货基（余额宝，年化约2%）
    
        **蓝线在红线上方 = 策略跑赢大盘。** 量化策略的核心价值就是持续跑赢指数。
        """)
    
        # 获取真实指数数据
        from src.strategy.smart_strategy import fetch_index_nav
        index_data = fetch_index_nav(str(bt_result.nav_dates[0])[:10], str(bt_result.nav_dates[-1])[:10])
    
        fig_nav = go.Figure()
    
        # 策略净值
        fig_nav.add_trace(go.Scatter(
            x=bt_result.nav_dates[:len(bt_result.strategy_nav)],
            y=bt_result.strategy_nav,
            name="📈 策略净值",
            line=dict(color="#1f77b4", width=3),
        ))
    
        # 沪深300
        if "hs300" in index_data:
            fig_nav.add_trace(go.Scatter(
                x=index_data["hs300"]["dates"],
                y=index_data["hs300"]["nav"],
                name=f"沪深300 (年化{index_data['hs300']['annual_return']:+.1f}%)",
                line=dict(color="#d62728", width=1.8),
            ))
    
        # 中证500
        if "zz500" in index_data:
            fig_nav.add_trace(go.Scatter(
                x=index_data["zz500"]["dates"],
                y=index_data["zz500"]["nav"],
                name=f"中证500 (年化{index_data['zz500']['annual_return']:+.1f}%)",
                line=dict(color="#ff7f0e", width=1.5),
            ))
    
        # 全市场等权基准
        bm_len = min(len(bt_result.benchmark_nav), len(bt_result.nav_dates))
        fig_nav.add_trace(go.Scatter(
            x=bt_result.nav_dates[:bm_len],
            y=bt_result.benchmark_nav[:bm_len],
            name="全市场等权",
            line=dict(color="#888888", width=1.2, dash="dash"),
        ))
    
        # 货基
        cash_len = min(len(bt_result.cash_nav), len(bt_result.nav_dates))
        fig_nav.add_trace(go.Scatter(
            x=bt_result.nav_dates[:cash_len],
            y=bt_result.cash_nav[:cash_len],
            name="货基(年化2%)",
            line=dict(color="#f0ad4e", width=1, dash="dot"),
        ))
    
        fig_nav.add_hline(y=1.0, line_color="gray", opacity=0.3)
        fig_nav.update_layout(
            height=450,
            yaxis_title="净值（1元变成了多少）",
            legend=dict(orientation="h", y=-0.18, font=dict(size=11)),
            margin=dict(t=30),
            plot_bgcolor="#fafafa",
            hovermode="x unified",
        )
        st.plotly_chart(fig_nav, width="stretch")
    
        # 净值解读
        final_strat = bt_result.strategy_nav[-1]
        final_bm = bt_result.benchmark_nav[-1] if bt_result.benchmark_nav else 1
        hs300_txt = ""
        if "hs300" in index_data:
            hs300_final = index_data["hs300"]["final_nav"]
            hs300_annual = index_data["hs300"]["annual_return"]
            hs300_txt = f"沪深300变成 **{hs300_final:.2f}元** (年化{hs300_annual:+.1f}%)，"
    
        st.markdown(f"""
        > **结果:** 投入1元 → 策略变成 **{final_strat:.2f}元** (年化{bt_result.annual_return:+.1f}%)，
        > {hs300_txt}全市场等权变成 **{final_bm:.2f}元** (年化{bt_result.benchmark_annual_return:+.1f}%)。
        """)
    
        # 超额对比表格
        if index_data:
            st.markdown("#### 策略 vs 各基准的超额收益")
            compare_data = [{"基准": "全市场等权", "基准年化": f"{bt_result.benchmark_annual_return:+.1f}%",
                             "策略超额": f"{bt_result.excess_return:+.1f}%/年"}]
            if "hs300" in index_data:
                excess_hs300 = bt_result.annual_return - index_data["hs300"]["annual_return"]
                compare_data.append({"基准": "沪深300(大盘)", "基准年化": f"{index_data['hs300']['annual_return']:+.1f}%",
                                     "策略超额": f"{excess_hs300:+.1f}%/年"})
            if "zz500" in index_data:
                excess_zz500 = bt_result.annual_return - index_data["zz500"]["annual_return"]
                compare_data.append({"基准": "中证500(中盘)", "基准年化": f"{index_data['zz500']['annual_return']:+.1f}%",
                                     "策略超额": f"{excess_zz500:+.1f}%/年"})
            compare_data.append({"基准": "货币基金", "基准年化": "+2.0%",
                                 "策略超额": f"{bt_result.annual_return - 2:+.1f}%/年"})
            st.dataframe(pd.DataFrame(compare_data), width="stretch", hide_index=True)
    
        st.divider()
    
        # ── 第二张图: 仓位状态 ──
        st.subheader("🚦 仓位与市场状态")
        st.markdown("""
        V3策略**始终保持95%高仓位**，不做择时。只有在全市场暴跌(单日>4%)时会临时减仓。
        下图显示每个调仓时点的市场状态（仅用于调整选股因子配比，不影响仓位）。
        """)
    
        if bt_result.position_history:
            pos_dates = [p[0] for p in bt_result.position_history]
            pos_values = [p[1] * 100 for p in bt_result.position_history]
            pos_regimes = [p[2] for p in bt_result.position_history]
    
            # 颜色根据市场状态
            pos_colors = []
            for r in pos_regimes:
                if r == "bull":
                    pos_colors.append("#d62728")
                elif r == "bear":
                    pos_colors.append("#2ca02c")
                else:
                    pos_colors.append("#f0ad4e")
    
            fig_pos = go.Figure()
            fig_pos.add_trace(go.Bar(
                x=pos_dates, y=pos_values,
                marker_color=pos_colors,
                name="仓位%",
            ))
            fig_pos.update_layout(
                height=250,
                yaxis_title="仓位 %",
                yaxis=dict(range=[0, 105]),
                margin=dict(t=20, b=20),
                plot_bgcolor="#fafafa",
            )
            st.plotly_chart(fig_pos, width="stretch")
    
            # 统计
            bull_pct = sum(1 for r in pos_regimes if r == "bull") / len(pos_regimes) * 100
            bear_pct = sum(1 for r in pos_regimes if r == "bear") / len(pos_regimes) * 100
            neutral_pct = 100 - bull_pct - bear_pct
            st.markdown(f"""
            > 📊 回测期间市场状态分布：🟢 牛市 {bull_pct:.0f}% | 🟡 震荡 {neutral_pct:.0f}% | 🔴 熊市 {bear_pct:.0f}%
            > | 平均仓位 {bt_result.avg_position:.0f}%
            """)
    
        # ── 自适应权重变化图 ──
        if bt_result.adaptive_mode and bt_result.weight_history:
            st.markdown("#### 🧠 因子权重随行情的变化")
            st.markdown("下图展示策略在每个调仓时点使用的因子配比。颜色越深=该因子权重越大。")
    
            wh_dates = [w[0] for w in bt_result.weight_history]
            wh_regimes = [w[1] for w in bt_result.weight_history]
            wh_weights = [w[2] for w in bt_result.weight_history]
    
            w_mom = [w.get("momentum", 0) * 100 for w in wh_weights]
            w_rev = [w.get("reversal", 0) * 100 for w in wh_weights]
            w_lv = [w.get("low_vol", 0) * 100 for w in wh_weights]
            w_vs = [w.get("volume_shrink", 0) * 100 for w in wh_weights]
    
            fig_weights = go.Figure()
            fig_weights.add_trace(go.Scatter(
                x=wh_dates, y=w_mom, name="动量(追涨)",
                stackgroup="one", line=dict(width=0),
                fillcolor="rgba(31, 119, 180, 0.7)",
            ))
            fig_weights.add_trace(go.Scatter(
                x=wh_dates, y=w_rev, name="反转(抄底)",
                stackgroup="one", line=dict(width=0),
                fillcolor="rgba(44, 160, 44, 0.7)",
            ))
            fig_weights.add_trace(go.Scatter(
                x=wh_dates, y=w_lv, name="低波(防守)",
                stackgroup="one", line=dict(width=0),
                fillcolor="rgba(255, 127, 14, 0.7)",
            ))
            fig_weights.add_trace(go.Scatter(
                x=wh_dates, y=w_vs, name="缩量(蓄势)",
                stackgroup="one", line=dict(width=0),
                fillcolor="rgba(148, 103, 189, 0.5)",
            ))
            fig_weights.update_layout(
                height=250,
                yaxis_title="权重 %",
                yaxis=dict(range=[0, 100]),
                legend=dict(orientation="h", y=-0.2, font=dict(size=11)),
                margin=dict(t=20, b=20),
                plot_bgcolor="#fafafa",
            )
            st.plotly_chart(fig_weights, width="stretch")
            st.caption("牛市时蓝色(动量)面积大=追涨；熊市时绿色(反转)+橙色(低波)面积大=防守。策略自动切换打法。")
    
        st.divider()
    
        # ── 第三张图: 分年度表现 ──
        st.subheader("📅 分年度表现 — 每一年赚了多少？")
        st.markdown("""
        策略不可能每年都赚钱（尤其在大熊市），关键是看：
        - 市场涨的年份，策略是否跟上了？
        - 市场跌的年份，策略是否少亏了？
        """)
    
        if bt_result.yearly_returns:
            years = sorted(bt_result.yearly_returns.keys())
            strat_rets = [bt_result.yearly_returns[y] for y in years]
            excess_rets = [bt_result.yearly_excess.get(y, 0) for y in years]
    
            fig_yearly = go.Figure()
            fig_yearly.add_trace(go.Bar(
                x=[str(y) for y in years],
                y=strat_rets,
                name="策略收益",
                marker_color=["#d62728" if r > 0 else "#2ca02c" for r in strat_rets],
                text=[f"{r:+.1f}%" for r in strat_rets],
                textposition="outside",
                textfont=dict(size=12, color="#1a1a2e"),
            ))
            fig_yearly.add_trace(go.Bar(
                x=[str(y) for y in years],
                y=excess_rets,
                name="超额收益(vs市场)",
                marker_color=["#1f77b4" if r > 0 else "#ff9800" for r in excess_rets],
                text=[f"{r:+.1f}%" for r in excess_rets],
                textposition="outside",
                textfont=dict(size=11, color="#3a3a5a"),
                opacity=0.7,
            ))
            fig_yearly.update_layout(
                height=350,
                barmode="group",
                yaxis_title="收益率 %",
                legend=dict(orientation="h", y=-0.15),
                margin=dict(t=30),
                plot_bgcolor="#fafafa",
            )
            fig_yearly.add_hline(y=0, line_color="gray", opacity=0.5)
            st.plotly_chart(fig_yearly, width="stretch")
    
            st.markdown("""
            > 💡 **红色/绿色** = 策略当年是赚是亏 | **蓝色/橙色** = 和市场比是跑赢还是跑输。
            > 即使策略亏钱，只要蓝色>0，说明它比"不用策略瞎买"要好。
            """)
    
        st.divider()
    
        # ── 策略解读总结 ──
        st.subheader("🧠 策略总结")
    
        st.markdown(f"""
        <div style="background:#f0f4f8; padding:20px; border-radius:12px;">
            <table style="width:100%; border-collapse:collapse; color:#2a2a4a;">
                <tr style="border-bottom:1px solid #ddd;">
                    <td style="padding:10px; font-weight:600; width:30%;">📌 策略核心逻辑</td>
                    <td style="padding:10px;">市场好时满仓多因子选股，市场差时轻仓避险</td>
                </tr>
                <tr style="border-bottom:1px solid #ddd;">
                    <td style="padding:10px; font-weight:600;">📈 年化收益</td>
                    <td style="padding:10px;">{bt_result.annual_return:+.1f}%（市场 {bt_result.benchmark_annual_return:+.1f}%）</td>
                </tr>
                <tr style="border-bottom:1px solid #ddd;">
                    <td style="padding:10px; font-weight:600;">🎯 超额收益</td>
                    <td style="padding:10px;">{bt_result.excess_return:+.1f}% / 年</td>
                </tr>
                <tr style="border-bottom:1px solid #ddd;">
                    <td style="padding:10px; font-weight:600;">📉 最大回撤</td>
                    <td style="padding:10px;">{bt_result.max_drawdown:.1f}%（有止损保护）</td>
                </tr>
                <tr style="border-bottom:1px solid #ddd;">
                    <td style="padding:10px; font-weight:600;">📐 夏普比率</td>
                    <td style="padding:10px;">{bt_result.sharpe_ratio:.2f} ({'优秀' if bt_result.sharpe_ratio > 1 else '不错' if bt_result.sharpe_ratio > 0.5 else '一般' if bt_result.sharpe_ratio > 0 else '较差'})</td>
                </tr>
                <tr style="border-bottom:1px solid #ddd;">
                    <td style="padding:10px; font-weight:600;">🚦 择时效果</td>
                    <td style="padding:10px;">平均仓位 {bt_result.avg_position:.0f}%，触发止损 {bt_result.stop_loss_count} 次</td>
                </tr>
                <tr>
                    <td style="padding:10px; font-weight:600;">⚖️ 月度胜率</td>
                    <td style="padding:10px;">{bt_result.win_rate:.0f}%</td>
                </tr>
            </table>
        </div>
        """, unsafe_allow_html=True)
    
        # 给出实际建议
        st.markdown("#### 💡 这个策略能直接用吗？")
        if bt_result.excess_return > 5 and bt_result.sharpe_ratio > 0.5:
            st.success(f"""
            策略表现不错，超额收益 {bt_result.excess_return:+.1f}%/年，夏普 {bt_result.sharpe_ratio:.2f}。
    
            **但请注意：**
            - 回测 ≠ 实盘。实际交易还有滑点、流动性、情绪干扰等问题
            - 建议先用少量资金（总资产的10-20%）模拟跟踪1-2个月
            - 关注「市场温度」来辅助判断当前是否适合执行策略
            - 如果连续2个月跑输市场超过5%，停下来检查市场环境是否变了
            """)
        elif bt_result.excess_return > 0:
            st.info(f"""
            策略略微跑赢市场 {bt_result.excess_return:+.1f}%/年，有一定价值但优势不大。
    
            **建议：**
            - 尝试调整参数（如加大择时力度、调整因子权重）
            - 关注是否有某些年份拖了后腿
            - 这种微弱优势在实盘中可能被交易成本吃掉
            """)
        else:
            st.warning(f"""
            策略未能跑赢市场（超额 {bt_result.excess_return:+.1f}%）。可能原因：
    
            1. **回测时间段主要是熊市**：2022-2024年A股环境极端恶劣，大部分策略都失效
            2. **择时参数可能需要调整**：试试降低牛市判定阈值，让策略更保守
            3. **因子在当前环境失效**：市场风格极端集中（如全炒AI），分散化策略反而跑输
    
            **别灰心——策略的价值在于"少亏"而非"暴赚"。** 如果市场跌了20%而你只跌了10%，这就是风控的价值。
            
            **试试：** 把回测起始年改成2020或2021，看看包含2020年反弹时策略的整体表现。
            """)
    
        with st.expander("❓ 常见问题", expanded=False):
            st.markdown("""
            **Q: 策略显示亏钱，是不是策略有问题？**
            A: 不一定。理解这一点很重要：**A股2015-2024这10年，全市场等权指数年化-16%。**
            在一个10年大熊市里，做多策略不亏钱几乎不可能（除非你有做空工具）。
            策略的价值不是"绝对赚钱"，而是"比市场好很多"。
    
            **Q: 量化私募是怎么做到绝对正收益的？**
            A: 他们有三个你没有的武器：
            1. **股指期货对冲**：选股多头 + 做空股指 = 对冲掉市场下跌，只保留选股的超额收益
            2. **融券做空**：直接做空差股票，熊市也能赚钱
            3. **高频交易**：毫秒级别的价差套利，和市场涨跌无关
    
            我们的策略做到了超额+15%/年（和专业量化机构的alpha能力相当），
            但因为没有对冲工具，在熊市中绝对收益会是负的。
    
            **Q: 那我该怎么用这个策略？**
            A: 两种方式：
            1. **结合市场温度择时**：温度>60°时空仓，温度<40°时按策略买入。不是每时每刻都在场。
            2. **当做选股参考**：策略告诉你"如果要买股票，买这些"。至于"要不要买"，看市场温度。
    
            **Q: 择时准确吗？会不会经常踏空？**
            A: 择时不追求精确抄底逃顶，而是"大概率对"。会有踏空（减仓后市场反弹了），
            但长期来看，避开大跌的价值 > 偶尔踏空的损失。
    
            **Q: 小市值因子会不会有一天失效？**
            A: 有可能。2024年初"微盘股崩盘"就是一次。但学术上小市值效应在全球市场持续了50年，
            短期失效不代表长期无效。策略通过择时+多因子组合来降低单一因子失效的风险。
    
            **Q: 为什么超额收益这么高但绝对收益是负的？**
            A: 因为基准（全市场等权）跌了-16%/年。你跑赢市场15%，-16%+15% ≈ -1%。
            如果未来市场走牛（比如涨10%/年），策略就是 10%+15% = 25%/年。
            **超额收益才是策略的真正能力，绝对收益取决于市场环境。**
            """)
    
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 深度分析面板（策略验证全套）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        st.divider()
        st.subheader("🔬 策略深度验证")
        st.caption("以下分析从多个维度证明策略的有效性。点击展开查看详细数据。")
    
        # ── 分析1: 单因子有效性 ──
        with st.expander("📊 验证1: 每个因子独立有效吗？", expanded=False):
            st.markdown("""
            策略用了5个因子组合选股。下面逐一检验每个因子**单独使用**是否有效。
            如果单个因子无效，放进组合也没意义。
    
            **判定标准：**
            - |ICIR| > 0.5 且 多空超额 > 10% → ✅ 强有效
            - |ICIR| > 0.3 或 多空超额 > 5% → ⚠️ 弱有效
            - 都不满足 → ❌ 无效
            """)
    
            try:
                from src.strategy.strategy_analyzer import analyze_all_factors
                with st.spinner("逐因子检验中(约40秒)..."):
                    factor_results = analyze_all_factors(df_bt, hold_days=20)
    
                # 汇总表
                factor_table = []
                for name, info in factor_results.items():
                    factor_table.append({
                        "因子": name,
                        "ICIR": f"{info['ic']['icir']:.3f}",
                        "IC正比例": f"{info['ic']['positive_pct']:.0f}%",
                        "多空超额/年": f"{info['quintile']['spread']:+.1f}%",
                        "单调性": "✓" if info["quintile"]["monotonic"] else "✗",
                        "判定": info["verdict"],
                    })
                st.dataframe(pd.DataFrame(factor_table), width="stretch", hide_index=True)
    
                st.markdown("""
                > **解读：** ICIR的绝对值越大越好(>0.5=好因子)。多空超额=按因子选出最好的一组 vs 最差的一组，
                > 年化收益差多少。单调性=各组是否从高到低/低到高排列。
                """)
            except Exception as e:
                st.warning(f"因子分析失败: {e}")
    
        # ── 分析2: 因子相关性 ──
        with st.expander("🔗 验证2: 因子之间是否冗余？", expanded=False):
            st.markdown("""
            如果两个因子高度相关(相关系数>0.5)，同时使用它们等于重复计算，没有额外价值。
            好的组合应该用**低相关**的因子——它们提供不同维度的信息。
            """)
    
            try:
                from src.strategy.strategy_analyzer import compute_factor_correlation
                corr = compute_factor_correlation(df_bt)
                if not corr.empty:
                    st.dataframe(corr, width="stretch")
                    # 检查是否有高相关
                    high_corr = []
                    for i in range(len(corr)):
                        for j in range(i+1, len(corr)):
                            val = abs(corr.iloc[i, j])
                            if val > 0.5:
                                high_corr.append(f"{corr.index[i]} vs {corr.columns[j]}: {corr.iloc[i,j]:.2f}")
                    if high_corr:
                        st.warning(f"⚠️ 存在高相关因子对: {', '.join(high_corr)}")
                    else:
                        st.success("✅ 所有因子间相关性<0.5，组合有效，没有冗余。")
                else:
                    st.info("数据不足，无法计算相关性")
            except Exception as e:
                st.warning(f"相关性分析失败: {e}")
    
        # ── 分析3: 参数敏感性 ──
        with st.expander("🎛️ 验证3: 改参数后策略会崩吗？(参数稳定性)", expanded=False):
            st.markdown("""
            **核心问题：** 你的参数(调仓20天、持仓30只、止损-20%)是不是恰好选了最好的？
            如果改一点策略就崩，说明"过拟合"——只对历史有效，未来大概率失效。
    
            下面测试：把参数在合理范围内变化，看策略表现是否稳定。
            """)
    
            try:
                from src.strategy.strategy_analyzer import run_sensitivity_test
                with st.spinner("运行参数敏感性测试(约2分钟)..."):
                    sens_hold = run_sensitivity_test(df_bt, "hold_days", [10, 15, 20, 25, 30, 40])
                    sens_topn = run_sensitivity_test(df_bt, "top_n", [15, 20, 30, 40, 50])
                    sens_stop = run_sensitivity_test(df_bt, "stock_stop_loss", [-0.10, -0.15, -0.20, -0.25, -0.30])
    
                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    st.markdown("**调仓频率(天)**")
                    df_s = pd.DataFrame(sens_hold)
                    df_s.columns = ["调仓天数", "年化%", "夏普", "回撤%", "胜率%"]
                    st.dataframe(df_s, hide_index=True)
                with col_b:
                    st.markdown("**持仓股数**")
                    df_s2 = pd.DataFrame(sens_topn)
                    df_s2.columns = ["持仓数", "年化%", "夏普", "回撤%", "胜率%"]
                    st.dataframe(df_s2, hide_index=True)
                with col_c:
                    st.markdown("**止损线**")
                    df_s3 = pd.DataFrame(sens_stop)
                    df_s3.columns = ["止损%", "年化%", "夏普", "回撤%", "胜率%"]
                    st.dataframe(df_s3, hide_index=True)
    
                # 判断稳定性
                sharpes = [r["sharpe"] for r in sens_hold]
                sharpe_range = max(sharpes) - min(sharpes)
                if sharpe_range < 0.5:
                    st.success(f"✅ 参数稳定！调仓频率从10天到40天，夏普比率波动仅{sharpe_range:.2f}（<0.5=稳健）")
                else:
                    st.warning(f"⚠️ 参数敏感度偏高，夏普波动{sharpe_range:.2f}。选用默认值是合理的但非唯一解。")
            except Exception as e:
                st.warning(f"敏感性测试失败: {e}")
    
        # ── 分析4: 月度收益分布 ──
        with st.expander("📅 验证4: 收益是稳定赚的还是靠几个月暴赚？", expanded=False):
            st.markdown("""
            好策略的月度收益应该**分布均匀**——大多数月份小赚，偶尔月份小亏。
            坏策略靠1-2个月暴赚拉高年化，其余时间都在亏——这种不可持续。
            """)
    
            try:
                from src.strategy.strategy_analyzer import compute_monthly_returns
                monthly_df = compute_monthly_returns(bt_result.strategy_nav, bt_result.nav_dates)
                if not monthly_df.empty:
                    rets = monthly_df["月收益%"].values
                    pos_months = (rets > 0).sum()
                    neg_months = (rets <= 0).sum()
                    avg_pos = rets[rets > 0].mean() if pos_months > 0 else 0
                    avg_neg = rets[rets <= 0].mean() if neg_months > 0 else 0
    
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("盈利月份", f"{pos_months}个月", help="月收益>0的月数")
                    c2.metric("亏损月份", f"{neg_months}个月", help="月收益≤0的月数")
                    c3.metric("平均盈利月", f"+{avg_pos:.1f}%", help="赚钱的月份平均赚多少")
                    c4.metric("平均亏损月", f"{avg_neg:.1f}%", help="亏钱的月份平均亏多少")
    
                    # 月度收益柱状图
                    fig_monthly = go.Figure(go.Bar(
                        x=monthly_df["月份"],
                        y=monthly_df["月收益%"],
                        marker_color=["#d62728" if r > 0 else "#2ca02c" for r in rets],
                    ))
                    fig_monthly.add_hline(y=0, line_color="gray")
                    fig_monthly.update_layout(height=250, yaxis_title="月收益%", margin=dict(t=20, b=20))
                    st.plotly_chart(fig_monthly, width="stretch")
    
                    # 盈亏比
                    if avg_neg != 0:
                        profit_loss_ratio = abs(avg_pos / avg_neg)
                        st.markdown(f"> **盈亏比** = {profit_loss_ratio:.2f} (平均赚÷平均亏，>1.5=好，>2=优秀)")
            except Exception as e:
                st.warning(f"月度分析失败: {e}")
    
        # ── 分析5: 回撤深度分析 ──
        with st.expander("📉 验证5: 最大回撤详情（亏多久、亏多深）", expanded=False):
            st.markdown("列出历史上所有超过-5%的回撤事件，帮你理解'最坏情况能有多坏'。")
    
            try:
                from src.strategy.strategy_analyzer import compute_drawdown_analysis
                dd_analysis = compute_drawdown_analysis(bt_result.strategy_nav, bt_result.nav_dates)
    
                st.metric("水下时间占比", f"{dd_analysis['underwater_pct']}%",
                          help="处于亏损状态(低于历史最高点)的天数占比。越低越好。<50%=不错。")
    
                if dd_analysis["worst_drawdowns"]:
                    st.markdown("**最严重的5次回撤：**")
                    dd_table = pd.DataFrame(dd_analysis["worst_drawdowns"])
                    dd_table.columns = ["开始日期", "最深日期", "恢复日期", "最大跌幅%", "持续天数"]
                    st.dataframe(dd_table, width="stretch", hide_index=True)
    
                # 滚动回撤曲线
                dd_dates = [d for d, _ in dd_analysis["rolling_drawdown"]]
                dd_vals = [v for _, v in dd_analysis["rolling_drawdown"]]
                fig_dd = go.Figure(go.Scatter(
                    x=dd_dates, y=dd_vals,
                    fill="tozeroy", fillcolor="rgba(214,39,40,0.2)",
                    line=dict(color="#d62728", width=1),
                ))
                fig_dd.update_layout(height=200, yaxis_title="回撤%", margin=dict(t=10, b=20))
                st.plotly_chart(fig_dd, width="stretch")
            except Exception as e:
                st.warning(f"回撤分析失败: {e}")
    
        # ── 分析6: 样本内外验证 ──
        with st.expander("🧪 验证6: 策略会不会过拟合？(样本外验证)", expanded=False):
            st.markdown("""
            **过拟合** = 策略只对历史数据有效，对未来无效（相当于"背答案"）。
    
            检验方法：把数据分成两段——
            - 前70%：假装这是"历史"（样本内）
            - 后30%：假装这是"未来"（样本外）
    
            如果策略在"未来"数据上表现远差于"历史" → 过拟合。
            如果两者接近或样本外更好 → 策略稳健，不是过拟合。
            """)
    
            try:
                from src.strategy.strategy_analyzer import run_in_out_sample_test
                with st.spinner("运行样本内外验证(约30秒)..."):
                    oos = run_in_out_sample_test(df_bt, split_ratio=0.7)
    
                col_in, col_out = st.columns(2)
                with col_in:
                    st.markdown(f"**样本内(训练期)**")
                    st.caption(oos["in_sample"]["period"])
                    st.metric("年化收益", f"{oos['in_sample']['annual_return']:+.1f}%")
                    st.metric("夏普比率", f"{oos['in_sample']['sharpe']:.2f}")
                with col_out:
                    st.markdown(f"**样本外(验证期)**")
                    st.caption(oos["out_sample"]["period"])
                    st.metric("年化收益", f"{oos['out_sample']['annual_return']:+.1f}%")
                    st.metric("夏普比率", f"{oos['out_sample']['sharpe']:.2f}")
    
                deg = oos["degradation_pct"]
                if deg <= 0:
                    st.success(f"✅ 样本外表现优于样本内(衰减{deg:.0f}%)——策略没有过拟合，在新数据上更好！")
                elif deg < 30:
                    st.info(f"✅ 样本外轻微衰减{deg:.0f}%——在正常范围内，策略稳健。")
                else:
                    st.warning(f"⚠️ 样本外衰减{deg:.0f}%——有一定过拟合风险，建议保守使用。")
            except Exception as e:
                st.warning(f"样本内外验证失败: {e}")
    
        # ── 分析7: 滚动回测(策略是否在衰退) ──
        with st.expander("📈 验证7: 策略最近还有效吗？(滚动年化)", expanded=False):
            st.markdown("""
            策略可能过去有效但最近失效了（因子衰退）。
            下面展示**滚动1年年化收益**——每个点是"从这天往前看1年的年化"。
    
            如果曲线一直在0以上 = 策略持续有效。
            如果曲线最近跌到0以下 = 策略可能在衰退。
            """)
    
            try:
                nav_arr = np.array(bt_result.strategy_nav)
                if len(nav_arr) > 252:
                    # 滚动252天年化
                    rolling_annual = []
                    rolling_dates = []
                    for i in range(252, len(nav_arr)):
                        roll_ret = (nav_arr[i] / nav_arr[i - 252]) - 1
                        annual = roll_ret * 100  # 1年期直接就是年化
                        rolling_annual.append(annual)
                        rolling_dates.append(bt_result.nav_dates[i])
    
                    fig_rolling = go.Figure()
                    fig_rolling.add_trace(go.Scatter(
                        x=rolling_dates, y=rolling_annual,
                        name="滚动1年收益",
                        line=dict(color="#1f77b4", width=1.5),
                        fill="tozeroy",
                        fillcolor="rgba(31,119,180,0.1)",
                    ))
                    fig_rolling.add_hline(y=0, line_color="gray", line_dash="dash")
                    fig_rolling.add_hline(y=20, line_color="green", line_dash="dot",
                                          annotation_text="年化20%", annotation_position="top left")
                    fig_rolling.update_layout(
                        height=280, yaxis_title="滚动1年年化收益%",
                        margin=dict(t=20, b=20), plot_bgcolor="#fafafa",
                    )
                    st.plotly_chart(fig_rolling, width="stretch")
    
                    # 统计
                    above_zero_pct = sum(1 for r in rolling_annual if r > 0) / len(rolling_annual) * 100
                    recent_annual = rolling_annual[-1] if rolling_annual else 0
                    st.markdown(f"""
                    > **滚动收益>0的时间占比:** {above_zero_pct:.0f}%（越高越好，>70%=优秀）
                    > **最近1年年化:** {recent_annual:+.1f}%
                    """)
    
                    if above_zero_pct > 70 and recent_annual > 0:
                        st.success("✅ 策略持续有效，最近仍在赚钱。")
                    elif recent_annual > 0:
                        st.info("✅ 策略最近有效，但历史上有部分时段表现不佳。")
                    else:
                        st.warning("⚠️ 策略最近1年表现不佳，关注是否发生了风格切换。")
                else:
                    st.info("数据不足1年，无法计算滚动年化。")
            except Exception as e:
                st.warning(f"滚动回测失败: {e}")
    
        # ── 分析8: 因子贡献归因 ──
        with st.expander("🧩 验证8: 赚钱主要靠哪个因子？(归因分析)", expanded=False):
            st.markdown("""
            策略用了5个因子组合。但收益到底主要来自哪个因子？
            如果90%的收益来自一个因子，其他4个就是摆设。
            好的组合应该每个因子都贡献一部分。
            """)
    
            try:
                from src.strategy.strategy_analyzer import compute_factor_quintile_returns
    
                # 用验证1的结果(如果有)，否则重新算
                if 'factor_results' in dir():
                    fr = factor_results
                else:
                    from src.strategy.strategy_analyzer import analyze_all_factors
                    with st.spinner("计算因子归因..."):
                        fr = analyze_all_factors(df_bt, hold_days=20)
    
                # 展示各因子的独立alpha
                contrib_data = []
                for name, info in fr.items():
                    spread = info["quintile"]["spread"]
                    contrib_data.append({"因子": name, "独立多空超额(年化)": f"{spread:+.1f}%"})
    
                st.dataframe(pd.DataFrame(contrib_data), width="stretch", hide_index=True)
    
                st.markdown("""
                > **解读：** "独立多空超额"= 如果只用这一个因子选股能赚多少。
                > 各因子超额加起来 > 策略实际超额，是因为因子间有部分重叠。
                > 关键看：是不是所有因子都>0？如果有<0的，说明那个因子在当前时段拖后腿了。
                """)
    
                # 可视化
                names = [d["因子"] for d in contrib_data]
                spreads = [info["quintile"]["spread"] for info in fr.values()]
                fig_attr = go.Figure(go.Bar(
                    x=names, y=spreads,
                    marker_color=["#d62728" if s > 0 else "#2ca02c" for s in spreads],
                    text=[f"{s:+.1f}%" for s in spreads],
                    textposition="outside",
                ))
                fig_attr.add_hline(y=0, line_color="gray")
                fig_attr.update_layout(height=280, yaxis_title="独立年化超额%", margin=dict(t=20, b=20), plot_bgcolor="#fafafa")
                st.plotly_chart(fig_attr, width="stretch")
    
            except Exception as e:
                st.warning(f"归因分析失败: {e}")


    with tab2:
        st.subheader("📡 实盘信号")
        st.caption("基于V3策略生成的当前可执行交易计划。每20个交易日更新一次。")

        try:
            from src.strategy.live_signal import generate_live_signal, SIGNAL_DIR

            # 尝试加载最近的已保存信号
            latest_signal_path = SIGNAL_DIR / "latest.json"
            cached_signal = None
            if latest_signal_path.exists():
                import json as _json
                cached_signal = _json.loads(latest_signal_path.read_text())

            col_action, col_info = st.columns([1, 2])
            with col_action:
                gen_signal = st.button("🔄 生成最新信号", type="primary",
                                       help="基于最新数据重新计算策略信号（约10秒）")
            with col_info:
                if cached_signal:
                    st.caption(f"📋 上次信号日期: {cached_signal.get('date', 'N/A')}")

            if gen_signal:
                with st.spinner("正在计算实盘信号（约10秒）..."):
                    signal = generate_live_signal(capital=100000)
            elif cached_signal:
                # 展示缓存的信号
                signal = cached_signal
            else:
                signal = None

            if signal and "error" not in signal:
                # 市场状态
                regime_map = {"bull": "🟢 牛市", "neutral": "🟡 震荡", "bear": "🔴 熊市"}
                regime_text = regime_map.get(signal.get("market_regime", ""), "未知")

                st.markdown(f"""
                <div style="background:#f0f4f8; padding:12px 16px; border-radius:8px; margin-bottom:16px; border-left:4px solid #1f77b4;">
                    <strong>📅 信号日期:</strong> {signal.get('date', 'N/A')} &nbsp;|&nbsp;
                    <strong>市场状态:</strong> {regime_text} &nbsp;|&nbsp;
                    <strong>均线上方:</strong> {signal.get('market_above_ma_pct', 'N/A')}%
                </div>
                """, unsafe_allow_html=True)

                # 风控提醒
                risk_alerts = signal.get("risk_alerts", [])
                if risk_alerts:
                    for alert in risk_alerts:
                        st.warning(alert)

                # 调仓计划
                rebalance = signal.get("rebalance_plan", signal.get("rebalance", {}))
                if rebalance:
                    st.markdown("#### 📊 调仓计划")
                    rc1, rc2, rc3, rc4 = st.columns(4)
                    rc1.metric("买入", f"{rebalance.get('buy_count', len(rebalance.get('buy', [])))} 只")
                    rc2.metric("卖出", f"{rebalance.get('sell_count', len(rebalance.get('sell', [])))} 只")
                    rc3.metric("持有", f"{rebalance.get('hold_count', len(rebalance.get('hold', [])))} 只")
                    rc4.metric("换手率", f"{rebalance.get('turnover_pct', 0)}%")

                # 目标持仓列表
                portfolio = signal.get("target_portfolio", signal.get("portfolio", []))
                if portfolio:
                    st.markdown("#### 🛒 目标持仓清单")

                    # 操作指南
                    guide = signal.get("operation_guide", {})
                    if guide:
                        st.markdown(f"""
                        > 💰 **资金分配**: 总资金 ¥{guide.get('capital', 100000):,.0f} |
                        > 股票仓位 {guide.get('position_pct', 95):.0f}% |
                        > 每只约 ¥{guide.get('per_stock', 0):,.0f} |
                        > 止损线 {guide.get('stop_loss_pct', -20):.0f}% |
                        > 下次调仓 {guide.get('next_rebalance_days', 20)}天后
                        """)

                    # 持仓表格
                    port_data = []
                    for p in portfolio[:30]:
                        row = {
                            "代码": p.get("code", ""),
                            "现价": f"¥{p.get('close', 0):.2f}",
                            "数量(股)": p.get("shares", 0),
                            "金额": f"¥{p.get('actual_amount', 0):,.0f}",
                        }
                        if "stop_price" in p:
                            row["止损价"] = f"¥{p['stop_price']:.2f}"
                        if "take_price" in p:
                            row["止盈价"] = f"¥{p['take_price']:.2f}"
                        if "score" in p:
                            row["评分"] = f"{p['score']:.4f}"
                        port_data.append(row)

                    st.dataframe(pd.DataFrame(port_data), width="stretch", hide_index=True)

                    st.caption("💡 以上为策略建议，不构成投资建议。实际操作请结合自身判断。")
            elif signal and "error" in signal:
                st.warning(f"⚠️ {signal['error']}")
            else:
                st.info("💡 点击「生成最新信号」按钮获取当前策略信号，或先点击侧边栏「一键更新数据」确保数据最新。")

        except Exception as e:
            st.error(f"实盘信号模块加载失败: {e}")
            st.caption("请确保数据已更新，或检查 src/strategy/live_signal.py 模块是否正常。")

    with tab3:
        st.subheader("📝 模拟交易")
        st.caption("用真实行情验证策略，不花真金白银。跟踪策略信号的模拟执行效果。")

        try:
            from src.strategy.paper_trading import PaperTrader

            trader = PaperTrader(capital=100000)
            summary = trader.get_portfolio_summary()

            # 总资产概览
            st.markdown("#### 💰 账户概览")
            ac1, ac2, ac3, ac4 = st.columns(4)
            ac1.metric("总资产", f"¥{summary['total_asset']:,.2f}",
                       delta=f"{summary['total_return_pct']:+.2f}%",
                       help="初始资金 + 累计盈亏")
            ac2.metric("持仓市值", f"¥{summary['market_value']:,.2f}",
                       help="当前所有股票的市值总和")
            ac3.metric("可用现金", f"¥{summary['cash']:,.2f}",
                       help="尚未投入股市的资金")
            ac4.metric("持仓数", f"{summary['positions_count']} 只",
                       help="当前持有的股票数量")

            st.caption(f"📅 模拟开始日期: {summary['start_date']} | 累计交易: {summary['total_trades']} 笔")

            # 当前持仓明细
            positions = summary.get("positions", [])
            if positions:
                st.markdown("#### 📋 当前持仓")
                pos_df = pd.DataFrame(positions)
                st.dataframe(pos_df, width="stretch", hide_index=True)
            else:
                st.info("📭 当前无持仓。点击下方按钮按最新信号建仓。")

            # 净值曲线
            nav_history = summary.get("nav_history", [])
            if nav_history and len(nav_history) > 1:
                st.markdown("#### 📈 净值走势")
                nav_df = pd.DataFrame(nav_history)
                fig_paper_nav = go.Figure(go.Scatter(
                    x=nav_df["date"], y=nav_df["nav"],
                    mode="lines+markers",
                    line=dict(color="#1f77b4", width=2),
                    name="模拟盘净值",
                ))
                fig_paper_nav.add_hline(y=1.0, line_dash="dash", line_color="gray")
                fig_paper_nav.update_layout(
                    height=300, yaxis_title="净值",
                    margin=dict(t=20, b=20), plot_bgcolor="#fafafa",
                )
                st.plotly_chart(fig_paper_nav, width="stretch")

            st.divider()

            # 操作按钮
            st.markdown("#### ⚙️ 操作")
            op_col1, op_col2, op_col3 = st.columns(3)

            with op_col1:
                if st.button("📡 按最新信号调仓", help="加载最新策略信号并执行模拟买卖"):
                    with st.spinner("执行调仓..."):
                        from src.strategy.live_signal import generate_live_signal
                        signal = generate_live_signal(capital=100000)
                        if "error" not in signal:
                            result = trader.execute_signal(signal)
                            st.success(f"✅ 调仓完成: 买入{result.get('bought', 0)}只, "
                                       f"卖出{result.get('sold', 0)}只, "
                                       f"跳过{result.get('skipped', 0)}只")
                            st.rerun()
                        else:
                            st.warning(f"信号异常: {signal['error']}")

            with op_col2:
                if st.button("🔄 更新持仓价格", help="用最新收盘价更新所有持仓的现价和盈亏"):
                    with st.spinner("更新价格..."):
                        trader.update_prices()
                        st.success("✅ 价格已更新")
                        st.rerun()

            with op_col3:
                if st.button("🗑️ 重置模拟盘", help="清空所有持仓和交易记录，重新开始"):
                    trader.reset()
                    st.success("✅ 模拟盘已重置")
                    st.rerun()

            # 交易历史
            if trader.trades:
                with st.expander(f"📜 交易历史 (共{len(trader.trades)}笔)", expanded=False):
                    trade_data = []
                    for t in reversed(trader.trades[-50:]):  # 最近50笔
                        trade_data.append({
                            "日期": t.trade_date,
                            "代码": t.code,
                            "方向": "🟢买入" if t.direction == "buy" else "🔴卖出",
                            "价格": f"¥{t.price:.2f}",
                            "数量": f"{t.shares}股",
                            "金额": f"¥{t.amount:,.0f}",
                            "原因": t.reason,
                        })
                    st.dataframe(pd.DataFrame(trade_data), width="stretch", hide_index=True)

        except Exception as e:
            st.error(f"模拟交易模块加载失败: {e}")
            st.caption("请确保 src/strategy/paper_trading.py 模块正常，且 data/paper_trading/ 目录可写。")


# ══════════════════════════════════════════════════════════════
# 页面4: 使用指南
# ══════════════════════════════════════════════════════════════

elif page == "📖 使用指南":
    st.title("📖 使用指南")
    st.caption("Sparrow 投资助手完整使用说明 — 帮你快速上手，每天5分钟做出投资决策。")

    # ── Quick Start: 每日工作流 ──
    st.markdown("""
    ## 🚀 Quick Start：每日工作流

    > 每天只需 **3步**，5分钟内完成投资决策。
    """)

    st.markdown("""
    <div style="background:#e8f5e9; padding:16px 20px; border-radius:10px; margin-bottom:12px; border-left:4px solid #4caf50;">
        <p style="margin:0 0 6px 0; font-size:1.1em;"><strong>Step 1️⃣ 查看全球联动：了解隔夜市场动态</strong></p>
        <p style="margin:0; color:#2e7d32; font-size:0.95em;">
        每天早上9点前打开「🌍 全球联动」页面，看昨晚美股/日韩/黄金涨跌情况。<br/>
        重点关注：有没有 ±2% 以上的异动？如果有，点击"AI分析"了解原因和对A股的影响。
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="background:#e3f2fd; padding:16px 20px; border-radius:10px; margin-bottom:12px; border-left:4px solid #2196f3;">
        <p style="margin:0 0 6px 0; font-size:1.1em;"><strong>Step 2️⃣ 查看市场温度：判断整体市场冷热</strong></p>
        <p style="margin:0; color:#1565c0; font-size:0.95em;">
        打开「🌡️ 市场温度」页面，看综合温度读数。<br/>
        温度低（<30°）= 市场便宜，适合买入；温度高（>70°）= 市场贵了，不要追高。
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="background:#fff3e0; padding:16px 20px; border-radius:10px; margin-bottom:12px; border-left:4px solid #ff9800;">
        <p style="margin:0 0 6px 0; font-size:1.1em;"><strong>Step 3️⃣ 查看我的持仓：获取持仓相关建议</strong></p>
        <p style="margin:0; color:#e65100; font-size:0.95em;">
        打开「💼 我的持仓」页面，查看你的ETF和基金的具体操作建议。<br/>
        系统会综合RSI、均线、市场温度给出"加仓/持有/减仓"建议。
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    ---

    **操作频率建议：**

    | 频率 | 建议 |
    |------|------|
    | 每天看一次 | ✅ 推荐。花5分钟走完上面3步 |
    | 每周看一次 | 也行，基金投资不需要天天盯盘 |
    | 每月看一次 | 可能错过极端低估的加仓机会 |
    """)

    st.divider()

    # ── 6页功能详解 ──
    st.markdown("## 📑 功能页面详解")
    st.markdown("Sparrow 共有 6 个页面，每个页面解决一个特定问题：")

    # 页面1: 市场温度
    with st.expander("🌡️ 市场温度 — 综合估值/趋势/成交量判断市场冷热", expanded=False):
        st.markdown("""
        **解决什么问题：** 现在市场整体是贵还是便宜？适不适合买入？

        **核心指标：**
        - **综合温度（0~100°）**：数字越低=市场越便宜。就像天气温度一样直观
        - **估值维度（权重50%）**：股价相对历史均线的位置，越低=越便宜
        - **趋势维度（权重30%）**：多少股票在涨？占比高=市场强势
        - **成交量维度（权重20%）**：交易是否活跃？太活跃可能过热

        **怎么用：**
        | 温度区间 | 含义 | 操作建议 |
        |---------|------|---------|
        | 🟢 0~30° | 历史低位，很便宜 | 大胆买入/加倍定投 |
        | 🟡 30~55° | 正常偏低 | 正常定投 |
        | 🟠 55~75° | 偏贵 | 减少买入 |
        | 🔴 75~100° | 历史高位，很贵 | 停止买入/考虑卖出 |

        **小白提示：** 温度27°和28°区别很小，不要纠结个位数变化，看大区间就好。
        """)

    # 页面2: 全球联动
    with st.expander("🌍 全球联动 — 核心全球指数实时数据 + AI异动分析", expanded=False):
        st.markdown("""
        **解决什么问题：** 昨晚全球发生了什么？对今天A股有什么影响？

        **核心功能：**
        - **全球资产涨跌全景图（Treemap）**：红=涨、绿=跌，一眼看出资金去了哪里
        - **覆盖11个核心指数**：标普500、纳斯达克、道琼斯、日经225、韩国KOSPI、沪深300、上证、创业板、恒指、恒科、黄金
        - **±2% 异动检测**：自动高亮当日大涨/大跌的资产
        - **AI分析按钮**：一键生成市场解读，包括异动原因+对A股影响+操作建议

        **联动规律（小白记住这几条）：**
        - 美股大跌 → A股次日大概率低开
        - 黄金暴涨 → 避险情绪升温 → A股可能承压
        - 日韩暴跌 → A股盘中跟跌概率高

        **最佳使用时间：** 每天早上9:00前（A股开盘前），看昨晚全球表现。
        """)

    # 页面3: 我的持仓
    with st.expander("💼 我的持仓 — 实际持仓追踪及操作建议", expanded=False):
        st.markdown("""
        **解决什么问题：** 我手上的ETF和基金，现在该买还是该卖？

        **核心功能：**
        - **场内ETF分析**：基于RSI、均线、成交量等技术指标，给出买入/持有/卖出建议
        - **场外基金建议**：基于市场温度，给出定投金额调整建议（加倍/正常/减半/暂停）
        - **K线走势图**：展示价格和均线位置，直观判断趋势

        **关键指标解释：**
        - **RSI**：超卖（<30，可能反弹）/ 超买（>70，可能回调）
        - **均线站上**：价格在几条均线上方（越多=越强势）
        - **量比**：今日成交量 ÷ 近期平均量（>1.5=活跃，<0.7=冷清）

        **操作建议逻辑：**
        - 场内ETF：RSI低 + 均线下方 + 市场温度低 → 加仓
        - 场外基金：市场温度<30° → 加倍定投；>70° → 暂停定投
        """)

    # 页面4: 行业分析
    with st.expander("🏭 行业分析 — 行业轮动排名及阶段判断", expanded=False):
        st.markdown("""
        **解决什么问题：** 现在哪些行业热门？哪些行业正在启动？

        **核心功能：**
        - **今日行业涨跌排名**：涨幅/跌幅前10，及各行业龙头股
        - **行业阶段判断**：自动识别行业处于初始期/主升浪/尾期/回调/低迷
        - **全行业涨跌分布图**：可视化所有行业当日表现

        **行业轮动简单策略：**
        1. 每周看一次排名
        2. 找到处于"初始期"的行业 → 最佳买点
        3. 涨到"尾期"时卖出 → 别恋战

        **注意：** 行业数据需要积累至少20天才能有效分析阶段，刚开始使用时请坚持每天更新数据。
        """)

    # 页面5: 策略交易
    with st.expander("📈 策略交易 — 回测/实盘信号/模拟交易三合一", expanded=False):
        st.markdown("""
        **解决什么问题：** 量化策略靠不靠谱？今天该买哪些股票？

        **三个子功能（Tab切换）：**

        **① 策略回测**
        - 展示V3策略（满仓选股+个股止损）的历史表现
        - 核心指标：年化收益、最大回撤、夏普比率
        - 用人话解释：年化36% = 投1万，一年后变1.36万

        **② 实盘信号**
        - 展示今天的交易信号：买哪些、卖哪些、为什么
        - 每天收盘后自动计算

        **③ 模拟交易**
        - 用虚拟资金跟踪策略表现
        - 查看模拟持仓和累计盈亏

        **小白提示：** 这个页面是进阶功能。建议先把前3个页面用熟，再来研究策略交易。
        """)

    # 页面6: 使用指南
    with st.expander("📖 使用指南 — 就是本页面", expanded=False):
        st.markdown("""
        **就是你正在看的这个页面。** 包含：
        - 每日工作流（Quick Start）
        - 所有6个页面的功能说明
        - 投资假设说明
        - 常见问题解答

        随时回来查阅不懂的概念。
        """)

    st.divider()

    # ── 投资假设说明 ──
    st.markdown("## 🎯 投资假设说明")
    st.markdown("Sparrow 的所有建议都基于以下用户画像假设。如果你的情况不同，请酌情调整：")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("""
        <div style="background:#f3e5f5; padding:16px; border-radius:10px; border-left:4px solid #9c27b0;">
            <p style="margin:0 0 8px 0; font-weight:600; color:#4a148c;">👤 目标用户</p>
            <p style="margin:0; color:#4a148c;">投资小白（刚入门1-2年）</p>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("")
        st.markdown("""
        <div style="background:#e8eaf6; padding:16px; border-radius:10px; border-left:4px solid #3f51b5;">
            <p style="margin:0 0 8px 0; font-weight:600; color:#1a237e;">💰 资金规模</p>
            <p style="margin:0; color:#1a237e;">~1万元（小资金量）</p>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown("""
        <div style="background:#e0f7fa; padding:16px; border-radius:10px; border-left:4px solid #00bcd4;">
            <p style="margin:0 0 8px 0; font-weight:600; color:#006064;">📦 持仓类型</p>
            <p style="margin:0; color:#006064;">中概互联ETF（513050）+ 2只场外基金</p>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("")
        st.markdown("""
        <div style="background:#fce4ec; padding:16px; border-radius:10px; border-left:4px solid #e91e63;">
            <p style="margin:0 0 8px 0; font-weight:600; color:#880e4f;">📉 最大回撤容忍</p>
            <p style="margin:0; color:#880e4f;">20%（亏2000元是心理极限）</p>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("")
    st.warning("⚠️ **重要：所有建议（加仓/减仓/止损）都基于以上假设。** 如果你的资金量更大、风险承受能力更强或持仓品种不同，建议需要相应调整。")

    st.divider()

    # ── 常见问题 ──
    st.markdown("## ❓ 常见问题")

    with st.expander("数据多久更新一次？"):
        st.markdown("""
        - **自动更新**：每次打开应用时，系统会自动检测数据是否过期（超过1个交易日），如果过期会自动静默更新
        - **手动更新**：点击左侧边栏的「🔄 一键更新数据」按钮立即刷新
        - **建议频率**：每天开盘前或收盘后更新一次即可
        """)

    with st.expander("市场温度低为什么还在跌？"):
        st.markdown("""
        温度低 = 相对历史来说便宜，但**不代表明天就会涨**。市场可能继续下跌（更便宜）。

        低温加仓的逻辑是：**长期来看，在便宜时买入的胜率和收益远高于贵的时候买入。** 这是一个概率游戏，不是精确预测。

        建议：分批买入，不要一次性all in。
        """)

    with st.expander("AI分析准确吗？"):
        st.markdown("""
        AI分析基于当日全球行情数据，提供的是**参考性解读**，不是精确预测。

        它的价值在于：帮你快速理解"发生了什么"和"可能的影响"，省去自己查新闻的时间。

        **请勿**仅凭AI建议做买卖决策，应结合市场温度、持仓分析等多个维度综合判断。
        """)

    with st.expander("数据来源可靠吗？"):
        st.markdown("""
        - **行情数据**：来自通达信（TCP协议）和腾讯财经接口，都是免费但稳定的数据源
        - **覆盖范围**：A股5000+只股票，1990年至今全部日K线
        - **全球指数**：来自腾讯财经实时行情接口
        - **存储**：PostgreSQL数据库 + Parquet本地缓存，保证数据安全
        """)

    # ── 启动方式 ──
    st.divider()
    st.markdown("## 🛠️ 技术信息")
    with st.expander("如何启动应用"):
        st.markdown("""
        ```bash
        cd ~/moubiao/sparrow
        source .venv/bin/activate
        streamlit run app.py
        ```
        浏览器会自动打开 http://localhost:8501
        """)
