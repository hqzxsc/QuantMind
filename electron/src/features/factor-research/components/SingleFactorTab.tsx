/** 因子研究 —— 单因子分析页签：定义 + KPI + 净值 + IC + 最新持仓 */
import React, { useEffect, useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import { getFactorDetail } from '../services/factorResearchService';
import type { FactorDetail, FactorMeta } from '../types/factorResearch';
import { Card, IcChart, KpiTiles, NavChart, fmtNum } from './common';

interface Props {
  factors: FactorMeta[];
  code: string | null;
  onSelect: (code: string) => void;
}

export const SingleFactorTab: React.FC<Props> = ({ factors, code, onSelect }) => {
  const [detail, setDetail] = useState<FactorDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  const grouped = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out: Array<{ l1: string; items: FactorMeta[] }> = [];
    for (const f of factors) {
      if (q && !(`${f.code} ${f.name_cn} ${f.l2}`.toLowerCase().includes(q))) continue;
      let g = out.find((x) => x.l1 === f.l1);
      if (!g) {
        g = { l1: f.l1, items: [] };
        out.push(g);
      }
      g.items.push(f);
    }
    return out;
  }, [factors, query]);

  useEffect(() => {
    if (!code) return;
    let alive = true;
    setLoading(true);
    setError(null);
    getFactorDetail(code)
      .then((d) => { if (alive) setDetail(d); })
      .catch((e: unknown) => { if (alive) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [code]);

  return (
    <div className="flex-1 min-h-0 flex gap-2">
      {/* 左侧因子清单 */}
      <div className="w-[240px] shrink-0 flex flex-col bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden">
        <div className="p-2 border-b border-slate-100">
          <div className="flex items-center gap-1.5 rounded-lg bg-slate-50 border border-slate-200 px-2 py-1">
            <Search className="w-3 h-3 text-slate-400" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索因子…"
              className="w-full bg-transparent text-[11px] outline-none placeholder:text-slate-300"
            />
          </div>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-1.5">
          {grouped.map((g) => (
            <div key={g.l1}>
              <div className="px-2 pt-2 pb-1 text-[10px] font-extrabold text-slate-400">{g.l1}</div>
              {g.items.map((f) => (
                <button
                  key={f.code}
                  onClick={() => onSelect(f.code)}
                  className={`w-full text-left px-2 py-1 rounded-lg transition-colors ${
                    f.code === code ? 'bg-blue-50 text-blue-700' : 'hover:bg-slate-50 text-slate-600'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-bold truncate">{f.name_cn}</span>
                    {!f.available && <span className="text-[9px] text-slate-300 shrink-0">缺数据</span>}
                  </div>
                  <div className="text-[9px] text-slate-400 font-mono">{f.code} · {f.l2}</div>
                </button>
              ))}
            </div>
          ))}
        </div>
      </div>

      {/* 右侧详情 */}
      <div className="flex-1 min-w-0 min-h-0 overflow-y-auto custom-scrollbar flex flex-col gap-2">
        {error ? (
          <div className="flex-1 flex items-center justify-center text-xs text-rose-500">{error}</div>
        ) : loading && !detail ? (
          <div className="flex-1 rounded-2xl bg-slate-50 animate-pulse" />
        ) : detail ? (
          <>
            {/* 标题 + 定义 */}
            <Card title="因子定义">
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-base font-extrabold text-slate-800">{detail.name_cn}</span>
                    <span className="font-mono text-[11px] text-slate-400">{detail.code}</span>
                    <span className="rounded-full bg-slate-100 border border-slate-200 px-2 py-[1px] text-[10px] font-bold text-slate-500">
                      {detail.l1} · {detail.l2}
                    </span>
                    <span
                      className={`rounded-full px-2 py-[1px] text-[10px] font-bold ${
                        detail.direction === 1 ? 'bg-rose-50 text-rose-600 border border-rose-100' : 'bg-emerald-50 text-emerald-600 border border-emerald-100'
                      }`}
                    >
                      方向：{detail.direction === 1 ? '正向（越大越好）' : '负向（越小越好）'}
                    </span>
                    {detail.env_tag && (
                      <span className="rounded-full bg-amber-50 text-amber-600 border border-amber-100 px-2 py-[1px] text-[10px] font-bold">{detail.env_tag}</span>
                    )}
                    {detail.time_tag && (
                      <span className="rounded-full bg-slate-50 text-slate-500 border border-slate-200 px-2 py-[1px] text-[10px] font-bold">{detail.time_tag}</span>
                    )}
                  </div>
                  <p className="mt-1.5 text-[11px] text-slate-500 leading-relaxed whitespace-pre-line">{detail.description}</p>
                  {detail.formula && (
                    <pre className="mt-1.5 rounded-lg bg-slate-50 border border-slate-100 px-2.5 py-1.5 text-[10px] text-slate-600 font-mono whitespace-pre-wrap">{detail.formula}</pre>
                  )}
                  {detail.wind_source && <div className="mt-1 text-[9px] text-slate-400">数据源（demo 原口径）：{detail.wind_source}</div>}
                </div>
              </div>
            </Card>

            {/* KPI */}
            <KpiTiles kpi={detail.kpi} />

            {/* 净值 + IC */}
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-2">
              <Card title="组合净值（Top30 等权，月末调仓）" extra={<span className="text-[10px] text-slate-400">虚线=中证500</span>}>
                <NavChart
                  series={[
                    { name: detail.name_cn, data: detail.nav },
                    { name: '中证500', data: detail.benchmark, color: '#94a3b8' },
                  ]}
                  height={190}
                />
              </Card>
              <Card title="月频 IC（Spearman）" extra={<span className="text-[10px] text-slate-400">柱=当月 · 线=12期均值</span>}>
                <IcChart points={detail.ic} height={190} />
              </Card>
            </div>

            {/* 最新持仓 */}
            <Card
              title={`最新持仓（${detail.holdings_date || '—'} · Top ${detail.holdings.length}）`}
              extra={<span className="text-[10px] text-slate-400">按因子打分降序（方向已调整）</span>}
            >
              <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-x-4">
                {detail.holdings.map((h, i) => (
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
          <div className="flex-1 flex items-center justify-center text-xs text-slate-400">← 从左侧选择因子</div>
        )}
        {detail && (
          <div className="text-[10px] text-slate-300 pb-1">
            IC 均值 {fmtNum(detail.kpi.ic_mean, 4)} · 回测期数 {detail.kpi.n_months ?? '—'} · 缓存的快照数据（构建脚本 build_factor_research.py 生成）
          </div>
        )}
      </div>
    </div>
  );
};
