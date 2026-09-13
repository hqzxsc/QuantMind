#!/usr/bin/env python3
"""因子研究快照构建（QuantDB → factor_research 数据集）

用法（仓库根执行）:
    python3 backend/scripts/build_factor_research.py                 # 全量 2020-01 至今
    python3 backend/scripts/build_factor_research.py --smoke 200     # 冒烟：200 只 + 2023 起
    python3 backend/scripts/build_factor_research.py --skip-financial

产物（<quantdb>/factor_research/）:
    meta.json           构建元信息（时间窗/股票池/基准/列数）
    metrics.json        每因子 KPI + 排行榜（综合分）
    ic.parquet          月频 IC 序列（date × factor）
    nav.parquet         Top-N 回测净值（date × factor；top_n 见 meta）
    corr.parquet        因子两两相关（全窗口均值）
    monthly_scores.parquet  月末截面打分（合成/对比用）
    fwd_returns.parquet 月末→次月末持有期收益
    holdings_latest.json    最新一期各因子 Top-N 持仓
    benchmarks.parquet  基准（中证500）净值
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

from backend.services.engine.factor_research import analysis  # noqa: E402
from backend.services.engine.factor_research import data as frdata  # noqa: E402
from backend.services.engine.factor_research import engine, financials  # noqa: E402
from backend.services.engine.factor_research.catalog import FACTORS, BY_CODE  # noqa: E402

LOOKBACK_START = "2018-06-01"  # 252 日窗口预热
DEFAULT_START = "2020-01-01"
TOP_N = 30


def _out_dir() -> Path:
    from backend.shared.quantdb_paths import resolve_quantdb_dir

    d = resolve_quantdb_dir() / "factor_research"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=DEFAULT_START, help="首个采样月末（含）")
    ap.add_argument("--end", default=None, help="截止日（默认最新）")
    ap.add_argument("--smoke", type=int, default=0, help="冒烟模式：只取 N 只股票")
    ap.add_argument(
        "--skip-financial", action="store_true", help="跳过财务/行为类（仅行情+估值）"
    )
    ap.add_argument("--top-n", type=int, default=TOP_N)
    args = ap.parse_args()

    t0 = time.time()
    out = _out_dir()
    print(
        f"[1/7] 载入 daily_forward / valuation / index ... ({LOOKBACK_START} ~ {args.end or 'latest'})"
    )
    daily = frdata.load_daily_panel(LOOKBACK_START, args.end or "20991231")
    val = frdata.load_valuation_panel(LOOKBACK_START, args.end or "20991231")
    bench_close = frdata.load_index_close(LOOKBACK_START, args.end or "20991231")
    instr = frdata.load_instrument()

    close = daily["close"]
    if args.smoke:
        syms = list(close.columns)[: args.smoke]
        for d_ in (daily, val):
            for k in d_:
                d_[k] = d_[k][syms]
        close = daily["close"]

    dates = [
        d for d in frdata.month_end_dates(close.index) if d >= pd.Timestamp(args.start)
    ]
    universe = frdata.build_universe(instr, close)
    print(
        f"      交易日 {len(close)}，采样日 {len(dates)}（{dates[0].date()} ~ {dates[-1].date()}），股票 {close.shape[1]}"
    )

    print("[2/7] 行情类 + 估值类因子 ...")
    raw: dict[str, pd.DataFrame] = {}
    raw.update(engine.compute_trade_factors(daily, val, instr, dates))
    raw.update(engine.compute_valuation_factors(val, instr, dates, pb_daily=val["pb"]))
    print(f"      {len(raw)} 个")

    if not args.skip_financial:
        print("[3/7] 财务类 + 行为类因子（PIT）...")
        syms = list(close.columns)
        fin_dirs = {
            "income": frdata.resolve_quantdb_subdir(*frdata.INCOME_PARTS),
            "balance": frdata.resolve_quantdb_subdir(*frdata.BALANCE_PARTS),
            "cashflow": frdata.resolve_quantdb_subdir(*frdata.CASHFLOW_PARTS),
            "pershare": frdata.resolve_quantdb_subdir(*frdata.PERSHARE_PARTS),
        }
        fin = financials.compute_financial_factors(
            syms, fin_dirs, dates, total_mv=val["total_mv"]
        )
        beh = financials.compute_behavior_factors(
            syms,
            frdata.resolve_quantdb_subdir(*frdata.HOLDER_PARTS),
            dates,
            circ_cap=val["circulating_capital"],
        )
        raw.update(fin)
        raw.update(beh)
        print(f"      累计 {len(raw)} 个")
    else:
        print("[3/7] 跳过财务/行为类")

    print("[4/7] 截面打分（rank→正态分位，按方向）...")
    scores: dict[str, pd.DataFrame] = {}
    for code, df in raw.items():
        meta = BY_CODE.get(code)
        if meta is None:
            continue
        scores[code] = engine.rank_to_score(
            df.reindex(dates), int(meta["direction"]), universe.reindex(dates)
        )

    print("[5/7] 持有期收益 / IC / 回测 ...")
    fwd = analysis.forward_returns(close, dates, universe)
    metrics: dict[str, dict] = {}
    ic_all, nav_all, holdings_latest = [], [], {}
    for code, sc in scores.items():
        ic = analysis.ic_series(sc, fwd)
        bt = analysis.backtest_topn(sc, fwd, top_n=args.top_n)
        k = analysis.kpi(bt["ret"], bt["nav"])
        k["ic_mean"] = round(float(ic.mean()), 4) if ic.notna().any() else None
        k["ic_std"] = round(float(ic.std()), 4) if ic.notna().any() else None
        k["ic_ir"] = (
            round(float(ic.mean() / ic.std()), 3)
            if ic.notna().any() and ic.std()
            else None
        )
        k["ic_win_rate"] = (
            round(float((ic > 0).mean()), 4) if ic.notna().any() else None
        )
        metrics[code] = k
        ic_all.append(ic.rename(code))
        nav_all.append(bt["nav"].rename(code))
        if bt["holdings"]:
            holdings_latest[code] = bt["holdings"][max(bt["holdings"])]
    ic_df = pd.concat(ic_all, axis=1)
    nav_df = pd.concat(nav_all, axis=1)

    print("[6/7] 因子相关矩阵 ...")
    corr = analysis.correlation_pairs(scores)

    print("[7/7] 落盘 ...")
    long_rows = []
    for code, sc in scores.items():
        s = sc.stack()
        long_rows.append(
            pd.DataFrame(
                {
                    "trade_date": s.index.get_level_values(0),
                    "symbol": s.index.get_level_values(1),
                    "factor_code": code,
                    "score": s.values.astype("float32"),
                }
            )
        )
    long = pd.concat(long_rows, ignore_index=True)
    long.to_parquet(out / "monthly_scores.parquet", index=False)

    fwd_long = fwd.stack()
    pd.DataFrame(
        {
            "trade_date": fwd_long.index.get_level_values(0),
            "symbol": fwd_long.index.get_level_values(1),
            "fwd_ret": fwd_long.values.astype("float32"),
        }
    ).to_parquet(out / "fwd_returns.parquet", index=False)

    ic_out = ic_df.stack()
    pd.DataFrame(
        {
            "trade_date": ic_out.index.get_level_values(0),
            "factor_code": ic_out.index.get_level_values(1),
            "ic": ic_out.values.astype("float32"),
        }
    ).to_parquet(out / "ic.parquet", index=False)

    nav_rows = []
    for code in nav_df.columns:
        s = nav_df[code].dropna()
        for d, v in s.items():
            nav_rows.append({"trade_date": d, "factor_code": code, "nav": float(v)})
    pd.DataFrame(nav_rows).to_parquet(out / "nav.parquet", index=False)
    corr.to_parquet(out / "corr.parquet", index=False)

    bench = bench_close.reindex(dates).dropna()
    bench_nav = (bench / bench.iloc[0]).rename("nav").reset_index()
    bench_nav.columns = ["trade_date", "nav"]
    bench_nav["index_code"] = frdata.BENCHMARK_SYMBOL
    bench_nav.to_parquet(out / "benchmarks.parquet", index=False)

    lb = analysis.leaderboard(metrics)
    available = {f["code"]: f for f in FACTORS if f["available"]}
    meta = {
        "built_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "window": [str(dates[0].date()), str(dates[-1].date())],
        "n_dates": len(dates),
        "n_symbols": int(close.shape[1]),
        "top_n": args.top_n,
        "cost_rate": analysis.COST_RATE,
        "benchmark": frdata.BENCHMARK_SYMBOL,
        "n_factors_total": len(FACTORS),
        "n_factors_available": len(available),
        "n_factors_computed": len(scores),
        "elapsed_sec": round(time.time() - t0, 1),
        "skip_financial": bool(args.skip_financial),
        "smoke": args.smoke,
    }

    # NaN/Inf → null（Python json 默认放行 NaN，但 FastAPI 序列化会拒绝，故在源头消毒）
    def _clean(o):
        if isinstance(o, float):
            return o if np.isfinite(o) else None
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_clean(v) for v in o]
        return o

    (out / "metrics.json").write_text(
        json.dumps(
            _clean({"leaderboard": lb, "metrics": metrics, "meta": meta}),
            ensure_ascii=False,
            indent=1,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    (out / "holdings_latest.json").write_text(
        json.dumps(
            {"date": str(dates[-1].date()), "holdings": holdings_latest},
            ensure_ascii=False,
            indent=1,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    print(
        f"完成 → {out}（{meta['elapsed_sec']}s，因子 {len(scores)} 个，样本 {len(dates)} 期）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
