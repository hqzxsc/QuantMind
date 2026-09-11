"""全局股票池 - 唯一解析入口 PoolResolver。

所有功能（回测 / 训练 / 推理 / 模拟盘 / 实盘 / 因子挖掘 / Strategy Lab SDK）
都必须经本模块把「池引用」解析成 `PoolSnapshot`，不再各自维护白名单与路径探测。

支持的 ref 语法（兼容全部历史写法，逐步收敛）：

| ref                          | 含义                                        |
|------------------------------|---------------------------------------------|
| `pool:csi300`                | 库内池（按 code），读已发布版本              |
| `pool:csi300@3`              | 指定版本（回测复现用）                       |
| `pool_id:sp_xxx_ab12cd34`    | 按 pool_id                                  |
| `csi300` / `all_a`           | 裸内置池 code（历史写法）                    |
| `list:SH600036,SZ000001`     | 内联列表                                     |
| `file:/abs/x.txt`            | 本地文件（txt 每行一个代码 / csv 带表头）    |
| `/abs/x.txt`                 | 同上（历史写法：裸路径）                     |
| `all`                        | 不过滤（`unfiltered=True`）                  |
| `cos://...`                  | 已由回测运行时解析 → 此处仅告警透传          |
| `user_strategies/...`        | 旧自定义池路径 → 此处仅告警透传              |

设计取舍：核心逻辑为**同步**实现（同步 psycopg2 池 + QuantDB 同步读），
异步上下文经 `resolve()` 用 `asyncio.to_thread` 包装。单一实现，无重复分支。
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Sequence

from sqlalchemy import text as sql_text

from .constants import (
    MEMBER_TABLE_MAX,
    SCOPE_GLOBAL,
    STORAGE_SNAPSHOT,
    STORAGE_TABLE,
)
from .materializer import read_snapshot
from .normalize import (
    checksum_symbols,
    normalize_market,
    normalize_symbols,
    to_api_symbol,
)
from .schemas import PoolMember, PoolSnapshot

logger = logging.getLogger(__name__)

# 成分提供方签名：(market, index_symbol, pool_code) -> members
# - CN 市场走 pool_code（QuantDB 用 UNIVERSE_MAP 按池名解析，如 csi300 / all_a）
# - 非 CN 市场走 index_symbol（QuantHK / QuantUS 指数代码），P2 补齐
IndexProvider = Callable[[str, str | None, str], list[PoolMember]]

_index_provider: IndexProvider | None = None


def register_index_provider(provider: IndexProvider | None) -> None:
    """注册指数成分提供方（engine 侧启动时注入 QuantDB 实现）。"""
    global _index_provider
    _index_provider = provider


def _default_index_provider(
    market: str, index_symbol: str | None, pool_code: str = ""
) -> list[PoolMember]:
    """默认实现：惰性走 QuantDB 指数权重。

    放在函数内惰性导入，避免 shared 层在模块加载期依赖 engine。
    """
    mk = normalize_market(market)
    if mk == "CN":
        if not pool_code:
            return []
    elif not index_symbol:
        return []

    from backend.services.engine.data_platform.quantdb_hub import QuantDBDataHub

    hub = QuantDBDataHub.get_instance()

    if mk == "CN":
        # QuantDB 的口径是「池名」（csi300 / all_a / ...），不是指数代码
        df = hub.fetch_universe_stocks(pool_code)
    else:
        df = _fetch_index_weights_generic(hub, index_symbol)

    if df is None or getattr(df, "empty", True):
        return []

    symbol_col = "symbol" if "symbol" in df.columns else None
    if symbol_col is None:
        return []
    weight_col = "weight" if "weight" in df.columns else None

    members: list[PoolMember] = []
    for row in df.to_dict("records"):
        sym = str(row.get(symbol_col) or "").strip()
        if not sym:
            continue
        weight = row.get(weight_col) if weight_col else None
        members.append(
            PoolMember(
                symbol=sym,
                weight=float(weight) if weight is not None else None,
            )
        )
    return members


def _fetch_index_weights_generic(hub, index_symbol: str):
    """非 CN 市场：直接按指数代码取权重（QuantDB/QuantHK/QuantUS 口径待 P2 补齐）。"""
    try:
        return hub.fetch_index_weights(index_symbol)
    except Exception as exc:  # noqa: BLE001
        logger.warning("指数权重读取失败 index_symbol=%s: %s", index_symbol, exc)
        return None


@dataclass
class ResolveContext:
    """解析上下文：决定可见范围与代码口径。"""

    tenant_id: str | None = None
    user_id: str | None = None
    market: str | None = None  # 显式市场覆盖（优先于池自身 market）

    def normalized_market(self, fallback: str = "CN") -> str:
        return normalize_market(self.market or fallback)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
class PoolResolver:
    async def resolve(
        self,
        ref: str | None,
        ctx: ResolveContext | None = None,
        *,
        version: int | None = None,
        strict: bool = False,
    ) -> PoolSnapshot:
        return await asyncio.to_thread(
            self.resolve_sync, ref, ctx, version=version, strict=strict
        )

    def resolve_sync(
        self,
        ref: str | None,
        ctx: ResolveContext | None = None,
        *,
        version: int | None = None,
        strict: bool = False,
    ) -> PoolSnapshot:
        ctx = ctx or ResolveContext()
        raw = (ref or "").strip()

        try:
            return self._dispatch(raw, ctx, version=version)
        except Exception as exc:  # noqa: BLE001
            if strict:
                raise
            logger.warning("股票池解析失败 ref=%r: %s", raw, exc)
            return PoolSnapshot(
                pool_id=f"unresolved:{raw}",
                code=raw,
                market=ctx.normalized_market(),
                source="unresolved",
                warnings=[f"解析失败: {exc}"],
            )

    # ------------------------------------------------------------------
    # 分派
    # ------------------------------------------------------------------
    def _dispatch(
        self, raw: str, ctx: ResolveContext, *, version: int | None
    ) -> PoolSnapshot:
        if not raw:
            return self._unfiltered("", ctx, "ref 为空 → 视为不过滤")

        lowered = raw.lower()

        if lowered in ("all", "all_a_share", "*"):
            return self._unfiltered(raw, ctx, "ref=all → 不过滤（全市场）")

        if lowered.startswith("pool_id:"):
            return self._from_pool_id(raw.split(":", 1)[1].strip(), ctx, version)

        if lowered.startswith("pool:"):
            body = raw.split(":", 1)[1].strip()
            code, _, ver = body.partition("@")
            if ver.strip().isdigit() and version is None:
                version = int(ver.strip())
            return self._from_code(code.strip(), ctx, version)

        if lowered.startswith("list:"):
            return self._from_inline(raw.split(":", 1)[1], ctx)

        if lowered.startswith("file:"):
            return self._from_file(raw.split(":", 1)[1].strip(), ctx)

        if lowered.startswith("cos://"):
            return self._unsupported(
                raw,
                ctx,
                "cos:// 引用由回测运行时解析（ai_strategy cos_uploader）；"
                "新链路请先导入为池（POST /api/v1/admin/stock-pools/import）",
            )

        if lowered.startswith("user_pool:") or "user_strategies/" in raw:
            return self._unsupported(
                raw,
                ctx,
                "旧自定义池路径由回测运行时解析（stock_pool_files）；"
                "新链路请迁移为池记录",
            )

        # 裸路径（历史写法：universe 直接是本地 txt 路径）
        if "/" in raw or raw.endswith(".txt") or raw.endswith(".csv"):
            return self._from_file(raw, ctx)

        # 裸 code：先查库，再回内置
        return self._from_code(raw, ctx, version)

    # ------------------------------------------------------------------
    # 各来源
    # ------------------------------------------------------------------
    def _from_pool_id(
        self, pool_id: str, ctx: ResolveContext, version: int | None
    ) -> PoolSnapshot:
        from backend.shared.database_pool import get_db

        with get_db() as session:
            return self._snapshot_for_pool_id_sync(session, pool_id, ctx, version)

    def _from_code(
        self, code: str, ctx: ResolveContext, version: int | None
    ) -> PoolSnapshot:
        if not code:
            return self._unfiltered("", ctx, "code 为空 → 不过滤")

        from backend.shared.database_pool import get_db

        with get_db() as session:
            row = self._query_pool_by_code(session, code, ctx)
            if row is None:
                # 库内没有：回退内置目录（fresh install / seed 未跑）
                from .builtins import get_builtin

                builtin = get_builtin(code)
                if builtin is not None:
                    return self._from_builtin(builtin, ctx, version)
                return PoolSnapshot(
                    pool_id=f"missing:{code}",
                    code=code,
                    market=ctx.normalized_market(),
                    source="missing",
                    warnings=[f"未知股票池 code={code}（库内无记录，内置目录也没有）"],
                )
            return self._snapshot_for_row_sync(session, row, ctx, version)

    def _from_builtin(
        self, builtin, ctx: ResolveContext, version: int | None
    ) -> PoolSnapshot:
        market = ctx.normalized_market(builtin.market)
        members = self._index_members(market, builtin.index_symbol, builtin.code)
        warnings: list[str] = []
        if not members and not builtin.optional_source:
            warnings.append(f"内置池 {builtin.code} 未取到成分（QuantDB 指数权重为空）")
        elif not members:
            warnings.append(f"内置池 {builtin.code} 依赖的数据源尚未接入，当前为空")
        return self._build_snapshot(
            pool_id=builtin.pool_id,
            code=builtin.code,
            version=None,
            market=market,
            members=members,
            source="builtin",
            warnings=warnings,
        )

    def _from_inline(self, body: str, ctx: ResolveContext) -> PoolSnapshot:
        market = ctx.normalized_market()
        symbols = normalize_symbols(
            [s for s in body.replace(";", ",").split(",") if s.strip()], market
        )
        members = [PoolMember(symbol=s) for s in symbols]
        return self._build_snapshot(
            pool_id="inline",
            code="inline",
            version=None,
            market=market,
            members=members,
            source="inline",
            warnings=[],
        )

    def _from_file(self, path: str, ctx: ResolveContext) -> PoolSnapshot:
        market = ctx.normalized_market()
        fp = Path(path)
        if not fp.is_absolute():
            fp = Path.cwd() / fp
        if not fp.exists():
            return PoolSnapshot(
                pool_id=f"file:{path}",
                code=path,
                market=market,
                source="file",
                warnings=[f"股票池文件不存在: {fp}"],
            )

        text = fp.read_text(encoding="utf-8", errors="ignore")
        raw_symbols: list[str] = []
        if fp.suffix.lower() == ".csv":
            reader = csv.reader(io.StringIO(text))
            rows = [r for r in reader if r and any(c.strip() for c in r)]
            if rows:
                header = [c.strip().lower() for c in rows[0]]
                idx = 0
                for candidate in ("symbol", "code", "证券代码", "代码"):
                    if candidate in header:
                        idx = header.index(candidate)
                        break
                else:
                    # 无表头 → 第一行当数据
                    raw_symbols.append(rows[0][idx])
                for r in rows[1:]:
                    if idx < len(r):
                        raw_symbols.append(r[idx])
        else:
            for line in text.splitlines():
                parts = line.strip().split("\t")
                if parts and parts[0]:
                    raw_symbols.append(parts[0])

        symbols = normalize_symbols(raw_symbols, market)
        members = [PoolMember(symbol=s) for s in symbols]
        return self._build_snapshot(
            pool_id=f"file:{fp}",
            code=fp.stem,
            version=None,
            market=market,
            members=members,
            source="file",
            warnings=[],
        )

    # ------------------------------------------------------------------
    # 库内池
    # ------------------------------------------------------------------
    def _query_pool_by_code(self, session, code: str, ctx: ResolveContext):
        scopes = [SCOPE_GLOBAL]
        if ctx.tenant_id:
            scopes.append("tenant")
        if ctx.user_id:
            scopes.append("user")

        for scope in scopes:
            row = (
                session.execute(
                    sql_text(
                        """
                    SELECT * FROM qm_stock_pool
                     WHERE code = :code
                       AND scope = :scope
                       AND (
                            scope = 'global'
                         OR tenant_id = :tid
                         OR owner_user_id = :uid
                       )
                       AND status <> 'archived'
                     ORDER BY CASE scope WHEN 'user' THEN 0 WHEN 'tenant' THEN 1 ELSE 2 END
                     LIMIT 1
                    """
                    ),
                    {
                        "code": code,
                        "scope": scope,
                        "tid": ctx.tenant_id,
                        "uid": ctx.user_id,
                    },
                )
                .mappings()
                .first()
            )
            if row is not None:
                return row
        return None

    def _snapshot_for_pool_id_sync(
        self, session, pool_id: str, ctx: ResolveContext, version: int | None
    ) -> PoolSnapshot:
        row = (
            session.execute(
                sql_text("SELECT * FROM qm_stock_pool WHERE pool_id = :pid"),
                {"pid": pool_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            return PoolSnapshot(
                pool_id=pool_id,
                code=pool_id,
                market=ctx.normalized_market(),
                source="missing",
                warnings=[f"pool_id 不存在: {pool_id}"],
            )
        return self._snapshot_for_row_sync(session, row, ctx, version)

    def _snapshot_for_row_sync(
        self, session, row, ctx: ResolveContext, version: int | None
    ) -> PoolSnapshot:
        data = dict(row)
        pool_id = str(data["pool_id"])
        code = str(data["code"])
        market = ctx.normalized_market(data.get("market") or "CN")
        definition = data.get("definition") or {}
        warnings: list[str] = []

        current_version = int(data.get("current_version") or 0)

        # 1) 指定版本 → 读版本快照
        if version is not None:
            ver_row = (
                session.execute(
                    sql_text(
                        "SELECT * FROM qm_stock_pool_version "
                        "WHERE pool_id = :pid AND version = :ver"
                    ),
                    {"pid": pool_id, "ver": int(version)},
                )
                .mappings()
                .first()
            )
            if ver_row is None:
                return PoolSnapshot(
                    pool_id=pool_id,
                    code=code,
                    market=market,
                    source="pool",
                    warnings=[f"{pool_id} 不存在版本 v{version}"],
                )
            members = self._members_of_version(
                session, pool_id, dict(ver_row), warnings
            )
            return self._build_snapshot(
                pool_id=pool_id,
                code=code,
                version=int(version),
                market=market,
                members=members,
                source="pool",
                warnings=warnings,
            )

        # 2) 指数成分型 → 优先实时取（成分会随指数调整变化）
        if definition.get("kind") == "index_weights":
            members = self._index_members(market, definition.get("index_symbol"), code)
            if members:
                return self._build_snapshot(
                    pool_id=pool_id,
                    code=code,
                    version=current_version or None,
                    market=market,
                    members=members,
                    source="pool",
                    warnings=warnings,
                )
            if not definition.get("optional_source"):
                warnings.append(f"{code} 指数权重为空，回退已发布成员")

        # 3) 已发布版本
        if current_version <= 0:
            warnings.append(f"{code} 尚未发布任何版本，线上不可消费（后台需先发布）")
            return self._build_snapshot(
                pool_id=pool_id,
                code=code,
                version=None,
                market=market,
                members=[],
                source="pool",
                warnings=warnings,
            )

        ver_row = (
            session.execute(
                sql_text(
                    "SELECT * FROM qm_stock_pool_version "
                    "WHERE pool_id = :pid AND version = :ver"
                ),
                {"pid": pool_id, "ver": current_version},
            )
            .mappings()
            .first()
        )
        ver_data = dict(ver_row) if ver_row else {"storage_mode": STORAGE_TABLE}
        members = self._members_of_version(session, pool_id, ver_data, warnings)
        return self._build_snapshot(
            pool_id=pool_id,
            code=code,
            version=current_version,
            market=market,
            members=members,
            source="pool",
            warnings=warnings,
        )

    def _members_of_version(
        self, session, pool_id: str, ver_data: dict, warnings: list[str]
    ) -> list[PoolMember]:
        from sqlalchemy import text as _text

        version = int(ver_data.get("version") or 0)
        storage_mode = ver_data.get("storage_mode") or STORAGE_TABLE

        if storage_mode == STORAGE_SNAPSHOT:
            path = ver_data.get("snapshot_path")
            if not path:
                warnings.append(f"{pool_id} v{version} 标记为 snapshot 但未记录路径")
                return []
            members = read_snapshot(path)
            if not members:
                warnings.append(f"{pool_id} v{version} 快照为空或不可读: {path}")
            return members

        rows = (
            session.execute(
                sql_text(
                    "SELECT symbol, name, weight, industry, effective_from, effective_to, meta "
                    "FROM qm_stock_pool_member "
                    "WHERE pool_id = :pid AND version = :ver ORDER BY symbol ASC"
                ),
                {"pid": pool_id, "ver": version},
            )
            .mappings()
            .all()
        )
        return [
            PoolMember(
                symbol=str(r["symbol"]),
                name=r.get("name"),
                weight=r.get("weight"),
                industry=r.get("industry"),
                effective_from=r.get("effective_from"),
                effective_to=r.get("effective_to"),
                meta=r.get("meta") or {},
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # 指数成分
    # ------------------------------------------------------------------
    def _index_members(
        self, market: str, index_symbol: str | None, pool_code: str = ""
    ) -> list[PoolMember]:
        provider = _index_provider or _default_index_provider
        try:
            raw = provider(market, index_symbol, pool_code)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "指数成分读取失败 market=%s index_symbol=%s pool_code=%s: %s",
                market,
                index_symbol,
                pool_code,
                exc,
            )
            return []

        out: list[PoolMember] = []
        seen: set[str] = set()
        for m in raw or []:
            codes = normalize_symbols([m.symbol], market)
            if not codes:
                continue
            code = codes[0]
            if code in seen:
                continue
            seen.add(code)
            out.append(m.model_copy(update={"symbol": code, "api_symbol": None}))
        return out

    # ------------------------------------------------------------------
    # 组装
    # ------------------------------------------------------------------
    def _build_snapshot(
        self,
        *,
        pool_id: str,
        code: str,
        version: int | None,
        market: str,
        members: Sequence[PoolMember],
        source: str,
        warnings: list[str],
    ) -> PoolSnapshot:
        symbols = [m.symbol for m in members]
        weights = {m.symbol: float(m.weight) for m in members if m.weight is not None}
        return PoolSnapshot(
            pool_id=pool_id,
            code=code,
            version=version,
            market=market,
            checksum=checksum_symbols(symbols) if symbols else None,
            symbols=symbols,
            api_symbols=[to_api_symbol(s, market) for s in symbols],
            weights=weights,
            storage_mode=(
                STORAGE_TABLE if len(symbols) <= MEMBER_TABLE_MAX else STORAGE_SNAPSHOT
            ),
            source=source,
            warnings=warnings,
        )

    def _unfiltered(self, raw: str, ctx: ResolveContext, reason: str) -> PoolSnapshot:
        return PoolSnapshot(
            pool_id="all",
            code="all",
            market=ctx.normalized_market(),
            source="all",
            unfiltered=True,
            warnings=[reason],
        )

    def _unsupported(self, raw: str, ctx: ResolveContext, reason: str) -> PoolSnapshot:
        return PoolSnapshot(
            pool_id=f"unsupported:{raw}",
            code=raw,
            market=ctx.normalized_market(),
            source="unsupported",
            warnings=[reason],
        )


resolver = PoolResolver()


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------
async def resolve_pool(
    ref: str | None,
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
    market: str | None = None,
    version: int | None = None,
    strict: bool = False,
) -> PoolSnapshot:
    return await resolver.resolve(
        ref,
        ResolveContext(tenant_id=tenant_id, user_id=user_id, market=market),
        version=version,
        strict=strict,
    )


def resolve_pool_sync(
    ref: str | None,
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
    market: str | None = None,
    version: int | None = None,
    strict: bool = False,
) -> PoolSnapshot:
    return resolver.resolve_sync(
        ref,
        ResolveContext(tenant_id=tenant_id, user_id=user_id, market=market),
        version=version,
        strict=strict,
    )
