"""因子报告（Alphalens 式）——快照读取 + 单因子按需计算。

数据来源与口径见 `backend/scripts/build_factor_report.py`（快照构建脚本）：
  - 快照 JSON：429 个因子的窗口指标（IC/ICIR/分位收益/换手）+ 429×429 秩相关矩阵
  - 单因子明细：按需扫描 alpha_library 的分区（只读所需列），算分位净值、IC 序列、换手序列

本模块只读，不写数据；快照由脚本离线生成（build_factor_report.py）。
"""

from __future__ import annotations

import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from backend.shared.quantdb_paths import resolve_quantdb_subdir

log = logging.getLogger(__name__)

N_QUANTILES = 10
NON_FACTOR_COLS = {"symbol", "time", "dt"}
HORIZONS = ("fwd_ret_1", "fwd_ret_2", "fwd_ret_5", "fwd_ret_10", "fwd_ret_20")

# 明细结果的进程内缓存（{factor:horizon:lookback} → payload），避免重复扫分区
_DETAIL_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DETAIL_TTL_SECONDS = 600


def factor_root() -> Path:
    return resolve_quantdb_subdir("6_ml_datasets", "alpha_library")


def label_root() -> Path:
    return resolve_quantdb_subdir("6_ml_datasets", "alpha_library_labels")


def snapshot_path() -> Path:
    return factor_root() / "report" / "factor_report.json"


def load_snapshot() -> dict[str, Any] | None:
    """读取快照；不存在返回 None（页面据此提示「尚未生成」）。"""
    path = snapshot_path()
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        log.warning(f"因子报告快照解析失败: {e}")
        return None


def _partition_dates(limit: int | None) -> list[str]:
    """可用的因子分区日期（升序）。limit 表示只取最近 N 个。"""
    dts = sorted(p.name.split("=")[1] for p in factor_root().glob("dt=*") if p.is_dir())
    if limit and limit > 0:
        dts = dts[-limit:]
    return dts


def _read_factor_column(dt: str, factor: str) -> Any:
    """读单个分区里某因子的 symbol + 因子值。"""
    path = factor_root() / f"dt={dt}" / "data.parquet"
    if not path.exists():
        return None
    tbl = pq.read_table(path, columns=["symbol", factor])
    return tbl.to_pandas()


def _read_label_column(dt: str, horizon: str) -> Any:
    path = label_root() / f"dt={dt}" / "data.parquet"
    if not path.exists():
        return None
    try:
        tbl = pq.read_table(path, columns=["symbol", horizon])
    except Exception:  # noqa: BLE001 — 老分区可能缺列
        return None
    return tbl.to_pandas()


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """秩相关（各自转秩后 Pearson）。样本过少或任一序列无变化时返回 NaN。

    注意：常量序列的秩相关在数学上是未定义的 —— 必须在**转秩之前**判方差，
    否则稳定排序会给常量列安排上 1..n 的假秩，算出一个看似正常的相关性。
    """
    if a.size < 20:
        return float("nan")
    if np.nanstd(a) <= 0 or np.nanstd(b) <= 0:
        return float("nan")
    ra = _rank(a)
    rb = _rank(b)
    sa, sb = ra.std(), rb.std()
    if sa <= 0 or sb <= 0:
        return float("nan")
    return float(((ra - ra.mean()) * (rb - rb.mean())).mean() / (sa * sb))


def _rank(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="stable")
    r = np.empty(x.size, dtype=np.float64)
    r[order] = np.arange(1, x.size + 1, dtype=np.float64)
    return r


def _load_pairs(dt: str, factor: str, horizon: str) -> tuple[str, Any, Any] | None:
    """读一个分区的因子列与标签列（供线程池并行调用）。"""
    fac = _read_factor_column(dt, factor)
    lab = _read_label_column(dt, horizon)
    if fac is None or lab is None:
        return None
    return dt, fac, lab


def compute_detail(factor: str, horizon: str = "fwd_ret_5", lookback: int = 250) -> dict[str, Any]:
    """按需计算单因子明细：分位净值曲线、分位平均收益、IC 序列、换手序列、覆盖率。"""
    if horizon not in HORIZONS:
        raise ValueError(f"不支持的前瞻期: {horizon}")
    if not factor or not factor.replace("_", "").isalnum():
        raise ValueError("非法因子名")

    key = f"{factor}:{horizon}:{lookback}"
    hit = _DETAIL_CACHE.get(key)
    if hit and time.time() - hit[0] < _DETAIL_TTL_SECONDS:
        return hit[1]

    dts = _partition_dates(lookback)
    loaded: list[tuple[str, Any, Any]] = []
    # 分区读取是纯 IO（每分区只读 2 列），线程池并行把首次明细从 ~7s 压到 1-2s
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_load_pairs, dt, factor, horizon) for dt in dts]
        for fut in futures:
            try:
                res = fut.result()
            except Exception:  # noqa: BLE001 — 单个分区读失败不影响整体
                continue
            if res:
                loaded.append(res)
    loaded.sort(key=lambda x: x[0])

    dates: list[str] = []
    q_returns: list[np.ndarray] = []     # 每个调仓日：各分位的 k 日前瞻收益
    ic_series: list[float] = []
    coverage: list[float] = []
    members_prev: np.ndarray | None = None
    turnover_series: list[float] = []

    for dt, fac, lab in loaded:
        merged = fac.merge(lab, on="symbol", how="inner")
        x = merged[factor].to_numpy(dtype=np.float64)
        y = merged[horizon].to_numpy(dtype=np.float64)
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < 50:
            continue
        xv, yv = x[ok], y[ok]
        # 分位：按因子值升序等分成 10 组
        nq = max(1, math.ceil(xv.size / N_QUANTILES))
        order = np.argsort(xv, kind="stable")
        group = np.empty(xv.size, dtype=np.int16)
        group[order] = np.minimum(np.arange(xv.size, dtype=np.int64) // nq, N_QUANTILES - 1)

        cnt = np.bincount(group, minlength=N_QUANTILES)
        ssum = np.bincount(group, weights=yv, minlength=N_QUANTILES)
        q_ret = np.divide(ssum, np.maximum(cnt, 1), out=np.zeros(N_QUANTILES), where=cnt > 0)

        dates.append(dt)
        q_returns.append(q_ret)
        ic_series.append(_spearman(xv, yv))
        coverage.append(float(ok.sum() / max(len(merged), 1)))

        if members_prev is not None and members_prev.size == xv.size:
            turnover_series.append(float((group != members_prev).mean()))
        members_prev = group

    if not dates:
        return {"factor": factor, "horizon": horizon, "empty": True, "dates": [], "reason": "窗口内没有可用的因子/标签分区"}

    q_mat = np.vstack(q_returns)                       # T × 10
    k = int(horizon.split("_")[-1])
    daily = q_mat / k                                  # 折算到日均（重叠期近似）
    curves = np.cumprod(1.0 + daily, axis=0)           # 各分位净值曲线

    ic_arr = np.array(ic_series, dtype=np.float64)
    roll = 20
    ic_roll: list[float | None] = []
    for i in range(len(ic_arr)):
        if i + 1 < roll:
            ic_roll.append(None)
        else:
            seg = ic_arr[i + 1 - roll : i + 1]
            ic_roll.append(None if not np.isfinite(seg).any() else float(np.nanmean(seg)))

    payload = {
        "factor": factor,
        "horizon": horizon,
        "empty": False,
        "dates": dates,
        "quantile_mean": [float(v) for v in np.nanmean(q_mat, axis=0)],     # 各分位平均 k 日收益
        "quantile_curves": [[float(v) for v in curves[:, j]] for j in range(N_QUANTILES)],
        "ls_curve": [float(v) for v in curves[:, -1] / np.maximum(curves[:, 0], 1e-9)],
        "ic_series": [None if not np.isfinite(v) else float(v) for v in ic_arr],
        "ic_rolling": ic_roll,
        "ic_mean": float(np.nanmean(ic_arr)) if np.isfinite(ic_arr).any() else None,
        "ic_std": float(np.nanstd(ic_arr)) if np.isfinite(ic_arr).any() else None,
        "turnover_dates": dates[1 : 1 + len(turnover_series)],
        "turnover_series": turnover_series,
        "turnover_mean": float(np.mean(turnover_series)) if turnover_series else None,
        "coverage_mean": float(np.mean(coverage)) if coverage else None,
        "n_dates": len(dates),
        "start": dates[0],
        "end": dates[-1],
    }
    _DETAIL_CACHE[key] = (time.time(), payload)
    return payload


def correlation_slice(names: list[str]) -> dict[str, Any]:
    """从快照里取子矩阵（保持请求顺序，忽略不存在的因子）。"""
    snap = load_snapshot()
    if not snap:
        return {"available": False, "reason": "快照尚未生成，请运行 backend/scripts/build_factor_report.py"}
    corr = snap.get("correlation") or {}
    all_names: list[str] = list(corr.get("factors") or [])
    matrix: list[list[float]] = list(corr.get("matrix") or [])
    idx = {n: i for i, n in enumerate(all_names)}
    picked = [n for n in names if n in idx]
    if not picked:
        return {"available": True, "factors": [], "matrix": []}
    rows = [[float(matrix[idx[a]][idx[b]]) for b in picked] for a in picked]
    return {"available": True, "factors": picked, "matrix": rows}


def top_correlated(factor: str, top: int = 8) -> list[dict[str, Any]]:
    """与某因子相关性最高（含负相关）的其它因子，用于明细页「相关因子」区。"""
    snap = load_snapshot()
    if not snap:
        return []
    corr = snap.get("correlation") or {}
    all_names: list[str] = list(corr.get("factors") or [])
    matrix: list[list[float]] = list(corr.get("matrix") or [])
    if factor not in all_names:
        return []
    i = all_names.index(factor)
    pairs = [
        {"name": n, "corr": round(float(matrix[i][j]), 3)}
        for j, n in enumerate(all_names)
        if j != i
    ]
    pairs.sort(key=lambda p: abs(p["corr"]), reverse=True)
    return pairs[: max(1, top)]
