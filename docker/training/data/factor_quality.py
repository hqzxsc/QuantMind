"""因子质量度量：扰动保真度（PFS）与多样性熵（DH）—— 纯函数，训练/挖掘两侧共用。

对齐 AlphaEval（KDD 2026，arXiv 2508.13174）五维评估中的两维，度量与回测解耦，
直接在特征矩阵上计算：

  - **PFS（Perturbation Fidelity Score，鲁棒性）**：截面 z 分上加噪（高斯与
    t 厚尾两个变体），逐日算 Spearman(原排名, 扰动后排名)，取两变体的最小值。
    排名不塌 = 因子对数据误差 / 离散化 / 异常值钝感；一扰就乱 = 换个数据源、
    错一个价位就换仓，不可信。
  - **DH（Diversity Entropy，多样性）**：相关矩阵归一化特征值熵；配套
    effective_factors()（= exp(熵)，单位「相当于几个独立因子」）用于贪心选择时
    判定新增因子的有效增益——两两相关性阈值对多重共线（与多只同时中等相关）
    是盲的，集合级谱度量能看见。

本文件只依赖 numpy/pandas，必须保持可独立 import —— 两个部署面共用同一实现：
  - 训练容器：docker/training/data/ 整目录挂载/推送（``from data.factor_quality import ...``）
  - engine 容器：按路径 importlib 加载（/app/docker/training/data/factor_quality.py）
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

# 逐日截面最少有效股票数（与 parallel_utils 的 IC 口径一致）
MIN_STOCKS_PER_DAY = 30
# 一个因子至少要有多少有效交易日才给出 PFS（与 IC 口径一致）
MIN_DAYS = 20
# PFS 逐日采样的最大天数：逐日 spearman 的均值估计在 120 天上已经稳定，
# 全量逐日没必要（多年训练段线性变慢）。按出现顺序等距抽样，固定 seed 可复现。
MAX_PFS_DAYS = 120


def _colwise_rank_corr(a: np.ndarray, b: np.ndarray, min_stocks: int) -> np.ndarray:
    """逐列 Spearman（对秩的 Pearson），返回每列一个相关系数；有效数不足记 NaN。

    a/b 为同形状秩矩阵，NaN 表示该 (日, 特征) 无观测（两矩阵 NaN 位置一致）。
    """
    mask = np.isfinite(a) & np.isfinite(b)
    n = mask.sum(axis=0)
    # 逐列均值（只对有效元素）；被掩位置取 0 再求和，除以有效数
    sa = np.where(mask, a, 0.0).sum(axis=0)
    sb = np.where(mask, b, 0.0).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        ma = sa / n
        mb = sb / n
    da = np.where(mask, a - ma, 0.0)
    db = np.where(mask, b - mb, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = (da * db).sum(axis=0) / n
        va = (da * da).sum(axis=0) / n
        vb = (db * db).sum(axis=0) / n
        rho = cov / np.sqrt(va * vb)
    bad = (n < min_stocks) | ~np.isfinite(rho)
    rho = np.where(bad, np.nan, rho)
    return rho


def _sample_positions(n_days: int, max_days: int) -> set[int] | None:
    """按出现顺序等距抽样的**位置**集合（固定规则，可复现）；None = 不抽样。

    按位置而非日期值抽样：pd.unique 在 pandas 3 返回 datetime64 数组（tolist()
    是整数纳秒），按值建集合做成员测试跨版本会静默失配（全部命中不了）。
    groupby(sort=False) 与 pd.unique 都是首次出现顺序，位置等价于日期。
    """
    if max_days <= 0 or n_days <= max_days:
        return None
    idx = np.linspace(0, n_days - 1, num=max_days).round().astype(int)
    return set(np.unique(idx).tolist())


def compute_pfs(
    df: pd.DataFrame,
    features: Sequence[str],
    *,
    sigma: float = 0.1,
    dof: int = 3,
    min_stocks: int = MIN_STOCKS_PER_DAY,
    min_days: int = MIN_DAYS,
    max_days: int = MAX_PFS_DAYS,
    seed: int = 42,
) -> dict[str, dict[str, Any]]:
    """逐因子扰动保真度：截面 z 分加噪后排名还剩多少（1 = 完全不受影响）。

    噪声加在**截面 z 分**上（尺度无关）：高斯 N(0, sigma^2) 与 t(dof) 厚尾各一轮，
    厚尾按方差归一（t 的方差 dof/(dof-2)，乘 sqrt((dof-2)/dof) 与高斯同量级）。
    逐日取该日有效股票（>= min_stocks）的 Spearman(原排名, 扰动排名)，对天取均值；
    PFS 取两个噪声变体的**最小值**（最坏情形）。

    返回 ``{feature: {"pfs": float|None, "pfs_gauss": ..., "pfs_t": ..., "n_days": int}}``；
    有效天数不足 min_days 的因子三个值均为 None（无法判定，不参与筛选）。
    """
    feats = [f for f in features if f in df.columns]
    if not feats or "trade_date" not in df.columns:
        return {}

    days = pd.unique(df["trade_date"])
    keep_pos = _sample_positions(len(days), int(max_days))

    rng = np.random.default_rng(seed)
    t_scale = float(sigma) * np.sqrt((dof - 2.0) / dof) if dof > 2 else float(sigma)
    per_day: dict[str, dict[str, list[float]]] = {
        f: {"gauss": [], "t": []} for f in feats
    }

    for pos, (_day, g) in enumerate(df.groupby("trade_date", sort=False)):
        if keep_pos is not None and pos not in keep_pos:
            continue
        x = g[feats]
        arr = x.to_numpy(dtype=np.float64, copy=False)
        # 手写 nan 感知的截面均值/标准差（ddof=0）：全 NaN 列不给 RuntimeWarning，
        # 也避免给 12000 行面板套两层 nan* 函数的开销
        finite = np.isfinite(arr)
        cnt = finite.sum(axis=0)
        denom = np.maximum(cnt, 1)
        mu = np.where(finite, arr, 0.0).sum(axis=0) / denom
        dev = np.where(finite, arr - mu, 0.0)
        sd = np.sqrt((dev * dev).sum(axis=0) / denom)
        with np.errstate(invalid="ignore", divide="ignore"):
            z = (arr - mu) / sd
        z[~np.isfinite(z)] = np.nan
        # 秩：对原值与对 z 分等价（单调），且 NaN 自然成 NaN
        r0 = x.rank(axis=0).to_numpy(dtype=np.float64)
        for kind, noise in (
            ("gauss", rng.normal(0.0, float(sigma), z.shape)),
            ("t", rng.standard_t(int(dof), size=z.shape) * t_scale),
        ):
            zp = pd.DataFrame(z + noise, columns=feats, index=x.index)
            rp = zp.rank(axis=0).to_numpy(dtype=np.float64)
            rho = _colwise_rank_corr(r0, rp, min_stocks)
            for j, f in enumerate(feats):
                v = rho[j]
                if np.isfinite(v):
                    per_day[f][kind].append(float(v))

    out: dict[str, dict[str, Any]] = {}
    for f in feats:
        acc: dict[str, Any] = {}
        g_vals = np.asarray(per_day[f]["gauss"], dtype=np.float64)
        t_vals = np.asarray(per_day[f]["t"], dtype=np.float64)
        if len(g_vals) >= min_days:
            acc["pfs_gauss"] = float(g_vals.mean())
        else:
            acc["pfs_gauss"] = None
        if len(t_vals) >= min_days:
            acc["pfs_t"] = float(t_vals.mean())
        else:
            acc["pfs_t"] = None
        both = [v for v in (acc["pfs_gauss"], acc["pfs_t"]) if v is not None]
        acc["pfs"] = min(both) if both else None
        acc["n_days"] = int(min(len(g_vals), len(t_vals)))
        out[f] = acc
    return out


def _clean_corr(corr: Any) -> np.ndarray | None:
    """相关矩阵规整：非方阵/含非有限值返回 None；特征值计算前对称化。"""
    m = np.asarray(corr, dtype=np.float64)
    if m.ndim != 2 or m.shape[0] != m.shape[1] or m.shape[0] < 2:
        return None
    if not np.isfinite(m).all():
        return None
    return (m + m.T) / 2.0


def _eigen_spectrum(corr: Any) -> np.ndarray | None:
    """相关矩阵特征值（升序），负值截断为 0 并归一化。"""
    m = _clean_corr(corr)
    if m is None:
        return None
    lam = np.linalg.eigvalsh(m)
    lam = np.clip(lam, 0.0, None)
    total = lam.sum()
    if not np.isfinite(total) or total <= 0:
        return None
    return lam / total


def diversity_entropy(corr: Any) -> float | None:
    """归一化特征值熵 DH ∈ [0, 1]：1 = 完全正交，→0 = 完全共线。

    corr 为相关矩阵（list / ndarray）。不足 2 维或非有限返回 None。
    """
    p = _eigen_spectrum(corr)
    if p is None:
        return None
    n = len(p)
    if n < 2:
        return None
    nz = p[p > 0]
    return float(-(nz * np.log(nz)).sum() / np.log(n))


def effective_factors(corr: Any) -> float | None:
    """有效因子数 N_eff = exp(熵_nats)：这组因子相当于几个独立因子。

    1 个因子 = 1.0；N 个两两正交 = N；完全共线 → 1。用于贪心选择的
    「新增因子增益」判定：gain = N_eff(new) - N_eff(old)，正交新增 ≈ +1，
    冗余新增 → 0。
    """
    p = _eigen_spectrum(corr)
    if p is None:
        return None
    nz = p[p > 0]
    return float(np.exp(-(nz * np.log(nz)).sum()))
