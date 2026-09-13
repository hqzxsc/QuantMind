"""8月分数分桶胜率分析：找最优买卖分数阈值。

对 8 月每个信号的分数分桶，统计各桶：
- B1: 次日开盘买、次日开盘卖（持有1天）的收益/胜率
- B5: 次日开盘买、持有5天开盘卖（中期）的收益/胜率

用于找出：哪个分数段胜率最高（值得买）、低于哪个分数胜率差（该卖）。
"""
import sys
from datetime import date
from pathlib import Path
from collections import Counter, defaultdict

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MODEL_ID = "mdl_cn_train_20260819100559_9163cb84_ac5c5b2e"
from backend.shared.database_manager_v2 import get_session
from backend.shared.stock_utils import StockCodeUtil
from sqlalchemy import text


def load_aug_signals():
    import asyncio
    async def _load():
        async with get_session(read_only=True) as s:
            res = await s.execute(text("""
                SELECT trade_date, symbol, fusion_score
                FROM engine_signal_scores e
                WHERE e.run_id IN (SELECT run_id FROM qm_model_inference_runs
                                   WHERE model_id=:mid AND status='completed')
                  AND trade_date BETWEEN '2026-08-03' AND '2026-08-18'
            """), {"mid": MODEL_ID})
            df = pd.DataFrame(res.fetchall(), columns=["trade_date", "symbol", "score"])
            df["symbol"] = df["symbol"].apply(lambda x: StockCodeUtil.to_suffix(str(x).strip().upper()))
            return df
    return asyncio.run(_load())


def load_klines(symbols):
    import pyarrow.parquet as pq
    import os
    root = Path(os.getenv("QM_QUANTDB_DATA_DIR", "/data/quantdb"))
    daily_dir = root / "1_kline_data" / "daily_unadjusted"
    parts = [p / "data.parquet" for p in sorted(daily_dir.glob("dt=2026*"))]
    filters = [("symbol", "in", sorted(symbols))]
    all_dfs = []
    for f in parts:
        try:
            t = pq.read_table(f, columns=["symbol", "time", "open", "close"], filters=filters)
            if t.num_rows:
                all_dfs.append(t.to_pandas())
        except Exception:
            continue
    full = pd.concat(all_dfs, ignore_index=True)
    full["trade_date"] = full["time"].astype(str).str[:10]
    return full[["symbol", "trade_date", "open", "close"]]


def main():
    print("加载信号...")
    sig = load_aug_signals()
    all_syms = set(sig["symbol"])
    print(f"8月信号: {len(sig)} 条, 股票 {len(all_syms)}")
    klines = load_klines(all_syms)
    print(f"K线: {len(klines)} 条")

    # 建立价格索引: (symbol, date) -> open, close
    price = {}
    for _, r in klines.iterrows():
        price[(r["symbol"], r["trade_date"])] = {"open": float(r["open"]), "close": float(r["close"])}

    # 排序交易日
    dates = sorted(set(sig["trade_date"]))
    next2 = {}
    dl = sorted(set(klines["trade_date"]))
    # 信号日期（date对象）→ 字符串
    for i in range(len(dates) - 1):
        next2[str(dates[i])] = str(dates[i + 1])
    date_list = dl  # K线日期已是字符串
    date_pos = {d: i for i, d in enumerate(date_list)}

    results = {"B1": [], "B5": []}
    for _, row in sig.iterrows():
        sym, d, sc = row["symbol"], str(row["trade_date"]), float(row["score"])
        if d not in next2:
            continue
        t1 = next2[d]
        # 持有1天：t+1开盘买 t+2开盘卖
        # 持有5天：t+1开盘买 t+5开盘卖
        p_t1 = price.get((sym, t1))
        if not p_t1:
            continue
        buy_px = p_t1["open"]
        if buy_px <= 0:
            continue
        # 找 t+2 和 t+5
        p2 = p5 = None
        pos = date_pos.get(t1)
        if pos is not None:
            if pos + 1 < len(date_list):
                p2 = price.get((sym, date_list[pos + 1]))
            if pos + 4 < len(date_list):
                p5 = price.get((sym, date_list[pos + 4]))
        if p2 and p2["open"] > 0:
            results["B1"].append({"score": sc, "ret": p2["open"] / buy_px - 1})
        if p5 and p5["open"] > 0:
            results["B5"].append({"score": sc, "ret": p5["open"] / buy_px - 1})

    # 分桶统计
    def bucket(stats, name):
        if not stats:
            print(f"{name}: 无数据")
            return
        # 分数分桶：负分 / 0-0.005 / 0.005-0.01 / 0.01-0.015 / 0.015-0.02 / 0.02+
        bins = [(-1, 0, "<0(负)"), (0, 0.005, "0~0.005"), (0.005, 0.01, "0.005~0.01"),
                (0.01, 0.015, "0.01~0.015"), (0.015, 0.02, "0.015~0.02"), (0.02, 1, "≥0.02")]
        print(f"\n{name} 分数分桶胜率（样本 {len(stats)}）:")
        print(f"{'分数段':14} {'样本':>6} {'胜率':>8} {'均收益':>9} {'中位':>9}")
        for lo, hi, label in bins:
            grp = [x for x in stats if lo <= x["score"] < hi]
            if not grp:
                print(f"{label:14} {0:>6} {'--':>8} {'--':>9} {'--':>9}")
                continue
            wins = sum(1 for x in grp if x["ret"] > 0)
            avg = sum(x["ret"] for x in grp) / len(grp)
            med = sorted(x["ret"] for x in grp)[len(grp) // 2]
            print(f"{label:14} {len(grp):>6} {wins/len(grp)*100:>7.1f}% {avg*100:>+8.2f}% {med*100:>+8.2f}%")

        # 按分数降序 top10% vs 全部
        sorted_stats = sorted(stats, key=lambda x: -x["score"])
        top10 = sorted_stats[:max(1, len(sorted_stats)//10)]
        win_top = sum(1 for x in top10 if x["ret"] > 0) / len(top10)
        avg_top = sum(x["ret"] for x in top10) / len(top10)
        print(f"  → 分数最高10%: 样本{len(top10)} 胜率{win_top*100:.1f}% 均收益{avg_top*100:+.2f}%")
        # 负分 vs 正分
        neg = [x for x in stats if x["score"] < 0]
        pos = [x for x in stats if x["score"] > 0]
        if neg:
            print(f"  → 负分: 样本{len(neg)} 胜率{sum(1 for x in neg if x['ret']>0)/len(neg)*100:.1f}%")
        if pos:
            print(f"  → 正分: 样本{len(pos)} 胜率{sum(1 for x in pos if x['ret']>0)/len(pos)*100:.1f}%")

    bucket(results["B1"], "持有1天(B1)")
    bucket(results["B5"], "持有5天(B5)")


if __name__ == "__main__":
    main()
