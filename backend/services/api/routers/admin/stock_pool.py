"""后台管理 - 全局股票池（Admin Stock Pool, v2 简化版）。

成员唯一事实源 = 前缀式 TXT（/data/stock_pool/<code>.txt，一行一个代码），
编辑保存 → 重写 TXT → 立即生效，没有草稿/发布版本模型。
PG 单表只存元信息；binding 表记录长生命周期引用（被引用的池不可删）。

设计：读写分离
- 本路由负责「写」：建池 / 改元信息 / 覆盖成员 / 导入 / 刷新内置池；
- 读侧统一走 `backend.shared.stock_pool.PoolResolver`（见 /resolve 调试接口）。

路径：`/api/v1/admin/stock-pools/*`（`require_admin` 路由级兜底）。
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
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
    PoolMembersSave,
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


def _members_payload(pool: StockPool) -> dict[str, Any]:
    """读 TXT → API 响应（成员就是文件内容，保存即可见）。"""
    api_symbols = repo.read_members(pool)
    return {
        "pool_id": pool.pool_id,
        "code": pool.code,
        "market": pool.market,
        "file_path": pool.file_path,
        "total": len(api_symbols),
        "checksum": pool.checksum,
        "symbols": api_symbols,
        "updated_at": pool.updated_at.isoformat() if pool.updated_at else None,
    }


# ---------------------------------------------------------------------------
# 元信息（必须声明在 /{pool_id} 之前）
# ---------------------------------------------------------------------------
@router.get("/meta", summary="股票池枚举与阈值元信息")
async def get_pool_meta():
    from backend.shared.stock_pool.materializer import pool_dir

    return {
        "markets": sorted(sp_const.MARKETS),
        "pool_types": sorted(sp_const.POOL_TYPES),
        "scopes": sorted(sp_const.SCOPES),
        "statuses": sorted(sp_const.STATUSES),
        "target_types": sorted(sp_const.TARGET_TYPES),
        "binding_modes": sorted(sp_const.BINDING_MODES),
        "member_max": sp_const.MEMBER_MAX,
        "pool_txt_dir": str(pool_dir()),
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
        ..., description="池引用，如 pool:csi300 / csi300 / list:SH600036 / all"
    ),
    market: str | None = Query(None),
    preview_limit: int = Query(20, ge=0, le=500),
):
    """排查「池为什么是空的」的第一入口：返回来源、成员数、警告与样本。"""
    snap = await resolver.resolve(ref, ResolveContext(market=market), strict=False)
    return {
        "ref": ref,
        "pool_id": snap.pool_id,
        "code": snap.code,
        "market": snap.market,
        "source": snap.source,
        "unfiltered": snap.unfiltered,
        "symbol_count": len(snap.symbols),
        "checksum": snap.checksum,
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
                    SELECT pool_id, code, market, pool_type, scope, status,
                           file_path, symbol_count, checksum, is_system
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
            warnings: list[str] = []
            file_path = str(row.get("file_path") or "")
            if not file_path:
                warnings.append("成员 TXT 尚未生成（编辑保存或刷新内置池后生成）")
            elif not Path(file_path).exists():
                warnings.append(f"成员 TXT 文件缺失: {file_path}")
            elif int(row["symbol_count"] or 0) == 0:
                warnings.append("成员 TXT 为空")
            bindings = await repo.count_bindings(session, str(row["pool_id"]))
            if not row["is_system"] and bindings == 0:
                warnings.append(
                    "无引用登记（如需「被引用不可删」保护，请登记 binding 或跑引用回填）"
                )
            items.append({**dict(row), "binding_count": bindings, "warnings": warnings})

    return {
        "total": len(items),
        "unhealthy": sum(1 for i in items if i["warnings"]),
        "bound_total": sum(i["binding_count"] for i in items),
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
    return {
        "total": total,
        "items": [p.model_dump() for p in pools],
        "limit": limit,
        "offset": offset,
    }


@router.post("", summary="新建股票池")
async def create_pool(payload: StockPoolCreate, request: Request):
    if payload.pool_type not in sp_const.POOL_TYPES:
        raise HTTPException(status_code=422, detail=f"未知池类型: {payload.pool_type}")
    if payload.scope != "global":
        raise HTTPException(
            status_code=422,
            detail="后台管理仅创建全局池；用户私有池由旧池文件登记桥自动创建",
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
# 上传解析（声明在 /{pool_id} 之前）
# ---------------------------------------------------------------------------
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


@router.post("/create-from-members", summary="用解析确认后的成员建池（保存即生效）")
async def create_pool_from_members(
    payload: PoolCreateFromMembersRequest, request: Request
):
    """建池 + 写成员 TXT，一次完成，立即可被 resolver / 回测消费。

    成员代码由前端回传，服务端**重新校验**（不信任客户端），坏代码拒绝并回传样本。
    """
    if not payload.symbols:
        raise HTTPException(status_code=422, detail="成员列表为空，无法建池")

    market = normalize_market(payload.market)
    valid: list[str] = []
    rejected: list[str] = []
    for item in payload.symbols:
        code = to_storage_symbol(str(item or ""), market)
        if code and is_valid_symbol(code, market):
            valid.append(code)
        else:
            rejected.append(str(item)[:32])
    valid = normalize_symbols(valid, market)
    if not valid:
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
        result = await repo.save_members(session, pool, valid, actor=_actor(request))
        pool = await _require_pool(session, pool.pool_id)

    return {
        "success": True,
        "pool": pool.model_dump(),
        "accepted": result["accepted"],
        "rejected": result["rejected"],
        "duplicates": result["duplicates"],
        "rejected_samples": result["rejected_samples"],
        "checksum": result["checksum"],
        "file_path": result["file_path"],
    }


# ---------------------------------------------------------------------------
# 详情 / 更新 / 归档 / 删除
# ---------------------------------------------------------------------------
@router.get("/{pool_id}", summary="股票池详情")
async def get_pool_detail(pool_id: str):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        usages = await repo.list_usages(session, pool_id)
    return {**pool.model_dump(), "binding_count": len(usages)}


@router.patch("/{pool_id}", summary="更新股票池元信息")
async def update_pool(pool_id: str, payload: StockPoolUpdate, request: Request):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        if pool.is_system and payload.status is not None:
            raise HTTPException(status_code=409, detail="内置系统池不可改状态")
        updated = await repo.update_pool(session, pool_id, payload, actor=_actor(request))
    if updated is None:
        raise HTTPException(status_code=404, detail=f"股票池不存在: {pool_id}")
    return updated.model_dump()


@router.post("/{pool_id}/archive", summary="归档股票池（被引用时拒绝）")
async def archive_pool(pool_id: str, request: Request):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        if pool.is_system:
            raise HTTPException(status_code=409, detail="内置系统池不可归档")
        blockers = await repo.archive_pool(session, pool_id, actor=_actor(request))
    if blockers:
        raise HTTPException(
            status_code=409,
            detail=f"该池仍被 {len(blockers)} 处引用，不可归档: {blockers[:10]}",
        )
    return {"success": True, "pool_id": pool_id, "status": sp_const.STATUS_ARCHIVED}


@router.delete("/{pool_id}", summary="删除股票池（仅限已归档 / 无引用）")
async def delete_pool(pool_id: str):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        if pool.is_system:
            raise HTTPException(status_code=409, detail="内置系统池不可删除")
        if pool.status != sp_const.STATUS_ARCHIVED:
            raise HTTPException(
                status_code=409, detail="仅允许删除已归档的池（先归档再删除）"
            )
        bindings = await repo.count_bindings(session, pool_id)
        if bindings:
            raise HTTPException(status_code=409, detail=f"仍被 {bindings} 处引用")
        await repo.delete_pool(session, pool_id)
    return {"success": True, "pool_id": pool_id}


# ---------------------------------------------------------------------------
# 成员（TXT 即事实源，保存即生效）
# ---------------------------------------------------------------------------
@router.get("/{pool_id}/members", summary="成员列表（读 TXT，前缀式）")
async def get_members(pool_id: str):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
    return _members_payload(pool)


@router.put("/{pool_id}/members", summary="整体覆盖成员（写 TXT，立即生效）")
async def save_members(pool_id: str, payload: PoolMembersSave, request: Request):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        if pool.is_system:
            raise HTTPException(
                status_code=409,
                detail="内置系统池成分由指数权重自动刷新，不可手改；"
                "如需自定义请新建 imported 池",
            )
        raw = list(payload.symbols or [])
        if not raw and payload.text:
            raw = [
                line.split(",")[0].split(";")[0].strip()
                for line in payload.text.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        if not raw:
            raise HTTPException(status_code=422, detail="symbols 与 text 至少提供一个")
        if len(raw) > sp_const.MEMBER_MAX:
            raise HTTPException(
                status_code=422,
                detail=f"成员数 {len(raw)} 超过上限 {sp_const.MEMBER_MAX}",
            )
        result = await repo.save_members(session, pool, raw, actor=_actor(request))
        pool = await _require_pool(session, pool_id)
    return {"success": True, **result, "pool": pool.model_dump()}


@router.post("/{pool_id}/members/import", summary="向已有池导入文本（覆盖成员）")
async def import_members(
    pool_id: str,
    request: Request,
    payload: dict = Body(..., description='{"content": "csv/txt 原始文本", "fmt": "csv|txt"}'),
):
    content = str(payload.get("content") or "")
    fmt = str(payload.get("fmt") or "txt").lower()
    if not content.strip():
        raise HTTPException(status_code=422, detail="content 为空")

    raw: list[str] = []
    if fmt == "csv":
        reader = csv.reader(io.StringIO(content))
        rows = [r for r in reader if r and any(c.strip() for c in r)]
        idx, start = 0, 0
        if rows:
            header = [c.strip().lower() for c in rows[0]]
            for cand in ("symbol", "code", "证券代码", "代码", "ticker"):
                if cand in header:
                    idx = header.index(cand)
                    start = 1
                    break
        for r in rows[start:]:
            if idx < len(r):
                raw.append(r[idx].strip())
    else:
        for line in content.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                raw.append(s.split(",")[0].split("\t")[0].strip())

    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        if pool.is_system:
            raise HTTPException(status_code=409, detail="内置系统池不可导入覆盖")
        result = await repo.save_members(session, pool, raw, actor=_actor(request))
    return {"success": True, **result}


@router.get("/{pool_id}/export", summary="导出成员（text/plain，一行一个前缀式代码）")
async def export_members(pool_id: str):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
    data = _members_payload(pool)
    body = "\n".join(data["symbols"]) + ("\n" if data["symbols"] else "")
    return PlainTextResponse(
        content=body,
        headers={
            "Content-Disposition": f'attachment; filename="pool_{pool.code}.txt"'
        },
    )


@router.post("/{pool_id}/refresh", summary="刷新内置池成分（从 QuantDB 重写 TXT）")
async def refresh_builtin_pool(pool_id: str, request: Request):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        if not pool.is_system:
            raise HTTPException(status_code=409, detail="仅内置系统池支持自动刷新")
        builtin = sp_builtins.get_builtin(pool.code)
        if builtin is None:
            raise HTTPException(status_code=404, detail=f"不在内置目录: {pool.code}")

        from backend.shared.stock_pool.resolver import _fetch_builtin_symbols

        symbols = _fetch_builtin_symbols(builtin)
        if not symbols:
            raise HTTPException(
                status_code=503,
                detail=f"{pool.code} 暂无成分（QuantDB 未就绪或数据源未接入），"
                "TXT 保持不变",
            )
        result = await repo.save_members(session, pool, symbols, actor=_actor(request))
        pool = await _require_pool(session, pool_id)
    return {"success": True, **result, "pool": pool.model_dump()}


@router.get("/{pool_id}/preview", summary="预览成分（含最新行情指标）")
async def preview_pool(
    pool_id: str,
    limit: int = Query(200, ge=1, le=2000),
):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        api_symbols = repo.read_members(pool)[:limit]
        metrics = await _fetch_latest_metrics(session, api_symbols, pool.market)

    index = None
    try:
        index = sp_parser.load_stock_index()
    except Exception:  # noqa: BLE001 - 索引缺失不阻断预览
        pass

    items = []
    for api in api_symbols:
        storage = to_storage_symbol(api, pool.market)
        entry = index.by_symbol.get(storage) if index else None
        items.append(
            {
                "symbol": storage,
                "api_symbol": api,
                "name": (entry.name if entry else None),
                "metrics": metrics.get(api, {}),
            }
        )
    return {
        "pool_id": pool_id,
        "code": pool.code,
        "total": len(items),
        "items": items,
        "metrics_available": bool(metrics),
    }


async def _fetch_latest_metrics(
    session, api_symbols: list[str], market: str
) -> dict[str, dict]:
    """尽力取最新行情指标；表/列缺失时返回空（不阻断预览）。"""
    if not api_symbols or normalize_market(market) != sp_const.MARKET_CN:
        return {}
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
                    {"codes": api_symbols[:1000]},
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
# 引用（binding）
# ---------------------------------------------------------------------------
@router.get("/{pool_id}/usages", summary="引用情况（策略 / 模型 / 账户）")
async def list_usages(pool_id: str, target_type: str | None = Query(None)):
    async with get_session() as session:
        await repo.ensure_tables(session)
        await _require_pool(session, pool_id)
        usages = await repo.list_usages(session, pool_id, target_type=target_type)
    return {"total": len(usages), "items": usages}


@router.post("/{pool_id}/bindings", summary="登记引用（策略/模型/账户等长生命周期绑定）")
async def bind_pool_endpoint(pool_id: str, payload: PoolBindingRequest, request: Request):
    async with get_session() as session:
        await repo.ensure_tables(session)
        pool = await _require_pool(session, pool_id)
        await repo.bind_pool(
            session,
            pool.pool_id,
            payload.target_type,
            payload.target_id,
            mode=payload.mode,
            priority=payload.priority,
            tenant_id=payload.tenant_id,
            user_id=payload.user_id,
            actor=_actor(request),
        )
    return {"success": True, "pool_id": pool_id}


@router.delete("/{pool_id}/bindings/{target_type}/{target_id:path}", summary="解除引用")
async def unbind_pool_endpoint(pool_id: str, target_type: str, target_id: str):
    async with get_session() as session:
        await repo.ensure_tables(session)
        removed = await repo.unbind_pool(session, pool_id, target_type, target_id)
    return {"success": True, "removed": removed}


@router.get("/bindings/by-target", summary="反查：某个策略/模型/账户绑了哪些池")
async def list_pools_for_target(
    target_type: str = Query(...),
    target_id: str = Query(..., min_length=1),
):
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

    metadata 里的 `pool_id` 是引用串（如 `pool:csi300`），先经 resolver 解析成
    库内池再登记；解析不到（临时 list:/file: 或已删池）计入 unresolved。
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
                                   metadata_json ->> 'pool_id' AS pool_ref
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
            )
            .mappings()
            .all()
        }

        created: list[dict[str, Any]] = []
        missing: list[str] = []
        for row in rows:
            pool_ref = str(row["pool_ref"])
            snap = resolver.resolve_sync(pool_ref)
            pool_id = snap.pool_id if snap.source == "pool" else ""
            if pool_id not in known:
                missing.append(pool_ref)
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
