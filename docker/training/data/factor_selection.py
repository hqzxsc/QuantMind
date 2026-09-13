"""因子筛选（P2 由 train.py 拆出，逐行搬运，含自适应回填）。

IC/ICIR 双阈值初筛 → 综合评分自适应回填 → 相关性去冗余 → 稳定性检验
→ 扰动保真度(PFS) → 输出。
阈值做软地板：弱窗口按 score=|IC|×|ICIR| 回填，强窗口输出与老行为一致。

PFS/DH 两道质量闸门（对齐 AlphaEval，KDD 2026，见 data/factor_quality.py）：
  - Step 3 相关性剪枝在「两两 |ρ| < 阈值」之外加一道**多样性增益**：
    新增因子至少要贡献 dh_min_gain 个有效因子（N_eff 增量），否则跳过——
    两两阈值对「与多只同时中等相关」的多重共线是盲的。
  - Step 5 对入选清单做**扰动保真度**检验：截面 z 分加噪后排名塌掉的因子
    换成后续候选（PFS 回填），保证进模型的特征对数据误差不敏感。
两道闸门均可经 factor_selection 配置关闭，关闭后与老行为逐行一致。
"""
from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
import pandas as pd

from data.factor_quality import compute_pfs, diversity_entropy, effective_factors

logger = logging.getLogger("quantmind.train")

# PFS 淘汰后保留名单的下限：低于此数视为「判定过严/数据异常」，保留原名单（老行为）
_PFS_MIN_SURVIVORS = 30


def _stability_ok(ic_result: dict[str, Any]) -> bool | None:
    """Step 4 稳定性判据（滚动60日 IC 标准差 / |IC均值| < 2）。

    True/False = 通过/不通过；None = 逐日序列缺失或过短，无法判定
    （老行为：不入 stable、也不写淘汰原因）。
    """
    daily_series = ic_result.get("daily_ics")
    if not daily_series or len(daily_series) < 20:
        return None
    daily_ics = np.asarray(daily_series, dtype=np.float64)
    rolling_std = pd.Series(daily_ics).rolling(60, min_periods=20).std()
    mean_ic = abs(float(np.mean(daily_ics)))
    return bool(mean_ic > 0 and rolling_std.mean() / (mean_ic + 1e-9) < 2.0)


def select_top_factors(
    df: pd.DataFrame,
    features: list[str],
    label_col: str = "label",
    n_top: int = 80,
    ic_threshold: float = 0.01,
    icir_threshold: float = 0.15,
    correlation_threshold: float = 0.9,
    min_pool: int | None = None,
    pfs_enabled: bool = True,
    pfs_threshold: float = 0.9,
    pfs_sigma: float = 0.1,
    dh_enabled: bool = True,
    dh_min_gain: float = 0.1,
) -> tuple[list[str], dict[str, Any]]:
    """专业因子筛选：IC/ICIR 初筛 → 自适应回填 → 相关性去冗余 → 稳定性检验 → PFS。

    自适应规则（阈值做软地板，排序取优）：
    - 阈值双过者为优选池，按 |ICIR| 降序排在前（与老行为一致）；
    - 优选不足 min_pool（默认 max(10, n_top//4)）时，按综合评分
      score=|IC均值|×|ICIR| 从达标样本天数的落选者中回填到
      min(n_top, max(len(优选), min(min_pool, 合格数)))，回填者按评分排在后；
    - 弱窗口下不再出现"全军覆没/只剩 1 个"，强窗口下优选已够数则输出与老行为逐行一致。

    质量闸门（可关，关闭即老行为）：
    - dh_enabled/dh_min_gain：相关性剪枝时附加多样性增益下限（N_eff 增量）；
    - pfs_enabled/pfs_threshold：入选清单做扰动保真度检验，塌者换后续候选；
      pfs_sigma 为截面 z 分上的噪声幅度（高斯与 t 厚尾两轮取最差）。

    返回 (selected, report)：
    - selected: 入选特征列表
    - report: 结构化筛选报告（每特征的 IC/ICIR/覆盖率/PFS/入选或淘汰原因），
      写入 result metadata，供前端展示"为什么选/为什么不选"。
      报告只增键（pfs/dh_gain/diversity/pfs_fallback 等），
      老键集合与语义不变，前端无需改动。

    report 结构:
        {
          "method": "ic_icir",
          "thresholds": {"n_top", "ic_threshold", "icir_threshold", "correlation_threshold", "min_pool",
                         "pfs_threshold", "pfs_sigma", "dh_min_gain"},
          "stage_counts": {"input", "ic_pass", "corr_pass", "stable", "pfs_pass", "selected",
                           "backfilled", "dh_rejected"},
          "train_rows": int,
          "diversity": {"n_factors", "n_eff", "entropy"} | None,
          "pfs_fallback": bool,
          "features": [
            {"name", "ic", "icir", "ic_positive_rate", "n_days", "coverage", "status", "reason",
             "score", "backfilled", "pfs", "pfs_gauss", "pfs_t", "dh_gain"}
          ],
          "backfilled_features": [str],
        }
    """
    logger.info("=== Factor Selection: IC/ICIR screening ===")
    logger.info("Input: %d features, target top-%d", len(features), n_top)

    # 覆盖率：训练段特征非空比例（与 NaN 数据洞直接挂钩，如 L2 vpin 系 2024Q4-2025Q1 缺失）
    coverage_map: dict[str, float] = {}
    present_cols = [f for f in features if f in df.columns]
    if present_cols:
        try:
            cov_arr = df[present_cols].notna().mean(axis=0)
            coverage_map = {f: float(cov_arr[f]) for f in present_cols}
        except Exception:
            coverage_map = {f: 1.0 for f in present_cols}

    # Step 1: 日频 Rank IC 计算（原始 spearmanr 算法，数值 100% 正确）
    # 多进程并行：按特征分片给多个进程同时算（fork，DataFrame 零拷贝共享），
    # 默认 min(CPU 核数, 特征数) 个 worker，TRAIN_IC_WORKERS 环境变量可覆盖。
    # 算法、逐日循环、dropna 规则与旧串行实现完全一致——只加速不近似。
    t0_sel = time.time()
    ic_results: dict[str, dict] = {}
    try:
        from parallel_utils import compute_daily_ics

        ic_results = compute_daily_ics(df, features, label_col=label_col)
        logger.info(
            "IC/ICIR screening done in %.1fs (%d features)",
            time.time() - t0_sel, len(ic_results),
        )
    except ImportError:
        # parallel_utils 未随 train.py 同步（旧镜像/旧编排器）：回退串行，不中断训练
        logger.warning("parallel_utils not found, falling back to serial IC computation")
        from scipy.stats import spearmanr

        for feat in features:
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
                ic_results[feat] = {"ic_mean": 0.0, "icir": 0.0, "ic_positive_rate": 0.0, "n_days": len(daily_ics)}
                continue
            arr = np.array(daily_ics)
            ic_results[feat] = {
                "ic_mean": float(np.mean(arr)),
                "icir": float(np.mean(arr) / (np.std(arr) + 1e-9)),
                "ic_positive_rate": float(np.mean(arr > 0)),
                "n_days": len(arr),
                "daily_ics": daily_ics,  # 供 Step 4 稳定性检验复用，避免二次计算
            }
        logger.info("IC/ICIR screening done in %.1fs (%d features)", time.time() - t0_sel, len(ic_results))

    # 逐特征决策原因（status: selected / rejected + reason 说明被哪个门槛淘汰）
    decisions: dict[str, str] = {}
    for feat in features:
        if feat not in ic_results:
            decisions[feat] = "特征不在训练数据中"
        elif int(ic_results[feat].get("n_days") or 0) < 20:
            decisions[feat] = "IC 有效样本天数不足(<20日)"
        else:
            decisions[feat] = ""  # 进入阈值判定

    # Step 2: IC阈值初筛
    candidates = {}
    for f, r in ic_results.items():
        if f not in decisions or decisions[f]:
            continue
        if abs(r["ic_mean"]) >= ic_threshold and abs(r["icir"]) >= icir_threshold:
            candidates[f] = r
        elif abs(r["ic_mean"]) < ic_threshold:
            decisions[f] = f"|IC|={abs(r['ic_mean']):.4f} < 阈值 {ic_threshold}"
        else:
            decisions[f] = f"|ICIR|={abs(r['icir']):.3f} < 阈值 {icir_threshold}"
    logger.info("After IC/ICIR threshold: %d candidates (|IC|>=%.2f, |ICIR|>=%.1f)",
                len(candidates), ic_threshold, icir_threshold)

    # Step 2b: 自适应回填（阈值做软地板）。优选不足 min_pool 时，按综合评分
    # score=|IC|×|ICIR| 从样本天数达标的落选者中回填；优选已够数时零行为变更。
    resolved_min_pool = min_pool if min_pool is not None else max(10, n_top // 4)

    def _factor_score(r: dict) -> float:
        try:
            return abs(float(r.get("ic_mean") or 0.0)) * abs(float(r.get("icir") or 0.0))
        except (TypeError, ValueError):
            return 0.0

    eligible = [
        f for f in features
        if f in ic_results and int(ic_results[f].get("n_days") or 0) >= 20
    ]
    pool_target = min(n_top, max(len(candidates), min(resolved_min_pool, len(eligible))))
    backfilled: list[str] = []
    if len(candidates) < pool_target:
        rest = sorted(
            (f for f in eligible if f not in candidates),
            key=lambda f: _factor_score(ic_results[f]), reverse=True,
        )
        for feat in rest:
            if len(candidates) + len(backfilled) >= pool_target:
                break
            score = _factor_score(ic_results[feat])
            prev_reason = decisions.get(feat) or "未达阈值"
            decisions[feat] = f"{prev_reason}；未达阈值，按综合评分回填(score={score:.4f})"
            backfilled.append(feat)
    if backfilled:
        logger.info("Adaptive backfill: %d preferred + %d backfilled (pool_target=%d, min_pool=%d)",
                    len(candidates), len(backfilled), pool_target, resolved_min_pool)

    # Step 3: 排序 + 贪心去冗余。优选按 |ICIR|（老口径），回填按综合评分缀后；
    # 优选已够数时 backfilled 为空，输出与老行为一致。
    # dh_enabled 时叠加多样性增益闸门：两两相关阈值放行的候选，若对集合的
    # 有效因子数 N_eff 增量不足（与多只同时中等相关的多重共线），同样跳过。
    preferred_order = sorted(candidates.keys(),
        key=lambda f: abs(candidates[f]["icir"]), reverse=True)
    backfill_order = sorted(backfilled,
        key=lambda f: _factor_score(ic_results[f]), reverse=True)
    sorted_features = preferred_order + backfill_order

    selected: list[str] = []
    dh_gains: dict[str, float] = {}
    dh_rejected = 0
    for feat in sorted_features:
        if len(selected) >= n_top:
            decisions[feat] = "超出 top-N 名额"
            continue
        if len(selected) == 0:
            selected.append(feat)
            continue
        # 抽样计算相关性（全量可能 OOM）
        sample_n = min(50000, len(df))
        corr_df = df[selected + [feat]].sample(sample_n, random_state=42).corr()
        max_corr = corr_df[feat].drop(feat).abs().max()
        if max_corr >= correlation_threshold:
            decisions[feat] = f"与已选特征相关性 {max_corr:.3f} >= {correlation_threshold}"
            continue
        if dh_enabled and len(selected) >= 2:
            # 同一抽样下对比「加入前/后」的有效因子数（特征值谱，~1ms/次）
            n_eff_new = effective_factors(corr_df.to_numpy())
            n_eff_old = effective_factors(corr_df.loc[selected, selected].to_numpy())
            if n_eff_new is not None and n_eff_old is not None:
                gain = float(n_eff_new - n_eff_old)
                dh_gains[feat] = round(gain, 4)
                if gain < dh_min_gain:
                    dh_rejected += 1
                    decisions[feat] = (
                        f"多样性增益不足 ΔN_eff={gain:+.3f} < {dh_min_gain}"
                        f"（已选 {len(selected)} 个，最大相关 {max_corr:.3f}）"
                    )
                    continue
        selected.append(feat)

    logger.info("After correlation pruning (thresh=%.2f): %d selected (diversity gate rejected %d)",
                correlation_threshold, len(selected), dh_rejected)
    corr_pass_count = len(selected)

    # Step 4: 稳定性检验（复用 Step 1 已算的逐日 IC 序列，避免二次双重循环）
    # 滚动60日 IC 标准差 / 均值 → 稳定性比率
    stable = []
    for feat in selected:
        ok = _stability_ok(ic_results[feat])
        if ok is True:
            stable.append(feat)
        elif ok is False:
            decisions[feat] = "IC 稳定性不足(滚动60日波动 > 2×|IC均值|)"
        # ok is None（逐日 IC 序列缺失/过短）→ 与老行为一致：不入 stable、不判定
    if len(stable) >= 30:
        selected = stable[:n_top]
        logger.info("After stability filter: %d stable factors", len(selected))
    else:
        # 稳定因子不足 30 个时保留相关性去冗余后的名单（老行为），
        # 并把被稳定性淘汰的决策回收（它们仍入选）
        for feat in selected:
            decisions.pop(feat, None)
    stable_count = len(stable) if len(stable) >= 30 else len(selected)

    # Step 5: 扰动保真度（PFS）。入选清单做两轮噪声注入（高斯 + t 厚尾），
    # 排名一扰就塌的因子换成后续候选（只从「被 top-N 名额截止」的候选人里补，
    # 不动相关性/稳定性已淘汰的名单）。淘汰过多（<30）视为判定过严 → 保留原名单。
    pfs_info: dict[str, dict[str, Any]] = {}
    pfs_fallback = False
    pfs_swaps = 0
    pfs_filled: list[str] = []
    pfs_pass_count = len(selected)  # PFS 未启用/未触发时漏斗该级等于上一级
    if pfs_enabled and len(selected) >= 10:
        t_pfs = time.time()
        selected_before = set(selected)
        # 回填候选 = 相关性/多样性放行、只是被 top-N 名额截止的，且同样要通过
        # 稳定性检验（不然 PFS 回填会绕过 Step 4，最终名单口径不一致）
        backfill_pool = [
            f for f in sorted_features
            if f not in selected_before and decisions.get(f) == "超出 top-N 名额"
            and _stability_ok(ic_results[f]) is not False
        ]
        # 打分预算：入选数 + 同量级回填候选，防止极端数据下打分面无限膨胀
        budget = max(0, min(len(backfill_pool), n_top))
        scored = selected + backfill_pool[:budget]
        pfs_info = compute_pfs(df, scored, sigma=pfs_sigma)
        survivors = [
            f for f in selected
            if (pfs_info.get(f) or {}).get("pfs") is None
            or (pfs_info.get(f) or {})["pfs"] >= pfs_threshold
        ]
        rejected_by_pfs = [f for f in selected if f not in set(survivors)]
        # 生存者下限：低于此数视为「判定过严/数据异常」回退原名单。与 n_top 取小——
        # n_top 很小的训练（如 20）若用固定 30 做下限，闸门将永远无法生效（静默空转）。
        pfs_min_survivors = min(_PFS_MIN_SURVIVORS, max(5, n_top // 2))
        if rejected_by_pfs and len(survivors) >= pfs_min_survivors:
            fill: list[str] = []
            for f in backfill_pool[:budget]:
                if len(survivors) + len(fill) >= n_top:
                    break
                p = (pfs_info.get(f) or {}).get("pfs")
                if p is None or p >= pfs_threshold:
                    fill.append(f)
                    decisions[f] = "PFS 回填（原名单有因子未通过扰动保真度检验）"
            pfs_swaps = len(rejected_by_pfs)
            for f in rejected_by_pfs:
                p = (pfs_info.get(f) or {}).get("pfs")
                decisions[f] = f"扰动保真度不足 PFS={p:.3f} < {pfs_threshold}"
            selected = survivors + fill
            pfs_filled = fill
        elif rejected_by_pfs:
            # 保留原名单（老行为），PFS 仅进报告标注
            pfs_fallback = True
        pfs_pass_count = len(selected) if pfs_fallback else len(survivors)
        logger.info(
            "After perturbation fidelity (thresh=%.2f): %d selected (%d swapped%s) in %.1fs",
            pfs_threshold, len(selected), pfs_swaps,
            ", fallback to pre-PFS list" if pfs_fallback else "", time.time() - t_pfs,
        )

    # 输出 top-10 供日志
    for i, feat in enumerate(selected[:10]):
        r = ic_results[feat]
        logger.info("  %2d. %-30s IC=%.4f  ICIR=%.3f  IC>0=%.1f%%",
                    i + 1, feat, r["ic_mean"], r["icir"], r["ic_positive_rate"] * 100)

    # 入选集合多样性 KPI（同一抽样口径；供报告与前端展示"这些特征相当于几个独立因子"）
    diversity: dict[str, Any] | None = None
    if selected:
        try:
            sample_n = min(50000, len(df))
            sel_corr = df[selected].sample(sample_n, random_state=42).corr().to_numpy()
            n_eff = effective_factors(sel_corr)
            if n_eff is not None:
                diversity = {
                    "n_factors": len(selected),
                    "n_eff": round(float(n_eff), 2),
                    "entropy": round(float(diversity_entropy(sel_corr) or 0.0), 4),
                }
        except Exception as exc:  # noqa: BLE001 — KPI 失败不影响筛选结果
            logger.warning("Diversity KPI computation failed: %s", exc)

    # ── 组装结构化筛选报告（只增键：pfs/dh_gain/diversity/pfs_fallback 等）──
    selected_set = set(selected)
    backfilled_set = set(backfilled)
    pfs_filled_set = set(pfs_filled)
    report_features = []
    for feat in features:
        r = ic_results.get(feat)
        pfs_row = pfs_info.get(feat) or {}
        if r is None:
            report_features.append({
                "name": feat, "ic": None, "icir": None, "ic_positive_rate": None,
                "n_days": 0, "coverage": round(coverage_map.get(feat, 1.0), 4),
                "status": "rejected", "reason": decisions.get(feat, "特征不在训练数据中"),
                "score": 0.0, "backfilled": False,
                "pfs": pfs_row.get("pfs"), "pfs_gauss": pfs_row.get("pfs_gauss"),
                "pfs_t": pfs_row.get("pfs_t"), "dh_gain": dh_gains.get(feat),
                "pfs_backfilled": feat in pfs_filled_set,
            })
            continue
        feat_score = round(abs(float(r.get("ic_mean") or 0.0)) * abs(float(r.get("icir") or 0.0)), 6)
        report_features.append({
            "name": feat,
            "ic": round(float(r.get("ic_mean", 0.0)), 4),
            "icir": round(float(r.get("icir", 0.0)), 3),
            "ic_positive_rate": round(float(r.get("ic_positive_rate", 0.0)), 4),
            "n_days": int(r.get("n_days", 0)),
            "coverage": round(coverage_map.get(feat, 1.0), 4),
            "status": "selected" if feat in selected_set else "rejected",
            "reason": "通过全部筛选" if feat in selected_set else (decisions.get(feat) or "未通过筛选"),
            "score": feat_score,
            "backfilled": feat in backfilled_set,
            "pfs": round(pfs_row["pfs"], 4) if pfs_row.get("pfs") is not None else None,
            "pfs_gauss": round(pfs_row["pfs_gauss"], 4) if pfs_row.get("pfs_gauss") is not None else None,
            "pfs_t": round(pfs_row["pfs_t"], 4) if pfs_row.get("pfs_t") is not None else None,
            "dh_gain": dh_gains.get(feat),
            "pfs_backfilled": feat in pfs_filled_set,
        })
    report_features.sort(key=lambda x: (x["status"] != "selected", -(abs(x["icir"] or 0))))
    report = {
        "method": "ic_icir",
        "thresholds": {
            "n_top": n_top,
            "ic_threshold": ic_threshold,
            "icir_threshold": icir_threshold,
            "correlation_threshold": correlation_threshold,
            "min_pool": resolved_min_pool,
            "pfs_threshold": pfs_threshold if pfs_enabled else None,
            "pfs_sigma": pfs_sigma if pfs_enabled else None,
            "dh_min_gain": dh_min_gain if dh_enabled else None,
        },
        "diversity": diversity,
        "pfs_fallback": pfs_fallback,
        "stage_counts": {
            "input": len(features),
            "ic_pass": len(candidates),
            "corr_pass": corr_pass_count,
            "stable": stable_count,
            "pfs_pass": pfs_pass_count,
            "pfs_backfilled": len(pfs_filled),
            "dh_rejected": dh_rejected,
            "selected": len(selected),
            "backfilled": len(backfilled),
        },
        "train_rows": int(len(df)),
        "features": report_features,
        "selected": selected,
        "backfilled_features": backfilled,
    }
    return selected, report


def _log_factor_selection_summary(report: dict[str, Any]) -> None:
    """把筛选报告压缩成可读日志：漏斗 + 质量闸门摘要 + 淘汰原因统计 + "可惜"名单。"""
    sc = report.get("stage_counts") or {}
    logger.info(
        "Factor selection funnel: %d -> IC/ICIR %d -> corr %d -> stable %d -> PFS %d -> selected %d",
        sc.get("input", 0), sc.get("ic_pass", 0), sc.get("corr_pass", 0),
        sc.get("stable", 0), sc.get("pfs_pass", 0), sc.get("selected", 0),
    )
    div = report.get("diversity") or {}
    thr = report.get("thresholds") or {}
    if div or sc.get("dh_rejected") or report.get("pfs_fallback"):
        logger.info(
            "Quality gates: %s factors ~= %s effective (diversity entropy %s); "
            "dh min gain %s rejected %d; pfs threshold %s%s",
            div.get("n_factors", sc.get("selected", 0)), div.get("n_eff", "?"),
            div.get("entropy", "?"), thr.get("dh_min_gain"), sc.get("dh_rejected", 0),
            thr.get("pfs_threshold"),
            " [PFS FALLBACK: kept pre-PFS list]" if report.get("pfs_fallback") else "",
        )
    reasons: dict[str, int] = {}
    for f in report.get("features") or []:
        if f.get("status") != "selected":
            r = str(f.get("reason") or "未通过筛选")
            reasons[r] = reasons.get(r, 0) + 1
    for r, cnt in sorted(reasons.items(), key=lambda kv: -kv[1]):
        logger.info("  rejected x%-3d %s", cnt, r)
    # 高 |ICIR| 却被拒的特征（"可惜"名单：多为稳定性/覆盖率问题）
    rejected = [
        f for f in (report.get("features") or [])
        if f.get("status") != "selected" and f.get("icir")
    ]
    notable = sorted(rejected, key=lambda f: -abs(f["icir"]))[:8]
    if notable:
        logger.info("Notable rejections (high |ICIR| but dropped):")
        for f in notable:
            logger.info(
                "  %-32s IC=%.4f ICIR=%.3f cov=%.0f%% -> %s",
                f.get("name", ""), f.get("ic", 0.0), f.get("icir", 0.0),
                (f.get("coverage") or 0.0) * 100, f.get("reason", ""),
            )
