/** 个股终端 — 搜索驱动展示：顶部搜索 + 左右布局（左上K线/左下推理分 + 右详情） */
import { useCallback, useEffect, useState } from 'react';
import { CandlestickChart, Search, Layers, Building2 } from 'lucide-react';
import { message } from 'antd';
import { PAGE_LAYOUT } from '../../../config/pageLayout';
import { StockListItem, StockProfile, KlineBar } from '../types';
import { stockTerminalService } from '../services/stockTerminalService';
import { StockSearchBar } from '../components/StockSearchBar';
import { InferenceScoreChart } from '../components/InferenceScoreChart';
import { KlineChart } from '../components/kline/KlineChart';
import { OverviewTab } from '../components/OverviewTab';
import { TagStrip } from '../components/TagStrip';
import { FinancialsTab, ValuationTab, ChipFlowTab, MarginTab, SentimentTab, HoldersTab } from '../components/tabs/P2Tabs';
import { NewsTab } from '../components/tabs/NewsTab';
import { L2FeatureCard } from '../components/L2FeatureCard';

type DetailTab = 'overview' | 'financials' | 'valuation' | 'chipflow' | 'margin' | 'sentiment' | 'holders' | 'news' | 'l2';

const DETAIL_TABS: { id: DetailTab; label: string }[] = [
  { id: 'overview', label: '概况' },
  { id: 'financials', label: '财务' },
  { id: 'valuation', label: '估值' },
  { id: 'chipflow', label: '筹码' },
  { id: 'margin', label: '融资' },
  { id: 'sentiment', label: '形态' },
  { id: 'holders', label: '股东' },
  { id: 'news', label: '资讯' },
  { id: 'l2', label: 'L2' },
];

const KLINE_PERIODS: { key: 'daily' | 'weekly' | 'monthly'; label: string }[] = [
  { key: 'daily', label: '日K' },
  { key: 'weekly', label: '周K' },
  { key: 'monthly', label: '月K' },
];

function resampleBars(bars: KlineBar[], period: 'weekly' | 'monthly'): KlineBar[] {
  const map = new Map<string, KlineBar[]>();
  for (const b of bars) {
    const key = period === 'weekly' ? weekKey(b.date) : b.date.slice(0, 7);
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(b);
  }
  return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([, grp]) => ({
    date: period === 'weekly' ? grp[grp.length - 1].date : `${grp[0].date.slice(0, 7)}-01`,
    open: grp[0].open,
    high: Math.max(...grp.map((g) => g.high)),
    low: Math.min(...grp.map((g) => g.low)),
    close: grp[grp.length - 1].close,
    volume: grp.reduce((s, g) => s + (g.volume ?? 0), 0),
  }));
}

function weekKey(date: string): string {
  const d = new Date(date + 'T00:00:00');
  const day = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - day);
  return d.toISOString().slice(0, 10);
}

export default function StockTerminalPage() {
  const [selected, setSelected] = useState<StockListItem | null>(null);
  const [profile, setProfile] = useState<StockProfile | null>(null);
  const [bars, setBars] = useState<KlineBar[]>([]);
  const [barsLoading, setBarsLoading] = useState(false);
  const [period, setPeriod] = useState<'daily' | 'weekly' | 'monthly'>('daily');
  const [signalDate, setSignalDate] = useState<string | undefined>(undefined);
  const [detailTab, setDetailTab] = useState<DetailTab>('overview');
  const [watchlist, setWatchlist] = useState<Set<string>>(new Set());

  // watchlist 仅为搜索下拉星标
  useEffect(() => {
    let cancelled = false;
    import('../../../services/researchService')
      .then(({ researchService }) =>
        researchService.getWatchlist(200).then((resp) => {
          if (!cancelled) setWatchlist(new Set(resp.items.map((i) => i.symbol)));
        }),
      )
      .catch(() => {
        if (!cancelled) setWatchlist(new Set());
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSelect = useCallback((item: StockListItem) => {
    setSelected(item);
    setDetailTab('overview');
    setSignalDate(undefined);
  }, []);

  // 详情随选中+信号日联动
  useEffect(() => {
    if (!selected) {
      setProfile(null);
      return;
    }
    let cancelled = false;
    stockTerminalService.getProfile(selected.symbol, signalDate).then((p) => {
      if (!cancelled) setProfile(p);
    });
    return () => {
      cancelled = true;
    };
  }, [selected, signalDate]);

  // K线随选中+周期联动
  useEffect(() => {
    if (!selected) {
      setBars([]);
      return;
    }
    let cancelled = false;
    setBarsLoading(true);
    stockTerminalService
      .getDailyKline(selected.symbol, 250)
      .then((items) => {
        if (cancelled) return;
        if (period !== 'daily' && items.length) {
          setBars(resampleBars(items, period));
        } else {
          setBars(items);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setBars([]);
          message.error('K线加载失败');
        }
      })
      .finally(() => {
        if (!cancelled) setBarsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected, period]);

  const up = (profile?.pct_change ?? selected?.pct_change ?? 0) >= 0;

  return (
    <div className={PAGE_LAYOUT.outerClass}>
      <div className={PAGE_LAYOUT.frameClass}>
        {/* 顶栏 */}
        <header className={PAGE_LAYOUT.headerClass} style={{ height: `${PAGE_LAYOUT.headerHeight}px` }}>
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-10 h-10 bg-gradient-to-br from-blue-500 to-violet-500 rounded-2xl flex items-center justify-center shadow-lg shrink-0">
              <CandlestickChart className="w-5 h-5 text-white" />
            </div>
            <div className="flex items-center gap-2.5 ml-1 min-w-0">
              <h1 className="text-xl font-bold text-slate-800 tracking-tight">个股终端</h1>
              <div className="h-4 w-[1px] bg-slate-200 self-center shrink-0" />
              <span className="text-sm font-medium text-slate-500 hidden sm:inline truncate">搜索个股 · K线 · 推理分 · 详情</span>
            </div>
          </div>
          {selected && (
            <div className="hidden md:flex items-center gap-2 text-[11px] text-slate-500 shrink-0">
              <span className="font-mono font-bold text-slate-700">{selected.symbol}</span>
              <span className="text-slate-300">·</span>
              <span className={`font-mono font-bold ${up ? 'text-rose-500' : 'text-emerald-500'}`}>
                {profile?.close?.toFixed(2) ?? selected.close?.toFixed(2) ?? '--'}
              </span>
            </div>
          )}
        </header>

        {/* 顶部搜索条 */}
        <div className="shrink-0 bg-white border-b border-gray-200 px-6 py-3">
          <StockSearchBar onSelect={handleSelect} watchlistSymbols={watchlist} />
        </div>

        {/* 主体 */}
        {!selected ? (
          <div className="flex-1 flex flex-col items-center justify-center gap-3 bg-gray-50/50 p-8 text-center">
            <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-500 to-violet-500 flex items-center justify-center shadow-md">
              <Search className="w-6 h-6 text-white" />
            </div>
            <div className="text-sm font-bold text-slate-700">在上方搜索框输入代码或名称开始</div>
            <div className="text-xs text-slate-400 max-w-[420px] leading-relaxed">
              不会预加载全量列表，输入关键词后联想最相关的 8 只股票；选中后左侧展示历史K线与默认模型推理分，右侧展示个股详情。
            </div>
          </div>
        ) : (
          <div className="flex flex-1 min-h-0 overflow-hidden bg-gray-50/50 p-4 gap-4">
            {/* 左侧：K线 + 推理分 */}
            <div className="flex-1 min-w-0 flex flex-col gap-4 overflow-hidden">
              {/* K线卡 */}
              <div className="flex-1 min-h-0 flex flex-col rounded-3xl bg-white border border-purple-100/80 shadow-sm overflow-hidden">
                <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-100 bg-slate-50/60 shrink-0">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-xs font-black text-slate-700 truncate">
                      {selected.name} <span className="font-mono text-[11px] text-slate-400">{selected.symbol}</span>
                    </span>
                    {profile && (
                      <span className={`text-[11px] font-mono font-bold ${up ? 'text-rose-500' : 'text-emerald-500'}`}>
                        {profile.close?.toFixed(2) ?? '--'} {up ? '+' : ''}{(profile.pct_change ?? 0).toFixed(2)}%
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1 p-1 bg-slate-100 rounded-full shrink-0">
                    {KLINE_PERIODS.map((p) => (
                      <button
                        key={p.key}
                        onClick={() => setPeriod(p.key)}
                        className={`px-3 py-1 rounded-full text-[11px] font-bold transition-colors ${period === p.key ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
                      >
                        {p.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="flex-1 min-h-0 p-2">
                  {barsLoading ? (
                    <div className="h-full flex items-center justify-center text-xs text-slate-400">K线加载中…</div>
                  ) : bars.length ? (
                    <KlineChart bars={bars} config={{ ma: true, boll: false, subplots: ['vol'] }} overlays={[]} height={320} />
                  ) : (
                    <div className="h-full flex items-center justify-center text-xs text-slate-400">暂无K线</div>
                  )}
                </div>
              </div>

              {/* 推理分折线卡 */}
              <div className="h-[280px] shrink-0 rounded-3xl bg-white border border-purple-100/80 shadow-sm overflow-hidden flex flex-col p-3">
                <InferenceScoreChart symbol={selected.symbol} selectedDate={signalDate} onPointClick={setSignalDate} height={250} />
              </div>
            </div>

            {/* 右侧：详情 */}
            <div className="w-[380px] shrink-0 flex flex-col rounded-3xl bg-white border border-slate-200/80 shadow-sm overflow-hidden">
              {/* 详情头部：名称 + 标签 */}
              <div className="px-4 py-3 border-b border-slate-100 bg-slate-50/60 shrink-0">
                <div className="flex items-center gap-2">
                  <Building2 className="w-3.5 h-3.5 text-blue-500 shrink-0" />
                  <span className="text-[13px] font-black text-slate-800 truncate">{profile?.name ?? selected.name}</span>
                  <span className="text-[10px] font-mono text-slate-400">{profile?.symbol ?? selected.symbol}</span>
                  <span className={`ml-auto text-[10px] font-mono font-bold ${up ? 'text-rose-500' : 'text-emerald-500'}`}>
                    {profile?.board ?? selected.board ?? ''}
                  </span>
                </div>
                {profile && (
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {profile.index_membership.slice(0, 3).map((m) => (
                      <span key={m.index_code} className="text-[10px] px-1.5 py-0.5 rounded bg-violet-50 text-violet-600 font-bold">
                        {m.index_name}
                      </span>
                    ))}
                    {profile.concepts.slice(0, 3).map((c) => (
                      <span key={c} className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700">
                        {c}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* 智能标签 */}
              {selected && (
                <div className="shrink-0 border-b border-slate-100">
                  <TagStrip symbol={selected.symbol} onSelectStock={setSelected} />
                </div>
              )}

              {/* Tab */}
              <div className="flex items-center gap-1 px-2 py-2 border-b border-slate-100 bg-white shrink-0 overflow-x-auto">
                {DETAIL_TABS.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => setDetailTab(t.id)}
                    className={`shrink-0 px-2.5 py-1 rounded-lg text-[11px] font-bold transition-colors ${detailTab === t.id ? 'bg-blue-600 text-white shadow-sm' : 'text-slate-500 hover:bg-slate-100'}`}
                  >
                    {t.label}
                  </button>
                ))}
                {profile && (
                  <span className="ml-auto flex items-center gap-1 text-[10px] text-slate-400 shrink-0">
                    <Layers className="w-3 h-3" />
                    {profile.trade_date}
                  </span>
                )}
              </div>

              <div className="flex-1 min-h-0 overflow-y-auto p-3 bg-white/40 custom-scrollbar">
                {detailTab === 'overview' && <OverviewTab profile={profile} />}
                {detailTab === 'financials' && <FinancialsTab symbol={selected.symbol} asof={signalDate} />}
                {detailTab === 'valuation' && <ValuationTab symbol={selected.symbol} asof={signalDate} />}
                {detailTab === 'chipflow' && <ChipFlowTab symbol={selected.symbol} asof={signalDate} />}
                {detailTab === 'margin' && <MarginTab symbol={selected.symbol} asof={signalDate} />}
                {detailTab === 'sentiment' && <SentimentTab symbol={selected.symbol} asof={signalDate} />}
                {detailTab === 'holders' && <HoldersTab symbol={selected.symbol} asof={signalDate} />}
                {detailTab === 'news' && <NewsTab symbol={selected.symbol} />}
                {detailTab === 'l2' && <L2FeatureCard l2={profile?.l2_features ?? null} signalDate={profile?.signal_date ?? null} />}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
