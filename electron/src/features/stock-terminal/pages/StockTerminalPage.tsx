/** 个股终端主页：左栏检索+筛选+列表；右侧 指数条/智能标签/分数日历 + 信息 Tab；点股票名弹整合 K 线窗 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { CandlestickChart, Layers, CalendarDays, Activity, ChevronUp } from 'lucide-react';
import { Modal, Tooltip, message } from 'antd';
import { StockListItem, StockProfile } from '../types';
import { stockTerminalService, IndexQuote } from '../services/stockTerminalService';
import { StockSidebar, PositionKind, toPrefix } from '../components/StockSidebar';
import { TagStrip } from '../components/TagStrip';
import { ListFilters } from '../components/StockFilterPanel';
import { ScoreCalendar } from '../components/ScoreCalendar';
import { IndexMaCard } from '../components/IndexMaCard';
import { KlineWorkspace } from '../components/kline/KlineWorkspace';
import { OverviewTab } from '../components/OverviewTab';
import { L2FeatureCard } from '../components/L2FeatureCard';
import { FinancialsTab, ValuationTab, ChipFlowTab, MarginTab, SentimentTab, HoldersTab } from '../components/tabs/P2Tabs';
import { NewsTab } from '../components/tabs/NewsTab';
import { realTradingService, AccountInfo } from '../../../services/realTradingService';
import { authService } from '../../auth/services/authService';

type InfoTab = 'overview' | 'financials' | 'valuation' | 'chipflow' | 'margin' | 'sentiment' | 'holders' | 'news' | 'l2';

const TAB_META: { id: InfoTab; label: string }[] = [
  { id: 'overview', label: '概况' },
  { id: 'financials', label: '财务报表' },
  { id: 'valuation', label: '估值' },
  { id: 'chipflow', label: '筹码资金' },
  { id: 'margin', label: '融资融券' },
  { id: 'sentiment', label: '技术形态' },
  { id: 'holders', label: '股东分红' },
  { id: 'news', label: '个股资讯' },
  { id: 'l2', label: 'L2特征' },
];

export default function StockTerminalPage() {
  const [selected, setSelected] = useState<StockListItem | null>(null);
  const [profile, setProfile] = useState<StockProfile | null>(null);
  const [klineOpen, setKlineOpen] = useState(false);
  const [calendarCollapsed, setCalendarCollapsed] = useState(false);
  const [infoTab, setInfoTab] = useState<InfoTab>('overview');
  const [watchlist, setWatchlist] = useState<Set<string>>(new Set());
  const [onlyWatchlist, setOnlyWatchlist] = useState(false);
  const [tagFilter, setTagFilter] = useState<{ id: string; name: string } | null>(null);
  const [listFilters, setListFilters] = useState<ListFilters>({ excludeSt: true });
  const [listTotal, setListTotal] = useState(0);
  const [fullTotal, setFullTotal] = useState(0);
  const [listModels, setListModels] = useState<{ model_id: string; display_name?: string }[]>([]);
  const [quotes, setQuotes] = useState<IndexQuote[]>([]);
  const [signalDate, setSignalDate] = useState<string | undefined>();
  const [calRefresh, setCalRefresh] = useState(0);
  const [positions, setPositions] = useState<Map<string, PositionKind>>(new Map());

  // 概况随日历日期联动：历史日读该日 technical_indicators/valuation 快照
  useEffect(() => {
    if (!selected) { setProfile(null); return; }
    let cancelled = false;
    stockTerminalService.getProfile(selected.symbol, signalDate).then(p => { if (!cancelled) setProfile(p); });
    return () => { cancelled = true; };
  }, [selected, signalDate]);

  useEffect(() => {
    let cancelled = false;
    import('../../../services/researchService').then(({ researchService }) => {
      return researchService.getWatchlist(200).then(resp => {
        if (!cancelled) setWatchlist(new Set(resp.items.map(i => i.symbol)));
      });
    }).catch(() => { if (!cancelled) setWatchlist(new Set()); });
    return () => { cancelled = true; };
  }, []);

  // 行内星标：加/移自选（prefix 存储；自选视图下列表按新集合自动重拉）
  const onToggleWatch = useCallback(async (item: StockListItem, watched: boolean) => {
    const prefix = toPrefix(item.symbol);
    try {
      const { researchService } = await import('../../../services/researchService');
      if (watched) {
        await researchService.removeFromWatchlist(prefix);
      } else {
        await researchService.addToWatchlist(prefix, { stockName: item.name });
      }
      const next = new Set(watchlist);
      if (watched) next.delete(prefix); else next.add(prefix);
      setWatchlist(next);
      message.success(watched ? `已移出自选：${item.name}` : `已加入自选：${item.name}`);
    } catch {
      message.error('自选操作失败，请重试');
    }
  }, [watchlist]);

  // 模拟盘 + 实盘持仓 -> prefix 来源映射（列表行「模拟/实盘」徽标；自选视图同样生效）
  useEffect(() => {
    let cancelled = false;
    const extract = (acc: AccountInfo | null | undefined): string[] => {
      if (!acc || !acc.positions) return [];
      if (Array.isArray(acc.positions)) {
        return acc.positions.map(p => p.symbol).filter((s): s is string => !!s);
      }
      return Object.keys(acc.positions);
    };
    Promise.allSettled([
      realTradingService.getAccount('current', authService.getTenantId()),
      realTradingService.getSimulationAccount('current', authService.getTenantId()),
    ]).then(([real, sim]) => {
      if (cancelled) return;
      const map = new Map<string, PositionKind>();
      for (const s of extract(real.status === 'fulfilled' ? real.value : null)) {
        map.set(toPrefix(s), 'REAL');
      }
      for (const s of extract(sim.status === 'fulfilled' ? sim.value : null)) {
        const prefix = toPrefix(s);
        map.set(prefix, map.has(prefix) ? 'BOTH' : 'SIM');
      }
      setPositions(map);
    });
    return () => { cancelled = true; };
  }, []);

  // 指数快照（/market/quotes，本地 parquet 毫秒级）；signalDate 为历史日时返回该日行情
  useEffect(() => {
    let cancelled = false;
    stockTerminalService.getIndexQuotes(signalDate).then(qs => { if (!cancelled) setQuotes(qs); });
    return () => { cancelled = true; };
  }, [signalDate]);

  // 稳定引用：sideFilters 每次渲染都新建对象会触发侧栏 fetchList 无限重建（表格打架根源）
  const sideFilters = useMemo(() => ({
    ...listFilters,
    tagId: tagFilter?.id,
    tagName: tagFilter?.name,
  }), [listFilters, tagFilter]);

  const handleTotals = useCallback((total: number) => {
    setListTotal(total);
    // date 只影响基准日，不改变股票集合——不计入筛选激活判断
    const { date: _dateIgnored, ...rest } = listFilters;
    const hasActive = Object.values(rest).some(v => v != null && v !== '') || !!tagFilter;
    if (!hasActive) setFullTotal(total);
  }, [listFilters, tagFilter]);

  const up = (profile?.pct_change ?? 0) >= 0;

  return (
    <div className="w-full h-full bg-[#f8fafc] p-6 flex flex-col overflow-hidden font-sans box-border select-none">
      {/* 主一体化框架 (32px 大圆角) */}
      <div className="bg-white border border-gray-200 shadow-sm w-full h-full rounded-[32px] flex p-4 gap-4 overflow-hidden">

        {/* 左栏：当日指数条 + 检索/筛选/列表 */}
        <div className="flex flex-col gap-3 min-h-0 h-full">
          {/* 当日指数条：A股核心指数快照（红色涨绿色跌），置于股票列表上方 */}
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3 }}
            className="bg-slate-50/70 rounded-2xl border border-slate-200/80 shadow-2xs px-3 py-2 flex flex-col gap-1.5 shrink-0"
          >
            <div className="flex items-center gap-1.5">
              <Activity className="w-3 h-3 text-blue-500" />
              <span className="text-[11px] font-black text-slate-700">{signalDate ? '当日指数（历史）' : '今日指数'}</span>
              <span className="text-[9px] text-slate-400 font-bold">· 股票列表 分数降序</span>
              {quotes.length > 0 && <span className="text-[9px] text-slate-400 font-mono ml-auto">{quotes[0]?.trade_date ?? ''}</span>}
            </div>
            {quotes.length ? (
              <div className="grid grid-cols-4 gap-x-2 gap-y-1">
                {quotes.map(q => {
                  const qup = q.change_percent >= 0;
                  return (
                    <Tooltip key={q.symbol} title={`${q.name} ${q.price}`}>
                      <span className="flex flex-col leading-tight">
                        <span className="text-[12px] text-slate-400 font-bold truncate">{q.name}</span>
                        <span className="flex items-baseline gap-1 font-mono">
                          <span className="text-[14px] font-bold text-slate-700">{Number(q.price).toFixed(2)}</span>
                          <span className={`text-[12px] font-bold ${qup ? 'text-rose-600' : 'text-emerald-600'}`}>
                            {qup ? '+' : ''}{Number(q.change_percent).toFixed(2)}%
                          </span>
                        </span>
                      </span>
                    </Tooltip>
                  );
                })}
              </div>
            ) : (
              <span className="text-[10px] text-slate-400">本地指数行情加载中…</span>
            )}
          </motion.div>

          <StockSidebar
            selected={selected?.symbol ?? null}
            onSelect={setSelected}
            watchlistSymbols={watchlist}
            onToggleWatch={onToggleWatch}
            positions={positions}
            onlyWatchlist={onlyWatchlist}
            onOnlyWatchlist={setOnlyWatchlist}
            filters={sideFilters}
            onFiltersChange={setListFilters}
            onModels={setListModels}
            models={listModels}
            onTotals={handleTotals}
            onSignalDate={setSignalDate}
            fullTotal={fullTotal}
          />
        </div>

        {/* 右侧：股票信息 + 智能标签 + 分数日历 + 信息 Tabs */}
        <div className="flex-1 min-w-0 flex flex-col gap-3 min-h-0">

          {/* 顶部标头：三区布局 — 左 K线入口 / 中 股票信息居中 / 右 宽基概念标签 */}
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.02 }}
            className="bg-slate-50/70 rounded-2xl border border-slate-200/80 shadow-2xs px-4 py-3 flex items-center gap-4 shrink-0"
          >
            {/* 左：K 线入口 */}
            <button
              onClick={() => selected && setKlineOpen(true)}
              disabled={!selected}
              className="w-9 h-9 rounded-xl bg-blue-50 border border-blue-100 flex items-center justify-center text-blue-600 shrink-0 hover:opacity-80 disabled:opacity-40 transition-opacity"
              title="点击查看完整 K 线"
            >
              <CandlestickChart className="w-4 h-4" />
            </button>

            {/* 中：股票名称/代码/价格/板块，容器内居中对齐 */}
            <button
              onClick={() => selected && setKlineOpen(true)}
              disabled={!selected}
              className="flex flex-col items-center justify-center text-center gap-0.5 min-w-0 shrink-0 hover:opacity-80 disabled:opacity-40 transition-opacity"
              title="点击查看完整 K 线"
            >
              <div className="flex items-center justify-center gap-2">
                <span className="text-base font-black text-slate-800 truncate max-w-[160px]">{selected ? selected.name : '选择股票'}</span>
                {selected && <span className="text-[11px] font-mono text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded-lg border border-blue-100/80">{selected.symbol}</span>}
              </div>
              {selected && (
                <div className="flex items-center justify-center gap-2">
                  <span className={`text-[11px] font-mono font-bold ${up ? 'text-rose-500' : 'text-emerald-500'}`}>
                    {profile?.close?.toFixed(2) ?? '--'} {up ? '+' : ''}{(profile?.pct_change ?? 0).toFixed(2)}%
                  </span>
                  <span className="text-[10px] text-slate-400">{profile?.board} · {profile?.industry ?? '--'}</span>
                </div>
              )}
            </button>

            {/* 右：宽基归属与标识 + 概念板块（点击筛选左侧列表） */}
            {selected && profile && (
              <div className="flex-1 min-w-0 flex flex-col gap-1.5">
                <div className="flex flex-wrap gap-1.5 items-center">
                  <span className="text-[10px] font-bold text-slate-400 shrink-0" title="点击筛选成分股">宽基</span>
                  {profile.index_membership.map(m => {
                    const active = listFilters.indexCode === m.index_code;
                    return (
                      <button key={m.index_code} title={`按 ${m.index_name} 成分筛选`}
                        onClick={() => setListFilters({ ...listFilters, indexCode: active ? undefined : m.index_code, indexName: active ? undefined : m.index_name })}
                        className={`text-[11px] rounded px-2 py-0.5 font-bold transition-colors ${
                          active ? 'bg-violet-600 text-white' : 'bg-violet-50 text-violet-600 hover:bg-violet-100'
                        }`}>
                        {m.index_name}{m.weight != null ? ` ${m.weight.toFixed(1)}%` : ''}
                      </button>
                    );
                  })}
                  {profile.flags.is_st && <span className="text-[10px] bg-rose-50 text-rose-500 rounded px-1.5 py-0.5 font-bold">ST</span>}
                  {profile.flags.marginable && <span className="text-[10px] bg-blue-50 text-blue-600 rounded px-1.5 py-0.5 font-bold">融资融券</span>}
                  {profile.flags.sh_hk_connect && <span className="text-[10px] bg-cyan-50 text-cyan-600 rounded px-1.5 py-0.5 font-bold">沪港通</span>}
                  {!profile.index_membership.length && <span className="text-[10px] text-slate-300">无</span>}
                </div>
                <div className="flex flex-wrap gap-1.5 items-center">
                  <span className="text-[10px] font-bold text-slate-400 shrink-0" title="点击筛选同概念股">概念</span>
                  {profile.concepts.length
                    ? profile.concepts.map(c => {
                        const active = listFilters.concept === c;
                        return (
                          <button key={c} title={`按概念「${c}」筛选`}
                            onClick={() => setListFilters({ ...listFilters, concept: active ? undefined : c })}
                            className={`text-[11px] rounded px-2 py-0.5 transition-colors ${
                              active ? 'bg-amber-500 text-white font-bold' : 'bg-amber-50/70 text-amber-700 hover:bg-amber-100'
                            }`}>
                            {c}
                          </button>
                        );
                      })
                    : <span className="text-[10px] text-slate-300">无</span>}
                </div>
              </div>
            )}
          </motion.div>

          {/* 智能标签条：点击筛选按钮 -> 左侧列表按标签过滤；命中组合单独一行 */}
          {selected && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, delay: 0.04 }}
              className="bg-slate-50/70 rounded-2xl border border-slate-200/80 shadow-2xs shrink-0"
            >
              <TagStrip
                symbol={selected.symbol}
                onSelectStock={setSelected}
                onSelectTag={(t) => setTagFilter(tagFilter?.id === t.id ? null : t)}
                activeTagId={tagFilter?.id ?? null}
              />
            </motion.div>
          )}

          {/* 分数日历：个股历史推理分数（可折叠，展开 260px / 收起仅标题行） */}
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.06 }}
            className="bg-slate-50/70 rounded-2xl border border-slate-200/80 shadow-2xs flex flex-col overflow-hidden shrink-0"
            style={{ height: calendarCollapsed ? 36 : 260 }}
          >
            <div className="flex items-center justify-between px-4 py-2 border-b border-slate-200/80 shrink-0">
              <button onClick={() => setCalendarCollapsed(!calendarCollapsed)}
                className="flex items-center gap-1.5 text-left hover:opacity-70 transition-opacity">
                <CalendarDays className="w-3.5 h-3.5 text-indigo-500" />
                <span className="text-xs font-black text-slate-700">推理分数日历</span>
                <ChevronUp className={`w-3 h-3 text-slate-400 transition-transform ${calendarCollapsed ? 'rotate-180' : ''}`} />
              </button>
              {!calendarCollapsed && selected && (
                <div className="flex items-center gap-1.5 min-w-0 ml-2">
                  <span className="text-[10px] text-slate-400 font-mono truncate">
                    {selected.name} · 历史推理分数（红正绿负）
                  </span>
                  {signalDate && (
                    <button
                      onClick={() => setListFilters({ ...listFilters, date: undefined })}
                      title="当前列表基准信号日，点击回到最新"
                      className="shrink-0 text-[9px] font-mono font-bold text-amber-600 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5 hover:bg-amber-100 transition-colors"
                    >
                      {signalDate} ✕
                    </button>
                  )}
                </div>
              )}
            </div>
            {!calendarCollapsed && (
              <div className="flex-1 min-h-0 flex bg-white/60">
                {/* 日历：压缩到一半宽度 */}
                <div className="w-1/2 min-w-0 overflow-y-auto px-4 py-2 border-r border-slate-200/80">
                  <ScoreCalendar
                    symbol={selected?.symbol ?? ''}
                    selectedDate={signalDate}
                    modelId={listFilters.model}
                    refreshKey={calRefresh}
                    onInferred={() => setCalRefresh(calRefresh + 1)}
                    onBarClick={(d) => {
                      setSignalDate(d);  // 乐观高亮：立刻圈选点击日
                      setListFilters({ ...listFilters, date: d });
                    }}
                  />
                </div>
                {/* 大盘均线过滤：日历右侧 */}
                <div className="w-1/2 min-w-0 px-3 py-2">
                  <IndexMaCard date={signalDate} />
                </div>
              </div>
            )}
          </motion.div>

          {/* 信息 Tab 区 */}
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.08 }}
            className="bg-slate-50/70 rounded-2xl border border-slate-200/80 shadow-2xs flex flex-col overflow-hidden flex-1 min-h-0"
          >
            <div className="flex items-center justify-between px-4 py-2 border-b border-slate-200/80 bg-white/60">
              <div className="flex items-center gap-1 flex-wrap">
                {TAB_META.map(t => (
                  <button key={t.id} onClick={() => setInfoTab(t.id)}
                    className={`px-2.5 py-1 rounded-lg text-[11px] font-bold transition-colors ${infoTab === t.id ? 'bg-blue-600 text-white shadow-2xs' : 'text-slate-500 hover:text-slate-700 hover:bg-slate-100/70'}`}>
                    {t.label}
                  </button>
                ))}
              </div>
              {profile && (
                <div className="flex items-center gap-1.5 text-[10px] text-slate-400 shrink-0">
                  <Layers className="w-3 h-3 text-slate-300" />
                  本地 QuantDB · {profile.trade_date || '--'}
                </div>
              )}
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto p-3 bg-white/40">
              {infoTab === 'overview' && <OverviewTab profile={profile} />}
              {infoTab === 'financials' && selected && <FinancialsTab symbol={selected.symbol} asof={signalDate} />}
              {infoTab === 'valuation' && selected && <ValuationTab symbol={selected.symbol} asof={signalDate} />}
              {infoTab === 'chipflow' && selected && <ChipFlowTab symbol={selected.symbol} asof={signalDate} />}
              {infoTab === 'margin' && selected && <MarginTab symbol={selected.symbol} asof={signalDate} />}
              {infoTab === 'sentiment' && selected && <SentimentTab symbol={selected.symbol} asof={signalDate} />}
              {infoTab === 'holders' && selected && <HoldersTab symbol={selected.symbol} asof={signalDate} />}
              {infoTab === 'news' && selected && <NewsTab symbol={selected.symbol} />}
              {infoTab === 'l2' && selected && (
                <L2FeatureCard l2={profile?.l2_features ?? null} signalDate={profile?.signal_date ?? null} />
              )}
            </div>
          </motion.div>
        </div>
      </div>

      {/* 整合 K 线弹窗：左侧 K 线主体（融合全部功能），右侧竖排智能标签 */}
      <Modal
        open={klineOpen}
        onCancel={() => setKlineOpen(false)}
        footer={null}
        width={1440}
        destroyOnHidden
        centered
        title={null}
        styles={{ body: { height: 720, padding: 0 } }}
      >
        {selected && klineOpen && (
          <div className="h-full flex">
            {/* 左：K 线主体（周期/指标/指数/回放/信号/回测/多模型分数/模拟交易/参考线） */}
            <div className="flex-1 min-w-0">
              <KlineWorkspace stock={selected} profile={profile} height={640} onSelectStock={setSelected} />
            </div>
            {/* 右：竖排智能标签 */}
            <div className="w-44 shrink-0 border-l border-slate-100 overflow-y-auto">
              <TagStrip symbol={selected.symbol} onSelectStock={setSelected} vertical />
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
