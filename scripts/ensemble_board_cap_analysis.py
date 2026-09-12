"""高分股票(≥2.2)板块×市值×周期收益分析"""
import sys
import asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collections import defaultdict
import statistics

from scripts.ensemble_backtest import load_signals, load_klines, load_index_ma20
from backend.shared.database_manager_v2 import get_session
from backend.shared.stock_utils import StockCodeUtil
from sqlalchemy import text


def load_caps() -> dict[str, float]:
    """用同步 psycopg2 读市值，避免 asyncio loop 冲突"""
    import psycopg2
    import os
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"), port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "quantmind"),
        user=os.getenv("DB_USER", "quantmind"), password=os.getenv("DB_PASSWORD", "quantmind2026"),
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, total_mv FROM stock_daily_latest
        WHERE trade_date='2026-08-05' AND total_mv>0
    """)
    caps = {str(r[0]): float(r[1]) for r in cur.fetchall()}
    cur.close(); conn.close()
    return caps


def board_of(code: str) -> str:
    if code.startswith('688'): return '科创板'
    if code.startswith('30'): return '创业板'
    if code.startswith('00') or code.startswith('002') or code.startswith('003'): return '深主板'
    if code.startswith('60'): return '沪主板'
    if code.startswith('4') or code.startswith('8') or code.startswith('92'): return '北交所'
    return '其他'


def cap_of(mv_yi: float) -> str:
    if mv_yi < 30: return '微盘'
    if mv_yi < 100: return '小盘'
    if mv_yi < 300: return '中盘'
    if mv_yi < 1000: return '大盘'
    return '超大盘'


def main():
    print("加载信号/K线/MA20/市值...")
    signals = load_signals()
    caps = load_caps()
    all_symbols = set()
    for items in signals.values():
        for sym, sc, rk in items[:20]:
            all_symbols.add(sym)
    klines = load_klines(all_symbols)
    index_ma = load_index_ma20()
    print(f"信号{len(signals)}天 K线{len(klines)}只 市值{len(caps)}只")

    price = {}
    for suffix, df in klines.items():
        for _, row in df.iterrows():
            price[(suffix, str(row['trade_date'])[:10])] = float(row['close'])
    dates = sorted(signals.keys())

    # 板块×市值×周期 收益
    ret_bm = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for i, d in enumerate(dates):
        if not index_ma.get(d):
            continue
        for sym, sc, rk in signals[d]:
            if sc < 2.2:
                continue
            suffix = StockCodeUtil.to_suffix(sym)
            prefix = StockCodeUtil.to_prefix(sym)
            mv = caps.get(prefix, caps.get(suffix, 0)) / 1e8
            cap = cap_of(mv) if mv > 0 else '未知'
            b = board_of(sym)
            for h in (1, 3, 5):
                fi = i + h
                if fi >= len(dates):
                    continue
                c0 = price.get((suffix, d))
                c1 = price.get((suffix, dates[fi]))
                if c0 and c1 and c0 > 0:
                    ret_bm[b][cap][h].append((c1 / c0 - 1) * 100)

    print("\n=== 板块 × 市值 × T+3 均收% (样本≥5) ===")
    for b in ['深主板', '沪主板', '创业板', '科创板']:
        row = []
        for cap in ['微盘', '小盘', '中盘', '大盘', '超大盘']:
            rets = ret_bm[b].get(cap, {}).get(3, [])
            if len(rets) >= 5:
                row.append(f"{cap}:{statistics.mean(rets):.1f}%({len(rets)})")
        if row:
            print(f"  {b}: {' '.join(row)}")

    print("\n=== 深主板 T+3 中位数(排除极值稳健性) ===")
    for cap in ['微盘', '小盘', '中盘', '大盘', '超大盘']:
        rets = ret_bm['深主板'].get(cap, {}).get(3, [])
        if len(rets) >= 5:
            print(f"  {cap}: n={len(rets)} 均{statistics.mean(rets):.2f}% 中位{statistics.median(rets):.2f}% "
                  f"胜率{sum(1 for x in rets if x>0)/len(rets)*100:.0f}%")


if __name__ == "__main__":
    main()
