/** 因子研究 —— 筛选页签：质量门槛 + 同源去重的保留清单（训练特征选择用） */
import React, { useEffect, useMemo, useState } from 'react';
import { Check, Copy, Filter, ShieldAlert } from 'lucide-react';
import { getScreening } from '../services/factorResearchService';
import type { ScreeningResponse } from '../types/factorResearch';
import { Card, fmtNum } from './common';

const LIB_LABEL: Record<string, string> = {
  alpha101: 'Alpha101',
  gtja191: 'GTJA191',
  alpha158: 'Alpha158',
  factor_research: '因子研究',
};

export const ScreeningTab: React.FC = () => {
  const [data, setData] = useState<ScreeningResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lib, setLib] = useState<string>('全部');
  const [dropView, setDropView] = useState<'dup' | 'gate'>('dup');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    getScreening()
      .then((d) => { if (alive) setData(d); })
      .catch((e: unknown) => { if (alive) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const libOptions = useMemo(() => {
    const s = new Set((data?.kept || []).map((k) => k.library));
    return ['全部', ...Array.from(s)];
  }, [data]);

  const keptView = useMemo(() => {
    const rows = data?.kept || [];
    return lib === '全部' ? rows : rows.filter((r) => r.library === lib);
  }, [data, lib]);

  const copyKept = async () => {
    const text = keptView.map((r) => r.name).join('\n');
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* 剪贴板不可用时忽略 */
    }
  };

  if (loading) return <div className="flex-1 rounded-2xl bg-slate-50 animate-pulse" />;
  if (error) return <div className="flex-1 flex items-center justify-center text-xs text-rose-500">{error}</div>;
  if (!data || !data.counts?.total_considered) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-2 text-slate-400">
        <Filter className="w-8 h-8 text-slate-200" />
        <span className="text-xs">暂无筛选结果</span>
        <span className="text-[10px] text-slate-300">
          运行 <code className="font-mono">python3 backend/scripts/screen_factors.py</code> 生成
        </span>
      </div>
    );
  }

  const c = data.counts;
  const tiles = [
    { label: '参与筛选', value: c.total_considered, tone: 'text-slate-700' },
    { label: '过门槛候选', value: c.candidates, tone: 'text-slate-700' },
    { label: '最终保留', value: c.kept, tone: 'text-rose-600' },
    { label: '门槛剔除', value: c.gated_out, tone: 'text-slate-400' },
    { label: '去重剔除', value: c.deduped, tone: 'text-amber-600' },
  ];

  return (
    <div className="flex-1 min-h-0 flex flex-col gap-2">
      {/* 门槛与统计 */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex items-center gap-1.5 rounded-full bg-slate-100 border border-slate-200 px-3 py-1 text-[11px] font-bold text-slate-500">
          <ShieldAlert className="w-3 h-3 text-indigo-500" />
          |IC| ≥ {data.gates?.min_abs_ic} · |ICIR| ≥ {data.gates?.min_abs_icir} · 去重 |ρ| ≥ {data.gates?.corr_threshold}
        </div>
        {tiles.map((t) => (
          <span key={t.label} className="inline-flex items-center gap-1.5 rounded-full bg-white border border-slate-200 px-3 py-1 text-[11px] font-bold text-slate-500">
            {t.label}
            <b className={`font-mono ${t.tone}`}>{t.value}</b>
          </span>
        ))}
        <div className="flex-1" />
        <span className="text-[10px] text-slate-400">
          {data.cross_corr} · {data.generated_at ? `生成于 ${String(data.generated_at).slice(0, 16).replace('T', ' ')}` : ''}
        </span>
      </div>

      <div className="flex-1 min-h-0 flex gap-2">
        {/* 保留清单 */}
        <Card
          title={`保留清单（${keptView.length}）· 已去重，可直接进训练特征`}
          className="flex-1"
          extra={
            <div className="flex items-center gap-1.5">
              <div className="flex items-center gap-1 rounded-full bg-slate-100 border border-slate-200 p-0.5">
                {libOptions.map((o) => (
                  <button
                    key={o}
                    onClick={() => setLib(o)}
                    className={`rounded-full px-2.5 py-[2px] text-[10px] font-bold transition-colors ${
                      lib === o ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                    }`}
                  >
                    {LIB_LABEL[o] || o}
                  </button>
                ))}
              </div>
              <button
                onClick={copyKept}
                className="flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2.5 py-[3px] text-[10px] font-bold text-blue-600 hover:bg-blue-100"
              >
                {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                {copied ? '已复制' : `复制 ${keptView.length} 个特征名`}
              </button>
            </div>
          }
        >
          <div className="h-full overflow-y-auto custom-scrollbar">
            <table className="w-full text-[11px]">
              <thead className="sticky top-0 bg-white z-10">
                <tr className="text-slate-400 font-bold">
                  <th className="text-left py-1.5 w-8">#</th>
                  <th className="text-left py-1.5">因子</th>
                  <th className="text-left py-1.5 w-24">子库</th>
                  <th className="text-left py-1.5">说明</th>
                  <th className="text-right py-1.5 w-16">|IC|</th>
                  <th className="text-right py-1.5 w-16">|ICIR|</th>
                </tr>
              </thead>
              <tbody>
                {keptView.map((r, i) => (
                  <tr key={r.name} className="border-t border-slate-100 hover:bg-slate-50/70">
                    <td className="py-1.5 font-mono text-slate-400">{i + 1}</td>
                    <td className="py-1.5 font-mono font-bold text-slate-700">{r.name}</td>
                    <td className="py-1.5 text-[10px] text-slate-400">{LIB_LABEL[r.library] || r.library}</td>
                    <td className="py-1.5 text-slate-500 text-[10px] truncate max-w-[220px]">{r.display_name}</td>
                    <td className="py-1.5 text-right font-mono text-slate-600">{fmtNum(Math.abs(r.ic_mean || 0), 4)}</td>
                    <td className="py-1.5 text-right font-mono font-bold text-slate-700">{fmtNum(Math.abs(r.icir || 0), 3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        {/* 剔除明细 */}
        <div className="w-[380px] shrink-0 flex flex-col bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden">
          <div className="px-3 py-2 border-b border-slate-100 flex items-center gap-1.5">
            <div className="flex items-center gap-1 rounded-full bg-slate-100 border border-slate-200 p-0.5">
              <button
                onClick={() => setDropView('dup')}
                className={`rounded-full px-2.5 py-[2px] text-[10px] font-bold transition-colors ${dropView === 'dup' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500'}`}
              >
                同源去重（{data.dropped_duplicate.length}）
              </button>
              <button
                onClick={() => setDropView('gate')}
                className={`rounded-full px-2.5 py-[2px] text-[10px] font-bold transition-colors ${dropView === 'gate' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500'}`}
              >
                门槛剔除（{data.dropped_gated.length}）
              </button>
            </div>
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-2 space-y-0.5">
            {dropView === 'dup'
              ? data.dropped_duplicate.map((d, i) => (
                  <div key={`${d.name}-${i}`} className="flex items-center justify-between px-2 py-1 rounded-lg hover:bg-slate-50">
                    <span className="text-[11px] font-mono text-slate-500 truncate">{d.name}</span>
                    <span className="text-[10px] text-slate-400 shrink-0 ml-2">
                      → <b className="font-mono text-slate-600">{d.duplicate_of}</b>
                      <span className="ml-1 rounded bg-amber-50 border border-amber-100 px-1 py-[1px] font-mono text-amber-600">ρ={d.abs_corr.toFixed(2)}</span>
                    </span>
                  </div>
                ))
              : data.dropped_gated.map((d) => (
                  <div key={d.name} className="flex items-center justify-between px-2 py-1 rounded-lg hover:bg-slate-50">
                    <span className="text-[11px] font-mono text-slate-500 truncate">{d.name}</span>
                    <span className="text-[10px] text-slate-400 shrink-0 ml-2">{d.reason}</span>
                  </div>
                ))}
          </div>
          <div className="px-3 py-1.5 border-t border-slate-100 text-[10px] text-slate-400">
            去重为并查集聚类（传递闭包）：同簇只留 |ICIR| 最强者；ρ 列为与保留代表的直接相关
          </div>
        </div>
      </div>
    </div>
  );
};
