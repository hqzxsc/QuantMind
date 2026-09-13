"""训练侧因子筛选质量闸门（PFS 扰动保真度 / DH 多样性增益）运行时回归。

被测模块 docker/training/data/factor_selection.py 依赖包内导入
（``from data.factor_quality import ...``），按训练容器的目录布局加载：
把 docker/training 作为 sys.path 的 ``data`` 包根，显式注册后 exec_module。

覆盖：
  - 关闭两道闸门 = 老行为（多重共线因子照常入选）；
  - DH 闸门拒绝「与多只同时中等相关」的因子（两两阈值看不见的盲区）；
  - PFS 闸门把加噪后排名塌掉的因子换掉，报告给出明确原因；
  - 淘汰过多时回退老名单（fail-safe），报告标记 pfs_fallback；
  - 报告新增键（pfs/dh_gain/diversity/stage_counts.pfs_pass）齐备且漏斗单调。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
TRAINING_DATA = ROOT / "docker" / "training" / "data"


def _load_training_modules():
    """按训练容器布局加载 data.factor_quality / data.factor_selection。"""
    if "data" not in sys.modules:
        pkg = types.ModuleType("data")
        pkg.__path__ = [str(TRAINING_DATA)]  # type: ignore[attr-defined]
        sys.modules["data"] = pkg
    for name in ("factor_quality", "factor_selection"):
        full = f"data.{name}"
        if full in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(full, TRAINING_DATA / f"{name}.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
    return sys.modules["data.factor_selection"]


fs = _load_training_modules()


def _panel(n_days: int = 30, n_stocks: int = 200, n_smooth: int = 32, seed: int = 11) -> tuple[pd.DataFrame, dict]:
    """合成截面面板：n_smooth 个平滑因子 + 1 个与 label 强相关但大量并值的因子。

    n_smooth 默认 32：PFS 闸门有「存活 < 30 就回退」的 fail-safe，面板太小会走
    回退分支（另见 test_pfs_fallback_when_gate_too_strict）。
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    frames = []
    for d in dates:
        base = rng.normal(size=(n_stocks, n_smooth))
        label = base.mean(axis=1) + rng.normal(0, 0.3, n_stocks)
        row = {"trade_date": d, "symbol": [f"s{i:04d}" for i in range(n_stocks)], "label": label}
        for j in range(n_smooth):
            row[f"good_{j:02d}"] = base[:, j] + rng.normal(0, 0.3, n_stocks)
        # 并值因子：label 排名前 5% 记 1，其余 0（IC 强但排名一扰就乱）
        tied = np.zeros(n_stocks)
        tied[np.argsort(label)[-max(3, int(n_stocks * 0.05)):]] = 1.0
        row["tied_flag"] = tied
        frames.append(pd.DataFrame(row))
    df = pd.concat(frames, ignore_index=True)
    return df, {"smooth": [f"good_{j:02d}" for j in range(n_smooth)], "tied": "tied_flag"}


def _collinear_panel(n_days: int = 30, n_stocks: int = 300, seed: int = 5) -> pd.DataFrame:
    """A、B、D 相互正交且都驱动 label；C = 0.6A + 0.6B − 0.53D。

    C 与 A、B 各 ρ≈0.6（两两阈值 0.9 放行），对 label 的载荷被 −D 抵消
    （IC 低于 A/B）→ 排序上必然排在 A、B、D 之后，评估时已选集合已覆盖 C 的
    生成空间（多重共线），DH 增益 ≈ 1 − 2ρ² ≈ 0.28。
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    frames = []
    for d in dates:
        a = rng.normal(size=n_stocks)
        b = rng.normal(size=n_stocks)
        dd = rng.normal(size=n_stocks)
        c = 0.6 * a + 0.6 * b - np.sqrt(1 - 0.72) * dd
        label = a + b + dd + rng.normal(0, 0.2, n_stocks)
        frames.append(pd.DataFrame({
            "trade_date": d, "symbol": [f"s{i:04d}" for i in range(n_stocks)],
            "fA": a, "fB": b, "fD": dd, "fC": c, "label": label,
        }))
    return pd.concat(frames, ignore_index=True)


def _medium_panel(n_days: int = 30, n_stocks: int = 300, seed: int = 6) -> pd.DataFrame:
    """A、B、D 正交驱动 label；C = 0.75A + 0.66E（E 独立）→ 只与 A 相关 ρ≈0.75。

    中等冗余（增益 ≈ 0.4）：默认 0.1 放行，收紧到 0.5 时拒绝——用于验证闸门
    阈值可调；IC 低于 A/B 保证排序上 C 最后评估。
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    frames = []
    for d in dates:
        a = rng.normal(size=n_stocks)
        b = rng.normal(size=n_stocks)
        dd = rng.normal(size=n_stocks)
        e = rng.normal(size=n_stocks)
        c = 0.75 * a + np.sqrt(1 - 0.5625) * e
        label = a + b + dd + rng.normal(0, 0.2, n_stocks)
        frames.append(pd.DataFrame({
            "trade_date": d, "symbol": [f"s{i:04d}" for i in range(n_stocks)],
            "fA": a, "fB": b, "fD": dd, "fC": c, "label": label,
        }))
    return pd.concat(frames, ignore_index=True)


def _select(df: pd.DataFrame, **kw):
    feats = [c for c in df.columns if c not in ("trade_date", "symbol", "label")]
    defaults = {"label_col": "label", "n_top": len(feats)}
    defaults.update(kw)
    return fs.select_top_factors(df, feats, **defaults)


# ─────────────────────── DH 多样性增益闸门 ───────────────────────


def test_gates_off_reproduces_legacy_selection() -> None:
    """两道闸门关闭：fC 通过两两相关阈值（maxρ≈0.6 < 0.9）照常入选 —— 老行为。"""
    df = _collinear_panel()
    selected, report = _select(df, pfs_enabled=False, dh_enabled=False)
    assert set(selected) == {"fA", "fB", "fD", "fC"}
    assert report["thresholds"]["dh_min_gain"] is None
    assert report["stage_counts"]["dh_rejected"] == 0


def test_diversity_gate_rejects_multicollinear_addition_by_default() -> None:
    """多重共线：fC 与已选的 A、B、D 各相关 0.6/0.6/−0.53（两两阈值全放行），
    但被三者联合张成、无独立信息 → 默认闸门（min_gain=0.1）就拒绝并给出原因。"""
    df = _collinear_panel()
    selected, report = _select(df, pfs_enabled=False, dh_enabled=True)
    assert set(selected) == {"fA", "fB", "fD"}
    assert report["stage_counts"]["dh_rejected"] == 1
    row = next(f for f in report["features"] if f["name"] == "fC")
    assert row["status"] == "rejected"
    assert "多样性增益不足" in row["reason"]
    assert row["dh_gain"] is not None and row["dh_gain"] < 0.1
    # 入选集合的 dh_gain 有记录
    assert any(f["dh_gain"] is not None for f in report["features"] if f["status"] == "selected")


def test_diversity_gate_threshold_is_configurable() -> None:
    """中等冗余（与已选之一 ρ≈0.75，增益≈0.4）：默认放行，收紧阈值后拒绝。"""
    df = _medium_panel()
    selected, report = _select(df, pfs_enabled=False, dh_enabled=True)
    assert "fC" in selected  # 默认 min_gain=0.1 放行
    row = next(f for f in report["features"] if f["name"] == "fC")
    assert row["dh_gain"] is not None and 0.1 < row["dh_gain"] < 0.5

    selected_strict, report_strict = _select(df, pfs_enabled=False, dh_enabled=True, dh_min_gain=0.5)
    assert "fC" not in selected_strict
    assert report_strict["stage_counts"]["dh_rejected"] == 1


def test_diversity_gate_keeps_orthogonal_factor() -> None:
    """正交因子不受 DH 闸门影响（增益≈1），任何阈值下都不误伤。"""
    df = _collinear_panel()
    rng = np.random.default_rng(3)
    df["fE"] = rng.normal(size=len(df))  # 与所有已选正交
    selected, report = _select(df, pfs_enabled=False, dh_enabled=True, dh_min_gain=0.5)
    assert "fE" in selected
    row = next(f for f in report["features"] if f["name"] == "fE")
    assert row["status"] == "selected"


# ─────────────────────── PFS 扰动保真度闸门 ───────────────────────


def test_pfs_gate_swaps_fragile_factor() -> None:
    """加噪后排名塌掉的并值因子被换掉，报告给出 PFS 原因；关闭闸门则照常入选。"""
    df, cols = _panel()
    selected_on, report_on = _select(df, pfs_enabled=True, dh_enabled=False)
    assert cols["tied"] not in selected_on
    row = next(f for f in report_on["features"] if f["name"] == cols["tied"])
    assert row["status"] == "rejected"
    assert "扰动保真度不足" in row["reason"]
    assert row["pfs"] is not None and row["pfs"] < 0.9

    selected_off, _ = _select(df, pfs_enabled=False, dh_enabled=False)
    assert cols["tied"] in selected_off


def test_pfs_values_present_for_selected_rows() -> None:
    """入选项在报告里带上 PFS 明细（pfs / pfs_gauss / pfs_t），平滑因子高分。"""
    df, cols = _panel()
    _, report = _select(df, pfs_enabled=True, dh_enabled=False)
    sel_rows = [f for f in report["features"] if f["status"] == "selected"]
    assert sel_rows
    for f in sel_rows:
        assert f["pfs"] is not None
        assert f["pfs"] == min(f["pfs_gauss"], f["pfs_t"])
    smooth = [f for f in sel_rows if f["name"].startswith("good_")]
    assert smooth and all(f["pfs"] > 0.9 for f in smooth)


def test_pfs_fallback_when_gate_too_strict() -> None:
    """所有因子都是并值型时 PFS 全灭 → 回退老名单（fail-safe），报告标记 pfs_fallback。"""
    rng = np.random.default_rng(21)
    dates = pd.date_range("2024-01-01", periods=30, freq="B")
    frames = []
    for d in dates:
        label = rng.normal(size=200)
        row = {"trade_date": d, "symbol": [f"s{i:04d}" for i in range(200)], "label": label}
        for j in range(12):
            # 每个因子：独立噪声决定 label 排名前 5% 标记（彼此相关度低、IC 强、全并值）
            score = label + rng.normal(0, 1.5, 200)
            v = np.zeros(200)
            v[np.argsort(score)[-10:]] = 1.0
            row[f"flag_{j:02d}"] = v
        frames.append(pd.DataFrame(row))
    df = pd.concat(frames, ignore_index=True)
    on, report_on = _select(df, pfs_enabled=True, dh_enabled=False)
    off, _ = _select(df, pfs_enabled=False, dh_enabled=False)
    assert report_on["pfs_fallback"] is True
    assert set(on) == set(off)


# ─────────────────────── 报告结构 / 漏斗 ───────────────────────


def test_report_new_keys_and_monotone_funnel() -> None:
    df, _ = _panel()
    _, report = _select(df, pfs_enabled=True, dh_enabled=True)
    sc = report["stage_counts"]
    for key in ("input", "ic_pass", "corr_pass", "stable", "pfs_pass", "selected", "dh_rejected"):
        assert key in sc, f"stage_counts 缺 {key}"
    assert sc["input"] >= sc["ic_pass"] >= sc["corr_pass"] >= sc["stable"] >= sc["pfs_pass"] >= sc["selected"]
    thr = report["thresholds"]
    assert thr["pfs_threshold"] == 0.9 and thr["dh_min_gain"] == 0.1
    assert "pfs_fallback" in report
    div = report["diversity"]
    assert div and div["n_factors"] == len(report["selected"])
    # 老键仍在（前端兼容）
    for key in ("method", "train_rows", "backfilled_features"):
        assert key in report
    for f in report["features"]:
        for key in ("pfs", "pfs_gauss", "pfs_t", "dh_gain"):
            assert key in f


def test_defaults_are_gates_on() -> None:
    """默认参数即开启两道闸门（不传参数时生效）。"""
    df = _collinear_panel()
    feats = [c for c in df.columns if c not in ("trade_date", "symbol", "label")]
    _, report = fs.select_top_factors(df, feats, n_top=len(feats))
    assert report["thresholds"]["pfs_threshold"] == 0.9
    assert report["thresholds"]["dh_min_gain"] == 0.1
    assert report["thresholds"]["pfs_sigma"] == 0.1
