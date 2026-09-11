"""全局股票池模块 - 常量与枚举。

本模块是「全局股票池」的唯一事实源（SSOT）：
- 内置系统池（csi300 等）以 seed 数据形式落 `qm_stock_pool`；
- 所有功能（回测 / 训练 / 推理 / 模拟盘 / 实盘 / 因子挖掘 / 策略 SDK）
  统一经 `PoolResolver` 读取，不再各自维护白名单。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 存储策略
# ---------------------------------------------------------------------------
# 成员数不超过该阈值 → 逐行落 qm_stock_pool_member；
# 超过 → 只写 parquet 快照（storage_mode='snapshot'）。
MEMBER_TABLE_MAX = 2000

# 快照落盘根目录（容器内）。与 QuantDB 数据卷同级，便于训练容器只读挂载。
SNAPSHOT_DIR = "/data/stock_pool"

# 物化出的 Qlib instruments 文件名前缀，避免与 Qlib 原生池（csi300 等）冲突
INSTRUMENT_FILE_PREFIX = "pool_"

# ---------------------------------------------------------------------------
# 池类型
# ---------------------------------------------------------------------------
POOL_TYPE_SYSTEM_INDEX = "system_index"  # 指数成分池（成分来自 QuantDB 指数权重）
POOL_TYPE_STATIC = "static"  # 手工维护的固定成分
POOL_TYPE_IMPORTED = "imported"  # 文件导入
POOL_TYPE_DYNAMIC = "dynamic"  # 规则型动态池（一期仅预留定义，不实现刷新引擎）
POOL_TYPE_ELIGIBILITY = "eligibility"  # 资格型池（如融资融券池）

POOL_TYPES = frozenset(
    {
        POOL_TYPE_SYSTEM_INDEX,
        POOL_TYPE_STATIC,
        POOL_TYPE_IMPORTED,
        POOL_TYPE_DYNAMIC,
        POOL_TYPE_ELIGIBILITY,
    }
)

# ---------------------------------------------------------------------------
# 作用域
# ---------------------------------------------------------------------------
SCOPE_GLOBAL = "global"  # 全平台，仅管理员可改
SCOPE_TENANT = "tenant"  # 租户级（预留）
SCOPE_USER = "user"  # 用户级（预留，当前仍由 stock_pool_files 承担）

SCOPES = frozenset({SCOPE_GLOBAL, SCOPE_TENANT, SCOPE_USER})

# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_ARCHIVED = "archived"

STATUSES = frozenset({STATUS_DRAFT, STATUS_PUBLISHED, STATUS_ARCHIVED})

# ---------------------------------------------------------------------------
# 版本存储模式
# ---------------------------------------------------------------------------
STORAGE_TABLE = "table"
STORAGE_SNAPSHOT = "snapshot"

# ---------------------------------------------------------------------------
# 绑定目标（哪个功能在用这个池）
# ---------------------------------------------------------------------------
TARGET_BACKTEST = "backtest"
TARGET_TRAINING = "training"
TARGET_INFERENCE = "inference"
TARGET_SIMULATION = "simulation"
TARGET_LIVE = "live"
TARGET_STRATEGY = "strategy"
TARGET_FACTOR = "factor"

TARGET_TYPES = frozenset(
    {
        TARGET_BACKTEST,
        TARGET_TRAINING,
        TARGET_INFERENCE,
        TARGET_SIMULATION,
        TARGET_LIVE,
        TARGET_STRATEGY,
        TARGET_FACTOR,
    }
)

# 绑定模式
BINDING_MODE_FILTER = "filter"  # 作为候选全集（交集过滤）
BINDING_MODE_WHITELIST = "whitelist"  # 白名单，与 filter 同义，语义区分
BINDING_MODE_BLACKLIST = "blacklist"  # 排除

BINDING_MODES = frozenset(
    {BINDING_MODE_FILTER, BINDING_MODE_WHITELIST, BINDING_MODE_BLACKLIST}
)

# ---------------------------------------------------------------------------
# 支持的市场（与 data_platform.normalize_market 口径对齐）
# ---------------------------------------------------------------------------
MARKET_CN = "CN"
MARKET_HK = "HK"
MARKET_US = "US"
MARKET_BC = "BC"
MARKET_FUTURES = "FUTURES"

MARKETS = frozenset({MARKET_CN, MARKET_HK, MARKET_US, MARKET_BC, MARKET_FUTURES})

# ---------------------------------------------------------------------------
# Redis 缓存（与项目其它 key 统一走 quantmind: 前缀）
# ---------------------------------------------------------------------------
CACHE_KEY_PREFIX = "quantmind:stock_pool:snapshot"
CACHE_TTL_SECONDS = 3600
