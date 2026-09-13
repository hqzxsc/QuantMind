/** 因子研究 —— 共享小组件：KPI 磁贴 / 净值图 / IC 图 / 格式化 */
import React from 'react';
import { EChartsChart } from '../../../components/common/EChartsChart';
import type { FactorKpi, SeriesPoint } from '../types/factorResearch';

/** 多因子对比配色（首个为站内主色，后续为高区分度序列） */
export const FACTOR_COLORS = ['#2563eb', '#e11d48', '#059669', '#d97706', '#7c3aed', '#0891b2', '#be185d', '#4d7c0f', '#b45309', '#1d4ed8', '#9f1239', '#065f46'];

export const fmtPct = (v?: number | null, digits = 1): string =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(digits)}%`;

export const fmtNum = (v?: number | null, digits = 2): string =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : v.toFixed(digits);

interface KpiTilesProps {
  kpi: FactorKpi;
  compact?: boolean;
}

/** KPI 磁贴：年化 / 夏普 / 最大回撤 / 月胜率 / IC 均值 / ICIR */
export const KpiTiles: React.FC<KpiTilesProps> = ({ kpi, compact }) => {
  const items: Array<{ label: string; value: string; tone?: 'pos' | 'neg' }> = [
    { label: '年化收益', value: fmtPct(kpi.annual_return), tone: (kpi.annual_return || 0) >= 0 ? 'pos' : 'neg' },
    { label: '夏普', value: fmtNum(kpi.sharpe), tone: (kpi.sharpe || 0) >= 0 ? 'pos' : 'neg' },
    { label: '最大回撤', value: fmtPct(kpi.max_drawdown) },
    { label: '月胜率', value: fmtPct(kpi.win_rate) },
    { label: 'IC 均值', value: fmtNum(kpi.ic_mean, 3), tone: (kpi.ic_mean || 0) >= 0 ? 'pos' : 'neg' },
    { label: 'ICIR', value: fmtNum(kpi.ic_ir, 2) },
  ];
  return (
    <div className={`grid ${compact ? 'grid-cols-3' : 'grid-cols-3 lg:grid-cols-6'} gap-2`}>
      {items.map((it) => (
        <div key={it.label} className="rounded-xl border border-slate-200/80 bg-white px-3 py-2">
          <div className="text-[10px] font-bold text-slate-400">{it.label}</div>
          <div
            className={`text-sm font-extrabold font-mono ${
              it.tone === 'pos' ? 'text-rose-600' : it.tone === 'neg' ? 'text-emerald-600' : 'text-slate-700'
            }`}
          >
            {it.value}
          </div>
        </div>
      ))}
    </div>
  );
};

interface NavChartProps {
  series: Array<{ name: string; data: SeriesPoint[]; color?: string }>;
  height?: number;
}

/** 净值曲线（多序列 + 可选基准虚线） */
export const NavChart: React.FC<NavChartProps> = ({ series, height = 180 }) => {
  const dates = series[0]?.data.map((p) => p.date) || [];
  const option = {
    grid: { left: 44, right: 12, top: 24, bottom: 22 },
    tooltip: { trigger: 'axis' },
    legend: series.length > 1 ? { top: 0, textStyle: { fontSize: 10, color: '#64748b' } } : undefined,
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 9, color: '#94a3b8' }, axisTick: { show: false } },
    yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 9, color: '#94a3b8' }, splitLine: { lineStyle: { color: '#f1f5f9' } } },
    series: series.map((s, i) => ({
      name: s.name,
      type: 'line',
      data: s.data.map((p) => p.value),
      showSymbol: false,
      lineStyle: { width: i === 0 ? 1.8 : 1.4, color: s.color || FACTOR_COLORS[i % FACTOR_COLORS.length] },
      itemStyle: { color: s.color || FACTOR_COLORS[i % FACTOR_COLORS.length] },
      emphasis: { focus: 'series' },
    })),
  };
  return (
    <div style={{ height }}>
      <EChartsChart option={option} />
    </div>
  );
};

interface IcChartProps {
  points: SeriesPoint[];
  height?: number;
}

/** 月频 IC：柱（红正/绿负）+ 12 期滚动均值线 */
export const IcChart: React.FC<IcChartProps> = ({ points, height = 160 }) => {
  const dates = points.map((p) => p.date);
  const vals = points.map((p) => p.value);
  const roll: number[] = vals.map((_, i) => {
    const w = vals.slice(Math.max(0, i - 11), i + 1);
    return w.reduce((a, b) => a + b, 0) / w.length;
  });
  const option = {
    grid: { left: 44, right: 12, top: 16, bottom: 22 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 9, color: '#94a3b8' }, axisTick: { show: false } },
    yAxis: { type: 'value', axisLabel: { fontSize: 9, color: '#94a3b8' }, splitLine: { lineStyle: { color: '#f1f5f9' } } },
    series: [
      {
        name: 'IC',
        type: 'bar',
        data: vals.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? 'rgba(225,29,72,0.65)' : 'rgba(5,150,105,0.65)' } })),
        barMaxWidth: 8,
      },
      { name: '12期均值', type: 'line', data: roll, showSymbol: false, lineStyle: { width: 1.5, color: '#2563eb' } },
    ],
  };
  return (
    <div style={{ height }}>
      <EChartsChart option={option} />
    </div>
  );
};

/** 卡片壳 */
export const Card: React.FC<{ title: string; extra?: React.ReactNode; children: React.ReactNode; className?: string }> = ({
  title,
  extra,
  children,
  className,
}) => (
  <div className={`bg-white rounded-2xl border border-slate-200/80 shadow-sm p-3 flex flex-col min-h-0 ${className || ''}`}>
    <div className="flex items-baseline justify-between mb-1.5">
      <h4 className="text-xs font-extrabold text-slate-800">{title}</h4>
      {extra}
    </div>
    <div className="flex-1 min-h-0">{children}</div>
  </div>
);
