"""全局股票池 - 数据访问层（原生 SQL）。

版本模型（draft → publish）：
- `qm_stock_pool.current_version` = **已发布**版本号（0 表示从未发布）。
- 成员编辑永远作用于 **staging 版本** = `current_version + 1`。
- `publish()` 把 staging 提升为 current_version 并落版本记录；
  未发布的编辑对线上消费方（resolver）不可见。
- 已发布池被再次编辑后 `status` 回落 `draft`，表示「有未发布改动」。
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from collections.abc import Iterable, Sequence

from sqlalchemy import text

from .constants import (
    MEMBER_TABLE_MAX,
    SCOPE_GLOBAL,
    STORAGE_SNAPSHOT,
    STORAGE_TABLE,
    STATUS_ARCHIVED,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
)
from .normalize import (
    checksum_symbols,
    normalize_market,
    normalize_symbols,
    to_api_symbol,
)
from .schemas import (
    PoolMember,
    PoolVersion,
    StockPool,
    StockPoolCreate,
    StockPoolUpdate,
)

logger = logging.getLogger(__name__)

_MIGRATION_SQL = (
    Path(__file__).resolve().parent / "migrations" / "001_create_stock_pool.sql"
)

_TABLES_READY = False


# ---------------------------------------------------------------------------
# 建表
# ---------------------------------------------------------------------------
async def ensure_tables(session) -> None:
    """幂等建表。SQL 文件是唯一事实源（main_oss 启动期也会执行同一份）。"""
    global _TABLES_READY
    if _TABLES_READY:
        return
    try:
        sql = _MIGRATION_SQL.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 仅在文件缺失时触发
        logger.error("股票池建表 SQL 读取失败: %s", exc)
        raise
    for statement in _split_statements(sql):
        await session.execute(text(statement))
    await session.commit()
    _TABLES_READY = True


def _split_statements(sql: str) -> list[str]:
    """按分号拆分 SQL 语句，跳过纯注释行。"""
    lines: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        lines.append(line)
    body = "\n".join(lines)
    return [s.strip() for s in body.split(";") if s.strip()]


# ---------------------------------------------------------------------------
# 行 → DTO
# ---------------------------------------------------------------------------
def _row_to_pool(row) -> StockPool:
    data = dict(row)
    return StockPool(**data)


def _row_to_member(row, market: str) -> PoolMember:
    data = dict(row)
    symbol = data.get("symbol") or ""
    data["api_symbol"] = to_api_symbol(symbol, market)
    data.pop("pool_id", None)
    data.pop("version", None)
    data.pop("id", None)
    data.pop("created_at", None)
    return PoolMember(**data)


def _row_to_version(row) -> PoolVersion:
    data = dict(row)
    data.pop("id", None)
    return PoolVersion(**data)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 池 CRUD
# ---------------------------------------------------------------------------
async def list_pools(
    session,
    *,
    market: str | None = None,
    pool_type: str | None = None,
    status: str | None = None,
    scope: str | None = None,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[StockPool], int]:
    where: list[str] = []
    params: dict[str, Any] = {"limit": int(limit), "offset": int(offset)}

    if market:
        where.append("market = :market")
        params["market"] = normalize_market(market)
    if pool_type:
        where.append("pool_type = :pool_type")
        params["pool_type"] = pool_type
    if status:
        where.append("status = :status")
        params["status"] = status
    if scope:
        where.append("scope = :scope")
        params["scope"] = scope
    if keyword:
        where.append("(code ILIKE :kw OR name ILIKE :kw)")
        params["kw"] = f"%{keyword}%"

    clause = f"WHERE {' AND '.join(where)}" if where else ""

    total = (
        await session.execute(
            text(f"SELECT COUNT(*) FROM qm_stock_pool {clause}"), params
        )
    ).scalar() or 0

    rows = (
        (
            await session.execute(
                text(
                    f"""
                SELECT * FROM qm_stock_pool
                {clause}
                ORDER BY is_system DESC, market ASC, code ASC
                LIMIT :limit OFFSET :offset
                """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )

    return [_row_to_pool(r) for r in rows], int(total)


async def get_pool(session, pool_id: str) -> StockPool | None:
    row = (
        (
            await session.execute(
                text("SELECT * FROM qm_stock_pool WHERE pool_id = :pid"),
                {"pid": pool_id},
            )
        )
        .mappings()
        .first()
    )
    return _row_to_pool(row) if row else None


async def get_pool_by_code(
    session,
    code: str,
    *,
    scope: str = SCOPE_GLOBAL,
    tenant_id: str | None = None,
    owner_user_id: str | None = None,
) -> StockPool | None:
    """按 (scope, tenant, code) 查池。

    `owner_user_id` 传值时额外限定 owner —— **scope='user' 必须传**，
    否则不同用户的同名私有池会互相命中（曾出现在旧池登记桥里）。
    """
    sql = """
        SELECT * FROM qm_stock_pool
        WHERE scope = :scope
          AND COALESCE(tenant_id, '') = COALESCE(:tid, '')
          AND code = :code
    """
    params: dict[str, Any] = {"scope": scope, "tid": tenant_id, "code": code}
    if owner_user_id is not None:
        sql += " AND owner_user_id = :owner"
        params["owner"] = str(owner_user_id)

    row = (await session.execute(text(sql), params)).mappings().first()
    return _row_to_pool(row) if row else None


async def create_pool(
    session, payload: StockPoolCreate, actor: str = "system"
) -> StockPool:
    pool_id = _new_pool_id(payload.code)
    await session.execute(
        text(
            """
            INSERT INTO qm_stock_pool (
                pool_id, code, name, description, market, pool_type, scope,
                tenant_id, owner_user_id, status, visibility, definition,
                refresh_policy, source_kind, source_ref, is_system,
                created_by, updated_by
            ) VALUES (
                :pool_id, :code, :name, :description, :market, :pool_type, :scope,
                :tenant_id, :owner_user_id, :status, :visibility,
                CAST(:definition AS JSONB), CAST(:refresh_policy AS JSONB),
                :source_kind, :source_ref, FALSE, :actor, :actor
            )
            """
        ),
        {
            "pool_id": pool_id,
            "code": payload.code,
            "name": payload.name,
            "description": payload.description,
            "market": normalize_market(payload.market),
            "pool_type": payload.pool_type,
            "scope": payload.scope,
            "tenant_id": payload.tenant_id,
            "owner_user_id": payload.owner_user_id,
            "status": STATUS_DRAFT,
            "visibility": "internal",
            "definition": _json(payload.definition),
            "refresh_policy": _json(payload.refresh_policy),
            "source_kind": payload.source_kind,
            "source_ref": payload.source_ref,
            "actor": actor,
        },
    )
    await session.commit()
    created = await get_pool(session, pool_id)
    assert created is not None
    return created


async def update_pool(
    session, pool_id: str, payload: StockPoolUpdate, actor: str = "system"
) -> StockPool | None:
    sets: list[str] = ["updated_at = NOW()", "updated_by = :actor"]
    params: dict[str, Any] = {"pid": pool_id, "actor": actor}

    if payload.name is not None:
        sets.append("name = :name")
        params["name"] = payload.name
    if payload.description is not None:
        sets.append("description = :description")
        params["description"] = payload.description
    if payload.definition is not None:
        sets.append("definition = CAST(:definition AS JSONB)")
        params["definition"] = _json(payload.definition)
    if payload.refresh_policy is not None:
        sets.append("refresh_policy = CAST(:refresh_policy AS JSONB)")
        params["refresh_policy"] = _json(payload.refresh_policy)
    if payload.status is not None:
        sets.append("status = :status")
        params["status"] = payload.status
    if payload.visibility is not None:
        sets.append("visibility = :visibility")
        params["visibility"] = payload.visibility

    await session.execute(
        text(f"UPDATE qm_stock_pool SET {', '.join(sets)} WHERE pool_id = :pid"), params
    )
    await session.commit()
    return await get_pool(session, pool_id)


async def set_pool_status(
    session, pool_id: str, status: str, actor: str = "system"
) -> None:
    await session.execute(
        text(
            """
            UPDATE qm_stock_pool
               SET status = :status, updated_at = NOW(), updated_by = :actor
             WHERE pool_id = :pid
            """
        ),
        {"pid": pool_id, "status": status, "actor": actor},
    )
    await session.commit()


async def archive_pool(session, pool_id: str, actor: str = "system") -> list[str]:
    """软删。返回阻止归档的引用描述（非空表示已被引用）。"""
    usages = await list_usages(session, pool_id)
    if usages:
        return [f"{u['target_type']}:{u['target_id']}" for u in usages]
    await set_pool_status(session, pool_id, STATUS_ARCHIVED, actor)
    return []


async def delete_pool(session, pool_id: str) -> None:
    """硬删（成员/版本/绑定由外键级联删除）。仅允许已归档的池。"""
    await session.execute(
        text("DELETE FROM qm_stock_pool WHERE pool_id = :pid"), {"pid": pool_id}
    )
    await session.commit()


# ---------------------------------------------------------------------------
# 版本
# ---------------------------------------------------------------------------
async def staging_version(session, pool_id: str) -> int:
    row = (
        await session.execute(
            text("SELECT current_version FROM qm_stock_pool WHERE pool_id = :pid"),
            {"pid": pool_id},
        )
    ).first()
    current = int(row[0]) if row and row[0] is not None else 0
    return current + 1


async def has_draft_changes(session, pool_id: str) -> bool:
    staging = await staging_version(session, pool_id)
    count = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM qm_stock_pool_member "
                "WHERE pool_id = :pid AND version = :ver"
            ),
            {"pid": pool_id, "ver": staging},
        )
    ).scalar() or 0
    return int(count) > 0


async def list_versions(session, pool_id: str, limit: int = 50) -> list[PoolVersion]:
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT * FROM qm_stock_pool_version
                 WHERE pool_id = :pid
                 ORDER BY version DESC
                 LIMIT :limit
                """
                ),
                {"pid": pool_id, "limit": int(limit)},
            )
        )
        .mappings()
        .all()
    )
    return [_row_to_version(r) for r in rows]


async def get_version(session, pool_id: str, version: int) -> PoolVersion | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT * FROM qm_stock_pool_version WHERE pool_id = :pid AND version = :ver"
                ),
                {"pid": pool_id, "ver": int(version)},
            )
        )
        .mappings()
        .first()
    )
    return _row_to_version(row) if row else None


# ---------------------------------------------------------------------------
# 成员
# ---------------------------------------------------------------------------
async def list_members(
    session,
    pool_id: str,
    market: str = "CN",
    *,
    version: int | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[PoolMember], int]:
    ver = (
        int(version) if version is not None else await staging_version(session, pool_id)
    )

    total = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM qm_stock_pool_member "
                "WHERE pool_id = :pid AND version = :ver"
            ),
            {"pid": pool_id, "ver": ver},
        )
    ).scalar() or 0

    sql = (
        "SELECT * FROM qm_stock_pool_member "
        "WHERE pool_id = :pid AND version = :ver ORDER BY symbol ASC"
    )
    params: dict[str, Any] = {"pid": pool_id, "ver": ver}
    if limit is not None:
        sql += " LIMIT :limit OFFSET :offset"
        params["limit"] = int(limit)
        params["offset"] = int(offset)

    rows = (await session.execute(text(sql), params)).mappings().all()
    return [_row_to_member(r, market) for r in rows], int(total)


async def list_published_symbols(session, pool_id: str, version: int) -> list[str]:
    """读取已发布版本的成员（后缀式）。仅 storage_mode='table' 有成员行。"""
    rows = (
        await session.execute(
            text(
                "SELECT symbol FROM qm_stock_pool_member "
                "WHERE pool_id = :pid AND version = :ver ORDER BY symbol ASC"
            ),
            {"pid": pool_id, "ver": int(version)},
        )
    ).all()
    return [str(r[0]) for r in rows]


async def replace_members(
    session,
    pool_id: str,
    members: Sequence[PoolMember],
    market: str = "CN",
    *,
    changelog: str | None = None,
    actor: str = "system",
) -> int:
    """整体覆盖 staging 成员。返回写入条数。"""
    mk = normalize_market(market)
    ver = await staging_version(session, pool_id)

    await session.execute(
        text(
            "DELETE FROM qm_stock_pool_member WHERE pool_id = :pid AND version = :ver"
        ),
        {"pid": pool_id, "ver": ver},
    )

    deduped = _dedupe_members(members, mk)
    for chunk in _chunks(deduped, 500):
        await session.execute(
            text(
                """
                INSERT INTO qm_stock_pool_member (
                    pool_id, version, symbol, name, weight, industry,
                    effective_from, effective_to, meta
                ) VALUES (
                    :pool_id, :version, :symbol, :name, :weight, :industry,
                    :effective_from, :effective_to, CAST(:meta AS JSONB)
                )
                """
            ),
            [
                {
                    "pool_id": pool_id,
                    "version": ver,
                    "symbol": m.symbol,
                    "name": m.name,
                    "weight": m.weight,
                    "industry": m.industry,
                    "effective_from": m.effective_from,
                    "effective_to": m.effective_to,
                    "meta": _json(m.meta),
                }
                for m in chunk
            ],
        )

    # 有未发布改动 → 状态回落 draft（已归档的池不在此处复活）
    await session.execute(
        text(
            """
            UPDATE qm_stock_pool
               SET status = CASE WHEN status = :published THEN :draft ELSE status END,
                   updated_at = NOW(),
                   updated_by = :actor
             WHERE pool_id = :pid
            """
        ),
        {
            "pid": pool_id,
            "published": STATUS_PUBLISHED,
            "draft": STATUS_DRAFT,
            "actor": actor,
        },
    )
    if changelog:
        await session.execute(
            text(
                """
                UPDATE qm_stock_pool
                   SET refresh_policy = refresh_policy || CAST(:patch AS JSONB)
                 WHERE pool_id = :pid
                """
            ),
            {"pid": pool_id, "patch": _json({"pending_changelog": changelog})},
        )
    await session.commit()
    return len(deduped)


async def publish(
    session,
    pool_id: str,
    *,
    actor: str = "system",
    changelog: str | None = None,
    snapshot_path: str | None = None,
) -> PoolVersion | None:
    """把 staging 提升为已发布版本。storage_mode 由成员数阈值决定。"""
    pool = await get_pool(session, pool_id)
    if pool is None:
        return None

    ver = await staging_version(session, pool_id)
    symbols = (
        await session.execute(
            text(
                "SELECT symbol FROM qm_stock_pool_member "
                "WHERE pool_id = :pid AND version = :ver ORDER BY symbol ASC"
            ),
            {"pid": pool_id, "ver": ver},
        )
    ).all()
    symbol_list = [str(r[0]) for r in symbols]

    storage_mode = (
        STORAGE_TABLE if len(symbol_list) <= MEMBER_TABLE_MAX else STORAGE_SNAPSHOT
    )
    digest = checksum_symbols(symbol_list)

    await session.execute(
        text(
            """
            INSERT INTO qm_stock_pool_version (
                pool_id, version, status, market, member_count, checksum,
                storage_mode, snapshot_path, changelog, published_by
            ) VALUES (
                :pid, :ver, :status, :market, :count, :checksum,
                :storage, :snapshot, :changelog, :actor
            )
            ON CONFLICT (pool_id, version) DO UPDATE SET
                member_count = EXCLUDED.member_count,
                checksum = EXCLUDED.checksum,
                storage_mode = EXCLUDED.storage_mode,
                snapshot_path = EXCLUDED.snapshot_path,
                changelog = EXCLUDED.changelog,
                published_by = EXCLUDED.published_by,
                published_at = NOW()
            """
        ),
        {
            "pid": pool_id,
            "ver": ver,
            "status": STATUS_PUBLISHED,
            "market": pool.market,
            "count": len(symbol_list),
            "checksum": digest,
            "storage": storage_mode,
            "snapshot": snapshot_path,
            "changelog": changelog,
            "actor": actor,
        },
    )

    await session.execute(
        text(
            """
            UPDATE qm_stock_pool
               SET current_version = :ver,
                   status = :status,
                   symbol_count = :count,
                   checksum = :checksum,
                   updated_at = NOW(),
                   updated_by = :actor
             WHERE pool_id = :pid
            """
        ),
        {
            "pid": pool_id,
            "ver": ver,
            "status": STATUS_PUBLISHED,
            "count": len(symbol_list),
            "checksum": digest,
            "actor": actor,
        },
    )
    await session.commit()
    return await get_version(session, pool_id, ver)


# ---------------------------------------------------------------------------
# 绑定 / 引用
# ---------------------------------------------------------------------------
async def list_usages(
    session, pool_id: str, *, target_type: str | None = None
) -> list[dict[str, Any]]:
    sql = """
        SELECT id, pool_id, target_type, target_id, mode, priority,
               tenant_id, user_id, created_at, updated_at
          FROM qm_stock_pool_binding
         WHERE pool_id = :pid
    """
    params: dict[str, Any] = {"pid": pool_id}
    if target_type:
        sql += " AND target_type = :ttype"
        params["ttype"] = target_type
    sql += " ORDER BY target_type, target_id"

    rows = (await session.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


async def list_pools_for_target(
    session, target_type: str, target_id: str
) -> list[dict[str, Any]]:
    """反查：某个目标（策略/模型/账户）绑了哪些池。"""
    rows = (
        (
            await session.execute(
                text(
                    """
                    SELECT b.pool_id, b.mode, b.priority, p.code, p.name,
                           p.market, p.status, p.current_version, p.symbol_count
                      FROM qm_stock_pool_binding b
                      JOIN qm_stock_pool p ON p.pool_id = b.pool_id
                     WHERE b.target_type = :ttype AND b.target_id = :tid
                     ORDER BY b.priority ASC, p.code ASC
                    """
                ),
                {"ttype": target_type, "tid": target_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def unbind_pool(
    session, pool_id: str, target_type: str, target_id: str
) -> int:
    """解除单个引用，返回删除行数。"""
    result = await session.execute(
        text(
            """
            DELETE FROM qm_stock_pool_binding
             WHERE pool_id = :pid AND target_type = :ttype AND target_id = :tid
            """
        ),
        {"pid": pool_id, "ttype": target_type, "tid": target_id},
    )
    await session.commit()
    return int(result.rowcount or 0)


async def count_bindings(session, pool_id: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM qm_stock_pool_binding WHERE pool_id = :pid"
                ),
                {"pid": pool_id},
            )
        ).scalar()
        or 0
    )


async def bind_pool(
    session,
    pool_id: str,
    target_type: str,
    target_id: str,
    *,
    mode: str = "filter",
    priority: int = 100,
    tenant_id: str | None = None,
    user_id: str | None = None,
    actor: str = "system",
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO qm_stock_pool_binding (
                pool_id, target_type, target_id, mode, priority,
                tenant_id, user_id, created_by
            ) VALUES (
                :pid, :ttype, :tid, :mode, :priority,
                :tenant_id, :user_id, :actor
            )
            ON CONFLICT (pool_id, target_type, target_id) DO UPDATE SET
                mode = EXCLUDED.mode,
                priority = EXCLUDED.priority,
                updated_at = NOW()
            """
        ),
        {
            "pid": pool_id,
            "ttype": target_type,
            "tid": target_id,
            "mode": mode,
            "priority": int(priority),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "actor": actor,
        },
    )
    await session.commit()


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _json(value: Any) -> str:
    import json

    return json.dumps(value or {}, ensure_ascii=False)


def _new_pool_id(code: str) -> str:
    import uuid

    return f"sp_{code[:24]}_{uuid.uuid4().hex[:8]}"


def _dedupe_members(members: Iterable[PoolMember], market: str) -> list[PoolMember]:
    seen: set[str] = set()
    out: list[PoolMember] = []
    for m in members:
        codes = normalize_symbols([m.symbol], market)
        if not codes:
            continue
        code = codes[0]
        if code in seen:
            continue
        seen.add(code)
        out.append(m.model_copy(update={"symbol": code, "api_symbol": None}))
    return out


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def snapshot_dir() -> Path:
    return Path(os.getenv("QM_STOCK_POOL_SNAPSHOT_DIR", "/data/stock_pool"))


def today() -> date:
    return datetime.now(timezone.utc).date()
