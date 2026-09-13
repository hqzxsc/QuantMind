#!/usr/bin/env python3
"""经典因子库构建：Alpha360 / TDXGS / JQ110 → <quantdb>/6_ml_datasets/<lib>/

用法（仓库根执行）:
    python3 backend/scripts/build_factor_library.py --lib all            # 全量 2016 至今
    python3 backend/scripts/build_factor_library.py --lib tdxgs --max-symbols 300   # 冒烟

产物（每库）:
    dt=YYYYMMDD/data.parquet（symbol, time, 因子列 float32；原子写，已有分区跳过）
    meta.json（窗口/股票数/因子数/口径说明）

口径要点：
    - 价格用 daily_forward 前复权；volume=股；amount=万元（名义/未复权基准）
    - JQ110 金额类用**名义成交额**（真实成交的钱）：money_flow = Σ amount×sign(ret)，
      成交额水平/TVMA/换手/流动性同基；β 用中证500 真实回归
    - 无未来函数（rolling/shift/ewm/cumsum 全因果）；严格窗口 min_periods=窗口
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

from backend.services.engine.factor_library import alpha360, jq110, tdxgs  # noqa: E402
from backend.services.engine.factor_research import data as frdata  # noqa: E402
from backend.shared.quantdb_paths import resolve_quantdb_dir  # noqa: E402

START_DEFAULT = "20160101"


def _out_root(lib: str) -> Path:
    d = resolve_quantdb_dir() / "6_ml_datasets" / lib
    d.mkdir(parents=True, exist_ok=True)
    return d


_OHLCV = ("open", "high", "low", "close", "volume", "amount")


def _write_day(
    out_root: Path,
    ts: pd.Timestamp,
    day: pd.DataFrame,
    daily: dict | None = None,
    i: int | None = None,
) -> bool:
    """原子写单日分区（symbol 为 index；已有分区跳过）。

    附带 OHLCV 列（同 daily_forward 语义：前复权价/股/万元）——因子报告
    close_fwd 标签与 l1_factors 契约都依赖本表自带 close。
    """
    dt_str = ts.strftime("%Y%m%d")
    dt_dir = out_root / f"dt={dt_str}"
    dt_dir.mkdir(parents=True, exist_ok=True)
    target = dt_dir / "data.parquet"
    if target.exists():
        return False
    day = day.reset_index()
    day = day.rename(columns={day.columns[0]: "symbol"})
    if daily is not None and i is not None:
        for fld in _OHLCV:
            day[fld] = (
                daily[fld].iloc[i].reindex(day["symbol"]).to_numpy(dtype=np.float32)
            )
    day.insert(1, "time", ts)
    day = day.replace([np.inf, -np.inf], np.nan)
    tmp = dt_dir / ".tmp-data.parquet"
    try:
        day.to_parquet(tmp, index=False)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def _write_frames(
    factors: dict[str, pd.DataFrame], lib: str, start_dt: str, daily: dict
) -> int:
    """dict-of-wide-frames → 逐日分区（堆叠后按日切片，同 alpha_library 模式）。"""
    out_root = _out_root(lib)
    names = list(factors)
    frames = list(factors.values())
    dates, syms = frames[0].index, list(frames[0].columns)
    arrs = [f.to_numpy() for f in frames]  # 已在上游转 float32
    written = 0
    chunk = 40
    for b in range(0, len(dates), chunk):
        b_end = min(b + chunk, len(dates))
        block = np.stack([a[b:b_end] for a in arrs], axis=2)  # (nb, n_sym, n_fac)
        for k in range(b, b_end):
            dt_str = pd.Timestamp(dates[k]).strftime("%Y%m%d")
            if dt_str < start_dt:
                continue
            day = pd.DataFrame(block[k - b], index=syms, columns=names)
            written += _write_day(out_root, pd.Timestamp(dates[k]), day, daily, k)
        del block
    return written


def _load_inputs(start: str):
    print("[1/3] 载入 daily_forward / valuation / index ...")
    daily = frdata.load_daily_panel(start, "20991231")
    val = frdata.load_valuation_panel(start, "20991231", fields=("float_mv",))
    idx = daily["close"].index
    float_mv = val["float_mv"].reindex(index=idx, columns=daily["close"].columns)
    market_close = frdata.load_index_close(start, "20991231").reindex(idx).ffill()
    market_ret = market_close.pct_change()
    # 名义成交额（元）：amount 万元 × 1e4；换手 = 名义成交额 / 流通市值
    amount_yuan = daily["amount"] * 1e4
    turnover = amount_yuan / (float_mv + 1e-12)
    # vwap（名义口径）：成交额 / 成交量
    vwap = amount_yuan / (daily["volume"] + 1e-12)
    print(f"      {len(idx)} 交易日 × {daily['close'].shape[1]} 只")
    return daily, amount_yuan, turnover, vwap, market_ret


def _meta(lib: str, n_factors: int, dates: pd.Index, n_syms: int, conv: str) -> dict:
    return {
        "dataset": lib,
        "built_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "window": [str(dates[0].date()), str(dates[-1].date())],
        "n_days": len(dates),
        "n_symbols": n_syms,
        "n_factors": n_factors,
        "conventions": conv,
        "source": "QuantDB daily_forward（前复权价）+ valuation（流通市值）+ index_daily(000905.SH)；分区附 OHLCV（同 daily_forward 语义）",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--lib", choices=["alpha360", "tdxgs", "jq110", "all"], default="all"
    )
    ap.add_argument("--start", default=START_DEFAULT)
    ap.add_argument("--max-symbols", type=int, default=0, help="仅前 N 只（冒烟/调试）")
    args = ap.parse_args()

    t0 = time.time()
    daily, amount, turnover, vwap, market_ret = _load_inputs(args.start)
    if args.max_symbols:
        syms = list(daily["close"].columns)[: args.max_symbols]
        for d_ in (daily,):
            for k in d_:
                d_[k] = d_[k][syms]
        amount, turnover, vwap = amount[syms], turnover[syms], vwap[syms]

    libs = ["alpha360", "tdxgs", "jq110"] if args.lib == "all" else [args.lib]
    for lib in libs:
        print(f"[2/3] 计算 {lib} ...")
        tc = time.time()
        if lib == "alpha360":
            factors = None  # 流式
        elif lib == "tdxgs":
            factors = tdxgs.compute(daily)
        else:
            factors = jq110.compute(daily, amount, turnover, vwap, market_ret)
        if factors is not None:
            for k in list(
                factors
            ):  # 逐帧转 float32 并即时释放 float64（降内存峰值 ~6GB）
                factors[k] = factors[k].astype(np.float32)
        conv = {
            "alpha360": "字段 t−d 值/当日 close（VOLUME 除以当日 volume）；60 日回溯",
            "tdxgs": "通达信/MyTT 口径（ddof=0、中国式 SMA、EMA=ewm span）；严格窗口 min_periods=窗口",
            "jq110": "聚宽口径；金额=名义成交额（元）、β=对中证500 真实回归；严格窗口",
        }[lib]

        print(f"[3/3] 落盘 {lib} ...")
        out_root = _out_root(lib)
        if lib == "alpha360":
            written = 0
            for i, (ts, day) in enumerate(alpha360.iter_partitions(daily, vwap)):
                if ts.strftime("%Y%m%d") < args.start:
                    continue
                written += _write_day(out_root, ts, day, daily, i)
            n_factors = 360
            dates = daily["close"].index
            n_syms = daily["close"].shape[1]
        else:
            written = _write_frames(factors, lib, args.start, daily)
            n_factors = len(factors)
            dates = next(iter(factors.values())).index
            n_syms = next(iter(factors.values())).shape[1]
        meta = _meta(lib, n_factors, dates, n_syms, conv)
        (out_root / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(
            f"      {lib}: {n_factors} 因子 × {len(dates)} 日 × {n_syms} 只；新写分区 {written}（{time.time() - tc:.0f}s）"
        )

    print(f"完成（总 {time.time() - t0:.0f}s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
