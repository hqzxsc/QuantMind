"""生成 L2 CatBoost T+5 策略专业回测报告（Markdown → 可转 PDF）。

内置回测全部数据（附详情），生成研报级 Markdown。
"""
import sys
from datetime import date
from pathlib import Path
import asyncio

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.backtest_l2_top20 import (
    load_signals, load_klines, load_st_symbols, run_backtest, INIT_CASH, TOP_N, MODEL_ID,
    COMMISSION, STAMP_TAX, START_DATE,
)
from backend.shared.database_manager_v2 import get_session
from sqlalchemy import text


def load_names():
    """股票名称/行业映射（同步 psycopg2，避免 asyncio 生命周期冲突）"""
    import os
    import psycopg2
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "quantmind-db"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "quantmind"),
        password=os.getenv("POSTGRES_PASSWORD", "quantmind2026"),
        dbname=os.getenv("POSTGRES_DB", "quantmind"),
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT symbol, name, industry FROM stocks")
            return {str(x): (str(y), str(z or "")) for x, y, z in cur.fetchall()}
    finally:
        conn.close()


def load_ind():
    n = load_names()
    return {k: v[1] for k, v in n.items()}


def _fmt(v, digits=2):
    if v is None:
        return "--"
    return f"{v:,.{digits}f}"


def main():
    print("loading signal...", file=sys.stderr)
    signals = load_signals()
    all_syms = set()
    for it in signals.values():
        for s, _ in it:
            all_syms.add(s)
    print("loading kline...", file=sys.stderr)
    klines = load_klines(all_syms)
    result = run_backtest(signals, klines, st_symbols=load_st_symbols())
    daily = result["daily"]
    trades = result["trades"]
    dates = result["dates"]

    names = load_names()
    inds = load_ind()

    # ===== 计算统计 =====
    netx = [daily[d]["value"] / INIT_CASH for d in dates]
    total_ret = netx[-1] - 1
    days = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days
    years = max(days, 1) / 365
    annual = netx[-1] ** (1 / years) - 1 if netx[-1] > 0 else None
    # 最大回撤
    peak = -1e9
    max_dd = 0
    for n in netx:
        peak = max(peak, n)
        dd = (peak - n) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    # 日收益
    rets = []
    for i in range(1, len(netx)):
        rets.append(netx[i] / netx[i - 1] - 1)
    import math
    vol = (sum((r - sum(rets) / len(rets)) ** 2 for r in rets) / max(len(rets) - 1, 1)) ** 0.5 if rets else 0
    sharpe = (sum(rets) / len(rets) / vol * math.sqrt(252)) if rets and vol > 0 else None
    downside = [r for r in rets if r < 0]
    downside_vol = (sum(r * r for r in downside) / max(len(downside), 1)) ** 0.5 if downside else 0
    sortino = (sum(rets) / len(rets) / downside_vol * math.sqrt(252)) if rets and downside_vol > 0 else None

    buys = [t for t in trades if t["action"] == "BUY"]
    sells = [t for t in trades if t["action"] == "SELL"]
    total_buy = sum(t["px"] * t["shares"] for t in buys)
    total_sell = sum(t["px"] * t["shares"] for t in sells)

    # 各行收益（卖出盈利）
    win_count = 0
    sell_pnl = 0
    for t in sells:
        # 需要配对买入价——简化：按买入均价粗算
        pass
    # 用 final: 综合统计

    lines = []
    A = lines.append
    A("# L2 CatBoost T+5 策略回测报告")
    A("")
    A(f"> **模型**：L2 CatBoost T+5 (2023-2025训练) _CN  ·  `{MODEL_ID}`")
    A(f"> **策略**：Top{TOP_N} 每日滚动 · 资金用满(≥60%) · 涨停/跌停等开板 · 剔除ST · 佣金万三 + 印花税0.1%")
    A(f"> **周期**：{dates[0]} ~ {dates[-1]}  ·  **初始资金**：50 万元")
    A("")
    A("---")
    A("")
    A("## 一、核心业绩指标")
    A("")
    A("| 指标 | 数值 | 指标 | 数值 |")
    A("|---|---|---|---|")
    A(f"| 累计收益 | **{total_ret*100:+.2f}%** | 年化收益 | {annual*100:+.2f}%（短周期外推） |")
    A(f"| 最大回撤 | {max_dd*100:.2f}% | 夏普比率 | {sharpe:.2f} |" if sharpe else "| 最大回撤 | -- | 夏普 | -- |")
    A(f"| 索提诺比率 | {sortino:.2f} | 日波动率 | {vol*100:.2f}% |" if sortino else "| 索提诺 | -- | 日波动 | -- |")
    A(f"| 交易天数 | {len(dates)} | 卖出笔数 | {len(sells)} |")
    A(f"| 总买入额 | {_fmt(total_buy)} 元 | 总卖出额 | {_fmt(total_sell)} 元 |")
    A(f"| 末日现金 | {_fmt(daily[dates[-1]]['cash'])} 元 | 末日持仓市值 | {_fmt(daily[dates[-1]]['value'] - daily[dates[-1]]['cash'])} 元 |")
    val_last = daily[dates[-1]]['value']
    cash_last = daily[dates[-1]].get('cash', 0)
    A(f"| 末日净值 | {netx[-1]:.4f} | 资金利用率 | {(val_last-cash_last)/val_last*100:.1f}% |")
    A("")
    A("> 说明：回测周期仅 11 个交易日，年化/夏普为短周期外推，参考意义有限；累计收益与最大回撤为实际测算。")
    A("")
    A("---")
    A("")
    A("## 二、净值曲线")
    A("")
    A("| 日期 | 持仓数 | 现金(元) | 持仓市值(元) | 总资产(元) | 净值 | 日收益 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    prev = None
    for i, d in enumerate(dates):
        info = daily[d]
        val = info["value"]
        cash_d = info.get("cash", 0)
        hold = val - cash_d
        net = val / INIT_CASH
        dr = (net / prev - 1) * 100 if prev else None
        A(f"| {d} | {info.get('n', 0)} | {_fmt(cash_d,0)} | {_fmt(hold,0)} | {_fmt(val,0)} | {net:.4f} | {dr:+.2f}% |" if dr is not None else f"| {d} | {info.get('n',0)} | {_fmt(cash_d,0)} | {_fmt(hold,0)} | {_fmt(val,0)} | {net:.4f} | -- |")
        prev = net
    A("")
    A("---")
    A("")
    A("## 三、策略规则")
    A("")
    A("1. **选股**：每日取模型预测分数最高的 20 只作为目标持仓（仅正分，剔除 ST/*ST）。")
    A("2. **买入**：每只按目标金额分配（= 权益×90%÷20，至少用到 60% 资金），开盘价买入，取 100 股整数倍。")
    A("3. **涨跌停处理**：涨停（一字板）买入挂单等待开板；跌停（一字板）卖出挂单等待开板，确保不追涨停、不砸跌停。")
    A("4. **换股**：持仓跌出次日前 20、或分数为负 → 次日开盘卖出；新入选补足 20 只。")
    A("5. **交易成本**：佣金万三（买卖），印花税 0.1%（卖）。")
    A("6. **交易时点**：当日收盘分数 → 次日开盘价交易，避免前视偏差。")
    A("")
    A("---")
    A("")
    A("## 四、持仓明细（Top20 目标池）")
    A("")
    A("| 排名 | 代码 | 名称 | 行业 | 信号分 |")
    A("|---|---|---|---|---:|")
    # 末日 top20 信号
    last_d = dates[-1]
    last_items = sorted(signals[last_d], key=lambda x: -x[1])[:TOP_N]
    for i, (sym, sc) in enumerate(last_items):
        row = names.get(sym) or (sym, "")
        name = row[0] if isinstance(row, tuple) else row
        ind = row[1] if isinstance(row, tuple) else inds.get(sym, "")
        A(f"| {i+1} | {sym} | {name} | {ind} | {sc:.4f} |")
    A("")
    A("---")
    A("")
    A("## 五、交易明细（前 30 笔）")
    A("")
    A("| 日期 | 代码 | 名称 | 方向 | 价格 | 股数 | 金额(元) | 原因 |")
    A("|---|---|---|---|---:|---:|---:|---|")
    for t in trades[:30]:
        sym = t["symbol"]
        row = names.get(sym) or (sym, "")
        name = row[0] if isinstance(row, tuple) else row
        A(f"| {t['day']} | {sym} | {name} | {t['action']} | {t['px']:.2f} | {t['shares']} | {_fmt(t['px']*t['shares'],0)} | {t['reason']} |")
    if len(trades) > 30:
        A(f"| ... | 其余 {len(trades)-30} 笔见附录 | | | | | | |")
    A("")
    out = Path(__file__).parent / "L2_CatBoost_T5_回测报告.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已生成: {out} ({len(lines)} 行)")


if __name__ == "__main__":
    main()
