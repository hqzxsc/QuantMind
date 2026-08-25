# QuantMind Skills（AI 编程工具技能包）

QuantMind 提供一组完整的**通用技能**（Skills），让主流 AI 编程工具都能调用平台的量化功能：数据分析、因子挖掘、模型训练、策略生成、回测、模拟交易、部署运维等。

每个技能是 `.claude/skills/<skill-name>/SKILL.md`，通过自然语言触发词激活。安装后，AI 助手可自动识别用户意图并调用对应技能完成操作。

## 兼容工具

| 工具 | 说明 |
|------|------|
| **Claude Code / QuantBot** | 原生支持 SKILL.md，解压到 `~/.claude/skills/` 即自动识别 |
| **OpenCode** | 开源 AI 编程工具，读项目 AGENTS.md（把技能要点并入即可） |
| **OpenAI Codex** | 读 `~/.codex/AGENTS.md`（技能要点放入） |
| **腾讯 Trae / CodeBuddy** | 读项目 AGENTS.md |
| **字节豆包 MarsCode** | 读项目 AGENTS.md |

> **通用做法**：所有工具都读取 AGENTS.md。把技能包关键流程（如部署检查表、API 端点清单）导入项目 `AGENTS.md`，AI 即可按流程执行。Claude Code 用户直接解压到 `~/.claude/skills/` 最省事。

## 安装方式

### 方式一：从技能包安装（推荐）

打包文件：`quantmind-operations-skill.zip`，包含全部技能。

```bash
# Claude Code / QuantBot：解压到全局技能目录
unzip quantmind-operations-skill.zip -d ~/.claude/

# 其他工具：解压后把 SKILL.md 要点并入项目 AGENTS.md
unzip quantmind-operations-skill.zip -d /tmp/qm-skills/
cat /tmp/qm-skills/.claude/skills/quantmind-deploy/SKILL.md >> AGENTS.md
```

### 方式二：从项目仓库安装

项目根目录 `.claude/skills/` 下即为全部技能，直接使用或复制到 `~/.claude/skills/`。

## 技能总览

| 技能 | 触发词 | 功能 |
|------|--------|------|
| [quantmind-operations](#quantmind-operations) | 模型训练、模型管理、数据更新、RSS、新闻分析 | 平台运营操作总指南：模型训练、模型管理、后台数据更新、推理、RSS 新闻对接 |
| [stock-market-analysis](#stock-market-analysis) | 分析市场、数据分析、全市场扫描、行业轮动、导出CSV | 股票市场深度数据分析与导出（多市场） |
| [smart-strategy-stock-picking](#smart-strategy-stock-picking) | 选股、筛选股票、股票池、条件选股 | 基于 QuantDB 数据的条件选股（自然语言 / 条件） |
| [ai-ide-strategy-writing](#ai-ide-strategy-writing) | 写策略、生成策略、策略代码、AI-IDE | AI 生成 Qlib 量化策略代码，AI-IDE 执行（Docker runner） |
| [backtest-center](#backtest-center) | 回测、回测中心、策略对比、参数优化 | Qlib 回测：快速回测、专家模式、策略对比、参数优化、向量化极速回测 |
| [batch-inference-analysis](#batch-inference-analysis) | 分析批量推理、解读信号、每日选股 | 批量推理结果分析：每日信号、行业轮动、个股分数、分数校准 |
| [rd-agent-factor-mining](#rd-agent-factor-mining) | 挖因子、因子挖掘、因子演化、RD-Agent | 自动调用 RD-Agent 进行因子挖掘（多市场） |
| [quantdb-sdk](#quantdb-sdk) | quantdb、数据key、数据集、查询K线 | QuantDB 数据 SDK：API Key、数据集目录、字段查询 |
| [simulation-trading](#simulation-trading) | 模拟交易、模拟下单、查持仓、查账户 | 模拟交易：下单买卖、持仓管理、成交查询、模拟盘启动 |
| [tdx-live-trading](#tdx-live-trading) | 实时推理、自动买卖、实盘下单、挂单、撤单、交易记录、持仓、实时监控、链路状态、TDX、通达信 | 实盘链路操作+全链路实时监控：桥健康、L2 实时推理循环、滚动买卖策略、今日委托/手续费、实盘持仓 |
| [trading-agents](#trading-agents) | 投研分析、研究报告、多Agent分析、TradingAgents | 多 Agent 投研分析：7 分析师、辩论、风险评估、报告生成 |
| [quantmind-deploy](#quantmind-deploy) | 部署、一键部署、部署失败、装不上 | QuantMind 部署运维：一键/快速/手动部署、数据库初始化、问题排查 |
| [news-sentiment-research](#news-sentiment-research) | 新闻情绪、新闻规律、情绪分析、消息面、新闻回测、情绪策略 | 新闻情绪研究方法论：七维深度分析 → 融合规律优化回测 → 研报 MD+PDF |

---

## 国际市场支持

QuantMind 全面支持 **A 股 / 港股 / 美股 / 区块链 / 期货** 五大市场。技能在对应市场自动切换数据源与训练配置。

### 国际市场数据接入

| 市场 | 本地数据中枢 | 数据源 | 同步脚本 | Qlib 缓存 | 特征文件 |
|------|-------------|--------|----------|-----------|----------|
| **A 股** | `data/quantdb/`（QuantDBDataHub） | QuantDB SDK（付费高质量：K线/财报/估值/315维AI因子） | `quantdb_daily_sync.py` | `data/quantdb/.qlib_cache/cn_data` | `db/feature_snapshots/model_features_{year}.parquet`（按年） |
| **港股** | `data/quanthk/`（QuantHKDataHub） | Yahoo Finance + akshare 港股基本面 + CCASS 机构持仓 + 南向资金 | `quanthk_daily_sync.py` | `data/quanthk/.qlib_cache/hk_data` | `db/feature_snapshots/model_features_hk.parquet` |
| **美股** | `data/quantus/`（QuantUSDataHub） | Yahoo Finance + akshare 指数 | `quantus_daily_sync.py` | `data/quantus/.qlib_cache/us_data` | `db/feature_snapshots/model_features_us.parquet` |
| **区块链** | `data/quantbc/`（QuantBCDataHub） | Binance（`data-api.binance.vision`，支持 5m/1m） | `quantbc_daily_sync.py` | `data/quantbc/.qlib_cache/bc_data` | `db/feature_snapshots/model_features_crypto.parquet` |
| **期货** | `data/quantfutures/`（QuantFuturesDataHub） | akshare（贵金属/商品/国际 `foreign_daily/cn_daily/sge_daily`） | `quantfutures_daily_sync.py` | `data/quantfutures/.qlib_cache/futures_data` | `db/feature_snapshots/model_features_futures.parquet` |

### 数据流转链

```
外部数据源（QuantDB SDK / Yahoo / Binance / akshare）
      ↓  各市场 *_daily_sync.py
本地 parquet（data/quantdb/、data/quanthk/、data/quantus/、data/quantbc/、data/quantfutures/）
      ↓  quantdb_daily_sync 的 fill_pg_from_parquet（DuckDB join）
PostgreSQL stock_daily_latest（A股快照表）
      ↓  qlib_data_builder.py（QlibDataBuilder.for_market）
Qlib 二进制缓存（.qlib_cache/{cn,hk,us,bc,futures}_data）
      ↓  generate_feature_snapshots.py / update_feature_parquet.py
特征快照（db/feature_snapshots/model_features_{year|market}.parquet + .metadata.json）
      ↓  模型训练 / 推理 / 回测
```

- **A股同步 4 阶段**：`sync_parquet()`（V2 分区增量）→ `fill_pg_from_parquet()` → `update_qlib_cache()` → 年度特征快照
- **多市场 Qlib 路径**：`backend/shared/qlib_paths.py` 的 `resolve_qlib_provider_uri(market)` 按市场解析
- **特征快照**：A股按年 `model_features_{year}.parquet`（含 .metadata.json 年度详情），非A股单体 `model_features_{market}.parquet`

### 国际市场模型训练

模型训练已实现 **按市场切换**：

- **特征目录市场过滤** — `config/features/model_training_feature_catalog_v1.json` 每个特征带 `markets` 标签；训练时按所选市场过滤可用特征（A股 197 全量，港股/美股/区块链各 100+，期货 118）
- **训练数据源切换** — `docker/training/train.py` 的 `load_data(market=...)` 按市场读取对应特征 parquet（`model_features_{market}.parquet`）
- **模型市场分段存储** — `backend/shared/model_registry.py`：模型 ID 带市场前缀（`mdl_{market}_...`），非 A 股模型存储到 `{user}/{market}/` 子目录；`list_models(market=...)` 支持按市场过滤
- **推理脚本市场参数** — 推理模板（`inference_ensemble*.py`）支持 `--market`，按模型 metadata 的市场自动发现模型、加载对应数据
- **回测中心** — `backtestConfig` 按市场切换 Qlib provider_uri / 基准指数 / 股票池

#### 国际市场训练示例

```bash
# 港股模型训练（后端 API）
curl -X POST $BASE/api/v1/models/run-training \
  -H "$AUTH" -H "$CT" \
  -d '{
    "model_type": "lightgbm",
    "display_name": "HK_volume_model",
    "train_start": "2022-01-01",
    "train_end": "2025-12-31",
    "features": ["mom_ret_20d", "vol_atr_20", "liq_amihud_20"],
    "context": {
      "market": "HK",
      "benchmark": "HSI",
      "initial_capital": 1000000
    }
  }'
```

---

## 技能详情

### quantmind-operations

**平台运营操作总指南**。覆盖模型训练（5 步流程：特征选择 → 训练目标 → 参数配置 → 执行训练 → 结果入库）、模型管理、后台数据更新、字段信息查询、RSS 新闻对接与分析。

**参考文件**：
- `references/news-analysis.md` — RSS 新闻对接与分析
- `references/training-data-ops.md` — 训练数据运营

### stock-market-analysis

**股票市场深度数据分析与导出**。全市场信号扫描、行业轮动、个股全维度分析、数据挖掘、CSV/Excel 导出。支持多市场（A股/港股/美股）。

### smart-strategy-stock-picking

**智能策略选股**。基于 QuantDB 数据的条件选股，支持自然语言或结构化条件筛选股票、构建股票池、生成策略。选股池按市场动态加载（港股/美股/区块链/期货从本地 parquet 读取标的 + 名称/PE/市值补充）。

### ai-ide-strategy-writing

**AI-IDE 写策略**。用 AI 生成 Qlib 量化策略代码、自然语言条件选股、策略落库。

### backtest-center

**回测中心**。快速回测、专家模式、回测历史、策略对比、参数优化、策略管理、高级分析。回测配置按市场切换（Qlib provider_uri、基准指数、股票池）。

### batch-inference-analysis

**批量推理结果分析**。用选股策略方法论分析每日信号、行业轮动、个股分数区间、负分参考。基于推理回测数据（信号表 trade_date=T+1）。

### rd-agent-factor-mining

**RD-Agent 因子挖掘**。自动调用 RD-Agent 进行因子演化，支持 **A股/港股/美股/区块链/期货** 五市场，各市场使用专属因子集（Alpha158 / HK_ALPHA / US_ALPHA / CRYPTO_ALPHA / FUTURES_ALPHA）与 Qlib 缓存。

### quantdb-sdk

**QuantDB 数据 SDK**。API Key 配置、数据集目录、字段查询、K 线/财务/因子远程查询。

### simulation-trading

**模拟交易 + 实盘/模拟盘切换**。下单买卖、持仓管理、成交查询、账户状态、资金快照、模拟盘启动（real-trading）、组合管理（portfolios）。前端 HeaderBar 右上角 REAL/SIM 拨动开关切换实盘/模拟盘（localStorage 持久化，默认模拟盘）。

### tdx-live-trading

**TDX 实盘链路 + 模拟/实盘全链路实时监控**。覆盖：L2 实时推理循环（`tdx:l2:config`）、滚动买卖策略选择（execute_mode/阈值/上证MA20 只卖不买）、桥下单/挂单/卖出/撤单（代码后缀格式、T+1、盘后废单规则）、交易记录（30s UPSERT 同步 + 手续费估算）、实盘持仓（volume=0 过滤）、桥健康监控。一键状态快照：`docker exec -i -w /app quantmind python - < .claude/skills/tdx-live-trading/scripts/tdx_live_status.py`，AI 按异常判定表巡检。桥源码在 `bridge/windows/`。

### trading-agents

**投研分析（TradingAgents）**。多 Agent 研究报告生成：7 分析师（技术/情绪/新闻/基本面/政策/游资/解禁）+ 质量门控 + 多空辩论 + 交易决策 + 风控评估，12 阶段管线。支持 CN/US/HK/CRYPTO/FUTURES，本地 QuantDB 数据驱动。

### quantmind-deploy

**部署运维**。一键部署（`quick-deploy.sh`）、快速部署、手动部署、数据库初始化（`db_init.sql` 含 users 表）、服务健康检查、常见部署问题排查（用户表缺失/Docker Compose 版本/镜像源/torch 安装等）。11+ 容器架构总览。含：技能包安装指南（让 AI 部署）、推荐编程工具、**AutoDL 云端 GPU 训练**（`training-nodes` 配置/测试/状态）。

### news-sentiment-research

**新闻情绪研究方法论**。Huntly RSS 42 万篇新闻 → FinBERT+词典双引擎情绪 → 事件研究（T+0~T+20 收益曲线）+ 七维深度分析（来源预测力/时段特征/信号强度/首日动量/情绪反转/事件标签/连续信号）→ 融合规律优化策略回测（来源白名单+时段过滤+多篇确认+首日动量+反转出场+连续/标签加成+无止损+动态止盈）→ 研报级 MD+PDF 落盘。实测最优 +84.34% / Calmar 7.76。

**参考文件**：
- `scripts/backtest_news_event_study.py` — 事件研究
- `scripts/backtest_news_deep_analysis.py` — 七维深度分析
- `scripts/backtest_news_optimized.py` — 优化策略回测（消融开关）

### 市场分析页面（股票市场分析）

市场分析页面（`/api/v1/market-analysis/*`）核心端点：
- `GET /indices/overview` — 五大指数快照
- `GET /money-flow/stocks?limit=` — 个股资金流向排行
- `GET /money-flow/period?period=&dimension=&category=&limit=` — 多周期资金流（1/3/5/10/20日）
- `GET /money-flow/sankey` — 主力/散户资金流桑基图
- `GET /tags/by-tag` / `/tags/by-stock` — 概念/行业标签双向查询
- `GET /heatmap?trade_date=` — 申万行业热力图

---

## 相关平台功能（非技能）

以下功能通过 QuantBot 交互或前端页面访问，已纳入对应技能或国际市场能力：

- **智能策略向导** — 条件选股 → 股票池 → 策略参数 → 验证保存 四步流程
- **市场分析平台** — 大盘广度（涨跌家数）、资金流向（北向/主力/申万 Sankey 图）、申万行业热力图、板块轮动、规则引擎；页面版端点见 [[stock-market-analysis]] 第 5 节（`/api/v1/market-analysis/*`）
- **投研分析（TradingAgents）** — 见 [[trading-agents]] 技能：多 Agent A 股/港股/美股研究报告生成（7 分析师 + 辩论 + 风险评估，12 阶段管线）
- **实盘/模拟盘切换** — 见 [[simulation-trading]] 技能：前端 HeaderBar REAL/SIM 开关 + `/api/v1/real-trading/*` + `/api/v1/simulation/*`

---

## 技能开发约定

新增技能时：

1. 在 `.claude/skills/<skill-name>/` 下创建 `SKILL.md`
2. `SKILL.md` 顶部 YAML frontmatter 必须包含 `name` 和 `description`（description 含触发词，供 AI 意图识别）
3. 内容按「认证 → 端点 → 示例」组织，所有 API 统一走 `/api/v1` 前缀
4. 涉及市场的操作，标注市场参数（CN/HK/US/CRYPTO/FUTURES）
5. 打包进 `quantmind-operations-skill.zip`（保持目录结构 `.claude/skills/<name>/`）
