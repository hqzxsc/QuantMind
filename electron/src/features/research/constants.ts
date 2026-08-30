import type { ResearchFiltersState } from './types';

/**
 * 快捷模板：全部按“当前候选池的分位数”定义，而非写死数值。
 *
 * 各推理批次的数据分布差异很大（不同模型、不同交易日，PG 与 QuantDB 覆盖也不同），
 * 写死阈值必然在部分批次上退化成“一只都选不出”或“全都选中”。
 * `xxxTop: 0.2` = 取该指标最高的 20%；`xxxBottom: 0.2` = 取最低的 20%。
 */
export const PRESET_FILTER_MAP: Record<string, any> = {
  高分优选: { scoreTopPercent: 10 },
  白马蓝筹: { roeTop: 0.3, totalMvTop: 0.3 },
  题材活跃: { turnoverTop: 0.25, amountTop: 0.25 },
  低位反弹: { maGap20Bottom: 0.25, rsiBottom: 0.25 },
  高波动: { volStd20Top: 0.25 },
  低估值: { peBottom: 0.25, pbBottom: 0.3 },
};

export const DEFAULT_RESEARCH_FILTERS: ResearchFiltersState = {
  // Core
  minScore: -1.0,
  excludeSt: false,
  highConfidenceOnly: false,
  // Market & Liquidity
  amountRange: [0, 100000],
  turnoverRange: [0, 100],
  totalMvRange: [0, 1000000],
  floatMvRange: [0, 1000000],
  // Momentum
  return1dRange: [-100, 100],
  return3dRange: [-100, 100],
  return5dRange: [-100, 100],
  return10dRange: [-100, 100],
  return20dRange: [-100, 100],
  return60dRange: [-100, 100],
  maGap5Range: [-100, 100],
  maGap10Range: [-100, 100],
  maGap20Range: [-100, 100],
  rsiRange: [0, 100],
  rsi14Range: [0, 100],
  kdjKRange: [0, 100],
  macdHistRange: [-10, 10],
  // Volatility
  volStd5Range: [0, 10],
  volStd20Range: [0, 10],
  volStd60Range: [0, 10],
  atr14Range: [0, 100],
  // Technical
  volRatio5Range: 0,
  volRatio20Range: 0,
  beta20Range: [-3, 3],
  // Fundamental
  peRange: [-10000, 100000],
  roeRange: [-1000, 1000],
  profitGrowthRange: [-1000, 1000],
  pbRange: [0, 1000],
  psTtmRange: [0, 1000],
  listedDaysRange: [0, 30000],
  // Sector/Concept
  selectedSectors: [],
  selectedConcepts: [],
  selectedIndices: [],
  marketType: 'all',
  // Meta
  volumeTrendOnly: false,
  advancedFiltersEnabled: false,
};

export const BUTTON_STYLES = {
  headerRefresh:
    'h-9 rounded-xl border-slate-200 bg-white px-4 text-xs font-bold text-slate-600 shadow-sm transition-all hover:border-blue-400 hover:text-blue-500 hover:shadow-md active:scale-95',
  headerSave:
    'h-9 rounded-xl border border-slate-200 bg-white px-4 text-xs font-bold text-slate-700 shadow-sm transition-all hover:border-slate-300 hover:text-slate-900 active:scale-95',
  applyFilters:
    'group relative w-full overflow-hidden rounded-2xl bg-slate-900 py-3.5 font-black text-white shadow-xl shadow-slate-900/20 transition-all hover:bg-slate-800 hover:shadow-2xl hover:-translate-y-0.5 active:scale-95 active:translate-y-0',
};

export const FIELD_STYLES = {
  select: 'research-next-select rounded-xl font-bold border-slate-200',
  input: 'research-next-input rounded-xl border-slate-200 font-medium h-10',
  slider: 'research-next-slider py-4',
  collapse: 'research-next-collapse border-none bg-transparent',
  table: 'research-next-table custom-scrollbar',
  segmented: 'research-next-segmented rounded-2xl p-1 bg-slate-100',
};

export const TEMPLATE_BUTTON_STYLES = {
  idle: 'bg-slate-50 text-slate-500 border-slate-200 hover:border-blue-200 hover:bg-blue-50 hover:text-blue-500',
  active: 'bg-blue-600 text-white border-blue-600 shadow-md shadow-blue-500/20',
};

// Column group definitions（固定列：候选池只展示 50 维宽表 features_daily
// 提供的字段 + universe 基础标识列，不再支持列自定义勾选）
export interface ColumnGroup {
  key: string;
  label: string;
  columns: string[];
  defaultVisible: boolean;
}

export const COLUMN_GROUPS: ColumnGroup[] = [
  {
    key: 'identity',
    label: '标识',
    columns: ['rank', 'stock', 'score', 'latestChange'],
    defaultVisible: true,
  },
  {
    key: 'returns',
    label: '收益',
    columns: ['return1d', 'return3d', 'return5d', 'return10d', 'return20d', 'return60d'],
    defaultVisible: true,
  },
  {
    key: 'liquidity',
    label: '流动性',
    columns: ['turnoverRate', 'amount', 'volRatio5', 'volRatio20', 'volumeTrend3d'],
    defaultVisible: true,
  },
  {
    key: 'fundamental',
    label: '基本面',
    columns: ['pe', 'pb', 'roe', 'psTtm', 'profitGrowth', 'totalMv', 'floatMv', 'listedDays'],
    defaultVisible: true,
  },
  {
    key: 'technical',
    label: '技术面',
    columns: ['ma5', 'ma10', 'ma20', 'ma60', 'maGap5', 'maGap10', 'maGap20', 'rsi', 'rsi14', 'atr', 'macdHist', 'kdjK', 'beta20'],
    defaultVisible: true,
  },
  {
    key: 'volatility',
    label: '波动率',
    columns: ['volStd5', 'volStd20', 'volStd60'],
    defaultVisible: true,
  },
  {
    key: 'tags',
    label: '标签',
    columns: ['sector', 'status'],
    defaultVisible: true,
  },
];
