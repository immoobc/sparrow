# Sparrow 量化数据库设计方案

## 一、架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                    Sparrow 量化数据基座                           │
├─────────────────────────────────────────────────────────────────┤
│  Layer 0: 元数据层     股票基础信息 / 交易日历 / 数据采集状态      │
│  Layer 1: 行情层       日K/分钟K/tick / 实时快照 / 指数 / ETF     │
│  Layer 2: 财务层       三大报表 / 财务指标 / 业绩预告              │
│  Layer 3: 估值层       PE/PB/PS / 一致预期 / 研报评级             │
│  Layer 4: 资金层       资金流向 / 融资融券 / 大宗交易 / 北向资金   │
│  Layer 5: 事件层       龙虎榜 / 解禁 / 分红 / 公告 / 新闻         │
│  Layer 6: 板块层       行业分类 / 概念板块 / 板块行情              │
│  Layer 7: 调度层       采集任务 / 运行日志 / 数据质量监控          │
└─────────────────────────────────────────────────────────────────┘
```

## 二、技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 主数据库 | PostgreSQL 16+ | 分区表、JSONB、窗口函数、成熟稳定 |
| 时序扩展 | TimescaleDB (可选) | 分钟级行情压缩存储，自动分区 |
| 缓存 | Redis | 实时行情快照、采集锁、限流计数 |
| 调度 | APScheduler / Celery | 定时采集、补数据任务 |

## 三、数据库选型：PostgreSQL

选 PostgreSQL 而非 ClickHouse/InfluxDB 的理由：
- 你是个人量化，数据量级在 亿行以内（A股~5000只 × 30年日K = ~3600万行）
- 需要复杂 JOIN（财务+行情+资金面交叉分析）
- 分区表 + 索引足够覆盖性能需求
- 部署简单，单机即可
- 后续如需升级，TimescaleDB 无缝扩展

---

## 四、表结构详细设计

### Layer 0: 元数据层

#### 0.1 stock_basic — 股票基础信息（全市场）

```sql
CREATE TABLE stock_basic (
    code         CHAR(6) PRIMARY KEY,          -- 股票代码 6位
    name         VARCHAR(20) NOT NULL,         -- 股票简称
    market       VARCHAR(4) NOT NULL,          -- sh/sz/bj
    board        VARCHAR(10),                  -- 主板/中小板/创业板/科创板/北交所
    industry_l1  VARCHAR(30),                  -- 一级行业(东财)
    industry_l2  VARCHAR(30),                  -- 二级行业
    list_date    DATE,                         -- 上市日期
    delist_date  DATE,                         -- 退市日期(NULL=在市)
    total_shares BIGINT,                       -- 总股本(股)
    float_shares BIGINT,                       -- 流通股本(股)
    is_st        BOOLEAN DEFAULT FALSE,        -- 是否ST
    is_active    BOOLEAN DEFAULT TRUE,         -- 是否活跃(未退市)
    updated_at   TIMESTAMP DEFAULT NOW()
);

-- 数据源: 东财 push2 stock/get + mootdx finance
-- 更新频率: 每日盘后 1 次
```

#### 0.2 trade_calendar — 交易日历

```sql
CREATE TABLE trade_calendar (
    cal_date     DATE PRIMARY KEY,             -- 日期
    is_open      BOOLEAN NOT NULL,             -- 是否交易日
    prev_trade   DATE,                         -- 上一个交易日
    next_trade   DATE                          -- 下一个交易日
);

-- 数据源: 交易所公告 + mootdx K线反推
-- 覆盖: 1990-12-19 至今
-- 更新: 每年初补全新一年日历
```

#### 0.3 index_basic — 指数基础信息

```sql
CREATE TABLE index_basic (
    code         VARCHAR(10) PRIMARY KEY,      -- 指数代码(如 000001.SH)
    name         VARCHAR(40) NOT NULL,         -- 指数名称
    market       VARCHAR(4),                   -- sh/sz
    category     VARCHAR(20),                  -- 规模/行业/主题/策略
    base_date    DATE,                         -- 基期
    base_point   DECIMAL(10,2),                -- 基点
    components   INT,                          -- 成分股数量
    updated_at   TIMESTAMP DEFAULT NOW()
);
```

#### 0.4 etf_basic — ETF 基础信息

```sql
CREATE TABLE etf_basic (
    code         CHAR(6) PRIMARY KEY,
    name         VARCHAR(40) NOT NULL,
    market       VARCHAR(4),
    track_index  VARCHAR(10),                  -- 跟踪指数代码
    fund_type    VARCHAR(20),                  -- 股票型/债券型/商品型/货币型
    list_date    DATE,
    manager      VARCHAR(40),                  -- 基金管理人
    updated_at   TIMESTAMP DEFAULT NOW()
);
```

---

### Layer 1: 行情层（核心，数据量最大）

#### 1.1 stock_daily — 日线行情（分区表，按年分区）

```sql
CREATE TABLE stock_daily (
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    open         DECIMAL(10,3),
    high         DECIMAL(10,3),
    low          DECIMAL(10,3),
    close        DECIMAL(10,3),
    volume       BIGINT,                       -- 成交量(股)
    amount       DECIMAL(18,2),                -- 成交额(元)
    turnover     DECIMAL(8,4),                 -- 换手率%
    amplitude    DECIMAL(8,4),                 -- 振幅%
    change_pct   DECIMAL(8,4),                 -- 涨跌幅%
    change_amt   DECIMAL(10,3),                -- 涨跌额
    adj_factor   DECIMAL(12,6) DEFAULT 1.0,    -- 复权因子
    PRIMARY KEY (code, trade_date)
) PARTITION BY RANGE (trade_date);

-- 按年创建分区
CREATE TABLE stock_daily_1990 PARTITION OF stock_daily
    FOR VALUES FROM ('1990-01-01') TO ('1991-01-01');
-- ... 逐年创建到 2030
CREATE TABLE stock_daily_2024 PARTITION OF stock_daily
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE stock_daily_2025 PARTITION OF stock_daily
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE stock_daily_2026 PARTITION OF stock_daily
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

CREATE INDEX idx_daily_code ON stock_daily (code, trade_date DESC);
CREATE INDEX idx_daily_date ON stock_daily (trade_date, code);

-- 数据源: mootdx bars(category=4) — TCP不封IP
-- 覆盖: 1990年至今 全部A股
-- 更新: 每日15:30盘后采集
-- 预计数据量: ~4000万行(5000只 × 8000交易日)
```

#### 1.2 stock_minute — 分钟线行情（分区表，按月分区）

```sql
CREATE TABLE stock_minute (
    code         CHAR(6) NOT NULL,
    datetime     TIMESTAMP NOT NULL,           -- 精确到分钟
    period       SMALLINT NOT NULL DEFAULT 1,  -- 1/5/15/30/60 分钟
    open         DECIMAL(10,3),
    high         DECIMAL(10,3),
    low          DECIMAL(10,3),
    close        DECIMAL(10,3),
    volume       BIGINT,
    amount       DECIMAL(18,2),
    PRIMARY KEY (code, datetime, period)
) PARTITION BY RANGE (datetime);

-- 按月分区 (数据量大，建议只保留近2年分钟线)
CREATE TABLE stock_minute_202601 PARTITION OF stock_minute
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');

CREATE INDEX idx_minute_code_time ON stock_minute (code, datetime DESC);

-- 数据源: mootdx bars(category=7/8/9/10/11)
-- 覆盖: 近2年(更早的分钟数据通达信不提供)
-- 更新: 盘中实时 or 盘后批量
-- 策略建议: 只存自己关注的票池的分钟线，不全量采集
```

#### 1.3 stock_tick — 逐笔成交（可选，数据量极大）

```sql
CREATE TABLE stock_tick (
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    time         TIME NOT NULL,
    price        DECIMAL(10,3),
    volume       INT,                          -- 成交量(手)
    num          INT,                          -- 成交笔数
    direction    SMALLINT,                     -- 0买/1卖/2中性
    PRIMARY KEY (code, trade_date, time)
) PARTITION BY RANGE (trade_date);

-- 数据源: mootdx transaction
-- 注意: 数据量巨大(单只票/天约4000条)，建议按需采集
-- 更新: 盘后对关注票池采集
```

#### 1.4 stock_realtime — 实时快照（Redis + 落库）

```sql
CREATE TABLE stock_realtime_snapshot (
    code         CHAR(6) NOT NULL,
    snap_time    TIMESTAMP NOT NULL,
    price        DECIMAL(10,3),
    open         DECIMAL(10,3),
    high         DECIMAL(10,3),
    low          DECIMAL(10,3),
    last_close   DECIMAL(10,3),
    volume       BIGINT,
    amount       DECIMAL(18,2),
    pe_ttm       DECIMAL(10,3),
    pb           DECIMAL(10,4),
    mcap         DECIMAL(18,2),                -- 总市值(元)
    float_mcap   DECIMAL(18,2),                -- 流通市值(元)
    turnover     DECIMAL(8,4),
    limit_up     DECIMAL(10,3),                -- 涨停价
    limit_down   DECIMAL(10,3),                -- 跌停价
    vol_ratio    DECIMAL(8,4),                 -- 量比
    bid1_price   DECIMAL(10,3),
    bid1_vol     INT,
    ask1_price   DECIMAL(10,3),
    ask1_vol     INT,
    PRIMARY KEY (code, snap_time)
) PARTITION BY RANGE (snap_time);

-- 数据源: 腾讯财经(PE/PB/市值) + mootdx(五档盘口)
-- 盘中: Redis缓存最新快照, 定时(如每5分钟)落库
-- 盘后: 存当日收盘快照
-- 用途: 盘中监控 + 历史估值序列
```

#### 1.5 index_daily — 指数日线

```sql
CREATE TABLE index_daily (
    code         VARCHAR(10) NOT NULL,         -- 如 000001.SH
    trade_date   DATE NOT NULL,
    open         DECIMAL(10,3),
    high         DECIMAL(10,3),
    low          DECIMAL(10,3),
    close        DECIMAL(10,3),
    volume       BIGINT,
    amount       DECIMAL(18,2),
    change_pct   DECIMAL(8,4),
    PRIMARY KEY (code, trade_date)
) PARTITION BY RANGE (trade_date);

-- 数据源: 腾讯财经 tencent_quote (指数)
-- 覆盖: 上证指数1990至今, 其余指数从成立日开始
```

#### 1.6 etf_daily — ETF 日线

```sql
CREATE TABLE etf_daily (
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    open         DECIMAL(10,4),
    high         DECIMAL(10,4),
    low          DECIMAL(10,4),
    close        DECIMAL(10,4),
    volume       BIGINT,
    amount       DECIMAL(18,2),
    turnover     DECIMAL(8,4),
    change_pct   DECIMAL(8,4),
    PRIMARY KEY (code, trade_date)
);

-- 数据源: 腾讯财经 tencent_quote (ETF)
```

---

### Layer 2: 财务层

#### 2.1 financial_indicator — 核心财务指标（季度）

```sql
CREATE TABLE financial_indicator (
    code           CHAR(6) NOT NULL,
    report_date    DATE NOT NULL,               -- 报告期 (如 2025-12-31)
    report_type    VARCHAR(10),                 -- 年报/半年报/一季报/三季报
    -- 盈利能力
    eps            DECIMAL(10,4),               -- 每股收益
    bvps           DECIMAL(10,4),               -- 每股净资产
    roe            DECIMAL(8,4),                -- 净资产收益率%
    roa            DECIMAL(8,4),                -- 总资产收益率%
    gross_margin   DECIMAL(8,4),               -- 毛利率%
    net_margin     DECIMAL(8,4),               -- 净利率%
    -- 规模
    revenue        DECIMAL(18,2),               -- 营业收入(元)
    net_profit     DECIMAL(18,2),               -- 归母净利润(元)
    deducted_profit DECIMAL(18,2),             -- 扣非净利润(元)
    -- 成长性
    revenue_yoy    DECIMAL(8,4),               -- 营收同比%
    profit_yoy     DECIMAL(8,4),               -- 净利同比%
    -- 运营效率
    inventory_days DECIMAL(8,2),               -- 存货周转天数
    ar_days        DECIMAL(8,2),               -- 应收周转天数
    -- 杠杆
    debt_ratio     DECIMAL(8,4),               -- 资产负债率%
    current_ratio  DECIMAL(8,4),               -- 流动比率
    -- 现金流
    ocf_per_share  DECIMAL(10,4),              -- 每股经营现金流
    free_cf        DECIMAL(18,2),              -- 自由现金流(元)
    -- 分红
    dividend_ps    DECIMAL(10,4),              -- 每股股利
    payout_ratio   DECIMAL(8,4),              -- 派息率%
    PRIMARY KEY (code, report_date)
);

CREATE INDEX idx_fin_ind_code ON financial_indicator (code, report_date DESC);

-- 数据源: mootdx finance (37字段快照) + 新浪三表计算
-- 覆盖: 2000年至今(更早数据不完整)
-- 更新: 季报披露后采集 (4/8/10/次年4月)
```

#### 2.2 balance_sheet — 资产负债表

```sql
CREATE TABLE balance_sheet (
    code           CHAR(6) NOT NULL,
    report_date    DATE NOT NULL,
    -- 资产
    total_assets        DECIMAL(18,2),
    current_assets      DECIMAL(18,2),
    cash_equivalents    DECIMAL(18,2),         -- 货币资金
    accounts_recv       DECIMAL(18,2),         -- 应收账款
    inventory           DECIMAL(18,2),         -- 存货
    fixed_assets        DECIMAL(18,2),         -- 固定资产
    intangible_assets   DECIMAL(18,2),         -- 无形资产
    goodwill            DECIMAL(18,2),         -- 商誉
    -- 负债
    total_liabilities   DECIMAL(18,2),
    current_liab        DECIMAL(18,2),
    short_loan          DECIMAL(18,2),         -- 短期借款
    long_loan           DECIMAL(18,2),         -- 长期借款
    bonds_payable       DECIMAL(18,2),         -- 应付债券
    -- 权益
    total_equity        DECIMAL(18,2),
    minority_interest   DECIMAL(18,2),         -- 少数股东权益
    retained_earnings   DECIMAL(18,2),         -- 未分配利润
    raw_data           JSONB,                  -- 原始完整数据
    PRIMARY KEY (code, report_date)
);

-- 数据源: 新浪财报三表 sina_financial_report("fzb")
```

#### 2.3 income_statement — 利润表

```sql
CREATE TABLE income_statement (
    code           CHAR(6) NOT NULL,
    report_date    DATE NOT NULL,
    revenue             DECIMAL(18,2),         -- 营业收入
    cost_of_revenue     DECIMAL(18,2),         -- 营业成本
    gross_profit        DECIMAL(18,2),         -- 毛利润
    selling_expense     DECIMAL(18,2),         -- 销售费用
    admin_expense       DECIMAL(18,2),         -- 管理费用
    rd_expense          DECIMAL(18,2),         -- 研发费用
    finance_expense     DECIMAL(18,2),         -- 财务费用
    operating_profit    DECIMAL(18,2),         -- 营业利润
    non_operating       DECIMAL(18,2),         -- 营业外收支净额
    profit_before_tax   DECIMAL(18,2),         -- 利润总额
    income_tax          DECIMAL(18,2),         -- 所得税
    net_profit          DECIMAL(18,2),         -- 净利润
    net_profit_parent   DECIMAL(18,2),         -- 归母净利润
    deducted_profit     DECIMAL(18,2),         -- 扣非净利润
    raw_data           JSONB,
    PRIMARY KEY (code, report_date)
);

-- 数据源: 新浪财报三表 sina_financial_report("lrb")
```

#### 2.4 cash_flow — 现金流量表

```sql
CREATE TABLE cash_flow (
    code           CHAR(6) NOT NULL,
    report_date    DATE NOT NULL,
    -- 经营活动
    ocf_total           DECIMAL(18,2),         -- 经营活动现金流入
    ocf_outflow         DECIMAL(18,2),         -- 经营活动现金流出
    ocf_net             DECIMAL(18,2),         -- 经营活动现金流净额
    -- 投资活动
    icf_total           DECIMAL(18,2),         -- 投资活动现金流入
    icf_outflow         DECIMAL(18,2),         -- 投资活动现金流出
    icf_net             DECIMAL(18,2),         -- 投资活动现金流净额
    capex               DECIMAL(18,2),         -- 购建固定资产支出
    -- 筹资活动
    fcf_total           DECIMAL(18,2),         -- 筹资活动现金流入
    fcf_outflow         DECIMAL(18,2),         -- 筹资活动现金流出
    fcf_net             DECIMAL(18,2),         -- 筹资活动现金流净额
    -- 汇总
    cash_change         DECIMAL(18,2),         -- 现金净增加额
    cash_end            DECIMAL(18,2),         -- 期末现金余额
    raw_data           JSONB,
    PRIMARY KEY (code, report_date)
);

-- 数据源: 新浪财报三表 sina_financial_report("llb")
```

#### 2.5 earnings_forecast — 业绩预告/快报

```sql
CREATE TABLE earnings_forecast (
    id             SERIAL PRIMARY KEY,
    code           CHAR(6) NOT NULL,
    report_date    DATE NOT NULL,              -- 预告对应报告期
    announce_date  DATE,                       -- 公告日期
    forecast_type  VARCHAR(10),                -- 预增/预减/扭亏/首亏/续亏/略增/略减
    profit_min     DECIMAL(18,2),              -- 预计净利润下限
    profit_max     DECIMAL(18,2),              -- 预计净利润上限
    change_min     DECIMAL(8,4),               -- 变动幅度下限%
    change_max     DECIMAL(8,4),               -- 变动幅度上限%
    summary        TEXT,                       -- 业绩变动原因摘要
    created_at     TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_ef_code ON earnings_forecast (code, report_date DESC);
```

---

### Layer 3: 估值层

#### 3.1 valuation_daily — 每日估值指标

```sql
CREATE TABLE valuation_daily (
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    pe_ttm       DECIMAL(12,4),                -- 市盈率TTM
    pe_static    DECIMAL(12,4),                -- 静态PE
    pb           DECIMAL(10,4),                -- 市净率
    ps_ttm       DECIMAL(10,4),                -- 市销率TTM
    pcf_ttm      DECIMAL(10,4),                -- 市现率TTM
    mcap         DECIMAL(18,2),                -- 总市值(元)
    float_mcap   DECIMAL(18,2),                -- 流通市值(元)
    dv_ratio     DECIMAL(8,4),                 -- 股息率%
    PRIMARY KEY (code, trade_date)
) PARTITION BY RANGE (trade_date);

CREATE INDEX idx_val_code ON valuation_daily (code, trade_date DESC);

-- 数据源: 腾讯财经(PE/PB/市值) 每日收盘后采集
-- 核心用途: PE/PB百分位分析、估值区间判断
-- 覆盖: 2005年至今(腾讯接口历史有限，早期靠计算填充)
```

#### 3.2 analyst_consensus — 一致预期（机构盈利预测）

```sql
CREATE TABLE analyst_consensus (
    code           CHAR(6) NOT NULL,
    forecast_year  SMALLINT NOT NULL,          -- 预测年度
    snap_date      DATE NOT NULL,              -- 快照日期(何时采集的)
    eps_avg        DECIMAL(10,4),              -- 一致预期EPS(均值)
    eps_min        DECIMAL(10,4),
    eps_max        DECIMAL(10,4),
    analyst_count  SMALLINT,                   -- 覆盖机构数
    revenue_avg    DECIMAL(18,2),              -- 一致预期营收
    profit_avg     DECIMAL(18,2),              -- 一致预期净利润
    target_price   DECIMAL(10,3),              -- 目标价(均值)
    PRIMARY KEY (code, forecast_year, snap_date)
);

CREATE INDEX idx_consensus_code ON analyst_consensus (code, snap_date DESC);

-- 数据源: 同花顺 ths_eps_forecast + 东财 reportapi (predictThisYearEps)
-- 更新: 每周一次(机构预测不会频繁变)
-- 用途: 前向PE计算、PEG计算
```

#### 3.3 research_report — 研报库

```sql
CREATE TABLE research_report (
    id             SERIAL PRIMARY KEY,
    info_code      VARCHAR(30) UNIQUE,         -- 东财研报唯一码(拼PDF用)
    code           CHAR(6),                    -- 相关股票(NULL=行业/宏观研报)
    title          VARCHAR(200) NOT NULL,
    publish_date   DATE,
    org_name       VARCHAR(50),                -- 机构简称
    author         VARCHAR(100),               -- 分析师
    rating         VARCHAR(10),                -- 买入/增持/中性/减持
    eps_this_year  DECIMAL(10,4),
    eps_next_year  DECIMAL(10,4),
    eps_next2_year DECIMAL(10,4),
    industry       VARCHAR(30),                -- 行业分类
    report_type    VARCHAR(20),                -- 个股/行业/宏观/策略
    industry_code  VARCHAR(10),                -- 东财行业码(行业研报用)
    pdf_path       VARCHAR(200),               -- 本地PDF存储路径
    abstract       TEXT,                       -- 研报摘要
    created_at     TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_report_code ON research_report (code, publish_date DESC);
CREATE INDEX idx_report_date ON research_report (publish_date DESC);
CREATE INDEX idx_report_industry ON research_report (industry_code, publish_date DESC);

-- 数据源: 东财 reportapi (个股+行业研报)
-- 更新: 每日盘后增量采集
```

---

### Layer 4: 资金层

#### 4.1 fund_flow_daily — 个股资金流向（日级）

```sql
CREATE TABLE fund_flow_daily (
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    main_net     DECIMAL(18,2),                -- 主力净流入(元)
    super_net    DECIMAL(18,2),                -- 超大单净流入
    large_net    DECIMAL(18,2),                -- 大单净流入
    mid_net      DECIMAL(18,2),                -- 中单净流入
    small_net    DECIMAL(18,2),                -- 小单净流入
    PRIMARY KEY (code, trade_date)
) PARTITION BY RANGE (trade_date);

CREATE INDEX idx_flow_code ON fund_flow_daily (code, trade_date DESC);

-- 数据源: 东财 push2his stock_fund_flow_120d
-- 覆盖: 近120交易日(滚动)，每日增量追加
-- 更新: 每日盘后
```

#### 4.2 fund_flow_minute — 个股资金流向（分钟级）

```sql
CREATE TABLE fund_flow_minute (
    code         CHAR(6) NOT NULL,
    datetime     TIMESTAMP NOT NULL,
    main_net     DECIMAL(18,2),
    super_net    DECIMAL(18,2),
    large_net    DECIMAL(18,2),
    mid_net      DECIMAL(18,2),
    small_net    DECIMAL(18,2),
    PRIMARY KEY (code, datetime)
) PARTITION BY RANGE (datetime);

-- 数据源: 东财 push2 eastmoney_fund_flow_minute
-- 盘中采集(关注票池), 盘后可丢弃或归档
-- 数据量大，建议只存关注池
```

#### 4.3 margin_trading — 融资融券

```sql
CREATE TABLE margin_trading (
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    rzye         DECIMAL(18,2),                -- 融资余额(元)
    rzmre        DECIMAL(18,2),                -- 融资买入额
    rzche        DECIMAL(18,2),                -- 融资偿还额
    rqye         DECIMAL(18,2),                -- 融券余额(元)
    rqmcl        BIGINT,                       -- 融券卖出量
    rqchl        BIGINT,                       -- 融券偿还量
    rzrqye       DECIMAL(18,2),                -- 融资融券余额合计
    PRIMARY KEY (code, trade_date)
);

CREATE INDEX idx_margin_code ON margin_trading (code, trade_date DESC);

-- 数据源: 东财 datacenter RPTA_WEB_RZRQ_GGMX
-- 覆盖: 2010年至今(两融业务开通后)
-- 更新: 每日盘后(T+1公布)
```

#### 4.4 block_trade — 大宗交易

```sql
CREATE TABLE block_trade (
    id           SERIAL PRIMARY KEY,
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    deal_price   DECIMAL(10,3),                -- 成交价
    close_price  DECIMAL(10,3),                -- 当日收盘价
    premium_pct  DECIMAL(8,4),                 -- 溢价率%
    deal_volume  BIGINT,                       -- 成交量(股)
    deal_amount  DECIMAL(18,2),                -- 成交额(元)
    buyer        VARCHAR(100),                 -- 买方营业部
    seller       VARCHAR(100),                 -- 卖方营业部
    created_at   TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_block_code ON block_trade (code, trade_date DESC);
CREATE INDEX idx_block_date ON block_trade (trade_date DESC);

-- 数据源: 东财 datacenter RPT_DATA_BLOCKTRADE
-- 覆盖: 2014年至今
-- 更新: 每日盘后
```

#### 4.5 northbound_flow — 北向资金

```sql
CREATE TABLE northbound_flow (
    trade_date   DATE PRIMARY KEY,
    hgt_net      DECIMAL(12,4),                -- 沪股通净买入(亿元)
    sgt_net      DECIMAL(12,4),                -- 深股通净买入(亿元)
    total_net    DECIMAL(12,4),                -- 合计净买入(亿元)
    hgt_buy      DECIMAL(12,4),                -- 沪股通买入
    hgt_sell     DECIMAL(12,4),                -- 沪股通卖出
    sgt_buy      DECIMAL(12,4),
    sgt_sell     DECIMAL(12,4)
);

-- 数据源: 同花顺 hsgtApi (实时) + 自缓存CSV历史
-- 注意: 东财北向数据2024-08后断供, 用同花顺实时+本地缓存
-- 覆盖: 2014年(沪港通开通)至今
-- 更新: 每日收盘后存当日数据
```

#### 4.6 northbound_minute — 北向资金分钟流向

```sql
CREATE TABLE northbound_minute (
    trade_date   DATE NOT NULL,
    time         TIME NOT NULL,
    hgt_cumul    DECIMAL(12,4),                -- 沪股通累计净买入(亿)
    sgt_cumul    DECIMAL(12,4),                -- 深股通累计净买入(亿)
    PRIMARY KEY (trade_date, time)
);

-- 数据源: 同花顺 hsgt_realtime (262个时间点/天)
-- 用途: 盘中北向资金异动监控
-- 保留: 近30天(更早可归档/删除)
```

---

### Layer 5: 事件层

#### 5.1 dragon_tiger_record — 龙虎榜上榜记录

```sql
CREATE TABLE dragon_tiger_record (
    id           SERIAL PRIMARY KEY,
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    reason       VARCHAR(200),                 -- 上榜原因
    net_buy_amt  DECIMAL(18,2),                -- 龙虎榜净买额(元)
    buy_amt      DECIMAL(18,2),                -- 买入总额
    sell_amt     DECIMAL(18,2),                -- 卖出总额
    turnover_pct DECIMAL(8,4),                 -- 换手率%
    close_price  DECIMAL(10,3),
    change_pct   DECIMAL(8,4),
    UNIQUE (code, trade_date, reason)
);

CREATE INDEX idx_dtb_code ON dragon_tiger_record (code, trade_date DESC);
CREATE INDEX idx_dtb_date ON dragon_tiger_record (trade_date DESC);
```

#### 5.2 dragon_tiger_seat — 龙虎榜席位明细

```sql
CREATE TABLE dragon_tiger_seat (
    id           SERIAL PRIMARY KEY,
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    direction    VARCHAR(4) NOT NULL,           -- BUY/SELL
    rank         SMALLINT,                     -- 席位排名1-5
    dept_name    VARCHAR(100),                 -- 营业部名称
    dept_code    VARCHAR(20),                  -- 营业部代码(0=机构)
    buy_amt      DECIMAL(18,2),
    sell_amt     DECIMAL(18,2),
    net_amt      DECIMAL(18,2)
);

CREATE INDEX idx_seat_code ON dragon_tiger_seat (code, trade_date DESC);
CREATE INDEX idx_seat_dept ON dragon_tiger_seat (dept_name, trade_date DESC);

-- 数据源: 东财 datacenter RPT_BILLBOARD_DAILYDETAILSBUY/SELL
-- 用途: 机构动向追踪、知名游资(赵老哥等)跟踪
```

#### 5.3 lockup_schedule — 限售解禁日历

```sql
CREATE TABLE lockup_schedule (
    id           SERIAL PRIMARY KEY,
    code         CHAR(6) NOT NULL,
    free_date    DATE NOT NULL,                -- 解禁日期
    stock_type   VARCHAR(50),                  -- 限售股类型
    free_shares  BIGINT,                       -- 解禁股数
    free_ratio   DECIMAL(8,4),                 -- 占总股本比例%
    free_mcap    DECIMAL(18,2),                -- 解禁市值(元,按解禁日价格)
    UNIQUE (code, free_date, stock_type)
);

CREATE INDEX idx_lockup_code ON lockup_schedule (code, free_date);
CREATE INDEX idx_lockup_date ON lockup_schedule (free_date);

-- 数据源: 东财 datacenter RPT_LIFT_STAGE
-- 覆盖: 全历史 + 未来预告
-- 更新: 每周一次
```

#### 5.4 dividend_history — 分红送转

```sql
CREATE TABLE dividend_history (
    id              SERIAL PRIMARY KEY,
    code            CHAR(6) NOT NULL,
    ex_date         DATE,                      -- 除权除息日
    record_date     DATE,                      -- 股权登记日
    report_year     VARCHAR(10),               -- 分红年度
    bonus_rmb       DECIMAL(10,6),             -- 每股派息(税前,元)
    transfer_ratio  DECIMAL(8,4),              -- 每10股转增
    bonus_ratio     DECIMAL(8,4),              -- 每10股送股
    progress        VARCHAR(20),               -- 进度(实施/预案/...)
    UNIQUE (code, ex_date, report_year)
);

CREATE INDEX idx_div_code ON dividend_history (code, ex_date DESC);

-- 数据源: 东财 datacenter RPT_SHAREBONUS_DET
-- 用途: 股息率计算、除权价修正
```

#### 5.5 holder_num — 股东户数变化

```sql
CREATE TABLE holder_num (
    code         CHAR(6) NOT NULL,
    end_date     DATE NOT NULL,                -- 统计截止日
    holder_num   INT,                          -- 股东户数
    change_num   INT,                          -- 较上期变化数
    change_ratio DECIMAL(8,4),                 -- 环比变化%
    avg_shares   DECIMAL(12,2),                -- 户均持股
    PRIMARY KEY (code, end_date)
);

-- 数据源: 东财 datacenter RPT_HOLDERNUMLATEST
-- 用途: 筹码集中度分析(户数持续减少=主力吸筹)
-- 更新: 季度(跟随财报披露)
```

#### 5.6 stock_news — 个股新闻

```sql
CREATE TABLE stock_news (
    id           SERIAL PRIMARY KEY,
    code         CHAR(6),                      -- 相关股票(NULL=大盘新闻)
    title        VARCHAR(200) NOT NULL,
    content      TEXT,
    source       VARCHAR(50),                  -- 来源(东财/财联社/...)
    publish_time TIMESTAMP,
    url          VARCHAR(500),
    news_type    VARCHAR(20),                  -- stock/global/industry
    sentiment    SMALLINT,                     -- 情感: -1负面/0中性/1正面(后续NLP填充)
    created_at   TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_news_code ON stock_news (code, publish_time DESC);
CREATE INDEX idx_news_time ON stock_news (publish_time DESC);

-- 数据源: 东财个股新闻 + 东财全球资讯
-- 更新: 每小时增量
-- 用途: 舆情监控、事件驱动策略
```

#### 5.7 announcement — 公告

```sql
CREATE TABLE announcement (
    id              SERIAL PRIMARY KEY,
    code            CHAR(6) NOT NULL,
    title           VARCHAR(300) NOT NULL,
    announce_date   DATE,
    announce_type   VARCHAR(50),               -- 公告类型
    url             VARCHAR(500),
    content_summary TEXT,                      -- 摘要(可选,mootdx F10)
    pdf_path        VARCHAR(200),              -- 本地PDF路径(可选)
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_ann_code ON announcement (code, announce_date DESC);
CREATE INDEX idx_ann_date ON announcement (announce_date DESC);

-- 数据源: 巨潮 cninfo + mootdx F10 最新提示
-- 覆盖: 2000年至今
-- 更新: 每日盘后增量
```

#### 5.8 hot_stocks — 每日强势股/题材归因

```sql
CREATE TABLE hot_stocks (
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    name         VARCHAR(20),
    reason_tags  VARCHAR(200),                 -- 题材标签(如"算力租赁+AI政务")
    change_pct   DECIMAL(8,4),                 -- 涨幅%
    turnover_pct DECIMAL(8,4),                 -- 换手率%
    amount       DECIMAL(18,2),                -- 成交额
    dde_net      DECIMAL(18,2),                -- 大单净量
    PRIMARY KEY (code, trade_date)
);

CREATE INDEX idx_hot_date ON hot_stocks (trade_date DESC);

-- 数据源: 同花顺热点 ths_hot_reason
-- 用途: 题材跟踪、热点轮动分析
-- 更新: 每日盘后15:30采集
```

---

### Layer 6: 板块层

#### 6.1 sector_info — 板块基础信息

```sql
CREATE TABLE sector_info (
    sector_code  VARCHAR(10) PRIMARY KEY,      -- 板块代码(BK开头)
    sector_name  VARCHAR(40) NOT NULL,
    sector_type  VARCHAR(10),                  -- industry/concept/region
    component_count INT,                       -- 成分股数量
    updated_at   TIMESTAMP DEFAULT NOW()
);

-- 数据源: 东财 clist (m:90+t:1/2/3)
```

#### 6.2 sector_component — 板块成分股映射

```sql
CREATE TABLE sector_component (
    sector_code  VARCHAR(10) NOT NULL,
    code         CHAR(6) NOT NULL,
    is_leader    BOOLEAN DEFAULT FALSE,        -- 是否龙头
    updated_at   TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (sector_code, code)
);

CREATE INDEX idx_sc_code ON sector_component (code);

-- 数据源: 东财 slist eastmoney_concept_blocks
-- 更新: 每周一次(板块调整不频繁)
```

#### 6.3 sector_daily — 板块日线行情

```sql
CREATE TABLE sector_daily (
    sector_code  VARCHAR(10) NOT NULL,
    trade_date   DATE NOT NULL,
    change_pct   DECIMAL(8,4),                 -- 板块涨跌幅%
    up_count     INT,                          -- 上涨家数
    down_count   INT,                          -- 下跌家数
    leader_code  CHAR(6),                      -- 领涨股代码
    leader_name  VARCHAR(20),                  -- 领涨股名称
    leader_pct   DECIMAL(8,4),                 -- 领涨股涨幅
    turnover     DECIMAL(18,2),                -- 板块成交额
    PRIMARY KEY (sector_code, trade_date)
);

CREATE INDEX idx_sector_daily_date ON sector_daily (trade_date DESC);

-- 数据源: 东财 push2 industry_comparison
-- 更新: 每日盘后
-- 用途: 行业轮动分析、板块强弱对比
```

---

### Layer 7: 调度与监控层

#### 7.1 collect_task — 采集任务定义

```sql
CREATE TABLE collect_task (
    task_id      VARCHAR(50) PRIMARY KEY,
    task_name    VARCHAR(100) NOT NULL,
    data_source  VARCHAR(30),                  -- mootdx/tencent/eastmoney/ths/sina/cninfo
    target_table VARCHAR(50),                  -- 目标写入表
    schedule     VARCHAR(50),                  -- cron表达式
    priority     SMALLINT DEFAULT 5,           -- 优先级1-10
    is_enabled   BOOLEAN DEFAULT TRUE,
    config       JSONB,                        -- 任务配置(如: 票池、参数等)
    description  TEXT,
    created_at   TIMESTAMP DEFAULT NOW()
);
```

#### 7.2 collect_log — 采集执行日志

```sql
CREATE TABLE collect_log (
    id           BIGSERIAL PRIMARY KEY,
    task_id      VARCHAR(50) NOT NULL,
    start_time   TIMESTAMP NOT NULL,
    end_time     TIMESTAMP,
    status       VARCHAR(10),                  -- running/success/failed/timeout
    rows_fetched INT DEFAULT 0,                -- 拉取行数
    rows_written INT DEFAULT 0,                -- 写入行数
    error_msg    TEXT,
    duration_ms  INT,                          -- 耗时毫秒
    extra        JSONB                         -- 额外信息
);

CREATE INDEX idx_log_task ON collect_log (task_id, start_time DESC);
CREATE INDEX idx_log_status ON collect_log (status, start_time DESC);

-- 用途: 监控采集健康度、发现异常
```

#### 7.3 data_quality — 数据质量监控

```sql
CREATE TABLE data_quality (
    id           SERIAL PRIMARY KEY,
    check_date   DATE NOT NULL,
    table_name   VARCHAR(50) NOT NULL,
    check_type   VARCHAR(30),                  -- missing/duplicate/outlier/stale
    issue_count  INT,
    details      JSONB,                        -- 具体问题记录
    resolved     BOOLEAN DEFAULT FALSE,
    created_at   TIMESTAMP DEFAULT NOW()
);

-- 用途: 每日自动检查
-- 检查项: 缺失交易日数据、重复数据、异常值(涨跌幅>20%非ST)、数据陈旧
```

---

## 五、采集策略与调度设计

### 5.1 采集优先级与频率

| 优先级 | 数据 | 数据源 | 频率 | 备注 |
|--------|------|--------|------|------|
| P0 | 日K线 | mootdx | 每日15:30 | 全市场~5000只 |
| P0 | 收盘估值 | 腾讯财经 | 每日15:35 | PE/PB/市值 |
| P1 | 资金流(日) | 东财push2his | 每日16:00 | 全市场(限流,约90分钟) |
| P1 | 龙虎榜 | 东财datacenter | 每日18:00 | 当日上榜 |
| P1 | 北向资金 | 同花顺hsgt | 每日15:05 | 收盘后立即采 |
| P2 | 融资融券 | 东财datacenter | 每日20:00 | T+1公布 |
| P2 | 大宗交易 | 东财datacenter | 每日20:00 | T+1公布 |
| P2 | 强势股归因 | 同花顺热点 | 每日15:30 | 不封IP |
| P2 | 行业板块 | 东财push2 | 每日15:35 | ~100个行业 |
| P3 | 研报 | 东财reportapi | 每日21:00 | 增量采集 |
| P3 | 新闻 | 东财全球资讯 | 每小时 | 滚动增量 |
| P3 | 分钟线 | mootdx | 盘后/按需 | 关注池 |
| P4 | 财务数据 | 新浪三表 | 季度 | 财报季集中采集 |
| P4 | 一致预期 | 同花顺basic | 每周 | 机构预测 |
| P4 | 公告 | 巨潮cninfo | 每日 | 增量 |
| P4 | 股东户数 | 东财datacenter | 季度 | 跟随财报 |
| P5 | 解禁日历 | 东财datacenter | 每周 | 变动不大 |
| P5 | 分红历史 | 东财datacenter | 每月 | 除权季集中 |
| P5 | 板块成分 | 东财slist | 每周 | 调整不频繁 |

### 5.2 盘中实时采集（可选）

```
盘中策略（09:30-15:00）:
├── 每3秒: 腾讯实时行情 → Redis (关注池~50只)
├── 每1分钟: mootdx 五档盘口 → Redis
├── 每5分钟: 资金流分钟级 → fund_flow_minute
├── 每5分钟: 北向分钟流向 → northbound_minute
└── 每30分钟: 实时快照落库 → stock_realtime_snapshot
```

### 5.3 历史数据回填策略

```
一次性回填（建库时执行）:
├── 日K线: mootdx 支持拉取全历史(1990至今), 单次800条, 循环拉取
│   └── 预计: 5000只 × 50次请求 = 25万次TCP请求, 约2-3小时
├── 指数日线: 主要指数从成立日开始
├── 财务数据: 新浪三表从2000年开始, 每只8期 × 三表
├── 融资融券: 东财datacenter 2010年至今 (需限流, 约2天)
├── 龙虎榜: 东财datacenter 2015年至今
├── 大宗交易: 2014年至今
├── 分红历史: 全历史
├── 解禁日历: 全历史
└── 交易日历: 从交易所公告整理

增量补数据:
├── 每日检查: 对比交易日历, 找出缺失日期
├── 自动补采: 对缺失数据自动发起补采任务
└── 断点续采: 记录上次采集位置, 中断后从断点继续
```

---

## 六、东财限流设计（关键）

```python
# 核心限流参数
EM_CONFIG = {
    "min_interval": 1.0,        # 两次请求最小间隔(秒)
    "batch_interval": 1.5,      # 批量模式间隔
    "max_retry": 3,             # 最大重试
    "retry_backoff": 30,        # 被封后等待(秒)
    "daily_limit": 5000,        # 每日请求上限(保守)
    "session_reuse": True,      # 复用HTTP会话
}

# 批量采集策略:
# 全市场资金流(5000只): 5000 × 1.5秒 = 7500秒 ≈ 2小时
# 可拆分到 16:00-18:00 和 20:00-22:00 两个窗口
```

---

## 七、量化策略常用查询场景

以下查询场景验证了表设计的完备性：

```sql
-- 1. 个股估值历史分位 (PE百分位)
SELECT code, trade_date, pe_ttm,
       PERCENT_RANK() OVER (PARTITION BY code ORDER BY pe_ttm) as pe_pctl
FROM valuation_daily
WHERE code = '600519' AND trade_date >= '2015-01-01';

-- 2. 资金面+行情联合分析 (主力连续流入+放量)
SELECT d.code, d.trade_date, d.close, d.volume, d.change_pct,
       f.main_net, f.super_net
FROM stock_daily d
JOIN fund_flow_daily f ON d.code = f.code AND d.trade_date = f.trade_date
WHERE d.code = '000858'
  AND f.main_net > 0  -- 主力净流入
  AND d.volume > (SELECT AVG(volume) FROM stock_daily
                  WHERE code = '000858' AND trade_date >= d.trade_date - 20)
ORDER BY d.trade_date DESC LIMIT 20;

-- 3. 龙虎榜机构连续买入标的
SELECT code, COUNT(*) as times, SUM(net_buy_amt) as total_net
FROM dragon_tiger_record r
JOIN dragon_tiger_seat s ON r.code = s.code AND r.trade_date = s.trade_date
WHERE s.dept_code = '0'  -- 机构席位
  AND s.direction = 'BUY'
  AND r.trade_date >= CURRENT_DATE - 30
GROUP BY code HAVING COUNT(*) >= 2
ORDER BY total_net DESC;

-- 4. 筹码集中 + 业绩增长 选股
SELECT h.code, b.name, h.change_ratio as holder_change,
       fi.profit_yoy, fi.roe
FROM holder_num h
JOIN stock_basic b ON h.code = b.code
JOIN financial_indicator fi ON h.code = fi.code
WHERE h.end_date = (SELECT MAX(end_date) FROM holder_num WHERE code = h.code)
  AND h.change_ratio < -5           -- 股东数减少5%以上
  AND fi.report_date = (SELECT MAX(report_date) FROM financial_indicator WHERE code = fi.code)
  AND fi.profit_yoy > 30            -- 净利润增长30%+
  AND fi.roe > 15                   -- ROE > 15%
ORDER BY h.change_ratio;

-- 5. 行业轮动跟踪 (近5日行业涨幅排名)
SELECT sector_code, 
       (SELECT sector_name FROM sector_info si WHERE si.sector_code = sd.sector_code),
       SUM(change_pct) as cum_change,
       AVG(up_count::float / NULLIF(up_count + down_count, 0)) as avg_up_ratio
FROM sector_daily sd
WHERE trade_date >= CURRENT_DATE - 7
GROUP BY sector_code
ORDER BY cum_change DESC LIMIT 20;

-- 6. 解禁预警 (未来30天大额解禁)
SELECT l.code, b.name, l.free_date, l.stock_type,
       l.free_shares, l.free_ratio,
       l.free_shares * d.close as est_mcap  -- 估算解禁市值
FROM lockup_schedule l
JOIN stock_basic b ON l.code = b.code
JOIN stock_daily d ON l.code = d.code
  AND d.trade_date = (SELECT MAX(trade_date) FROM stock_daily WHERE code = l.code)
WHERE l.free_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 30
  AND l.free_ratio > 5  -- 占比>5%才关注
ORDER BY l.free_date;
```

---

## 八、存储空间估算

| 表 | 预计行数 | 单行大小 | 总大小 |
|----|----------|----------|--------|
| stock_daily | 4000万 | ~100B | ~4 GB |
| stock_minute (2年) | 2.4亿 | ~80B | ~19 GB |
| valuation_daily | 3000万 | ~80B | ~2.4 GB |
| fund_flow_daily | 600万 | ~60B | ~360 MB |
| financial_indicator | 30万 | ~200B | ~60 MB |
| 三大报表 | 各30万 | ~500B | 各~150 MB |
| 其他事件/新闻 | 百万级 | ~200B | ~200 MB |
| **合计** | | | **~28 GB** |

> 单机 PostgreSQL 完全承载，SSD 推荐 100GB+ 预留增长空间。

---

## 九、技术实现建议

### 9.1 项目结构

```
sparrow/
├── config/
│   ├── database.yaml          # 数据库连接配置
│   └── schedule.yaml          # 采集调度配置
├── src/
│   ├── datasource/            # 数据源适配层
│   │   ├── mootdx_source.py   # 通达信(K线/盘口/财务)
│   │   ├── tencent_source.py  # 腾讯(PE/PB/市值/实时)
│   │   ├── eastmoney_source.py # 东财(资金/龙虎榜/研报)
│   │   ├── ths_source.py      # 同花顺(热点/北向/预期)
│   │   ├── sina_source.py     # 新浪(三大报表)
│   │   └── cninfo_source.py   # 巨潮(公告)
│   ├── collector/             # 采集任务
│   │   ├── daily_collector.py  # 每日盘后采集
│   │   ├── realtime_collector.py # 盘中实时
│   │   ├── backfill.py        # 历史回填
│   │   └── scheduler.py       # 调度引擎
│   ├── storage/               # 存储层
│   │   ├── models.py          # SQLAlchemy ORM
│   │   ├── repo.py            # 数据仓库(CRUD)
│   │   └── migrations/        # 数据库迁移(Alembic)
│   ├── quality/               # 数据质量
│   │   ├── checker.py         # 完整性/一致性检查
│   │   └── fixer.py           # 自动修复
│   └── api/                   # 数据服务API(给策略调用)
│       ├── market.py          # 行情查询
│       ├── fundamental.py     # 基本面查询
│       └── signal.py          # 信号层查询
├── scripts/
│   ├── init_db.sql            # 建表DDL
│   ├── backfill_daily.py      # 历史K线回填脚本
│   └── seed_calendar.py       # 交易日历初始化
├── tests/
├── docs/
│   └── database_design.md     # 本文档
└── requirements.txt
```

### 9.2 技术栈推荐

| 组件 | 推荐 | 备选 |
|------|------|------|
| 语言 | Python 3.11+ | |
| 数据库 | PostgreSQL 16 | SQLite(轻量起步) |
| ORM | SQLAlchemy 2.0 | |
| 迁移 | Alembic | |
| 调度 | APScheduler | Celery(重量级) |
| 缓存 | Redis | 内存dict(单机) |
| HTTP | httpx(async) | requests(同步) |
| 配置 | pydantic-settings | |
| 日志 | loguru | |

### 9.3 分步实施路线

```
Phase 1 (1-2周): 基础搭建
├── PostgreSQL + 建表
├── 交易日历
├── stock_basic 全市场
├── 日K线全历史回填 (mootdx)
└── 每日K线自动采集

Phase 2 (1-2周): 估值+资金面
├── 每日估值采集 (腾讯)
├── 资金流日级 (东财)
├── 北向资金 (同花顺)
├── 融资融券
└── 数据质量检查

Phase 3 (1-2周): 事件+信号
├── 龙虎榜 + 席位
├── 热点归因 (同花顺)
├── 解禁日历
├── 行业板块
└── 大宗交易

Phase 4 (2-3周): 财务+研报
├── 新浪三大报表回填
├── 财务指标计算
├── 研报增量采集
├── 一致预期
└── 公告采集

Phase 5 (持续): 高级功能
├── 盘中实时监控
├── 分钟线采集
├── 数据API服务
├── 异常告警
└── 策略回测接口
```

---

## 十、设计要点总结

1. **分区表**: 日K线/分钟线/估值 按时间分区，查询性能和维护都方便
2. **JSONB 兜底**: 三大报表保留 `raw_data` 字段，确保原始数据不丢
3. **增量设计**: 所有表都支持 UPSERT (ON CONFLICT DO UPDATE)，重复采集幂等
4. **数据源隔离**: datasource 层封装所有 API 差异，上层不关心数据来自哪里
5. **限流内置**: 东财请求全走 em_get() 统一限流，不会忘记
6. **质量监控**: 自动检查缺失、重复、异常，采集不是"采了就完"
7. **渐进建设**: 从日K线开始，逐步丰富，不要一口气全做
