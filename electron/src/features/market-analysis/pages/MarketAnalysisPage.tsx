import React, { useState, useEffect } from 'react';
import { Sparkles, Search, Activity, Layers, Network, TrendingUp, BarChart3, Clock, RefreshCw, Zap } from 'lucide-react';
import { Input, Spin, message } from 'antd';
import { BroadMarketHeader, IndexItem } from '../components/BroadMarketHeader';
import { ShenwanHeatmapChart } from '../components/ShenwanHeatmapChart';
import { CapitalFlowSankeyChart } from '../components/CapitalFlowSankeyChart';
import { StockMoneyFlowTable, StockMoneyFlowItem } from '../components/StockMoneyFlowTable';
import { MarketBreadthCard } from '../components/MarketBreadthCard';
import { TagLookupPanel } from '../components/TagLookupPanel';
import { CapitalFlowHorizontalBarChart, FlowItem } from '../components/CapitalFlowHorizontalBarChart';
import { Tag as TagIcon } from 'lucide-react';
import { SERVICE_ENDPOINTS } from '../../../config/services';

const MARKET_ANALYSIS_API = `${SERVICE_ENDPOINTS.USER_SERVICE}/market-analysis`;

interface MarketBreadthData {
  trade_date: string;
  advance_count: number;
  decline_count: number;
  flat_count: number;
  limit_up_count: number;
  limit_down_count: number;
  total_turnover_yi: number;
  profit_effect: number;
  limit_up_broken_ratio: number;
}

export const MarketAnalysisPage: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [activeTab, setActiveTab] = useState('panorama'); // 默认进入大盘全景看板
  const [searchQuery, setSearchQuery] = useState('');
  const [indices, setIndices] = useState<IndexItem[]>([]);
  const [stockFlows, setStockFlows] = useState<StockMoneyFlowItem[]>([]);
  const [breadth, setBreadth] = useState<MarketBreadthData | null>(null);
  const [dataDate, setDataDate] = useState<string>('');
  const [heatmapData, setHeatmapData] = useState<any[]>([]);
  const [sankeyData, setSankeyData] = useState<{ nodes: any[]; links: any[] } | null>(null);
  const [updateTime, setUpdateTime] = useState<string>('');
  const [treemapData, setTreemapData] = useState<any[]>([]);

  // 🎯 多周期资金流向专属状态
  const [period, setPeriod] = useState<'1d' | '3d' | '5d' | '10d' | '20d'>('5d');
  const [flowDimension, setFlowDimension] = useState<'sector' | 'stock'>('sector');
  const [categoryMode, setCategoryMode] = useState<'shenwan' | 'concept'>('shenwan');
  const [chartViewMode, setChartViewMode] = useState<'bar' | 'treemap'>('bar');
  const [selectedFlowItem, setSelectedFlowItem] = useState<FlowItem | null>(null);

  useEffect(() => {
    fetchMarketData();
  }, []);

  // 矩形树图数据：随分类模式切换拉取对应热力图
  useEffect(() => {
    if (activeTab !== 'flow-bar' || chartViewMode !== 'treemap') return;
    const token = localStorage.getItem('access_token') || '';
    fetch(`${MARKET_ANALYSIS_API}/heatmap?category=${categoryMode}`, { headers: { Authorization: `Bearer ${token}` } })
      .then((res) => (res.ok ? res.json() : null))
      .then((d) => setTreemapData(d?.items && d.items.length > 0 ? d.items : []))
      .catch(() => setTreemapData([]));
  }, [activeTab, chartViewMode, categoryMode]);

  const fetchMarketData = async () => {
    setLoading(true);
    const token = localStorage.getItem('access_token') || '';
    const headers = token ? { Authorization: `Bearer ${token}` } : {};

    try {
      const [resIdx, resStock, resBreadth, resHeatmap, resSankey] = await Promise.all([
        fetch(`${MARKET_ANALYSIS_API}/indices/overview`, { headers }),
        fetch(`${MARKET_ANALYSIS_API}/money-flow/stocks?limit=20`, { headers }),
        fetch(`${MARKET_ANALYSIS_API}/breadth`, { headers }),
        fetch(`${MARKET_ANALYSIS_API}/heatmap?category=shenwan`, { headers }),
        fetch(`${MARKET_ANALYSIS_API}/money-flow/sankey`, { headers }),
      ]);

      if (resIdx.ok) {
        const idxData = await resIdx.json();
        if (Array.isArray(idxData) && idxData.length > 0) {
          setIndices(idxData);
        }
      }
      if (resStock.ok) {
        const stockData = await resStock.json();
        if (Array.isArray(stockData) && stockData.length > 0) {
          setStockFlows(stockData);
        }
      }
      if (resBreadth.ok) {
        const bData = await resBreadth.json();
        setDataDate(bData?.trade_date || '');
        if (bData && bData.total_turnover_yi > 0) {
          setBreadth(bData);
        }
      }
      if (resHeatmap.ok) {
        const hData = await resHeatmap.json();
        if (hData && Array.isArray(hData.items) && hData.items.length > 0) {
          setHeatmapData(hData.items);
        }
      }
      if (resSankey.ok) {
        const sData = await resSankey.json();
        if (sData && sData.nodes && sData.nodes.length > 0) {
          setSankeyData(sData);
        }
      }

      const now = new Date();
      setUpdateTime(now.toTimeString().split(' ')[0]);
    } catch (e) {
      console.warn('后端市场接口连接出现异常，保留现有渲染状态', e);
    } finally {
      setLoading(false);
    }
  };

  /** 手动触发读取 QuantDB 数据，SSE 逐步流式推送，边分析边渲染 */
  const handleTriggerAnalysis = async () => {
    setAnalyzing(true);
    const token = localStorage.getItem('access_token') || '';
    const headers = token ? { Authorization: `Bearer ${token}` } : {};

    try {
      const res = await fetch(`${MARKET_ANALYSIS_API}/analyze/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...headers,
        },
      });

      if (!res.ok || !res.body) {
        message.info('已触发市场分析');
        await fetchMarketData();
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      const handleEvent = (eventType: string, evt: any) => {
        const data = evt?.data;
        switch (eventType) {
          case 'indices':
            if (Array.isArray(data?.indices) && data.indices.length > 0) {
              setIndices(data.indices);
            }
            break;
          case 'breadth':
            if (data?.breadth && data.breadth.total_turnover_yi > 0) {
              setBreadth(data.breadth);
              setDataDate(data.breadth.trade_date || '');
            }
            break;
          case 'heatmap':
            if (Array.isArray(data?.heatmap) && data.heatmap.length > 0) {
              setHeatmapData(data.heatmap);
            }
            break;
          case 'sankey':
            if (data?.sankey && data.sankey.nodes && data.sankey.nodes.length > 0) {
              setSankeyData(data.sankey);
            }
            break;
          case 'stock_flow':
            if (Array.isArray(data?.stock_flow) && data.stock_flow.length > 0) {
              setStockFlows(data.stock_flow);
            }
            break;
          default:
            break;
        }
        setUpdateTime(new Date().toTimeString().split(' ')[0]);
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE 消息以空行分隔；解析完整块后保留尾部未闭合部分
        const blocks = buffer.split('\n\n');
        buffer = blocks.pop() || '';
        for (const block of blocks) {
          if (!block.trim()) continue;
          let eventType = '';
          let dataStr = '';
          for (const line of block.split('\n')) {
            const l = line.trim();
            if (l.startsWith('event:')) {
              eventType = l.slice(6).trim();
            } else if (l.startsWith('data:')) {
              dataStr += l.slice(5).trim();
            }
          }
          if (!dataStr) continue;
          let evt: any;
          try {
            evt = JSON.parse(dataStr);
          } catch {
            continue;
          }
          handleEvent(eventType, evt);
        }
      }
      message.success('市场分析完成，已同步 QuantDB 最新数据');
    } catch (e) {
      console.error('市场分析触发失败:', e);
      message.error('触发市场分析失败，请检查后端服务状态');
    } finally {
      setAnalyzing(false);
    }
  };

  const navTabs = [
    { id: 'panorama', label: '大盘全景看板', icon: Activity },
    { id: 'flow-bar', label: '多周期资金流向', icon: BarChart3 },
    { id: 'money-flow', label: '板块资金链', icon: Network },
    { id: 'stock-flow', label: '个股资金流向', icon: TrendingUp },
    { id: 'tag-lookup', label: '标签双向查询', icon: TagIcon },
  ];

  const periodOptions: Array<{ id: '1d' | '3d' | '5d' | '10d' | '20d'; label: string }> = [
    { id: '1d', label: '1日' },
    { id: '3d', label: '3日' },
    { id: '5d', label: '5日' },
    { id: '10d', label: '10日' },
    { id: '20d', label: '20日' },
  ];

  return (
    <div
      className="w-full h-full overflow-y-auto bg-slate-50/60 px-5 pt-4 pb-28 flex flex-col gap-2.5 font-sans"
      style={{
        WebkitMaskImage: 'linear-gradient(to bottom, transparent 0%, rgba(0,0,0,0.25) 10px, rgba(0,0,0,0.75) 20px, black 32px, black calc(100% - 28px), rgba(0,0,0,0.75) calc(100% - 16px), rgba(0,0,0,0.25) calc(100% - 8px), transparent 100%)',
        maskImage: 'linear-gradient(to bottom, transparent 0%, rgba(0,0,0,0.25) 10px, rgba(0,0,0,0.75) 20px, black 32px, black calc(100% - 28px), rgba(0,0,0,0.75) calc(100% - 16px), rgba(0,0,0,0.25) calc(100% - 8px), transparent 100%)',
      }}
    >
        {/* 🌟 紧凑 Banner 顶栏 */}
        <div className="relative rounded-2xl bg-gradient-to-r from-purple-100/90 via-indigo-50/80 to-purple-50/90 text-slate-900 px-5 py-2.5 shadow-xs border border-purple-200/60 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="px-3 py-0.5 rounded-full bg-purple-600/10 text-purple-700 border border-purple-200 text-xs font-extrabold font-mono flex items-center gap-1.5 shadow-2xs whitespace-nowrap">
              <Sparkles className="w-3.5 h-3.5 text-purple-600" />
              <span>QuantDB 2.0 数据引擎</span>
            </span>
            <h1 className="text-base font-extrabold tracking-tight bg-gradient-to-r from-purple-950 via-indigo-900 to-slate-900 bg-clip-text text-transparent whitespace-nowrap">
              全市场多维数据分析与资金链全景
            </h1>
          </div>

          <div className="flex items-center gap-2.5 flex-shrink-0">
            {dataDate && (
              <span
                title="行情数据对应的最新交易日"
                className="hidden md:flex items-center gap-1.5 px-3 py-1 rounded-full bg-white/80 text-slate-600 border border-purple-200/70 text-[11px] font-extrabold font-mono whitespace-nowrap shadow-2xs"
              >
                <Clock className="w-3 h-3 text-purple-500" />
                <span>数据日期:</span>
                <span className="text-purple-700">{dataDate}</span>
              </span>
            )}
            <div className="w-60">
              <Input
                prefix={<Search className="w-3.5 h-3.5 text-purple-400 mr-1.5" />}
                placeholder="全局搜索行业或股票..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="rounded-xl border border-purple-200/80 bg-white text-xs text-slate-800 placeholder-slate-400 py-1.5 px-3.5 shadow-2xs hover:border-purple-300 focus:bg-white focus:ring-2 focus:ring-purple-100 transition-all"
              />
            </div>

            {/* 🎯 手动市场分析触发按钮 */}
            <button
              onClick={handleTriggerAnalysis}
              disabled={analyzing}
              className={`flex items-center gap-1.5 px-4 py-1.5 rounded-full text-xs font-extrabold shadow-md transition-all duration-200 whitespace-nowrap cursor-pointer ${
                analyzing
                  ? 'bg-purple-400 text-white cursor-wait opacity-80'
                  : 'bg-gradient-to-r from-purple-600 via-indigo-600 to-purple-700 hover:from-purple-500 hover:to-indigo-500 active:scale-95 text-white shadow-purple-600/30'
              }`}
              title="从本地 QuantDB 读取最新数据并重新执行全市场多维分析"
            >
              {analyzing ? (
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Zap className="w-3.5 h-3.5 text-amber-300 fill-amber-300" />
              )}
              <span>{analyzing ? '正在分析…' : '市场分析'}</span>
            </button>
          </div>
        </div>

        {/* 📊 五大核心指数快照 */}
        <BroadMarketHeader indices={indices} loading={loading} />

        {/* 📌 功能切换 Tabs 导航栏 */}
        <div className="flex items-center justify-between border-b border-purple-100/80 pb-1 pt-0.5">
        <div className="flex items-center gap-2 overflow-x-auto p-1">
          {navTabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 px-5 py-2 rounded-full text-xs font-extrabold transition-all duration-200 whitespace-nowrap ${
                  isActive
                    ? 'bg-purple-600 text-white shadow-lg shadow-purple-600/30 scale-[1.02]'
                    : 'bg-white/90 text-slate-600 hover:text-slate-900 hover:bg-slate-100 border border-slate-200/80 shadow-2xs hover:shadow-xs'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>

        <span className="text-[11px] text-slate-400 font-mono hidden sm:inline-block">
          数据更新于: {updateTime || '刚刚'}
        </span>
      </div>

      {/* 📊 资金流向全景主功能页 (含 1日/3日/5日/10日/20日 横向柱状图) */}
      {activeTab === 'flow-bar' && (
        <div className="flex flex-col gap-4">
          {/* 🛠️ 控制中心工具栏 Toolbar */}
          <div className="bg-white/95 backdrop-blur-md rounded-2xl p-3.5 border border-purple-100/80 shadow-xs flex flex-wrap items-center justify-between gap-4">
            {/* 1. 周期选择器 (1日, 3日, 5日, 10日, 20日) */}
            <div className="flex items-center gap-2">
              <span className="text-xs font-extrabold text-slate-700 flex items-center gap-1">
                <Clock className="w-3.5 h-3.5 text-purple-600" />
                <span>统计周期:</span>
              </span>
              <div className="flex items-center bg-slate-100/90 border border-slate-200/60 p-1 rounded-full gap-1 shadow-2xs">
                {periodOptions.map((p) => (
                  <button
                    key={p.id}
                    onClick={() => setPeriod(p.id)}
                    className={`px-3.5 py-1 rounded-full text-xs font-extrabold transition-all duration-200 ${
                      period === p.id
                        ? 'bg-purple-600 text-white shadow-md shadow-purple-600/20 scale-[1.02]'
                        : 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/60'
                    }`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            {/* 2. 维度与分类选择 */}
            <div className="flex items-center gap-3">
              <div className="flex items-center bg-purple-50/90 border border-purple-200/70 rounded-full p-1 gap-1 shadow-2xs">
                <button
                  onClick={() => setFlowDimension('sector')}
                  className={`px-4 py-1 rounded-full text-xs font-extrabold transition-all duration-200 ${
                    flowDimension === 'sector'
                      ? 'bg-purple-600 text-white shadow-md shadow-purple-600/20 scale-[1.02]'
                      : 'text-purple-700 hover:text-purple-900 hover:bg-purple-100/60'
                  }`}
                >
                  板块/行业流向
                </button>
                <button
                  onClick={() => setFlowDimension('stock')}
                  className={`px-4 py-1 rounded-full text-xs font-extrabold transition-all duration-200 ${
                    flowDimension === 'stock'
                      ? 'bg-purple-600 text-white shadow-md shadow-purple-600/20 scale-[1.02]'
                      : 'text-purple-700 hover:text-purple-900 hover:bg-purple-100/60'
                  }`}
                >
                  个股流向
                </button>
              </div>

              {flowDimension === 'sector' && (
                <div className="flex items-center bg-slate-100/90 border border-slate-200/60 rounded-full p-1 gap-1 shadow-2xs">
                  <button
                    onClick={() => setCategoryMode('shenwan')}
                    className={`px-4 py-1 rounded-full text-xs font-extrabold transition-all duration-200 ${
                      categoryMode === 'shenwan'
                        ? 'bg-white text-slate-900 shadow-sm border border-slate-200/80 font-black'
                        : 'text-slate-500 hover:text-slate-800'
                    }`}
                  >
                    申万一级
                  </button>
                  <button
                    onClick={() => setCategoryMode('concept')}
                    className={`px-4 py-1 rounded-full text-xs font-extrabold transition-all duration-200 ${
                      categoryMode === 'concept'
                        ? 'bg-white text-slate-900 shadow-sm border border-slate-200/80 font-black'
                        : 'text-slate-500 hover:text-slate-800'
                    }`}
                  >
                    热门概念
                  </button>
                </div>
              )}

              {/* 3. 视图模式 (柱状图 vs TreeMap 树图) */}
              <div className="flex items-center bg-slate-100/90 border border-slate-200/60 rounded-full p-1 gap-1 shadow-2xs">
                <button
                  onClick={() => setChartViewMode('bar')}
                  className={`flex items-center gap-1.5 px-4 py-1 rounded-full text-xs font-extrabold transition-all duration-200 ${
                    chartViewMode === 'bar'
                      ? 'bg-purple-600 text-white shadow-md shadow-purple-600/20 scale-[1.02]'
                      : 'text-slate-600 hover:text-slate-900'
                  }`}
                >
                  <BarChart3 className="w-3.5 h-3.5" />
                  <span>横向柱图</span>
                </button>
                <button
                  onClick={() => setChartViewMode('treemap')}
                  className={`flex items-center gap-1.5 px-4 py-1 rounded-full text-xs font-extrabold transition-all duration-200 ${
                    chartViewMode === 'treemap'
                      ? 'bg-purple-600 text-white shadow-md shadow-purple-600/20 scale-[1.02]'
                      : 'text-slate-600 hover:text-slate-900'
                  }`}
                >
                  <Layers className="w-3.5 h-3.5" />
                  <span>矩形树图</span>
                </button>
              </div>
            </div>
          </div>

          {/* 📈 主图表展现区 */}
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
            <div
              className={`${
                selectedFlowItem ? 'lg:col-span-8' : 'lg:col-span-12'
              } bg-white/95 backdrop-blur-md rounded-3xl p-5 border border-purple-100/80 shadow-md shadow-purple-500/5 flex flex-col gap-3 transition-all duration-300`}
            >
              <div className="flex items-center justify-between border-b border-slate-100 pb-3">
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-purple-600 animate-pulse" />
                  <h3 className="text-sm font-extrabold text-slate-900">
                    {period.toUpperCase()} 资金净流入/净流出{flowDimension === 'sector' ? '板块' : '个股'}排行榜
                  </h3>
                </div>
                <span className="text-xs text-slate-400 font-mono">
                  右侧红色: 资金净流入  |  左侧绿色: 资金净流出
                </span>
              </div>

              {chartViewMode === 'bar' ? (
                <CapitalFlowHorizontalBarChart
                  period={period}
                  dimension={flowDimension}
                  categoryMode={categoryMode}
                  height={flowDimension === 'sector' && categoryMode === 'shenwan' ? 780 : 560}
                  onItemClick={(item) => setSelectedFlowItem(item)}
                />
              ) : (
                <ShenwanHeatmapChart data={treemapData} height={780} />
              )}
            </div>

            {/* 🎯 点击下钻卡片 Panel */}
            {selectedFlowItem && (
              <div className="lg:col-span-4 bg-white/95 backdrop-blur-md rounded-3xl p-5 border border-purple-100/80 shadow-md shadow-purple-500/5 flex flex-col gap-4 animate-in fade-in slide-in-from-right-4 duration-300">
                <div className="flex items-center justify-between border-b border-purple-100 pb-3">
                  <div>
                    <h4 className="text-sm font-extrabold text-slate-900 flex items-center gap-2">
                      <span>{selectedFlowItem.name}</span>
                      {selectedFlowItem.symbol && (
                        <span className="text-xs font-mono text-purple-600 bg-purple-50 px-2 py-0.5 rounded-md border border-purple-200">
                          {selectedFlowItem.symbol}
                        </span>
                      )}
                    </h4>
                    <p className="text-[11px] text-slate-400 mt-0.5">下钻资金流与主力动向拆解</p>
                  </div>
                  <button
                    onClick={() => setSelectedFlowItem(null)}
                    className="text-slate-400 hover:text-slate-700 text-xs font-bold px-2 py-1 bg-slate-100 rounded-lg"
                  >
                    关闭 ✕
                  </button>
                </div>

                <div className="flex flex-col gap-3">
                  <div className="grid grid-cols-2 gap-2">
                    <div className="bg-purple-50/60 rounded-2xl p-3 border border-purple-100">
                      <span className="text-[11px] text-slate-500">区间累计净流入</span>
                      <div className={`text-base font-extrabold font-mono mt-1 ${selectedFlowItem.net_inflow >= 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                        {selectedFlowItem.net_inflow >= 0 ? '+' : ''}
                        {(selectedFlowItem.net_inflow / 100000000).toFixed(2)} 亿元
                      </div>
                    </div>

                    <div className="bg-purple-50/60 rounded-2xl p-3 border border-purple-100">
                      <span className="text-[11px] text-slate-500">主力净占比</span>
                      <div className="text-base font-extrabold font-mono text-purple-700 mt-1">
                        {selectedFlowItem.main_ratio}%
                      </div>
                    </div>
                  </div>

                  <div className="flex flex-col gap-2 pt-2 border-t border-slate-100">
                    <span className="text-xs font-bold text-slate-700">筹码四分结构 (元):</span>
                    <div className="space-y-1.5 text-xs font-mono">
                      <div className="flex justify-between items-center bg-rose-50/80 px-3 py-1.5 rounded-xl border border-rose-100">
                        <span className="text-rose-700 font-bold">🔴 超大单 (主力)</span>
                        <span className="font-extrabold text-rose-800">
                          {(selectedFlowItem.super_large / 100000000).toFixed(2)} 亿
                        </span>
                      </div>
                      <div className="flex justify-between items-center bg-orange-50/80 px-3 py-1.5 rounded-xl border border-orange-100">
                        <span className="text-orange-700 font-bold">🟠 大单 (游资)</span>
                        <span className="font-extrabold text-orange-800">
                          {(selectedFlowItem.large / 100000000).toFixed(2)} 亿
                        </span>
                      </div>
                      <div className="flex justify-between items-center bg-amber-50/80 px-3 py-1.5 rounded-xl border border-amber-100">
                        <span className="text-amber-700 font-bold">🟡 中单</span>
                        <span className="font-extrabold text-amber-800">
                          {(selectedFlowItem.medium / 100000000).toFixed(2)} 亿
                        </span>
                      </div>
                      <div className="flex justify-between items-center bg-emerald-50/80 px-3 py-1.5 rounded-xl border border-emerald-100">
                        <span className="text-emerald-700 font-bold">🟢 小单 (散户)</span>
                        <span className="font-extrabold text-emerald-800">
                          {(selectedFlowItem.small / 100000000).toFixed(2)} 亿
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── 原有其他页面内容维持完备 ── */}
      {activeTab === 'panorama' && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-stretch">
          <div className="lg:col-span-5 flex flex-col justify-between gap-4">
            <MarketBreadthCard
              advanceCount={breadth?.advance_count}
              declineCount={breadth?.decline_count}
              flatCount={breadth?.flat_count}
              limitUpCount={breadth?.limit_up_count}
              limitDownCount={breadth?.limit_down_count}
              totalTurnoverYi={breadth?.total_turnover_yi}
              profitEffect={breadth?.profit_effect}
              limitUpBrokenRatio={breadth?.limit_up_broken_ratio}
            />
            <div className="flex flex-col gap-2.5 bg-white/95 backdrop-blur-md rounded-3xl p-5 border border-purple-100/80 shadow-md shadow-purple-500/5">
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-extrabold text-slate-800 flex items-center gap-1.5">
                  <TrendingUp className="w-3.5 h-3.5 text-purple-600" />
                  <span>主力资金净流入 Top 5 股票</span>
                </h3>
                <span
                  onClick={() => setActiveTab('stock-flow')}
                  className="text-[11px] text-purple-600 font-extrabold hover:underline cursor-pointer flex items-center"
                >
                  完整排行榜 ➔
                </span>
              </div>
              <StockMoneyFlowTable items={stockFlows.slice(0, 5)} isMini={true} />
            </div>
          </div>

          <div className="lg:col-span-7 bg-white/95 backdrop-blur-md rounded-3xl p-5 border border-purple-100/80 shadow-md shadow-purple-500/5 flex flex-col justify-between">
            <div className="flex items-center justify-between border-b border-purple-100/60 pb-3 mb-1">
              <h3 className="text-xs font-extrabold text-slate-900 flex items-center gap-1.5">
                <Layers className="w-3.5 h-3.5 text-purple-600" />
                <span>申万一级分类热力矩形图谱</span>
              </h3>
              <span className="text-[10px] text-slate-400 font-mono">市值权重 vs 涨跌幅</span>
            </div>
            <ShenwanHeatmapChart data={heatmapData} height={530} />
          </div>
        </div>
      )}

      {activeTab === 'money-flow' && (
        <div className="bg-white/90 backdrop-blur-md rounded-2xl p-5 border border-slate-200/80 shadow-sm flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-extrabold text-slate-800 flex items-center gap-1.5">
              <Network className="w-3.5 h-3.5 text-purple-600" />
              <span>主力与散户资金流动全景桑基图 (Sankey Diagram)</span>
            </h3>
            <span className="text-xs text-slate-400">资金实时划转链条</span>
          </div>
          <CapitalFlowSankeyChart
            nodes={sankeyData?.nodes}
            links={sankeyData?.links}
            height={480}
          />
        </div>
      )}

      {activeTab === 'stock-flow' && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-extrabold text-slate-800 flex items-center gap-1.5">
              <TrendingUp className="w-3.5 h-3.5 text-purple-600" />
              <span>个股主力资金净流入排行榜</span>
            </h3>
            <span className="text-xs text-slate-400">包含超大单、大单、中单、小单拆解</span>
          </div>
          <StockMoneyFlowTable items={stockFlows} loading={loading} latestDate={breadth?.trade_date} />
        </div>
      )}

      {activeTab === 'tag-lookup' && <TagLookupPanel />}
    </div>
  );
};


