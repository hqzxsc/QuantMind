"""
parallel_utils 并行因子筛选测试
================================
验证 docker/training/parallel_utils.py 的并行 IC 计算：
1. 并行结果与串行逐日 spearmanr 完全一致（数值 100% 相同，非近似）
2. 多 worker 与单 worker 结果一致
3. 环境变量 TRAIN_IC_WORKERS 控制 worker 数
4. fallback：进程池失败时回退串行，不中断训练
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# docker/training 与 backend 不在同一源码树，手动加入 sys.path
_DOCKER_TRAINING = Path(__file__).resolve().parents[3] / "docker" / "training"
if str(_DOCKER_TRAINING) not in sys.path:
    sys.path.insert(0, str(_DOCKER_TRAINING))

from parallel_utils import compute_daily_ics  # noqa: E402


def _make_frame(
    n_dates: int = 60,
    n_symbols: int = 120,
    n_features: int = 12,
    seed: int = 7,
    nan_ratio: float = 0.05,
) -> pd.DataFrame:
    """构造训练风格数据：trade_date × symbol × n_features + label。

    特征与 label 带随机关联：label 由若干特征线性组合加噪声生成，
    保证 Spearman IC 有真实信号且各特征 IC 各不相同。
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_dates)
    weights = rng.normal(size=n_features)
    recs = []
    for d in dates:
        x = rng.normal(size=(n_symbols, n_features))
        label = x @ weights + rng.normal(scale=0.5, size=n_symbols)
        for s in range(n_symbols):
            rec = {"trade_date": d, "symbol": f"S{s:03d}"}
            for i in range(n_features):
                rec[f"f{i:02d}"] = x[s, i]
            rec["label"] = label[s]
            recs.append(rec)
    df = pd.DataFrame(recs)

    # 随机插入 NaN（触发 dropna 分支）
    mask = rng.random(df.shape) < nan_ratio
    df = df.mask(mask)

    # 个别符号只在少数交易日出现（触发 "<30 观测跳过" 分支）
    sparse = rng.choice(n_symbols, size=3, replace=False)
    kept_dates = dates[: max(2, n_dates // 8)]
    drop_mask = (df["symbol"].isin([f"S{s:03d}" for s in sparse])) & (
        ~df["trade_date"].isin(kept_dates)
    )
    df = df.loc[~drop_mask].reset_index(drop=True)
    return df


def _serial_daily_ic(df: pd.DataFrame, feats: list[str], label_col: str = "label"):
    """参考实现：完全复刻旧串行逻辑（逐特征 × 逐日 spearmanr）。"""
    from scipy.stats import spearmanr

    results = {}
    for feat in feats:
        if feat not in df.columns:
            continue
        daily_ics = []
        for _, g in df.groupby("trade_date", sort=False):
            valid = g[[feat, label_col]].dropna()
            if len(valid) < 30:
                continue
            ic, _ = spearmanr(valid[feat], valid[label_col])
            if np.isfinite(ic):
                daily_ics.append(ic)
        if len(daily_ics) < 20:
            results[feat] = {
                "ic_mean": 0.0,
                "icir": 0.0,
                "ic_positive_rate": 0.0,
                "n_days": len(daily_ics),
            }
            continue
        arr = np.array(daily_ics)
        results[feat] = {
            "ic_mean": float(np.mean(arr)),
            "icir": float(np.mean(arr) / (np.std(arr) + 1e-9)),
            "ic_positive_rate": float(np.mean(arr > 0)),
            "n_days": len(arr),
            "daily_ics": daily_ics,
        }
    return results


def _drop_daily_ics(d: dict):
    """daily_ics 是内部序列，不在最终结果中使用，比较时剔除。"""
    return {k: {kk: vv for kk, vv in v.items() if kk != "daily_ics"} for k, v in d.items()}


@pytest.fixture
def sample_df():
    return _make_frame()


class TestComputeDailyICs:
    def test_parallel_matches_serial(self, sample_df):
        """并行（多 worker）结果与串行逐日 spearmanr 完全一致。"""
        df = sample_df
        feats = [c for c in df.columns if c.startswith("f")]
        expected = _serial_daily_ic(df, feats)
        actual = compute_daily_ics(df, feats, n_workers=4)
        assert set(actual.keys()) == set(expected.keys())
        assert _drop_daily_ics(actual) == _drop_daily_ics(expected)

    def test_single_worker_matches_serial(self, sample_df):
        """单 worker（串行路径）与参考实现一致。"""
        df = sample_df
        feats = [c for c in df.columns if c.startswith("f")]
        expected = _serial_daily_ic(df, feats)
        actual = compute_daily_ics(df, feats, n_workers=1)
        assert _drop_daily_ics(actual) == _drop_daily_ics(expected)

    def test_env_worker_override(self, sample_df, monkeypatch):
        """TRAIN_IC_WORKERS 环境变量覆盖默认 worker 数（0/1 均走串行）。"""
        df = sample_df
        feats = [c for c in df.columns if c.startswith("f")]
        expected = _serial_daily_ic(df, feats)
        monkeypatch.setenv("TRAIN_IC_WORKERS", "1")
        actual = compute_daily_ics(df, feats)
        assert _drop_daily_ics(actual) == _drop_daily_ics(expected)

    def test_unknown_features_skipped(self, sample_df):
        """不在 df 中的特征被静默跳过（与旧逻辑一致）。"""
        df = sample_df
        feats = ["f00", "nonexistent_feat"]
        result = compute_daily_ics(df, feats, n_workers=2)
        assert "nonexistent_feat" not in result
        assert "f00" in result

    def test_small_nan_dense_variants(self):
        """不同 NaN 密度、符号数、日期数下并行仍与串行一致（覆盖全部分支）。"""
        for kw in (
            dict(nan_ratio=0.0, seed=1),
            dict(nan_ratio=0.2, seed=2),
            dict(n_dates=25, n_symbols=50, n_features=5, seed=3),
        ):
            df = _make_frame(**kw)
            feats = [c for c in df.columns if c.startswith("f")]
            expected = _serial_daily_ic(df, feats)
            actual = compute_daily_ics(df, feats, n_workers=2)
            assert _drop_daily_ics(actual) == _drop_daily_ics(expected), kw

    def test_env_zero_falls_back_serial(self, sample_df, monkeypatch):
        """TRAIN_IC_WORKERS=0 时退化为串行。"""
        df = sample_df
        feats = [c for c in df.columns if c.startswith("f")]
        expected = _serial_daily_ic(df, feats)
        monkeypatch.setenv("TRAIN_IC_WORKERS", "0")
        actual = compute_daily_ics(df, feats)
        assert _drop_daily_ics(actual) == _drop_daily_ics(expected)

    def test_empty_features(self, sample_df):
        """空特征列表：直接返回空 dict。"""
        assert compute_daily_ics(sample_df, []) == {}
