"""TDX L2 实时推理任务 — 13 因子截面标准化 → ICIR 加权 → 实时胜率分 → 自动买卖。

信号合成（对应当日推理缓存的日频分）:
    signal_score = 100 / (1 + e^(-z_combined))          # 0~100
    z_combined  = Σ(w_i · z_i) / Σw_i                   # 池内截面 z-score × ICIR 权重
    realtime_score = 0.6 × (fusion/3×100) + 0.4 × signal_score

触发（全部 Redis tdx:l2:config 可调）:
    买入: realtime_score > buy_trigger(65) 且 大盘>MA20 且 未持仓 且 冷却已过
    卖出: realtime_score < sell_trigger(45) 且 available_volume>0 (T+1 已解锁)
    冷却: 每只 cooldown_min(30) 分钟（Redis tdx:l2:cooldown:{symbol}）

执行复用 TdxRollingTradeService（三档模式 + 会员门控）:
    tdx  = place_rolling_orders（TQ 收费账号 Value=2 直接提交免确认）
    paper= place_paper_orders（本地模拟盘免确认）
    off  = 仅更新分数不执行
"""
import asyncio
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

from backend.services.trade.redis_client import redis_client as trade_redis
from backend.services.trade.services.tdx_l2_capture_task import (
    FACTOR_ICIR,
    _CONFIG_KEY,
    _REALTIME_KEY,
)

logger = logging.getLogger(__name__)

_CONFIG_DEFAULTS: dict[str, Any] = {
    "enabled": False,          # 默认关，手动开启（安全）
    "pool_size": 20,
    "buy_trigger": 65.0,
    "sell_trigger": 45.0,
    "interval_sec": 60,
    "cooldown_min": 30,
    "daily_weight": 0.6,       # 日频分权重
    "signal_weight": 0.4,      # 实时信号分权重
    "factor_weights": None,    # None = 用回测 ICIR 权重 FACTOR_ICIR
}

_STATUS_KEY = "tdx:l2:realtime:status"
_SCORE_KEY = "tdx:l2:score:{symbol}"
_COOLDOWN_KEY = "tdx:l2:cooldown:{symbol}"
_FACTOR_LIST = list(FACTOR_ICIR.keys())

realtime_status: dict[str, Any] = {
    "running": False,
    "last_cycle_at": None,
    "last_error": None,
    "last_buys": [],
    "last_sells": [],
    "pool_size": 0,
    "scored": 0,
}


# ============ 配置 ============

def load_l2_config() -> dict[str, Any]:
    cfg = dict(_CONFIG_DEFAULTS)
    if trade_redis.client is not None:
        saved = trade_redis.get(_CONFIG_KEY) or {}
        if isinstance(saved, dict):
            cfg.update(saved)
    # 清理遗留因子
    if cfg.get("factor_weights") is not None and not isinstance(cfg["factor_weights"], dict):
        cfg["factor_weights"] = None
    return cfg


def save_l2_config(updates: dict[str, Any]) -> dict[str, Any]:
    """更新 L2 实时配置（Redis），返回保存后的完整配置。"""
    cfg = load_l2_config()
    allowed = set(_CONFIG_DEFAULTS.keys())
    for k, v in updates.items():
        if k not in allowed:
            continue
        if k in ("buy_trigger", "sell_trigger", "interval_sec", "cooldown_min"):
            cfg[k] = float(v)
        elif k == "pool_size":
            cfg[k] = max(5, min(int(v), 50))
        elif k in ("daily_weight", "signal_weight"):
            cfg[k] = float(v)
        elif k == "enabled":
            cfg[k] = bool(v)
        elif k == "factor_weights" and isinstance(v, dict):
            cfg[k] = {kk: float(vv) for kk, vv in v.items() if kk in FACTOR_ICIR}
    if trade_redis.client is not None:
        trade_redis.set(_CONFIG_KEY, cfg)
    return cfg


# ============ 分数合成 ============

def _z_score(values: dict[str, float]) -> dict[str, float]:
    """截面 z-score（clip ±3），标准差为 0 时归零。"""
    vs = list(values.values())
    n = len(vs)
    if n < 3:
        return {k: 0.0 for k in values}
    mean = sum(vs) / n
    var = sum((v - mean) ** 2 for v in vs) / (n - 1)
    std = math.sqrt(var)
    if std < 1e-12:
        return {k: 0.0 for k in values}
    return {k: max(-3.0, min(3.0, (v - mean) / std)) for k, v in values.items()}


def compute_signal_scores(
    pool_factors: dict[str, dict[str, float | None]], factor_weights: dict[str, float] | None
) -> dict[str, float]:
    """池内截面标准化 + ICIR 加权 + sigmoid → 0~100 实时信号分。

    pool_factors: {symbol: {factor: value}}（13 因子原始值，None=样本不足跳过）
    factor_weights: None = 用回测 ICIR 权重；否则用自定义 {factor: weight}
    """
    weights = factor_weights or FACTOR_ICIR
    w_sum = sum(weights.values()) + 1e-9
    symbols = list(pool_factors.keys())
    signal: dict[str, float] = {}
    for sym in symbols:
        z_total = 0.0
        for factor, w in weights.items():
            # 该因子在池内所有有效样本上的截面 z（缺失/None 的股票按 0 处理）
            vals = {
                s: pf[factor]
                for s, pf in pool_factors.items()
                if isinstance(pf.get(factor), (int, float))
            }
            if len(vals) < 3:
                continue
            z = _z_score(vals)
            z_total += w * z.get(sym, 0.0)
        z = max(-3.0, min(3.0, z_total / w_sum))
        signal[sym] = round(100.0 / (1.0 + math.exp(-z)), 2)
    return signal


def compute_realtime_score(fusion_score: float, signal_score: float, daily_weight: float = 0.6, signal_weight: float = 0.4) -> float:
    """实时胜率分 = 日频分(0~100) × daily_weight + 信号分 × signal_weight。"""
    daily = (fusion_score / 3.0 * 100.0) if fusion_score else 50.0
    return round(daily_weight * daily + signal_weight * signal_score, 2)


# ============ 冷却 ============

def _cooldown_key(symbol: str) -> str:
    return _COOLDOWN_KEY.format(symbol=symbol.lower())


def is_cooldown(symbol: str, cooldown_min: float) -> bool:
    if trade_redis.client is None or cooldown_min <= 0:
        return False
    ts = trade_redis.get(_cooldown_key(symbol))
    if not ts:
        return False
    return (time.time() - float(ts)) < cooldown_min * 60


def set_cooldown(symbol: str, cooldown_min: float) -> None:
    if trade_redis.client is None:
        return
    trade_redis.set(_cooldown_key(symbol), time.time())


# ============ 执行 ============

async def _execute_signals(
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    buys: list[dict[str, Any]],
    sells: list[dict[str, Any]],
    execute_mode: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """按三档模式执行下单，返回 (placed, failed, error)。"""
    from backend.services.trade.services.tdx_rolling_trade_service import (
        TdxRollingTradeService,
    )

    svc = TdxRollingTradeService()
    if execute_mode == "paper":
        placed, failed = await svc.place_paper_orders(
            tenant_id=tenant_id, user_id=user_id, run_id=run_id, buys=buys, sells=sells
        )
        return placed, failed, None
    if execute_mode == "tdx":
        placed, failed = await svc.place_rolling_orders(run_id=run_id, buys=buys, sells=sells)
        return placed, failed, None
    return [], [], f"execute_mode={execute_mode} 仅预警不执行"


async def run_tdx_l2_realtime_task(interval_sec: int = 0) -> None:
    """L2 实时推理主循环：读采集因子 → 截面标准化 → 合成 → 触发买卖。"""
    from backend.services.trade.services.member_gate import is_paid_member
    from backend.services.trade.services.tdx_rolling_trade_service import (
        TdxRollingTradeService,
        load_rolling_config,
    )

    svc = TdxRollingTradeService()
    tenant_id, user_id = "default", "00000001"
    base_interval = float(interval_sec or 0)
    realtime_status["running"] = True
    logger.info("[TdxL2] 实时推理任务启动")

    while True:
        cycle_start = time.monotonic()
        buys_all: list[dict[str, Any]] = []
        sells_all: list[dict[str, Any]] = []
        error = None
        try:
            cfg = load_l2_config()
            interval = float(cfg.get("interval_sec") or base_interval or 60)
            if not cfg.get("enabled"):
                await asyncio.sleep(min(interval, 30))
                continue

            # 1. 读采集任务写的全部实时因子
            if trade_redis.client is None:
                await asyncio.sleep(interval)
                continue
            keys = trade_redis.client.scan_iter(match=_REALTIME_KEY.format(symbol="*"))
            pool_data: dict[str, dict[str, Any]] = {}
            for key in keys:
                payload = trade_redis.get(key)
                if not isinstance(payload, dict):
                    continue
                factors = payload.get("factors") or {}
                if not isinstance(factors, dict):
                    continue
                sym = str(payload.get("symbol") or "")
                if sym:
                    pool_data[sym] = payload
            pool_factors = {s: p.get("factors") or {} for s, p in pool_data.items()}
            realtime_status["pool_size"] = len(pool_factors)
            if len(pool_factors) < 5:
                await asyncio.sleep(interval)
                continue

            # 2. 信号分 + 日频融合分
            signal_scores = compute_signal_scores(pool_factors, cfg.get("factor_weights"))
            _, fusion_scores, _ = await svc.load_latest_scores(tenant_id=tenant_id, user_id=user_id)
            fusion_scores = fusion_scores or {}

            # 3. 三档模式 + 会员门控（与 run_rolling_push 同规则）
            _, fixed_buy_amount, execute_mode = load_rolling_config(tenant_id, user_id)
            if execute_mode != "off":
                try:
                    member_ok = await is_paid_member(tenant_id, user_id)
                except Exception:
                    member_ok = False
                if not member_ok:
                    await asyncio.sleep(interval)
                    continue

            # 4. 持仓（tdx/off 读桥，paper 读模拟盘）
            if execute_mode == "paper":
                positions, _ = await svc.load_positions_from_paper(tenant_id, user_id)
            else:
                positions, _ = await svc.load_positions_from_tdx()
            held = {p.get("symbol"): p for p in positions if isinstance(p, dict)}

            # 5. 触发判断
            buy_trigger = float(cfg.get("buy_trigger") or 65)
            sell_trigger = float(cfg.get("sell_trigger") or 45)
            cooldown_min = float(cfg.get("cooldown_min") or 30)
            daily_w = float(cfg.get("daily_weight") or 0.6)
            signal_w = float(cfg.get("signal_weight") or 0.4)
            index_above, market_detail = await svc.is_index_above_ma20()

            sell_items: list[dict[str, Any]] = []
            buy_items: list[dict[str, Any]] = []
            for sym, signal in signal_scores.items():
                fusion = fusion_scores.get(sym)
                realtime_score = compute_realtime_score(
                    fusion or 0.0, signal, daily_w, signal_w
                )
                redis_payload = {
                    "symbol": sym,
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "realtime_score": realtime_score,
                    "signal_score": signal,
                    "fusion_score": fusion,
                    "trigger": "buy" if realtime_score > buy_trigger else "sell" if realtime_score < sell_trigger else "hold",
                }
                if trade_redis.client is not None:
                    trade_redis.set(_SCORE_KEY.format(symbol=sym), redis_payload)

                # 卖出: 持仓 + T+1 可卖 + 分数跌破阈值 + 冷却已过
                pos = held.get(sym)
                if (
                    pos
                    and float(pos.get("available_volume") or 0) > 0
                    and realtime_score < sell_trigger
                    and not is_cooldown(sym, cooldown_min)
                ):
                    sell_items.append(
                        {
                            "symbol": sym,
                            "name": pos.get("name") or sym,
                            "score": realtime_score,
                            "volume": int(pos.get("volume") or 0),
                            "available_volume": int(pos.get("available_volume") or 0),
                            "reason": f"L2 实时分 {realtime_score} < {sell_trigger}",
                        }
                    )
                # 买入: 池内 + 未持仓 + 大盘 MA20 + 分数突破 + 冷却已过
                elif (
                    fusion is not None
                    and sym not in held
                    and index_above
                    and realtime_score > buy_trigger
                    and not is_cooldown(sym, cooldown_min)
                ):
                    price = float(pool_data.get(sym, {}).get("now") or 0)
                    if price <= 0:
                        continue
                    from backend.services.trade.services.tdx_rolling_trade_service import (
                        LOT_SIZE,
                    )

                    volume = int((float(fixed_buy_amount or 0) / price) // LOT_SIZE) * LOT_SIZE
                    if volume < LOT_SIZE:
                        continue
                    buy_items.append(
                        {
                            "symbol": sym,
                            "score": realtime_score,
                            "volume": volume,
                            "close": round(price, 2),
                            "amount": round(volume * price, 2),
                            "reason": f"L2 实时分 {realtime_score} > {buy_trigger}",
                        }
                    )

            # 6. 执行（先卖后买，同 rolling）
            if execute_mode != "off" and (buy_items or sell_items):
                _, run_id, _ = await svc.load_latest_scores(tenant_id=tenant_id, user_id=user_id)
                placed, failed = await _execute_signals(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    run_id=run_id or "l2",
                    buys=buy_items,
                    sells=sell_items,
                    execute_mode=execute_mode,
                )
                buys_all = [p for p in placed if p.get("side") == "buy"]
                sells_all = [p for p in placed if p.get("side") == "sell"]
                for item in placed:
                    set_cooldown(item["symbol"], cooldown_min)
                error = " | ".join(str(f.get("error")) for f in failed[:3]) or None
            elif buy_items or sell_items:
                buys_all, sells_all = buy_items, sell_items

            realtime_status.update(
                {
                    "last_buys": buys_all[:10],
                    "last_sells": sells_all[:10],
                    "last_cycle_at": datetime.now().isoformat(timespec="seconds"),
                    "index_above_ma20": index_above,
                    "market_detail": market_detail,
                    "execute_mode": execute_mode,
                    "last_error": error,
                }
            )
        except Exception as exc:
            realtime_status["last_error"] = str(exc)
            logger.warning("[TdxL2] 实时推理异常: %s", exc)

        elapsed = time.monotonic() - cycle_start
        await asyncio.sleep(max(5.0, interval - elapsed))
