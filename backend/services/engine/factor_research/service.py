"""因子研究 —— 服务层：目录 / 排行榜 / 单因子 / 对比 / 实时合成。

所有数据来自 store 层快照；compose 在请求内用月末打分现算（73 因子 × ~80 期，
向量化毫秒级），不在线重算原始因子。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.services.engine.factor_research import analysis, store
from backend.services.engine.factor_research.catalog import BY_CODE, FACTORS, L1_ORDER


def _instrument_names() -> dict[str, dict]:
    """symbol → {name, industry}（证券主表快照，缺失时优雅降级）。"""
    try:
        from backend.services.engine.factor_research import data as frdata

        instr = frdata.load_instrument()
        return {
            r["symbol"]: {"name": r["name"], "industry": r["industry"]}
            for _, r in instr.iterrows()
        }
    except Exception:
        return {}


def catalog() -> dict:
    m = store.metrics()
    meta = m.get("meta", {})
    computed = set(m.get("metrics", {}).keys())
    items = []
    for f in FACTORS:
        items.append(
            {
                "code": f["code"],
                "name_cn": f["name_cn"],
                "l1": f["l1"],
                "l2": f["l2"],
                "direction": f["direction"],
                "description": f["description"],
                "formula": f["formula"],
                "wind_source": f["wind_source"],
                "env_tag": f["env_tag"],
                "time_tag": f["time_tag"],
                "available": bool(f["available"])
                and (f["code"] in computed or not computed),
                "unavailable_reason": f["unavailable_reason"],
            }
        )
    return {"factors": items, "l1_order": L1_ORDER, "meta": meta}


def leaderboard(sort: str = "composite") -> dict:
    m = store.metrics()
    rows = list(m.get("leaderboard", []))
    for r in rows:
        meta = BY_CODE.get(r["code"], {})
        r["name_cn"] = meta.get("name_cn", r["code"])
        r["l1"] = meta.get("l1", "")
        r["l2"] = meta.get("l2", "")
    if sort in {"annual_return", "sharpe", "ic_mean", "ic_ir", "max_drawdown"}:
        rows.sort(key=lambda x: (x.get(sort) is None, -(x.get(sort) or 0)))
    return {"leaderboard": rows, "meta": m.get("meta", {})}


def _series_from_long(df: pd.DataFrame | None, code: str, value_col: str) -> list[dict]:
    if df is None or df.empty:
        return []
    sub = df[df["factor_code"] == code][["trade_date", value_col]].dropna()
    return [
        {"date": str(pd.Timestamp(d).date()), "value": round(float(v), 6)}
        for d, v in zip(sub["trade_date"], sub[value_col], strict=False)
    ]


def factor_detail(code: str) -> dict | None:
    meta = BY_CODE.get(code)
    if meta is None:
        return None
    m = store.metrics()
    kpi = m.get("metrics", {}).get(code, {})
    nav_rows = _series_from_long(store.nav_table(), code, "nav")
    ic_rows = _series_from_long(store.ic_table(), code, "ic")
    bench = store.benchmark_table()
    bench_rows = []
    if bench is not None and not bench.empty:
        bench_rows = [
            {"date": str(pd.Timestamp(d).date()), "value": round(float(v), 6)}
            for d, v in zip(bench["trade_date"], bench["nav"], strict=False)
        ]
    hold = store.holdings()
    names = _instrument_names()
    picks = hold.get("holdings", {}).get(code, [])
    holdings_rows = [
        {
            "symbol": s,
            "name": names.get(s, {}).get("name", s),
            "industry": names.get(s, {}).get("industry", ""),
        }
        for s in picks
    ]
    return {
        "code": code,
        "name_cn": meta["name_cn"],
        "l1": meta["l1"],
        "l2": meta["l2"],
        "direction": meta["direction"],
        "description": meta["description"],
        "formula": meta["formula"],
        "wind_source": meta["wind_source"],
        "env_tag": meta["env_tag"],
        "time_tag": meta["time_tag"],
        "kpi": kpi,
        "nav": nav_rows,
        "ic": ic_rows,
        "benchmark": bench_rows,
        "holdings": holdings_rows,
        "holdings_date": hold.get("date"),
        "available": bool(meta["available"]),
    }


def compare(codes: list[str]) -> dict:
    out = []
    for code in codes[:12]:  # 对比上限 12 个，防止响应过大
        d = factor_detail(code)
        if d is None:
            continue
        out.append(
            {
                "code": d["code"],
                "name_cn": d["name_cn"],
                "l1": d["l1"],
                "l2": d["l2"],
                "kpi": d["kpi"],
                "nav": d["nav"],
                "ic": d["ic"],
            }
        )
    corr = store.corr_table()
    corr_sub = None
    if corr is not None and not corr.empty and codes:
        s = set(codes)
        cs = corr[corr["factor_a"].isin(s) & corr["factor_b"].isin(s)]
        # 转原生 float（parquet 读出的是 numpy 类型）；非有限值 → None（JSON 拒绝 NaN）
        corr_sub = [
            {
                "factor_a": str(r["factor_a"]),
                "factor_b": str(r["factor_b"]),
                "corr": float(r["corr"]) if np.isfinite(r["corr"]) else None,
            }
            for r in cs.to_dict("records")
        ]
    bench = store.benchmark_table()
    bench_rows = []
    if bench is not None and not bench.empty:
        bench_rows = [
            {"date": str(pd.Timestamp(d).date()), "value": round(float(v), 6)}
            for d, v in zip(bench["trade_date"], bench["nav"], strict=False)
        ]
    return {"factors": out, "corr": corr_sub, "benchmark": bench_rows}


def screening() -> dict:
    """因子筛选清单（质量门槛 + 同源去重，含剔除原因）。"""
    return store.screening()


def correlation(codes: list[str] | None = None) -> dict:
    corr = store.corr_table()
    if corr is None or corr.empty:
        return {"pairs": []}
    if codes:
        s = set(codes)
        corr = corr[corr["factor_a"].isin(s) | corr["factor_b"].isin(s)]
    return {
        "pairs": [
            {
                "factor_a": str(r["factor_a"]),
                "factor_b": str(r["factor_b"]),
                "corr": float(r["corr"]) if np.isfinite(r["corr"]) else None,
            }
            for r in corr.to_dict("records")
        ]
    }


def compose(
    weights: dict[str, float],
    top_n: int = 30,
    threshold: float | None = None,
    cost_rate: float | None = None,
) -> dict:
    """自定义权重合成 +（可选）阈值过滤 → 实时回测。

    weights: {factor_code: 权重}（可正可负，内部按 Σ|w| 归一）
    threshold: 合成打分下限（z 分位刻度；None=不过滤；0=只保留高于截面均值）
    """
    weights = {
        c: float(w) for c, w in (weights or {}).items() if c in BY_CODE and w != 0
    }
    if not weights:
        return {"error": "weights 为空"}
    codes = list(weights)
    scores = store.scores_for(codes)
    if scores is None or scores.empty:
        return {"error": "快照缺失（请先运行 build_factor_research.py）"}
    wsum = sum(abs(w) for w in weights.values())
    scores["_contrib"] = scores["score"].astype("float64") * scores["factor_code"].map(
        dict(weights)
    ).astype("float64")
    comp = scores.groupby(["trade_date", "symbol"], as_index=False)["_contrib"].sum()
    comp["score"] = comp["_contrib"] / wsum
    wide = comp.pivot(index="trade_date", columns="symbol", values="score")
    wide.index = pd.to_datetime(wide.index)
    wide = wide.sort_index()
    if threshold is not None:
        wide = wide.where(wide >= float(threshold))
    fwd = store.fwd_returns()
    fwd_wide = fwd.pivot(index="trade_date", columns="symbol", values="fwd_ret")
    fwd_wide.index = pd.to_datetime(fwd_wide.index)
    bt = analysis.backtest_topn(
        wide,
        fwd_wide.sort_index(),
        top_n=int(top_n),
        cost_rate=cost_rate or analysis.COST_RATE,
    )
    k = analysis.kpi(bt["ret"], bt["nav"])
    names = _instrument_names()
    last_date = max(bt["holdings"]) if bt["holdings"] else None
    latest = (
        wide.loc[last_date].dropna().sort_values(ascending=False)
        if last_date is not None
        else pd.Series(dtype=float)
    )
    latest_rows = [
        {
            "symbol": s,
            "name": names.get(s, {}).get("name", s),
            "industry": names.get(s, {}).get("industry", ""),
            "score": round(float(v), 3),
        }
        for s, v in latest.head(int(top_n)).items()
    ]
    nav_rows = [
        {"date": str(pd.Timestamp(d).date()), "value": round(float(v), 6)}
        for d, v in bt["nav"].items()
    ]
    turnover_rows = [
        {"date": str(pd.Timestamp(d).date()), "value": round(float(v), 4)}
        for d, v in bt["turnover"].items()
    ]
    bench = store.benchmark_table()
    bench_rows = []
    if bench is not None and not bench.empty:
        bench_rows = [
            {"date": str(pd.Timestamp(d).date()), "value": round(float(v), 6)}
            for d, v in zip(bench["trade_date"], bench["nav"], strict=False)
        ]
    return {
        "kpi": k,
        "nav": nav_rows,
        "turnover": turnover_rows,
        "benchmark": bench_rows,
        "holdings": latest_rows,
        "holdings_date": str(last_date.date()) if last_date is not None else None,
        "weights": weights,
        "top_n": int(top_n),
        "threshold": threshold,
    }
