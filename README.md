# Sparrow 🐦 — A股量化数据基座

个人量化策略的数据底座。离线数据完善，实时数据及时，定时采集补全。

## 数据覆盖

| 层级 | 数据 | 数据源 | 封IP | 频率 |
|------|------|--------|------|------|
| 行情 | 日K线/分钟K | mootdx (TCP) | 不封 | 每日 |
| 估值 | PE/PB/市值 | 腾讯财经 | 不封 | 每日 |
| 资金 | 主力/大单净流入 | 东财 push2his | 有风控 | 每日 |
| 资金 | 北向资金 | 同花顺 hsgt | 不封 | 每日 |
| 资金 | 融资融券 | 东财 datacenter | 有风控 | 每日 |
| 事件 | 龙虎榜+席位 | 东财 datacenter | 有风控 | 每日 |
| 事件 | 强势股归因 | 同花顺热点 | 不封 | 每日 |
| 事件 | 解禁日历 | 东财 datacenter | 有风控 | 每周 |
| 事件 | 大宗交易 | 东财 datacenter | 有风控 | 每日 |
| 板块 | 行业涨跌/领涨 | 东财 push2 | 有风控 | 每日 |
| 基础 | 股票列表/交易日历 | 东财+mootdx | — | 每日 |

全部免费数据源，无需 API Key。

## 快速开始

```bash
# 1. 创建虚拟环境
python3 -m venv .venv && source .venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 安装 PostgreSQL (macOS)
brew install postgresql@16
brew services start postgresql@16

# 4. 创建数据库
createdb sparrow

# 5. 配置
cp .env.example .env
# 编辑 .env: DATABASE_URL=postgresql://你的用户名@localhost:5432/sparrow

# 6. 初始化表结构
python3 main.py init

# 7. 测试数据源
python3 main.py smoke-test

# 8. 开始采集（按顺序执行）
python3 main.py stock-list      # 1分钟
python3 main.py calendar        # 2分钟
python3 main.py backfill        # 2-3小时(全历史)
python3 main.py valuation       # 3分钟
python3 main.py northbound      # 5秒
python3 main.py hot-stocks      # 5秒
python3 main.py sector          # 10秒
python3 main.py dragon-tiger    # 1分钟

# 9. 查看状态
python3 main.py status

# 10. 启动定时调度（后台运行）
python3 main.py scheduler
```

## 全部命令

| 命令 | 说明 | 耗时 |
|------|------|------|
| `init` | 初始化数据库(建表) | 1秒 |
| `stock-list` | 采集全市场股票列表 | ~1分钟 |
| `calendar` | 生成交易日历 | ~2分钟 |
| `backfill` | 回填全历史日K线 | ~2-3小时 |
| `daily` | 每日增量K线 | ~15分钟 |
| `valuation` | 每日估值(PE/PB/市值) | ~3分钟 |
| `fund-flow` | 全市场资金流(东财限流) | ~2小时 |
| `northbound` | 北向资金 | 5秒 |
| `dragon-tiger` | 龙虎榜+席位 | ~1分钟 |
| `hot-stocks` | 强势股归因 | 5秒 |
| `sector` | 行业板块行情 | 10秒 |
| `lockup` | 解禁日历 | 10秒 |
| `check` | 数据质量检查 | 2秒 |
| `status` | 查看数据库状态 | 1秒 |
| `scheduler` | 启动定时调度器 | 持续运行 |
| `smoke-test` | 数据源连通性测试 | 10秒 |

## 定时调度（scheduler）

工作日自动执行：

```
09:00  股票列表更新
15:05  北向资金
15:30  强势股归因
15:35  每日K线
15:40  每日估值
15:45  行业板块
18:00  龙虎榜
20:00  大宗交易
21:00  数据质量检查
```

## 项目结构

```
sparrow/
├── src/
│   ├── config.py                  # 全局配置
│   ├── logger.py                  # 日志
│   ├── datasource/                # 数据源适配层
│   │   ├── mootdx_source.py      # 通达信(K线/盘口)
│   │   ├── tencent_source.py     # 腾讯财经(估值/实时)
│   │   ├── eastmoney_source.py   # 东财(资金/龙虎榜,内置限流)
│   │   └── ths_source.py         # 同花顺(热点/北向)
│   ├── collector/                 # 采集任务
│   │   ├── stock_list_collector.py
│   │   ├── daily_kline_collector.py
│   │   ├── valuation_collector.py
│   │   ├── fund_flow_collector.py
│   │   ├── margin_collector.py
│   │   ├── northbound_collector.py
│   │   ├── dragon_tiger_collector.py
│   │   ├── hot_stocks_collector.py
│   │   ├── event_collector.py     # 解禁/分红/大宗/股东户数
│   │   ├── sector_collector.py
│   │   ├── calendar_collector.py
│   │   └── scheduler.py          # 定时调度
│   ├── storage/                   # 存储层
│   │   ├── database.py
│   │   └── models.py
│   └── quality/                   # 数据质量
│       └── checker.py
├── scripts/
│   ├── init_db.sql                # 建表DDL(分区表)
│   ├── setup_db.py                # 初始化
│   ├── backfill_daily.py          # K线回填
│   └── smoke_test.py              # 连通性测试
├── docs/
│   └── database_design.md         # 数据库设计文档
├── main.py                        # CLI 入口
├── requirements.txt
├── pyproject.toml
└── .env.example
```

## 东财防封说明

东财是资金面/事件层的唯一数据源，有风控机制。本项目所有东财请求统一走 `em_get()` 限流入口：
- 串行请求，间隔 ≥ 1秒
- 复用 HTTP 会话
- 每日请求上限 5000 次
- 批量采集时自动降速

## 实施路线

- [x] Phase 1: 项目骨架 + 日K线采集
- [x] Phase 2: 估值 + 资金面 + 北向
- [x] Phase 3: 龙虎榜 + 热点 + 解禁 + 大宗
- [x] Phase 4: 板块 + 交易日历 + 质量检查
- [ ] Phase 5: 财报三表 + 研报
- [ ] Phase 6: 盘中实时 + API 服务
