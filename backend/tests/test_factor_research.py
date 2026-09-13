"""因子研究模块（factor_research）核心计算回归 —— 合成数据，无 IO。

钉住所依赖的不变量：
1. 打分 rank→正态分位：方向翻转、±4 截断、NaN 保持、截面归一
2. TTM 口径：income=单季求和；cashflow=上年年报+本年累计−上年同期（实测口径，见 financials.py 头注释）
3. IC：完全单调 → 1，反向 → −1
4. Top-N 回测：首期换手 1（全额计费）、涨跌停外的成本按换手计
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.services.engine.factor_research import analysis
from backend.services.engine.factor_research.engine import rank_to_score
from backend.services.engine.factor_research.financials import (
    _ttm_single_quarter,
    _ttm_ytd,
)

SYMS = [f"{i:06d}.SZ" for i in range(10)]
DATES = pd.to_datetime(["2026-01-30", "2026-02-27", "2026-03-31", "2026-04-30"])


# ---------------------------------------------------------------------------
# 打分
# ---------------------------------------------------------------------------
def test_rank_to_score_direction_and_bounds():
    raw = pd.DataFrame({"f": [1.0, 2.0, 3.0, 4.0]}, index=["s1", "s2", "s3", "s4"])
    s_pos = rank_to_score(raw, 1)
    s_neg = rank_to_score(raw, -1)
    assert list(s_pos["f"]) == sorted(s_pos["f"])  # 正向：值大者分高
    assert np.allclose(s_pos.to_numpy(), -s_neg.to_numpy())
    assert s_pos.to_numpy().max() <= 4 and s_pos.to_numpy().min() >= -4
    assert abs(float(s_pos["f"].mean())) < 1e-9  # 截面均值 ≈ 0


def test_rank_to_score_nan_preserved():
    raw = pd.DataFrame({"f": [1.0, np.nan, 3.0]}, index=["s1", "s2", "s3"])
    s = rank_to_score(raw, 1)
    assert np.isnan(s.loc["s2", "f"])
    assert not np.isnan(s.loc["s1", "f"])


# ---------------------------------------------------------------------------
# TTM
# ---------------------------------------------------------------------------
def _q(idx: list[str], vals: list[float], col: str = "v") -> pd.DataFrame:
    return pd.DataFrame({col: vals}, index=pd.to_datetime(idx))


def test_ttm_single_quarter_sums_four_consecutive():
    df = _q(
        ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"],
        [10.0, 20.0, 30.0, 40.0, 5.0],
    )
    ttm = _ttm_single_quarter(df, ("v",))
    assert np.isnan(ttm["v"].iloc[2])  # 不足 4 季
    assert ttm["v"].iloc[3] == 100.0  # 10+20+30+40
    assert ttm["v"].iloc[4] == 95.0  # 20+30+40+5


def test_ttm_ytd_uses_prior_fy_and_same_quarter():
    # 茅台式 YTD：Q1=10, H1=30, 9M=50, FY=90；上年 FY=80、上年 Q1=8
    df = _q(
        ["2024-03-31", "2024-12-31", "2025-03-31", "2025-06-30"],
        [8.0, 80.0, 10.0, 30.0],
    )
    ttm = _ttm_ytd(df, ("v",))
    assert ttm["v"].iloc[1] == 80.0  # 年报即 TTM
    assert ttm["v"].iloc[2] == 80.0 + 10.0 - 8.0  # 82
    assert np.isnan(ttm["v"].iloc[0])  # 缺上年年报


# ---------------------------------------------------------------------------
# IC / 回测 / KPI
# ---------------------------------------------------------------------------
def _score_fwd(perfect: bool):
    rng = np.random.default_rng(3)
    scores = pd.DataFrame(
        rng.normal(size=(4, 30)), index=DATES, columns=[f"{i:04d}" for i in range(30)]
    )
    fwd = scores.copy()
    if not perfect:
        fwd = -fwd
    return scores, fwd


def test_ic_perfect_and_reversed():
    s, f = _score_fwd(perfect=True)
    ic = analysis.ic_series(s, f)
    assert ic.dropna().min() > 0.99
    s2, f2 = _score_fwd(perfect=False)
    ic2 = analysis.ic_series(s2, f2)
    assert ic2.dropna().max() < -0.99


def test_backtest_topn_first_period_cost_and_nav():
    scores = pd.DataFrame(
        [
            [3.0, 2.0, 1.0, 0.0],
            [3.0, 2.0, 1.0, 0.0],
            [3.0, 2.0, 1.0, 0.0],
            [np.nan] * 4,
        ],
        index=DATES,
        columns=["s1", "s2", "s3", "s4"],
    )
    fwd = pd.DataFrame(
        [
            [0.10, 0.05, 0.02, 0.01],
            [0.10, 0.05, 0.02, 0.01],
            [np.nan] * 4,
            [np.nan] * 4,
        ],
        index=DATES,
        columns=["s1", "s2", "s3", "s4"],
    )
    bt = analysis.backtest_topn(scores, fwd, top_n=2)
    # 首期：picks=[s1,s2]，turnover=1.0，成本 0.002；gross=(0.10+0.05)/2=0.075
    assert bt["turnover"].iloc[0] == 1.0
    exp_net = 0.075 - 0.002
    assert abs(bt["ret"].iloc[0] - exp_net) < 1e-9
    # 第二期持仓不变 → 换手 0、无成本
    assert bt["turnover"].iloc[1] == 0.0
    assert abs(bt["ret"].iloc[1] - 0.075) < 1e-9
    assert abs(bt["nav"].iloc[1] - (1 + exp_net) * (1 + 0.075)) < 1e-9


def test_kpi_basic():
    nav = pd.Series([1.0, 1.1, 1.05, 1.2], index=DATES)
    ret = nav.pct_change().fillna(0.0)
    k = analysis.kpi(ret, nav)
    assert k["n_months"] == 4
    assert k["max_drawdown"] is not None and 0 < k["max_drawdown"] < 0.1
    assert 0 <= k["win_rate"] <= 1


def test_leaderboard_ranks_by_composite():
    metrics = {
        "A": {"ic_mean": 0.10, "sharpe": 2.0, "annual_return": 0.3},
        "B": {"ic_mean": 0.01, "sharpe": 0.2, "annual_return": 0.05},
        "C": {"ic_mean": -0.09, "sharpe": 1.0, "annual_return": 0.1},  # 强反向
    }
    lb = analysis.leaderboard(metrics)
    assert lb[0]["code"] == "A" and lb[0]["rank"] == 1
    assert lb[0]["composite"] >= lb[-1]["composite"]
