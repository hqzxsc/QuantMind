/**
 * 因子报告（Alphalens 式）——技能中心「因子报告」页签
 *
 * 三个问题一屏回答：
 *  1) 分位收益：把股票按因子值分 10 组，Q10−Q1 是不是单调、价差多大（区分真因子与噪声）
 *  2) 换手：这组因子每天换掉多少仓位（决定交易成本能否吃得住）
 *  3) 相关性：它是不是别人的复制品（|ρ|>0.9 的因子只该留一个）
 *
 * 数据：backend/services/engine/factor_report（快照 + 按需明细），
 * 快照由 backend/scripts/build_factor_report.py 生成。
 */

import React, { useEffect, useMemo, useState } from 'react';
import { AlertCircle, RefreshCw, Sparkles } from 'lucide-react';
import { FactorRankList } from './FactorRankList';
import { FactorDetailCharts } from './FactorDetailCharts';
import { FactorCorrelationHeatmap } from './FactorCorrelationHeatmap';
import {
  getFactorCorrelation,
  getFactorDetail,
  getFactorRelated,
  getFactorSummary,
} from '../../services/factorReportService';
import type {
  FactorCorrelation,
  FactorDetail,
  FactorRelated,
  FactorReportMeta,
  FactorSummary,
} from '../../types/factorReport';

function MetricTile({ label, value, tone = 'slate', title }: {
  label: string;
  value: string;
  tone?: 'slate' | 'up' | 'down';
  title?: string;
}) {
  const color = tone === 'up' ? 'text-rose-600' : tone === 'down' ? 'text-emerald-600' : 'text-slate-800';
  return (
    <div className="bg-white rounded-xl border border-slate-200/80 px-3 py-2 min-w-[86px]" title={title}>
      <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wide">{label}</div>
      <div className={`text-sm font-black font-mono leading-tight mt-0.5 ${color}`}>{value}</div>
    </div>
  );
}

export const FactorReportPanel: React.FC = () => {
  const [factors, setFactors] = useState<FactorSummary[]>([]);
  const [meta, setMeta] = useState<FactorReportMeta | null>(null);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<FactorDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [related, setRelated] = useState<FactorRelated | null>(null);
  const [correlation, setCorrelation] = useState<FactorCorrelation | null>(null);
  const [corrLoading, setCorrLoading] = useState(false);

  // 快照摘要（一次拉全量 429，前端做筛选/搜索）
  const loadSummary = async (pickFirst = false) => {
    setListLoading(true);
    try {
      const res = await getFactorSummary({ sort: 'abs_ic' });
      if (!res.available) {
        setUnavailable(res.reason || '快照尚未生成');
        setFactors([]);
        return;
      }
      setUnavailable(null);
      setFactors(res.factors || []);
      setMeta(res.meta || null);
      if (pickFirst && (res.factors || []).length > 0) {
        // 函数式更新在 tsc 下会报类型错（历史坑），改用传值：仅在尚未选中时自动选第一个
        if (!selected) setSelected(res.factors[0].name);
      }
    } catch (e) {
      setUnavailable(e instanceof Error ? e.message : '因子报告加载失败');
    } finally {
      setListLoading(false);
    }
  };

  useEffect(() => {
    void loadSummary(true);
  }, []);

  // 选中因子变化 → 明细 + 相关因子 → 相关性矩阵
  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setDetailLoading(true);
    setDetail(null);
    getFactorDetail(selected)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => !cancelled && setDetail(null))
      .finally(() => !cancelled && setDetailLoading(false));

    setCorrLoading(true);
    getFactorRelated(selected, 7)
      .then(async (r) => {
        if (cancelled) return;
        setRelated(r);
        const names = [selected, ...r.related.map((x) => x.name)];
        const corr = await getFactorCorrelation(names);
        if (!cancelled) setCorrelation(corr);
      })
      .catch(() => {
        if (!cancelled) {
          setRelated(null);
          setCorrelation(null);
        }
      })
      .finally(() => !cancelled && setCorrLoading(false));

    return () => {
      cancelled = true;
    };
  }, [selected]);

  const current = useMemo(() => factors.find((f) => f.name === selected) || null, [factors, selected]);

  /** 单调性是 +1 表示因子值越大收益越高；配合 IC 符号给出人话解释 */
  const monotoneText = current?.monotonicity == null
    ? '—'
    : `${current.monotonicity > 0 ? '+' : ''}${current.monotonicity.toFixed(2)}`;

  return (
    <div className="flex h-full min-h-0 bg-gray-50/40">
      {/* 左：因子榜 */}
      <aside className="w-[280px] shrink-0 border-r border-gray-200 bg-white flex flex-col min-h-0">
        <FactorRankList
          factors={factors}
          selected={selected}
          onSelect={setSelected}
          loading={listLoading}
        />
      </aside>

      {/* 右：详情 */}
      <main className="flex-1 min-w-0 flex flex-col gap-3 p-3 min-h-0">
        {/* 顶：快照信息 + 因子指标 */}
        <div className="flex items-center gap-3 flex-wrap shrink-0">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 flex items-center justify-center shadow-sm">
              <Sparkles className="w-4 h-4 text-white" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-black text-slate-800">{selected || '因子报告'}</span>
                {current && (
                  <span className="rounded-full bg-indigo-50 border border-indigo-100 px-2 py-[1px] text-[10px] font-bold text-indigo-600">
                    {current.library}
                  </span>
                )}
              </div>
              <span className="text-[10px] text-slate-400 font-mono">
                {meta
                  ? `A股全市场 · ${meta.horizon.replace('fwd_ret_', 'T+')} 前瞻 · ${meta.start}~${meta.end} · ${meta.n_dates} 个交易日 · 快照 ${meta.generated_at}`
                  : '加载中…'}
              </span>
            </div>
          </div>

          <button
            onClick={() => void loadSummary(false)}
            className="ml-auto flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1 text-[11px] font-bold text-slate-500 hover:text-indigo-600 hover:border-indigo-200"
            title="重新读取快照（快照由服务器脚本定期生成）"
          >
            <RefreshCw className={`w-3 h-3 ${listLoading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>

        {unavailable ? (
          <div className="flex-1 min-h-0 flex flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-amber-200 bg-amber-50/50 text-center px-6">
            <AlertCircle className="w-5 h-5 text-amber-500" />
            <span className="text-xs font-bold text-amber-700">因子报告快照不可用</span>
            <span className="text-[11px] text-amber-600/90 leading-5 max-w-xl">{unavailable}</span>
          </div>
        ) : (
          <>
            {/* 指标条 */}
            <div className="flex items-center gap-2 flex-wrap shrink-0">
              <MetricTile
                label="IC 均值"
                value={current ? `${current.ic_mean >= 0 ? '+' : ''}${current.ic_mean.toFixed(4)}` : '—'}
                tone={current && current.ic_mean >= 0 ? 'up' : 'down'}
                title="横截面秩相关（Spearman）的日均值，|IC|>0.03 通常算有效"
              />
              <MetricTile
                label="ICIR"
                value={current ? current.icir.toFixed(3) : '—'}
                tone={current && current.icir >= 0 ? 'up' : 'down'}
                title="IC 均值 / IC 标准差，衡量稳定性；|ICIR|>0.3 较好"
              />
              <MetricTile label="t 值" value={current ? current.t_value.toFixed(1) : '—'} title="ICIR × √样本数；|t|>2 显著" />
              <MetricTile label="IC 胜率" value={current ? `${(current.win_rate * 100).toFixed(1)}%` : '—'} title="IC>0 的交易日占比" />
              <MetricTile
                label="多空价差"
                value={current ? `${current.ls_mean >= 0 ? '+' : ''}${(current.ls_mean * 100).toFixed(3)}%` : '—'}
                tone={current && current.ls_mean >= 0 ? 'up' : 'down'}
                title="Q10 组 − Q1 组的平均前瞻收益（每个调仓周期）"
              />
              <MetricTile
                label="单边换手"
                value={current ? `${(current.turnover * 100).toFixed(0)}%` : '—'}
                title="十分位组合每日成员变动比例，直接决定交易成本"
              />
              <MetricTile label="单调性" value={monotoneText} title="分位序号与分位收益的秩相关，±1 = 完美单调" />
            </div>

            <FactorDetailCharts detail={detail} loading={detailLoading} />
            <div className="h-[240px] shrink-0 flex">
              <div className="flex-1 min-w-0">
                <FactorCorrelationHeatmap
                  correlation={correlation}
                  related={related}
                  loading={corrLoading}
                  onPick={setSelected}
                />
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
};
