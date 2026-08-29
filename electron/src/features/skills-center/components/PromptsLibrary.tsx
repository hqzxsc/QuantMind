/**
 * 提示词库 — 技能中心主从布局（左列：分类+标题，中列：提示词详情）
 *
 * 数据来自 prompts.generated.ts（源文件：仓库根目录 prompts/*.md）。
 * 复制按钮一键复制完整提示词到 QuantBot（QwenPaw）。
 *
 * 视觉与项目风格对齐：电光 indigo→purple 渐变主色、玻璃卡片、分层阴影、
 * 大写分类字标与紧凑圆角，替代原先的扁平蓝主题。
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
} from 'lucide-react';
import { PROMPTS, type PromptMeta } from '../prompts.generated';

const CATEGORY_ORDER = ['环境初始化', '研究分析', '策略·因子·模型·回测', '交易', '平台运营', '券商 SDK'];

interface CategoryStyle {
  dot: string;
  chipBg: string;
  chipText: string;
  accent: string;
}

const CATEGORY_STYLE: Record<string, CategoryStyle> = {
  研究分析: { dot: '#2563eb', chipBg: '#eff6ff', chipText: '#1d4ed8', accent: '#3b82f6' },
  '策略·因子·模型·回测': { dot: '#7c3aed', chipBg: '#f5f3ff', chipText: '#6d28d9', accent: '#8b5cf6' },
  交易: { dot: '#ea580c', chipBg: '#fff7ed', chipText: '#c2410c', accent: '#f97316' },
  平台运营: { dot: '#16a34a', chipBg: '#f0fdf4', chipText: '#15803d', accent: '#22c55e' },
  '券商 SDK': { dot: '#64748b', chipBg: '#f1f5f9', chipText: '#475569', accent: '#94a3b8' },
};

const DEFAULT_STYLE: CategoryStyle = { dot: '#64748b', chipBg: '#f1f5f9', chipText: '#475569', accent: '#94a3b8' };

const PromptsLibrary: React.FC = () => {
  const [selected, setSelected] = useState<PromptMeta>(PROMPTS[0]);
  const [copied, setCopied] = useState(false);

  const grouped = useMemo(() => {
    const map = new Map<string, PromptMeta[]>();
    for (const p of PROMPTS) {
      if (!map.has(p.category)) map.set(p.category, []);
      map.get(p.category)!.push(p);
    }
    return [...map.entries()].sort(
      (a, b) => CATEGORY_ORDER.indexOf(a[0]) - CATEGORY_ORDER.indexOf(b[0]),
    );
  }, []);

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
      {/* 左列：分类 + 标题列表（玻璃白底 + 细分隔线） */}
      <div className="w-64 shrink-0 overflow-y-auto border-r border-slate-200/70 bg-white/60 py-4 backdrop-blur">
        <div className="mb-2 flex items-center gap-2 px-4 text-[12px] font-semibold uppercase tracking-[0.12em] text-slate-400">
          <BookMarked style={{ width: 14, height: 14 }} />
          提示词库
        </div>
        {grouped.map(([category, prompts]) => {
          const cat = CATEGORY_STYLE[category] ?? DEFAULT_STYLE;
          return (
            <div key={category} className="mb-1.5">
              <div className="flex items-center gap-2 px-4 pb-1.5 pt-2.5">
                <span className="h-2 w-2 rounded-full" style={{ background: cat.dot }} />
                <span className="text-[12.5px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                  {category}
                </span>
              </div>
              <div className="space-y-0.5 px-2">
                {prompts.map((p) => {
                  const active = p.name === selected.name;
                  return (
                    <button
                      key={p.name}
                      onClick={() => {
                        setSelected(p);
                        setCopied(false);
                      }}
                      className="group relative flex w-full items-center gap-2.5 rounded-lg py-2 pl-3 pr-2 text-left transition-all duration-150"
                      style={{
                        background: active ? 'linear-gradient(90deg, rgba(99,102,241,0.10), rgba(168,85,247,0.05))' : 'transparent',
                      }}
                    >
                      {active && (
                        <span
                          className="absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-full"
                          style={{ background: `linear-gradient(180deg, ${style.accent}, #a855f7)` }}
                        />
                      )}
                      <FileText
                        style={{
                          width: 15,
                          height: 15,
                          flexShrink: 0,
                          color: active ? '#6366f1' : '#94a3b8',
                        }}
                      />
                      <span
                        className="truncate text-[13.5px] transition-colors"
                        style={{
                          color: active ? '#4338ca' : '#334155',
                          fontWeight: active ? 600 : 500,
                        }}
                      >
                        {p.title}
                      </span>
                      <ChevronRight
                        style={{
                          width: 13,
                          height: 13,
                          flexShrink: 0,
                          marginLeft: 'auto',
                          color: active ? '#8b5cf6' : 'transparent',
                        }}
                      />
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      {/* 中列：提示词详情 */}
      <div className="flex min-w-0 flex-1 flex-col overflow-y-auto bg-white/40">
        {/* 头部 */}
        <div className="flex items-start justify-between gap-3 border-b border-slate-200/60 px-6 py-5">
          <div className="min-w-0">
            <div className="flex items-center gap-3">
              <div
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-white"
                style={{ background: `linear-gradient(135deg, ${style.accent}, #a855f7)` }}
              >
                <Sparkles style={{ width: 16, height: 16 }} />
              </div>
              <h3 className="truncate text-[17px] font-bold text-slate-900">{selected.title}</h3>
              <span
                className="shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-semibold"
                style={{ background: style.chipBg, color: style.chipText }}
              >
                {selected.category}
              </span>
            </div>
            <div className="mt-2 pl-12 text-[12.5px] leading-relaxed text-slate-500">
              {selected.description}
            </div>
            <div className="mt-1 pl-12 text-[11.5px] text-slate-400">产出：{selected.outputs}</div>
          </div>
          <button
            onClick={handleCopy}
            className="flex shrink-0 items-center gap-1.5 rounded-xl px-4 py-2 text-[12.5px] font-semibold text-white shadow-lg transition-all duration-150 active:translate-y-0.5"
            style={{
              background: copied
                ? 'linear-gradient(135deg, #059669, #10b981)'
                : 'linear-gradient(135deg, #4f46e5, #a855f7)',
              boxShadow: copied
                ? '0 8px 20px -6px rgba(16,185,129,0.45)'
                : '0 8px 20px -6px rgba(99,102,241,0.5)',
            }}
          >
            {copied ? (
              <ClipboardCheck style={{ width: 14, height: 14 }} />
            ) : (
              <Copy style={{ width: 14, height: 14 }} />
            )}
            {copied ? '已复制' : '复制提示词'}
          </button>
        </div>

        {/* 正文 */}
        <div className="flex min-h-0 flex-1 flex-col px-6 py-4">
          <div className="mb-2 flex items-center gap-1.5 text-[12px] text-slate-400">
            <Lightbulb style={{ width: 12, height: 12 }} />
            提示词全文（
            {'{占位符}'}
            {' '}处替换为你的实际内容，复制后粘贴到 QuantBot 对话）
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto rounded-2xl border border-slate-200/60 bg-white/80 shadow-sm">
            <div className="flex items-center gap-2 border-b border-slate-200/60 bg-slate-50/70 px-4 py-2 text-[11px] font-medium text-slate-400">
              <span className="h-2 w-2 rounded-full bg-indigo-300" />
              <span>提示词全文</span>
              <span className="ml-auto">{selected.title}</span>
            </div>
            <pre
              className="whitespace-pre-wrap p-4 text-[12.5px] leading-relaxed text-slate-700"
              style={{ fontFamily: 'var(--font-mono)' }}
            >
              {selected.body}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
};

export default PromptsLibrary;