# 全局股票池（Global Stock Pool）

回测 / 模型训练 / 推理 / 模拟盘 / 实盘 / 因子挖掘 / Strategy Lab 共用的
**股票池唯一事实源**。

## 为什么要有这个模块

模块落地前，系统里「股票池」是六个互不相通的孤岛：

| 概念 | 位置 | 粒度 |
|---|---|---|
| `UNIVERSE_MAP` 硬编码 8 个指数池 | `data_platform/quantdb_hub.py` | 全局 |
| 物化文件 `instruments/{pool}.txt` | `qlib_data_builder.py` | 全局 |
| 白名单 `_ALLOWED_UNIVERSES` | `strategy_lab/sdk/context.py` | 全局 |
| `stock_pool_files`（COS 文件 + `is_active` 单选） | `db_init.sql` | 用户 |
| `qm_user_watchlist` / `qm_user_research_pool` | `db_init.sql` | 用户 |
| 融资融券池（文件型） | `shared/margin_stock_pool.py` | 全局 |

三个硬伤：

1. **白名单三份且不一致** —— `_ALLOWED_UNIVERSES` 含 `hs300_ext` / `hk_main` /
   `us_sp500`，而 `UNIVERSE_MAP` 里没有 → SDK 能写、回测解析静默查空。
2. **`universe` 是万能字符串** —— 内置名 / 本地路径 / `cos://` / `user_strategies/`
   路径都塞在同一个字段，在 `backtest_service_runtime.py`、`utils/simple_signal.py`、
   `qlib_data_builder.py` 各解析一遍。
3. **训练与推理完全没有池概念** —— 训练只按 `factor_source` 拉全市场
   （`shared/training/schemas.py` 的 `DataCfg` 无池字段），推理全市场打分。

## 设计原则

**读写分离。**

- **写**（定义 / 成员 / 版本 / 发布）→ 后台管理 `/api/v1/admin/stock-pools/*`
- **读**（各功能消费）→ `PoolResolver`，所有功能只认这一个入口

## 目录结构

```
backend/shared/stock_pool/
├── constants.py      池类型 / 状态 / 作用域 / 绑定目标 / 阈值
├── schemas.py        Pydantic DTO（StockPool / PoolMember / PoolVersion / PoolSnapshot）
├── normalize.py      代码口径归一（内部委托 StockCodeUtil）
├── builtins.py       内置池目录（seed 数据，收敛原三份白名单）
├── repository.py     数据访问（原生 SQL；draft → publish 版本模型）
├── resolver.py       ★ PoolResolver —— 唯一解析入口
├── parser.py         ★ 股票解析引擎（上传文件 ↔ stocks_index.json 对比）
├── filters.py        ★ 信号池过滤（推理/模拟盘/实盘共用，严格语义）
├── legacy_bridge.py  旧 stock_pool_files → 一等池登记桥（P4）
├── materializer.py   物化：parquet 快照 + Qlib instruments/*.txt
├── seed.py           幂等 seed（启动期调用）
└── migrations/001_create_stock_pool.sql
```

## 数据模型（4 张表）

- `qm_stock_pool` —— 池主表（`scope` 决定谁能改：global 仅管理员）
- `qm_stock_pool_version` —— 版本历史，可回滚
- `qm_stock_pool_member` —— 成员（仅 `storage_mode='table'` 的池使用）
- `qm_stock_pool_binding` —— 引用关系（哪个功能在用哪个池）

### 版本模型（draft → publish）

- `current_version` = **已发布**版本号（0 表示从未发布）
- 成员编辑永远作用于 **staging 版本** = `current_version + 1`
- **未发布的编辑对线上消费方不可见**；必须 `publish` 才生效
- 已发布池被再次编辑后 `status` 回落 `draft`（表示有未发布改动）

### 混合存储

| 成员数 | storage_mode | 成员落哪 |
|---|---|---|
| ≤ `MEMBER_TABLE_MAX`（2000） | `table` | `qm_stock_pool_member` 逐行 |
| > 2000 | `snapshot` | parquet 快照（`version.snapshot_path`，目录 `/data/stock_pool`） |

阈值理由：小池要支持逐行增删改查与后台编辑；`all_a` 这类 5000+ 标的的池
每次发布重写全表不划算，走文件快照。

### 代码口径（强制）

- **库内成员一律后缀式**：CN `600036.SH` / HK `0700.HK` / US `AAPL`
- **API 出入参一律前缀式**：CN `SH600036`
- 转换**只经** `normalize.py`（内部委托 `StockCodeUtil`），**禁止手写切片**

## ref 语法（向后兼容全部历史写法）

| ref | 含义 |
|---|---|
| `pool:csi300` | 库内池（按 code），读已发布版本 |
| `pool:csi300@3` | 指定版本（**回测复现用**） |
| `pool_id:sp_xxx_ab12cd34` | 按 pool_id |
| `csi300` / `all_a` | 裸内置池 code（历史写法） |
| `list:SH600036,SZ000001` | 内联列表 |
| `file:/abs/x.txt` | 本地文件（txt 每行一个 / csv 带表头） |
| `/abs/x.txt` | 同上（历史写法：裸路径） |
| `all` | **不过滤**（`unfiltered=True`，对应旧 `universe='all'`） |
| `cos://...` | 回测运行时已解析 → resolver 透传告警，不重复实现 |
| `user_strategies/...` | 旧自定义池路径 → 同上 |

## API

### 后台管理（`require_admin`，`/api/v1/admin/stock-pools`）

```
GET    /meta                     枚举与阈值元信息
GET    /resolve?ref=             解析调试（排障第一入口）
GET    /health                   健康检查（未发布 / 空成员 / 草稿告警）
GET    /                         列表
POST   /                         新建（dynamic 类型一期拒绝）
POST   /import                   导入文件新建池
GET    /{pool_id}                详情 + 最近版本
PATCH  /{pool_id}                改元信息
POST   /{pool_id}/archive        归档（被引用时 409）
DELETE /{pool_id}                删除（仅已归档 / 无引用）
GET    /{pool_id}/members        成员（scope=draft|published）
PUT    /{pool_id}/members        整体覆盖草稿
POST   /{pool_id}/members/import 向已有池导入
GET    /{pool_id}/members/export 导出 CSV
GET    /{pool_id}/preview        预览（含最新行情指标）
POST   /{pool_id}/publish        发布草稿为新版本
GET    /{pool_id}/versions       版本列表
POST   /{pool_id}/rollback       回滚（生成新版本，不破坏历史）
GET    /{pool_id}/diff           版本差异
GET    /{pool_id}/usages         引用情况
```

### 用户态只读（`/api/v1/stock-pools`）

```
GET /options            下拉选项（轻量，各功能页选择器用）
GET /                   列表
GET /resolve?ref=       解析（功能页联调）
GET /{pool_id}          详情
GET /{pool_id}/members  已发布成员
```

前端：`electron/src/features/admin/components/AdminStockPool.tsx`
（后台「推理引擎 → 全局股票池」），页面内分两个页签：
**股票池列表** 与 **上传解析导入**。

## 股票解析（上传 CSV/TXT → 对比索引 → 生成股票池）

用户上传的文件格式千奇百怪，解析引擎（`parser.py`）按以下规则收敛：

```
原始字节 →（自动探测 UTF-8 / GBK）→ 逐行全单元格提取候选
        → 与 data/stocks/stocks_index.json 对比 → 匹配报告 → 确认后建池
```

| 行为 | 说明 |
|---|---|
| **全单元格扫描** | 不假设代码在第几列。逐行把每个单元格依次尝试匹配，第一个命中即为该行股票。因此 `600519,贵州茅台` 与 `贵州茅台,600519` 都能解析 |
| **多写法兼容** | `600519` / `SH600519` / `600519.SH` / `sh600519` / 中文简称 |
| **名称归一化** | NFKC + 去空白：`万 科Ａ` 可匹配 `万科A`；索引无重名故无歧义 |
| **交易所纠正** | `sh300750` / `300750.SH` 这类前缀写错，按 6 位代码纠正为 `300750.SZ` 并告警 |
| **宽松名称兜底** | 去掉 `*` / `ST` 前缀再试一次；仅唯一命中才接受，标记 `name_loose` 供复核 |
| **重复识别** | 同一只股票的不同写法会被判重（`duplicates`） |
| **未匹配分类** | `not_in_index`（格式合法但索引没有：**北交所 / 新股 / 退市**）与 `unrecognized`（纯垃圾）分开 |
| **不落库** | `/parse` 只出报告；用户勾选保留哪些行、填池名后再调 `/create-from-members` |

统计口径：`total = matched(唯一) + unmatched + duplicates`。

**已知边界**：`stocks_index.json` 目前只有沪深两市（5220 只，`exchanges = {SH, SZ}`），
**没有北交所（BJ）**，也没有港股/美股；`pinyin` 字段全空，因此拼音匹配不可用。
这些标的会落在 `not_in_index` 并在报告里给出明确提示，而不是静默丢掉。

## 使用方式

```python
from backend.shared.stock_pool import resolve_pool

snap = await resolve_pool("pool:csi300", tenant_id=tid, user_id=uid)
snap.api_symbols  # ['SH600036', ...]  前缀式，给 API/前端
snap.symbols  # ['600036.SH', ...] 后缀式，给 parquet/Qlib
snap.to_qlib_instruments()  # ['sh600036', ...] Qlib 桥接口径
snap.checksum  # 版本校验和 → 回测/训练结果里必须落库
snap.warnings  # 空池 / 未接入 / 未发布 的原因
```

Celery / 同步上下文用 `resolve_pool_sync(...)`（同一实现，无重复分支）。

## 启动期初始化

`backend/main_oss.py::_ensure_stock_pool()` 在 `main()` 里调用：
建表 + seed 内置池 + 创建快照目录。幂等，失败仅告警（与 `_ensure_seed_admin` 同策略）。

## 后续阶段（P2 / P3 / P4）

- **P2 读路径切换 —— ✅ 已完成**
  - `quantdb_hub.UNIVERSE_MAP` / `UNIVERSE_NAMES` 改为 `dict(cn_index_symbols())` / `dict(cn_index_names())`
  - `qlib_data_builder._build_universe_instruments` 直读 `builtins.cn_index_symbols()`，
    不再读 `hub.UNIVERSE_MAP` 属性（只物化 CN 池，避免把 HK/US 写进 A 股 qlib 缓存）
  - `strategy_lab/sdk/context._ALLOWED_UNIVERSES` = `frozenset(BUILTIN_CODES)`
    （顺带补齐了原先缺失的 `sse50` / `gem` / `star`）
  - `routers/alpha_agent._VALID_CN_UNIVERSES` = `list(cn_index_symbols().keys())`（原先硬编码两处）
  - 回归护栏：`TestP2SingleSourceOfTruth` 含**源码级**检查，禁止再出现硬编码白名单
- **P3 消费方接入 —— ✅ 已完成（核心链路）**
  - ✅ **回测**：`QlibBacktestRequest.pool_id` + 运行时解析并物化 instruments 覆盖 `universe`，
    回填 `pool_version` / `pool_checksum` / `pool_warnings`（随 `request.dict()` 落盘，可复现）。
    **空池显式失败**（抛错 → `status="failed"`），不再静默退化成全市场。
  - ✅ **推理**：`InferenceScriptRunner.execute(pool_id=...)` 在写库前裁剪信号；
    `run_daily_inference_script` 两条路径（主模型 + alpha158 兜底）都透传；
    用户态 `InferenceRunRequest.pool_id` 已开放。
    **池为空或零命中 → `failure_stage="pool_filter"` 失败返回**，不写库。
  - ✅ **训练**：`DataCfg` 新增 `pool_id`/`pool_symbols`/`pool_version`/`pool_checksum`；
    编排器侧（有 DB）用 `training/pool_binding.resolve_training_pool()` 解析并注入
    `config.yaml`（本地 Docker + 远端 SSH 两个编排器共用）；容器内
    `data/loading.py` 在 symbol 归一化之后按池过滤，**池内零命中直接抛错**。
  - ✅ **模拟盘**：`SimulationEngine.run_cycle(pool_id=...)` 在调仓前严格过滤信号
    （也接受 `params_override["pool_id"]`），池为空/零命中终止本轮并写 `report.error`。
  - ✅ **实盘 / 托管调度**：`live_trade_config.pool_id` 为绑定home；
    `manual_execution_service._load_signal_rows()` 新增带池过滤的包装层
    （原查询保持 `_load_signal_rows_raw`，覆盖 DB 与 pred.parquet 两条路径），
    三个调用点全部接入；池为空/零命中 → 返回空列表（**实盘不下单**）。
  - 共用过滤器：`filters.filter_signals_by_pool()`（严格语义，与「单股补推」的宽松兜底刻意区分）
- **P4 收敛治理 —— ✅ 已完成（轻量方案，按「保留 stock_pool_files 兼容」的决定）**
  - **引用守卫真正生效**：`qm_stock_pool_binding` 原先**没有任何写入口**，
    `/usages` 恒为空 → 「被引用不可删」是空转的。现补齐：
    - `POST /{pool_id}/bindings` 登记引用、`DELETE /{pool_id}/bindings/{type}/{id}` 解除、
      `GET /bindings/by-target` 反查某目标绑了哪些池
    - `POST /bindings/reconcile` 从既有模型 `metadata_json.pool_id` **回填**引用，
      让守卫对存量模型也生效（无需改动所有写路径）
    - `docker/training/train.py` 的 metadata 现记录 `pool_id`/`pool_version`/`pool_checksum`，
      供回填与复现
  - **语义约定（重要）**：binding 只登记**长生命周期**引用（策略 / 模型 / 模拟盘账户 /
    实盘配置 / 因子）。回测与推理是**一次性运行**，不登记 binding —— 其池版本随结果记录落盘，
    否则历史运行会让池永不可删且绑定表无界增长。
  - **旧池写侧统一**：`stock_pool_files` 保存时 best-effort 登记为一等池
    （`scope=user` + 成员 + 已发布版本，`source_kind="legacy_cos_file"`），
    于是能被 `pool:<code>` 解析、出现在统一列表里。旧链路（文件 + `is_active`）**完全不动**，
    登记失败只告警。
  - **健康面板**：新增 `binding_count` / `bound_total` / `unbound`，并对「无任何引用登记」的非系统池告警。
  - **跨用户安全修复**：`get_pool_by_code` 新增 `owner_user_id` 过滤 ——
    `scope=user` 的池必须按 owner 限定，否则不同用户同名私有池会互相写入。
  - 前端：池详情抽屉内新增「引用」区（登记/解除/列表），工具栏新增「引用回填预览」。
  - **未做（明确边界）**：完整收编 `qm_user_watchlist` / `qm_user_research_pool`
    到 `scope=user`（需数据迁移 + 前端改造 + 权限配额）—— 与早先「只做全局层」的决定一致，未纳入。

## 未做（明确边界）

- **dynamic 规则型池**：`definition` / `refresh_policy` 字段已预留键位
  （见 `builtins.RESERVED_DEFINITION_KEYS`），规则 DSL 与刷新调度二期再做。
  创建接口对 `pool_type='dynamic'` 直接返回 422，避免出现「建了但不会刷新」的空壳。
- **HK / US 指数成分**：`hk_main` / `us_sp500` 标记 `optional_source=True`，
  数据源未接入时解析返回空并给出明确告警（而不是像旧链路那样静默查空）。
- **`stock_pool_files` 迁移**：本期只做兼容（resolver 对 `user_pool:` / `cos://` 透传告警），
  不做数据迁移。
- **用户级私有池（`scope=user`）**：表结构已支持，但写入与可见性逻辑本期未开放；
  当前所有池都是 `scope=global`。上传解析入口放在后台管理，因此不需要用户级写入。
  若后续要做「普通用户自助建池」，需补齐 `scope=user` 的创建/可见性/配额。
- **北交所 / 港股 / 美股**：`stocks_index.json` 只覆盖沪深两市，解析时这些标的会落在
  `not_in_index`。要支持需先扩展索引来源。
- **动态池刷新调度**：复用 Celery beat 时需新增 `engine.tasks.refresh_stock_pools`，
  不要再造一套调度（系统已因重复 worker 踩过「定时任务随机不执行」的坑）。
