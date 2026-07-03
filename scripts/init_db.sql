-- ============================================================
-- Sparrow 量化数据库 — 建表脚本
-- PostgreSQL 16+
-- 执行: psql -U sparrow -d sparrow -f scripts/init_db.sql
-- ============================================================

-- Layer 0: 元数据层
-- ============================================================

CREATE TABLE IF NOT EXISTS stock_basic (
    code         CHAR(6) PRIMARY KEY,
    name         VARCHAR(20) NOT NULL,
    market       VARCHAR(4) NOT NULL,
    board        VARCHAR(10),
    industry_l1  VARCHAR(30),
    industry_l2  VARCHAR(30),
    list_date    DATE,
    delist_date  DATE,
    total_shares BIGINT,
    float_shares BIGINT,
    is_st        BOOLEAN DEFAULT FALSE,
    is_active    BOOLEAN DEFAULT TRUE,
    updated_at   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trade_calendar (
    cal_date     DATE PRIMARY KEY,
    is_open      BOOLEAN NOT NULL,
    prev_trade   DATE,
    next_trade   DATE
);

CREATE TABLE IF NOT EXISTS index_basic (
    code         VARCHAR(10) PRIMARY KEY,
    name         VARCHAR(40) NOT NULL,
    market       VARCHAR(4),
    category     VARCHAR(20),
    base_date    DATE,
    base_point   DECIMAL(10,2),
    components   INT,
    updated_at   TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- Layer 1: 行情层
-- ============================================================

-- 日K线 (分区表，按年分区)
CREATE TABLE IF NOT EXISTS stock_daily (
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    open         DECIMAL(10,3),
    high         DECIMAL(10,3),
    low          DECIMAL(10,3),
    close        DECIMAL(10,3),
    volume       BIGINT,
    amount       DECIMAL(18,2),
    turnover     DECIMAL(8,4),
    amplitude    DECIMAL(8,4),
    change_pct   DECIMAL(8,4),
    change_amt   DECIMAL(10,3),
    adj_factor   DECIMAL(12,6) DEFAULT 1.0,
    PRIMARY KEY (code, trade_date)
) PARTITION BY RANGE (trade_date);

-- 创建年度分区 (1990-2030)
DO $$
DECLARE
    yr INT;
BEGIN
    FOR yr IN 1990..2030 LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS stock_daily_%s PARTITION OF stock_daily
             FOR VALUES FROM (%L) TO (%L)',
            yr,
            format('%s-01-01', yr),
            format('%s-01-01', yr + 1)
        );
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_daily_code ON stock_daily (code, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_date ON stock_daily (trade_date, code);

-- 指数日线
CREATE TABLE IF NOT EXISTS index_daily (
    code         VARCHAR(10) NOT NULL,
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

DO $$
DECLARE
    yr INT;
BEGIN
    FOR yr IN 1990..2030 LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS index_daily_%s PARTITION OF index_daily
             FOR VALUES FROM (%L) TO (%L)',
            yr,
            format('%s-01-01', yr),
            format('%s-01-01', yr + 1)
        );
    END LOOP;
END $$;

-- ============================================================
-- Layer 2: 财务层
-- ============================================================

CREATE TABLE IF NOT EXISTS financial_indicator (
    code           CHAR(6) NOT NULL,
    report_date    DATE NOT NULL,
    report_type    VARCHAR(10),
    eps            DECIMAL(10,4),
    bvps           DECIMAL(10,4),
    roe            DECIMAL(8,4),
    roa            DECIMAL(8,4),
    gross_margin   DECIMAL(8,4),
    net_margin     DECIMAL(8,4),
    revenue        DECIMAL(18,2),
    net_profit     DECIMAL(18,2),
    deducted_profit DECIMAL(18,2),
    revenue_yoy    DECIMAL(8,4),
    profit_yoy     DECIMAL(8,4),
    debt_ratio     DECIMAL(8,4),
    current_ratio  DECIMAL(8,4),
    ocf_per_share  DECIMAL(10,4),
    dividend_ps    DECIMAL(10,4),
    PRIMARY KEY (code, report_date)
);

CREATE INDEX IF NOT EXISTS idx_fin_ind_code
    ON financial_indicator (code, report_date DESC);

-- ============================================================
-- Layer 3: 估值层
-- ============================================================

CREATE TABLE IF NOT EXISTS valuation_daily (
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    pe_ttm       DECIMAL(12,4),
    pe_static    DECIMAL(12,4),
    pb           DECIMAL(10,4),
    ps_ttm       DECIMAL(10,4),
    mcap         DECIMAL(18,2),
    float_mcap   DECIMAL(18,2),
    turnover     DECIMAL(8,4),
    PRIMARY KEY (code, trade_date)
) PARTITION BY RANGE (trade_date);

DO $$
DECLARE
    yr INT;
BEGIN
    FOR yr IN 2005..2030 LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS valuation_daily_%s PARTITION OF valuation_daily
             FOR VALUES FROM (%L) TO (%L)',
            yr,
            format('%s-01-01', yr),
            format('%s-01-01', yr + 1)
        );
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_val_code
    ON valuation_daily (code, trade_date DESC);

-- 一致预期
CREATE TABLE IF NOT EXISTS analyst_consensus (
    code           CHAR(6) NOT NULL,
    forecast_year  SMALLINT NOT NULL,
    snap_date      DATE NOT NULL,
    eps_avg        DECIMAL(10,4),
    eps_min        DECIMAL(10,4),
    eps_max        DECIMAL(10,4),
    analyst_count  SMALLINT,
    revenue_avg    DECIMAL(18,2),
    profit_avg     DECIMAL(18,2),
    target_price   DECIMAL(10,3),
    PRIMARY KEY (code, forecast_year, snap_date)
);

-- 研报
CREATE TABLE IF NOT EXISTS research_report (
    id             SERIAL PRIMARY KEY,
    info_code      VARCHAR(30) UNIQUE,
    code           CHAR(6),
    title          VARCHAR(200) NOT NULL,
    publish_date   DATE,
    org_name       VARCHAR(50),
    author         VARCHAR(100),
    rating         VARCHAR(10),
    eps_this_year  DECIMAL(10,4),
    eps_next_year  DECIMAL(10,4),
    eps_next2_year DECIMAL(10,4),
    industry       VARCHAR(30),
    report_type    VARCHAR(20),
    pdf_path       VARCHAR(200),
    created_at     TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_report_code
    ON research_report (code, publish_date DESC);

-- ============================================================
-- Layer 4: 资金层
-- ============================================================

CREATE TABLE IF NOT EXISTS fund_flow_daily (
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    main_net     DECIMAL(18,2),
    super_net    DECIMAL(18,2),
    large_net    DECIMAL(18,2),
    mid_net      DECIMAL(18,2),
    small_net    DECIMAL(18,2),
    PRIMARY KEY (code, trade_date)
) PARTITION BY RANGE (trade_date);

DO $$
DECLARE
    yr INT;
BEGIN
    FOR yr IN 2015..2030 LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS fund_flow_daily_%s PARTITION OF fund_flow_daily
             FOR VALUES FROM (%L) TO (%L)',
            yr,
            format('%s-01-01', yr),
            format('%s-01-01', yr + 1)
        );
    END LOOP;
END $$;

CREATE TABLE IF NOT EXISTS margin_trading (
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    rzye         DECIMAL(18,2),
    rzmre        DECIMAL(18,2),
    rzche        DECIMAL(18,2),
    rqye         DECIMAL(18,2),
    rqmcl        BIGINT,
    rqchl        BIGINT,
    rzrqye       DECIMAL(18,2),
    PRIMARY KEY (code, trade_date)
);

CREATE TABLE IF NOT EXISTS block_trade (
    id           SERIAL PRIMARY KEY,
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    deal_price   DECIMAL(10,3),
    close_price  DECIMAL(10,3),
    premium_pct  DECIMAL(8,4),
    deal_volume  BIGINT,
    deal_amount  DECIMAL(18,2),
    buyer        VARCHAR(100),
    seller       VARCHAR(100),
    created_at   TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_block_code
    ON block_trade (code, trade_date DESC);

CREATE TABLE IF NOT EXISTS northbound_flow (
    trade_date   DATE PRIMARY KEY,
    hgt_net      DECIMAL(12,4),
    sgt_net      DECIMAL(12,4),
    total_net    DECIMAL(12,4),
    hgt_buy      DECIMAL(12,4),
    hgt_sell     DECIMAL(12,4),
    sgt_buy      DECIMAL(12,4),
    sgt_sell     DECIMAL(12,4)
);

CREATE TABLE IF NOT EXISTS northbound_minute (
    trade_date   DATE NOT NULL,
    time         VARCHAR(10) NOT NULL,
    hgt_cumul    DECIMAL(12,4),
    sgt_cumul    DECIMAL(12,4),
    PRIMARY KEY (trade_date, time)
);

-- ============================================================
-- Layer 5: 事件层
-- ============================================================

CREATE TABLE IF NOT EXISTS dragon_tiger_record (
    id           SERIAL PRIMARY KEY,
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    reason       VARCHAR(200),
    net_buy_amt  DECIMAL(18,2),
    buy_amt      DECIMAL(18,2),
    sell_amt     DECIMAL(18,2),
    turnover_pct DECIMAL(8,4),
    close_price  DECIMAL(10,3),
    change_pct   DECIMAL(8,4),
    UNIQUE (code, trade_date, reason)
);

CREATE INDEX IF NOT EXISTS idx_dtb_code
    ON dragon_tiger_record (code, trade_date DESC);

CREATE TABLE IF NOT EXISTS dragon_tiger_seat (
    id           SERIAL PRIMARY KEY,
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    direction    VARCHAR(4) NOT NULL,
    rank         SMALLINT,
    dept_name    VARCHAR(100),
    dept_code    VARCHAR(20),
    buy_amt      DECIMAL(18,2),
    sell_amt     DECIMAL(18,2),
    net_amt      DECIMAL(18,2)
);

CREATE INDEX IF NOT EXISTS idx_seat_code
    ON dragon_tiger_seat (code, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_seat_dept
    ON dragon_tiger_seat (dept_name, trade_date DESC);

CREATE TABLE IF NOT EXISTS lockup_schedule (
    id           SERIAL PRIMARY KEY,
    code         CHAR(6) NOT NULL,
    free_date    DATE NOT NULL,
    stock_type   VARCHAR(50),
    free_shares  BIGINT,
    free_ratio   DECIMAL(8,4),
    UNIQUE (code, free_date, stock_type)
);

CREATE INDEX IF NOT EXISTS idx_lockup_date
    ON lockup_schedule (free_date);

CREATE TABLE IF NOT EXISTS dividend_history (
    id              SERIAL PRIMARY KEY,
    code            CHAR(6) NOT NULL,
    ex_date         DATE,
    record_date     DATE,
    report_year     VARCHAR(10),
    bonus_rmb       DECIMAL(10,6),
    transfer_ratio  DECIMAL(8,4),
    bonus_ratio     DECIMAL(8,4),
    progress        VARCHAR(20),
    UNIQUE (code, ex_date, report_year)
);

CREATE TABLE IF NOT EXISTS holder_num (
    code         CHAR(6) NOT NULL,
    end_date     DATE NOT NULL,
    holder_num   INT,
    change_num   INT,
    change_ratio DECIMAL(8,4),
    avg_shares   DECIMAL(12,2),
    PRIMARY KEY (code, end_date)
);

CREATE TABLE IF NOT EXISTS hot_stocks (
    code         CHAR(6) NOT NULL,
    trade_date   DATE NOT NULL,
    name         VARCHAR(20),
    reason_tags  VARCHAR(200),
    change_pct   DECIMAL(8,4),
    turnover_pct DECIMAL(8,4),
    amount       DECIMAL(18,2),
    dde_net      DECIMAL(18,2),
    PRIMARY KEY (code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_hot_date
    ON hot_stocks (trade_date DESC);

-- ============================================================
-- Layer 6: 板块层
-- ============================================================

CREATE TABLE IF NOT EXISTS sector_info (
    sector_code  VARCHAR(10) PRIMARY KEY,
    sector_name  VARCHAR(40) NOT NULL,
    sector_type  VARCHAR(10),
    component_count INT,
    updated_at   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sector_component (
    sector_code  VARCHAR(10) NOT NULL,
    code         CHAR(6) NOT NULL,
    is_leader    BOOLEAN DEFAULT FALSE,
    updated_at   TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (sector_code, code)
);

CREATE INDEX IF NOT EXISTS idx_sc_code ON sector_component (code);

CREATE TABLE IF NOT EXISTS sector_daily (
    sector_code  VARCHAR(10) NOT NULL,
    trade_date   DATE NOT NULL,
    change_pct   DECIMAL(8,4),
    up_count     INT,
    down_count   INT,
    leader_code  CHAR(6),
    leader_name  VARCHAR(20),
    leader_pct   DECIMAL(8,4),
    turnover     DECIMAL(18,2),
    PRIMARY KEY (sector_code, trade_date)
);

-- ============================================================
-- Layer 7: 调度与监控层
-- ============================================================

CREATE TABLE IF NOT EXISTS collect_task (
    task_id      VARCHAR(50) PRIMARY KEY,
    task_name    VARCHAR(100) NOT NULL,
    data_source  VARCHAR(30),
    target_table VARCHAR(50),
    schedule     VARCHAR(50),
    priority     SMALLINT DEFAULT 5,
    is_enabled   BOOLEAN DEFAULT TRUE,
    config       JSONB,
    description  TEXT,
    created_at   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS collect_log (
    id           BIGSERIAL PRIMARY KEY,
    task_id      VARCHAR(50) NOT NULL,
    start_time   TIMESTAMP NOT NULL,
    end_time     TIMESTAMP,
    status       VARCHAR(10),
    rows_fetched INT DEFAULT 0,
    rows_written INT DEFAULT 0,
    error_msg    TEXT,
    duration_ms  INT,
    extra        JSONB
);

CREATE INDEX IF NOT EXISTS idx_log_task
    ON collect_log (task_id, start_time DESC);

CREATE TABLE IF NOT EXISTS data_quality (
    id           SERIAL PRIMARY KEY,
    check_date   DATE NOT NULL,
    table_name   VARCHAR(50) NOT NULL,
    check_type   VARCHAR(30),
    issue_count  INT,
    details      JSONB,
    resolved     BOOLEAN DEFAULT FALSE,
    created_at   TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- 完成
-- ============================================================
COMMENT ON TABLE stock_basic IS '股票基础信息（全市场A股）';
COMMENT ON TABLE stock_daily IS '日线行情（分区表，按年）';
COMMENT ON TABLE valuation_daily IS '每日估值指标（分区表，按年）';
COMMENT ON TABLE fund_flow_daily IS '个股资金流向（日级，分区表）';
COMMENT ON TABLE collect_log IS '数据采集执行日志';
