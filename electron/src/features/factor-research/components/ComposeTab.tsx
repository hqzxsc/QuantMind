/** 因子研究 —— 多因子合成页签：自定义权重 + 阈值过滤 → 实时回测 */
import React, { useMemo, useState } from 'react';
import { Plus, Play, Trash2, Wand2 } from 'lucide-react';
import { postCompose } from '../services/factorResearchService';
import type { ComposeResponse, FactorMeta } from '../types/factorResearch';
import { Card, IcChart, KpiTiles, NavChart, FACTOR_COLORS } from './common';
import { EChartsChart } from '../../../components/common/EChartsChart';

interface Props {
  factors: FactorMeta[];
  codes: string[];
  onChangeCodes: (codes: string[]) => void;
}

interface WeightRow {
  code: string;
  weight: number;
}

export const ComposeTab: React.FC<Props> = ({ factors, codes, onChangeCodes }) => {
  // 权重覆盖表（默认 1）；因子集合本身由页面级 codes 驱动，避免双份状态漂移
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [topN, setTopN] = useState(30);
  const [threshold, setThreshold] = useState<number | ''>('');
  const [result, setResult] = useState<ComposeResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pick, setPick] = useState('');

  const rows: WeightRow[] = useMemo(
    () => codes.map((c) => ({ code: c, weight: weights[c] ?? 1 })),
    [codes, weights],
  );

  const nameOf = useMemo(() => {
    const m = new Map(factors.map((f) => [f.code, f.name_cn]));
    return (c: string) => m.get(c) || c;
  }, [factors]);

  const addable = useMemo(
    () => factors.filter((f) => f.available && !codes.includes(f.code)),
    [factors, codes],
  );

  const update = (code: string, value: number) => {
    setWeights({ ...weights, [code]: value });
  };

  const remove = (code: string) => {
    onChangeCodes(codes.filter((c) => c !== code));
  };

  const run = async () => {
    const weights: Record<string, number> = {};
    for (const r of rows) if (r.weight !== 0) weights[r.code] = r.weight;
    if (!Object.keys(weights).length) {
      setError('请至少添加一个因子并设置非零权重');
      return;
    }
    setRunning(true);
    setError(null);
    try {
      const res = await postCompose({ weights, top_n: topN, threshold: threshold === '' ? null : Number(threshold) });
      setResult(res);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const turnoverOption = useMemo(() => {
    if (!result?.turnover?.length) return null;
    return {
      grid: { left: 40, right: 10, top: 10, bottom: 20 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: result.turnover.map((p) => p.date), axisLabel: { fontSize: 9, color: '#94a3b8' }, axisTick: { show: false } },
      yAxis: { type: 'value', max: 1, axisLabel: { fontSize: 9, color: '#94a3b8' }, splitLine: { lineStyle: { color: '#f1f5f9' } } },
      series: [{ type: 'line', data: result.turnover.map((p) => p.value), showSymbol: false, areaStyle: { opacity: 0.15 }, lineStyle: { width: 1.5, color: '#d97706' }, itemStyle: { color: '#d97706' } }],
    };
  }, [result]);

  return (
    <div className="flex-1 min-h-0 flex gap-2">
      {/* 左：权重编辑器 */}
      <div className="w-[300px] shrink-0 flex flex-col bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden">
        <div className="px-3 py-2 border-b border-slate-100 flex items-center justify-between">
          <span className="text-xs font-extrabold text-slate-800">因子与权重</span>
          <button
            onClick={() => setWeights({})}
            className="flex items-center gap-1 text-[10px] font-bold text-blue-600 hover:text-blue-700"
            title="全部设为 1"
          >
            <Wand2 className="w-3 h-3" /> 等权
          </button>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-2 space-y-1.5">
          {rows.map((r, i) => (
            <div key={r.code} className="rounded-xl border border-slate-100 bg-slate-50/50 px-2 py-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-bold text-slate-700 truncate">
                  <span className="inline-block w-1.5 h-1.5 rounded-full mr-1" style={{ backgroundColor: FACTOR_COLORS[i % FACTOR_COLORS.length] }} />
                  {nameOf(r.code)}
                </span>
                <button onClick={() => remove(r.code)} className="text-slate-300 hover:text-rose-500">
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
              <div className="flex items-center gap-2 mt-1">
                <span className="font-mono text-[9px] text-slate-400">{r.code}</span>
                <input
                  type="range"
                  min={-3}
                  max={3}
                  step={0.5}
                  value={r.weight}
                  onChange={(e) => update(r.code, Number(e.target.value))}
                  className="flex-1"
                />
                <input
                  type="number"
                  step={0.5}
                  value={r.weight}
                  onChange={(e) => update(r.code, Number(e.target.value))}
                  className="w-14 rounded-md border border-slate-200 bg-white px-1 py-0.5 text-[11px] font-mono text-right"
                />
              </div>
            </div>
          ))}
          {rows.length === 0 && <div className="text-[11px] text-slate-400 px-2 py-4 text-center">从下方下拉添加因子，或去「排行榜」勾选带入</div>}
        </div>
        <div className="p-2 border-t border-slate-100 space-y-2">
          <div className="flex items-center gap-1.5">
            <Plus className="w-3.5 h-3.5 text-slate-400 shrink-0" />
            <select
              value={pick}
              onChange={(e) => {
                const c = e.target.value;
                setPick('');
                if (c && !codes.includes(c)) {
                  onChangeCodes([...codes, c]);
                }
              }}
              className="w-full rounded-lg border border-slate-200 bg-white px-2 py-1 text-[11px]"
            >
              <option value="">添加因子…</option>
              {addable.map((f) => (
                <option key={f.code} value={f.code}>{f.l1} / {f.l2} · {f.name_cn}</option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2 text-[11px]">
            <label className="text-slate-500 shrink-0">持仓数</label>
            <input type="number" min={1} max={200} value={topN} onChange={(e) => setTopN(Number(e.target.value))}
              className="w-16 rounded-md border border-slate-200 px-1.5 py-0.5 font-mono text-right" />
            <label className="text-slate-500 shrink-0 ml-1" title="合成打分的下限（z 刻度）：0 ≈ 只买高于截面均值的股票">阈值</label>
            <input
              type="number" step={0.25} placeholder="不过滤" value={threshold}
              onChange={(e) => setThreshold(e.target.value === '' ? '' : Number(e.target.value))}
              className="w-16 rounded-md border border-slate-200 px-1.5 py-0.5 font-mono text-right placeholder:text-slate-300"
            />
            <button
              onClick={run}
              disabled={running}
              className="ml-auto flex items-center gap-1 rounded-full bg-blue-600 text-white px-3 py-1 text-[11px] font-bold hover:bg-blue-700 disabled:opacity-50"
            >
              <Play className="w-3 h-3" /> {running ? '回测中…' : '运行回测'}
            </button>
          </div>
          {error && <div className="text-[10px] text-rose-500">{error}</div>}
        </div>
      </div>

      {/* 右：结果 */}
      <div className="flex-1 min-w-0 min-h-0 overflow-y-auto custom-scrollbar flex flex-col gap-2">
        {result ? (
          <>
            <KpiTiles kpi={result.kpi} />
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-2">
              <Card title="合成组合净值" extra={<span className="text-[10px] text-slate-400">虚线=中证500 · 双边成本 0.2%</span>}>
                <NavChart
                  series={[
                    { name: '合成组合', data: result.nav },
                    { name: '中证500', data: result.benchmark, color: '#94a3b8' },
                  ]}
                  height={200}
                />
              </Card>
              <Card title="换手率（月末）" extra={<span className="text-[10px] text-slate-400">新进持仓占比</span>}>
                {turnoverOption ? <div style={{ height: 200 }}><EChartsChart option={turnoverOption} /></div> : <div className="h-[200px] flex items-center justify-center text-xs text-slate-400">—</div>}
              </Card>
            </div>
            <Card title={`最新持仓（${result.holdings_date || '—'} · Top ${result.holdings.length}）`}
              extra={<span className="text-[10px] text-slate-400">合成打分降序 · 阈值 {result.threshold ?? '无'}</span>}>
              <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-x-4">
                {result.holdings.map((h, i) => (
                  <div key={h.symbol} className="flex items-center justify-between py-[3px] border-b border-slate-50">
                    <span className="text-[11px] text-slate-600 truncate">
                      <span className="font-mono text-[10px] text-slate-400 mr-1">{i + 1}</span>
                      {h.name || h.symbol}
                    </span>
                    <span className="text-[9px] text-slate-400 shrink-0 ml-1">{h.industry}</span>
                  </div>
                ))}
              </div>
            </Card>
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center gap-2 text-slate-400">
            <Play className="w-8 h-8 text-slate-200" />
            <span className="text-xs">配置权重与阈值后点击「运行回测」</span>
            <span className="text-[10px] text-slate-300">口径：月末调仓 · Top-N 等权 · 双边成本 0.2% · 打分 = Σ w×因子分 / Σ|w|</span>
          </div>
        )}
        {result?.kpi?.n_months ? (
          <div className="text-[10px] text-slate-300 pb-1">回测 {result.kpi.n_months} 期 · 因子：{Object.keys(result.weights).join('、')}</div>
        ) : null}
      </div>
    </div>
  );
};
