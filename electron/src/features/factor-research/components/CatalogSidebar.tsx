/**
 * 因子研究 —— 左侧因子目录：大类 → 小类分组、搜索、标签筛选、
 * 每行显示综合分与两个标签，勾选框带入对比/合成，点击名称看单因子。
 */
import React, { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Search } from 'lucide-react';
import type { FactorMeta, LeaderboardRow } from '../types/factorResearch';
import { fmtNum, TagChip } from './common';

interface Props {
  factors: FactorMeta[];
  l1Order: string[];
  rowsByCode: Map<string, LeaderboardRow>;
  selected: string[];
  activeCode: string | null;
  tagFilter: string[];
  onToggle: (code: string) => void;
  onOpen: (code: string) => void;
}

export const CatalogSidebar: React.FC<Props> = ({
  factors, l1Order, rowsByCode, selected, activeCode, tagFilter, onToggle, onOpen,
}) => {
  const [query, setQuery] = useState('');
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const byL1 = new Map<string, Map<string, FactorMeta[]>>();
    for (const f of factors) {
      if (q && !`${f.code} ${f.name_cn} ${f.l2}`.toLowerCase().includes(q)) continue;
      if (tagFilter.length) {
        const row = rowsByCode.get(f.code);
        const tags = row ? [row.env_tag, row.time_tag] : [f.env_tag, f.time_tag];
        if (!tags.some((t) => tagFilter.includes(t))) continue;
      }
      const l2m = byL1.get(f.l1) || new Map<string, FactorMeta[]>();
      const arr = l2m.get(f.l2) || [];
      arr.push(f);
      l2m.set(f.l2, arr);
      byL1.set(f.l1, l2m);
    }
    const order = l1Order.length ? l1Order : Array.from(byL1.keys());
    return order
      .filter((l1) => byL1.has(l1))
      .map((l1) => {
        const l2m = byL1.get(l1)!;
        const l2Groups = Array.from(l2m.entries()).map(([l2, list]) => ({
          l2,
          list: [...list].sort((a, b) => {
            const ra = rowsByCode.get(a.code)?.composite ?? -Infinity;
            const rb = rowsByCode.get(b.code)?.composite ?? -Infinity;
            return rb - ra;
          }),
        }));
        const count = l2Groups.reduce((n, g) => n + g.list.length, 0);
        return { l1, l2Groups, count };
      });
  }, [factors, l1Order, query, tagFilter, rowsByCode]);

  return (
    <div className="w-[248px] shrink-0 flex flex-col bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden">
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
        <div className="mt-1 flex items-center justify-between px-0.5">
          <span className="text-[9px] text-slate-400">综合分按当前区间自动重算</span>
          <span className="text-[9px] font-bold text-indigo-500">已选 {selected.length}</span>
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar py-1">
        {groups.map((g) => {
          const isCollapsed = collapsed[g.l1];
          return (
            <div key={g.l1}>
              <button
                onClick={() => setCollapsed({ ...collapsed, [g.l1]: !isCollapsed })}
                className="w-full flex items-center gap-1 px-2 py-1.5 text-left hover:bg-slate-50"
              >
                {isCollapsed ? <ChevronRight className="w-3 h-3 text-slate-400" /> : <ChevronDown className="w-3 h-3 text-slate-400" />}
                <span className="text-[10px] font-extrabold text-slate-500">{g.l1}</span>
                <span className="ml-auto text-[9px] text-slate-300">{g.count}</span>
              </button>
              {!isCollapsed &&
                g.l2Groups.map((lg) => (
                  <div key={lg.l2} className="pb-0.5">
                    <div className="px-3 pt-1 pb-0.5 text-[9px] font-bold text-slate-400/90">{lg.l2}</div>
                    {lg.list.map((f) => {
                      const row = rowsByCode.get(f.code);
                      const isActive = f.code === activeCode;
                      const isSel = selected.includes(f.code);
                      return (
                        <div
                          key={f.code}
                          className={`group flex items-center gap-1.5 pl-2.5 pr-1.5 py-[3px] cursor-pointer transition-colors ${
                            isActive ? 'bg-blue-50' : 'hover:bg-slate-50'
                          } ${!f.available ? 'opacity-45' : ''}`}
                          onClick={() => f.available && onOpen(f.code)}
                        >
                          <input
                            type="checkbox"
                            checked={isSel}
                            disabled={!f.available}
                            onClick={(e) => e.stopPropagation()}
                            onChange={() => onToggle(f.code)}
                            className="w-3 h-3 accent-indigo-600 shrink-0"
                          />
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-1">
                              <span className={`truncate text-[11px] font-bold ${isActive ? 'text-blue-700' : 'text-slate-700'}`}>
                                {f.name_cn}
                              </span>
                              {!f.available && <span className="shrink-0 text-[8px] text-slate-400">缺数据</span>}
                            </div>
                            <div className="flex items-center gap-1.5 mt-[1px]">
                              <span className="font-mono text-[9px] text-slate-400">{f.code}</span>
                              {row && (
                                <span className="font-mono text-[9px] font-bold text-indigo-500" title="综合分">
                                  {fmtNum(row.composite, 2)}
                                </span>
                              )}
                              {row && <TagChip tag={row.env_tag} small />}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ))}
            </div>
          );
        })}
        {groups.length === 0 && (
          <div className="text-[10px] text-slate-300 text-center py-6">无匹配因子</div>
        )}
      </div>
    </div>
  );
};
