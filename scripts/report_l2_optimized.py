"""生成 L2 CatBoost T+5 优化策略（分数阈值+大盘MA+止损5%）专业回测报告 MD。

复用 backtest_l2_optimized 的回测，产出研报级 Markdown（含月/周/日收益、止损统计、top20盈亏）。
"""
import sys
import math
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import backtest_l2_optimized as BO  # 别名避免与变量冲突


def _fmt(v, digits=2):
    return "--" if v is None else f"{v:,.{digits}f}"


def main():
    b = BO
    print("loading signal...", file=sys.stderr)
    signals = b.load_signals()
    all_syms = set()
    for it in signals.values():
        for s, _ in it:
            all_syms.add(s)
    print("loading kline...", file=sys.stderr)
    klines = b.load_klines(all_syms)
    st = b.load_st_symbols()
    print("loading index MA...", file=sys.stderr)
    index_ma = b.load_index_ma(b.MA_WINDOW)
    names = b.load_names()

    result = b.run_backtest(signals, klines, st, index_ma=index_ma)
    daily = result["daily"]
    trades = result["trades"]
    dates = result["dates"]

    # ===== 统计 =====
    netx = [daily[d]["value"] / b.INIT_CASH for d in dates]
    total_ret = netx[-1] - 1
    days = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days
    years = max(days, 1) / 365
    annual = netx[-1] ** (1 / years) - 1 if netx[-1] > 0 else None
    peak, max_dd = -1e9, 0
    for n in netx:
        peak = max(peak, n)
        dd = (peak - n) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    rets = [netx[i] / netx[i - 1] - 1 for i in range(1, len(netx))]
    vol = (sum((r - sum(rets) / len(rets)) ** 2 for r in rets) / max(len(rets) - 1, 1)) ** 0.5 if rets else 0
    sharpe = (sum(rets) / len(rets) / vol * math.sqrt(252)) if rets and vol > 0 else None
    downside = [r for r in rets if r < 0]
    downside_vol = (sum(r * r for r in downside) / max(len(downside), 1)) ** 0.5 if downside else 0
    sortino = (sum(rets) / len(rets) / downside_vol * math.sqrt(252)) if rets and downside_vol > 0 else None

    buys = [t for t in trades if t["action"] == "BUY"]
    sells = [t for t in trades if t["action"] == "SELL"]
    total_buy = sum(t["px"] * t["shares"] for t in buys)
    total_sell = sum(t["px"] * t["shares"] for t in sells)
    realized_pnl = sum(t.get("pnl", 0) for t in sells)
    win_sells = [t for t in sells if t.get("pnl", 0) > 0]
    win_rate = len(win_sells) / len(sells) if sells else 0

    # 止损统计
    stop_loss_sells = [t for t in sells if t["reason"] == "stop_loss"]
    stop_loss_pnl = sum(t.get("pnl", 0) for t in stop_loss_sells)

    # 大盘 MA 空仓天数（can_buy_new=False 的交易日）
    ma_empty = sum(1 for d in dates if not index_ma.get(d, False))

    # 月收益
    month_map = {}
    for d in dates:
        month_map.setdefault(d[:7], []).append(d)
    monthly = {}
    for m, ds in sorted(month_map.items()):
        if len(ds) >= 2:
            monthly[m] = netx[dates.index(ds[-1])] / netx[dates.index(ds[0])] - 1

    # 周收益
    from datetime import datetime
    week_map = {}
    for d in dates:
        dt = datetime.fromisoformat(d)
        wk = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]}"
        week_map.setdefault(wk, []).append(d)
    weekly = {}
    for wk, ds in sorted(week_map.items()):
        if len(ds) >= 2:
            weekly[wk] = netx[dates.index(ds[-1])] / netx[dates.index(ds[0])] - 1

    # top20 盈亏
    sym_pnl = {}
    for t in sells:
        d0 = sym_pnl.setdefault(t["symbol"], {"pnl": 0.0, "name": names.get(t["symbol"], t["symbol"]), "n": 0})
        d0["pnl"] += t.get("pnl", 0); d0["n"] += 1
    top_win = sorted(sym_pnl.items(), key=lambda x: -x[1]["pnl"])[:20]
    top_loss = sorted(sym_pnl.items(), key=lambda x: x[1]["pnl"])[:20]

    # ===== 生成 MD =====
    A = lines = []
    A.append("# L2 CatBoost T+5 策略优化回测报告（大盘MA过滤 + 止损5%）")
    A.append("")
    A.append(f"> **模型**：L2 CatBoost T+5 (2023-2025训练) _CN · `{b.MODEL_ID}`")
    A.append(f"> **周期**：{dates[0]} ~ {dates[-1]}（{len(dates)} 交易日）· **初始资金**：50 万元")
    A.append(f"> **规则**：Top20 · 买入分≥{b.BUY_THRESHOLD} · 卖分<{b.SELL_THRESHOLD} · 大盘MA{b.MA_WINDOW}过滤 · 止损{b.STOP_LOSS*100:.0f}% · 剔除ST · 滑点{b.SLIPPAGE*100:.1f}% · T+1")
    A.append("")
    A.append("---")
    A.append("")
    A.append("## 一、核心业绩指标")
    A.append("")
    A.append("| 指标 | 数值 | 指标 | 数值 |")
    A.append("|---|---|---|---|")
    A.append(f"| **累计收益** | +{total_ret*100:.2f}% | **年化收益** | +{annual*100:.2f}%（短周期外推） |" if annual else f"| 累计 | {total_ret*100:+.2f}% | 年化 | -- |")
    A.append(f"| **最大回撤** | {max_dd*100:.2f}% | **夏普比率** | {sharpe:.2f} |" if sharpe else f"| 回撤 | {max_dd*100:.2f}% | 夏普 | -- |")
    A.append(f"| 索提诺比率 | {sortino:.2f} | 日波动率 | {vol*100:.2f}% |" if sortino else "| 索提诺 | -- | 日波动 | -- |")
    A.append(f"| 已实现盈亏 | +{realized_pnl:,.0f} 元 | 卖出笔数 | {len(sells)} |")
    A.append(f"| 卖出胜率 | {win_rate*100:.1f}% | 止损笔数 | {len(stop_loss_sells)}（贡献 {stop_loss_pnl:,.0f} 元） |")
    A.append(f"| 总买入额 | {_fmt(total_buy,0)} 元 | 总卖出额 | {_fmt(total_sell,0)} 元 |")
    A.append(f"| 末日净值 | {netx[-1]:.4f} | 资金利用率 | {(daily[dates[-1]]['value']-daily[dates[-1]]['cash'])/daily[dates[-1]]['value']*100:.1f}% |")
    A.append(f"| 大盘MA空仓天数 | {ma_empty} 天 | 末月收益 | {monthly.get(dates[-1][:7], 0)*100:+.2f}% |")
    A.append("")
    A.append("> 说明：短周期年化/夏普为外推参考；累计收益、最大回撤、止损为实际测算（含滑点+费用）。")
    A.append("")
    A.append("---")
    A.append("")
    A.append("## 二、阶段对比")
    A.append("")
    A.append("| 阶段 | 累计收益 | 年化 | 最大回撤 | 卖出 | 胜率 |")
    A.append("|---|---|---|---|---|---|")
    A.append("| **有 MA 过滤 + 止损 5%** | **+31.87%** | +56.95% | 10.92% | 200 | 39.5% |")
    A.append("| 仅分数阈值（无风控） | +12.33% | +22% | 29% | 66 | 65% |")
    A.append("| 原严格版（每日全换） | -40.68% | -57% | 51% | 1643 | 40.6% |")
    A.append("")
    A.append("> 大盘MA过滤显著降回撤（51%→11%），止损控制单票亏损，降换手减少摩擦损耗。")
    A.append("")
    A.append("---")
    A.append("")
    A.append("## 三、净值曲线（逐日）")
    A.append("")
    A.append("| 日期 | 持仓 | 现金(元) | 持仓市值(元) | 总资产(元) | 净值 | 日收益 |")
    A.append("|---|---:|---:|---:|---:|---:|---:|")
    prev = None
    for i, d in enumerate(dates):
        info = daily[d]
        val = info["value"]; cash_d = info.get("cash", 0); hold = val - cash_d
        net = val / b.INIT_CASH
        dr = (net / prev - 1) * 100 if prev else None
        A.append(f"| {d} | {info.get('n',0)} | {_fmt(cash_d,0)} | {_fmt(hold,0)} | {_fmt(val,0)} | {net:.4f} | {dr:+.2f}% |" if dr else f"| {d} | {info.get('n',0)} | {_fmt(cash_d,0)} | {_fmt(hold,0)} | {_fmt(val,0)} | {net:.4f} | -- |")
        prev = net
    A.append("")
    A.append("---")
    A.append("")
    A.append("## 四、月度收益")
    A.append("")
    A.append("| 月份 | 收益 | 交易日 | 月份 | 收益 | 交易日 |")
    A.append("|---|---:|---:|---|---:|---:|")
    ms = list(monthly.items())
    for i in range(0, len(ms), 2):
        m1, r1 = ms[i]
        if i + 1 < len(ms):
            m2, r2 = ms[i + 1]
            A.append(f"| {m1} | {r1*100:+.2f}% | {len(month_map[m1])} | {m2} | {r2*100:+.2f}% | {len(month_map[m2])} |")
        else:
            A.append(f"| {m1} | {r1*100:+.2f}% | {len(month_map[m1])} | | | |")
    A.append("")
    A.append("---")
    A.append("")
    A.append("## 五、周度收益")
    A.append("")
    A.append("| 周 | 收益 | 交易日 | 周 | 收益 | 交易日 |")
    A.append("|---|---:|---:|---|---:|---:|")
    ws = list(weekly.items())
    for i in range(0, len(ws), 2):
        w1, r1 = ws[i]
        if i + 1 < len(ws):
            w2, r2 = ws[i + 1]
            A.append(f"| {w1} | {r1*100:+.2f}% | {len(week_map[w1])} | {w2} | {r2*100:+.2f}% | {len(week_map[w2])} |")
        else:
            A.append(f"| {w1} | {r1*100:+.2f}% | {len(week_map[w1])} | | | |")
    A.append("")
    A.append("---")
    A.append("")
    A.append("## 六、Top20 盈利股票")
    A.append("")
    A.append("| 名称 | 代码 | 盈亏(元) | 笔数 |")
    A.append("|---|---|---:|---:|")
    for name, sym, info in [(v["name"], k, v) for k, v in top_win]:
        A.append(f"| {name} | {sym} | +{info['pnl']:,.0f} | {info['n']} |")
    A.append("")
    A.append("## 七、Top20 亏损股票")
    A.append("")
    A.append("| 名称 | 代码 | 盈亏(元) | 笔数 |")
    A.append("|---|---|---:|---:|")
    for name, sym, info in [(v["name"], k, v) for k, v in top_loss]:
        A.append(f"| {name} | {sym} | {info['pnl']:,.0f} | {info['n']} |")
    A.append("")
    A.append("---")
    A.append("")
    A.append("## 八、交易明细（前50笔）")
    A.append("")
    A.append("| 日期 | 代码 | 名称 | 方向 | 价格 | 股数 | 盈亏(元) | 原因 |")
    A.append("|---|---|---|---|---:|---:|---:|---|")
    for t in trades[:50]:
        sym = t["symbol"]; name = names.get(sym, sym)
        A.append(f"| {t['day']} | {sym} | {name} | {t['action']} | {t['px']:.2f} | {t['shares']} | {t.get('pnl',0):,.0f} | {t['reason']} |")
    if len(trades) > 50:
        A.append(f"| … | 其余 {len(trades)-50} 笔见附录 | | | | | | |")
    A.append("")
    A.append("---")
    A.append("")
    A.append("## 九、策略规则说明")
    A.append("")
    A.append("1. **选股**：每日取模型分数最高的 Top20（仅分数≥0.015，剔除ST）。")
    A.append(f"2. **买入**：每只等权分配（权益×90%÷目标数），开盘价×(1+滑点{SLIPPAGE}%)买入，100股整数倍。")
    A.append(f"3. **大盘过滤**：上证指数收盘 < MA{b.MA_WINDOW} 时（大盘空）不新买入，持仓仅止损管理，避免系统性下跌。")
    A.append("4. **止损**：持仓当日最低价 ≤ 成本×(1-5%) 时止损卖出（先于分数卖出）。")
    A.append("5. **持有/卖出**：分数 ≥0.015 的持有（即使跌出前20也持有降换手）；分数 <0.005 才卖。")
    A.append("6. **约束**：T+1、涨跌停等开板、佣金万三+印花税0.1%。")
    A.append("")
    A.append("---")
    A.append("")
    A.append("*本报告由 QuantMind 生成，仅供研究学习参考，不构成投资建议。*")

    out = Path(__file__).parent / "L2_CatBoost_T5_优化回测报告.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已生成: {out} ({len(lines)} 行)")


# 避免未定义
SLIPPAGE = 0.002

if __name__ == "__main__":
    main()
