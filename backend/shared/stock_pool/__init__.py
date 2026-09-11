"""全局股票池模块（Global Stock Pool）。

设计要点见 `docs`/本包 docstring：
- **读写分离**：后台管理负责「写」（定义 / 成员 / 版本 / 发布），
  所有功能统一经 `PoolResolver` 负责「读」。
- **唯一事实源**：内置池目录（`builtins.py`）收敛原先散落的
  `UNIVERSE_MAP` / `UNIVERSE_NAMES` / `_ALLOWED_UNIVERSES` 三份白名单。
- **混合存储**：成员数 ≤ `MEMBER_TABLE_MAX`（2000）落成员表，
  超过则落 parquet 快照（`materializer.py`）。
- **口径统一**：库内成员一律后缀式 `600036.SH`，转换只经 `normalize.py`
  （内部委托 `StockCodeUtil`），禁止手写切片。

典型用法：

    from backend.shared.stock_pool import resolve_pool

    snap = await resolve_pool("pool:csi300", tenant_id=tid, user_id=uid)
    symbols_prefix = snap.api_symbols   # ['SH600036', ...]
    symbols_suffix = snap.symbols       # ['600036.SH', ...]
"""

from __future__ import annotations

from .constants import (
    BINDING_MODES,
    INSTRUMENT_FILE_PREFIX,
    MARKETS,
    MEMBER_TABLE_MAX,
    POOL_TYPES,
    SCOPES,
    SCOPE_GLOBAL,
    SNAPSHOT_DIR,
    STORAGE_SNAPSHOT,
    STORAGE_TABLE,
    STATUSES,
    STATUS_ARCHIVED,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    TARGET_BACKTEST,
    TARGET_FACTOR,
    TARGET_INFERENCE,
    TARGET_LIVE,
    TARGET_SIMULATION,
    TARGET_STRATEGY,
    TARGET_TRAINING,
    TARGET_TYPES,
)
from .builtins import (
    BUILTIN_BY_CODE,
    BUILTIN_CODES,
    BUILTIN_POOLS,
    INDEX_NAMES,
    INDEX_SYMBOLS,
    BuiltinPool,
    cn_index_names,
    cn_index_symbols,
    get_builtin,
    is_builtin,
)
from .filters import (
    PoolFilterOutcome,
    filter_signals_by_pool,
    intersect_symbols,
)
from .normalize import (
    checksum_symbols,
    is_valid_symbol,
    normalize_market,
    normalize_symbols,
    normalize_to_qlib,
    to_api_symbol,
    to_storage_symbol,
)
from .parser import (
    IndexEntry,
    ParseReport,
    ParseRow,
    StockIndex,
    decode_bytes,
    load_stock_index,
    match_token,
    normalize_name,
    parse_stock_list,
    parse_upload,
    resolve_index_path,
    symbol_to_name,
    validate_symbols,
)
from .resolver import (
    PoolResolver,
    ResolveContext,
    register_index_provider,
    resolve_pool,
    resolve_pool_sync,
    resolver,
)
from .schemas import (
    PoolBinding,
    PoolCreateFromMembersRequest,
    PoolImportRequest,
    PoolImportResult,
    PoolMember,
    PoolMembersReplace,
    PoolParseRequest,
    PoolSnapshot,
    PoolVersion,
    StockPool,
    StockPoolCreate,
    StockPoolUpdate,
)
from .seed import (
    seed_builtin_pools,
    seed_builtin_pools_sync,
    snapshot_dir_ready,
)

__all__ = [
    # constants
    "BINDING_MODES",
    "INSTRUMENT_FILE_PREFIX",
    "MARKETS",
    "MEMBER_TABLE_MAX",
    "POOL_TYPES",
    "SCOPES",
    "SCOPE_GLOBAL",
    "SNAPSHOT_DIR",
    "STORAGE_SNAPSHOT",
    "STORAGE_TABLE",
    "STATUSES",
    "STATUS_ARCHIVED",
    "STATUS_DRAFT",
    "STATUS_PUBLISHED",
    "TARGET_BACKTEST",
    "TARGET_FACTOR",
    "TARGET_INFERENCE",
    "TARGET_LIVE",
    "TARGET_SIMULATION",
    "TARGET_STRATEGY",
    "TARGET_TRAINING",
    "TARGET_TYPES",
    # builtins
    "BUILTIN_BY_CODE",
    "BUILTIN_CODES",
    "BUILTIN_POOLS",
    "INDEX_NAMES",
    "INDEX_SYMBOLS",
    "BuiltinPool",
    "cn_index_names",
    "cn_index_symbols",
    "get_builtin",
    "is_builtin",
    # filters
    "PoolFilterOutcome",
    "filter_signals_by_pool",
    "intersect_symbols",
    # normalize
    "checksum_symbols",
    "is_valid_symbol",
    "normalize_market",
    "normalize_symbols",
    "normalize_to_qlib",
    "to_api_symbol",
    "to_storage_symbol",
    # parser
    "IndexEntry",
    "ParseReport",
    "ParseRow",
    "StockIndex",
    "decode_bytes",
    "load_stock_index",
    "match_token",
    "normalize_name",
    "parse_stock_list",
    "parse_upload",
    "resolve_index_path",
    "symbol_to_name",
    "validate_symbols",
    # resolver
    "PoolResolver",
    "ResolveContext",
    "register_index_provider",
    "resolve_pool",
    "resolve_pool_sync",
    "resolver",
    # schemas
    "PoolBinding",
    "PoolCreateFromMembersRequest",
    "PoolImportRequest",
    "PoolImportResult",
    "PoolMember",
    "PoolMembersReplace",
    "PoolParseRequest",
    "PoolSnapshot",
    "PoolVersion",
    "StockPool",
    "StockPoolCreate",
    "StockPoolUpdate",
    # seed
    "seed_builtin_pools",
    "seed_builtin_pools_sync",
    "snapshot_dir_ready",
]
