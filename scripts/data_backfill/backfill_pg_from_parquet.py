"""把 feature_snapshots parquet 历史数据回填到 PG stock_daily_new_* 分区表。

设计：
- 源：/app/db/feature_snapshots/model_features_{YYYY}.parquet（已挂载到容器）
- 目标：stock_daily_latest 父表的月度分区（按 trade_date 自动路由）
- 策略：核心列直接映射 + 4 列从 mom_ma_gap_* / mom_ret_1d 派生 + 其它列 NULL
- 写法：INSERT ... ON CONFLICT (trade_date, symbol) DO NOTHING — 可中断续跑

用法：
    docker exec quantmind python /app/scripts/data_backfill/backfill_pg_from_parquet.py \\
        --start-year 2016 --end-year 2025 [--limit-symbols N] [--dry-run]

依据：docs/risk_scorecard_design_v2.md §1.1 / docs/data_backfill_plan.md
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

# 让 docker exec 直接调
sys.path.insert(0, "/app")

import pyarrow.parquet as pq
from sqlalchemy import text

from backend.shared.database_manager_v2 import get_session


# parquet → PG 列直接映射（36 项）
DIRECT_MAP = {
    "symbol": "symbol",
    "trade_date": "trade_date",
    "open": "open", "high": "high", "low": "low", "close": "close",
    "volume": "volume",
    "factor": "adj_factor",
    "mom_ret_1d": "return_1d", "mom_ret_3d": "return_3d",
    "mom_ret_5d": "return_5d", "mom_ret_10d": "return_10d",
    "mom_ret_20d": "return_20d", "mom_ret_60d": "return_60d",
    "mom_ma_gap_5": "ma_gap_5", "mom_ma_gap_10": "ma_gap_10",
    "mom_ma_gap_20": "ma_gap_20",
    "mom_macd_hist": "macd_hist",
    "mom_rsi_14": "rsi_14", "mom_rsi_6": "rsi_6",
    "kdj_k": "kdj_k",
    "vol_atr_14": "vol_atr_14",
    "vol_std_5": "vol_std_5", "vol_std_20": "vol_std_20", "vol_std_60": "vol_std_60",
    "style_bp": "bp", "style_ep_ttm": "ep_ttm",
    "style_ln_mv_total": "ln_mv_total",
    "style_beta_20": "beta_20",
    "liq_volume_ratio_5": "volume_ratio_5",
    "liq_volume_ratio_20": "volume_ratio_20",
    "liq_volume_ma_5": "volume_ma_5",
    "liq_amount_ma_5": "amount_ma_5",
    "flow_net_amount": "flow_net_amount",
    "flow_pressure_index": "main_flow",
    "liq_turnover_os": "turnover_rate",
}

# parquet 派生列 → PG 列
# ma_n = close / (1 + mom_ma_gap_n) ，因为 mom_ma_gap_n = (close - ma_n)/ma_n
# pct_change = return_1d * 100（PG 列单位是百分数）
# amount = close * volume * adj_factor 的逆运算很难，parquet 里其实有 liq_amount → amount
DERIVED_MAP = {
    "amount": ("liq_amount", lambda v, row: v),  # parquet 也叫 liq_amount，加进来
    "ma5": ("mom_ma_gap_5", lambda gap, row:
            (row["close"] / (1 + gap)) if (gap is not None and row.get("close") is not None and (1 + gap) != 0) else None),
    "ma10": ("mom_ma_gap_10", lambda gap, row:
             (row["close"] / (1 + gap)) if (gap is not None and row.get("close") is not None and (1 + gap) != 0) else None),
    "ma20": ("mom_ma_gap_20", lambda gap, row:
             (row["close"] / (1 + gap)) if (gap is not None and row.get("close") is not None and (1 + gap) != 0) else None),
    "ma60": ("mom_ma_gap_60", lambda gap, row:
             (row["close"] / (1 + gap)) if (gap is not None and row.get("close") is not None and (1 + gap) != 0) else None),
    "pct_change": ("mom_ret_1d", lambda r, row: r * 100 if r is not None else None),
}

# 全部要写入的 PG 列
PG_COLUMNS = list(DIRECT_MAP.values()) + list(DERIVED_MAP.keys())


def _norm_symbol(s: str) -> str:
    """parquet 里的 symbol 应该是 600519.SH 这种后缀格式，直接用。"""
    return str(s).strip().upper()


def _build_insert_sql() -> str:
    cols_str = ", ".join(PG_COLUMNS)
    placeholders = ", ".join(f":{c}" for c in PG_COLUMNS)
    return (
        f"INSERT INTO stock_daily_latest ({cols_str}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT (trade_date, symbol) DO NOTHING"
    )


async def _backfill_year(year: int, limit_symbols: int | None, dry_run: bool) -> dict:
    """回填一年的数据。按月分块以降低单次事务体积。"""
    parquet_path = f"/app/db/feature_snapshots/model_features_{year}.parquet"
    if not Path(parquet_path).exists():
        print(f"[{year}] ❌ {parquet_path} not found, skip")
        return {"year": year, "status": "no_file"}

    pf = pq.ParquetFile(parquet_path)
    print(f"[{year}] parquet rows={pf.metadata.num_rows:,} cols={pf.metadata.num_columns}")

    available_cols = set(pf.schema_arrow.names)
    direct_avail = {p: g for p, g in DIRECT_MAP.items() if p in available_cols}
    derived_avail = {pg: (src, fn) for pg, (src, fn) in DERIVED_MAP.items() if src in available_cols}

    needed_parquet_cols = list(direct_avail.keys()) + [src for src, _ in derived_avail.values()]
    if "close" not in needed_parquet_cols and "close" in available_cols:
        needed_parquet_cols.append("close")  # 派生 ma 需要 close

    needed_parquet_cols = list(set(needed_parquet_cols))
    print(f"[{year}] reading {len(needed_parquet_cols)} parquet columns")

    df = pf.read(columns=needed_parquet_cols).to_pandas()
    print(f"[{year}] loaded {len(df):,} rows into memory")

    if limit_symbols:
        symbols = df["symbol"].unique()[:limit_symbols]
        df = df[df["symbol"].isin(symbols)]
        print(f"[{year}] limited to {limit_symbols} symbols → {len(df):,} rows")

    # 转换 trade_date 到 date 对象（asyncpg 严格类型）
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].apply(
            lambda x: x.date() if hasattr(x, "date") else x
        )

    sql = _build_insert_sql()
    inserted = 0
    skipped = 0
    started = time.time()

    # 按月分批
    df["_month"] = df["trade_date"].apply(lambda d: d.month if d else None)
    months = sorted(m for m in df["_month"].unique() if m is not None)

    for month in months:
        mdf = df[df["_month"] == month]
        if mdf.empty:
            continue
        rows_payload = []
        for _, r in mdf.iterrows():
            row_dict = dict.fromkeys(PG_COLUMNS)
            # 直接映射
            for p_col, pg_col in direct_avail.items():
                v = r.get(p_col)
                # nan → None；保留 0 / 负值
                if v is None or (isinstance(v, float) and v != v):
                    row_dict[pg_col] = None
                elif pg_col == "symbol":
                    row_dict[pg_col] = _norm_symbol(v)
                else:
                    row_dict[pg_col] = v
            # 派生
            for pg_col, (src, fn) in derived_avail.items():
                v = r.get(src)
                if v is None or (isinstance(v, float) and v != v):
                    row_dict[pg_col] = None
                else:
                    try:
                        row_dict[pg_col] = fn(v, r)
                    except Exception:
                        row_dict[pg_col] = None
            rows_payload.append(row_dict)

        if dry_run:
            print(f"  [DRY] {year}-{month:02d}: would insert {len(rows_payload)} rows")
            inserted += len(rows_payload)
            continue

        # 分批写入：每 5000 行一个事务，控制内存 + 事务规模
        # 注：asyncpg/sqlalchemy 在 executemany 上 result.rowcount 可能返回 -1，
        # 因此 inserted 计数器以"事务成功提交的批次大小"为准（含 ON CONFLICT skip）。
        batch_size = 5000
        for i in range(0, len(rows_payload), batch_size):
            chunk = rows_payload[i:i+batch_size]
            try:
                async with get_session() as session:
                    await session.execute(text(sql), chunk)
                    await session.commit()
                inserted += len(chunk)
            except Exception as e:
                print(f"  [{year}-{month:02d}] batch {i}-{i+len(chunk)} failed: {e}")
                skipped += len(chunk)

        elapsed = time.time() - started
        rate = inserted / elapsed if elapsed > 0 else 0
        print(f"  [{year}-{month:02d}] inserted={inserted:,}  skipped={skipped:,}  rate={rate:.0f} rows/s")

    return {
        "year": year,
        "status": "ok",
        "inserted": inserted,
        "skipped": skipped,
        "elapsed_sec": round(time.time() - started, 1),
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-year", type=int, default=2016)
    ap.add_argument("--end-year", type=int, default=2025)
    ap.add_argument("--limit-symbols", type=int, default=None,
                    help="只回填前 N 个 symbol，用于 dry-run 测试")
    ap.add_argument("--dry-run", action="store_true",
                    help="只计算，不实际写 PG")
    args = ap.parse_args()

    print("\n=== Backfill PG stock_daily_latest from parquet ===")
    print(f"years: {args.start_year} ~ {args.end_year}")
    print(f"dry_run: {args.dry_run}")
    print(f"limit_symbols: {args.limit_symbols or 'all'}")
    print(f"PG columns to fill: {len(PG_COLUMNS)}")

    results = []
    for y in range(args.start_year, args.end_year + 1):
        try:
            r = await _backfill_year(y, args.limit_symbols, args.dry_run)
            results.append(r)
        except Exception as e:
            print(f"[{y}] FATAL: {e}")
            results.append({"year": y, "status": "error", "error": str(e)})

    print("\n=== SUMMARY ===")
    for r in results:
        print(r)


if __name__ == "__main__":
    asyncio.run(main())
