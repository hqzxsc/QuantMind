/**
 * 提示词库 — 左：搜索+胶囊分类+分组列表；右：详情卡片
 * 对齐 StockTerminal / MarketAnalysis / ModelTraining 的卡片与筛选项语言
 */
import React, { useMemo, useState } from 'react';
import {
  BookMarked,
  ChevronRight,
  ClipboardCheck,
  Copy,
  FileText,
  Lightbulb,
  Sparkles,
  Search,
  X,
} from 'lucide-react';
import { PROMPTS, type PromptMeta } from '../prompts.generated';

const CATEGORY_ORDER = ['平台运营', '环境初始化', '研究分析', '策略·因子·模型·回测', '交易'];

interface CategoryStyle {
  dot: string;
  chipBg: string;
  chipText: string;
  chipBorder: string;
  accent: string;
}

const CATEGORY_STYLE: Record<string, CategoryStyle> = {
  研究分析: { dot: '#2563eb', chipBg: '#eff6ff', chipText: '#1d4ed8', chipBorder: '#dbeafe', accent: '#3b82f6' },
  '策略·因子·模型·回测': { dot: '#7c3aed', chipBg: '#f5f3ff', chipText: '#6d28d9', chipBorder: '#ede9fe', accent: '#8b5cf6' },
  交易: { dot: '#ea580c', chipBg: '#fff7ed', chipText: '#c2410c', chipBorder: '#ffedd5', accent: '#f97316' },
  平台运营: { dot: '#16a34a', chipBg: '#f0fdf4', chipText: '#15803d', chipBorder: '#dcfce7', accent: '#22c55e' },
  环境初始化: { dot: '#0e7490', chipBg: '#ecfeff', chipText: '#0e7490', chipBorder: '#cffafe', accent: '#06b6d4' },
};

const DEFAULT_STYLE: CategoryStyle = { dot: '#64748b', chipBg: '#f1f5f9', chipText: '#475569', chipBorder: '#e2e8f0', accent: '#94a3b8' };

const PromptsLibrary: React.FC = () => {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<PromptMeta>(PROMPTS[0]);
  const [copied, setCopied] = useState(false);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return PROMPTS;
    return PROMPTS.filter(
      (p) =>
        p.title.toLowerCase().includes(q) ||
        p.description.toLowerCase().includes(q) ||
        p.name.toLowerCase().includes(q) ||
        p.category.toLowerCase().includes(q),
    );
  }, [query]);

  const grouped = useMemo(() => {
    const map = new Map<string, PromptMeta[]>();
    for (const p of filtered) {
      if (!map.has(p.category)) map.set(p.category, []);
      map.get(p.category)!.push(p);
    }
    return [...map.entries()].sort(
      (a, b) => CATEGORY_ORDER.indexOf(a[0]) - CATEGORY_ORDER.indexOf(b[0]),
    );
  }, [filtered]);

  // 若当前选中不在过滤结果内，回落到第一条
  React.useEffect(() => {
    if (!filtered.find((p) => p.name === selected.name) && filtered[0]) {
      setSelected(filtered[0]);
    }
  }, [filtered, selected.name]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(selected.body);
    } catch {
      const textarea = document.createElement('textarea');
      textarea.value = selected.body;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  const style = CATEGORY_STYLE[selected.category] ?? DEFAULT_STYLE;

  return (
    <div className="flex h-full w-full min-w-0 overflow-hidden">
      {/* 左列：搜索 + 分组列表 */}
      <div className="w-[280px] shrink-0 flex flex-col border-r border-gray-200 bg-white overflow-hidden">
        {/* 搜索区 */}
        <div className="px-4 pt-4 pb-3 border-b border-gray-100 shrink-0">
          <div className="flex items-center gap-2 text-[11px] font-black uppercase tracking-[0.12em] text-slate-400 mb-2.5">
            <BookMarked className="w-3.5 h-3.5" />
            提示词库
            <span className="ml-auto text-[10px] font-bold normal-case tracking-normal text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded-md">
              {filtered.length}/{PROMPTS.length}
            </span>
          </div>
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索 标题 / 描述 / 名称"
              className="w-full h-8 pl-8 pr-8 rounded-full border border-slate-200 bg-white text-[12px] placeholder:text-slate-400 focus:outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-100"
            />
            {query && (
              <button
                onClick={() => setQuery('')}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 w-6 h-6 rounded-md flex items-center justify-center text-slate-400 hover:bg-slate-100"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>

        {/* 分组列表 */}
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar px-3 py-3">
          {grouped.length === 0 ? (
            <div className="text-center py-10 text-xs text-slate-400">无匹配结果</div>
          ) : (
            grouped.map(([category, prompts]) => {
              const cat = CATEGORY_STYLE[category] ?? DEFAULT_STYLE;
              return (
                <div key={category} className="mb-4 last:mb-0">
                  <div className="flex items-center gap-2 px-1 pb-1.5">
                    <span className="h-1.5 w-1.5 rounded-full shrink-0" style={{ background: cat.dot }} />
                    <span className="text-[10px] font-black uppercase tracking-[0.10em] text-slate-400">{category}</span>
                    <span className="text-[10px] font-mono text-slate-300">{prompts.length}</span>
                  </div>
                  <div className="space-y-1">
                    {prompts.map((p) => {
                      const active = p.name === selected.name;
                      return (
                        <button
                          key={p.name}
                          onClick={() => {
                            setSelected(p);
                            setCopied(false);
                          }}
                          className={`group relative flex w-full items-center gap-2.5 rounded-xl py-2.5 pl-3 pr-2 text-left border transition-all ${
                            active
                              ? 'bg-indigo-50 border-indigo-100 shadow-sm'
                              : 'bg-white border-transparent hover:bg-slate-50 hover:border-slate-100'
                          }`}
                        >
                          {active && <span className="absolute left-0 top-2 bottom-2 w-0.5 bg-indigo-500 rounded-full" />}
                          <span
                            className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 border ${
                              active ? 'bg-white border-indigo-100 text-indigo-600' : 'bg-slate-50 border-slate-100 text-slate-400'
                            }`}
                          >
                            <FileText className="w-3.5 h-3.5" />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span
                              className={`block truncate text-[12.5px] leading-tight ${active ? 'font-semibold text-indigo-700' : 'font-medium text-slate-700'}`}
                            >
                              {p.title}
                            </span>
                            <span className="block truncate text-[11px] leading-tight text-slate-400">{p.description}</span>
                          </span>
                          <ChevronRight
                            className={`w-3 h-3 shrink-0 transition-colors ${active ? 'text-indigo-400' : 'text-slate-300 group-hover:text-slate-400'}`}
                          />
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })
          )}
        </div>

        <div className="px-4 py-2.5 border-t border-gray-100 bg-slate-50/60 text-[10px] leading-relaxed text-slate-400">
          选中后在右侧复制，到 QuantBot 粘贴即用；<span className="font-mono font-bold text-slate-500">{'{占位符}'}</span> 替换为实际内容。
        </div>
      </div>

      {/* 右列：详情（灰底 + 圆角白卡） */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-gray-50/50">
        <div className="flex-1 min-h-0 overflow-y-auto p-4 custom-scrollbar">
          <div className="mx-auto max-w-[760px] space-y-4">
            {/* 顶部信息卡 */}
            <div className="rounded-3xl bg-white border border-purple-100/80 shadow-sm p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-3 flex-wrap">
                    <div
                      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-white shadow-md"
                      style={{ background: `linear-gradient(135deg, ${style.accent}, #a855f7)` }}
                    >
                      <Sparkles className="w-4 h-4" />
                    </div>
                    <h3 className="text-[16px] font-bold text-slate-800 tracking-tight">{selected.title}</h3>
                    <span
                      className="shrink-0 rounded-full border px-2.5 py-0.5 text-[11px] font-bold"
                      style={{ background: style.chipBg, color: style.chipText, borderColor: style.chipBorder }}
                    >
                      {selected.category}
                    </span>
                  </div>
                  <p className="mt-2 pl-[48px] text-[12.5px] leading-relaxed text-slate-500">{selected.description}</p>
                  <p className="mt-1 pl-[48px] text-[11px] text-slate-400">
                    产出：<span className="font-medium text-slate-500">{selected.outputs}</span>
                    <span className="mx-1.5 text-slate-300">·</span>
                    <span className="font-mono text-[10px] text-slate-400">{selected.name}</span>
                  </p>
                </div>
                <button
                  onClick={handleCopy}
                  className="flex shrink-0 items-center gap-1.5 rounded-xl px-4 py-2 text-[12px] font-bold text-white shadow-lg transition-all active:translate-y-px"
                  style={{
                    background: copied ? 'linear-gradient(135deg, #059669, #10b981)' : 'linear-gradient(135deg, #4f46e5, #a855f7)',
                    boxShadow: copied ? '0 8px 20px -6px rgba(16,185,129,0.45)' : '0 8px 20px -6px rgba(99,102,241,0.45)',
                  }}
                >
                  {copied ? <ClipboardCheck className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                  {copied ? '已复制' : '复制提示词'}
                </button>
              </div>
            </div>

            {/* 提示词正文卡 */}
            <div className="rounded-3xl bg-white border border-slate-200/80 shadow-sm overflow-hidden">
              <div className="flex items-center gap-2 px-4 py-2.5 border-b border-slate-100 bg-slate-50/60">
                <span className="h-2 w-2 rounded-full bg-indigo-400" />
                <span className="text-[11px] font-bold text-slate-600">提示词全文</span>
                <span className="ml-auto text-[10px] font-mono text-slate-400 hidden sm:inline">
                  {'{占位符}'} 替换后粘贴到 QuantBot
                </span>
              </div>
              <div className="px-4 py-2.5 flex items-center gap-1.5 text-[11px] text-slate-400 bg-amber-50/40 border-b border-amber-100/60">
                <Lightbulb className="w-3 h-3 text-amber-500" />
                复制后在 QuantBot 对话中粘贴使用，报告将自动归档到右侧档案。
              </div>
              <pre
                className="whitespace-pre-wrap p-5 text-[12.5px] leading-relaxed text-slate-700 overflow-x-auto"
                style={{ fontFamily: 'var(--font-mono)' }}
              >
                {selected.body}
              </pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default PromptsLibrary;
