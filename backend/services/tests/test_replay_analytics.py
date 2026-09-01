"""R4 统计报告：算术对账测试（不只测跑通，要独立验证数字正确性）。

验证规则（见 docs/replay/REPLAY_R3_R5_PLAN.md）：
- sum(个股归因盈亏) == sum(trades.realized_pnl) 差 <0.01
- 净值曲线末值 == final_total_asset
- cum_return 末值 == 总收益率
- 手工构造已知序列验证夏普/回撤
"""

from datetime import date
from typing import Any

from backend.services.simulation.replay.analytics import (
    compute_attribution,
    compute_core_metrics,
    compute_nav_curve,
    compute_rolling_metrics,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snap(
    d: str,
    total: float,
    cash: float = 0.0,
    realized_cum: float = 0.0,
    unrealized: float = 0.0,
) -> dict[str, Any]:
    return {
        "trade_date": d,
        "total_asset": total,
        "cash": cash,
        "market_value": total - cash,
        "day_pnl": 0.0,
        "cum_pnl": total - 1_000_000,
        "realized_pnl_cum": realized_cum,
        "unrealized_pnl": unrealized,
        "position_count": 0,
    }


def _trade(
    sym: str,
    side: str,
    qty: float = 100.0,
    px: float = 10.0,
    pnl: float | None = None,
    cost: float | None = None,
    fee: float = 0.0,
    holding_days: int | None = None,
    origin: str = "signal",
    d: str = "2024-03-04",
) -> dict[str, Any]:
    return {
        "id": 1,
        "trade_date": d,
        "symbol": sym,
        "side": side,
        "origin": origin,
        "quantity": qty,
        "price": px,
        "trade_value": qty * px,
        "total_fee": fee,
        "realized_pnl": pnl,
        "avg_cost_before": cost,
        "holding_days": holding_days,
    }


# ---------------------------------------------------------------------------
# NAV 末值与总收益
# ---------------------------------------------------------------------------


def test_nav_curve_final_value_matches_snapshot():
    """净值曲线末值应等于 final_total_asset。"""
    snaps = [
        _snap("2024-03-01", 1_000_000.0),
        _snap("2024-03-04", 1_010_000.0),
        _snap("2024-03-05", 1_020_000.0),
    ]
    initial = 1_000_000.0
    curve = compute_nav_curve(snaps, initial)

    assert len(curve) == 3
    assert curve[-1]["total_asset"] == 1_020_000.0
    assert abs(curve[-1]["nav"] - 1.02) < 1e-6
    assert abs(curve[-1]["cum_return"] - 0.02) < 1e-6


def test_total_return_matches_curve():
    """metrics.total_return == curve[-1].cum_return。"""
    snaps = [
        _snap("2024-03-01", 1_000_000.0),
        _snap("2024-03-04", 1_050_000.0),
    ]
    trades: list[dict[str, Any]] = []
    initial = 1_000_000.0

    metrics = compute_core_metrics(snaps, trades, initial)
    curve = compute_nav_curve(snaps, initial)

    assert abs(metrics["total_return"] - curve[-1]["cum_return"]) < 1e-6
    assert abs(metrics["total_return"] - 0.05) < 1e-6
    assert metrics["final_asset"] == 1_050_000.0


# ---------------------------------------------------------------------------
# 个股归因汇总
# ---------------------------------------------------------------------------


def test_attribution_pnl_sums_match_trades():
    """sum(attribution.realized_pnl) == sum(sell_trades.realized_pnl)。"""
    trades = [
        _trade("AAPL", "BUY", qty=100, px=10, d="2024-03-01"),
        _trade("AAPL", "SELL", qty=100, px=12, pnl=200, cost=10, d="2024-03-05"),
        _trade("GOOG", "BUY", qty=50, px=20, d="2024-03-01"),
        _trade("GOOG", "SELL", qty=50, px=18, pnl=-100, cost=20, d="2024-03-05"),
        _trade("MSFT", "SELL", qty=30, px=15, pnl=50, cost=14, d="2024-03-05"),
    ]
    attrs = compute_attribution(trades)

    sum_attr = sum(a["realized_pnl"] for a in attrs)
    sum_trades = sum(
        t["realized_pnl"] for t in trades if t["side"] == "SELL"
    )
    assert abs(sum_attr - sum_trades) < 0.01
    assert len(attrs) == 3
    assert attrs[0]["symbol"] == "AAPL"  # 按盈亏降序


def test_attribution_contribution_sums_to_one():
    """contribution 之和应等于 1（仅在有盈亏时）。"""
    trades = [
        _trade("A", "SELL", pnl=300, cost=10, d="2024-03-05"),
        _trade("B", "SELL", pnl=200, cost=10, d="2024-03-05"),
        _trade("C", "SELL", pnl=500, cost=10, d="2024-03-05"),
    ]
    attrs = compute_attribution(trades)
    total_contrib = sum(a["contribution"] for a in attrs)
    assert abs(total_contrib - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# 已知序列验证夏普/回撤
# ---------------------------------------------------------------------------


def test_known_sequence_max_drawdown():
    """手工构造单峰回撤序列，验证回撤计算。

    净值: 1.0, 1.2, 0.9, 1.1
    cum_return: 0.0, 0.2, -0.05, 0.1722
    peak cum_return = 0.2, 最低 = -0.05
    max_dd = (0.2 - (-0.05)) / (1 + 0.2) = 0.2083... (相对回撤)
    """
    snaps = [
        _snap("2024-03-01", 100_000.0),
        _snap("2024-03-04", 120_000.0),
        _snap("2024-03-05", 90_000.0),
        _snap("2024-03-06", 110_000.0),
    ]
    initial = 100_000.0

    metrics = compute_core_metrics(snaps, [], initial)
    assert abs(metrics["max_drawdown"] - 0.208333) < 0.001


def test_zero_volatility_returns_zero_sharpe():
    """收益序列为 0 时 Sharpe 应为 0（不除零崩溃）。"""
    snaps = [
        _snap(f"2024-03-{d:02d}", 1_000_000.0)
        for d in range(1, 6)
    ]
    metrics = compute_core_metrics(snaps, [], 1_000_000.0)

    assert metrics["sharpe"] == 0.0
    assert metrics["annualized_volatility"] == 0.0


def test_no_trades_zero_win_rate():
    """无交易时胜率/盈亏比/期望为 0（不除零）。"""
    snaps = [_snap("2024-03-01", 1_000_000.0)]
    metrics = compute_core_metrics(snaps, [], 1_000_000.0)

    assert metrics["win_rate"] == 0.0
    assert metrics["pnl_ratio"] == 0.0
    assert metrics["expectancy"] == 0.0


def test_empty_inputs():
    """空 snapshots + trades 返回空指标。"""
    metrics = compute_core_metrics([], [], 1_000_000.0)
    assert metrics["total_days"] == 0
    assert metrics["total_return"] == 0.0

    curve = compute_nav_curve([], 1_000_000.0)
    assert curve == []

    attrs = compute_attribution([])
    assert attrs == []


# ---------------------------------------------------------------------------
# 滚动指标
# ---------------------------------------------------------------------------


def test_rolling_metrics_window_size():
    """滚动窗口大小：第 19 个数据点之后才有第一个 rolling 值（窗口=20）。"""
    snaps = [_snap(f"2024-03-{(d % 28) + 1:02d}", 1_000_000.0 + d * 1000) for d in range(1, 31)]
    initial = 1_000_000.0

    rolling = compute_rolling_metrics(snaps, initial, window=20)
    assert len(rolling["rolling_sharpe"]) == 11  # 30 - 20 + 1
    assert len(rolling["rolling_volatility"]) == 11


def test_monthly_returns_grouped():
    """月度收益按 YYYY-MM 分组累加。"""
    snaps = [
        _snap("2024-03-01", 1_010_000.0),
        _snap("2024-03-04", 1_020_000.0),
        _snap("2024-04-01", 1_030_000.0),
        _snap("2024-04-04", 1_050_000.0),
    ]
    initial = 1_000_000.0

    rolling = compute_rolling_metrics(snaps, initial)
    assert "2024-03" in rolling["monthly_returns"]
    assert "2024-04" in rolling["monthly_returns"]
    # Returns are relative: 0.01 + 0.0099 ≈ 0.0199 for March
    assert abs(rolling["monthly_returns"]["2024-03"] - 0.019901) < 0.001
    assert abs(rolling["monthly_returns"]["2024-04"] - 0.029221) < 0.001


# ---------------------------------------------------------------------------
# 止损归因
# ---------------------------------------------------------------------------


def test_stop_loss_pnl_separated():
    """止损单独立统计，不混入普通信号。"""
    trades = [
        _trade("A", "SELL", pnl=200, cost=10, origin="signal", d="2024-03-01"),
        _trade("B", "SELL", pnl=-300, cost=10, origin="stop_loss", d="2024-03-01"),
    ]
    snaps = [_snap("2024-03-01", 1_000_000.0)]
    metrics = compute_core_metrics(snaps, trades, 1_000_000.0)

    assert metrics["stop_loss_count"] == 1
    assert metrics["stop_loss_pnl"] == -300.0


# ---------------------------------------------------------------------------
# Fee drag
# ---------------------------------------------------------------------------


def test_fee_drag_calculation():
    """fee_drag = total_fee / initial_cash。"""
    trades = [
        _trade("A", "BUY", qty=100, px=10, fee=10, d="2024-03-01"),
        _trade("A", "SELL", qty=100, px=11, pnl=90, cost=10, fee=10, d="2024-03-05"),
    ]
    snaps = [
        _snap("2024-03-01", 1_000_000.0),
        _snap("2024-03-05", 1_000_090.0),
    ]
    metrics = compute_core_metrics(snaps, trades, 1_000_000.0)

    assert metrics["total_fee"] == 20.0
    assert abs(metrics["fee_drag"] - 0.00002) < 1e-9
