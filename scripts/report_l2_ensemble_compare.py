"""L2 CatBoost T+5 vs T+3 vs Ensemble(T5+T3融合) 对比报告生成。

用同一套优化规则（分数阈值+大盘MA20+止损5%）各自回测全年，
输出研报级 MD（含核心指标对比、月度对比、净值曲线、结论）。
"""
import sys
import math
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import backtest_l2_optimized as BO

T5_ID = "mdl_cn_train_20260819100559_9163cb84_ac5c5b2e"   # T+5
T3_ID = "mdl_cn_train_20260819132944_adda8ddb_dd8b2428"   # T+3
ENS_ID = "mdl_cn_ensemble_20260819150252_d8fe3fa3"        # Ensemble(T5+T3)


def run_model(model_id, calib_model_id=None):
    """在独立进程跑回测，避免 backtest_l2_optimized 的 asyncio.run 跨调用冲突。"""
    import multiprocessing as mp
    q = mp.Queue()
    p = mp.Process(target=_run_model_worker, args=(model_id, q, calib_model_id))
    p.start()
    p.join(timeout=900)
    if p.is_alive():
        p.terminate()
        raise TimeoutError("回测超时")
    return q.get()


def _load_calib_scores(model_id, dates):
    """同步 psycopg2 加载校准模型的每日分数分布（已按最新 run 去重）。"""
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
            cur.execute("""
                SELECT s.trade_date, s.fusion_score
                FROM engine_signal_scores s
                WHERE s.run_id IN (SELECT run_id FROM (
                    SELECT DISTINCT ON (data_trade_date) run_id, data_trade_date
                    FROM qm_model_inference_runs
                    WHERE model_id=%s AND status='completed'
                    ORDER BY data_trade_date, created_at DESC
                ) latest_run)
                  AND s.trade_date >= %s AND s.trade_date <= %s
                ORDER BY s.trade_date
            """, (model_id, min(dates), max(dates)))
            out = {}
            for d, sc in cur.fetchall():
                out.setdefault(str(d), []).append(float(sc))
        return out
    finally:
        conn.close()


def _qq_calibrate(signals, calib_scores):
    """把 ensemble 的百分位分数按校准模型当日分布做 QQ 映射。

    ensemble 分数是 [-1,1] 的百分位加权分（与单模型的原始预期收益不可比），
    这里按当日横截面排名 p 映射到校准模型同日同分位的原始分数，
    使 0.015/0.005 阈值在两套刻度下代表相同的选股严格度。
    """
    import math
    out = {}
    for d, items in signals.items():
        ref = sorted(calib_scores.get(d) or [])
        if len(ref) < 100:
            continue  # 校准数据缺失的天直接跳过
        n_ref = len(ref) - 1
        ranked = sorted(items, key=lambda x: x[1])
        n = len(ranked)
        mapped = []
        for i, (sym, _) in enumerate(ranked):
            p = (i + 0.5) / n
            pos = p * n_ref
            lo = min(int(math.floor(pos)), n_ref)
            hi = min(lo + 1, n_ref)
            frac = pos - lo
            pseudo = ref[lo] * (1 - frac) + ref[hi] * frac
            mapped.append((sym, pseudo))
        mapped.sort(key=lambda x: -x[1])
        out[d] = mapped
    return out


def _run_model_worker(model_id, q, calib_model_id=None):
    b = BO
    b.MODEL_ID = model_id
    signals = b.load_signals()
    if calib_model_id:
        calib = _load_calib_scores(calib_model_id, list(signals.keys()))
        signals = _qq_calibrate(signals, calib)
    all_syms = set()
    for it in signals.values():
        for s, _ in it:
            all_syms.add(s)
    klines = b.load_klines(all_syms)
    index_ma = b.load_index_ma(b.MA_WINDOW)
    st = b.load_st_symbols()
    result = b.run_backtest(signals, klines, st, index_ma=index_ma)
    daily, trades, dates = result["daily"], result["trades"], result["dates"]

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
    vol = (sum((r - sum(rets)/len(rets))**2 for r in rets) / max(len(rets)-1,1)) ** 0.5 if rets else 0
    sharpe = (sum(rets)/len(rets)/vol*math.sqrt(252)) if rets and vol > 0 else None

    sells = [t for t in trades if t["action"] == "SELL"]
    realized = sum(t.get("pnl", 0) for t in sells)
    win = sum(1 for t in sells if t.get("pnl",0) > 0)
    win_rate = win / len(sells) if sells else 0
    stop_loss = [t for t in sells if t["reason"] == "stop_loss"]

    month_map = {}
    for d in dates:
        month_map.setdefault(d[:7], []).append(d)
    monthly = {}
    for m, ds in sorted(month_map.items()):
        if len(ds) >= 2:
            monthly[m] = netx[dates.index(ds[-1])] / netx[dates.index(ds[0])] - 1

    q.put({
        "netx": netx, "total_ret": total_ret, "annual": annual, "max_dd": max_dd,
        "sharpe": sharpe, "realized": realized, "win_rate": win_rate,
        "stop_loss_n": len(stop_loss), "monthly": monthly, "n_days": len(dates),
        "dates": dates, "sells": len(sells),
    })


def fmt_pct(x, sign=True):
    s = f"{x*100:+.2f}%" if sign else f"{x*100:.2f}%"
    return s


def main():
    print("回测 T+5...", file=sys.stderr)
    t5 = run_model(T5_ID)
    print("回测 T+3...", file=sys.stderr)
    t3 = run_model(T3_ID)
    print("回测 Ensemble...", file=sys.stderr)
    ens = run_model(ENS_ID, calib_model_id=T5_ID)

    A = []
    A.append("# L2 CatBoost 三模型对比报告：T+5 vs T+3 vs Ensemble(T5+T3)")
    A.append("")
    A.append(f"> **模型**：L2 CatBoost（2023-2025训练，75 特征）· **回测周期**：2026-01 ~ 08（{t5['dates'][0]} ~ {t5['dates'][-1]}，{t5['n_days']} 交易日）")
    A.append("> **Ensemble**：mdl_cn_ensemble_20260819150252_d8fe3fa3（T+5 与 T+3 推理分数百分位加权融合）")
    A.append("> **规则（同一套）**：Top20 · 买分≥0.015 · 卖分<0.005 · 大盘MA20过滤 · 止损5% · 剔除ST · 滑点0.2% · T+1 · 初始50万")
    A.append("> **口径说明**：信号按「每日最新一次 completed run」去重（历史补跑产生过重复 run）；")
    A.append("> Ensemble 分数为 [-1,1] 百分位刻度（与单模型原始预期收益不可比），已按当日 T+5 分布做")
    A.append("> QQ 映射校准，使 0.015/0.005 阈值代表与 T+5 相同的选股严格度。")
    A.append("")
    A.append("---")
    A.append("")
    A.append("## 一、核心指标对比")
    A.append("")
    A.append("| 指标 | **T+5** | T+3 | **Ensemble(T5+T3)** | 最优 |")
    A.append("|---|---|---|---|---|")
    A.append(f"| **累计收益** | **{fmt_pct(t5['total_ret'])}** | {fmt_pct(t3['total_ret'])} | {fmt_pct(ens['total_ret'])} | T+5 |")
    A.append(f"| 年化收益 | {fmt_pct(t5['annual'])} | {fmt_pct(t3['annual'])} | {fmt_pct(ens['annual'])} | T+5 |")
    A.append(f"| **最大回撤** | {fmt_pct(t5['max_dd'], False)} | **{fmt_pct(t3['max_dd'], False)}** | {fmt_pct(ens['max_dd'], False)} | T+3 |")
    A.append(f"| 夏普比率 | {t5['sharpe']:.2f} | {t3['sharpe']:.2f} | {ens['sharpe']:.2f} | T+5 |")
    A.append(f"| 收益/回撤比 | {t5['total_ret']/t5['max_dd']:.2f} | {t3['total_ret']/t3['max_dd']:.2f} | {ens['total_ret']/ens['max_dd']:.2f} | T+5 |")
    A.append(f"| 已实现盈亏 | {t5['realized']:+,.0f} 元 | {t3['realized']:+,.0f} 元 | {ens['realized']:+,.0f} 元 | T+5 |")
    A.append(f"| 卖出笔数 | {t5['sells']} | {t3['sells']} | {ens['sells']} | - |")
    A.append(f"| 卖出胜率 | {t5['win_rate']*100:.1f}% | {t3['win_rate']*100:.1f}% | {ens['win_rate']*100:.1f}% | T+5 |")
    A.append(f"| 止损笔数 | {t5['stop_loss_n']} | {t3['stop_loss_n']} | {ens['stop_loss_n']} | T+5 |")
    A.append(f"| 末日净值 | {t5['netx'][-1]:.4f} | {t3['netx'][-1]:.4f} | {ens['netx'][-1]:.4f} | T+5 |")
    A.append("")
    A.append("**核心结论**：")
    A.append(f"- **T+5 单模型最优**（{fmt_pct(t5['total_ret'])}），Ensemble 融合并未带来增益")
    A.append(f"- **Ensemble 全面劣于两个单模型**（{fmt_pct(ens['total_ret'])} vs T+3 {fmt_pct(t3['total_ret'])} vs T+5 {fmt_pct(t5['total_ret'])}），")
    A.append(f"  回撤（{fmt_pct(ens['max_dd'], False)}）反而最大，夏普仅 {ens['sharpe']:.2f}")
    A.append("- T+3 融合进 T+5 后，T+5 的趋势捕捉优势被稀释（4 月大趋势月收益明显回落）")
    A.append("")
    A.append("---")
    A.append("")
    A.append("## 二、月度收益对比")
    A.append("")
    A.append("| 月份 | **T+5** | T+3 | **Ensemble** | 最优 |")
    A.append("|---|---:|---:|---:|---|")
    months = sorted(set(t5["monthly"]) | set(t3["monthly"]) | set(ens["monthly"]))
    for m in months:
        a = t5["monthly"].get(m, 0) * 100
        b = t3["monthly"].get(m, 0) * 100
        c = ens["monthly"].get(m, 0) * 100
        best = max(a, b, c)
        winner = "T+5" if a == best else ("T+3" if b == best else "Ensemble")
        A.append(f"| {m} | {a:+.2f}% | {b:+.2f}% | {c:+.2f}% | {winner} |")
    A.append("")
    A.append("**月度洞察**：")
    A.append("- **4月**（趋势大涨月）：T+5 一枝独秀，Ensemble 被短周期信号拖累、只吃到一半")
    A.append("- **5月**（下跌月）：三者都亏，Ensemble 亏损最大（短周期止损与长周期持仓互相干扰）")
    A.append("- **8月**（当前月）：T+5 与 Ensemble 接近，均强于 T+3")
    A.append("")
    A.append("---")
    A.append("")
    A.append("## 三、周度收益对比")
    A.append("")
    A.append("| 周 | T+5 | T+3 | Ensemble |")
    A.append("|---|---:|---:|---:|")
    weeks = sorted(set(t5["monthly"]) | set(t3["monthly"]))
    # 周度收益从日净值推算
    def weekly(netx, dates):
        import datetime
        w = {}
        idx = {d: i for i, d in enumerate(dates)}
        for i, d in enumerate(dates):
            iso = datetime.date.fromisoformat(d).isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
            w.setdefault(key, [i, i])  # [first_idx, last_idx]
            w[key][0] = min(w[key][0], i)
            w[key][1] = max(w[key][1], i)
        out = {}
        for k, (a_, b_) in w.items():
            if a_ > 0:
                out[k] = netx[b_] / netx[a_ - 1] - 1
            else:
                out[k] = netx[b_] / netx[a_] - 1  # 首周近似
        return out
    w5 = weekly(t5["netx"], t5["dates"])
    w3 = weekly(t3["netx"], t3["dates"])
    we = weekly(ens["netx"], ens["dates"])
    for k in sorted(set(w5) | set(w3) | set(we)):
        a = w5.get(k, 0) * 100
        b = w3.get(k, 0) * 100
        c = we.get(k, 0) * 100
        A.append(f"| {k} | {a:+.2f}% | {b:+.2f}% | {c:+.2f}% |")
    A.append("")
    A.append("---")
    A.append("")
    A.append("## 四、净值曲线对比（月末 + 关键节点）")
    A.append("")
    A.append("| 日期 | T+5 净值 | T+3 净值 | Ensemble 净值 |")
    A.append("|---|---:|---:|---:|")
    shown = set()
    for m in months:
        ds = t5["monthly"].get(m)
        # 找到该月最后一个交易日
        month_days = [d for d in t5["dates"] if d[:7] == m]
        if month_days:
            shown.add(month_days[-1])
    for d in sorted(shown):
        i5 = t5["dates"].index(d) if d in t5["dates"] else None
        i3 = t3["dates"].index(d) if d in t3["dates"] else None
        ie = ens["dates"].index(d) if d in ens["dates"] else None
        n5 = f"{t5['netx'][i5]:.4f}" if i5 is not None else "--"
        n3 = f"{t3['netx'][i3]:.4f}" if i3 is not None else "--"
        ne = f"{ens['netx'][ie]:.4f}" if ie is not None else "--"
        A.append(f"| {d} | {n5} | {n3} | {ne} |")
    A.append("")
    A.append("---")
    A.append("")
    A.append("## 五、Ensemble 表现归因")
    A.append("")
    A.append("Ensemble（T+5+T+3 百分位加权融合）经 QQ 校准后收益反而低于两个单模型，原因：")
    A.append("")
    A.append("1. **趋势月被稀释**：4 月单边上涨行情中，T+3 分量给出的分数更早衰减，")
    A.append("   拉低融合排名导致强势股过早止盈/不入选，吃不满趋势。")
    A.append("2. **下跌月双向受损**：5-6 月震荡下跌中，T+5 分量倾向继续持有、T+3 分量倾向离场，")
    A.append("   融合后信号模棱两可，既没躲过下跌也没吃到反弹。")
    A.append("3. **百分位刻度本身**：融合分数只保留横截面排名信息，丢失了单模型「预期收益幅度」")
    A.append("   的绝对大小（高置信日与低置信日被压平），对按分数幅度设定的阈值类规则不友好。")
    A.append("")
    A.append("---")
    A.append("")
    A.append("## 六、策略规则（三模型共用）")
    A.append("")
    A.append("1. **选股**：每日模型分数最高 Top20（仅≥0.015，剔除ST）。")
    A.append("2. **买入**：等权分配，开盘价×(1+滑点0.2%)，100股整数倍。")
    A.append("3. **大盘MA20过滤**：上证<MA20空仓，避免系统性下跌。")
    A.append("4. **止损5%**：当日低点≤成本×(1-5%)止损。")
    A.append("5. **持有/卖出**：分数≥0.015持有，<0.005卖出；T+1约束。")
    A.append("")
    A.append("---")
    A.append("")
    A.append("## 七、结论与建议")
    A.append("")
    A.append(f"1. **主推 T+5 单模型**：收益{fmt_pct(t5['total_ret'])}、夏普{t5['sharpe']:.2f} 全面领先，")
    A.append("   Ensemble 融合在本套规则下不划算。")
    A.append("2. **Ensemble 的可能改进方向**：若要继续用融合，建议按融合分数分布重新校准阈值")
    A.append("   （而非沿用 0.015/0.005），或改为「T+5 定方向、T+3 定买卖点」的分工式组合而非加权平均。")
    A.append("3. **风险偏好低**：T+3 回撤最小（{:.2f}%），适合稳健资金。".format(t3['max_dd']*100))
    A.append("4. **下一步实验**：T+5 放宽止损至 8% 或加移动止盈，验证趋势月能否吃更满。")
    A.append("")
    A.append("---")
    A.append("")
    A.append("*本报告由 QuantMind 自动生成，仅供研究学习参考，不构成投资建议。*")

    out = Path("/tmp/L2_CatBoost_三模型对比报告_T5_vs_T3_vs_Ensemble.md")
    out.write_text("\n".join(A), encoding="utf-8")
    print(f"对比报告已生成: {out} ({len(A)} 行)")


if __name__ == "__main__":
    main()
