# Requirements Document

## Introduction

Sparrow（麻雀虽小，五脏俱全）是一个基于 Streamlit 的个人量化投资助手平台。当前应用有 9 个页面，功能冗余度高，部分模块对目标用户（投资小白、万元级资金）无实际价值。本次重构目标：

1. **增强全球市场联动模块**：显示核心全球指数实时数据，结合 AI 分析异动新闻
2. **精简架构**：从 9 页精简到 6 页，移除/合并冗余模块，实现"小而美"
3. **提升决策效率**：帮助用户快速判断"今天该不该买/卖 ETF 和基金"

目标用户画像：投资小白，~1万资金，持有中概互联ETF + 2只场外基金，最大回撤容忍20%。

## Glossary

- **Sparrow**: 本投资助手系统的名称
- **Global_Market_Module**: 全球市场联动页面，展示美/日/韩/中指数及黄金数据
- **Market_Thermometer**: 市场温度计模块，综合估值/趋势/成交量判断市场冷热
- **Portfolio_Tracker**: 持仓追踪模块，展示用户实际持仓及操作建议
- **Sector_Analyzer**: 行业分析模块，展示行业轮动排名及阶段判断
- **Strategy_Module**: 策略模块，合并后的回测+实盘信号+模拟交易页面
- **AI_Client**: AI 分析客户端，调用 DashScope qwen-max 生成市场解读
- **Navigation_System**: 侧边栏导航系统，管理页面入口
- **Tencent_Finance_API**: 腾讯财经行情接口，提供全球指数实时数据
- **Significant_Move**: 显著异动，定义为单日涨跌幅绝对值超过 2%
- **Data_Freshness_Threshold**: 数据新鲜度阈值，定义为距最新数据不超过 1 个交易日

## Requirements

### Requirement 1: Global Market Real-Time Data Display

**User Story:** As a beginner investor, I want to see the latest day's data for core global indices on one page, so that I can quickly understand overnight market moves before making trading decisions.

#### Acceptance Criteria

1. WHEN the Global_Market_Module page loads, THE Sparrow SHALL display the latest trading day's closing price and daily change percentage for each of the following indices: S&P 500, Nasdaq, Dow Jones, Nikkei 225, KOSPI, CSI 300, Shanghai Composite, ChiNext Index, Hang Seng Index, Hang Seng Tech Index, and Gold Futures.
2. WHEN the Global_Market_Module fetches data from Tencent_Finance_API, THE Sparrow SHALL complete the data retrieval within 10 seconds.
3. IF the Tencent_Finance_API returns an error or times out, THEN THE Sparrow SHALL display the most recently cached data with a visible staleness indicator showing the data timestamp.
4. WHEN global data is successfully fetched, THE Sparrow SHALL display a color-coded treemap visualization grouping assets by region (US, Japan/Korea, A-shares, Hong Kong, Commodities) with red indicating positive change and green indicating negative change.
5. THE Sparrow SHALL display each index with its localized Chinese name, latest price, and percentage change formatted to two decimal places.

### Requirement 2: AI-Powered Significant Move Analysis

**User Story:** As a beginner investor, I want the system to automatically highlight and explain significant market moves using AI, so that I can understand what happened and how it might affect my holdings without searching for news myself.

#### Acceptance Criteria

1. WHEN any tracked global index experiences a Significant_Move (daily change exceeding ±2%), THE Global_Market_Module SHALL visually highlight that index with a distinctive alert badge.
2. WHEN the user clicks the "AI Analysis" button, THE AI_Client SHALL generate a market interpretation that includes: (a) which indices moved significantly and the probable cause, (b) the likely impact on A-share market next trading day, (c) a plain-language actionable suggestion (buy/sell/hold/wait).
3. THE AI_Client SHALL format analysis responses in plain language suitable for a beginner investor, limiting output to 300 characters or fewer in Chinese.
4. IF the AI_API_KEY is not configured, THEN THE Sparrow SHALL display a configuration instruction message instead of the analysis.
5. WHEN generating analysis, THE AI_Client SHALL include the full global market data context (all index prices and changes) in the prompt to ensure the AI response is grounded in current data.

### Requirement 3: Navigation Simplification

**User Story:** As a beginner investor, I want a streamlined navigation with fewer pages, so that I can find what I need without feeling overwhelmed by academic or redundant features.

#### Acceptance Criteria

1. THE Navigation_System SHALL present exactly 6 pages: Market Thermometer (市场温度), Global Market (全球联动), My Portfolio (我的持仓), Sector Analysis (行业分析), Strategy & Trading (策略交易), and User Guide (使用指南).
2. THE Navigation_System SHALL remove the standalone Factor Research (因子研究) page from the sidebar navigation.
3. THE Navigation_System SHALL merge the current Live Trading (实盘操作) page functionality into the Strategy & Trading (策略交易) page as a sub-section.
4. THE Navigation_System SHALL merge the current Paper Trading (模拟盘) page functionality into the Strategy & Trading (策略交易) page as a sub-section.
5. WHEN the user selects a page from the sidebar, THE Navigation_System SHALL render the selected page content within 2 seconds on a standard network connection.

### Requirement 4: Strategy Module Consolidation

**User Story:** As a beginner investor, I want a single unified strategy page that shows backtest results, live trading signals, and paper trading in one place, so that I can understand the full picture without switching between multiple pages.

#### Acceptance Criteria

1. THE Strategy_Module SHALL display three clearly labeled tabs within a single page: "Strategy Backtest" (策略回测), "Live Signal" (实盘信号), and "Paper Trading" (模拟交易).
2. WHEN the "Strategy Backtest" tab is active, THE Strategy_Module SHALL display the V3 strategy backtest results including annualized return, maximum drawdown, and Sharpe ratio.
3. WHEN the "Live Signal" tab is active, THE Strategy_Module SHALL display today's trading signal (buy/sell/hold) with the reasoning and the list of recommended stock operations.
4. WHEN the "Paper Trading" tab is active, THE Strategy_Module SHALL display the paper trading portfolio with current positions, cumulative P&L, and trade history.
5. THE Strategy_Module SHALL present backtest metrics with plain-language explanations suitable for a beginner investor (e.g., "Annual return of 36% means if you invested 10,000 at the start, you'd have 13,600 after one year").

### Requirement 5: Data Accuracy and Freshness

**User Story:** As an investor making real trading decisions, I want to trust that the data shown is accurate and current, so that I do not make decisions based on stale or incorrect information.

#### Acceptance Criteria

1. WHEN the application starts on a weekday, THE Sparrow SHALL automatically check Data_Freshness_Threshold and trigger a background data update if data is stale.
2. THE Sparrow SHALL display a visible data timestamp on every page showing the date of the most recent data used for calculations.
3. WHEN the user clicks the "One-Click Update" (一键更新数据) button, THE Sparrow SHALL fetch the latest daily K-line data, index data, and sector data, then refresh the Parquet cache.
4. IF a data update fails, THEN THE Sparrow SHALL display a specific error message indicating which data source failed and continue displaying previously cached data.
5. THE Sparrow SHALL parse numerical values from Tencent_Finance_API using explicit type conversion and validate that price values are positive numbers before displaying.

### Requirement 6: Remove Factor Research Module

**User Story:** As a beginner investor with limited capital, I want academic research features removed from the main interface, so that the app stays focused on actionable tools I actually use.

#### Acceptance Criteria

1. THE Sparrow SHALL remove the Factor Research (因子研究) page from the sidebar navigation and main application routing.
2. THE Sparrow SHALL retain the factor_engine.py and factor_zoo.py source files in the codebase for internal use by the Strategy_Module.
3. WHEN the Strategy_Module calculates multi-factor scores, THE Strategy_Module SHALL continue to use factor calculations internally without exposing a standalone research interface.

### Requirement 7: User Guide Update

**User Story:** As a beginner investor, I want the user guide to reflect the new simplified structure, so that I can understand how to use each page effectively.

#### Acceptance Criteria

1. WHEN the User Guide page loads, THE Sparrow SHALL display documentation for exactly the 6 pages in the simplified navigation.
2. THE User Guide SHALL include a "Quick Start" section that explains the recommended daily workflow: (1) check Global Market for overnight moves, (2) check Market Thermometer for overall temperature, (3) check My Portfolio for position-specific advice.
3. THE User Guide SHALL explain each metric and technical term using plain language suitable for a beginner investor.
4. THE User Guide SHALL include the investment profile assumptions (small capital, max 20% drawdown tolerance, ETF/fund focus) so the user understands the context of all recommendations.
