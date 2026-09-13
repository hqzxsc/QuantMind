#!/usr/bin/env python3
"""因子筛选：质量门槛 + 同源去重（alpha_library 429 ∪ factor_research 82）

输入（均已存在，无需重新计算因子值）：
  alpha_library/report/factor_report.json   —— 因子报告快照（|IC|/ICIR/换手/429×429 相关矩阵）
  factor_research/{metrics.json,corr.parquet,monthly_scores.parquet}
  alpha_library/dt=*/data.parquet           —— 月末采样做「跨库」相关（库内用各自现成矩阵）

筛选逻辑：
  1. 质量门槛：|IC 均值| ≥ min_ic 且 |ICIR| ≥ min_icir（两库同一把尺）；
  2. 去重：联合相关矩阵上做并查集聚类（|ρ| ≥ corr，默认 0.9），每簇保留强度最高者
     （强度 = |ICIR|，同分比 |IC|）；被剔除者标注 duplicate_of 与相关系数；
  3. 同义/同构因子（PLAN §7 的 41 组 a101≈gtja 对）会被聚类自然捕获。

输出（<quantdb>/factor_research/screening/）：
  factor_selection.json       机器可读：kept（按库分组）/ dropped（原因/重复对象）/ 门槛与统计
  筛选报告_YYYYMMDD.md         人读报告（清单 + 去重样例）
  kept_features.txt           训练可直接消费的特征名清单（每行一个）

用法：
  python3 backend/scripts/screen_factors.py                       # 默认门槛
  python3 backend/scripts/screen_factors.py --min-ic 0.03 --min-icir 0.3 --corr 0.85
  python3 backend/scripts/screen_factors.py --skip-cross          # 跳过跨库相关（快）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services.engine.factor_research.catalog import BY_CODE  # noqa: E402
from backend.shared.quantdb_paths import resolve_quantdb_dir  # noqa: E402

# factor_report 包 __init__ 会拉 FastAPI（宿主裸跑没有），按文件直载 clusters 模块（纯 numpy）
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "fr_clusters",
    Path(__file__).resolve().parents[1]
    / "services"
    / "engine"
    / "factor_report"
    / "clusters.py",
)
_fr_clusters = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fr_clusters)
cluster_by_correlation = _fr_clusters.cluster_by_correlation
summarize = _fr_clusters.summarize

ALPHA_REPORT = ("6_ml_datasets", "alpha_library", "report", "factor_report.json")
ALPHA_DIR = ("6_ml_datasets", "alpha_library")


def _load_alpha() -> tuple[list[str], np.ndarray, dict[str, dict]]:
    root = resolve_quantdb_dir()
    rep = json.loads((root.joinpath(*ALPHA_REPORT)).read_text(encoding="utf-8"))
    names = list(rep["correlation"]["factors"])
    matrix = np.asarray(rep["correlation"]["matrix"], dtype=np.float64)
    by_name = {f["name"]: f for f in rep["factors"]}
    metrics = {}
    for n in names:
        f = by_name.get(n) or {}
        metrics[n] = {
            "ic_mean": f.get("ic_mean"),
            "icir": f.get("icir"),
            "turnover": f.get("turnover"),
            "display_name": f.get("display_name") or n,
            "library": f.get("library") or "alpha_library",
        }
    return names, matrix, metrics


def _load_ours() -> tuple[list[str], np.ndarray, dict[str, dict]]:
    root = resolve_quantdb_dir() / "factor_research"
    m = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    metrics_json = m.get("metrics", {})
    names = sorted(metrics_json)
    corr_df = pd.read_parquet(root / "corr.parquet")
    idx = {n: i for i, n in enumerate(names)}
    M = np.eye(len(names))
    for r in corr_df.to_dict("records"):
        i, j = idx.get(r["factor_a"]), idx.get(r["factor_b"])
        if i is None or j is None or r["corr"] is None:
            continue
        M[i, j] = M[j, i] = float(r["corr"])
    metrics = {}
    for n in names:
        k = metrics_json[n]
        metrics[n] = {
            "ic_mean": k.get("ic_mean"),
            "icir": k.get("ic_ir"),
            "turnover": None,
            "display_name": (BY_CODE.get(n) or {}).get("name_cn") or n,
            "library": "factor_research",
        }
    return names, M, metrics


def _cross_corr(
    our_names: list[str], alpha_names: list[str], max_dates: int = 0
) -> tuple[np.ndarray, int]:
    """月末截面 Spearman：alpha_library 因子值 × factor_research 打分。返回 (mean_corr[A×C], n_dates)。"""
    root = resolve_quantdb_dir()
    scores = pd.read_parquet(
        root / "factor_research" / "monthly_scores.parquet",
        columns=["trade_date", "symbol", "factor_code", "score"],
    )
    scores["symbol"] = scores["symbol"].astype("category")
    scores["factor_code"] = scores["factor_code"].astype("category")
    groups = {pd.Timestamp(d): g for d, g in scores.groupby("trade_date", sort=True)}
    dates = sorted(groups)
    if max_dates:
        dates = dates[-max_dates:]
    acc = np.zeros((len(alpha_names), len(our_names)))
    cnt = np.zeros_like(acc)
    a_idx = np.arange(len(alpha_names))
    o_idx = np.arange(len(our_names))
    used = 0
    for d in dates:
        dt = pd.Timestamp(d).strftime("%Y%m%d")
        part_file = root.joinpath(*ALPHA_DIR) / f"dt={dt}" / "data.parquet"
        if not part_file.exists():
            continue
        try:
            part = pd.read_parquet(part_file, columns=["symbol", *alpha_names])
        except Exception:
            continue
        our_d = groups[d].pivot_table(
            index="symbol", columns="factor_code", values="score", aggfunc="last"
        )
        our_d = our_d.reindex(columns=our_names)
        comb = part.set_index("symbol").join(our_d, how="inner")
        if len(comb) < 60:
            continue
        rk = comb.rank()
        cm = rk.corr(min_periods=50).to_numpy(dtype=np.float64)
        block = cm[np.ix_(a_idx, len(alpha_names) + o_idx)]
        ok = np.isfinite(block)
        acc[ok] += block[ok]
        cnt[ok] += 1
        used += 1
    with np.errstate(invalid="ignore"):
        mean = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan)
    return mean, used


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--min-ic", type=float, default=0.02, help="|IC 均值| 门槛（默认 0.02）"
    )
    ap.add_argument(
        "--min-icir", type=float, default=0.2, help="|ICIR| 门槛（默认 0.2）"
    )
    ap.add_argument(
        "--corr", type=float, default=0.9, help="去重相关阈值 |ρ|（默认 0.9）"
    )
    ap.add_argument(
        "--skip-cross", action="store_true", help="跳过跨库相关（库内去重）"
    )
    ap.add_argument(
        "--refresh-cross", action="store_true", help="忽略跨库相关缓存，重算"
    )
    args = ap.parse_args()

    t0 = time.time()
    print("[1/5] 读取 alpha_library 因子报告快照 ...")
    a_names, a_corr, a_metrics = _load_alpha()
    print(f"      {len(a_names)} 个因子（矩阵 {a_corr.shape}）")
    print("[2/5] 读取 factor_research 指标/相关 ...")
    o_names, o_corr, o_metrics = _load_ours()
    print(f"      {len(o_names)} 个因子")

    overlap = set(a_names) & set(o_names)
    if overlap:
        raise SystemExit(f"因子重名（两库命名空间冲突）: {sorted(overlap)[:5]}")

    n_a = len(a_names)
    names = a_names + o_names
    metrics = {**a_metrics, **o_metrics}
    M = np.full((len(names), len(names)), np.nan)
    M[:n_a, :n_a] = a_corr
    M[n_a:, n_a:] = o_corr

    if args.skip_cross:
        print("[3/5] 跳过跨库相关（--skip-cross）")
        cross_note = "未计算（--skip-cross）"
    else:
        cache_file = (
            resolve_quantdb_dir() / "factor_research" / "screening" / "cross_corr.npz"
        )
        cross = None
        used = 0
        if cache_file.exists() and not args.refresh_cross:
            z = np.load(cache_file)
            cross, used = z["cross"], int(z["used"])
            if cross.shape == (n_a, len(o_names)):
                print(f"[3/5] 载入跨库相关缓存（{used} 期）；--refresh-cross 可重算")
            else:
                cross = None
        if cross is None:
            print("[3/5] 计算跨库月末截面相关（alpha_library × factor_research）...")
            cross, used = _cross_corr(o_names, a_names)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(cache_file, cross=cross, used=used)
            print(
                f"      完成：{used} 期 × {cross.shape}（{time.time() - t0:.0f}s，已缓存）"
            )
        M[:n_a, n_a:] = cross
        M[n_a:, :n_a] = cross.T
        cross_note = f"月末截面 Spearman 均值（{used} 期）"

    print("[4/5] 质量门槛 + 联合去重 ...")
    gated_out: dict[str, str] = {}
    candidates: list[str] = []
    for n in names:
        m = metrics[n]
        ic, icir = m.get("ic_mean"), m.get("icir")
        if ic is None or icir is None:
            gated_out[n] = "无 IC/ICIR 指标（未计算或退化）"
            continue
        if abs(ic) < args.min_ic:
            gated_out[n] = f"|IC|={abs(ic):.4f} < {args.min_ic}"
            continue
        if abs(icir) < args.min_icir:
            gated_out[n] = f"|ICIR|={abs(icir):.3f} < {args.min_icir}"
            continue
        candidates.append(n)

    c_idx = [names.index(n) for n in candidates]
    sub = M[np.ix_(c_idx, c_idx)]
    clusters = cluster_by_correlation(
        candidates, sub.tolist(), metrics, threshold=args.corr, keep="icir"
    )
    dup_of: dict[str, tuple[str, float]] = {}
    cluster_rows = []
    for c in clusters:
        rep = c["representative"]
        cluster_rows.append(c)
        for mem in c["members"]:
            if not mem["is_rep"]:
                dup_of[mem["name"]] = (rep, abs(float(mem.get("corr_to_rep") or 0.0)))

    kept = [n for n in candidates if n not in dup_of]
    kept.sort(key=lambda n: abs(float(metrics[n].get("icir") or 0)), reverse=True)

    print(
        f"      候选 {len(candidates)} → 保留 {len(kept)}（门槛剔除 {len(gated_out)}，去重剔除 {len(dup_of)}）"
    )

    print("[5/5] 落盘 ...")
    out_dir = resolve_quantdb_dir() / "factor_research" / "screening"
    out_dir.mkdir(parents=True, exist_ok=True)

    def _row(n: str) -> dict:
        m = metrics[n]
        b = BY_CODE.get(n) or {}
        return {
            "name": n,
            "display_name": m.get("display_name"),
            "library": m.get("library"),
            "l1": b.get("l1", ""),
            "l2": b.get("l2", ""),
            "ic_mean": m.get("ic_mean"),
            "icir": m.get("icir"),
            "turnover": m.get("turnover"),
        }

    selected = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "gates": {
            "min_abs_ic": args.min_ic,
            "min_abs_icir": args.min_icir,
            "corr_threshold": args.corr,
        },
        "cross_corr": cross_note,
        "counts": {
            "candidates": len(candidates),
            "kept": len(kept),
            "gated_out": len(gated_out),
            "deduped": len(dup_of),
            "total_considered": len(names),
        },
        "kept": [_row(n) for n in kept],
        "dropped_gated": [
            {"name": n, "library": metrics[n]["library"], "reason": r}
            for n, r in sorted(gated_out.items())
        ],
        "dropped_duplicate": [
            {
                "name": n,
                "library": metrics[n]["library"],
                "duplicate_of": rep,
                "abs_corr": round(cc, 3),
            }
            for n, (rep, cc) in sorted(dup_of.items())
        ],
        "clusters": cluster_rows,
        "cluster_summary": summarize(len(names), cluster_rows),
    }
    (out_dir / "factor_selection.json").write_text(
        json.dumps(selected, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_dir / "kept_features.txt").write_text(
        "\n".join(n for n in kept) + "\n", encoding="utf-8"
    )

    # ---- 报告 md ----
    lines = []
    lines.append(f"# 因子筛选报告（{pd.Timestamp.now().strftime('%Y-%m-%d')}）")
    lines.append("")
    lines.append(
        "> 来源：因子报告快照（alpha_library 429）+ 因子研究指标（factor_research 73）"
    )
    lines.append(
        f"> 门槛：|IC 均值| ≥ {args.min_ic}，|ICIR| ≥ {args.min_icir}；去重：联合相关 |ρ| ≥ {args.corr} 每簇留最优"
    )
    lines.append(f"> 跨库相关：{cross_note}")
    lines.append("")
    lines.append("## 统计")
    lines.append("")
    lines.append("| 项 | 数量 |")
    lines.append("|---|---|")
    lines.append(f"| 参与筛选 | {len(names)} |")
    lines.append(f"| 过门槛候选 | {len(candidates)} |")
    lines.append(f"| **最终保留** | **{len(kept)}** |")
    lines.append(f"| 门槛剔除 | {len(gated_out)} |")
    lines.append(f"| 同源去重剔除 | {len(dup_of)}（{len(cluster_rows)} 个簇） |")
    lines.append("")
    for is_ours, label in (
        (False, "Alpha 库（Alpha101 / GTJA191 / Alpha158）"),
        (True, "因子研究（行情+财务+行为 82）"),
    ):
        rows = [
            n for n in kept if (metrics[n]["library"] == "factor_research") == is_ours
        ]
        lines.append(f"## 保留清单 · {label}（{len(rows)}）")
        lines.append("")
        if not rows:
            lines.append("（无）")
            lines.append("")
            continue
        lines.append("| # | 因子 | 子库 | 说明 | IC 均值 | ICIR | 换手 |")
        lines.append("|---|---|---|---|---|---|---|")
        for i, n in enumerate(rows, 1):
            m = metrics[n]
            tv = f"{m['turnover']:.2f}" if m.get("turnover") is not None else "—"
            lines.append(
                f"| {i} | `{n}` | {m.get('library')} | {m.get('display_name')} | "
                f"{m.get('ic_mean')} | {m.get('icir')} | {tv} |"
            )
        lines.append("")
    lines.append(f"## 去重剔除（{len(dup_of)}）—— 与保留因子同源，**不要重复进训练**")
    lines.append("")
    lines.append("| 因子 | 重复于 | \\|ρ\\| | 说明 |")
    lines.append("|---|---|---|---|")
    for n, (rep, cc) in sorted(dup_of.items(), key=lambda x: -x[1][1]):
        lines.append(
            f"| `{n}` | `{rep}` | {cc:.3f} | {metrics[n].get('display_name')} |"
        )
    lines.append("")
    lines.append("### 去重簇一览（按代表强度降序，前 15 簇）")
    lines.append("")
    for c in cluster_rows[:15]:
        mems = "、".join(f"`{m['name']}`" for m in c["members"] if not m["is_rep"])
        rep = c["representative"]
        lines.append(f"- **{rep}**（保留，|ρ| 基准）← 同源 {c['size'] - 1} 个：{mems}")
    lines.append("")
    lines.append("## 训练接入")
    lines.append("")
    lines.append(
        "- 特征清单：`kept_features.txt`（每行一个因子名；跨库使用时按所属数据集分别取列）"
    )
    lines.append(
        "- 机器可读结果：`factor_selection.json`（kept / dropped 原因 / 簇结构）"
    )
    lines.append(
        "- 提醒：>0.9 相关的一对因子只保留一个即可；本清单已剔除同源冗余，直接进特征选择不会互相稀释。"
    )
    lines.append("")
    md_name = f"筛选报告_{pd.Timestamp.now().strftime('%Y%m%d')}.md"
    (out_dir / md_name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"完成 → {out_dir}（{time.time() - t0:.0f}s）")
    print(f"  保留 {len(kept)} 个；报告 {md_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
