#!/usr/bin/env python3
"""Rebuild qlib's ``$factor`` / price bins on a step-wise **multiplicative** basis.

Why
---
``factor.day.bin`` currently stores ``后复权价 / 不复权价`` where the vendor's 后复权价 is
built **additively** (hfq = m*raw + accumulated cash dividend A) instead of multiplicatively.
That ratio ``f = m + A/raw`` therefore drifts **every trading day, inversely to the price**,
and is not even monotone (measured: up to -13% regression inside one year; median number of
days on which ``$factor`` changes = 1940 out of ~2500).

qlib's matching engine interprets ``$factor`` as a *multiplicative* factor
(``real_shares = adjusted_amount * factor``, identity ``close_bin = raw * factor``), so a
daily-drifting factor breaks the trade log:

* sell quantity != buy quantity (measured on a live backtest: 1320 liquidations, 94.7% of them
  on symbols with **no** corporate action inside the holding window -> pure data defect);
* non-lot sell sizes, which A-share rules do not allow (only a liquidation may be a odd lot);
* window returns damped by the accumulated dividend level.

Fix
---
The authoritative total-return relation is ``TR_t = TR_{t-1}`` on an ex-date, hence

    factor_mult[t] = PROD over ex-dates j <= t of (raw_{j-1} / ex_reference_price_j)
    close_new[t]   = raw[t] * factor_mult[t]

with ``raw`` recovered from the bins themselves (``close_bin / factor_bin``, verified equal to
the official unadjusted close to 1.4e-7) and the ex-dates / cash / bonus / allotment taken from
the vendor dividend table (``dividend_factors``: ``time`` = 除权除息日, ``interest`` = 每10股现金
红利, ``stockBonus`` = 每10股送股, ``allotment`` = 每10股配股, ``allotPrice`` = **每股**配股价).
No k-line package has to be re-downloaded; the ``volume`` / ``amount`` bins are already raw and
are deliberately left untouched.

Guards (a symbol is refused, never silently corrupted)
------------------------------------------------------
1. forward: every event must predict the observed raw drop on its ex-date within
   ``[ref/p0 * (1 - limit), ref/p0 * (1 + limit)]`` -- catches wrong/mis-dated/duplicated rows;
2. reverse: any raw drop beyond 1.1x the board limit that the *adjusted* series clearly absorbs
   must be explained by an event -- catches missing rows (measured rate: 1.91%);
3. ``factor`` must end up step-wise and monotone non-decreasing;
4. ``raw`` must agree with ``amount / volume`` (independent unit check);
5. bin start index, length and NaN mask must be preserved.

Also materialises ``change.day.bin``
-----------------------------------
The bins have no ``change`` field, yet ``CnExchange.check_stock_limit`` reads ``$change`` to
detect limit-up/limit-down and swallows the failure -- so price-limit blocking has never been
active. ``pct_change(close_new)`` is exactly the exchange 含权涨跌幅 (on an ex-date the
denominator becomes the reference price), so the corrected series lets us emit it.

Usage
-----
    # audit a sample, write nothing (default)
    python scripts/data/maintenance/rebuild_qlib_multiplicative_factor.py --limit 200

    # mirror corrected bins elsewhere for review
    python ... --limit 200 --out-dir /tmp/qlib_fixed

    # full run in place; keeps <field>.day.bin.bak, refuses any flagged symbol
    python ... --all --apply
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PRICE_BINS = ("open", "high", "low")
REWRITE_BINS = ("open", "high", "low", "close", "factor")
# volume / amount are already unadjusted -> left byte-identical on purpose
DEFAULT_EVENTS = "/data/quantdb/3_financial_data/dividend_factors"

RAW_VWAP_TOL = (
    0.12  # amount/volume 与 raw 的比值离散度上限（两者量纲固定，只看相对波动）
)
DROP_RATIO = 1.1  # “必然是除权日”的判定：裸价跌幅超过 1.1x 涨停幅度
RESILIENCE = (
    0.05  # 后复权价比裸价抗跌多少个百分点才算真的除权（加法阻尼本身能吃掉 ~2%）
)
EVENT_TOL = 0.02  # 事件预测的除权跌幅与实际裸价跌幅的允许偏离
PRICE_MOVE_EPS = 1e-4  # 除权日后价格“真正动了”的最小相对变化
MAX_VERIFY_STEPS = 40  # 除权日停牌时，最多向后找多少个交易日去校验
STEP_EPS = 1e-7


def default_qlib_dir() -> str:
    """Locate qlib_data without assuming where this script is copied to."""
    env = os.getenv("QLIB_DATA_DIR")
    if env:
        return env
    here = Path(__file__).resolve()
    for anc in [here.parent, *here.parents]:
        cand = anc / "db" / "qlib_data"
        if (cand / "calendars" / "day.txt").exists():
            return str(cand)
    return str(Path("db/qlib_data"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild qlib $factor on a step-wise multiplicative basis"
    )
    parser.add_argument(
        "--qlib-dir",
        default=default_qlib_dir(),
        help="qlib_data root (contains calendars/day.txt and features/)",
    )
    parser.add_argument(
        "--events-dir",
        default=os.getenv("QUANTDB_DIVIDEND_DIR", DEFAULT_EVENTS),
        help="directory of <CODE.MKT>.parquet dividend/ex-date tables",
    )
    parser.add_argument("--symbols", default=None, help="file with one symbol per line")
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="audit a random subset of this many symbols (representative slice)",
    )
    parser.add_argument(
        "--seed", type=int, default=20260831, help="shuffle seed used by --sample"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="max symbols per run (ignored with --all)",
    )
    parser.add_argument("--all", action="store_true", help="process every symbol")
    parser.add_argument(
        "--out-dir", default=None, help="mirror the corrected bins into this directory"
    )
    parser.add_argument(
        "--apply", action="store_true", help="write in place with .bak backups"
    )
    parser.add_argument(
        "--no-change-field", action="store_true", help="do not emit change.day.bin"
    )
    parser.add_argument(
        "--report", default=None, help="write a JSON audit report to this path"
    )
    args = parser.parse_args()
    if args.apply and args.out_dir:
        parser.error("--apply and --out-dir are mutually exclusive")
    return args


# ----------------------------------------------------------------------------- bin io


def read_bin(path: Path) -> tuple[int, np.ndarray] | None:
    if not path.exists():
        return None
    arr = np.fromfile(path, dtype="<f4")
    if arr.size < 2:
        return None
    return int(arr[0]), arr[1:].astype(np.float64)


def write_bin(path: Path, start_idx: int, values: np.ndarray) -> None:
    payload = np.empty(values.size + 1, dtype="<f4")
    payload[0] = float(start_idx)
    payload[1:] = values
    tmp = path.with_name(path.name + ".tmp")
    payload.tofile(tmp)
    os.replace(tmp, path)


def board_limit(symbol: str) -> float:
    """涨跌停幅度：科创板/创业板 20%，北交所 30%，主板 10%（ST 更严，只会让判定更保守）。"""
    num = symbol[2:] if symbol[:2].lower() in ("sh", "sz", "bj") else symbol
    if num.startswith(("68", "30")):
        return 0.20
    if num.startswith(("8", "4", "92")):
        return 0.30
    return 0.10


def to_code(symbol: str) -> str:
    if len(symbol) > 2 and symbol[:2].lower() in ("sh", "sz", "bj"):
        return f"{symbol[2:]}.{symbol[:2].upper()}"
    return symbol


# ----------------------------------------------------------------------------- core


def load_events(events_dir: Path, code: str) -> pd.DataFrame | None:
    path = events_dir / f"{code}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if "time" not in df.columns:
        return None
    df["time"] = pd.to_datetime(df["time"])
    return df.sort_values("time").reset_index(drop=True)


def build_multiplicative_factor(
    raw: pd.Series, events: pd.DataFrame, limit: float
) -> tuple[pd.Series, list[dict], list[str], list[str], set[pd.Timestamp]]:
    """factor[t] = PROD(p0 / ex_reference) over ex-dates; validates each event ex post.

    返回的 ``steps`` 是实际生效的因子阶跃日（除权日停牌时 = 复牌首日），供反向闸门去重。
    """
    f = pd.Series(1.0, index=raw.index)
    cum = 1.0
    applied: list[dict] = []
    problems: list[str] = []
    notes: list[str] = []
    steps: set[pd.Timestamp] = set()
    for row in events.itertuples(index=False):
        ex = pd.Timestamp(row.time).normalize()
        prev = raw.index[raw.index < ex]
        if len(prev) == 0:
            # 上市/挂牌前的分红送配（常见于北交所承接新三板），对本段因子无影响，不算异常
            notes.append(f"event_before_listed {ex.date()}")
            continue
        p0 = float(raw.loc[prev[-1]])
        bonus = float(getattr(row, "stockBonus", 0) or 0) / 10.0
        allot = float(getattr(row, "allotment", 0) or 0) / 10.0
        cash = float(getattr(row, "interest", 0) or 0) / 10.0
        # allotPrice 是「每股」配股价（实测 94.7% 的配股事件以此口径拟合除权跌幅）
        aprice = float(getattr(row, "allotPrice", 0) or 0)
        ref = (p0 - cash + aprice * allot) / (1.0 + bonus + allot)
        if not np.isfinite(ref) or ref <= 0:
            problems.append(f"nonpositive_ref {ex.date()}")
            continue
        ratio = p0 / ref
        nxt = raw.index[raw.index >= ex]
        if len(nxt) == 0:
            # 除权日晚于该标的最后一条数据，对已写入的因子段无影响
            notes.append(f"event_after_last_bar {ex.date()}")
            continue
        # 除权日可能停牌（长停牌期间除权），裸价要等到复牌第一天才反映参考价：
        # 因子阶跃必须下在“价格真正变动的第一天”，并以该日做 ex post 校验。
        step_date, p1 = nxt[0], None
        for d in nxt[:MAX_VERIFY_STEPS]:
            v = float(raw.loc[d])
            if abs(v / p0 - 1.0) > PRICE_MOVE_EPS:
                step_date, p1 = d, v
                break
        if p1 is None:
            notes.append(f"event_unverifiable_suspended {ex.date()}")
        else:
            lo, hi = (
                ref / p0 * (1 - limit) - EVENT_TOL,
                ref / p0 * (1 + limit) + EVENT_TOL,
            )
            if not (lo <= p1 / p0 <= hi):
                # 事件行本身不可信（实测全为 type=15 预案行，除权尚未实施）：丢弃该事件。
                # 若它是“日期错位”的真除权，缺口会落在别处，由反向闸门 missing_event 兜住。
                notes.append(
                    f"event_mismatch {ex.date()} pred_ref/p0={ref / p0:.4f} "
                    f"actual={p1 / p0:.4f} on {step_date.date()}"
                )
                continue
        cum *= ratio
        f.loc[f.index >= step_date] = cum
        steps.add(step_date.normalize())
        applied.append(
            {
                "ex_date": str(ex.date()),
                "step_date": str(step_date.date()),
                "jump": ratio - 1.0,
                "cash_per_share": cash,
                "bonus_per_share": bonus,
                "allot_per_share": allot,
                "allot_price": aprice,
            }
        )
    return f, applied, problems, notes, steps


def find_unexplained_ex_dates(
    raw: pd.Series, hfq: pd.Series, covered: set[pd.Timestamp], limit: float
) -> list[str]:
    """反向闸门：裸价跌穿涨停，而后复权价明显抗跌 -> 必然是除权日，事件表却查无此日。"""
    idx = raw.index
    r_raw = raw.pct_change(fill_method=None)
    r_hfq = hfq.reindex(idx).ffill().pct_change(fill_method=None)
    out: list[str] = []
    hits = r_raw[r_raw < -(limit * DROP_RATIO + 0.005)]
    for dt, rr in hits.items():
        if dt.normalize() in covered:
            continue
        rb = r_hfq.get(dt, np.nan)
        if not np.isfinite(rb) or (1.0 + rb) / (1.0 + rr) - 1.0 <= RESILIENCE:
            continue  # 两列同步下跌 = 行情毛刺/异常交易，不是除权
        out.append(f"missing_event {dt.date()} raw={rr:.2%} hfq={rb:.2%}")
    return out


def process_symbol(
    qlib_dir: Path,
    events_dir: Path,
    sym: str,
    out_dir: Path | None,
    apply: bool,
    write_change: bool,
) -> dict:
    src = qlib_dir / "features" / sym
    result: dict = {
        "symbol": sym,
        "status": "ok",
        "problems": [],
        "notes": [],
        "written": [],
    }
    got_close = read_bin(src / "close.day.bin")
    got_factor = read_bin(src / "factor.day.bin")
    if got_close is None or got_factor is None:
        result.update(status="skipped", reason="missing close/factor.day.bin")
        return result
    start_idx, close = got_close
    fac_start, factor_old = got_factor
    if fac_start != start_idx or factor_old.size != close.size:
        result.update(status="skipped", reason="close/factor layout mismatch")
        return result

    events = load_events(events_dir, to_code(sym))
    if events is None:
        result.update(status="skipped", reason=f"no dividend table for {to_code(sym)}")
        return result

    has_price = np.isfinite(close) & (close > 0)
    usable = has_price & np.isfinite(factor_old) & (factor_old > 0)
    if int(usable.sum()) < 30:
        result.update(status="skipped", reason="too few usable rows")
        return result
    cal = read_calendar(qlib_dir)
    idx = pd.DatetimeIndex(pd.to_datetime(cal[start_idx : start_idx + close.size]))
    raw_s = pd.Series(np.where(usable, close / factor_old, np.nan), index=idx).ffill()
    raw = raw_s.where(raw_s > 0)
    if raw.isna().any():
        result["problems"].append("raw series has holes")

    limit = board_limit(sym)
    f_new, applied, ev_problems, ev_notes, ev_steps = build_multiplicative_factor(
        raw, events, limit
    )
    result["problems"].extend(ev_problems)
    result["notes"] = ev_notes
    covered = set(pd.DatetimeIndex(pd.to_datetime(events["time"])).normalize())
    covered |= ev_steps
    result["problems"].extend(
        find_unexplained_ex_dates(raw, pd.Series(close, index=idx), covered, limit)
    )

    fv = f_new.to_numpy()
    rel = np.diff(fv) / fv[:-1]
    stats = {
        "n_days": int(has_price.sum()),
        "n_events": int(len(events)),
        "n_events_applied": len(applied),
        "factor_steps_new": int((np.abs(rel) > STEP_EPS).sum()),
        "factor_steps_old": int(
            (
                np.abs(np.diff(factor_old[usable]) / factor_old[usable][:-1]) > STEP_EPS
            ).sum()
        ),
        "raw_vwap_rel": None,
        "factor_end": float(fv[-1]),
        "level_ratio_end": float((raw_s.to_numpy() * fv)[-1] / close[-1])
        if close[-1] > 0
        else None,
        "level_ratio_min": float(
            np.nanmin((raw_s.to_numpy() * fv) / np.where(has_price, close, np.nan))
        ),
        "level_ratio_max": float(
            np.nanmax((raw_s.to_numpy() * fv) / np.where(has_price, close, np.nan))
        ),
    }
    if (rel < -STEP_EPS).any():
        result["problems"].append("factor not monotone")
    if stats["factor_steps_new"] > max(0.05 * stats["n_days"], 30):
        result["problems"].append(f"factor not step-wise ({stats['factor_steps_new']})")
    vol, amt = read_bin(src / "volume.day.bin"), read_bin(src / "amount.day.bin")
    if vol and amt and vol[0] == amt[0] == start_idx:
        with np.errstate(invalid="ignore", divide="ignore"):
            # volume 为手、amount 为万元，amount/volume 与 raw 只差一个常数，故只看离散度
            vwap = np.where(usable & (vol[1] > 0), amt[1] / vol[1], np.nan)
            ratio = vwap / raw_s.to_numpy()
        med = float(np.nanmedian(ratio))
        stats["raw_vwap_rel"] = (
            float(np.nanmedian(np.abs(ratio / med - 1.0))) if med > 0 else None
        )
        if stats["raw_vwap_rel"] is not None and stats["raw_vwap_rel"] > RAW_VWAP_TOL:
            result["problems"].append(
                f"amount/volume vs raw rel={stats['raw_vwap_rel']:.3f}"
            )

    result["stats"] = stats
    new_close = np.where(has_price, raw_s.to_numpy() * fv, np.nan)
    values: dict[str, np.ndarray] = {
        "close": new_close,
        "factor": np.where(has_price, fv, np.nan),
    }
    for field in PRICE_BINS:
        got = read_bin(src / f"{field}.day.bin")
        if got is None or got[0] != start_idx or got[1].size != close.size:
            result["problems"].append(f"{field}.day.bin layout mismatch")
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            scale = np.where(usable, fv / factor_old, np.nan)
        values[field] = np.where(has_price, got[1] * scale, np.nan)
    if write_change:
        chg = np.full(close.size, np.nan)
        chg[1:] = new_close[1:] / new_close[:-1] - 1.0
        chg[~has_price] = np.nan
        values["change"] = chg

    if out_dir is None and not apply:
        return result
    if apply and result["problems"]:
        result["status"] = "blocked"
        return result

    dst = (out_dir / "features" / sym) if out_dir is not None else src
    dst.mkdir(parents=True, exist_ok=True)
    for field, arr in values.items():
        target = dst / f"{field}.day.bin"
        if apply:
            bak = target.with_name(target.name + ".bak")
            if target.exists() and not bak.exists():
                os.link(target, bak)  # 硬链接保留原始字节，不额外占空间
        write_bin(target, start_idx, arr)
        result["written"].append(field)
    return result


# ----------------------------------------------------------------------------- driver

_CALENDAR: list[str] | None = None


def read_calendar(qlib_dir: Path) -> list[str]:
    global _CALENDAR
    if _CALENDAR is None:
        _CALENDAR = [
            ln.strip()
            for ln in (qlib_dir / "calendars" / "day.txt").read_text().splitlines()
            if ln.strip()
        ]
    return _CALENDAR


def list_symbols(
    qlib_dir: Path,
    symbols_file: str | None,
    limit: int,
    every: bool,
    sample: int | None,
    seed: int,
) -> list[str]:
    if symbols_file:
        syms = [
            ln.strip()
            for ln in Path(symbols_file).read_text().splitlines()
            if ln.strip()
        ]
    else:
        syms = sorted(p.name for p in (qlib_dir / "features").iterdir() if p.is_dir())
    if sample is not None:
        rng = random.Random(seed)
        syms = syms[:]
        rng.shuffle(syms)
        return syms[:sample]
    return syms if every else syms[:limit]


def prepare_mirror(qlib_dir: Path, out_dir: Path) -> None:
    """A usable qlib_data mirror also needs the calendar and instruments."""
    (out_dir / "calendars").mkdir(parents=True, exist_ok=True)
    dst_cal = out_dir / "calendars" / "day.txt"
    if not dst_cal.exists():
        dst_cal.write_bytes((qlib_dir / "calendars" / "day.txt").read_bytes())
    src_inst = qlib_dir / "instruments"
    if src_inst.is_dir():
        os.system(f"cp -rn '{src_inst}' '{out_dir / 'instruments'}'")


def main() -> int:
    args = parse_args()
    qlib_dir, events_dir = Path(args.qlib_dir), Path(args.events_dir)
    if not (qlib_dir / "calendars" / "day.txt").exists():
        print(f"[ERR] no qlib calendar under {qlib_dir}", file=sys.stderr)
        return 2
    if not events_dir.is_dir():
        print(f"[ERR] no dividend table dir {events_dir}", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir) if args.out_dir else None
    apply = bool(args.apply)
    if out_dir is not None:
        prepare_mirror(qlib_dir, out_dir)

    syms = list_symbols(
        qlib_dir, args.symbols, args.limit, args.all, args.sample, args.seed
    )
    mode = (
        "APPLY (in place, .bak backups)"
        if apply
        else (f"MIRROR -> {out_dir}" if out_dir else "AUDIT ONLY")
    )
    print(f"qlib dir : {qlib_dir}")
    print(f"events   : {events_dir}")
    print(f"mode     : {mode}")
    print(f"symbols  : {len(syms)}")

    results: list[dict] = []
    skipped = blocked = 0
    for i, sym in enumerate(syms, 1):
        res = process_symbol(
            qlib_dir, events_dir, sym, out_dir, apply, not args.no_change_field
        )
        if res["status"] == "skipped":
            skipped += 1
        elif res["status"] == "blocked":
            blocked += 1
        results.append(res)
        if i % 500 == 0:
            print(f"  ... {i}/{len(syms)} (skipped {skipped}, blocked {blocked})")

    ok = [r for r in results if "stats" in r]
    if ok:
        df = pd.DataFrame([r["stats"] for r in ok])
        print("\n===== 审计摘要 =====")
        print(f"处理成功 {len(ok)} / 跳过 {skipped} / 因异常被拦截 {blocked}")
        print(
            f"因子变化次数：修复前中位 {df['factor_steps_old'].median():.0f} 天 -> "
            f"修复后中位 {df['factor_steps_new'].median():.0f} 天（=除权除息日数）"
        )
        print(
            f"新因子末值：中位 {df['factor_end'].median():.3f}  "
            f"p95 {df['factor_end'].quantile(0.95):.3f}  最大 {df['factor_end'].max():.3f}"
        )
        print("新/旧 close 水平比（= 历史回测净值的口径变化幅度）:")
        for col in ("level_ratio_end", "level_ratio_min", "level_ratio_max"):
            v = pd.Series(df[col].astype(float))
            print(
                f"  {col:16s} 中位 {v.median():.4f}  p5 {v.quantile(0.05):.4f}  "
                f"p95 {v.quantile(0.95):.4f}  min {v.min():.4f}  max {v.max():.4f}"
            )
        print(
            "amount/volume 与 raw 比值离散度（中位）："
            f"{pd.Series(df['raw_vwap_rel'].astype(float)).median():.4f}"
        )
        note_kinds: dict[str, int] = {}
        prob_kinds: dict[str, int] = {}
        for r in ok:
            for n in r.get("notes", []):
                k = n.split(" ", 1)[0]
                note_kinds[k] = note_kinds.get(k, 0) + 1
            for p in r["problems"]:
                k = p.split(" ", 1)[0]
                prob_kinds[k] = prob_kinds.get(k, 0) + 1
        print(f"事件备注（不算异常）：{note_kinds or '无'}")
        print(f"异常类型统计：{prob_kinds or '无'}")
        flagged = [r for r in ok if r["problems"]]
        if flagged:
            print(f"\n异常样本 {len(flagged)} 个（前 15）:")
            for r in flagged[:15]:
                print(f"  {r['symbol']}: {'; '.join(r['problems'][:3])}")
    else:
        print("[WARN] no symbol processed")

    if args.report:
        Path(args.report).write_text(
            json.dumps(results, ensure_ascii=False, default=str)
        )
        print(f"\nreport -> {args.report}")
    if apply:
        manifest = {
            "symbols_written": sum(1 for r in results if r["written"]),
            "symbols_blocked": blocked,
            "fields_rewritten": list(REWRITE_BINS),
            "fields_added": [] if args.no_change_field else ["change"],
            "fields_untouched": ["volume", "amount"],
            "backup_suffix": ".day.bin.bak",
            "rollback": 'for f in features/*/*.day.bin.bak; do mv "$f" "${f%.bak}"; done',
            "note": "downstream artifacts derived from $close (model labels, pred.parquet, "
            "feature snapshots) must be regenerated after applying.",
        }
        (qlib_dir / "_rebuild_manifest.json").write_text(json.dumps(manifest, indent=2))
        print(f"manifest -> {qlib_dir / '_rebuild_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
