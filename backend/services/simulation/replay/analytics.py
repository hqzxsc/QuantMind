"""回放统计报告引擎（纯计算，无 IO 无副作用）。

从 replay_equity_snapshots + replay_trades 聚合出回测级统计报告。
内联 _max_drawdown / _annualized_sharpe 算法（原 backtest_service.py 实现），
避免触发 inference 模块的重依赖链（sqlalchemy / model_registry / ...）。

设计约束：
- ≤400 行
- 所有函数接收 plain dict/list，不依赖 ORM 对象
- 不做 DB 查询，由 router 层负责数据加载
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

_TRADING_DAYS_PER_YEAR = 252


# ---------------------------------------------------------------------------
# Inlined indicators from backtest_service (avoids heavy import chain)
# ---------------------------------------------------------------------------


def _max_drawdown(cumulative_returns: list[float]) -> float:
    """Compute max relative drawdown from a cumulative return series.

    Copied from backtest_service.py to avoid importing the inference module
    (which pulls in sqlalchemy / model_registry / ...).
    """
    if not cumulative_returns:
        return 0.0
    peak = cumulative_returns[0]
    max_dd = 0.0
    for r in cumulative_returns:
        peak = max(peak, r)
        denom = max(1.0 + peak, 1e-9)
        dd = (peak - r) / denom
        max_dd = max(max_dd, dd)
    return float(max_dd)


def _newey_west_t_stat(series: list[float], lag: int) -> float | None:
    """IC 序列的 t 统计量，Bartlett 核 Newey-West 校正。"""
    arr = np.asarray([v for v in series if v is not None and not np.isnan(v)], dtype=float)
    n = arr.size
    if n < 3:
        return None
    mean = float(arr.mean())
    demeaned = arr - mean
    gamma0 = float(np.dot(demeaned, demeaned) / n)
    if gamma0 <= 0:
        return None
    variance = gamma0
    max_lag = int(min(max(lag, 0), n - 1))
    for k in range(1, max_lag + 1):
        gamma_k = float(np.dot(demeaned[:-k], demeaned[k:]) / n)
        weight = 1.0 - k / (max_lag + 1)
        variance += 2.0 * weight * gamma_k
    if variance <= 0:
        variance = gamma0
    return float(mean / np.sqrt(variance / n))


def _annualized_sharpe(returns: list[float], sample_interval: int, holding_days: int) -> float:
    """年化 Sharpe，Newey-West 校正。"""
    arr = np.asarray([v for v in returns if v is not None and not np.isnan(v)], dtype=float)
    if arr.size < 2:
        return 0.0
    std = float(arr.std(ddof=1))
    if std <= 0:
        return 0.0
    periods_per_year = _TRADING_DAYS_PER_YEAR / max(sample_interval, 1)
    lag = max(int(round(holding_days / max(sample_interval, 1))) - 1, 0)
    t = _newey_west_t_stat(arr.tolist(), lag)
    if t is not None and t != 0:
        std = abs(float(arr.mean())) / (abs(t) / np.sqrt(arr.size))
        if std <= 0:
            return 0.0
    return float(arr.mean() / std * np.sqrt(periods_per_year))


# ---------------------------------------------------------------------------
# Core metrics
# ---------------------------------------------------------------------------


def compute_core_metrics(
    snapshots: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    initial_cash: float,
) -> dict[str, Any]:
    """从快照 + 成交记录计算核心指标。

    Parameters
    ----------
    snapshots : list of dict
        replay_equity_snapshots 行，需含 trade_date / total_asset / day_pnl /
        cum_pnl / realized_pnl_cum / unrealized_pnl / cash / position_count
    trades : list of dict
        replay_trades 行，需含 side / realized_pnl / holding_days / total_fee /
        origin / quantity / price / trade_value
    initial_cash : float
        初始资金
    """
    if not snapshots:
        return _empty_metrics()

    # Sort by trade_date
    snapshots = sorted(snapshots, key=lambda s: s["trade_date"])
    trades = sorted(trades, key=lambda t: t.get("trade_date", ""))

    total_days = len(snapshots)
    final_asset = float(snapshots[-1]["total_asset"])
    total_return = (final_asset - initial_cash) / initial_cash

    # Annualized return
    annualized_return = (
        (1 + total_return) ** (_TRADING_DAYS_PER_YEAR / max(total_days, 1)) - 1
        if total_days > 0
        else 0.0
    )

    # Daily returns
    daily_returns: list[float] = []
    for i, s in enumerate(snapshots):
        if i == 0:
            # First day: return relative to initial_cash
            daily_returns.append(
                (float(s["total_asset"]) - initial_cash) / initial_cash
            )
        else:
            prev = float(snapshots[i - 1]["total_asset"])
            daily_returns.append(
                (float(s["total_asset"]) - prev) / prev if prev > 0 else 0.0
            )

    # Cumulative returns for drawdown
    cum_returns: list[float] = []
    running = 0.0
    for r in daily_returns:
        running += r
        cum_returns.append(running)

    # Max drawdown
    max_dd = _max_drawdown(cum_returns) if cum_returns else 0.0

    # Drawdown period
    dd_start, dd_end, dd_recovery_days = _drawdown_period(cum_returns, snapshots)

    # Sharpe (daily, non-overlapping → lag=0)
    sharpe = _annualized_sharpe(daily_returns, sample_interval=1, holding_days=1)

    # Sortino (downside deviation only)
    sortino = _annualized_sortino(daily_returns)

    # Calmar
    calmar = annualized_return / abs(max_dd) if abs(max_dd) > 1e-9 else 0.0

    # Annualized volatility
    vol = _annualized_volatility(daily_returns)

    # Sell trades for win rate / PnL ratio
    sell_trades = [t for t in trades if t.get("side") == "SELL"]
    win_trades = [t for t in sell_trades if (t.get("realized_pnl") or 0) > 0]
    loss_trades = [t for t in sell_trades if (t.get("realized_pnl") or 0) < 0]

    win_rate = len(win_trades) / max(len(sell_trades), 1)
    avg_win = (
        sum(t["realized_pnl"] for t in win_trades) / len(win_trades)
        if win_trades
        else 0.0
    )
    avg_loss = (
        sum(abs(t["realized_pnl"]) for t in loss_trades) / len(loss_trades)
        if loss_trades
        else 0.0
    )
    pnl_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

    # Turnover
    total_trade_value = sum(float(t.get("trade_value", 0)) for t in trades)
    avg_total_asset = (
        sum(float(s["total_asset"]) for s in snapshots) / total_days
        if total_days > 0
        else initial_cash
    )
    turnover = (
        total_trade_value / (avg_total_asset * total_days) * _TRADING_DAYS_PER_YEAR
        if avg_total_asset > 0 and total_days > 0
        else 0.0
    )

    # Fee drag
    total_fee = sum(float(t.get("total_fee", 0)) for t in trades)
    fee_drag = total_fee / initial_cash if initial_cash > 0 else 0.0

    # Average holding days
    holding_days_list = [
        int(t["holding_days"])
        for t in sell_trades
        if t.get("holding_days") is not None
    ]
    avg_holding_days = (
        sum(holding_days_list) / len(holding_days_list) if holding_days_list else 0.0
    )

    # Stop-loss stats (replay-specific)
    stop_loss_trades = [t for t in trades if t.get("origin") == "stop_loss"]
    stop_loss_count = len(stop_loss_trades)
    stop_loss_pnl = sum(float(t.get("realized_pnl", 0)) for t in stop_loss_trades)

    return {
        "total_return": round(total_return, 6),
        "annualized_return": round(annualized_return, 6),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "max_drawdown": round(max_dd, 6),
        "max_drawdown_start": dd_start,
        "max_drawdown_end": dd_end,
        "max_drawdown_recovery_days": dd_recovery_days,
        "calmar": round(calmar, 4),
        "annualized_volatility": round(vol, 6),
        "win_rate": round(win_rate, 4),
        "pnl_ratio": round(pnl_ratio, 4),
        "expectancy": round(expectancy, 2),
        "turnover": round(turnover, 4),
        "total_fee": round(total_fee, 2),
        "fee_drag": round(fee_drag, 6),
        "trade_count": len(trades),
        "sell_count": len(sell_trades),
        "avg_holding_days": round(avg_holding_days, 1),
        "stop_loss_count": stop_loss_count,
        "stop_loss_pnl": round(stop_loss_pnl, 2),
        "total_days": total_days,
        "initial_cash": initial_cash,
        "final_asset": round(final_asset, 2),
    }


# ---------------------------------------------------------------------------
# NAV curve
# ---------------------------------------------------------------------------


def compute_nav_curve(
    snapshots: list[dict[str, Any]],
    initial_cash: float,
) -> list[dict[str, Any]]:
    """逐日净值曲线。"""
    if not snapshots:
        return []

    snapshots = sorted(snapshots, key=lambda s: s["trade_date"])
    curve: list[dict[str, Any]] = []
    peak = 1.0

    for i, s in enumerate(snapshots):
        total_asset = float(s["total_asset"])
        nav = total_asset / initial_cash if initial_cash > 0 else 1.0
        day_return = (
            (total_asset - float(snapshots[i - 1]["total_asset"]))
            / float(snapshots[i - 1]["total_asset"])
            if i > 0 and float(snapshots[i - 1]["total_asset"]) > 0
            else (total_asset - initial_cash) / initial_cash
        )
        cum_return = nav - 1.0
        peak = max(peak, nav)
        drawdown = (peak - nav) / peak if peak > 0 else 0.0
        cash_ratio = float(s.get("cash", 0)) / total_asset if total_asset > 0 else 1.0

        curve.append({
            "date": str(s["trade_date"]),
            "total_asset": round(total_asset, 2),
            "nav": round(nav, 6),
            "day_return": round(day_return, 6),
            "cum_return": round(cum_return, 6),
            "drawdown": round(drawdown, 6),
            "cash_ratio": round(cash_ratio, 4),
        })

    return curve


# ---------------------------------------------------------------------------
# Rolling metrics
# ---------------------------------------------------------------------------


def compute_rolling_metrics(
    snapshots: list[dict[str, Any]],
    initial_cash: float,
    window: int = 20,
) -> dict[str, list[dict[str, Any]]]:
    """滚动夏普 / 滚动波动率 + 月度收益热力图。"""
    if not snapshots:
        return {"rolling_sharpe": [], "rolling_volatility": [], "monthly_returns": {}}

    snapshots = sorted(snapshots, key=lambda s: s["trade_date"])

    # Daily returns
    daily_returns: list[float] = []
    for i, s in enumerate(snapshots):
        if i == 0:
            daily_returns.append(
                (float(s["total_asset"]) - initial_cash) / initial_cash
            )
        else:
            prev = float(snapshots[i - 1]["total_asset"])
            daily_returns.append(
                (float(s["total_asset"]) - prev) / prev if prev > 0 else 0.0
            )

    # Rolling Sharpe
    rolling_sharpe: list[dict[str, Any]] = []
    for i in range(window - 1, len(daily_returns)):
        window_returns = daily_returns[i - window + 1 : i + 1]
        arr = np.array(window_returns)
        mean = float(arr.mean())
        std = float(arr.std(ddof=1))
        sharpe = mean / std * math.sqrt(_TRADING_DAYS_PER_YEAR) if std > 0 else 0.0
        rolling_sharpe.append({
            "date": str(snapshots[i]["trade_date"]),
            "value": round(sharpe, 4),
        })

    # Rolling volatility
    rolling_vol: list[dict[str, Any]] = []
    for i in range(window - 1, len(daily_returns)):
        window_returns = daily_returns[i - window + 1 : i + 1]
        std = float(np.std(window_returns, ddof=1))
        ann_vol = std * math.sqrt(_TRADING_DAYS_PER_YEAR)
        rolling_vol.append({
            "date": str(snapshots[i]["trade_date"]),
            "value": round(ann_vol, 6),
        })

    # Monthly returns
    monthly: dict[str, list[float]] = {}
    for i, s in enumerate(snapshots):
        month_key = str(s["trade_date"])[:7]  # "2024-03"
        monthly.setdefault(month_key, [])
        monthly[month_key].append(daily_returns[i])

    monthly_returns = {
        k: round(sum(v), 6) for k, v in monthly.items()
    }

    return {
        "rolling_sharpe": rolling_sharpe,
        "rolling_volatility": rolling_vol,
        "monthly_returns": monthly_returns,
    }


# ---------------------------------------------------------------------------
# Stock attribution
# ---------------------------------------------------------------------------


def compute_attribution(
    trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按 symbol 聚合个股归因。"""
    if not trades:
        return []

    by_symbol: dict[str, dict[str, Any]] = {}
    for t in trades:
        sym = str(t.get("symbol", ""))
        if not sym:
            continue
        if sym not in by_symbol:
            by_symbol[sym] = {
                "symbol": sym,
                "realized_pnl": 0.0,
                "buy_count": 0,
                "sell_count": 0,
                "win_count": 0,
                "loss_count": 0,
                "total_holding_days": 0,
                "sell_holding_days_count": 0,
                "total_fee": 0.0,
                "total_trade_value": 0.0,
            }
        entry = by_symbol[sym]
        side = str(t.get("side", ""))
        pnl = float(t.get("realized_pnl") or 0)
        fee = float(t.get("total_fee", 0))
        val = float(t.get("trade_value", 0))

        if side == "BUY":
            entry["buy_count"] += 1
        elif side == "SELL":
            entry["sell_count"] += 1
            entry["realized_pnl"] += pnl
            if pnl > 0:
                entry["win_count"] += 1
            elif pnl < 0:
                entry["loss_count"] += 1
            hd = t.get("holding_days")
            if hd is not None:
                entry["total_holding_days"] += int(hd)
                entry["sell_holding_days_count"] += 1

        entry["total_fee"] += fee
        entry["total_trade_value"] += val

    # Compute derived fields
    total_realized_pnl = sum(e["realized_pnl"] for e in by_symbol.values())
    result: list[dict[str, Any]] = []
    for entry in by_symbol.values():
        avg_hd = (
            entry["total_holding_days"] / entry["sell_holding_days_count"]
            if entry["sell_holding_days_count"] > 0
            else 0.0
        )
        contribution = (
            entry["realized_pnl"] / total_realized_pnl
            if abs(total_realized_pnl) > 0.01
            else 0.0
        )
        result.append({
            "symbol": entry["symbol"],
            "realized_pnl": round(entry["realized_pnl"], 2),
            "buy_count": entry["buy_count"],
            "sell_count": entry["sell_count"],
            "win_count": entry["win_count"],
            "loss_count": entry["loss_count"],
            "avg_holding_days": round(avg_hd, 1),
            "total_fee": round(entry["total_fee"], 2),
            "contribution": round(contribution, 4),
        })

    # Sort by realized_pnl descending
    result.sort(key=lambda x: x["realized_pnl"], reverse=True)
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_metrics() -> dict[str, Any]:
    """空指标占位。"""
    return {
        "total_return": 0.0,
        "annualized_return": 0.0,
        "sharpe": 0.0,
        "sortino": 0.0,
        "max_drawdown": 0.0,
        "max_drawdown_start": None,
        "max_drawdown_end": None,
        "max_drawdown_recovery_days": None,
        "calmar": 0.0,
        "annualized_volatility": 0.0,
        "win_rate": 0.0,
        "pnl_ratio": 0.0,
        "expectancy": 0.0,
        "turnover": 0.0,
        "total_fee": 0.0,
        "fee_drag": 0.0,
        "trade_count": 0,
        "sell_count": 0,
        "avg_holding_days": 0.0,
        "stop_loss_count": 0,
        "stop_loss_pnl": 0.0,
        "total_days": 0,
        "initial_cash": 0.0,
        "final_asset": 0.0,
    }


def _annualized_sortino(daily_returns: list[float]) -> float:
    """年化 Sortino（下行标准差）。"""
    arr = np.array(daily_returns, dtype=float)
    if arr.size < 2:
        return 0.0
    downside = arr[arr < 0]
    if downside.size == 0:
        return float(arr.mean() / 1e-9 * math.sqrt(_TRADING_DAYS_PER_YEAR))
    downside_std = float(np.std(downside, ddof=1))
    if downside_std <= 0:
        return 0.0
    return float(arr.mean() / downside_std * math.sqrt(_TRADING_DAYS_PER_YEAR))


def _annualized_volatility(daily_returns: list[float]) -> float:
    """年化波动率。"""
    arr = np.array(daily_returns, dtype=float)
    if arr.size < 2:
        return 0.0
    std = float(arr.std(ddof=1))
    return std * math.sqrt(_TRADING_DAYS_PER_YEAR)


def _drawdown_period(
    cum_returns: list[float],
    snapshots: list[dict[str, Any]],
) -> tuple[str | None, str | None, int | None]:
    """最大回撤的起止日 + 修复天数。"""
    if not cum_returns or not snapshots:
        return None, None, None

    peak_val = cum_returns[0]
    peak_idx = 0
    max_dd = 0.0
    dd_start_idx = 0
    dd_end_idx = 0

    for i, r in enumerate(cum_returns):
        if r > peak_val:
            peak_val = r
            peak_idx = i
        dd = peak_val - r
        if dd > max_dd:
            max_dd = dd
            dd_start_idx = peak_idx
            dd_end_idx = i

    start_date = str(snapshots[dd_start_idx]["trade_date"]) if dd_start_idx < len(snapshots) else None
    end_date = str(snapshots[dd_end_idx]["trade_date"]) if dd_end_idx < len(snapshots) else None

    # Recovery: first date after dd_end_idx where cum_return >= peak at dd_start_idx
    recovery_days = None
    target = cum_returns[dd_start_idx]
    for j in range(dd_end_idx + 1, len(cum_returns)):
        if cum_returns[j] >= target:
            recovery_days = j - dd_start_idx
            break

    return start_date, end_date, recovery_days
