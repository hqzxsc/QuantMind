"""后台管理 - 全局股票池（Admin Stock Pool）。

所有功能（回测 / 训练 / 推理 / 模拟盘 / 实盘 / 因子挖掘 / Strategy Lab）
共用的股票池定义与版本管理入口。

设计：读写分离
- 本路由负责「写」：定义池、维护成员、发布版本、回滚；
- 读侧统一走 `backend.shared.stock_pool.PoolResolver`（见 /resolve 调试接口）。

路径：`/api/v1/admin/stock-pools/*`（`require_admin` 路由级兜底）。
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from backend.services.api.user_app.middleware.auth import require_admin
from backend.shared.database_manager_v2 import get_session
from backend.shared.stock_pool import builtins as sp_builtins
from backend.shared.stock_pool import constants as sp_const
from backend.shared.stock_pool import parser as sp_parser
from backend.shared.stock_pool import repository as repo
from backend.shared.stock_pool.materializer import write_instruments
from backend.shared.stock_pool.normalize import (
    is_valid_symbol,
    normalize_market,
    normalize_symbols,
    to_api_symbol,
    to_storage_symbol,
)
from backend.shared.stock_pool.resolver import ResolveContext, resolver
from backend.shared.stock_pool.schemas import (
    PoolBindingRequest,
    PoolCreateFromMembersRequest,
    PoolImportRequest,
    PoolImportResult,
    PoolMember,
    PoolMembersReplace,
    PoolParseRequest,
    StockPool,
    StockPoolCreate,
    StockPoolUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


def _actor(request: Request) -> str:
    user = getattr(request.state, "user", {}) or {}
    return str(user.get("user_id") or "admin")


async def _require_pool(session, pool_id: str) -> StockPool:
    pool = await repo.get_pool(session, pool_id)
    if pool is None:
        raise HTTPException(status_code=404, detail=f"股票池不存在: {pool_id}")
    return pool


# ---------------------------------------------------------------------------
# 元信息（必须声明在 /{pool_id} 之前）
# ---------------------------------------------------------------------------
@router.get("/meta", summary="股票池枚举与阈值元信息")
async def get_pool_meta():
    return {
        "markets": sorted(sp_const.MARKETS),
        "pool_types": sorted(sp_const.POOL_TYPES),
        "scopes": sorted(sp_const.SCOPES),
        "statuses": sorted(sp_const.STATUSES),
        "target_types": sorted(sp_const.TARGET_TYPES),
        "binding_modes": sorted(sp_const.BINDING_MODES),
        "member_table_max": sp_const.MEMBER_TABLE_MAX,
        "storage_modes": [sp_const.STORAGE_TABLE, sp_const.STORAGE_SNAPSHOT],
        "snapshot_dir": str(repo.snapshot_dir()),
        "builtin_pools": [
            {
                "code": p.code,
                "name": p.name,
                "market": p.market,
                "index_symbol": p.index_symbol,
                "optional_source": p.optional_source,
                "description": p.description,
            }
            for p in sp_builtins.BUILTIN_POOLS
        ],
    }


@router.get("/resolve", summary="解析调试：任意 ref → 解析结果")
async def debug_resolve(
    ref: str = Query(
        ..., description="池引用，如 pool:csi300 / csi300 / list:SH600036"
    ),
    market: str | None = Query(None),
    version: int | None = Query(None),
    preview_limit: int = Query(20, ge=0, le=500),
):
    """排查「池为什么是空的」的第一入口：返回来源、版本、警告与样本。"""
    snap = await resolver.resolve(
        ref, ResolveContext(market=market), version=version, strict=False
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
        "storage_mode": snap.storage_mode,
        "warnings": snap.warnings,
        "sample": snap.api_symbols[:preview_limit],
    }


@router.get("/health", summary="股票池健康检查")
async def pool_health():
    async with get_session() as session:
        await repo.ensure_tables(session)
        rows = (
            (
                await session.execute(
                    text(
                        """
                    SELECT pool_id, code, market, pool_type, status,
                           current_version, symbol_count, checksum, is_system
                      FROM qm_stock_pool
                     WHERE status <> 'archived'
                     ORDER BY is_system DESC, code ASC
                    """
                    )
                )
            )
            .mappings()
            .all()
        )

        items: list[dict[str, Any]] = []
        for row in rows:
            pool_id = str(row["pool_id"])
            has_draft = await repo.has_draft_changes(session, pool_id)
            bindings = await repo.count_bindings(session, pool_id)
            warnings: list[str] = []
            if int(row["current_version"] or 0) <= 0:
                warnings.append("尚未发布任何版本，线上不可消费")
            elif int(row["symbol_count"] or 0) == 0:
                warnings.append("已发布版本成员为空")
            if has_draft:
                warnings.append("存在未发布的草稿改动")
            if row["pool_type"] == sp_const.POOL_TYPE_SYSTEM_INDEX:
                warnings.append("成分实时取自指数权重（不落成员表）")
            elif bindings == 0:
                warnings.append(
                    "无引用登记（如需「被引用不可删」保护，请登记 binding 或跑一次引用回填）"
                )

            items.append(
                {
                    **dict(row),
                    "has_draft_changes": has_draft,
                    "binding_count": bindings,
                    "warnings": warnings,
                }
            )

    return {
        "total": len(items),
        "unhealthy": sum(1 for i in items if i["warnings"]),
        "bound_total": sum(i["binding_count"] for i in items),
        "unbound": sum(1 for i in items if i["binding_count"] == 0),
        "snapshot_dir": str(repo.snapshot_dir()),
        "items": items,
    }


# ---------------------------------------------------------------------------
# 池列表 / 新建
# ---------------------------------------------------------------------------
@router.get("", summary="股票池列表")
async def list_pools(
    market: str | None = Query(None),
    pool_type: str | None = Query(None),
    status: str | None = Query(None),
    scope: str | None = Query(None),
    keyword: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pools, total = await repo.list_pools(
            session,
            market=market,
            pool_type=pool_type,
            status=status,
            scope=scope,
            keyword=keyword,
            limit=limit,
            offset=offset,
        )
        enriched: list[dict[str, Any]] = []
        for pool in pools:
            has_draft = await repo.has_draft_changes(session, pool.pool_id)
            enriched.append({**pool.model_dump(), "has_draft_changes": has_draft})
    return {"total": total, "items": enriched, "limit": limit, "offset": offset}


@router.post("", summary="新建股票池")
async def create_pool(payload: StockPoolCreate, request: Request):
    if payload.pool_type == sp_const.POOL_TYPE_DYNAMIC:
        raise HTTPException(
            status_code=422,
            detail="dynamic（规则型动态池）一期仅预留字段，尚未开放创建",
        )
    async with get_session() as session:
        await repo.ensure_tables(session)
        existing = await repo.get_pool_by_code(
            session, payload.code, scope=payload.scope, tenant_id=payload.tenant_id
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"code 已存在: {payload.code}")
        pool = await repo.create_pool(session, payload, actor=_actor(request))
    return pool.model_dump()


# ---------------------------------------------------------------------------
# 导入 / 导出（声明在 /{pool_id} 之前）
# ---------------------------------------------------------------------------
@router.post("/import", response_model=PoolImportResult, summary="导入文件新建股票池")
async def import_new_pool(
    request: Request,
    pool_code: str = Query(..., min_length=1, max_length=64),
    pool_name: str = Query(..., min_length=1, max_length=200),
    market: str = Query("CN"),
    description: str | None = Query(None),
    publish: bool = Query(True, description="导入后是否立即发布"),
    payload: PoolImportRequest = Body(...),
):
    async with get_session() as session:
        await repo.ensure_tables(session)
        existing = await repo.get_pool_by_code(session, pool_code)
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"code 已存在: {pool_code}")

        pool = await repo.create_pool(
            session,
            StockPoolCreate(
                code=pool_code,
                name=pool_name,
                description=description,
                market=market,
                pool_type=sp_const.POOL_TYPE_IMPORTED,
                source_kind="file_import",
            ),
            actor=_actor(request),
        )
        return await _import_members(
            session, pool, payload, actor=_actor(request), publish=publish
        )


@router.post("/parse", summary="上传 CSV/TXT 解析（与 stocks_index.json 对比，不落库）")
async def parse_uploaded_pool_file(payload: PoolParseRequest):
    """股票解析：把用户上传的文件解析成规范成分清单。

    流程：原始字节 →（自动探测 UTF-8 / GBK 编码）→ 逐行全单元格提取候选
    → 与 `data/stocks/stocks_index.json` 对比 → 返回匹配报告。

    本接口**只解析不落库**，供前端展示报告、用户确认/剔除后再调用
    `POST /create-from-members` 建池。
    """
    content: str | bytes
    if payload.content_base64:
        import base64
        import binascii

        try:
            content = base64.b64decode(payload.content_base64, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail=f"content_base64 解码失败: {exc}"
            ) from exc
    elif payload.content_text:
        content = payload.content_text
    else:
        raise HTTPException(
            status_code=422, detail="content_base64 与 content_text 至少提供一个"
        )

    try:
        report = sp_parser.parse_upload(
            content,
            fmt=payload.fmt,
            filename=payload.filename,
            has_header=payload.has_header,
            column=payload.column,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("股票池文件解析失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=422, detail=f"解析失败: {exc}") from exc

    return report.as_dict(row_limit=payload.row_limit)


@router.post("/create-from-members", summary="用解析确认后的成员建池（可选立即发布）")
async def create_pool_from_members(
    payload: PoolCreateFromMembersRequest, request: Request
):
    """建池 + 落成员（+ 发布）。

    成员来自前端回传，服务端**重新校验**；名称一律以本地索引为准覆盖，
    不接受客户端伪造的名称。
    """
    if payload.pool_type == sp_const.POOL_TYPE_DYNAMIC:
        raise HTTPException(
            status_code=422, detail="dynamic（规则型动态池）一期尚未开放创建"
        )
    if not payload.members:
        raise HTTPException(status_code=422, detail="成员列表为空，无法建池")

    index = sp_parser.load_stock_index()
    market = normalize_market(payload.market)

    accepted: list[PoolMember] = []
    rejected: list[str] = []
    seen: set[str] = set()

    for item in payload.members:
        symbol = to_storage_symbol(item.symbol, market)
        if not symbol or not is_valid_symbol(symbol, market):
            rejected.append(str(item.symbol))
            continue
        if symbol in seen:
            continue
        seen.add(symbol)

        entry = index.by_symbol.get(symbol)
        accepted.append(
            PoolMember(
                symbol=symbol,
                name=(entry.name if entry else item.name),
                weight=item.weight,
                meta={**(item.meta or {}), "in_index": bool(entry)},
            )
        )

    if not accepted:
        raise HTTPException(
            status_code=422,
            detail=f"成员全部校验失败（{len(rejected)} 条），示例: {rejected[:5]}",
        )

    async with get_session() as session:
        await repo.ensure_tables(session)
        existing = await repo.get_pool_by_code(session, payload.code)
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"code 已存在: {payload.code}")

        pool = await repo.create_pool(
            session,
            StockPoolCreate(
                code=payload.code,
                name=payload.name,
                description=payload.description,
                market=market,
                pool_type=payload.pool_type,
                source_kind="file_parsed",
                source_ref="stocks_index.json",
            ),
            actor=_actor(request),
        )
        await repo.replace_members(
            session, pool.pool_id, accepted, market, actor=_actor(request)
        )

        version = None
        if payload.publish:
            snapshot_path = await _maybe_snapshot(session, pool, accepted)
            ver = await repo.publish(
                session,
                pool.pool_id,
                actor=_actor(request),
                changelog=payload.changelog or f"上传解析导入（{len(accepted)} 只）",
                snapshot_path=snapshot_path,
            )
            version = ver.version if ver else None
            _best_effort_instruments(session, pool, accepted)

    return {
        "success": True,
        "pool_id": pool.pool_id,
        "code": pool.code,
        "accepted": len(accepted),
        "rejected": len(rejected),
        "rejected_samples": rejected[:20],
        "in_index": sum(1 for m in accepted if (m.meta or {}).get("in_index")),
        "version": version,
        "published": bool(version),
    }


async def _import_members(
    session,
    pool: StockPool,
    payload: PoolImportRequest,
    *,
    actor: str,
    publish: bool,
) -> PoolImportResult:
    parsed, rejected = _parse_symbols(payload, pool.market)
    members = [PoolMember(symbol=s) for s in parsed]

    await repo.replace_members(session, pool.pool_id, members, pool.market, actor=actor)

    version = None
    if publish:
        snapshot_path = await _maybe_snapshot(session, pool, members)
        ver = await repo.publish(
            session,
            pool.pool_id,
            actor=actor,
            changelog=payload.changelog or "文件导入",
            snapshot_path=snapshot_path,
        )
        version = ver.version if ver else None
        _best_effort_instruments(session, pool, members)

    return PoolImportResult(
        total=len(parsed) + len(rejected),
        accepted=len(parsed),
        rejected=len(rejected),
        duplicates=0,
        rejected_samples=rejected[:20],
        version=version,
    )


def _parse_symbols(
    payload: PoolImportRequest, market: str
) -> tuple[list[str], list[str]]:
    """解析 csv/txt 文本 → (合法代码列表, 被拒样本)。"""
    raw: list[str] = []

    if payload.fmt == "csv":
        reader = csv.reader(io.StringIO(payload.content))
        rows = [r for r in reader if r and any(c.strip() for c in r)]
        if not rows:
            return [], []
        idx = 0
        start = 0
        if payload.has_header:
            header = [c.strip().lower() for c in rows[0]]
            candidates = [
                payload.symbol_column,
                "symbol",
                "code",
                "证券代码",
                "代码",
                "ticker",
            ]
            for cand in candidates:
                if cand and cand.lower() in header:
                    idx = header.index(cand.lower())
                    break
            start = 1
        for r in rows[start:]:
            if idx < len(r):
                raw.append(r[idx].strip())
    else:
        for line in payload.content.splitlines():
            parts = line.strip().split("\t")
            if parts and parts[0] and not parts[0].startswith("#"):
                raw.append(parts[0].strip())

    rejected: list[str] = []
    accepted: list[str] = []
    for item in raw:
        if not item:
            continue
        if not is_valid_symbol(item, market):
            rejected.append(item)
            continue
        accepted.append(item)

    return normalize_symbols(accepted, market), rejected


async def _maybe_snapshot(
    session, pool: StockPool, members: list[PoolMember]
) -> str | None:
    """成员数超阈值时落 parquet 快照，返回路径。"""
    if len(members) <= sp_const.MEMBER_TABLE_MAX:
        return None
    from backend.shared.stock_pool.materializer import write_snapshot

    version = await repo.staging_version(session, pool.pool_id)
    try:
        return write_snapshot(pool.pool_id, version, members)
    except Exception as exc:  # noqa: BLE001
        logger.error("股票池快照写入失败 pool=%s: %s", pool.pool_id, exc)
        raise HTTPException(
            status_code=500,
            detail=f"成员数 {len(members)} 超过阈值 {sp_const.MEMBER_TABLE_MAX}，"
            f"但快照写入失败: {exc}",
        ) from exc


def _best_effort_instruments(
    session, pool: StockPool, members: list[PoolMember]
) -> None:
    """物化 Qlib instruments 文件。失败只告警，不影响发布。"""
    try:
        write_instruments(pool.code, [m.symbol for m in members], pool.market)
    except Exception as exc:  # noqa: BLE001
        logger.warning("股票池 instruments 物化失败 pool=%s: %s", pool.code, exc)


# ---------------------------------------------------------------------------
# 详情 / 更新 / 删除
# ---------------------------------------------------------------------------
@router.get("/{pool_id}", summary="股票池详情")
async def get_pool_detail(pool_id: str):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        has_draft = await repo.has_draft_changes(session, pool_id)
        versions = await repo.list_versions(session, pool_id, limit=20)
        staging = await repo.staging_version(session, pool_id)
    return {
        **pool.model_dump(),
        "has_draft_changes": has_draft,
        "staging_version": staging,
        "versions": [v.model_dump() for v in versions],
    }


@router.patch("/{pool_id}", summary="更新股票池元信息")
async def update_pool(pool_id: str, payload: StockPoolUpdate, request: Request):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        if pool.is_system and payload.status == sp_const.STATUS_ARCHIVED:
            raise HTTPException(status_code=409, detail="系统内置池不允许归档")
        updated = await repo.update_pool(
            session, pool_id, payload, actor=_actor(request)
        )
    assert updated is not None
    return updated.model_dump()


@router.post("/{pool_id}/archive", summary="归档股票池（被引用时拒绝）")
async def archive_pool(pool_id: str, request: Request):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        if pool.is_system:
            raise HTTPException(status_code=409, detail="系统内置池不允许归档")
        blockers = await repo.archive_pool(session, pool_id, actor=_actor(request))
    if blockers:
        raise HTTPException(
            status_code=409,
            detail=f"该池仍被引用，无法归档: {', '.join(blockers)}",
        )
    return {"success": True, "pool_id": pool_id, "status": sp_const.STATUS_ARCHIVED}


@router.delete("/{pool_id}", summary="删除股票池（仅限已归档 / 无引用）")
async def delete_pool(pool_id: str):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        if pool.is_system:
            raise HTTPException(status_code=409, detail="系统内置池不允许删除")
        usages = await repo.list_usages(session, pool_id)
        if usages:
            raise HTTPException(
                status_code=409,
                detail=(
                    "该池仍被引用，无法删除: "
                    + ", ".join(f"{u['target_type']}:{u['target_id']}" for u in usages)
                ),
            )
        await repo.delete_pool(session, pool_id)
    return {"success": True, "pool_id": pool_id}


# ---------------------------------------------------------------------------
# 成员
# ---------------------------------------------------------------------------
@router.get("/{pool_id}/members", summary="成员列表（默认草稿版本）")
async def list_members(
    pool_id: str,
    scope: str = Query("draft", pattern="^(draft|published)$"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)

        if scope == "published":
            if pool.current_version <= 0:
                return {"total": 0, "items": [], "version": 0, "scope": scope}
            version = pool.current_version
            members, total = await repo.list_members(
                session,
                pool_id,
                pool.market,
                version=version,
                limit=limit,
                offset=offset,
            )
        else:
            version = await repo.staging_version(session, pool_id)
            members, total = await repo.list_members(
                session,
                pool_id,
                pool.market,
                version=version,
                limit=limit,
                offset=offset,
            )

    return {
        "total": total,
        "items": [m.model_dump() for m in members],
        "version": version,
        "scope": scope,
        "limit": limit,
        "offset": offset,
    }


@router.put("/{pool_id}/members", summary="整体覆盖草稿成员")
async def replace_members(pool_id: str, payload: PoolMembersReplace, request: Request):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        if pool.status == sp_const.STATUS_ARCHIVED:
            raise HTTPException(status_code=409, detail="已归档的池不可编辑")

        accepted: list[PoolMember] = []
        rejected: list[str] = []
        for m in payload.members:
            if not is_valid_symbol(m.symbol, pool.market):
                rejected.append(m.symbol)
                continue
            accepted.append(m)

        count = await repo.replace_members(
            session,
            pool_id,
            accepted,
            pool.market,
            changelog=payload.changelog,
            actor=_actor(request),
        )
        staging = await repo.staging_version(session, pool_id)

    return {
        "success": True,
        "accepted": count,
        "rejected": len(rejected),
        "rejected_samples": rejected[:20],
        "staging_version": staging,
    }


@router.post(
    "/{pool_id}/members/import",
    response_model=PoolImportResult,
    summary="向已有池导入成员（覆盖草稿）",
)
async def import_members(
    pool_id: str,
    request: Request,
    publish: bool = Query(True),
    payload: PoolImportRequest = Body(...),
):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        if pool.status == sp_const.STATUS_ARCHIVED:
            raise HTTPException(status_code=409, detail="已归档的池不可编辑")
        return await _import_members(
            session, pool, payload, actor=_actor(request), publish=publish
        )


@router.get(
    "/{pool_id}/members/export",
    summary="导出成员为 CSV",
    response_class=PlainTextResponse,
)
async def export_members(
    pool_id: str,
    scope: str = Query("published", pattern="^(draft|published)$"),
):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        if scope == "published":
            version = pool.current_version or await repo.staging_version(
                session, pool_id
            )
        else:
            version = await repo.staging_version(session, pool_id)
        members, _ = await repo.list_members(
            session, pool_id, pool.market, version=version
        )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["symbol", "name", "weight", "industry"])
    for m in members:
        writer.writerow(
            [m.api_symbol or m.symbol, m.name or "", m.weight or "", m.industry or ""]
        )

    filename = f"{pool.code}_v{version}.csv"
    return PlainTextResponse(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{pool_id}/preview", summary="预览成分（含最新行情指标）")
async def preview_pool(
    pool_id: str,
    scope: str = Query("published", pattern="^(draft|published)$"),
    limit: int = Query(200, ge=1, le=2000),
):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        version = (
            pool.current_version
            if scope == "published" and pool.current_version > 0
            else await repo.staging_version(session, pool_id)
        )
        members, total = await repo.list_members(
            session, pool_id, pool.market, version=version, limit=limit
        )
        metrics = await _fetch_latest_metrics(
            session, [m.symbol for m in members], pool.market
        )

    items = []
    for m in members:
        api_symbol = m.api_symbol or to_api_symbol(m.symbol, pool.market)
        items.append({**m.model_dump(), "metrics": metrics.get(api_symbol, {})})

    return {
        "pool_id": pool_id,
        "code": pool.code,
        "version": version,
        "total": total,
        "items": items,
        "metrics_available": bool(metrics),
    }


async def _fetch_latest_metrics(
    session, symbols: list[str], market: str
) -> dict[str, dict]:
    """尽力取最新行情指标；表/列缺失时返回空（不阻断预览）。"""
    if not symbols or normalize_market(market) != sp_const.MARKET_CN:
        return {}
    api_symbols = [to_api_symbol(s, market) for s in symbols[:1000]]
    try:
        rows = (
            (
                await session.execute(
                    text(
                        """
                    SELECT symbol, stock_name, close, pe_ttm, pb, roe,
                           total_mv, amount, volume, pct_change, trade_date
                      FROM stock_daily_latest
                     WHERE symbol = ANY(:codes)
                    """
                    ),
                    {"codes": api_symbols},
                )
            )
            .mappings()
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("股票池预览取行情指标失败（忽略）: %s", exc)
        return {}

    out: dict[str, dict] = {}
    for row in rows:
        out[str(row["symbol"])] = {
            "name": row["stock_name"],
            "close": row["close"],
            "pe_ttm": row["pe_ttm"],
            "pb": row["pb"],
            "roe": row["roe"],
            "total_mv": row["total_mv"],
            "amount": row["amount"],
            "volume": row["volume"],
            "pct_change": row["pct_change"],
            "trade_date": str(row["trade_date"]) if row["trade_date"] else None,
        }
    return out


# ---------------------------------------------------------------------------
# 版本：发布 / 回滚 / 列表 / diff
# ---------------------------------------------------------------------------
@router.post("/{pool_id}/publish", summary="发布草稿为新版本")
async def publish_pool(
    pool_id: str,
    request: Request,
    changelog: str | None = Query(None),
):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        if pool.status == sp_const.STATUS_ARCHIVED:
            raise HTTPException(status_code=409, detail="已归档的池不可发布")

        staging = await repo.staging_version(session, pool_id)
        members, _ = await repo.list_members(
            session, pool_id, pool.market, version=staging
        )
        if not members:
            raise HTTPException(
                status_code=422,
                detail=(
                    "草稿成员为空，拒绝发布。"
                    "（系统指数池的成分由 QuantDB 实时提供，无需在此维护成员）"
                ),
            )

        snapshot_path = await _maybe_snapshot(session, pool, members)
        version = await repo.publish(
            session,
            pool_id,
            actor=_actor(request),
            changelog=changelog,
            snapshot_path=snapshot_path,
        )
        _best_effort_instruments(session, pool, members)

    assert version is not None
    return version.model_dump()


@router.get("/{pool_id}/versions", summary="版本列表")
async def list_versions(pool_id: str, limit: int = Query(50, ge=1, le=200)):
    async with get_session() as session:
        await repo.ensure_tables(session)
        await _require_pool(session, pool_id)
        versions = await repo.list_versions(session, pool_id, limit=limit)
    return {"total": len(versions), "items": [v.model_dump() for v in versions]}


@router.post("/{pool_id}/rollback", summary="回滚到指定版本（生成新版本，不破坏历史）")
async def rollback_pool(
    pool_id: str,
    request: Request,
    version: int = Query(..., ge=1),
    changelog: str | None = Query(None),
):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)

        target = await repo.get_version(session, pool_id, version)
        if target is None:
            raise HTTPException(status_code=404, detail=f"版本 v{version} 不存在")
        if target.storage_mode == sp_const.STORAGE_SNAPSHOT and target.snapshot_path:
            from backend.shared.stock_pool.materializer import read_snapshot

            members = read_snapshot(target.snapshot_path)
        else:
            symbols = await repo.list_published_symbols(session, pool_id, version)
            members = [PoolMember(symbol=s) for s in symbols]

        if not members:
            raise HTTPException(
                status_code=422, detail=f"版本 v{version} 成员为空，无法回滚"
            )

        await repo.replace_members(
            session,
            pool_id,
            members,
            pool.market,
            changelog=changelog or f"回滚到 v{version}",
            actor=_actor(request),
        )
        snapshot_path = await _maybe_snapshot(session, pool, members)
        new_version = await repo.publish(
            session,
            pool_id,
            actor=_actor(request),
            changelog=changelog or f"回滚到 v{version}",
            snapshot_path=snapshot_path,
        )
        _best_effort_instruments(session, pool, members)

    return {
        "success": True,
        "rolled_back_from": version,
        "new_version": new_version.version if new_version else None,
    }


@router.get("/{pool_id}/diff", summary="两个版本的成员差异")
async def diff_versions(
    pool_id: str,
    from_version: int = Query(..., alias="from", ge=1),
    to_version: int = Query(..., alias="to", ge=1),
    limit: int = Query(500, ge=1, le=5000),
):
    async with get_session() as session:
        await repo.ensure_tables(session)
        await _require_pool(session, pool_id)
        left, right = await _symbols_of_versions(
            session, pool_id, from_version, to_version
        )

    from_set, to_set = set(left), set(right)
    added = sorted(to_set - from_set)
    removed = sorted(from_set - to_set)
    return {
        "from": from_version,
        "to": to_version,
        "from_count": len(left),
        "to_count": len(right),
        "added_count": len(added),
        "removed_count": len(removed),
        "added": added[:limit],
        "removed": removed[:limit],
    }


async def _symbols_of_versions(
    session, pool_id: str, a: int, b: int
) -> tuple[list[str], list[str]]:
    out: list[str] = []
    for version in (a, b):
        ver = await repo.get_version(session, pool_id, version)
        if ver is None:
            raise HTTPException(status_code=404, detail=f"版本 v{version} 不存在")
        if ver.storage_mode == sp_const.STORAGE_SNAPSHOT and ver.snapshot_path:
            from backend.shared.stock_pool.materializer import read_snapshot

            out.append([m.symbol for m in read_snapshot(ver.snapshot_path)])
        else:
            out.append(await repo.list_published_symbols(session, pool_id, version))
    return out[0], out[1]


@router.get("/{pool_id}/usages", summary="引用情况（策略 / 回测 / 模型 / 账户）")
async def list_usages(pool_id: str, target_type: str | None = Query(None)):
    async with get_session() as session:
        await repo.ensure_tables(session)
        await _require_pool(session, pool_id)
        usages = await repo.list_usages(session, pool_id, target_type=target_type)
    return {"total": len(usages), "items": usages}


# ---------------------------------------------------------------------------
# 引用登记（P4）：没有写入口时 qm_stock_pool_binding 永远是空的，
# 「被引用不可删」的守卫就是空转 —— 这一节让它真正生效。
#
# 语义约定：binding 只登记**长生命周期**引用（策略 / 模型 / 模拟盘账户 / 实盘配置 /
# 因子），这类引用应当阻止删除。回测与推理是**一次性运行**，其可复现信息已随
# `pool_version` / `pool_checksum` 落在各自的结果记录里，不登记为 binding，
# 否则历史回测会让池永不可删，且绑定表随运行次数无界增长。
# ---------------------------------------------------------------------------
@router.post("/{pool_id}/bindings", summary="登记引用（策略/模型/账户等长生命周期绑定）")
async def bind_pool(
    pool_id: str,
    request: Request,
    payload: PoolBindingRequest = Body(...),
):
    if payload.target_type not in sp_const.TARGET_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"target_type 非法: {payload.target_type}；可选 {sorted(sp_const.TARGET_TYPES)}",
        )
    if payload.mode not in sp_const.BINDING_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"mode 非法: {payload.mode}；可选 {sorted(sp_const.BINDING_MODES)}",
        )

    async with get_session() as session:
        await repo.ensure_tables(session)
        await _require_pool(session, pool_id)
        await repo.bind_pool(
            session,
            pool_id,
            payload.target_type,
            payload.target_id,
            mode=payload.mode,
            priority=payload.priority,
            tenant_id=payload.tenant_id,
            user_id=payload.user_id,
            actor=_actor(request),
        )
        usages = await repo.list_usages(session, pool_id)
    return {"success": True, "total": len(usages), "items": usages}


@router.delete("/{pool_id}/bindings/{target_type}/{target_id:path}", summary="解除引用")
async def unbind_pool(pool_id: str, target_type: str, target_id: str):
    if target_type not in sp_const.TARGET_TYPES:
        raise HTTPException(status_code=422, detail=f"target_type 非法: {target_type}")
    async with get_session() as session:
        await repo.ensure_tables(session)
        await _require_pool(session, pool_id)
        removed = await repo.unbind_pool(session, pool_id, target_type, target_id)
        remaining = await repo.list_usages(session, pool_id)
    return {"success": True, "removed": removed, "total": len(remaining)}


@router.get("/bindings/by-target", summary="反查：某个策略/模型/账户绑了哪些池")
async def list_bindings_for_target(
    target_type: str = Query(...),
    target_id: str = Query(...),
):
    if target_type not in sp_const.TARGET_TYPES:
        raise HTTPException(status_code=422, detail=f"target_type 非法: {target_type}")
    async with get_session() as session:
        await repo.ensure_tables(session)
        items = await repo.list_pools_for_target(session, target_type, target_id)
    return {"total": len(items), "items": items}


@router.post("/bindings/reconcile", summary="从既有模型回填引用（让守卫对存量数据生效）")
async def reconcile_bindings(
    request: Request,
    dry_run: bool = Query(False, description="只统计不写入"),
):
    """扫描 `qm_user_models.metadata_json` 里记录的池，回填 model 类型 binding。

    为什么需要它：binding 表原本没有任何写入口（`/usages` 恒为空，
    「被引用不可删」的守卫是空转的）。P3 之后训练产物会在 metadata 里记
    `pool_id`，因此可以反推回填，无需改动所有写路径。

    只处理长生命周期引用（model）；回测/推理是一次性运行，不入 binding。
    """
    async with get_session() as session:
        await repo.ensure_tables(session)

        try:
            rows = (
                (
                    await session.execute(
                        text(
                            """
                            SELECT tenant_id, user_id, model_id,
                                   metadata_json ->> 'pool_id' AS pool_id,
                                   metadata_json ->> 'pool_version' AS pool_version,
                                   metadata_json ->> 'pool_checksum' AS pool_checksum
                              FROM qm_user_models
                             WHERE metadata_json ->> 'pool_id' IS NOT NULL
                               AND metadata_json ->> 'pool_id' <> ''
                            """
                        )
                    )
                )
                .mappings()
                .all()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("引用回填扫描失败（表结构可能未就绪）: %s", exc)
            raise HTTPException(
                status_code=503, detail=f"扫描 qm_user_models 失败: {exc}"
            ) from exc

        known = {
            str(r["pool_id"]): str(r["code"])
            for r in (
                await session.execute(
                    text("SELECT pool_id, code FROM qm_stock_pool")
                )
            ).mappings().all()
        }

        created: list[dict[str, Any]] = []
        missing: list[str] = []
        for row in rows:
            pool_id = str(row["pool_id"])
            if pool_id not in known:
                # metadata 里记的是 code（如 pool:csi300）或已被删除 → 跳过并报告
                missing.append(pool_id)
                continue
            item = {
                "pool_id": pool_id,
                "target_type": sp_const.TARGET_TRAINING,
                "target_id": str(row["model_id"]),
                "tenant_id": row["tenant_id"],
                "user_id": row["user_id"],
            }
            created.append(item)
            if not dry_run:
                await repo.bind_pool(
                    session,
                    pool_id,
                    sp_const.TARGET_TRAINING,
                    str(row["model_id"]),
                    mode=sp_const.BINDING_MODE_FILTER,
                    tenant_id=row["tenant_id"],
                    user_id=row["user_id"],
                    actor=_actor(request),
                )

    return {
        "dry_run": dry_run,
        "scanned": len(rows),
        "bound": len(created),
        "unresolved": len(missing),
        "unresolved_samples": sorted(set(missing))[:20],
        "items": created[:200],
    }
