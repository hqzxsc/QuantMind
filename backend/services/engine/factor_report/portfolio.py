"""因子组合构建：从体检结论里选出「可用因子集合 + 权重」，并说明每个因子为什么入选/淘汰。

三处共用同一份实现：
  - `scripts/build_factor_portfolio.py`（离线生成 JSON）
  - `GET /api/v1/factor-report/portfolio`（页面展示）
  - `scripts/apply_factor_selection_to_catalog.py`（写训练目录 → 训练页勾选）

选因子规则（透明可复现）：
  1. **硬门槛**：|ICIR| ≥ icir_min、覆盖率 ≥ coverage_min、T+20 调仓口径扣费净收益 > 0（可关）
  2. **相关性去重**：|ρ| ≥ corr_threshold 的同源簇只留 |ICIR| 最大者（复用 clusters 的连通分量）
  3. **权重**：最大 ICIR（收缩 Σ⁻¹μ，带方向符号）；另给等权 / |IC| 加权做对照
  4. **上限**：按 |IC|×|ICIR| 排序取前 n_top
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from .clusters import cluster_by_correlation
from .service import dataset_dir, load_snapshot, normalize_dataset

log = logging.getLogger(__name__)

DEFAULT_ROUND_TRIP_COST = 0.00025 * 2 + 0.0005 + 0.0005 * 2   # ≈ 0.20%（佣金双边+印花税+滑点双边）
DEFAULT_HOLDING_DAYS = 20                                      # 与 IC 峰位一致


def portfolio_path(dataset: str = "alpha_library") -> Path:
    return dataset_dir(normalize_dataset(dataset)) / "report" / "factor_portfolio.json"


def _series_stats(dataset: str, round_trip_cost: float, holding_days: int) -> dict[str, dict[str, float]]:
    """从序列快照算每因子的：扣费净收益（T+holding 调仓口径）、覆盖率、日换手。"""
    path = dataset_dir(dataset) / "report" / "factor_series.parquet"
    if not path.exists():
        return {}
    names = set(pq.ParquetFile(path).schema_arrow.names)
    cols = ["factor", "turnover", "coverage"]
    ls_col = f"ls_{holding_days}"
    if ls_col in names:
        cols.append(ls_col)
    df = pq.read_table(path, columns=cols).to_pandas()
    # 防御：分位列可能混入 ±inf（早年近零/负价的 forward return），统一打回 NaN 再聚合
    for c in ("turnover", "coverage", ls_col):
        if c in df.columns:
            df[c] = df[c].where(np.isfinite(df[c]))
    d = df["turnover"].fillna(0.0).clip(0.0, 1.0)
    # 持有 holding_days 的成员变动比例 ≈ 1−(1−日换手)^N；成本按 N 日摊到日均
    turn_n = (1.0 - (1.0 - d) ** holding_days).clip(0.0, 1.0)
    cost_daily = turn_n * round_trip_cost / holding_days
    if ls_col in df.columns:
        # 多空价差是"高分组 − 低分组"，组合按其方向使用时收益为正；这里取绝对值量级
        gross_daily = df[ls_col].abs().fillna(0.0) / holding_days
    else:
        gross_daily = np.nan
    df = df.assign(net_daily=gross_daily - cost_daily)
    g = df.groupby("factor").agg(
        net_daily=("net_daily", "mean"),
        turnover_daily=("turnover", "mean"),
        coverage=("coverage", "mean"),
    )
    return {str(k): {kk: float(vv) if np.isfinite(vv) else float("nan") for kk, vv in v.items()}
            for k, v in g.to_dict("index").items()}


def build_portfolio(
    dataset: str,
    *,
    n_top: int = 30,
    icir_min: float = 0.15,
    coverage_min: float = 0.60,
    corr_threshold: float = 0.9,
    require_positive_net: bool = True,
    round_trip_cost: float = DEFAULT_ROUND_TRIP_COST,
    holding_days: int = DEFAULT_HOLDING_DAYS,
    persist: bool = True,
) -> dict[str, Any]:
    """构建推荐因子集（含权重与淘汰理由）；persist=True 时写 factor_portfolio.json。"""
    ds = normalize_dataset(dataset)
    snap = load_snapshot(ds)
    if not snap:
        return {"available": False, "dataset": ds,
                "reason": f"数据集 {ds} 的快照尚未生成，请先运行 build_factor_report.py --dataset {ds}"}

    corr = snap.get("correlation") or {}
    all_names: list[str] = list(corr.get("factors") or [])
    matrix = list(corr.get("matrix") or [])
    metrics = {f["name"]: f for f in (snap.get("factors") or []) if f.get("name")}
    stats = _series_stats(ds, round_trip_cost, holding_days)

    rejected: list[dict[str, str]] = []
    passed: list[str] = []
    for name in all_names:
        m = metrics.get(name)
        if not m:
            rejected.append({"name": name, "reason": "缺指标"})
            continue
        icir = abs(float(m.get("icir") or 0.0))
        cov = stats.get(name, {}).get("coverage", float("nan"))
        net = stats.get(name, {}).get("net_daily", float("nan"))
        if icir < icir_min:
            rejected.append({"name": name, "reason": f"ICIR {icir:.2f} < {icir_min}"})
            continue
        if np.isfinite(cov) and cov < coverage_min:
            rejected.append({"name": name, "reason": f"覆盖率 {cov:.0%} < {coverage_min:.0%}"})
            continue
        if require_positive_net and np.isfinite(net) and net <= 0:
            rejected.append({"name": name, "reason": f"T+{holding_days} 扣费后净收益 {net * 100:+.3f}%/日 ≤ 0"})
            continue
        passed.append(name)

    # 相关性去重：同源簇只留 |ICIR| 最大者
    keep = set(passed)
    if corr_threshold < 1.0 and len(keep) > 1:
        clusters = cluster_by_correlation(all_names, matrix, metrics, threshold=corr_threshold, keep="icir")
        for c in clusters:
            members = [m["name"] for m in c["members"]]
            survivors = [n for n in members if n in keep]
            for n in survivors:
                if n != c["representative"]:
                    keep.discard(n)
                    rejected.append({"name": n, "reason": f"与 {c['representative']} 相关性 ≥ {corr_threshold}（同源去重）"})

    # 排序取前 n_top
    ranked = sorted(keep, key=lambda n: abs(float(metrics[n].get("ic_mean") or 0)) * abs(float(metrics[n].get("icir") or 0)),
                    reverse=True)
    sel = ranked[:n_top]
    for n in ranked[n_top:]:
        rejected.append({"name": n, "reason": f"评分排名 {ranked.index(n) + 1} 超出 top {n_top}"})

    if len(sel) < 3:
        return {"available": False, "dataset": ds,
                "reason": f"通过门槛的因子只有 {len(sel)} 个（<3），请放宽 n_top/icir_min 或关闭净收益门槛"}

    # ── 权重：最大 ICIR（收缩 Σ⁻¹μ，带方向）
    idx = {n: i for i, n in enumerate(all_names)}
    ii = [idx[n] for n in sel]
    mat = np.asarray(matrix, dtype=np.float64)
    Sigma = mat[np.ix_(ii, ii)]
    sign = np.array([1.0 if float(metrics[n].get("ic_mean") or 0) >= 0 else -1.0 for n in sel])
    mu = np.array([abs(float(metrics[n].get("ic_mean") or 0)) for n in sel])
    shrink = 0.3
    S = (1 - shrink) * Sigma + shrink * np.eye(len(sel))
    try:
        w = np.linalg.solve(S, mu * sign)
    except np.linalg.LinAlgError:
        w = mu * sign
    if not np.isfinite(w).all() or np.abs(w).sum() <= 0:
        w = mu * sign
    w = sign * (np.abs(w) / np.abs(w).sum())      # 方向符号 + gross=1 归一

    # ── 组合表现：用逐日 IC 序列合成（与单因子 ICIR 同口径）
    comp_ic = comp_icir = None
    sp = dataset_dir(ds) / "report" / "factor_series.parquet"
    if sp.exists():
        raw = pq.read_table(sp, columns=["factor", "date", "ic"]).to_pandas()
        piv = raw.pivot_table(index="date", columns="factor", values="ic")
        cols = [n for n in sel if n in piv.columns]
        if len(cols) >= 3:
            ww = np.array([w[sel.index(n)] for n in cols])
            series = piv[cols].to_numpy(dtype=float) @ ww
            series = series[np.isfinite(series)]
            if series.size > 20 and series.std() > 0:
                comp_ic = float(series.mean())
                comp_icir = float(series.mean() / series.std())

    def _clean(v):
        """NaN/Inf → None：JSON 不能带非有限数（FastAPI 序列化会直接 500）。"""
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if np.isfinite(f) else None

    items = []
    for n, wi in zip(sel, w, strict=False):
        m = metrics[n]
        st = stats.get(n, {})
        items.append({
            "name": n,
            "display_name": m.get("display_name"),
            "library": m.get("library"),
            "weight": round(float(wi), 4),
            "direction": int(1 if float(m.get("ic_mean") or 0) >= 0 else -1),
            "ic_mean": _clean(m.get("ic_mean")),
            "icir": _clean(m.get("icir")),
            "turnover": _clean(st.get("turnover_daily")),
            "coverage": _clean(st.get("coverage")),
            "net_daily": _clean(st.get("net_daily")),
        })
    items.sort(key=lambda x: abs(x["weight"]), reverse=True)

    payload = {
        "available": True,
        "dataset": ds,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rule": {
            "n_top": n_top,
            "icir_min": icir_min,
            "coverage_min": coverage_min,
            "corr_threshold": corr_threshold,
            "require_positive_net": require_positive_net,
            "round_trip_cost": round_trip_cost,
            "holding_days": holding_days,
            "scheme": "max_icir_shrunk",
        },
        "summary": {
            "n_universe": len(all_names),
            "n_passed": len(passed),
            "n_selected": len(sel),
            "n_rejected": len(rejected),
            "composite_ic": _clean(comp_ic),
            "composite_icir": _clean(comp_icir),
            "single_icir_avg": float(np.mean([abs(float(metrics[n].get("icir") or 0)) for n in sel])),
        },
        "factors": items,
        "rejected": rejected[:200],
    }
    if persist:
        path = portfolio_path(ds)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            # allow_nan=False：宁可写失败也不落一份浏览器/网关读不得的 JSON
            json.dump(payload, f, ensure_ascii=False, indent=1, allow_nan=False)
        log.info("因子组合已写出：%s（入选 %d 个）", path, len(sel))
    return payload
