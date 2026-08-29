/**
 * 提示词库 — 技能中心上区
 *
 * 数据来自 prompts.generated.ts（源文件：仓库根目录 prompts/*.md）。
 * 按分类分组渲染卡片，支持一键复制提示词到 QuantBot（QwenPaw）。
 */

import React, { useMemo, useState } from 'react';
import { Check, ChevronDown, ChevronRight, Copy, Sparkles } from 'lucide-react';
import { PROMPTS, type PromptMeta } from '../prompts.generated';

const CATEGORY_ORDER = ['环境初始化', '研究分析', '策略·因子·模型·回测', '交易', '平台运营', '券商 SDK'];

const CATEGORY_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  研究分析: { bg: '#eff6ff', border: '#bfdbfe', text: '#1d4ed8' },
  '策略·因子·模型·回测': { bg: '#f5f3ff', border: '#ddd6fe', text: '#6d28d9' },
  交易: { bg: '#fff7ed', border: '#fed7aa', text: '#c2410c' },
  平台运营: { bg: '#f0fdf4', border: '#bbf7d0', text: '#15803d' },
  '券商 SDK': { bg: '#f8fafc', border: '#e2e8f0', text: '#475569' },
};

const PromptCard: React.FC<{ prompt: PromptMeta }> = ({ prompt }) => {
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const color = CATEGORY_COLORS[prompt.category] ?? CATEGORY_COLORS['券商 SDK'];

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(prompt.body);
    } catch {
      // Electron/旧浏览器兜底
      const textarea = document.createElement('textarea');
      textarea.value = prompt.body;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div
      className="flex flex-col rounded-2xl border bg-white p-4 transition-shadow hover:shadow-md"
      style={{ borderColor: '#e2e8f0' }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span
              className="rounded-full px-2 py-0.5 text-[11px] font-medium"
              style={{ background: color.bg, border: `1px solid ${color.border}`, color: color.text }}
            >
              {prompt.category}
            </span>
          </div>
          <div className="mt-2 truncate text-[15px] font-semibold text-slate-800">{prompt.title}</div>
        </div>
        <Sparkles style={{ width: 16, height: 16, color: '#94a3b8', flexShrink: 0, marginTop: 2 }} />
      </div>

      <div className="mt-2 line-clamp-2 text-[12.5px] leading-relaxed text-slate-500">{prompt.description}</div>
      <div className="mt-1.5 truncate text-[11.5px] text-slate-400">产出：{prompt.outputs}</div>

      {expanded && (
        <pre className="mt-3 max-h-52 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-50 p-3 text-[11.5px] leading-relaxed text-slate-600">
          {prompt.body}
        </pre>
      )}

      <div className="mt-3 flex items-center gap-2">
        <button
          onClick={handleCopy}
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12.5px] font-medium text-white transition-colors"
          style={{ background: copied ? '#16a34a' : '#2563eb' }}
        >
          {copied ? <Check style={{ width: 13, height: 13 }} /> : <Copy style={{ width: 13, height: 13 }} />}
          {copied ? '已复制，去 QuantBot 粘贴' : '复制提示词'}
        </button>
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-[12.5px] text-slate-500 hover:bg-slate-50"
        >
          {expanded ? <ChevronDown style={{ width: 13, height: 13 }} /> : <ChevronRight style={{ width: 13, height: 13 }} />}
          {expanded ? '收起' : '预览'}
        </button>
      </div>
    </div>
  );
};

const PromptsLibrary: React.FC = () => {
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

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[16px] font-semibold text-slate-800">提示词库</h2>
        <span className="text-[12px] text-slate-400">
          复制提示词 → 粘贴到 QuantBot 对话 → AI 自动读取对应技能执行，报告自动落盘到下方报告档案
        </span>
      </div>
      {grouped.map(([category, prompts]) => (
        <div key={category} className="flex flex-col gap-3">
          <div className="text-[13px] font-medium text-slate-500">{category}</div>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-3">
            {prompts.map((p) => (
              <PromptCard key={p.name} prompt={p} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
};

export default PromptsLibrary;
