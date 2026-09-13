"""因子研究 API 路由（/api/v1/factor-research）。

挂载：backend/services/engine/main.py 中 include_router；
网关放行：backend/services/api/routers/engine_proxy.py 白名单前缀。

数据集：dataset=classic（demo 复刻 82 因子，默认）| private（筛选最终保留的私人因子库，当前 292）。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.services.engine.factor_research import service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/factor-research", tags=["Factor Research"])


def _snapshot_guard(exc: FileNotFoundError) -> None:
    """快照缺失（如未计算/重建中）→ 503 可读提示，而非 500。"""
    if "factor_panel" in str(exc) or "缺失" in str(exc):
        raise HTTPException(
            status_code=503,
            detail="因子快照不完整（可能未生成或正在重建）：请在页面右上角「快照」中一键计算",
        )
    raise exc


async def _run(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except FileNotFoundError as e:
        _snapshot_guard(e)
        raise  # pragma: no cover - _snapshot_guard 必然抛出


@router.get("/catalog")
async def get_catalog(dataset: str = "classic"):
    """因子目录（大类/小类/方向/公式/数据源/可用性）+ 快照元信息 + 基准。"""
    return await _run(service.catalog, dataset)


@router.get("/leaderboard")
async def get_leaderboard(
    start: str | None = None,
    end: str | None = None,
    n: int = 30,
    dataset: str = "classic",
):
    """排行榜：区间内重算 KPI/超额/RankIC + 持仓画像 + 双标签 + 综合分。

    n = 业绩 KPI 的持仓数（默认 30）；标签恒按 top-30 判定；全列返回，前端排序。
    """
    return await _run(service.leaderboard, start, end, n, dataset)


@router.get("/factor/{code}")
async def get_factor(
    code: str,
    ns: str = "30",
    start: str | None = None,
    end: str | None = None,
    stocks_n: int = 30,
    dataset: str = "classic",
):
    """单因子：区间内多档持仓数（ns=5,10,30）净值/KPI/超额 + N 扫描 + 个股表 + 分布。"""
    try:
        wanted = [int(x) for x in str(ns).split(",") if x.strip()]
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"ns 需为逗号分隔整数: {e}") from e
    d = await _run(service.factor_detail, code, wanted, start, end, stocks_n, dataset)
    if d is None:
        raise HTTPException(status_code=404, detail=f"未知因子: {code}")
    return d


class CompareItem(BaseModel):
    code: str
    n: int = Field(30, ge=1, le=100)


class CompareRequest(BaseModel):
    items: list[CompareItem]
    start: str | None = None
    end: str | None = None
    dataset: str = "classic"


@router.get("/compare")
async def get_compare(
    codes: str,
    start: str | None = None,
    end: str | None = None,
    dataset: str = "classic",
):
    """多因子对比（GET 兼容形态：codes=逗号分隔，每因子 Top-30）。"""
    lst = [c.strip() for c in codes.split(",") if c.strip()]
    if not lst:
        raise HTTPException(status_code=422, detail="codes 不能为空")
    return await _run(service.compare, lst, start, end, dataset)


@router.post("/compare")
async def post_compare(req: CompareRequest):
    """多因子对比（每因子可单独设持仓数 n，≤12 个）。"""
    if not req.items:
        raise HTTPException(status_code=422, detail="items 不能为空")
    items = [it.model_dump() for it in req.items]
    return await _run(service.compare, items, req.start, req.end, req.dataset)


@router.get("/screening")
async def get_screening():
    """因子筛选：质量门槛 + 同源去重后的保留清单与剔除明细（训练特征选择用）。"""
    return await asyncio.to_thread(service.screening)


@router.get("/snapshot-status")
async def get_snapshot_status(dataset: str = "classic"):
    """快照状态（是否存在/构建中/进度日志）—— 供前端「一键计算」入口。"""
    return await _run(service.snapshot_status, dataset)


@router.post("/build")
async def post_build(dataset: str = "classic"):
    """一键计算快照（后台子进程，全本地 QuantDB 计算、不上传任何数据）。"""
    out = await _run(service.start_build, dataset)
    if out.get("error"):
        raise HTTPException(status_code=500, detail=out["error"])
    return out


@router.get("/correlation")
async def get_correlation(codes: str | None = None):
    lst = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
    return await asyncio.to_thread(service.correlation, lst)


class ComposeRequest(BaseModel):
    weights: dict[str, float] = Field(..., description="因子代码 → 权重（可负）")
    top_n: int = Field(30, ge=1, le=200)
    threshold: float | None = Field(
        None, description="合成打分下限（z 刻度），None=不过滤"
    )
    thresholds: dict[str, float] | None = Field(
        None, description="每因子过滤下限（z 刻度，选股前先筛）"
    )
    cost_rate: float | None = Field(None, ge=0, le=0.05)
    start: str | None = None
    end: str | None = None
    dataset: str = "classic"


@router.post("/compose")
async def post_compose(req: ComposeRequest):
    """多因子合成：权重 +（逐因子/合成）阈值 → 区间内实时回测。"""
    out = await _run(
        service.compose,
        req.weights,
        req.top_n,
        req.threshold,
        req.thresholds,
        req.cost_rate,
        req.start,
        req.end,
        req.dataset,
    )
    if out.get("error"):
        raise HTTPException(status_code=422, detail=out["error"])
    return out


class OptimalRequest(BaseModel):
    codes: list[str] = Field(..., description="参与搜索的因子（≤10）")
    top_n: int = Field(30, ge=1, le=100)
    start: str | None = None
    end: str | None = None
    dataset: str = "classic"


@router.post("/optimal-weights")
async def post_optimal(req: OptimalRequest):
    """网格搜索最优权重（非负、和为 1）：夏普 / 年化 / 超额各一组。"""
    if not req.codes:
        raise HTTPException(status_code=422, detail="codes 不能为空")
    out = await _run(
        service.optimal_weights, req.codes, req.top_n, req.start, req.end, req.dataset
    )
    if out.get("error"):
        raise HTTPException(status_code=422, detail=out["error"])
    return out
