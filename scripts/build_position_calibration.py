"""离线回测：为仓位信号分(position_score)校准两个数据基础。

输出两份产物，落 JSON 到 /data/quantdb/position_signal_calibration/：
  1. ic_weights.json   —— p 融合权重（全市场/行业/板块/市值档百分位 → T+N收益的 RankIC）
  2. payoff_table.json —— 赔率查找表 b(score_pct_bucket, industry) = avg_win/avg_loss

用途：
  - position_score = 0.5 * (p - (1-p)/b)，其中
      p = w1·pct_market + w2·pct_industry + w3·pct_board + w4·pct_cap （权重来自 ic_weights）
      b = payoff_table[score_pct_bucket][industry]                    （来自 payoff_table）
  - T+N 的 N 取模型 target_horizon_days（默认 5）

数据源：
  - engine_signal_scores（历史推理分数，按模型过滤）
  - QuantDB 后复权日线 daily_backward（算 T+N 实际收益）
  - instrument_detail（行业 rs_hyname、流通市值 Ltsz、5大板块）

用法（容器内）：
  docker cp scripts/build_position_calibration.py quantmind:/tmp/
  docker exec quantmind python3 /tmp/build_position_calibration.py [--model MODEL_ID] [--horizon 5]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import date, timedelta

import duckdb
import pandas as pd

from backend.shared.database_manager_v2 import get_session
from sqlalchemy import text

OUT_DIR = "/data/quantdb/position_signal_calibration"
DAILY_DIR = "/data/quantdb/1_kline_data/daily_backward"
INSTR_GLOB = "/data/quantdb/2_base_sector/instrument_detail/**/*.parquet"

# 分数百分位档（赔率查找表的 key）：0=最低，9=最高，共10档
N_BUCKETS = 10


def classify_board(symbol: str) -> str:
    code = symbol.split(".")[0]
    if code.startswith("688"):
        return "科创板"
    if code.startswith("30"):
        return "创业板"
    if code.startswith(("002", "003")):
        return "中小板"
    if code.startswith(("000", "001")):
        return "深主板"
    if code.startswith("60"):
        return "沪主板"
    if code.startswith(("4", "8", "9")):
        return "北交所"
    return "其他"


def cap_tier(ltsz_yi: float | None) -> str:
    if ltsz_yi is None:
        return ""
    if ltsz_yi < 30:
        return "微盘"
    if ltsz_yi < 100:
        return "小盘"
    if ltsz_yi < 300:
        return "中盘"
    if ltsz_yi < 1000:
        return "大盘"
    return "超大盘"


def load_instrument_meta() -> pd.DataFrame:
    """行业 + 流通市值(亿) + 板块，symbol → meta。"""
    con = duckdb.connect()
    df = con.execute(f"""
        SELECT Symbol AS symbol, rs_hyname AS industry, Ltsz AS ltsz
        FROM read_parquet('{INSTR_GLOB}', union_by_name=true)
        WHERE Symbol LIKE '%.SH' OR Symbol LIKE '%.SZ' OR Symbol LIKE '%.BJ'
    """).fetchdf()
    df["ltsz_yi"] = pd.to_numeric(df["ltsz"], errors="coerce")  # Ltsz 已是亿元单位
    df["board"] = df["symbol"].map(classify_board)
    df["cap_tier"] = df["ltsz_yi"].map(cap_tier)
    # 过滤异常行业（退市/未分类）
    df = df[df["industry"].notna() & (df["industry"].str.strip() != "")]
    return df[["symbol", "industry", "board", "cap_tier", "ltsz_yi"]]


async def load_signals(model_id: str | None, days: int | None = None) -> pd.DataFrame:
    """历史推理分数，按模型过滤 + 可选最近 N 天，取每日最新批次。"""
    mwhere = (
        "AND run_id IN (SELECT run_id FROM qm_model_inference_runs WHERE model_id=:m)"
        if model_id else ""
    )
    dwhere = "AND trade_date >= :cutoff" if days else ""
    params: dict = {}
    if model_id:
        params["m"] = model_id
    if days:
        params["cutoff"] = date.today() - timedelta(days=days)
    sql = f"""
        SELECT trade_date, symbol, fusion_score, signal_side
        FROM engine_signal_scores
        WHERE tenant_id='default' AND fusion_score IS NOT NULL {mwhere} {dwhere}
    """
    async with get_session(read_only=True) as s:
        rows = (await s.execute(text(sql), params)).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["trade_date", "symbol", "fusion_score", "signal_side"])
    # symbol 归一到 suffix 格式（信号表存纯数字，instrument 存 suffix）
    df["symbol"] = df["symbol"].map(lambda x: x if "." in str(x) else _to_suffix(str(x)))
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    # 每日每票取最新一条（同日多 run）
    df = df.sort_values("trade_date").drop_duplicates(
        subset=["trade_date", "symbol"], keep="last"
    )
    return df


def _to_suffix(code: str) -> str:
    if "." in code:
        return code
    if code.startswith("6") or code.startswith("9"):
        return f"{code}.SH"
    if code.startswith("4") or code.startswith("8"):
        return f"{code}.BJ"
    return f"{code}.SZ"


def compute_t_plus_n_returns(signals: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """从后复权日线算每股 T+horizon 收益率，合并到信号表。

    ret = close(t+horizon) / close(t) - 1，t=信号日（持仓从次日开盘起的近似，
    用收盘→收盘足够校准 IC/赔率，不追求交易级精确）。
    """
    needed_dates = set(signals["trade_date"].unique())
    # 信号日 + horizon 天后的交易日：需要把每个信号日和它后 horizon 个交易日的 close 拉出来。
    # 简化：拉 [min, max+horizon自然日] 全区间日线，本地按 symbol 算 shift 收益。
    min_d = min(needed_dates)
    max_d = max(needed_dates) + timedelta(days=horizon * 2 + 10)
    con = duckdb.connect()
    # 按 dt 分区目录读：dt=YYYYMMDD
    df = con.execute(f"""
        SELECT symbol, CAST(time AS DATE) AS d, close
        FROM read_parquet('{DAILY_DIR}/dt=*/*.parquet', union_by_name=true)
        WHERE CAST(time AS DATE) BETWEEN '{min_d}' AND '{max_d}'
          AND close > 0
    """).fetchdf()
    if df.empty:
        return signals.assign(ret=None)
    df["d"] = pd.to_datetime(df["d"]).dt.date
    df = df.sort_values(["symbol", "d"])
    # 按 symbol shift horizon 个交易日（不是自然日）算收益
    df["close_t_plus_n"] = df.groupby("symbol")["close"].shift(-horizon)
    df["ret"] = df["close_t_plus_n"] / df["close"] - 1
    close_map = df.set_index(["symbol", "d"])[["ret"]].to_dict("index")
    rets = signals.apply(
        lambda r: close_map.get((r["symbol"], r["trade_date"]), {}).get("ret"),
        axis=1,
    )
    signals = signals.copy()
    signals["ret"] = rets
    return signals


def percentile_within_group(df: pd.DataFrame, score_col: str, group_col: str) -> pd.Series:
    """组内百分位（0~1），同组内按分数升序排名 / (n-1)。组内<2个返回0.5。"""
    def _rank(g: pd.Series) -> pd.Series:
        n = len(g)
        if n < 2:
            return pd.Series([0.5] * n, index=g.index)
        return g.rank(method="average") / (n - 1)
    # 直接对分数列分组排名，避免 apply 返回多列的陷阱
    return df.groupby(group_col, group_keys=False)[score_col].transform(_rank)


def rank_ic(scores: pd.Series, rets: pd.Series) -> float:
    """Spearman RankIC：两组序列的 Spearman 相关。返回 0~1 绝对值的均值口径。"""
    valid = scores.notna() & rets.notna()
    if valid.sum() < 10:
        return 0.0
    return float(scores[valid].corr(rets[valid], method="spearman") or 0.0)


async def main(model_id: str | None, horizon: int, days: int | None) -> None:
    print("[1/5] 加载 instrument 元数据（行业/市值/板块）...")
    meta = load_instrument_meta()
    print(f"      instrument: {len(meta)} 只")

    print(f"[2/5] 加载历史推理信号 model={'默认全模型' if not model_id else model_id}"
          f"{f' 最近{days}天' if days else '全量'}...")
    signals = await load_signals(model_id, days)
    if signals.empty:
        print("无信号数据，退出")
        return
    print(f"      信号: {len(signals)} 条，{signals['trade_date'].nunique()} 个交易日")

    print(f"[3/5] 合并元数据 + 算 T+{horizon} 收益...")
    df = signals.merge(meta, on="symbol", how="inner")
    print(f"      合并后: {len(df)} 条（行业命中）")
    df = compute_t_plus_n_returns(df, horizon)
    df = df[df["ret"].notna()]
    print(f"      有 T+{horizon} 收益: {len(df)} 条")

    if len(df) < 500:
        print("样本太少（<500），退出")
        return

    # ── IC 权重：各维度百分位 → T+N 收益的 RankIC ──────────────────
    print(f"[4/5] 计算各维度百分位 → T+{horizon} 收益的 RankIC...")
    def _group_rank(s: pd.Series) -> pd.Series:
        n = len(s)
        if n < 2:
            return pd.Series([0.5] * n, index=s.index)
        return s.rank(method="average") / (n - 1)
    df["pct_market"] = df.groupby("trade_date", group_keys=False)["fusion_score"].transform(_group_rank)
    df["pct_industry"] = percentile_within_group(df, "fusion_score", "industry")
    df["pct_board"] = percentile_within_group(df, "fusion_score", "board")
    df["pct_cap"] = percentile_within_group(df, "fusion_score", "cap_tier")

    ics = {
        "market": rank_ic(df["pct_market"], df["ret"]),
        "industry": rank_ic(df["pct_industry"], df["ret"]),
        "board": rank_ic(df["pct_board"], df["ret"]),
        "cap": rank_ic(df["pct_cap"], df["ret"]),
    }
    print(f"      RankIC: {ics}")
    # IC 可能为负（该维度百分位与收益反向）；用 |IC| 归一化作权重，
    # 但保留方向符号供 position_score 判断是否反向使用（初版默认正向，|IC| 归一）
    abs_ics = {k: abs(v) for k, v in ics.items()}
    total_ai = sum(abs_ics.values()) or 1.0
    weights = {k: round(abs_ics[k] / total_ai, 4) for k in abs_ics}
    # 兜底：IC 全 0 时均分
    if sum(weights.values()) == 0:
        weights = {"market": 0.4, "industry": 0.3, "board": 0.15, "cap": 0.15}
    print(f"      权重: {weights}")

    # ── 赔率查找表：b(score_pct_bucket, industry) = avg_win/avg_loss ──
    print("[5/5] 计算赔率查找表 b(bucket, industry)...")
    df["bucket"] = (df["pct_market"] * N_BUCKETS).clip(0, N_BUCKETS - 1).astype(int)
    payoff: dict[str, dict[str, float]] = {}
    for (ind, bucket), g in df.groupby(["industry", "bucket"]):
        if len(g) < 10:
            continue
        wins = g[g["ret"] > 0]["ret"]
        losses = g[g["ret"] < 0]["ret"]
        if len(wins) < 3 or len(losses) < 3:
            continue
        b = float(wins.mean() / abs(losses.mean()))
        payoff.setdefault(str(ind), {})[str(bucket)] = round(max(1.0, b), 3)
    # 全局兜底赔率（按 bucket 跨行业聚合，给冷门行业回退）
    fallback_by_bucket: dict[str, float] = {}
    for bucket, g in df.groupby("bucket"):
        if len(g) < 30:
            continue
        wins = g[g["ret"] > 0]["ret"]
        losses = g[g["ret"] < 0]["ret"]
        if len(wins) < 5 or len(losses) < 5:
            continue
        fallback_by_bucket[str(bucket)] = round(max(1.0, float(wins.mean() / abs(losses.mean()))), 3)
    overall_b = round(max(1.0, float(
        df[df["ret"] > 0]["ret"].mean() / abs(df[df["ret"] < 0]["ret"].mean())
    )), 3) if (df["ret"] > 0).any() and (df["ret"] < 0).any() else 1.5
    print(f"      覆盖行业: {len(payoff)}，bucket 兜底: {len(fallback_by_bucket)} 档，全局 b={overall_b}")

    os.makedirs(OUT_DIR, exist_ok=True)
    ic_out = {
        "model_id": model_id or "all",
        "horizon_days": horizon,
        "sample_count": int(len(df)),
        "rank_ic": ics,
        "weights": weights,
    }
    with open(f"{OUT_DIR}/ic_weights.json", "w", encoding="utf-8") as f:
        json.dump(ic_out, f, ensure_ascii=False, indent=2)

    payoff_out = {
        "model_id": model_id or "all",
        "horizon_days": horizon,
        "n_buckets": N_BUCKETS,
        "fallback_by_bucket": fallback_by_bucket,
        "overall_b": overall_b,
        "table": payoff,
    }
    with open(f"{OUT_DIR}/payoff_table.json", "w", encoding="utf-8") as f:
        json.dump(payoff_out, f, ensure_ascii=False, indent=2)

    print("\n完成。产物:")
    print(f"  {OUT_DIR}/ic_weights.json   权重={weights}")
    print(f"  {OUT_DIR}/payoff_table.json 覆盖行业={len(payoff)} 全局b={overall_b}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="模型ID（缺省=全模型）")
    ap.add_argument("--horizon", type=int, default=5, help="T+N 收益天数（默认5）")
    ap.add_argument("--days", type=int, default=365, help="只取最近 N 天信号（默认365，0=全量）")
    a = ap.parse_args()
    asyncio.run(main(a.model, a.horizon, a.days if a.days else None))
