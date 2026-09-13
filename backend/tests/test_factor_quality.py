"""因子质量度量（PFS 扰动保真度 / DH 多样性熵）单元测试。

被测模块在训练镜像侧：docker/training/data/factor_quality.py（纯 numpy/pandas，
经 importlib 按路径加载，与 test_training_factor_selection_report.py 同一模式）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
QUALITY_PY = ROOT / "docker" / "training" / "data" / "factor_quality.py"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_module():
    spec = importlib.util.spec_from_file_location("factor_quality_under_test", QUALITY_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fq = _load_module()


def _make_panel(
    n_days: int = 40,
    n_stocks: int = 300,
    seed: int = 7,
    factors: dict[str, np.ndarray] | None = None,
) -> pd.DataFrame:
    """构造训练风格的截面面板：trade_date × symbol × 若干特征。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    rows = []
    for d in dates:
        base = rng.normal(size=n_stocks)
        row = {"trade_date": d, "symbol": [f"s{i:04d}" for i in range(n_stocks)]}
        for name, gen in (factors or {}).items():
            row[name] = gen(base, rng, n_stocks)
        rows.append(pd.DataFrame(row))
    return pd.concat(rows, ignore_index=True)


# ─────────────────────────── PFS ───────────────────────────


def test_smooth_factor_keeps_ranking_under_noise() -> None:
    """连续平滑因子加噪后排名几乎不变：PFS 接近 1。"""
    df = _make_panel(factors={"smooth": lambda base, rng, n: base + rng.normal(0, 0.3, n)})
    out = fq.compute_pfs(df, ["smooth"])
    assert out["smooth"]["pfs"] is not None
    assert out["smooth"]["pfs"] > 0.97
    assert out["smooth"]["n_days"] > 0


def test_tie_heavy_factor_ranking_collapses() -> None:
    """大量并值的因子（如 5% 股票为 1、其余为 0）加噪后组内顺序完全随机会被打穿。"""
    def binary(base, rng, n):
        x = np.zeros(n)
        x[base.argsort()[-int(n * 0.05):]] = 1.0
        return x

    df = _make_panel(factors={
        "smooth": lambda base, rng, n: base,
        "tied": binary,
    })
    out = fq.compute_pfs(df, ["smooth", "tied"])
    assert out["tied"]["pfs"] is not None
    assert out["tied"]["pfs"] < 0.6
    assert out["smooth"]["pfs"] > out["tied"]["pfs"] + 0.3


def test_pfs_is_scale_invariant() -> None:
    """噪声加在截面 z 分上：因子整体乘 1000 不改变 PFS（尺度无关）。

    必须分两次单特征调用比较——同一次调用里各特征列拿到的是不同的噪声列
    （同一 draw 的不同列），结果本就不同。
    """
    df = _make_panel(factors={"f": lambda base, rng, n: base})
    scaled = df[["trade_date", "symbol", "f"]].copy()
    scaled["f"] = scaled["f"] * 1000.0
    a = fq.compute_pfs(df, ["f"])
    b = fq.compute_pfs(scaled, ["f"])
    assert a["f"]["pfs"] == pytest.approx(b["f"]["pfs"], abs=1e-9)


def test_pfs_deterministic_with_seed() -> None:
    df = _make_panel(factors={"f": lambda base, rng, n: base})
    a = fq.compute_pfs(df, ["f"], seed=42)
    b = fq.compute_pfs(df, ["f"], seed=42)
    c = fq.compute_pfs(df, ["f"], seed=43)
    assert a["f"]["pfs"] == b["f"]["pfs"]
    assert a["f"]["pfs"] != c["f"]["pfs"]


def test_pfs_t_variant_is_worst_case_min() -> None:
    """PFS 取高斯与 t 厚尾两个变体的最小值。"""
    df = _make_panel(factors={"f": lambda base, rng, n: base})
    out = fq.compute_pfs(df, ["f"])
    r = out["f"]
    assert r["pfs"] == min(r["pfs_gauss"], r["pfs_t"])


def test_pfs_none_when_too_few_days() -> None:
    df = _make_panel(n_days=10, factors={"f": lambda base, rng, n: base})
    out = fq.compute_pfs(df, ["f"], min_days=20)
    assert out["f"]["pfs"] is None
    assert out["f"]["n_days"] == 10


def test_pfs_none_for_all_nan_factor() -> None:
    df = _make_panel(factors={"f": lambda base, rng, n: base})
    df["dead"] = np.nan
    out = fq.compute_pfs(df, ["f", "dead"])
    assert out["dead"]["pfs"] is None
    assert out["f"]["pfs"] is not None


def test_pfs_skips_missing_columns_and_bad_input() -> None:
    df = _make_panel(factors={"f": lambda base, rng, n: base})
    assert fq.compute_pfs(df, ["f", "not_in_df"]).keys() == {"f"}
    assert fq.compute_pfs(df.drop(columns=["trade_date"]), ["f"]) == {}


def test_pfs_thin_cross_section_days_excluded() -> None:
    """单日有效股票不足 min_stocks 的交易日不参与均值。"""
    df = _make_panel(n_days=30, n_stocks=300, factors={"f": lambda base, rng, n: base})
    dates = sorted(df["trade_date"].unique())
    for d in dates[:15]:  # 前 15 天只留 10 只股票
        df.loc[df["trade_date"] == d, "f"] = np.where(
            df.loc[df["trade_date"] == d, "symbol"].isin([f"s{i:04d}" for i in range(10)]),
            df.loc[df["trade_date"] == d, "f"],
            np.nan,
        )
    out = fq.compute_pfs(df, ["f"])
    assert out["f"]["n_days"] == 15  # 只剩后半段 15 天有效


def test_pfs_samples_days_when_panel_too_long() -> None:
    """超过 max_days 时等距抽样：n_days 反映抽样后的天数（回归：pandas 3 下
    pd.unique 返回 datetime64、按值建集合会静默全部命中不了 → PFS 恒 None）。"""
    df = _make_panel(n_days=200, n_stocks=60, factors={"f": lambda base, rng, n: base})
    out = fq.compute_pfs(df, ["f"], max_days=50)
    assert out["f"]["n_days"] == 50
    assert out["f"]["pfs"] is not None


# ─────────────────────────── DH / 有效因子数 ───────────────────────────


def test_entropy_and_effective_count_for_orthogonal_factors() -> None:
    corr = np.eye(5)
    assert fq.diversity_entropy(corr) == pytest.approx(1.0)
    assert fq.effective_factors(corr) == pytest.approx(5.0)


def test_entropy_collapses_for_redundant_factors() -> None:
    corr = np.ones((4, 4))  # 完全共线
    assert fq.diversity_entropy(corr) == pytest.approx(0.0, abs=1e-9)
    assert fq.effective_factors(corr) == pytest.approx(1.0, abs=1e-9)


def test_effective_factors_two_factor_rho_half() -> None:
    corr = np.array([[1.0, 0.5], [0.5, 1.0]])
    assert fq.effective_factors(corr) == pytest.approx(1.7549, abs=1e-3)
    assert fq.diversity_entropy(corr) == pytest.approx(0.8113, abs=1e-3)


def test_effective_factors_gain_gates_redundant_addition() -> None:
    """贪心增益语义：正交新增 ≈ +1，冗余新增 ≈ 0。"""
    base = np.eye(6)
    add_orth = np.eye(7)
    gain_orth = fq.effective_factors(add_orth) - fq.effective_factors(base)
    assert gain_orth == pytest.approx(1.0, abs=1e-9)

    dup = np.eye(6)
    dup[0, 5] = dup[5, 0] = 0.999  # 第 7 个因子 ≈ 第 1 个的复制
    gain_dup = fq.effective_factors(dup) - fq.effective_factors(base)
    assert gain_dup < 0.1


def test_entropy_rejects_invalid_matrices() -> None:
    assert fq.diversity_entropy(np.eye(1)) is None
    assert fq.effective_factors([[1.0, np.nan], [np.nan, 1.0]]) is None
    assert fq.effective_factors(np.eye(3)[:, :2]) is None  # 非方阵
