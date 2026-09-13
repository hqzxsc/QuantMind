"""因子报告 REST 端点（引擎服务，经 api 网关 /api/v1/factor-report/* 转发）。

- GET /summary     快照摘要：429 因子的 IC/ICIR/分位价差/单调性/换手 + 快照元数据
- GET /detail      单因子明细（按需扫描分区）：分位净值、分位平均收益、IC 序列、换手序列
- GET /correlation 相关性子矩阵（从快照取，按请求顺序）
- GET /related     某因子的高相关因子 TopN
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from . import service

router = APIRouter(prefix="/api/v1/factor-report", tags=["Factor Report"])


@router.get("/summary")
async def factor_summary(
    library: str | None = Query(default=None, description="按库过滤：alpha158 / alpha101 / gtja191"),
    sort: str = Query(default="abs_ic", description="排序：abs_ic | icir | turnover | ls_mean | name"),
    limit: int = Query(default=0, ge=0, le=500, description="0 = 全部"),
):
    snap = service.load_snapshot()
    if not snap:
        return {
            "available": False,
            "reason": "因子报告快照尚未生成；在服务器执行 python backend/scripts/build_factor_report.py 后刷新",
        }
    items = list(snap.get("factors") or [])
    if library:
        items = [x for x in items if x.get("library") == library]

    def _key(x: dict):
        if sort == "icir":
            return abs(x.get("icir") or 0)
        if sort == "turnover":
            return -(x.get("turnover") or 0)
        if sort == "ls_mean":
            return abs(x.get("ls_mean") or 0)
        if sort == "name":
            return x.get("name") or ""
        return abs(x.get("ic_mean") or 0)

    reverse = sort != "name"
    if reverse:
        items.sort(key=_key, reverse=True)
    else:
        items.sort(key=_key)
    total = len(items)
    if limit:
        items = items[:limit]
    return {"available": True, "meta": snap.get("meta") or {}, "total": total, "factors": items}


@router.get("/detail")
async def factor_detail(
    factor: str = Query(..., description="因子名，如 a158_ROC20 / a101_003 / gtja_088"),
    horizon: str = Query(default="fwd_ret_5", description="前瞻期：fwd_ret_1/2/5/10/20"),
    lookback: int = Query(default=250, ge=20, le=1200, description="回看交易日数"),
):
    try:
        return service.compute_detail(factor, horizon=horizon, lookback=lookback)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/correlation")
async def factor_correlation(
    factors: str = Query(..., description="逗号分隔的因子名（2-30 个）"),
):
    names = [n.strip() for n in factors.split(",") if n.strip()]
    if not names:
        raise HTTPException(status_code=400, detail="factors 不能为空")
    if len(names) > 30:
        raise HTTPException(status_code=400, detail="一次最多比较 30 个因子")
    return service.correlation_slice(names)


@router.get("/related")
async def factor_related(
    factor: str = Query(..., description="因子名"),
    top: int = Query(default=8, ge=1, le=50),
):
    return {"factor": factor, "related": service.top_correlated(factor, top=top)}
