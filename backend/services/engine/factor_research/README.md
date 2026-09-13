# factor_research —— 因子研究（factor-lib-demo 改造版）

独立栏目「因子研究」（`/factor-research`）的后端：把开源项目
[fifamy/factor-lib-demo](https://github.com/fifamy/factor-lib-demo)（46 因子静态 Demo）
改造为 QuantMind 平台内的 82 因子多因子研究工作台，数据全部来自 QuantDB。

## 因子库（82 个，来源 data/factor_catalog.json @ 1c01589）

| 大类 (l1) | 小类 (l2) | 数量 |
|---|---|---|
| 市场交易信息 | 动量 / 波动 / 流动性 / Beta / 均值回复 / 价量 / 拥挤度 | 26 |
| 公司内生信息 | 估值 11 / 盈利能力 6 / 盈利质量 3 / 成长 9 / 财务稳健 10 / 市值 / 运营效率 5 | 45 |
| 投资者行为信息 | 分析师预期 6（✗ 缺数据） / 资金流 3（北向/主力，~ 待接入） / 筹码结构 3 | 12 |

- 可用性：快照构建后 `metrics.json.meta.n_factors_computed` 为准（当前 73 个可算，v1 缺 6 个分析师预期 + 3 个资金流）。
- 每个因子的方向（正/负）、公式、Wind 原始数据源见 `catalog.py`（提取自 demo 的 `factor_catalog.json`）。

## 口径（与 demo 对齐）

- **打分**：截面 pct rank → 正态分位（±4 截断），按 direction 调号 → 越高越好；
- **回测**：月末调仓、Top-N 等权、双边成本 0.2%（按换手计）、全 A 非 ST/退市池；
- **IC**：月频 Spearman（score vs 次月末收益）；排行榜综合分 = 0.5×|IC|分位 + 0.5×夏普分位；
- **价格**：daily_forward 前复权；amount 万元（换手 = amount×1e4/流通市值，名义基准一致）。

### 财报口径（实测确认，见 financials.py 头注释）

- `income` 是**单季值**（非 YTD）→ TTM = 最近 4 个连续单季求和；
- `cashflow` 是**年内累计（YTD）** → TTM = 上年年报 + 本年累计 − 上年同期累计；
- ⚠️ **income 的 `cost_of_goods_sold` / `net_profit_incl_min_int_inc` / `tot_profit` / `less_impair_loss_assets`
  在 QuantDB 中几乎全空（列存在但值为 None，2026-09-13 实测）**：
  - 毛利率/净利率/ROA/ROIC/存货周转/利息保障 → 改走 `pershare_index` 现成比率（实测满填充）；
  - 减值风险 → 用 `cashflow.asset_impairment_provision`（TTM）；
  - 应计 → 用归母净利近似；
- PIT：一律以 `m_anntime` 公告日做 as-of；同比/多年增长用「该日再往前 1/2/3 年的可见快照」；
- 亏损股 PE/扣非 PE、负现金流 PCF、负净资产 PB → NaN（rank 自然剔除）。

## 架构

```
catalog.py     82 因子元数据（代码生成自 demo JSON + 可用性标注）
data.py        QuantDB 读取（daily_forward/valuation/index/instrument）
engine.py      行情类 + 估值类因子 + 打分（rank→正态分位，Acklam ppf）
financials.py  PIT 财报面板（单季/YTD 双 TTM）+ 财务/行为因子
analysis.py    IC / Top-N 回测 / KPI / 相关矩阵 / 排行榜
store.py       快照读取（10 分钟 TTL 缓存）
service.py     目录/排行/单因子/对比/实时合成（compose 在线现算）
router.py      /api/v1/factor-research（engine 服务，经网关注册）
```

## 数据流

```
backend/scripts/build_factor_research.py        （构建，pandas，可宿主机裸跑）
    → <quantdb>/factor_research/*.parquet|json   （快照：打分/IC/净值/相关/KPI/持仓）
    → router.py 读取（compose 在线合成）          → 前端 features/factor-research/
```

构建命令（仓库根，全量约 30–45 分钟）：

```bash
python3 backend/scripts/build_factor_research.py                 # 2020-01 至今
python3 backend/scripts/build_factor_research.py --smoke 200     # 冒烟
python3 backend/scripts/build_factor_research.py --skip-financial
```

## API 端点（网关 :8000 → engine :8001）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/factor-research/catalog` | 因子目录 + 快照元信息 |
| GET | `/api/v1/factor-research/leaderboard?sort=` | 排行榜（composite/annual_return/sharpe/ic_mean/ic_ir） |
| GET | `/api/v1/factor-research/factor/{code}` | 单因子：KPI + 净值 + IC + 最新持仓 |
| GET | `/api/v1/factor-research/compare?codes=a,b,c` | 对比（≤12）：KPI + 净值 + IC + 相关子矩阵 |
| POST | `/api/v1/factor-research/compose` | 合成：`{weights:{code:w}, top_n, threshold}` |
| GET | `/api/v1/factor-research/screening` | 筛选结果（保留清单/去重剔除/门槛剔除） |

## 因子筛选（质量门槛 + 同源去重）

```bash
python3 backend/scripts/screen_factors.py            # 默认 |IC|≥0.02 · |ICIR|≥0.2 · 去重 |ρ|≥0.9
python3 backend/scripts/screen_factors.py --skip-cross   # 只做库内去重（不跑跨库相关）
```

- 输入 = 因子报告快照（alpha_library 429 的 IC/ICIR/相关矩阵）+ 本模块 73 个指标
  + 跨库月末截面相关（81 期，缓存于 `screening/cross_corr.npz`）；
- 输出 `screening/`：`factor_selection.json`（kept/剔除原因/簇）、`筛选报告_YYYYMMDD.md`、
  `kept_features.txt`（训练特征清单，一行一个）；
- 前端「筛选」页签（`/api/v1/factor-research/screening`）可视化同一份结果；
- 2026-09-13 首跑：502 → 门槛后 271 → 去重后 **178**（剔除 93 个同源，已知 a101≈gtja
  同构对/STOM≈TURN20 等全部命中）。

## 测试

```bash
python3 -m pytest backend/tests/test_factor_research.py -q
```

## 与「因子报告」的差异

`factor_report`（技能中心页签）是 Alphalens 式因子体检（分位收益/IC/换手/相关），覆盖
`alpha_library / l1 / l2` 数据集；本模块是**可交互的多因子研究**（排行榜→单因子→对比→合成），
覆盖 demo 的 82 因子（含财务/行为类），二者互补。

> 免责声明同项目根 CLAUDE.md：仅供学习研究，不构成投资建议。
