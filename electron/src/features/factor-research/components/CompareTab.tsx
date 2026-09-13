/** 因子研究 —— 多因子对比页签：KPI 并排 + 净值/IC 叠加 + 相关性热力图 */
import React, { useEffect, useMemo, useState } from 'react';
import { X } from 'lucide-react';
import { EChartsChart } from '../../../components/common/EChartsChart';
import { getCompare } from '../services/factorResearchService';
import type { CompareResponse } from '../types/factorResearch';
import { Card, FACTOR_COLORS, IcChart, NavChart, fmtNum, fmtPct } from './common';

interface Props {
  codes: string[];
  onRemove: (code: string) => void;
  nameOf: (code: string) => string;
}

export const CompareTab: React.FC<Props> = ({ codes, onRemove, nameOf }) => {
  const [data, setData] = useState<CompareResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!codes.length) {
      setData(null);
      return;
    }
    let alive = true;
    setLoading(true);
    setError(null);
    getCompare(codes)
      .then((d) => { if (alive) setData(d); })
      .catch((e: unknown) => { if (alive) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [codes.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  const corrMatrix = useMemo(() => {
    if (!data?.corr || !codes.length) return null;
    const names = codes.filter((c) => data.factors.some((f) => f.code === c));
    const idx = new Map(names.map((c, i) => [c, i]));
    const m: number[][] = names.map(() => names.map(() => NaN));
    for (const p of data.corr) {
      const i = idx.get(p.factor_a);
      const j = idx.get(p.factor_b);
      if (i === undefined || j === undefined) continue;
      m[i][j] = p.corr;
      m[j][i] = p.corr;
    }
    return { names, m };
  }, [data, codes]);

  const heatOption = useMemo(() => {
    if (!corrMatrix || corrMatrix.names.length < 2) return null;
    const { names, m } = corrMatrix;
    return {
      grid: { left: 76, right: 16, top: 10, bottom: 56 },
      tooltip: { formatter: (p: any) => `${names[p.data[1]]} × ${names[p.data[0]]}<br/>相关 ${p.data[2]}` },
      xAxis: { type: 'category', data: names, axisLabel: { fontSize: 9, color: '#94a3b8', rotate: 40 }, axisTick: { show: false } },
      yAxis: { type: 'category', data: names, axisLabel: { fontSize: 9, color: '#94a3b8' }, axisTick: { show: false } },
      visualMap: { show: false, min: -1, max: 1, inRange: { color: ['#047857', '#e2e8f0', '#be123c'] } },
      series: [{
        type: 'heatmap',
        data: m.flatMap((row, i) => row.map((v, j) => [i, j, v])),
        label: { show: names.length <= 8, fontSize: 9, color: '#0f172a', formatter: (p: any) => (p.data[2] === 1 ? '' : String(p.data[2])) },
        itemStyle: { borderColor: '#fff', borderWidth: 1 },
      }],
    };
  }, [corrMatrix]);

  if (!codes.length) {
    return <div className="flex-1 flex items-center justify-center text-xs text-slate-400">先从「排行榜」勾选 ≥2 个因子点击「带入对比」</div>;
  }

  return (
    <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar flex flex-col gap-2">
      {/* 已选因子 chips */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {codes.map((c, i) => (
          <span
            key={c}
            className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-bold"
            style={{ borderColor: `${FACTOR_COLORS[i % FACTOR_COLORS.length]}55`, color: FACTOR_COLORS[i % FACTOR_COLORS.length], backgroundColor: `${FACTOR_COLORS[i % FACTOR_COLORS.length]}10` }}
          >
            {nameOf(c)}
            <button onClick={() => onRemove(c)} className="hover:opacity-70"><X className="w-3 h-3" /></button>
          </span>
        ))}
        <span className="text-[10px] text-slate-400">最多 12 个（超出自动截断）</span>
      </div>

      {error ? (
        <div className="py-10 text-center text-xs text-rose-500">{error}</div>
      ) : loading && !data ? (
        <div className="flex-1 rounded-2xl bg-slate-50 animate-pulse" />
      ) : data ? (
        <>
          {/* KPI 对照表 */}
          <Card title="KPI 对照">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-slate-400 font-bold">
                  <th className="text-left py-1">因子</th>
                  <th className="text-right py-1">年化</th>
                  <th className="text-right py-1">夏普</th>
                  <th className="text-right py-1">最大回撤</th>
                  <th className="text-right py-1">月胜率</th>
                  <th className="text-right py-1">IC 均值</th>
                  <th className="text-right py-1">ICIR</th>
                </tr>
              </thead>
              <tbody>
                {data.factors.map((f, i) => (
                  <tr key={f.code} className="border-t border-slate-100">
                    <td className="py-1">
                      <span className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ backgroundColor: FACTOR_COLORS[i % FACTOR_COLORS.length] }} />
                      <span className="font-bold text-slate-700">{f.name_cn}</span>
                      <span className="ml-1.5 font-mono text-[10px] text-slate-400">{f.code}</span>
                    </td>
                    <td className={`py-1 text-right font-mono ${(f.kpi.annual_return || 0) >= 0 ? 'text-rose-600' : 'text-emerald-600'}`}>{fmtPct(f.kpi.annual_return)}</td>
                    <td className="py-1 text-right font-mono text-slate-600">{fmtNum(f.kpi.sharpe)}</td>
                    <td className="py-1 text-right font-mono text-slate-500">{fmtPct(f.kpi.max_drawdown)}</td>
                    <td className="py-1 text-right font-mono text-slate-500">{fmtPct(f.kpi.win_rate)}</td>
                    <td className={`py-1 text-right font-mono ${(f.kpi.ic_mean || 0) >= 0 ? 'text-rose-600' : 'text-emerald-600'}`}>{fmtNum(f.kpi.ic_mean, 3)}</td>
                    <td className="py-1 text-right font-mono text-slate-500">{fmtNum(f.kpi.ic_ir)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          {/* 净值叠加 + 相关热力图 */}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-2">
            <Card title="净值对比" extra={<span className="text-[10px] text-slate-400">虚线=中证500</span>}>
              <NavChart
                series={[
                  ...data.factors.map((f, i) => ({ name: f.name_cn, data: f.nav, color: FACTOR_COLORS[i % FACTOR_COLORS.length] })),
                  { name: '中证500', data: data.benchmark, color: '#94a3b8' },
                ]}
                height={230}
              />
            </Card>
            <Card title="因子相关性（月频秩相关均值）" extra={<span className="text-[10px] text-slate-400">红=正相关 绿=负相关</span>}>
              {heatOption ? <div style={{ height: 230 }}><EChartsChart option={heatOption} /></div> : <div className="h-[230px] flex items-center justify-center text-xs text-slate-400">需要 ≥2 个因子</div>}
            </Card>
          </div>

          {/* IC 并排 */}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-2">
            {data.factors.map((f, i) => (
              <Card key={f.code} title={`${f.name_cn} · 月频 IC`} extra={<span className="text-[10px] text-slate-400">{f.code}</span>}>
                <IcChart points={f.ic} height={140} />
              </Card>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
};
