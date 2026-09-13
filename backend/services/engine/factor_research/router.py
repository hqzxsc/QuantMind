"""因子研究 API 路由（/api/v1/factor-research）。

挂载：backend/services/engine/main.py 中 include_router；
网关放行：backend/services/api/routers/engine_proxy.py 白名单前缀。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.services.engine.factor_research import service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/factor-research", tags=["Factor Research"])


@router.get("/catalog")
async def get_catalog():
    """82 个因子目录（大类/小类/方向/公式/数据源/可用性）+ 快照元信息。"""
    return await asyncio.to_thread(service.catalog)


@router.get("/leaderboard")
async def get_leaderboard(sort: str = "composite"):
    """排行榜（综合分 / 年化 / 夏普 / IC 等排序）。"""
    return await asyncio.to_thread(service.leaderboard, sort)


@router.get("/factor/{code}")
async def get_factor(code: str):
    """单因子：定义 + KPI + 净值 + IC 序列 + 最新持仓。"""
    d = await asyncio.to_thread(service.factor_detail, code)
    if d is None:
        raise HTTPException(status_code=404, detail=f"未知因子: {code}")
    return d


@router.get("/compare")
async def get_compare(codes: str):
    """多因子对比：KPI + 净值 + IC + 相关矩阵（codes=逗号分隔，≤12）。"""
    lst = [c.strip() for c in codes.split(",") if c.strip()]
    if not lst:
        raise HTTPException(status_code=422, detail="codes 不能为空")
    return await asyncio.to_thread(service.compare, lst)


@router.get("/screening")
async def get_screening():
    """因子筛选：质量门槛 + 同源去重后的保留清单与剔除明细（训练特征选择用）。"""
    return await asyncio.to_thread(service.screening)


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
    cost_rate: float | None = Field(None, ge=0, le=0.05)


@router.post("/compose")
async def post_compose(req: ComposeRequest):
    """多因子合成：权重 + 阈值 → 实时回测（等权 Top-N，月末调仓）。"""
    out = await asyncio.to_thread(
        service.compose, req.weights, req.top_n, req.threshold, req.cost_rate
    )
    if out.get("error"):
        raise HTTPException(status_code=422, detail=out["error"])
    return out
