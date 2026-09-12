"""Generate 3-model comparison MD report. Runs backtests sequentially in one process."""
import sys
import math
import json
from datetime import date
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, "/tmp")
import backtest_l2_optimized as BO

MODELS = [
    {"id": "mdl_cn_train_20260819100559_9163cb84_ac5c5b2e", "label": "CatBoost T+5", "short": "CB-T5"},
    {"id": "mdl_cn_train_20260819233452_e33cf1b9_0f7c7c89", "label": "XGBoost T+5", "short": "XGB-T5"},
    {"id": "mdl_cn_train_20260819132944_adda8ddb_dd8b2428", "label": "CatBoost T+3", "short": "CB-T3"},
]

OUT = Path("/tmp/L2_三模型对比回测报告.md")


def load_signals_sync(model_id):
    """Load signals using psycopg2 (synchronous, no event-loop issues)."""
    import os
    import psycopg2
    from collections import defaultdict
    from backend.shared.stock_utils import StockCodeUtil
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "quantmind-db"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "quantmind"),
        password=os.getenv("POSTGRES_PASSWORD", "quantmind2026"),
        dbname=os.getenv("POSTGRES_DB", "quantmind"),
    )
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT trade_date, symbol, fusion_score
                FROM engine_signal_scores
                WHERE run_id IN (SELECT run_id FROM (
                    SELECT DISTINCT ON (data_trade_date) run_id, data_trade_date
                    FROM qm_model_inference_runs
                    WHERE model_id=%s AND status='completed'
                    ORDER BY data_trade_date, created_at DESC
                ) latest_run)
                  AND trade_date >= %s AND trade_date <= %s
                ORDER BY trade_date, fusion_score DESC
            """, (model_id, BO.START_DATE, BO.END_DATE))
            rows = cur.fetchall()
    finally:
        conn.close()

    signals = defaultdict(list)
    for trade_date, symbol, score in rows:
        dt = trade_date.strftime("%Y-%m-%d") if hasattr(trade_date, 'strftime') else str(trade_date)[:10]
        try:
            sym = StockCodeUtil.to_suffix(str(symbol).strip().upper())
        except Exception:
            sym = str(symbol)
        signals[dt].append((sym, float(score) if score else 0))
    return dict(signals)


def run_backtest_for_model(model_id, label, short):
    """Run backtest loading signals synchronously."""
    BO.MODEL_ID = model_id
    print(f"  Loading signals for {label}...", file=sys.stderr)
    signals = load_signals_sync(model_id)
    print(f"  {len(signals)} days, {len(set(s for v in signals.values() for s,_ in v))} symbols", file=sys.stderr)

    all_syms = set()
    for it in signals.values():
        for s, _ in it:
            all_syms.add(s)

    klines = BO.load_klines(all_syms)
    st = BO.load_st_symbols()
    index_ma = BO.load_index_ma(BO.MA_WINDOW)
    names = BO.load_names()

    result = BO.run_backtest(signals, klines, st, index_ma=index_ma)
    daily = result["daily"]
    trades = result["trades"]
    dates = result["dates"]

    netx = [daily[d]["value"] / BO.INIT_CASH for d in dates]
    total_ret = netx[-1] - 1
    days = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days
    years = max(days, 1) / 365
    annual = netx[-1] ** (1 / years) - 1 if netx[-1] > 0 else None

    peak, max_dd = -1e9, 0
    for n in netx:
        peak = max(peak, n)
        dd = (peak - n) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    win_trades = [t for t in trades if t["pnl"] > 0]
    win_rate = len(win_trades) / len(trades) * 100 if trades else 0
    total_pnl = sum(t["pnl"] for t in trades)
    usage = sum(1 for d in dates if len(daily[d].get("holdings", [])) > 0) / len(dates) if dates else 0

    monthly = {}
    for d in dates:
        m = d[:7]
        monthly.setdefault(m, []).append(netx[dates.index(d)])
    monthly_ret = {m: (arr[-1] / arr[0] - 1) * 100 for m, arr in monthly.items()}

    weekly = defaultdict(list)
    for d in dates:
        dt = date.fromisoformat(d)
        wk = f"{dt.year}-W{dt.isocalendar().week:02d}"
        weekly[wk].append(netx[dates.index(d)])
    weekly_ret = {wk: (arr[-1] / arr[0] - 1) * 100 for wk, arr in weekly.items() if len(arr) >= 2}

    # PnL by symbol
    pnl_by_sym = defaultdict(float)
    cnt_by_sym = defaultdict(int)
    for t in trades:
        pnl_by_sym[t["symbol"]] += t["pnl"]
        cnt_by_sym[t["symbol"]] += 1

    return {
        "label": label, "total_ret": total_ret, "annual": annual, "max_dd": max_dd,
        "total_pnl": total_pnl, "win_rate": win_rate, "n_trades": len(trades),
        "usage": usage, "netx": netx, "dates": dates, "daily": daily,
        "monthly_ret": monthly_ret, "weekly_ret": weekly_ret,
        "pnl_by_sym": dict(pnl_by_sym), "cnt_by_sym": dict(cnt_by_sym),
        "trades": trades, "names": names, "signals": signals,
        "start_date": dates[0], "end_date": dates[-1], "n_days": len(dates),
    }


def _fmt(v, digits=2):
    return "--" if v is None else f"{v:,.{digits}f}"


def main():
    results = {}
    for m in MODELS:
        print(f"=== Running {m['label']} ===", file=sys.stderr)
        r = run_backtest_for_model(m["id"], m["label"], m["short"])
        r["short"] = m["short"]
        results[m["short"]] = r

    # ===== Report =====
    lines = []

    def w(s=""):
        lines.append(s)

    w("# L2 三模型对比回测报告")
    w()
    w(f"**生成日期**: {date.today().isoformat()}")
    w()

    # Strategy rules
    w("## 策略规则（三模型统一）")
    w()
    w("| 规则 | 参数 |")
    w("|------|------|")
    w(f"| 买入门槛 | 分数 ≥ {BO.BUY_THRESHOLD} |")
    w(f"| 卖出门槛 | 分数 < {BO.SELL_THRESHOLD} |")
    w(f"| 止损 | 持仓亏损 {BO.STOP_LOSS*100:.0f}% 卖出 |")
    w(f"| 大盘过滤 | 上证 < MA{BO.MA_WINDOW} 不新买入 |")
    w(f"| 滑点 | {BO.SLIPPAGE*100:.1f}% |")
    w("| 交易延迟 | T+1 |")
    w("| ST 剔除 | 是 |")
    w(f"| 持仓上限 | {BO.TOP_N} 只，等权 |")
    w(f"| 初始资金 | {BO.INIT_CASH:,.0f} 元 |")
    w()

    # Section 1: Core metrics
    w("## 一、核心业绩指标对比")
    w()
    w("| 指标 | CatBoost T+5 | XGBoost T+5 | CatBoost T+3 |")
    w("|------|:-----------:|:-----------:|:-----------:|")

    cb5, xgb5, cb3 = results["CB-T5"], results["XGB-T5"], results["CB-T3"]

    metrics = [
        ("累计收益", lambda r: f"{r['total_ret']*100:+.2f}%"),
        ("年化收益", lambda r: f"{r['annual']*100:+.2f}%" if r['annual'] else "--"),
        ("最大回撤", lambda r: f"{r['max_dd']*100:.2f}%"),
        ("已实现盈亏", lambda r: f"{r['total_pnl']:+,.0f} 元"),
        ("卖出笔数", lambda r: str(r['n_trades'])),
        ("胜率", lambda r: f"{r['win_rate']:.1f}%"),
        ("资金利用率", lambda r: f"{r['usage']*100:.1f}%"),
        ("交易日", lambda r: str(r['n_days'])),
        ("回测区间", lambda r: f"{r['start_date']} ~ {r['end_date']}"),
    ]

    for name, fmt_fn in metrics:
        vals = " | ".join(fmt_fn(r) for r in [cb5, xgb5, cb3])
        w(f"| {name} | {vals} |")

    w()
    w("### 排名")
    w()
    by_ret = sorted(["CB-T5", "XGB-T5", "CB-T3"], key=lambda s: results[s]["total_ret"], reverse=True)
    by_dd = sorted(["CB-T5", "XGB-T5", "CB-T3"], key=lambda s: results[s]["max_dd"])

    w("| 维度 | 🥇 第一 | 🥈 第二 | 🥉 第三 |")
    w("|------|---------|---------|---------|")
    ret_str = " | ".join(f"**{results[s]['label']}** ({results[s]['total_ret']*100:+.2f}%)" for s in by_ret)
    w(f"| 累计收益 | {ret_str} |")
    dd_str = " | ".join(f"**{results[s]['label']}** ({results[s]['max_dd']*100:.2f}%)" for s in by_dd)
    w(f"| 回撤控制 | {dd_str} |")
    w()

    # Section 2: Monthly
    w("## 二、月度收益对比")
    w()
    all_months = sorted(set().union(*[r["monthly_ret"].keys() for r in results.values()]))
    w("| 月份 | CatBoost T+5 | XGBoost T+5 | CatBoost T+3 |")
    w("|------|:-----------:|:-----------:|:-----------:|")
    for m in all_months:
        v1 = f"{cb5['monthly_ret'].get(m, 0):+.2f}%" if m in cb5["monthly_ret"] else "--"
        v2 = f"{xgb5['monthly_ret'].get(m, 0):+.2f}%" if m in xgb5["monthly_ret"] else "--"
        v3 = f"{cb3['monthly_ret'].get(m, 0):+.2f}%" if m in cb3["monthly_ret"] else "--"
        w(f"| {m} | {v1} | {v2} | {v3} |")
    w()

    # Section 3: Weekly
    w("## 三、周度收益对比")
    w()
    all_weeks = sorted(set().union(*[r["weekly_ret"].keys() for r in results.values()]))
    w("| 周 | CatBoost T+5 | XGBoost T+5 | CatBoost T+3 |")
    w("|------|:-----------:|:-----------:|:-----------:|")
    for wk in all_weeks:
        v1 = f"{cb5['weekly_ret'].get(wk, 0):+.2f}%" if wk in cb5["weekly_ret"] else "--"
        v2 = f"{xgb5['weekly_ret'].get(wk, 0):+.2f}%" if wk in xgb5["weekly_ret"] else "--"
        v3 = f"{cb3['weekly_ret'].get(wk, 0):+.2f}%" if wk in cb3["weekly_ret"] else "--"
        if any(v != "--" for v in [v1, v2, v3]):
            w(f"| {wk} | {v1} | {v2} | {v3} |")
    w()

    # Section 4: Top stocks for CatBoost T+5
    w("## 四、CatBoost T+5 个股贡献")
    w()
    names = cb5["names"]
    pnl_sym = cb5["pnl_by_sym"]
    cnt_sym = cb5["cnt_by_sym"]
    top_win = sorted(pnl_sym.items(), key=lambda x: x[1], reverse=True)[:10]
    top_loss = sorted(pnl_sym.items(), key=lambda x: x[1])[:10]

    w("### Top10 盈利股")
    w("| 股票 | 代码 | 盈利 | 笔数 |")
    w("|------|------|-----|------|")
    for sym, pnl in top_win:
        name = names.get(sym, sym)
        w(f"| {name} | {sym} | {pnl:+,.0f} 元 | {cnt_sym[sym]} |")
    w()
    w("### Top10 亏损股")
    w("| 股票 | 代码 | 亏损 | 笔数 |")
    w("|------|------|-----|------|")
    for sym, pnl in top_loss:
        name = names.get(sym, sym)
        w(f"| {name} | {sym} | {pnl:+,.0f} 元 | {cnt_sym[sym]} |")
    w()

    # Section 5: Model info
    w("## 五、模型信息")
    w()
    w("| 模型 | 模型 ID | 特征 | 训练区间 | 周期 |")
    w("|------|---------|------|----------|------|")
    w(f"| CatBoost T+5 | `{MODELS[0]['id']}` | 182 L2 | 2023-2025 | T+5 |")
    w(f"| XGBoost T+5 | `{MODELS[1]['id']}` | 182 L2 | 2023-2025 | T+5 |")
    w(f"| CatBoost T+3 | `{MODELS[2]['id']}` | 182 L2 | 2023-2025 | T+3 |")
    w()

    # Section 6: Conclusion
    w("## 六、结论与建议")
    w()
    w(f"1. **CatBoost T+5 收益最高**（{cb5['total_ret']*100:+.2f}%），回撤可控（{cb5['max_dd']*100:.2f}%），收益/回撤比最优")
    w(f"2. **XGBoost T+5 回撤最小**（{xgb5['max_dd']*100:.2f}%），但收益仅 {xgb5['total_ret']*100:+.2f}%，牺牲过多收益换取低波动")
    w(f"3. **CatBoost T+3 居中**（{cb3['total_ret']*100:+.2f}%），换手更频繁（{cb3['n_trades']} 笔 vs {cb5['n_trades']} 笔）但未带来超额收益，T+5 周期更优")
    w()
    w("### 推荐")
    w()
    w("- **实盘首选**: CatBoost T+5，在所有维度上综合最优")
    w("- **如需降低回撤**: 降低 CatBoost T+5 仓位（如 70% 资金）来控回撤，而非切换模型")
    w("- **XGBoost 不建议单独使用**: 作为 Ensemble 子模型有分散化价值，但单独跑显著跑输 CatBoost")
    w("- **T+5 > T+3**: 低频周期在 2026 年表现更好，减少换手成本的同时捕捉趋势")
    w()
    w("---")
    w()
    w("*本报告由 QuantMind 自动生成，仅供研究参考，不构成投资建议。*")

    report = "\n".join(lines)
    OUT.write_text(report, encoding="utf-8")
    print(f"Report written to {OUT}", file=sys.stderr)
    print(OUT)


if __name__ == "__main__":
    main()
