#!/usr/bin/env python3
"""因子报告快照（多数据集 × 分位收益 / 换手 / 相关性 / IC）→ JSON + parquet。

支持的数据集（注册表见 backend/services/engine/factor_report/datasets.py）：
  alpha_library / l1_factors / l2_factors / l1_l2_factors

与 evaluate_alpha_library.py 的分工：
  - 那个脚本只算 IC（RankIC/ICIR/胜率/t 值），产出排序表 CSV；
  - 本脚本是「报告页」的数据源，在同一个 pass 里额外算出 Alphalens 式的
    分位收益（decile 组合平均前瞻收益 + 多空价差 + 单调性）、因子换手率、
    因子秩相关矩阵，外加单因子逐日明细序列（detail 接口用）。

数据口径（与本仓库其它因子链路一致）：
  - 因子值：data/quantdb/6_ml_datasets/alpha_library/dt=*/data.parquet（429 列，含 dt/symbol/time）
  - 前瞻收益：data/quantdb/6_ml_datasets/alpha_library_labels/dt=*/data.parquet
    （fwd_ret_1/2/5/10/20；尾部若干日尚未落地会全空，脚本自动跳过）
  - 每日横截面按 symbol 对齐后：因子取秩（rank，抗异常值，无需去极值），
    分位按秩等分；秩相关矩阵用「秩矩阵逐日 X^T X 累加」的流式算法（内存 O(n×k)）

用法：
  python backend/scripts/build_factor_report.py --dataset alpha_library   # 近 5 年、fwd_ret_5、8 进程
  python backend/scripts/build_factor_report.py --dataset l1_factors
  python backend/scripts/build_factor_report.py --dataset l2_factors
  python backend/scripts/build_factor_report.py --dataset l1_l2_factors
  python backend/scripts/build_factor_report.py --dataset l1_factors --years 3 --step 2   # 更快
  python backend/scripts/build_factor_report.py --dataset l1_factors --workers 1          # 单进程调试

产出：
  data/quantdb/6_ml_datasets/alpha_library/report/factor_report.json     排行 + 429×429 相关矩阵
  data/quantdb/6_ml_datasets/alpha_library/report/factor_series.parquet  单因子逐日明细序列
      （IC / 十分位收益 / 换手 / 覆盖率；报告页 detail 接口靠它把 2.4s 扫分区变成毫秒级切片）
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.shared.quantdb_paths import resolve_quantdb_subdir  # noqa: E402

log = logging.getLogger("factor_report")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 数据目录一律经 quantdb_paths 解析（容器 /data/quantdb、便携包 $ROOT/data/quantdb、Windows 盘符都覆盖）
IC_CSV = resolve_quantdb_subdir("6_ml_datasets", "alpha_library", "ic_evaluation", "ic_results_all.csv")

# 数据集注册表在服务侧（构建脚本与 API 共用同一份口径）
from backend.services.engine.factor_report.datasets import (  # noqa: E402
    DATASETS,
    dataset_dir,
    label_dir as dataset_label_dir,
    library_of,
)

N_QUANTILES = 10


# ─────────────────────────── 单日计算 ───────────────────────────

def _rank_axis0(x: np.ndarray) -> np.ndarray:
    """逐列横截面秩（NaN 保留为 NaN）。用 argsort 双排序，比 pandas.rank 快。"""
    n, k = x.shape
    ranks = np.full((n, k), np.nan, dtype=np.float32)
    for j in range(k):
        col = x[:, j]
        mask = ~np.isnan(col)
        m = int(mask.sum())
        if m < 20:  # 有效样本太少，该因子当日不参与
            continue
        order = np.argsort(col[mask], kind="stable")
        r = np.empty(m, dtype=np.float32)
        r[order] = np.arange(1, m + 1, dtype=np.float32)
        ranks[mask, j] = r
    return ranks


def compute_one_date(args: tuple) -> dict | None:
    """算一天：返回该日的秩相关累加块、分位收益、换手、IC 等中间量。

    任务参数（全部为可 pickle 的朴素类型，跨进程传递）：
      dt, horizon, factor_dir, factor_cols, meta_ignore, label_mode, label_ref
    label_mode:
      "labels_table" —— label_ref 为标签目录，读 dt 分区的 horizon 列
      "close_fwd"    —— label_ref 为**未来第 k 个交易日**的分区日期，
                        用本表 close 算 close_{T+k}/close_T - 1（与训练侧 return_Nd 同口径）
    """
    dt, horizon, factor_dir, factor_cols, meta_ignore, label_mode, label_ref = args
    try:
        fac = pq.read_table(f"{factor_dir}/dt={dt}/data.parquet").to_pandas()
    except FileNotFoundError:
        return None

    if label_mode == "labels_table":
        try:
            lab = pq.read_table(f"{label_ref}/dt={dt}/data.parquet", columns=["symbol", horizon]).to_pandas()
        except FileNotFoundError:
            return None
        if horizon not in lab.columns or lab[horizon].notna().sum() == 0:
            return None  # 尾部标签未落地
        merged = fac.merge(lab, on="symbol", how="inner")
        y = merged[horizon].to_numpy(dtype=np.float64)
    else:  # close_fwd
        if not label_ref:
            return None
        try:
            fut = pq.read_table(f"{factor_dir}/dt={label_ref}/data.parquet", columns=["symbol", "close"]).to_pandas()
        except FileNotFoundError:
            return None
        base = fac[["symbol", "close"]].rename(columns={"close": "close_t"})
        merged = base.merge(fut.rename(columns={"close": "close_tk"}), on="symbol", how="inner")
        c0 = merged["close_t"].to_numpy(dtype=np.float64)
        c1 = merged["close_tk"].to_numpy(dtype=np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            merged[horizon] = np.where((c0 > 0) & np.isfinite(c0) & np.isfinite(c1), c1 / c0 - 1.0, np.nan)
        merged = merged.merge(fac.drop(columns=["close"], errors="ignore"), on="symbol", how="inner")

    if len(merged) < 50:
        return None

    cols = [c for c in factor_cols if c not in meta_ignore]
    X = merged[cols].to_numpy(dtype=np.float32)
    y = merged[horizon].to_numpy(dtype=np.float64)
    ok_y = ~np.isnan(y)

    R = _rank_axis0(X)
    n_valid_col = np.isfinite(R).sum(axis=0)          # 每列有效样本数
    # 中位秩填充：NaN 秩补成该列有效样本的中位，避免整行丢弃（与 Alphalens 的重叠样本口径近似）
    Rf = np.where(np.isfinite(R), R, (n_valid_col + 1) / 2.0).astype(np.float32)

    # ── 秩相关矩阵累加：Σ R^T R（R 已按列中心化由全局均值/标准差在合并阶段处理）
    gram = (Rf.T @ Rf).astype(np.float64)
    col_sum = Rf.sum(axis=0, dtype=np.float64)
    n_rows = Rf.shape[0]

    # ── 分位组合：按秩等分成 N_QUANTILES 组，组内 y 均值
    q_ret = np.full((N_QUANTILES, len(cols)), np.nan)
    if ok_y.sum() >= 50:
        nq = np.ceil(n_valid_col / N_QUANTILES)  # 每组目标样本数
        for j in range(len(cols)):
            rj = R[:, j]
            valid = np.isfinite(rj) & ok_y
            if valid.sum() < 50:
                continue
            idx = np.minimum(((rj[valid] - 1) // np.maximum(nq[j], 1)).astype(int), N_QUANTILES - 1)
            yv = y[valid]
            cnt = np.bincount(idx, minlength=N_QUANTILES)
            ssum = np.bincount(idx, weights=yv, minlength=N_QUANTILES)
            with np.errstate(invalid="ignore"):
                q_ret[:, j] = np.where(cnt > 0, ssum / np.maximum(cnt, 1), np.nan)

    # ── 秩 IC（Spearman）：rank(y) 与各因子秩的相关
    ic = np.full(len(cols), np.nan)
    if ok_y.sum() >= 30:
        yr = np.full(len(y), np.nan)
        yr[ok_y] = _rank_axis0(y.reshape(-1, 1)[:, :]).ravel()[ok_y]
        ry = yr[ok_y]
        # 用秩秩相关（Pearson on ranks）
        Ry = R[ok_y, :]
        mu_y = ry.mean()
        sd_y = ry.std()
        # 整日因子全 NaN（早期预热日）时直接留 NaN，避免对空切片做 nanmean 刷警告
        if sd_y > 0 and np.isfinite(Ry).any():
            mu = np.nanmean(Ry, axis=0)
            sd = np.nanstd(Ry, axis=0)
            with np.errstate(invalid="ignore"):
                cov = np.nanmean((Ry - mu) * (ry - mu_y)[:, None], axis=0)
                ic = np.where(sd > 0, cov / (sd * sd_y), np.nan)

    # ── 十分位成员（用于换手）：返回当日每列的分位编号（-1 表示无效）
    if n_valid_col.max(initial=0) > 0:
        with np.errstate(invalid="ignore"):
            group = np.where(
                np.isfinite(R),
                np.minimum(((R - 1) / np.maximum(np.ceil(n_valid_col / N_QUANTILES), 1)).astype(np.int16), N_QUANTILES - 1),
                -1,
            )
    else:
        group = np.full(R.shape, -1, dtype=np.int16)

    # 单因子明细页要的覆盖率：该因子当日「因子与前瞻收益都有效」的比例
    cov = ((np.isfinite(X) & ok_y[:, None]).sum(axis=0) / max(len(merged), 1)).astype(np.float32)

    return {
        "dt": dt,
        "symbols": merged["symbol"].to_numpy(),
        "gram": gram,
        "col_sum": col_sum,
        "n_rows": n_rows,
        "q_ret": q_ret,
        "q_cnt": np.isfinite(q_ret).sum(axis=0),
        "ic": ic,
        "group": group,
        "coverage": cov,
    }


# ─────────────────────────── 主流程 ───────────────────────────

def merge_partials(partials: list[dict], n_factors: int) -> dict:
    """合并各日中间量 → 全局指标 + 单因子明细序列。

    明细序列（IC/十分位收益/换手/覆盖率，逐日 × 逐因子）同时在这里落成数组，
    由 main() 写成 parquet —— 报告页的 detail 接口靠它把「2.4s 扫分区」变成一次切片。
    """
    gram = np.zeros((n_factors, n_factors), dtype=np.float64)
    col_sum = np.zeros(n_factors, dtype=np.float64)
    n_rows = 0
    q_sum = np.zeros((N_QUANTILES, n_factors), dtype=np.float64)
    q_cnt = np.zeros(n_factors, dtype=np.int64)
    ic_list: list[np.ndarray] = []
    turnover_changed = np.zeros(n_factors, dtype=np.float64)
    turnover_valid = np.zeros(n_factors, dtype=np.float64)
    # 明细序列容器
    dates_out: list[str] = []
    q_mat: list[np.ndarray] = []        # 每日 (N_QUANTILES, n_factors)
    ic_mat: list[np.ndarray] = []       # 每日 (n_factors,)
    turnover_mat: list[np.ndarray] = []  # 每日 (n_factors,)
    coverage_mat: list[np.ndarray] = []  # 每日 (n_factors,)

    prev_group: np.ndarray | None = None
    prev_syms: np.ndarray | None = None
    for p in partials:
        gram += p["gram"]
        col_sum += p["col_sum"]
        n_rows += p["n_rows"]
        qs = np.where(np.isfinite(p["q_ret"]), p["q_ret"], 0.0)
        q_sum += qs
        q_cnt += p["q_cnt"]
        ic_list.append(p["ic"])
        # 换手：与前一有交易日比较十分位成员变化，按当日有效样本归一（单边换手率）。
        # ⚠️ 必须按 **symbol 对齐**再比：各数据集的行序不保证逐日稳定
        # （实测 l1_l2_factors 相邻两日同位置符号一致率低至 0.2%），
        # 按位置比较等于拿不同股票的分位做差，会把换手率算成噪声。
        g = p["group"]
        syms = p["symbols"]
        day_turnover = np.zeros(n_factors, dtype=np.float64)
        if prev_group is not None:
            _, ia, ib = np.intersect1d(prev_syms, syms, return_indices=True)
            a, b = prev_group[ia], g[ib]
            valid = (a >= 0) & (b >= 0)
            changed_cnt = ((a != b) & valid).sum(axis=0)
            valid_cnt = valid.sum(axis=0)
            turnover_changed += changed_cnt
            turnover_valid += valid_cnt
            day_turnover = np.divide(
                changed_cnt, np.maximum(valid_cnt, 1),
                out=np.zeros(n_factors), where=valid_cnt > 0,
            )
        prev_group, prev_syms = g, syms

        dates_out.append(p["dt"])
        q_mat.append(p["q_ret"].astype(np.float32))
        ic_mat.append(p["ic"].astype(np.float32))
        turnover_mat.append(day_turnover.astype(np.float32))
        coverage_mat.append(p.get("coverage", np.zeros(n_factors, dtype=np.float32)).astype(np.float32))

    means = col_sum / max(n_rows, 1)
    var = np.diag(gram) / max(n_rows, 1) - means**2
    sd = np.sqrt(np.maximum(var, 1e-12))
    cov = gram / max(n_rows, 1) - np.outer(means, means)
    corr = cov / np.outer(sd, sd)
    corr = np.clip(np.nan_to_num(corr, nan=0.0), -1.0, 1.0)
    np.fill_diagonal(corr, 1.0)

    q_mean = np.divide(q_sum, np.maximum(q_cnt, 1)[None, :], out=np.zeros_like(q_sum), where=q_cnt[None, :] > 0)
    ic_stack = np.vstack(ic_list) if ic_list else np.zeros((1, n_factors))
    ic_mean = np.nanmean(ic_stack, axis=0)
    ic_std = np.nanstd(ic_stack, axis=0)
    with np.errstate(invalid="ignore"):
        icir = np.where(ic_std > 0, ic_mean / ic_std, 0.0)
        win = np.nanmean((ic_stack > 0).astype(float), axis=0)
    t_value = icir * np.sqrt(np.maximum(np.isfinite(ic_stack).sum(axis=0), 1))

    turnover = np.divide(
        turnover_changed,
        np.maximum(turnover_valid, 1.0),
        out=np.zeros_like(turnover_changed),
        where=turnover_valid > 0,
    )

    return {
        "corr": corr,
        "q_mean": q_mean,
        "ic_mean": ic_mean,
        "icir": icir,
        "win_rate": win,
        "t_value": t_value,
        "turnover": turnover,
        "n_dates": len(dates_out),
        # 明细序列（写 parquet 用）
        "series": {
            "dates": dates_out,
            "q": np.stack(q_mat) if q_mat else np.zeros((0, N_QUANTILES, n_factors), dtype=np.float32),
            "ic": np.stack(ic_mat) if ic_mat else np.zeros((0, n_factors), dtype=np.float32),
            "turnover": np.stack(turnover_mat) if turnover_mat else np.zeros((0, n_factors), dtype=np.float32),
            "coverage": np.stack(coverage_mat) if coverage_mat else np.zeros((0, n_factors), dtype=np.float32),
        },
    }


def write_series_parquet(path: Path, factors: list[str], series: dict) -> int:
    """把逐日明细写成 parquet（长表：一行 = 一个因子 × 一个交易日）。

    报告页 detail 接口按 factor 过滤 + tail(lookback) 即可，实测从 2.4s 降到毫秒级；
    体积 ~10-20MB（482k 行 × 14 列），远小于逐次扫 250 个分区（~500 次文件打开）。
    """
    import pyarrow as pa
    import pyarrow.parquet as pq_mod

    dates = series["dates"]
    q = series["q"]           # (T, 10, K)
    ic = series["ic"]         # (T, K)
    turn = series["turnover"]  # (T, K)
    cov = series["coverage"]   # (T, K)
    if not dates:
        return 0
    t_len, k_len = q.shape[0], q.shape[2]
    q_tk = np.transpose(q, (0, 2, 1)).reshape(t_len * k_len, N_QUANTILES)  # (T*K, 10)

    table = pa.table({
        "factor": pa.array(np.tile(np.array(factors, dtype=object), t_len), type=pa.dictionary(pa.int16(), pa.string())),
        "date": pa.array(np.repeat(np.array(dates, dtype="int32"), k_len)),
        "ic": pa.array(ic.reshape(-1)),
        "turnover": pa.array(turn.reshape(-1)),
        "coverage": pa.array(cov.reshape(-1)),
        **{f"q{i + 1}": pa.array(q_tk[:, i]) for i in range(N_QUANTILES)},
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    pq_mod.write_table(table, str(path), compression="zstd")
    return table.num_rows


def main() -> int:
    ap = argparse.ArgumentParser(description="构建因子报告快照（多数据集）")
    ap.add_argument("--dataset", default="alpha_library", choices=sorted(DATASETS),
                    help="因子源：alpha_library / l1_factors / l2_factors / l1_l2_factors")
    ap.add_argument("--horizon", default="fwd_ret_5",
                    choices=["fwd_ret_1", "fwd_ret_2", "fwd_ret_3", "fwd_ret_5", "fwd_ret_10", "fwd_ret_20"])
    ap.add_argument("--years", type=int, default=5, help="回看年数（默认近 5 年；0 = 全历史）")
    ap.add_argument("--start", default=None, help="起始日期 YYYYMMDD（优先于 --years）")
    ap.add_argument("--end", default=None, help="结束日期 YYYYMMDD")
    ap.add_argument("--step", type=int, default=1, help="抽样步长：每 N 个交易日取一天")
    ap.add_argument("--workers", type=int, default=8, help="并行进程数（1 = 单进程）")
    ap.add_argument("--out", default=None, help="快照输出路径（默认 <数据集>/report/factor_report.json）")
    ap.add_argument("--series-out", default=None, help="明细序列输出路径（默认与快照同目录）")
    args = ap.parse_args()

    # 显式 spawn：Linux 默认 fork 与线程库（pyarrow/BLAS）混用会偶发死锁
    # —— 实测出现过父进程卡 anon_pipe_write、worker 卡 futex、全体 0% CPU 的挂死。
    try:
        mp.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

    cfg = DATASETS[args.dataset]
    factor_dir = dataset_dir(args.dataset)
    if not factor_dir.is_dir():
        log.error(f"数据集目录不存在: {factor_dir}")
        return 1
    out_path = Path(args.out) if args.out else factor_dir / "report" / "factor_report.json"
    series_out = Path(args.series_out) if args.series_out else out_path.parent / "factor_series.parquet"

    # 日期列表：先用**全部**日期（close_fwd 的 T+k 目标可能落在窗口右边界之外），再裁窗口抽样
    all_dts = sorted(p.name.split("=")[1] for p in factor_dir.glob("dt=*") if p.is_dir())
    if not all_dts:
        log.error("没有可用的分区")
        return 1
    dts = list(all_dts)
    if args.start:
        dts = [d for d in dts if d >= args.start]
    elif args.years > 0:
        cut = int(all_dts[-1][:4]) - args.years
        dts = [d for d in dts if int(d[:4]) > cut]
    if args.end:
        dts = [d for d in dts if d <= args.end]
    dts = dts[:: max(args.step, 1)]
    if not dts:
        log.error("窗口内没有可用的分区")
        return 1

    # 因子列（取窗口内最新分区的 schema）：
    # 排除 meta_cols，且**只取数值列** —— L1/L2 表里还带 release_id / published_at 这类
    # 血缘元数据（字符串），硬编码列名清单迟早漏，按 dtype 过滤更稳。
    sample = pq.ParquetFile(f"{factor_dir}/dt={dts[-1]}/data.parquet")
    sch = sample.schema_arrow
    meta_cols = set(cfg["meta_cols"])
    numeric_prefix = ("float", "double", "int", "decimal")
    factor_cols = [
        c for c in sch.names
        if c not in meta_cols and str(sch.field(c).type).startswith(numeric_prefix)
    ]
    skipped = [c for c in sch.names if c not in meta_cols and c not in factor_cols]
    n_factors = len(factor_cols)
    log.info(
        f"[{args.dataset}] 因子 {n_factors} 个；跳过非数值列 {skipped}；"
        f"日期 {dts[0]} ~ {dts[-1]} 共 {len(dts)} 天；horizon={args.horizon}；workers={args.workers}"
    )

    t0 = time.time()
    k = int(args.horizon.split("_")[-1])
    if cfg["label_mode"] == "labels_table":
        label_dir = str(dataset_label_dir(args.dataset))
        tasks = [(dt, args.horizon, str(factor_dir), factor_cols, meta_cols, "labels_table", label_dir) for dt in dts]
    else:
        idx = {d: i for i, d in enumerate(all_dts)}
        tasks = []
        for dt in dts:
            j = idx[dt] + k
            tasks.append((dt, args.horizon, str(factor_dir), factor_cols, meta_cols, "close_fwd",
                          all_dts[j] if j < len(all_dts) else None))
    partials: list[dict] = []
    if args.workers <= 1:
        for i, task in enumerate(tasks, 1):
            p = compute_one_date(task)
            if p:
                partials.append(p)
            if i % 50 == 0:
                log.info(f"  {i}/{len(tasks)} 天，用时 {time.time() - t0:.0f}s，有效 {len(partials)} 天")
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(compute_one_date, t) for t in tasks]
            for i, fut in enumerate(as_completed(futures), 1):
                p = fut.result()
                if p:
                    partials.append(p)
                if i % 100 == 0:
                    log.info(f"  {i}/{len(tasks)} 天，用时 {time.time() - t0:.0f}s，有效 {len(partials)} 天")

    partials.sort(key=lambda p: p["dt"])  # 换手依赖日期顺序
    if not partials:
        log.error("没有任何有效日期（标签可能尚未落地）")
        return 1

    merged = merge_partials(partials, n_factors)
    corr = merged["corr"]
    q_mean = merged["q_mean"]
    ls = q_mean[-1] - q_mean[0]
    # 单调性：分位序号与平均收益的秩相关
    qi = np.arange(N_QUANTILES, dtype=np.float64)
    mono = np.full(n_factors, np.nan)
    for j in range(n_factors):
        v = q_mean[:, j]
        if np.isfinite(v).all() and np.std(v) > 0:
            mono[j] = np.corrcoef(qi, v)[0, 1]

    ic_csv: dict[str, dict] = {}
    if IC_CSV.exists():
        try:
            import csv as _csv

            with open(IC_CSV, encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    if row.get("horizon") == args.horizon:
                        ic_csv[row["factor"]] = row
        except Exception as e:  # noqa: BLE001
            log.warning(f"读取既有 IC CSV 失败（忽略）：{e}")

    # 库归属（共享规则：alpha 前缀 / 固定 L1·L2 / 按 L2 成员关系）
    from backend.services.engine.factor_report.datasets import l2_columns  # noqa: PLC0415

    l2_cols = l2_columns() if cfg["library_rule"] == "l2_membership" else None

    # 中文名/分类：复用平台因子字典（同一个字典支撑训练页字段说明，命名口径一致）
    try:
        from backend.services.engine.data_platform.quantdb_factor_dictionary import definition_for
    except Exception:  # noqa: BLE001 — 字典不可用时报告仍可出，只是没有中文名
        definition_for = None  # type: ignore[assignment]

    items = []
    for j, name in enumerate(factor_cols):
        display_name, category_name = None, None
        if definition_for is not None:
            try:
                d = definition_for(name)
                display_name, category_name = str(d["display_name"]), str(d["category_name"])
            except Exception:  # noqa: BLE001
                pass
        items.append({
            "name": name,
            "library": library_of(args.dataset, name, l2_cols),
            "display_name": display_name,
            "category_name": category_name,
            "ic_mean": round(float(merged["ic_mean"][j]), 5),
            "icir": round(float(merged["icir"][j]), 4),
            "t_value": round(float(merged["t_value"][j]), 2),
            "win_rate": round(float(merged["win_rate"][j]), 4),
            "quantiles": [round(float(v), 5) for v in q_mean[:, j]],
            "ls_mean": round(float(ls[j]), 5),
            "monotonicity": None if not np.isfinite(mono[j]) else round(float(mono[j]), 3),
            "turnover": round(float(merged["turnover"][j]), 4),
        })

    universe = str(cfg.get("universe") or args.dataset)
    payload = {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "dataset": args.dataset,
            "horizon": args.horizon,
            "label_mode": cfg["label_mode"],
            "start": partials[0]["dt"],
            "end": partials[-1]["dt"],
            "n_dates": len(partials),
            "n_factors": n_factors,
            "step": args.step,
            "universe": universe,
            "elapsed_sec": round(time.time() - t0, 1),
            "series_file": series_out.name,
        },
        "factors": items,
        "correlation": {
            "factors": factor_cols,
            # 3 位小数足够看，JSON 体积从 ~3.5MB 降到 ~1.5MB
            "matrix": np.round(corr, 3).tolist(),
        },
    }

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    log.info(f"快照已写出：{out}（{out.stat().st_size / 1e6:.1f} MB，用时 {time.time() - t0:.0f}s）")

    series_path = series_out
    rows = write_series_parquet(series_path, factor_cols, merged["series"])
    if rows:
        log.info(f"明细序列已写出：{series_path}（{rows} 行 × {N_QUANTILES + 4} 列，{series_path.stat().st_size / 1e6:.1f} MB）")
    else:
        log.warning("明细序列为空，未写出 parquet（detail 接口将回退到按需扫分区）")

    top = sorted(items, key=lambda x: abs(x["ic_mean"]), reverse=True)[:5]
    for it in top:
        log.info(f"  {it['name']:>12} IC={it['ic_mean']:+.4f} ICIR={it['icir']:+.3f} LS={it['ls_mean']:+.4f} 换手={it['turnover']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
