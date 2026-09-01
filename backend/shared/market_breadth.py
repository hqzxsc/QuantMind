"""市场广度与涨跌停判定纯函数（单一事实源，被复盘脚本与市场分析共用）。

涨跌停规则复用 backend/services/trade/simulation/services/local_market_data.py
（compute_limits / limit_pct，与 instrument_detail ZTPrice/DTPrice 交叉验证 99.71% 一致）。
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from backend.services.simulation.services.local_market_data import (
    compute_limits,
    limit_pct,
)

# 容差：SH/SZ 四舍五入到分最多压低 0.5%（股价 ≥1 元）；BJ 截尾到分最多压低 1%
TOL_SHSZ = 0.50
TOL_BJ = 1.00

CAT_LIMIT_UP = "limit_up"
CAT_LIMIT_DOWN = "limit_down"
CAT_BROKE_UP = "broke_up"
CAT_CORP_ACTION = "corp_action"
CAT_NORMAL = "normal"
CAT_UP = "up"
CAT_DOWN = "down"
CAT_FLAT = "flat"


def is_bse_symbol(symbol: str) -> bool:
    code = symbol.partition(".")[0]
    return symbol.endswith(".BJ") or code[:2] in ("43", "83", "87", "88", "92")


def price_tolerance(symbol: str) -> float:
    """价格比较容差（元）：比较涨停价时允许的分位浮点误差。"""
    return 0.004


def classify_price(
    close: float, high: float, up_price: float, down_price: float
) -> str:
    """按价格精确判定：收盘封板 / 炸板 / 跌停 / 普通（方向由调用方按 pct 符号归 up/down/flat）。"""
    if up_price > 0:
        if close >= up_price - price_tolerance("600000.SH"):
            return CAT_LIMIT_UP
        if high >= up_price - price_tolerance("600000.SH"):
            return CAT_BROKE_UP
    if down_price > 0 and close <= down_price + price_tolerance("600000.SH"):
        return CAT_LIMIT_DOWN
    return CAT_NORMAL


def is_corp_action_pct(pct: float, board_pct: float) -> bool:
    """涨跌幅显著超过板块限制 → 除权/拆并股等公司行为（非交易性波动）。"""
    return abs(pct) > board_pct * 100 + 1.0


def classify_by_pct(pct: float, symbol: str, is_st: bool, trade_date: date) -> str:
    """按涨跌幅 + 容差兜底判定（除权日昨收不可信时用）。"""
    board = float(limit_pct(symbol, is_st=is_st, trade_date=trade_date)) * 100
    tol = TOL_BJ if is_bse_symbol(symbol) else TOL_SHSZ
    if pct >= board - tol:
        return CAT_LIMIT_UP
    if pct <= -(board - tol):
        return CAT_LIMIT_DOWN
    if pct > 0:
        return CAT_UP
    if pct < 0:
        return CAT_DOWN
    return CAT_FLAT


def is_ex_div(official_pct: float, close: float, prev_close: float) -> bool:
    """除权除息日检测：官方 pct_change 与 (close/prev_close-1) 自算值差 > 0.5%。"""
    if prev_close is None or prev_close <= 0 or close is None:
        return False
    self_pct = (close / prev_close - 1) * 100
    return abs(official_pct - self_pct) > 0.5


def streak_from_tail(days: list[float], min_pct: float) -> int:
    """从最近一日（列表尾部）往前数，连续 ≥ min_pct 的天数。"""
    n = 0
    for v in reversed(days):
        if v is not None and v >= min_pct:
            n += 1
        else:
            break
    return n


_LABELS = ["涨停", ">7", "5~7", "3~5", "1~3", "0~1", "平盘",
           "-1~0", "-3~-1", "-5~-3", "-7~-5", "<-7", "跌停"]


def breadth_distribution(pct: pd.Series, limit_thresh: float = 9.7) -> dict[str, int]:
    """涨跌幅分布直方图（±limit_thresh 视为涨停/跌停近似桶）。"""
    dist: dict[str, int] = {label: 0 for label in _LABELS}
    for v in pct.dropna():
        if v >= limit_thresh:
            dist["涨停"] += 1
        elif 7.0 <= v < limit_thresh:
            dist[">7"] += 1
        elif 5.0 <= v < 7.0:
            dist["5~7"] += 1
        elif 3.0 <= v < 5.0:
            dist["3~5"] += 1
        elif 1.0 <= v < 3.0:
            dist["1~3"] += 1
        elif 0.0 < v < 1.0:
            dist["0~1"] += 1
        elif v == 0.0:
            dist["平盘"] += 1
        elif -1.0 < v < 0.0:
            dist["-1~0"] += 1
        elif -3.0 < v <= -1.0:
            dist["-3~-1"] += 1
        elif -5.0 < v <= -3.0:
            dist["-5~-3"] += 1
        elif -7.0 < v <= -5.0:
            dist["-7~-5"] += 1
        elif -limit_thresh < v <= -7.0:
            dist["<-7"] += 1
        else:
            dist["跌停"] += 1
    return dist


def market_breadth(pct: pd.Series) -> dict:
    """涨跌家数与涨跌比。"""
    up = int((pct > 0).sum())
    down = int((pct < 0).sum())
    flat = int((pct == 0).sum())
    ratio = round(up / down, 2) if down else None
    return {"up_count": up, "down_count": down, "flat_count": flat, "up_down_ratio": ratio}


def sector_aggregate(
    members: pd.DataFrame,
    pct: pd.Series,
    mv: pd.Series | None = None,
) -> pd.DataFrame:
    """板块表现聚合：成员 (SectorCode, SectorName, SectorType, Symbol) × 个股涨跌幅。"""
    cols = ["SectorCode", "SectorName", "SectorType", "Symbol"]
    m = (
        members[cols]
        .drop_duplicates(subset=["SectorCode", "Symbol"])
        .set_index("Symbol")
        .join(pct.rename("pct"), how="inner")
    )
    if m.empty:
        return pd.DataFrame(
            columns=["SectorCode", "SectorName", "SectorType", "n", "avg_pct",
                     "mv_weighted_pct", "ignored"]
        )
    if mv is not None:
        m = m.join(mv.rename("mv"), how="left")

    rows = []
    for (sec_code, sec_name, sec_type), g in m.groupby(["SectorCode", "SectorName", "SectorType"]):
        avg = float(g["pct"].mean())
        if mv is not None and g["mv"].notna().mean() >= 0.6:
            w = g["mv"].dropna()
            weighted = round(float((g.loc[w.index, "pct"] * w).sum() / w.sum()), 2)
        else:
            weighted = None
        rows.append(
            {
                "SectorCode": sec_code,
                "SectorName": sec_name,
                "SectorType": sec_type,
                "n": len(g),
                "avg_pct": round(avg, 2),
                "mv_weighted_pct": weighted,
                "ignored": 0,
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(
            columns=["SectorCode", "SectorName", "SectorType", "n", "avg_pct",
                     "mv_weighted_pct", "ignored"]
        )
    return out.sort_values("avg_pct", ascending=False).reset_index(drop=True)


def wan_to_yi(value_wan: float | None) -> float | None:
    """万元 → 亿元。"""
    if value_wan is None:
        return None
    return value_wan / 1e4


def fmt_yi(value_wan: float | None) -> str:
    """万元 → 亿元格式化。"""
    yi = wan_to_yi(value_wan)
    if yi is None:
        return "[数据缺失]"
    return f"{yi:,.2f} 亿元"


def volume_ratio_5(current: float | None, prior_amounts: list[float]) -> float | None:
    """量比：当日 / 前 5 日成交额均值。"""
    usable = [a for a in prior_amounts if a is not None and a > 0]
    if current is None or current <= 0 or not usable:
        return None
    return round(current / (sum(usable) / len(usable)), 2)
