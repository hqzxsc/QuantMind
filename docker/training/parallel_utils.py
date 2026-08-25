"""
训练脚本多核工具：因子筛选的并行实现
======================================
`select_top_factors` 的日频 Rank IC 计算原本是「特征 × 交易日」嵌套循环，
原本逐特征 × 逐日 spearmanr 全程单核（2026 单年快照实测 79 秒；
全量多年训练集估约 15-20 分钟）。

本模块把它改成多进程并行（Linux fork），**数值与串行版逐日 spearmanr
完全一致**：不对算法做任何近似/降采样。

并行策略（按交易日分块，而非按特征分块）：
    - 父进程一次性 ``df.groupby('trade_date')`` 得到全部日期子表，
      存入模块全局 ``_SHARED_GROUPS``；fork 子进程零拷贝继承。
    - 把日期区间切成 N 段，每个 worker 只处理自己那段日期、但遍历全部特征。
    - 这样每一行数据只被一个 worker 读取（内存带宽真正分摊）。
      若按特征分块，每个 worker 都要扫全表，高并发下内存带宽饱和、
      甚至变慢（实测 16 worker 慢于 8 worker）。
    - 结果按日期顺序拼接，与串行逐日循环完全一致。

用法（train.py）::

    from parallel_utils import compute_daily_ics
    ic_results = compute_daily_ics(df, features, label_col="label")

并行度控制：
    - 默认 ``min(CPU 核数, 特征数, 交易日数, 内存预算)``
    - 内存预算：按 ``/proc/meminfo`` 的 MemAvailable 收缩 worker 数，
      防止 fork 子进程并发新分配把整机内存打爆（OOM → SIGKILL 137）。
      每个 worker 的峰值新分配实测约 1-2GB（列子集 to_numpy + spearmanr 临时数组）。
    - 环境变量 ``TRAIN_IC_WORKERS=4`` 可显式指定 worker 数（0/1 = 串行，显式值覆盖内存预算）

实现要点：
    - fork 模式：子进程继承父进程地址空间，DataFrame 与分组子表零拷贝共享。
    - worker 只接收 ``(start, end)`` 日期下标区间，通过全局读取大表，不参与 pickle。
    - 进程池启动失败时自动回退串行，绝不让训练中断。
    - worker 内用 ``Series.to_numpy()`` + ``np.isnan`` 掩码替代 ``g[[feat, label]].dropna()``：
      结果行集合与 dropna(how='any') 完全一致（都只剔除 NaN 行），但不再为
      「特征×交易日」组合新建 2 列 DataFrame —— 内存从 ~1.3GB/worker 降到几十 MB。
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

logger = logging.getLogger("quantmind.train.parallel")

# 每只股票在每个交易日被纳入 IC 统计的最低有效观测数（与原串行实现一致）
_MIN_OBS_PER_DAY = 30
# 特征至少要有 20 个交易日的数据才计入（与原串行实现一致）
_MIN_DAYS = 20

# ── fork 共享状态（子进程继承，不参与 pickle）────────────────────────────────
# 父进程在创建进程池前赋值；fork 子进程继承同一地址空间，直接读取，
# 无需 pickle 传递大表（零拷贝 COW）。
_SHARED_DF: pd.DataFrame | None = None
_SHARED_GROUPS: list[tuple[Any, pd.DataFrame]] = []
_SHARED_FEATURES: list[str] = []
_SHARED_LABEL_COL: str = "label"


def _date_chunk_worker(idx_range: tuple[int, int]) -> dict[str, list[float]]:
    """进程池 worker：计算 ``[start, end)`` 区间内所有日期、所有特征的逐日 Rank IC。

    返回 ``{feature: [ic, ic, ...]}``（按日期顺序），由父进程汇总后算 ic_mean/icir。
    与原串行实现的差异仅在于「外层循环对象从特征换成日期」，
    每个特征最终得到的 daily_ics 序列与逐日 spearmanr 完全一致。

    内存注意：这里用 ``g[col].to_numpy()`` + ``np.isnan`` 掩码挑选有效行，
    与原 ``g[[feat, label_col]].dropna()`` 剔掉的行集合完全相同（dropna 只剔 NaN，
    不剔 inf），但不再为每个「特征 × 交易日」组合分配新的 2 列 DataFrame。
    """
    start, end = idx_range
    groups = _SHARED_GROUPS
    features = _SHARED_FEATURES
    label_col = _SHARED_LABEL_COL
    out: dict[str, list[float]] = {f: [] for f in features}
    for i in range(start, end):
        _, g = groups[i]
        label_arr = g[label_col].to_numpy()
        for feat in features:
            if feat not in g.columns:
                continue
            feat_arr = g[feat].to_numpy()
            valid_mask = ~(np.isnan(feat_arr) | np.isnan(label_arr))
            if int(valid_mask.sum()) < _MIN_OBS_PER_DAY:
                continue
            ic, _ = spearmanr(feat_arr[valid_mask], label_arr[valid_mask])
            if np.isfinite(ic):
                out[feat].append(float(ic))
    return out


def _chunk_ranges(n: int, n_chunks: int) -> list[tuple[int, int]]:
    """把 ``[0, n)`` 切成 ``n_chunks`` 个连续区间，返回 ``[(start, end), ...]``。"""
    n_chunks = max(1, n_chunks)
    k, r = divmod(n, n_chunks)
    ranges: list[tuple[int, int]] = []
    start = 0
    for i in range(n_chunks):
        size = k + (1 if i < r else 0)
        if size > 0:
            ranges.append((start, start + size))
            start += size
    return ranges


def _mem_available_gb() -> float | None:
    """读取 /proc/meminfo 的 MemAvailable（宿主机视角；容器未设 mem_limit 时即整机）。

    fork 子进程的 COW 页与列子集新分配都计入容器/宿主内存账，父进程大表
    已经常驻，所以 worker 预算必须基于「当前剩余内存」，而不是固定核数。
    读取失败返回 None（调用方回退 CPU 核数）。
    """
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0 / 1024.0
    except OSError:
        return None
    return None


def _budget_workers(cpu_count: int, features: list[str], n_dates: int) -> int:
    """按内存预算收缩 worker 数，防止并发 fork 新分配打爆整机内存。

    每个 worker 峰值新分配（列子集 to_numpy + spearmanr 临时数组）实测约
    1-2GB；保留 6GB 给父进程大表之外的开销（pandas 碎片、LightGBM 预热等）。
    无内存信息时回退 ``min(cpu_count, 特征数, 日期数)``（旧行为）。
    """
    avail = _mem_available_gb()
    if avail is None:
        workers = cpu_count
    else:
        workers = max(1, int((avail - 6.0) / 2.0))
    return max(1, min(workers, len(features), n_dates))


def _finalize(
    per_feat_ics: dict[str, list[float]], features: list[str]
) -> dict[str, dict[str, Any]]:
    """把每个特征的逐日 IC 列表汇总成与原串行实现一致的结果结构。"""
    results: dict[str, dict[str, Any]] = {}
    for feat in features:
        daily_ics = per_feat_ics.get(feat, [])
        if len(daily_ics) < _MIN_DAYS:
            results[feat] = {
                "ic_mean": 0.0,
                "icir": 0.0,
                "ic_positive_rate": 0.0,
                "n_days": len(daily_ics),
            }
            continue
        arr = np.asarray(daily_ics, dtype=np.float64)
        results[feat] = {
            "ic_mean": float(np.mean(arr)),
            "icir": float(np.mean(arr) / (np.std(arr) + 1e-9)),
            "ic_positive_rate": float(np.mean(arr > 0)),
            "n_days": len(arr),
            "daily_ics": daily_ics,
        }
    return results


def compute_daily_ics(
    df: pd.DataFrame,
    features: list[str],
    label_col: str = "label",
    n_workers: int | None = None,
) -> dict[str, dict[str, Any]]:
    """并行计算全部候选特征的逐日 Rank IC（与原串行实现数值一致）。

    Args:
        df: 含列 ``'trade_date'``、``label_col`` 及全部候选特征
        features: 候选特征列表（按此顺序计算，结果 key 顺序一致）
        label_col: 标签列名，默认 ``'label'``
        n_workers: 显式 worker 数；``None`` 时允许 ``TRAIN_IC_WORKERS``
            环境变量覆盖，仍未指定则按 ``min(CPU 核数, 特征数, 交易日数)``
            与剩余内存预算（_budget_workers）收缩。
    """
    feats = [f for f in features if f in df.columns]
    if not feats:
        return {}

    cpu_count = os.cpu_count() or 1
    env_workers = os.getenv("TRAIN_IC_WORKERS")
    if n_workers is not None:
        workers = n_workers
    elif env_workers and env_workers.strip().isdigit():
        workers = int(env_workers)
    else:
        workers = cpu_count
    workers = max(1, workers)

    # 赋值全局供 fork 子进程继承（必须早于进程池创建）
    global _SHARED_DF, _SHARED_GROUPS, _SHARED_FEATURES, _SHARED_LABEL_COL
    _SHARED_DF = df
    _SHARED_LABEL_COL = label_col
    _SHARED_FEATURES = feats
    # 一次性分组：子表是父进程 buffer 的视图（COW），fork 后子进程零拷贝读取。
    # 按特征分块时每个 worker 都要重复这次全表扫描；这里只扫一次，再分发给各 worker。
    _SHARED_GROUPS = list(df.groupby("trade_date", sort=False))
    n_dates = len(_SHARED_GROUPS)
    # worker 数不能超过日期数（否则有 worker 拿空区间白等）；
    # 也不需要超过特征数（计算量由特征×日期决定，日期分块已充分利用并发）。
    # 未显式指定（n_workers 参数 / TRAIN_IC_WORKERS 环境变量）时按剩余内存预算
    # 收缩 worker 数，防止 fork 子进程并发新分配把整机内存打爆（OOM → SIGKILL 137）。
    explicit = n_workers is not None or bool(env_workers and env_workers.strip().isdigit())
    if not explicit:
        workers = _budget_workers(workers, feats, n_dates)
    workers = max(1, min(workers, len(feats), n_dates))

    t0 = time.time()

    if workers <= 1 or cpu_count <= 1:
        out = _date_chunk_worker((0, n_dates))
        result = _finalize(out, feats)
        logger.info(
            "Daily IC computed in %.1fs (serial, %d features, %d dates)",
            time.time() - t0, len(feats), n_dates,
        )
        return result

    ranges = _chunk_ranges(n_dates, workers)
    try:
        ctx = multiprocessing.get_context("fork")
        merged: dict[str, list[float]] = {f: [] for f in feats}
        with ProcessPoolExecutor(max_workers=len(ranges), mp_context=ctx) as pool:
            for partial in pool.map(_date_chunk_worker, ranges):
                for f, lst in partial.items():
                    merged[f].extend(lst)
        # 日期区间按 df 顺序连续切分、再按 chunk 顺序拼接，
        # 每个特征的 daily_ics 与串行逐日循环顺序完全一致
        result = _finalize(merged, feats)
        logger.info(
            "Daily IC computed in %.1fs (%d workers, %d features, %d dates)",
            time.time() - t0, len(ranges), len(feats), n_dates,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("Parallel IC computation failed (%s), falling back to serial", exc)
        out = _date_chunk_worker((0, n_dates))
        result = _finalize(out, feats)
        logger.info(
            "Daily IC computed in %.1fs (serial fallback, %d features, %d dates)",
            time.time() - t0, len(feats), n_dates,
        )
        return result
