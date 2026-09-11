"""全局股票池 - 用户态只读接口（供各功能下拉选择）。

与后台管理 `/api/v1/admin/stock-pools` 的分工：
- 后台管理负责「写」（定义 / 成员 / 版本 / 发布）；
- 本路由只负责「读」，是回测 / 训练 / 推理 / 模拟盘 / 实盘 / 因子挖掘
  各功能页面选择股票池的统一数据源。

可见性：`scope=global` 全平台可见；`tenant` / `user` 需身份匹配；
`archived` 一律不可见。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

from backend.services.engine.auth_context import get_authenticated_identity
from backend.shared.database_manager_v2 import get_session
from backend.shared.stock_pool import constants as sp_const
from backend.shared.stock_pool import repository as repo
from backend.shared.stock_pool.resolver import ResolveContext, resolver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stock-pools", tags=["Stock Pool"])

_VISIBILITY_CLAUSE = """
    status <> 'archived'
    AND (
        scope = 'global'
        OR (scope = 'tenant' AND tenant_id = :tenant_id)
        OR (scope = 'user' AND owner_user_id = :user_id)
    )
"""


@router.get("/options", summary="股票池下拉选项（轻量）")
async def list_pool_options(
    request: Request,
    market: str | None = Query(None),
    pool_type: str | None = Query(None),
    include_system: bool = Query(True),
):
    """给前端选择器用的精简列表：不含成员明细。"""
    user_id, tenant_id = get_authenticated_identity(request)

    where = [_VISIBILITY_CLAUSE]
    params: dict[str, object] = {"tenant_id": tenant_id, "user_id": user_id}
    if market:
        where.append("market = :market")
        params["market"] = market.upper()
    if pool_type:
        where.append("pool_type = :pool_type")
        params["pool_type"] = pool_type
    if not include_system:
        where.append("is_system = FALSE")

    async with get_session() as session:
        await repo.ensure_tables(session)
        rows = (
            (
                await session.execute(
                    text(
                        f"""
                    SELECT pool_id, code, name, description, market, pool_type,
                           scope, status, current_version, symbol_count,
                           checksum, is_system
                      FROM qm_stock_pool
                     WHERE {" AND ".join(where)}
                     ORDER BY is_system DESC, market ASC, code ASC
                    """
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )

    return {
        "total": len(rows),
        "items": [dict(r) for r in rows],
        "member_table_max": sp_const.MEMBER_TABLE_MAX,
    }


@router.get("", summary="股票池列表")
async def list_pools(
    request: Request,
    market: str | None = Query(None),
    pool_type: str | None = Query(None),
    keyword: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    user_id, tenant_id = get_authenticated_identity(request)

    where = [_VISIBILITY_CLAUSE]
    params: dict[str, object] = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "limit": int(limit),
        "offset": int(offset),
    }
    if market:
        where.append("market = :market")
        params["market"] = market.upper()
    if pool_type:
        where.append("pool_type = :pool_type")
        params["pool_type"] = pool_type
    if keyword:
        where.append("(code ILIKE :kw OR name ILIKE :kw)")
        params["kw"] = f"%{keyword}%"

    clause = " AND ".join(where)

    async with get_session() as session:
        await repo.ensure_tables(session)
        total = (
            await session.execute(
                text(f"SELECT COUNT(*) FROM qm_stock_pool WHERE {clause}"), params
            )
        ).scalar() or 0
        rows = (
            (
                await session.execute(
                    text(
                        f"""
                    SELECT pool_id, code, name, description, market, pool_type,
                           scope, status, current_version, symbol_count,
                           checksum, is_system, updated_at
                      FROM qm_stock_pool
                     WHERE {clause}
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

    return {
        "total": int(total),
        "items": [dict(r) for r in rows],
        "limit": limit,
        "offset": offset,
    }


@router.get("/resolve", summary="解析池引用（供功能页联调 / 排障）")
async def resolve_ref(
    request: Request,
    ref: str = Query(..., description="pool:csi300 / csi300 / list:SH600036 / all"),
    market: str | None = Query(None),
    version: int | None = Query(None),
    preview_limit: int = Query(20, ge=0, le=500),
):
    _user_id, tenant_id = get_authenticated_identity(request)

    snap = await resolver.resolve(
        ref,
        ResolveContext(tenant_id=tenant_id, market=market),
        version=version,
        strict=False,
    )
    return {
        "ref": ref,
        "pool_id": snap.pool_id,
        "code": snap.code,
        "version": snap.version,
        "market": snap.market,
        "source": snap.source,
        "unfiltered": snap.unfiltered,
        "symbol_count": len(snap.symbols),
        "checksum": snap.checksum,
        "warnings": snap.warnings,
        "sample": snap.api_symbols[:preview_limit],
    }


@router.get("/{pool_id}", summary="股票池详情")
async def get_pool(request: Request, pool_id: str):
    user_id, tenant_id = get_authenticated_identity(request)

    async with get_session() as session:
        await repo.ensure_tables(session)
        row = (
            (
                await session.execute(
                    text(
                        f"""
                    SELECT pool_id, code, name, description, market, pool_type,
                           scope, status, current_version, symbol_count,
                           checksum, is_system, definition, refresh_policy,
                           created_at, updated_at
                      FROM qm_stock_pool
                     WHERE pool_id = :pid AND {_VISIBILITY_CLAUSE}
                    """
                    ),
                    {"pid": pool_id, "tenant_id": tenant_id, "user_id": user_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"股票池不存在或无权访问: {pool_id}"
            )
        versions = await repo.list_versions(session, pool_id, limit=10)

    return {**dict(row), "versions": [v.model_dump() for v in versions]}


@router.get("/{pool_id}/members", summary="股票池成员（已发布版本）")
async def list_members(
    request: Request,
    pool_id: str,
    version: int | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    user_id, tenant_id = get_authenticated_identity(request)

    async with get_session() as session:
        await repo.ensure_tables(session)
        row = (
            (
                await session.execute(
                    text(
                        f"""
                    SELECT market, current_version
                      FROM qm_stock_pool
                     WHERE pool_id = :pid AND {_VISIBILITY_CLAUSE}
                    """
                    ),
                    {"pid": pool_id, "tenant_id": tenant_id, "user_id": user_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"股票池不存在或无权访问: {pool_id}"
            )

        target_version = (
            int(version) if version is not None else int(row["current_version"] or 0)
        )
        if target_version <= 0:
            return {
                "pool_id": pool_id,
                "version": 0,
                "total": 0,
                "items": [],
                "warning": "该池尚未发布任何版本；系统指数池的成分请用 /resolve 获取",
            }

        members, total = await repo.list_members(
            session,
            pool_id,
            str(row["market"] or "CN"),
            version=target_version,
            limit=limit,
            offset=offset,
        )

    return {
        "pool_id": pool_id,
        "version": target_version,
        "total": total,
        "items": [m.model_dump() for m in members],
        "limit": limit,
        "offset": offset,
    }
