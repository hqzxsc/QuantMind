/**
 * 因子研究 — 独立栏目页（factor-lib-demo 改造版）
 *
 * 五个页签：排行榜 / 单因子分析 / 多因子对比 / 多因子合成 / 筛选
 * 数据：/api/v1/factor-research（引擎服务，快照由 build_factor_research.py 构建）
 */
import React, { useCallback, useEffect, useState } from 'react';
import { ArrowRightLeft, BarChart3, Filter, Layers, Sigma, TableProperties } from 'lucide-react';
import { PAGE_LAYOUT } from '../../../config/pageLayout';
import { getCatalog, getLeaderboard } from '../services/factorResearchService';
import type { FactorMeta, LeaderboardRow } from '../types/factorResearch';
import { LeaderboardTab } from '../components/LeaderboardTab';
import { SingleFactorTab } from '../components/SingleFactorTab';
import { CompareTab } from '../components/CompareTab';
import { ComposeTab } from '../components/ComposeTab';
import { ScreeningTab } from '../components/ScreeningTab';

type Tab = 'leaderboard' | 'single' | 'compare' | 'compose' | 'screening';

const TABS: Array<{ key: Tab; label: string; icon: React.ComponentType<{ className?: string }> }> = [
  { key: 'leaderboard', label: '排行榜', icon: BarChart3 },
  { key: 'single', label: '单因子分析', icon: TableProperties },
  { key: 'compare', label: '多因子对比', icon: ArrowRightLeft },
  { key: 'compose', label: '多因子合成', icon: Layers },
  { key: 'screening', label: '筛选', icon: Filter },
];

const FactorResearchPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('leaderboard');
  const [factors, setFactors] = useState<FactorMeta[]>([]);
  const [rows, setRows] = useState<LeaderboardRow[]>([]);
  const [meta, setMeta] = useState<Record<string, unknown>>({});
  const [selected, setSelected] = useState<string[]>([]);
  const [activeCode, setActiveCode] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([getCatalog(), getLeaderboard()])
      .then(([cat, lb]) => {
        if (!alive) return;
        setFactors(cat.factors);
        setMeta(cat.meta);
        setRows(lb.leaderboard);
        if (lb.leaderboard.length) setActiveCode(lb.leaderboard[0].code);
        setError(null);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const nameOf = useCallback((c: string) => factors.find((f) => f.code === c)?.name_cn || c, [factors]);

  const toggleSelected = (code: string) =>
    setSelected(selected.includes(code) ? selected.filter((c) => c !== code) : [...selected, code]);

  const window_ = meta?.window as string[] | undefined;
  const available = factors.filter((f) => f.available).length;

  return (
    <div className={PAGE_LAYOUT.outerClass}>
      <div className={PAGE_LAYOUT.frameClass}>
        <header className={PAGE_LAYOUT.headerClass} style={{ height: `${PAGE_LAYOUT.headerHeight}px` }}>
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-10 h-10 bg-gradient-to-br from-indigo-500 to-violet-500 rounded-2xl flex items-center justify-center shadow-lg shrink-0">
              <Sigma className="w-5 h-5 text-white" />
            </div>
            <div className="flex items-center gap-2.5 ml-1 min-w-0">
              <h1 className="text-xl font-bold text-slate-800 tracking-tight">因子研究</h1>
              <div className="h-4 w-[1px] bg-slate-200 self-center shrink-0" />
              <span className="text-sm font-medium text-slate-500 truncate">多因子库 · 单因子体检 · 对比 · 合成</span>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <div className="flex items-center gap-1 rounded-full bg-slate-100 border border-slate-200 p-0.5">
              {TABS.map((t) => {
                const Icon = t.icon;
                return (
                  <button
                    key={t.key}
                    onClick={() => setTab(t.key)}
                    className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-bold transition-colors ${
                      tab === t.key ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                    }`}
                  >
                    <Icon className="w-3 h-3" />
                    {t.label}
                  </button>
                );
              })}
            </div>
            <span className="hidden lg:inline-flex items-center gap-1.5 rounded-full bg-slate-100 border border-slate-200 px-3 py-1 text-[11px] font-bold text-slate-500">
              <span className="h-1.5 w-1.5 rounded-full bg-indigo-500" />
              {available}/{factors.length || 82} 因子可用
            </span>
            {window_ && (
              <span className="hidden xl:inline-flex items-center rounded-full bg-indigo-50 border border-indigo-100 px-2.5 py-1 text-[11px] font-bold text-indigo-600">
                {window_[0]} ~ {window_[1]}
              </span>
            )}
          </div>
        </header>

        <div className="flex-1 min-h-0 min-w-0 overflow-hidden p-3 flex flex-col">
          {error ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-2">
              <span className="text-sm text-rose-500">{error}</span>
              <span className="text-[11px] text-slate-400">
                若为 404/快照缺失：请先运行 <code className="font-mono">python3 backend/scripts/build_factor_research.py</code>
              </span>
            </div>
          ) : tab === 'leaderboard' ? (
            <LeaderboardTab
              rows={rows}
              loading={loading}
              error={null}
              selected={selected}
              onToggle={toggleSelected}
              onSendCompare={() => setTab('compare')}
              onSendCompose={() => setTab('compose')}
              onOpenSingle={(c) => {
                setActiveCode(c);
                setTab('single');
              }}
              meta={meta}
            />
          ) : tab === 'single' ? (
            <SingleFactorTab factors={factors} code={activeCode} onSelect={setActiveCode} />
          ) : tab === 'compare' ? (
            <CompareTab codes={selected} onRemove={toggleSelected} nameOf={nameOf} />
          ) : tab === 'screening' ? (
            <ScreeningTab />
          ) : (
            <ComposeTab factors={factors} codes={selected} onChangeCodes={setSelected} />
          )}
        </div>
      </div>
    </div>
  );
};

export default FactorResearchPage;
