#!/usr/bin/env python3
"""Alpha 库因子报告快照（429 因子 × 分位收益 / 换手 / 相关性 / IC）→ JSON。

与 evaluate_alpha_library.py 的分工：
  - 那个脚本只算 IC（RankIC/ICIR/胜率/t 值），产出排序表 CSV；
  - 本脚本是「报告页」的数据源，在同一个 pass 里额外算出 Alphalens 式的
    分位收益（decile 组合平均前瞻收益 + 多空价差 + 单调性）、因子换手率、
    以及 429×429 的因子秩相关矩阵。

数据口径（与本仓库其它因子链路一致）：
  - 因子值：data/quantdb/6_ml_datasets/alpha_library/dt=*/data.parquet（429 列，含 dt/symbol/time）
  - 前瞻收益：data/quantdb/6_ml_datasets/alpha_library_labels/dt=*/data.parquet
    （fwd_ret_1/2/5/10/20；尾部若干日尚未落地会全空，脚本自动跳过）
  - 每日横截面按 symbol 对齐后：因子取秩（rank，抗异常值，无需去极值），
    分位按秩等分；秩相关矩阵用「秩矩阵逐日 X^T X 累加」的流式算法（内存 O(n×k)）

用法：
  python backend/scripts/build_factor_report.py                    # 近 5 年、fwd_ret_5、8 进程
  python backend/scripts/build_factor_report.py --years 3 --step 2  # 更快
  python backend/scripts/build_factor_report.py --workers 1         # 单进程调试

产出：
  data/quantdb/6_ml_datasets/alpha_library/report/factor_report.json
"""

from __future__ import annotations

import argparse
import json
import logging
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
FACTOR_ROOT = resolve_quantdb_subdir("6_ml_datasets", "alpha_library")
LABEL_ROOT = resolve_quantdb_subdir("6_ml_datasets", "alpha_library_labels")
OUT_PATH = FACTOR_ROOT / "report" / "factor_report.json"
IC_CSV = FACTOR_ROOT / "ic_evaluation" / "ic_results_all.csv"

NON_FACTOR_COLS = {"symbol", "time", "dt"}
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


def compute_one_date(args: tuple[str, str, str, int]) -> dict | None:
    """算一天：返回该日的秩相关累加块、分位收益、换手、IC 等中间量。"""
    dt, horizon, factor_dir, label_dir = args
    try:
        fac = pq.read_table(f"{factor_dir}/dt={dt}/data.parquet").to_pandas()
        lab = pq.read_table(f"{label_dir}/dt={dt}/data.parquet", columns=["symbol", horizon]).to_pandas()
    except FileNotFoundError:
        return None

    if horizon not in lab.columns:
        return None
    if lab[horizon].notna().sum() == 0:  # 尾部标签未落地
        return None

    merged = fac.merge(lab, on="symbol", how="inner")
    if len(merged) < 50:
        return None

    cols = [c for c in fac.columns if c not in NON_FACTOR_COLS]
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
        if sd_y > 0:
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

    return {
        "dt": dt,
        "gram": gram,
        "col_sum": col_sum,
        "n_rows": n_rows,
        "q_ret": q_ret,
        "q_cnt": np.isfinite(q_ret).sum(axis=0),
        "ic": ic,
        "group": group,
    }


# ─────────────────────────── 主流程 ───────────────────────────

def merge_partials(partials: list[dict], n_factors: int) -> dict:
    """合并各日中间量 → 全局指标。"""
    gram = np.zeros((n_factors, n_factors), dtype=np.float64)
    col_sum = np.zeros(n_factors, dtype=np.float64)
    n_rows = 0
    q_sum = np.zeros((N_QUANTILES, n_factors), dtype=np.float64)
    q_cnt = np.zeros(n_factors, dtype=np.int64)
    ic_list: list[np.ndarray] = []
    turnover_changed = np.zeros(n_factors, dtype=np.float64)
    turnover_valid = np.zeros(n_factors, dtype=np.float64)

    prev_group: np.ndarray | None = None
    for p in partials:
        gram += p["gram"]
        col_sum += p["col_sum"]
        n_rows += p["n_rows"]
        qs = np.where(np.isfinite(p["q_ret"]), p["q_ret"], 0.0)
        q_sum += qs
        q_cnt += p["q_cnt"]
        ic_list.append(p["ic"])
        # 换手：与前一有交易日比较十分位成员变化，按当日有效样本归一（单边换手率）
        g = p["group"]
        if prev_group is not None and prev_group.shape == g.shape:
            valid = (g >= 0) & (prev_group >= 0)
            turnover_changed += ((g != prev_group) & valid).sum(axis=0)
            turnover_valid += valid.sum(axis=0)
        prev_group = g

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
        "n_dates": n_rows and len(ic_list),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="构建因子报告快照")
    ap.add_argument("--horizon", default="fwd_ret_5", choices=["fwd_ret_1", "fwd_ret_2", "fwd_ret_5", "fwd_ret_10", "fwd_ret_20"])
    ap.add_argument("--years", type=int, default=5, help="回看年数（默认近 5 年；0 = 全历史）")
    ap.add_argument("--start", default=None, help="起始日期 YYYYMMDD（优先于 --years）")
    ap.add_argument("--end", default=None, help="结束日期 YYYYMMDD")
    ap.add_argument("--step", type=int, default=1, help="抽样步长：每 N 个交易日取一天")
    ap.add_argument("--workers", type=int, default=8, help="并行进程数（1 = 单进程）")
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    dts = sorted(p.name.split("=")[1] for p in FACTOR_ROOT.glob("dt=*") if p.is_dir())
    if args.start:
        dts = [d for d in dts if d >= args.start]
    elif args.years > 0:
        cut = int(dts[-1][:4]) - args.years
        dts = [d for d in dts if int(d[:4]) > cut]
    if args.end:
        dts = [d for d in dts if d <= args.end]
    dts = dts[:: max(args.step, 1)]
    if not dts:
        log.error("没有可用的分区")
        return 1

    # 因子列名（取最新分区的 schema）
    sample = pq.ParquetFile(f"{FACTOR_ROOT}/dt={dts[-1]}/data.parquet")
    factors = [c for c in sample.schema_arrow.names if c not in NON_FACTOR_COLS]
    n_factors = len(factors)
    log.info(f"因子 {n_factors} 个；日期 {dts[0]} ~ {dts[-1]} 共 {len(dts)} 天；horizon={args.horizon}；workers={args.workers}")

    t0 = time.time()
    tasks = [(dt, args.horizon, str(FACTOR_ROOT), str(LABEL_ROOT)) for dt in dts]
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

    items = []
    for j, name in enumerate(factors):
        lib = "alpha158" if name.startswith("a158") else "alpha101" if name.startswith("a101") else "gtja191"
        items.append({
            "name": name,
            "library": lib,
            "ic_mean": round(float(merged["ic_mean"][j]), 5),
            "icir": round(float(merged["icir"][j]), 4),
            "t_value": round(float(merged["t_value"][j]), 2),
            "win_rate": round(float(merged["win_rate"][j]), 4),
            "quantiles": [round(float(v), 5) for v in q_mean[:, j]],
            "ls_mean": round(float(ls[j]), 5),
            "monotonicity": None if not np.isfinite(mono[j]) else round(float(mono[j]), 3),
            "turnover": round(float(merged["turnover"][j]), 4),
        })

    payload = {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "horizon": args.horizon,
            "start": partials[0]["dt"],
            "end": partials[-1]["dt"],
            "n_dates": len(partials),
            "n_factors": n_factors,
            "step": args.step,
            "universe": "A股全市场（alpha_library 因子表）",
            "elapsed_sec": round(time.time() - t0, 1),
        },
        "factors": items,
        "correlation": {
            "factors": factors,
            # 3 位小数足够看，JSON 体积从 ~3.5MB 降到 ~1.5MB
            "matrix": np.round(corr, 3).tolist(),
        },
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    log.info(f"快照已写出：{out}（{out.stat().st_size / 1e6:.1f} MB，用时 {time.time() - t0:.0f}s）")

    top = sorted(items, key=lambda x: abs(x["ic_mean"]), reverse=True)[:5]
    for it in top:
        log.info(f"  {it['name']:>12} IC={it['ic_mean']:+.4f} ICIR={it['icir']:+.3f} LS={it['ls_mean']:+.4f} 换手={it['turnover']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
