"""全局股票池 - Pydantic DTO。

DB 层统一用原生 SQL（`text()`）访问，ORM 仅用于外部读模型的类型约定；
这与仓库中较新的模块（`admin/quantdb_factor_catalog.py`）保持一致，
避免多个 declarative Base 造成的 metadata 分裂。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

PoolType = Literal["system_index", "static", "imported", "dynamic", "eligibility"]
PoolScope = Literal["global", "tenant", "user"]
PoolStatus = Literal["draft", "published", "archived"]
StorageMode = Literal["table", "snapshot"]
BindingMode = Literal["filter", "whitelist", "blacklist"]


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------
class StockPool(BaseModel):
    pool_id: str
    code: str
    name: str
    description: str | None = None
    market: str = "CN"
    pool_type: PoolType = "static"
    scope: PoolScope = "global"
    tenant_id: str | None = None
    owner_user_id: str | None = None
    status: PoolStatus = "draft"
    visibility: str = "internal"
    definition: dict[str, Any] = Field(default_factory=dict)
    refresh_policy: dict[str, Any] = Field(default_factory=dict)
    current_version: int = 0
    symbol_count: int = 0
    checksum: str | None = None
    source_kind: str | None = None
    source_ref: str | None = None
    is_system: bool = False
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PoolMember(BaseModel):
    symbol: str  # 库内后缀式
    api_symbol: str | None = None  # API 前缀式（读接口回填，便于前端直接用）
    name: str | None = None
    weight: float | None = None
    industry: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class PoolVersion(BaseModel):
    pool_id: str
    version: int
    status: str = "published"
    market: str | None = None
    member_count: int = 0
    checksum: str | None = None
    storage_mode: StorageMode = "table"
    snapshot_path: str | None = None
    changelog: str | None = None
    published_by: str | None = None
    published_at: datetime | None = None


class PoolBinding(BaseModel):
    pool_id: str
    target_type: str
    target_id: str
    mode: BindingMode = "filter"
    priority: int = 100
    tenant_id: str | None = None
    user_id: str | None = None


class PoolBindingRequest(BaseModel):
    """登记一条池引用（P4）。

    只登记**长生命周期**引用（策略 / 模型 / 模拟盘账户 / 实盘配置 / 因子）。
    回测与推理是一次性运行，不登记 binding —— 其可复现信息随结果记录落
    `pool_version` / `pool_checksum`，否则绑定表会随运行次数无界增长，
    且历史回测会让池永不可删。
    """

    target_type: Literal[
        "backtest",
        "training",
        "inference",
        "simulation",
        "live",
        "strategy",
        "factor",
    ]
    target_id: str = Field(..., min_length=1, max_length=200)
    mode: BindingMode = "filter"
    priority: int = Field(default=100, ge=0, le=10000)
    tenant_id: str | None = None
    user_id: str | None = None


class PoolSnapshot(BaseModel):
    """解析结果：所有功能消费的唯一形态。"""

    pool_id: str
    code: str
    version: int | None = None
    market: str = "CN"
    checksum: str | None = None
    as_of: date | None = None
    # 库内口径（后缀式）
    symbols: list[str] = Field(default_factory=list)
    # API 口径（前缀式）
    api_symbols: list[str] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    storage_mode: StorageMode = "table"
    source: str = "pool"  # pool | builtin | inline | file | all
    unfiltered: bool = False  # True 表示「不过滤」（对应旧的 universe='all'）
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.symbols

    def to_qlib_instruments(self) -> list[str]:
        from .normalize import normalize_to_qlib

        return [normalize_to_qlib(s, self.market) for s in self.symbols]


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------
class StockPoolCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    market: str = "CN"
    pool_type: PoolType = "static"
    scope: PoolScope = "global"
    tenant_id: str | None = None
    owner_user_id: str | None = None
    definition: dict[str, Any] = Field(default_factory=dict)
    refresh_policy: dict[str, Any] = Field(default_factory=dict)
    source_kind: str | None = None
    source_ref: str | None = None


class StockPoolUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    definition: dict[str, Any] | None = None
    refresh_policy: dict[str, Any] | None = None
    status: PoolStatus | None = None
    visibility: str | None = None


class PoolMembersReplace(BaseModel):
    """整体覆盖 draft 成员。"""

    members: list[PoolMember] = Field(default_factory=list)
    changelog: str | None = None


class PoolImportRequest(BaseModel):
    """文件导入。`content` 为原始文本（csv/txt），由后端解析。"""

    content: str = Field(..., description="文件内容（CSV/TXT 原始文本）")
    fmt: Literal["csv", "txt"] = "csv"
    has_header: bool = True
    symbol_column: str | None = Field(
        default=None, description="CSV 列名，缺省自动探测 symbol/code/代码"
    )
    changelog: str | None = None


class PoolImportResult(BaseModel):
    total: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    rejected_samples: list[str] = Field(default_factory=list)
    version: int | None = None


# ---------------------------------------------------------------------------
# 上传解析（CSV/TXT → 与 stocks_index.json 对比 → 生成股票池）
# ---------------------------------------------------------------------------
class PoolParseRequest(BaseModel):
    """上传解析请求。

    优先使用 `content_base64`（保留原始字节，可正确解码 GBK/GB18030 的
    Excel 导出文件）；纯手工粘贴内容时用 `content_text`。
    """

    content_base64: str | None = Field(
        default=None, description="文件原始字节的 base64（推荐，能正确处理 GBK）"
    )
    content_text: str | None = Field(default=None, description="直接粘贴的文本内容")
    filename: str | None = Field(default=None, description="原始文件名，用于推断格式")
    fmt: Literal["csv", "txt"] | None = Field(
        default=None, description="缺省按内容/后缀推断"
    )
    has_header: bool = Field(default=True, description="CSV 首行是否为表头")
    column: str | None = Field(
        default=None, description="强制指定代码列（列名或从 0 开始的列序号）"
    )
    row_limit: int = Field(default=500, ge=0, le=20000, description="返回明细行数上限")


class PoolCreateFromMembersRequest(BaseModel):
    """用解析确认后的成员列表建池。

    成员代码由前端回传，服务端仍会重新校验（不信任客户端）。
    """

    code: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    market: str = "CN"
    pool_type: PoolType = "imported"
    members: list[PoolMember] = Field(default_factory=list)
    publish: bool = Field(default=True, description="创建后是否立即发布")
    changelog: str | None = None
