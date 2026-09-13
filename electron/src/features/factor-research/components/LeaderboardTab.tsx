/** 因子研究 —— 排行榜页签：综合分排名 + 勾选带入对比/合成 */
import React, { useMemo, useState } from 'react';
import { ArrowRightLeft, CheckSquare, Layers, Square, TrendingUp } from 'lucide-react';
import type { LeaderboardRow } from '../types/factorResearch';
import { fmtNum, fmtPct, Card } from './common';

interface Props {
  rows: LeaderboardRow[];
  loading: boolean;
  error: string | null;
  selected: string[];
  onToggle: (code: string) => void;
  onSendCompare: () => void;
  onSendCompose: () => void;
  onOpenSingle: (code: string) => void;
  meta: Record<string, unknown>;
}

type SortKey = 'composite' | 'annual_return' | 'sharpe' | 'ic_mean' | 'ic_ir';

const SORTS: Array<{ key: SortKey; label: string }> = [
  { key: 'composite', label: '综合分' },
  { key: 'annual_return', label: '年化' },
  { key: 'sharpe', label: '夏普' },
  { key: 'ic_mean', label: 'IC 均值' },
  { key: 'ic_ir', label: 'ICIR' },
];

export const LeaderboardTab: React.FC<Props> = ({
  rows, loading, error, selected, onToggle, onSendCompare, onSendCompose, onOpenSingle, meta,
}) => {
  const [sortKey, setSortKey] = useState<SortKey>('composite');
  const [l1, setL1] = useState<string>('全部');

  const l1Options = useMemo(() => ['全部', ...Array.from(new Set(rows.map((r) => r.l1)))], [rows]);
  const view = useMemo(() => {
    const filtered = l1 === '全部' ? rows : rows.filter((r) => r.l1 === l1);
    const sorted = [...filtered];
    sorted.sort((a, b) => {
      if (sortKey === 'composite') return b.composite - a.composite;
      const av = (a[sortKey] as number | null) ?? -Infinity;
      const bv = (b[sortKey] as number | null) ?? -Infinity;
      return bv - av;
    });
    return sorted;
  }, [rows, sortKey, l1]);

  const window_ = meta?.window as string[] | undefined;

  return (
    <div className="flex-1 min-h-0 flex flex-col gap-2">
      {/* 工具条 */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex items-center gap-1 rounded-full bg-slate-100 border border-slate-200 p-0.5">
          {SORTS.map((s) => (
            <button
              key={s.key}
              onClick={() => setSortKey(s.key)}
              className={`rounded-full px-3 py-1 text-[11px] font-bold transition-colors ${
                sortKey === s.key ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1 rounded-full bg-slate-100 border border-slate-200 p-0.5">
          {l1Options.map((g) => (
            <button
              key={g}
              onClick={() => setL1(g)}
              className={`rounded-full px-3 py-1 text-[11px] font-bold transition-colors ${
                l1 === g ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              {g}
            </button>
          ))}
        </div>
        <div className="flex-1" />
        <span className="text-[11px] font-bold text-slate-400">
          {window_ ? `${window_[0]} ~ ${window_[1]} · 月频 · Top30 等权` : ''}
        </span>
        <button
          onClick={onSendCompare}
          disabled={selected.length < 2}
          className="flex items-center gap-1.5 rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-[11px] font-bold text-blue-600 hover:bg-blue-100 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <ArrowRightLeft className="w-3 h-3" />
          带入对比（{selected.length}）
        </button>
        <button
          onClick={onSendCompose}
          disabled={selected.length < 1}
          className="flex items-center gap-1.5 rounded-full border border-violet-200 bg-violet-50 px-3 py-1 text-[11px] font-bold text-violet-600 hover:bg-violet-100 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Layers className="w-3 h-3" />
          带入合成（{selected.length}）
        </button>
      </div>

      {/* 表格 */}
      <Card
        title={`因子排行榜（${view.length}）`}
        className="flex-1"
        extra={<span className="text-[10px] text-slate-400">综合分 = 0.5×|IC| 分位 + 0.5×夏普分位 · 点击行看单因子</span>}
      >
        {error ? (
          <div className="h-full flex items-center justify-center text-xs text-rose-500">{error}</div>
        ) : loading ? (
          <div className="h-full rounded-xl bg-slate-50 animate-pulse" />
        ) : (
          <div className="h-full overflow-y-auto custom-scrollbar">
            <table className="w-full text-[11px]">
              <thead className="sticky top-0 bg-white z-10">
                <tr className="text-slate-400 font-bold">
                  <th className="text-left py-1.5 pl-1 w-8"></th>
                  <th className="text-left py-1.5 w-10">#</th>
                  <th className="text-left py-1.5">因子</th>
                  <th className="text-left py-1.5 w-24">大类/小类</th>
                  <th className="text-right py-1.5 w-14">综合分</th>
                  <th className="text-right py-1.5 w-16">年化</th>
                  <th className="text-right py-1.5 w-12">夏普</th>
                  <th className="text-right py-1.5 w-16">最大回撤</th>
                  <th className="text-right py-1.5 w-12">月胜率</th>
                  <th className="text-right py-1.5 w-14">IC 均值</th>
                  <th className="text-right py-1.5 w-12">ICIR</th>
                </tr>
              </thead>
              <tbody>
                {view.map((r) => {
                  const isSel = selected.includes(r.code);
                  return (
                    <tr
                      key={r.code}
                      className={`border-t border-slate-100 hover:bg-slate-50/70 cursor-pointer ${isSel ? 'bg-blue-50/50' : ''}`}
                      onClick={() => onOpenSingle(r.code)}
                    >
                      <td className="py-1.5 pl-1" onClick={(e) => { e.stopPropagation(); onToggle(r.code); }}>
                        {isSel ? <CheckSquare className="w-3.5 h-3.5 text-blue-600" /> : <Square className="w-3.5 h-3.5 text-slate-300" />}
                      </td>
                      <td className="py-1.5 font-mono text-slate-400">{r.rank}</td>
                      <td className="py-1.5">
                        <span className="font-bold text-slate-700">{r.name_cn}</span>
                        <span className="ml-1.5 font-mono text-[10px] text-slate-400">{r.code}</span>
                      </td>
                      <td className="py-1.5 text-slate-400 text-[10px]">{r.l1.slice(0, 2)} / {r.l2}</td>
                      <td className="py-1.5 text-right font-mono font-bold text-slate-700">{fmtNum(r.composite, 3)}</td>
                      <td className={`py-1.5 text-right font-mono font-bold ${(r.annual_return || 0) >= 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                        {fmtPct(r.annual_return)}
                      </td>
                      <td className="py-1.5 text-right font-mono text-slate-600">{fmtNum(r.sharpe)}</td>
                      <td className="py-1.5 text-right font-mono text-slate-500">{fmtPct(r.max_drawdown)}</td>
                      <td className="py-1.5 text-right font-mono text-slate-500">{fmtPct(r.win_rate)}</td>
                      <td className={`py-1.5 text-right font-mono ${(r.ic_mean || 0) >= 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                        {fmtNum(r.ic_mean, 3)}
                      </td>
                      <td className="py-1.5 text-right font-mono text-slate-500">{fmtNum(r.ic_ir)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <div className="text-[10px] text-slate-400 flex items-center gap-1">
        <TrendingUp className="w-3 h-3" />
        口径：全 A 非 ST/退市池，月末调仓 Top30 等权，双边成本 0.2%，复权价 · 与 factor-lib-demo 一致
      </div>
    </div>
  );
};
