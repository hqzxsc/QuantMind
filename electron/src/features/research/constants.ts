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
  连板突破: { limitUpDays: 2 },
  白马蓝筹: { roeTop: 0.3, totalMvTop: 0.3 },
  题材活跃: { turnoverTop: 0.25, amountTop: 0.25 },
  低位反弹: { maGap20Bottom: 0.25, rsiBottom: 0.25 },
  高波动: { volStd20Top: 0.25 },
  强势动量: { momRet60dTop: 0.3, indStrength20Top: 0.3 },
  资金流入: { flowNetAmountTop: 0.25 },
  筹码获利: { chipProfitRatio20Top: 0.25 },
  低估值: { peBottom: 0.25, pbBottom: 0.3 },
};

export const DEFAULT_RESEARCH_FILTERS: ResearchFiltersState = {
  // Core
  minScore: -1.0,
  limitUpDays: 0,
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
  breakout20dRange: [-100, 100],
  // Volatility
  volStd5Range: [0, 10],
  volStd20Range: [0, 10],
  volStd60Range: [0, 10],
  atr14Range: [0, 100],
  volDownside20Range: [0, 100],
  volUpside20Range: [-200, 200],
  volRealizedRvRange: [0, 10],
  // Technical
  volRatio5Range: 0,
  volRatio20Range: 0,
  mfi14Range: [0, 100],
  bbPosRange: [0, 1],
  adx14Range: [0, 100],
  // Fund Flow
  mainFlowRange: [-1000000, 1000000],
  flowNetAmountRange: [-1000000, 1000000],
  flowLargeNetRange: [-1000000, 1000000],
  flowImbalanceRange: [-1, 1],
  flowMfiRange: [0, 100],
  // Style
  beta20Range: [-3, 3],
  beta60Range: [-3, 3],
  idioVol20Range: [0, 10],
  // Industry
  indStrength20Range: [-10, 10],
  indRet20Range: [-100, 100],
  indRelativeMomentum20Range: [-10, 10],
  // Chip
  chipProfitRatio20Range: [0, 1],
  chipProfitRatio60Range: [0, 1],
  chipFloatingRatioRange: [0, 500],
  // Fundamental
  peRange: [-10000, 100000],
  roeRange: [-1000, 1000],
  profitGrowthRange: [-1000, 1000],
  pbRange: [0, 1000],
  psTtmRange: [0, 1000],
  instOwnershipRange: [0, 100],
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

// Column group definitions for visibility toggle
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
    key: 'limitUp',
    label: '连板',
    columns: ['consecutiveLimitUpDays', 'volumeTrend3d'],
    defaultVisible: true,
  },
  {
    key: 'liquidity',
    label: '流动性',
    columns: ['turnoverRate', 'amount', 'volRatio5', 'volRatio20', 'liqAmountMa5', 'liqMfi14', 'liqAmihud20'],
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
    columns: ['ma5', 'ma10', 'ma20', 'maGap5', 'maGap10', 'maGap20', 'rsi', 'rsi14', 'atr', 'macdHist', 'kdjK', 'kdjD', 'kdjJ', 'bbPos', 'adx14'],
    defaultVisible: false,
  },
  {
    key: 'momentum',
    label: '动量',
    columns: ['momRet1d', 'momRet3d', 'momRet5d', 'momRet10d', 'momRet20d', 'momRet60d', 'momEmaGap12'],
    defaultVisible: false,
  },
  {
    key: 'volatility',
    label: '波动率',
    columns: ['volStd5', 'volStd20', 'volStd60', 'volAtr14', 'volParkinson20', 'volUpDownRatio', 'volSkew', 'volRealizedRv'],
    defaultVisible: false,
  },
  {
    key: 'fundFlow',
    label: '资金流',
    columns: ['mainFlow', 'flowNetAmount', 'flowLargeNet', 'flowMediumNet', 'flowSmallNet', 'flowNetRatio', 'flowLargeRatio', 'flowImbalanceVolume', 'flowMoneyFlowIndex'],
    defaultVisible: false,
  },
  {
    key: 'style',
    label: '风格',
    columns: ['styleBeta20', 'styleBeta60', 'styleIdioVol20', 'styleValue20', 'styleSize20', 'styleMvRank'],
    defaultVisible: false,
  },
  {
    key: 'industry',
    label: '行业',
    columns: ['indStrength20', 'indStrength60', 'indRet20', 'indRelativeMomentum20', 'indCrowding20', 'indRotationSpeed20'],
    defaultVisible: false,
  },
  {
    key: 'chip',
    label: '筹码',
    columns: ['chipProfitRatio20', 'chipProfitRatio60', 'chipProfitRatio120', 'chipCost90Width', 'chipConcentration20', 'chipPeakDistance'],
    defaultVisible: false,
  },
  {
    key: 'concept',
    label: '概念',
    columns: ['conceptHotScore', 'conceptMomentumTop3', 'conceptExposureTop1', 'conceptLeaderScore', 'conceptVolumeRatio'],
    defaultVisible: false,
  },
  {
    key: 'sentiment',
    label: '情绪',
    columns: ['sentimentLiquidityScore', 'sentimentBuyPressure', 'sentimentSellPressure', 'sentimentBodyRatio', 'sentimentIntradayVol'],
    defaultVisible: false,
  },
  {
    key: 'microstructure',
    label: '微观结构',
    columns: ['microVpin8', 'microVpin20', 'microVpin50', 'microEspEqual', 'microAmihudIlliquidity', 'microJumpFlag', 'microDepthImbalance1', 'microRealizedSpread'],
    defaultVisible: false,
  },
  {
    key: 'tags',
    label: '标签',
    columns: ['sector', 'status'],
    defaultVisible: true,
  },
];
