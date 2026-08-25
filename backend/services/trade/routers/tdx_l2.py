"""TDX L2 实时推理路由 — 配置 / 实时分数 / 任务状态。

链路: tdx_l2_capture_task（采集 13 因子）→ tdx_l2_realtime_task（截面标准化+触发）
配置存 Redis `tdx:l2:config`，前端可在设置页调整后即时生效。
"""
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.services.trade.deps import AuthContext, get_auth_context
from backend.services.trade.redis_client import redis_client as trade_redis
from backend.services.trade.services.tdx_l2_capture_task import (
    FACTOR_ICIR,
    l2_status as capture_status,
)
from backend.services.trade.services.tdx_l2_realtime import (
    _SCORE_KEY,
    load_l2_config,
    realtime_status,
    save_l2_config,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class L2ConfigUpdate(BaseModel):
    enabled: bool | None = Field(None, description="开启 L2 实时推理（需会员+执行模式非 off）")
    pool_size: int | None = Field(None, ge=5, le=50, description="候选池大小")
    buy_trigger: float | None = Field(None, ge=0, le=100, description="买入触发分")
    sell_trigger: float | None = Field(None, ge=0, le=100, description="卖出触发分")
    interval_sec: float | None = Field(None, ge=10, description="实时轮询周期（秒）")
    cooldown_min: float | None = Field(None, ge=0, description="单只冷却（分钟）")
    daily_weight: float | None = Field(None, ge=0, le=1, description="日频分权重")
    signal_weight: float | None = Field(None, ge=0, le=1, description="实时信号分权重")


@router.get("/tdx/l2-config")
async def get_l2_config(auth: AuthContext = Depends(get_auth_context)):
    """读取 L2 实时推理配置（含回测 ICIR 因子权重）。"""
    cfg = load_l2_config()
    return {"success": True, "config": cfg, "factor_icir": FACTOR_ICIR}


@router.put("/tdx/l2-config")
async def update_l2_config(
    data: L2ConfigUpdate,
    auth: AuthContext = Depends(get_auth_context),
):
    """保存 L2 实时推理配置。开启执行前校验付费会员（与滚动买卖同规则）。"""
    from backend.services.trade.services.member_gate import is_paid_member
    from backend.services.trade.services.tdx_rolling_trade_service import (
        DEFAULT_EXECUTE_MODE,
        load_rolling_config,
    )

    tenant_id = (auth.tenant_id or "default").strip() or "default"
    user_id = str(auth.user_id or "00000001").strip() or "00000001"

    updates = data.model_dump(exclude_none=True)
    if not updates:
        return {"success": True, "config": load_l2_config()}

    # 开启执行前校验会员（execute_mode=off 时仅计分，不强制）
    if updates.get("enabled"):
        _, _, execute_mode = load_rolling_config(tenant_id, user_id)
        if execute_mode != DEFAULT_EXECUTE_MODE and not await is_paid_member(
            tenant_id, user_id
        ):
            raise HTTPException(
                status_code=403,
                detail="直接下单为 QuantDB 付费会员专属功能，请保持会员在期后使用",
            )

    cfg = save_l2_config(updates)
    return {
        "success": True,
        "message": "L2 实时推理配置已保存",
        "config": cfg,
    }


@router.get("/tdx/l2/realtime")
async def get_l2_realtime(auth: AuthContext = Depends(get_auth_context)):
    """批量读取候选池实时胜率分 + 13 因子（Redis，毫秒级）。"""
    if trade_redis.client is None:
        raise HTTPException(status_code=503, detail="Redis 不可用")
    scores: list[dict[str, Any]] = []
    for key in trade_redis.client.scan_iter(match=_SCORE_KEY.format(symbol="*")):
        payload = trade_redis.get(key)
        if isinstance(payload, dict):
            scores.append(payload)
    scores.sort(key=lambda s: float(s.get("realtime_score") or 0), reverse=True)
    return {"success": True, "total": len(scores), "scores": scores}


@router.get("/tdx/l2/status")
async def get_l2_status(auth: AuthContext = Depends(get_auth_context)):
    """采集任务 + 实时推理任务状态。"""
    from sqlalchemy import text

    from backend.shared.database_manager_v2 import get_session

    pg_counts: dict[str, Any] = {"snapshot": 0, "daily": 0}
    try:
        async with get_session() as db:
            for table in ("tdx_l2_snapshot", "tdx_l2_daily"):
                rows = await db.execute(text(f"SELECT COUNT(*) AS c FROM {table}"))
                pg_counts[table] = (rows.first() or [0])[0]
    except Exception as exc:
        pg_counts["error"] = str(exc)

    return {
        "success": True,
        "capture": capture_status,
        "realtime": realtime_status,
        "pg_counts": pg_counts,
    }


# ============ 委托查询 / 撤单（全自动实盘控制台） ============

class CancelOrderRequest(BaseModel):
    symbol: str = Field(..., description="股票代码（前缀或后缀均可）")
    order_id: str = Field(..., description="委托编号（桥 orders/query 的 order_id）")


@router.get("/tdx/orders")
async def get_tdx_orders(auth: AuthContext = Depends(get_auth_context)):
    """拉取通达信当日委托 + 合并本系统在途重挂记录 + 委托时点行情。

    orders: 桥当日委托（含已成交/在途/废单, filled_price=实际成交均价）
    inflight: 未成交在途单（本系统重挂逻辑接管中）
    order_quotes: {order_id: 决策时点行情 + 成交后并入的实际成交价}
    quotes: {symbol: 每只最近一次委托行情}
    """
    from backend.services.trade.services.tdx_rolling_trade_service import (
        TdxRollingTradeService,
    )
    from backend.services.trade.services.tdx_l2_realtime import (
        _FILLED_STATUSES,
        list_inflight,
        load_order_quotes,
        load_symbol_quotes,
    )

    svc = TdxRollingTradeService()
    try:
        orders = await svc.pull_today_orders()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"委托查询失败(桥不可达): {exc}")
    inflight = list_inflight()
    order_quotes = load_order_quotes()
    # 当日委托优先于 Redis 缓存覆盖（成交价以桥为准）
    for o in orders:
        rec = order_quotes.get(str(o.get("order_id") or ""))
        if rec:
            rec["status"] = o.get("status") or rec.get("status")
            rec["filled_price"] = o.get("filled_price") or rec.get("filled_price")
            rec["filled_volume"] = o.get("filled_volume") or rec.get("filled_volume")
    return {
        "success": True,
        "orders": orders,
        "inflight": inflight,
        "order_quotes": order_quotes,
        "quotes": load_symbol_quotes(),
    }


@router.post("/tdx/orders/cancel")
async def cancel_tdx_order(
    data: CancelOrderRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    """一键撤单：撤掉通达信当日委托。

    撤单成功后复核一次当日委托, 返回该委托的最终状态
    （撤单可能失败或"撤销前已成交"）。
    """
    from backend.services.trade.services.tdx_rolling_trade_service import (
        TdxRollingTradeService,
    )

    svc = TdxRollingTradeService()
    try:
        resp = await svc.cancel_order(data.symbol, data.order_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"撤单失败(桥不可达): {exc}")
    ok = bool(resp.get("success"))
    final_status = None
    if ok:
        try:
            orders = await svc.pull_today_orders(data.symbol)
            final_status = next(
                (o for o in orders if str(o.get("order_id")) == data.order_id), None
            )
        except Exception:
            final_status = None
    return {
        "success": ok,
        "message": resp.get("message") or ("撤单已受理" if ok else "撤单失败"),
        "final_status": final_status,
    }


@router.get("/tdx/inflight")
async def get_tdx_inflight(auth: AuthContext = Depends(get_auth_context)):
    """读取未成交在途单（本系统重挂逻辑接管中的订单）。"""
    from backend.services.trade.services.tdx_l2_realtime import list_inflight

    return {"success": True, "inflight": list_inflight()}
