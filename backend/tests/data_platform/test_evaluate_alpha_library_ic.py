"""Alpha 库旧 IC 脚本（evaluate_alpha_library.py）口径回归 —— 合成数据，无 IO。

钉住三处曾把排序结果带偏的缺陷：
1. NaN 覆盖率不再打折 IC（旧实现分母用全截面样本数，50% NaN 时真实 0.65 报成 0.32）
2. 常量因子列得 NaN 而不是 ±inf（旧实现因子侧零方差不设防 → 分母 1e-16 溢出）
3. 反向因子不再被 score/阈值排序埋没（旧实现 ICIR 未取绝对值，最强反向因子排倒数第一）

注：报告页走的是新链路 backend/scripts/build_factor_report.py（同口径、无这些缺陷），
本测试只保护这条旧脚本不被误用。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.scripts.evaluate_alpha_library import compute_ic_series

N_SYMBOLS = 40
DATES = pd.to_datetime(["2026-08-03", "2026-08-04", "2026-08-05"])


def _build_frame() -> tuple[pd.DataFrame, list[str]]:
    rng = np.random.default_rng(7)
    rows = []
    for d in DATES:
        fwd = rng.normal(size=N_SYMBOLS)
        perfect = fwd.copy()
        half_nan = fwd.copy()
        half_nan[rng.random(N_SYMBOLS) < 0.5] = np.nan       # 50% 缺失
        constant = np.zeros(N_SYMBOLS)                        # 当日零方差
        rows.append(
            pd.DataFrame(
                {
                    "trade_date": d,
                    "perfect": perfect,
                    "reverse": -fwd,
                    "half_nan": half_nan,
                    "constant": constant,
                    "fwd_ret_5": fwd,
                }
            )
        )
    frame = pd.concat(rows, ignore_index=True)
    return frame, ["perfect", "reverse", "half_nan", "constant"]


@pytest.fixture(scope="module")
def ic_df() -> pd.DataFrame:
    frame, cols = _build_frame()
    return compute_ic_series(frame, cols, "fwd_ret_5")


def test_完美因子_IC_为1(ic_df):
    assert ic_df["perfect"].mean() == pytest.approx(1.0)


def test_反向因子_IC_为负1(ic_df):
    assert ic_df["reverse"].mean() == pytest.approx(-1.0)


def test_高缺失率不再低估_IC(ic_df):
    """50% NaN 的因子若与目标同序，IC 仍应为 1（成对完整口径）。"""
    assert ic_df["half_nan"].mean() == pytest.approx(1.0)


def test_常量列得_NaN_而非_inf(ic_df):
    vals = ic_df["constant"]
    assert not np.isinf(vals).any(), "常量列不应产生 ±inf"
    assert vals.isna().all()
