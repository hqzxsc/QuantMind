import React from 'react';
import { motion } from 'framer-motion';
import {
  Activity,
  BarChart3,
  CalendarDays,
  CandlestickChart,
  ChevronLeft,
  ChevronRight,
  Columns3,
  Download,
  Filter,
  Flame,
  LibraryBig,
  Microscope,
  Quote,
  RefreshCw,
  Search,
  Sparkles,
  Target,
} from 'lucide-react';
import ReactECharts from 'echarts-for-react';
import {
  Button,
  Checkbox,
  Collapse,
  Empty,
  Input,
  InputNumber,
  message,
  Modal,
  Pagination,
  Popover,
  Segmented,
  Select,
  Switch,
  Table,
  Tag,
} from 'antd';
import type { ColumnsType, ColumnType } from 'antd/es/table';
import { PAGE_LAYOUT } from '../config/pageLayout';
import { researchService, type ResearchRunOption } from '../services/researchService';
import RiskScoreCard from '../components/Research/RiskScoreCard';
import {
  BUTTON_STYLES,
  COLUMN_GROUPS,
  DEFAULT_RESEARCH_FILTERS,
  FIELD_STYLES,
  PRESET_FILTER_MAP,
  TEMPLATE_BUTTON_STYLES,
} from '../features/research/constants';
import {
  type DataSourceTab,
  type FilterSectionKey,
  type QuantDbFeatures,
  type ResearchFiltersState,
  type ResearchModelOption,
  type ResearchPoolRow,
  type ResearchStockRow,
  type SignalType,
  type SortKey,
  type WatchlistRow,
} from '../features/research/types';
import {
  fmt2,
  fmtMainFlowCn,
  fmtNullableExponential,
  fmtNullableFloat,
  fmtNullableSignedPercent2,
  fmtNullableYi,
  fmtPercent2,
  fmtPositiveOrDash,
  fmtSignedPercent2,
  normalizeRoe,
  normalizeSymbol,
  safeNum,
} from '../features/research/utils/formatters';
import {
  flattenProjectedValues,
  flattenQuantDbFeatures,
  mergePoolFeatures,
  mergeQuantDbFeatures,
  toSuffixSymbol,
} from '../features/research/utils/featureMapper';
import { FactorPanel } from '../features/research/components/FactorPanel';
import '../styles/research-next-theme.css';
import { useAppSelector } from '../store';
import { selectCurrentMarket } from '../store/slices/uiSlice';
import { getMarketConfig } from '../config/marketConfig';

/* ------------------------------------------------------------------ *
 * 常量与工具
 * ------------------------------------------------------------------ */

const VISIBLE_GROUPS_STORAGE_KEY = 'research.visibleColumnGroups';

/** snake_case -> camelCase，用于兼容后端两种字段命名 */
const toCamelKey = (key: string): string => key.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase());

/** 将后端返回的行做 camelCase 补齐（不覆盖已有 camelCase 字段） */
const camelizeRow = (item: Record<string, any>): Record<string, any> => {
  const out: Record<string, any> = { ...item };
  Object.keys(item).forEach((key) => {
    if (!key.includes('_')) return;
    const camel = toCamelKey(key);
    if (out[camel] === undefined) out[camel] = item[key];
  });
  return out;
};

/** 深拷贝筛选状态（数组字段需断开引用，避免误改默认值） */
const cloneFilters = (source: ResearchFiltersState): ResearchFiltersState => {
  const out: Record<string, any> = {};
  Object.entries(source).forEach(([key, value]) => {
    out[key] = Array.isArray(value) ? [...value] : value;
  });
  return out as ResearchFiltersState;
};

const isSameRange = (left: unknown, right: unknown): boolean =>
  Array.isArray(left) && Array.isArray(right) && left[0] === right[0] && left[1] === right[1];

/* ------------------------------------------------------------------ *
 * 表格列渲染器
 * ------------------------------------------------------------------ */

const DASH = <span className="font-medium text-slate-300">-</span>;

const isNil = (value: unknown): boolean =>
  value === null || value === undefined || (typeof value === 'number' && !Number.isFinite(value)) || Number.isNaN(Number(value));

type CellRenderer = (value: any, record: ResearchStockRow, index: number) => React.ReactNode;

/** 普通数值：固定小数位 + 可选后缀 */
const rNum = (digits: number, suffix = ''): CellRenderer => (value) =>
  isNil(value) ? DASH : (
    <span className="whitespace-nowrap font-medium text-slate-600">
      {Number(value).toFixed(digits)}{suffix}
    </span>
  );

/** 仅正数有意义（PE / PS 等） */
const rPositive = (digits: number): CellRenderer => (value) =>
  isNil(value) || Number(value) <= 0 ? DASH : (
    <span className="whitespace-nowrap font-medium text-slate-600">{Number(value).toFixed(digits)}</span>
  );

/** 涨跌类：红涨绿跌 + 正号 */
const rSigned = (digits: number, suffix = '%'): CellRenderer => (value) => {
  if (isNil(value)) return DASH;
  const n = Number(value);
  return (
    <span className={`whitespace-nowrap font-semibold ${n >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
      {n >= 0 ? '+' : ''}{n.toFixed(digits)}{suffix}
    </span>
  );
};

/** 红绿着色但不加正号（MACD / 动量因子等） */
const rColored = (digits: number, suffix = ''): CellRenderer => (value) => {
  if (isNil(value)) return DASH;
  const n = Number(value);
  return (
    <span className={`whitespace-nowrap ${n >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
      {n.toFixed(digits)}{suffix}
    </span>
  );
};

/** 乖离率：正值高亮 */
const rGap: CellRenderer = (value) => {
  if (isNil(value)) return DASH;
  const n = Number(value);
  return (
    <span className={`whitespace-nowrap ${n >= 0 ? 'font-medium text-indigo-500' : 'text-slate-400'}`}>
      {n > 0 ? '+' : ''}{n.toFixed(2)}%
    </span>
  );
};

/** RSI：超买红 / 超卖绿 */
const rRsi: CellRenderer = (value) => {
  if (isNil(value)) return DASH;
  const n = Number(value);
  return (
    <span className={`whitespace-nowrap ${n >= 70 ? 'font-bold text-rose-500' : n <= 30 ? 'text-emerald-500' : 'text-slate-600'}`}>
      {n.toFixed(1)}
    </span>
  );
};

/** ROE：过滤明显异常值 */
const rRoe: CellRenderer = (value) => {
  if (isNil(value)) return DASH;
  const n = Number(value);
  if (n <= -100 || n >= 100) return DASH;
  return <span className="whitespace-nowrap font-bold text-rose-500">{n.toFixed(1)}%</span>;
};

/** 科学计数法（Amihud 等极小量级指标） */
const rExp: CellRenderer = (value) =>
  isNil(value) ? DASH : <span className="whitespace-nowrap text-xs text-slate-600">{Number(value).toExponential(2)}</span>;

/** 资金流：后端口径为百万，统一走 fmtMainFlowCn */
const rFlow: CellRenderer = (value) => {
  if (isNil(value)) return DASH;
  const n = Number(value);
  return (
    <span className={`whitespace-nowrap font-medium ${n >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
      {fmtMainFlowCn(n)}
    </span>
  );
};

/** 原始元 -> 亿元 */
const rYuanToYi: CellRenderer = (value) =>
  isNil(value) ? DASH : (
    <span className="whitespace-nowrap font-medium text-slate-600">{(Number(value) / 100000000).toFixed(2)}亿</span>
  );

/** 整数（排名类） */
const rInt: CellRenderer = (value) =>
  isNil(value) ? DASH : <span className="whitespace-nowrap font-medium text-slate-600">{Number(value).toFixed(0)}</span>;

/** 量能趋势标签 */
const rVolumeTrend: CellRenderer = (value) => {
  if (isNil(value)) return DASH;
  const trend = Number(value);
  if (trend > 0) return <Tag color="orange" className="rounded-lg border-none font-bold">递增</Tag>;
  if (trend < 0) return <Tag color="blue" className="rounded-lg border-none font-bold">递减</Tag>;
  return <Tag color="default" className="rounded-lg border-none font-bold">平缓</Tag>;
};

/** 自选/研究池：涨跌幅口径可能是小数，做一次量级归一 */
const rScaledChange: CellRenderer = (value) => {
  if (isNil(value)) return DASH;
  const n = Number(value);
  const display = Math.abs(n) > 1.0 ? n : n * 100;
  return (
    <span className={`whitespace-nowrap font-bold ${display >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
      {display >= 0 ? '+' : ''}{display.toFixed(2)}%
    </span>
  );
};

interface ColumnDef {
  title: string;
  width: number;
  /** 与 key 不同名时显式声明数据字段 */
  dataIndex?: string;
  /** 依赖整行数据渲染，不绑定 dataIndex */
  custom?: boolean;
  render?: CellRenderer;
  ellipsis?: boolean;
}

/**
 * 全量列定义表。COLUMN_GROUPS 中的每个列 key 都必须在此登记。
 */
// 当前批次分数分位数（由 overview.summary.scoreDistribution 更新，供 score 列动态着色）
let currentScoreDist: { p25?: number; p50?: number; p75?: number } | null = null;

const COLUMN_DEFS: Record<string, ColumnDef> = {  // ---- 标识 ----
  rank: {
    title: '排名',
    width: 60,
    render: (value) => <span className="whitespace-nowrap font-bold text-slate-700">{value}</span>,
  },
  stock: {
    title: '股票',
    width: 132,
    custom: true,
    render: (_value, record) => (
      <div className="whitespace-nowrap text-center">
        <div className="whitespace-nowrap font-bold text-slate-900">{record.name}</div>
        <div className="whitespace-nowrap text-xs text-slate-500">{record.code}</div>
      </div>
    ),
  },
  score: {
    title: '模型分数',
    width: 98,
    render: (value) => {
      const n = safeNum(value, 0);
      // 按当前批次分数分位数动态着色（有分布时），否则正负二分
      let cls = 'text-blue-400';
      if (currentScoreDist) {
        const { p25, p50, p75 } = currentScoreDist;
        if (typeof p25 === 'number' && typeof p75 === 'number') {
          if (n >= p75) cls = 'text-rose-600';
          else if (n >= p50) cls = 'text-orange-500';
          else if (n >= p25) cls = 'text-sky-500';
          else cls = 'text-emerald-600';
        }
      } else {
        cls = n >= 0 ? 'text-rose-500' : 'text-emerald-500';
      }
      return <span className={`whitespace-nowrap font-black ${cls}`}>{n.toFixed(3)}</span>;
    },
  },
  latestChange: { title: '涨跌幅', width: 96, render: rSigned(2) },

  // ---- 收益 ----
  return1d: { title: '1日收益', width: 96, render: rSigned(2) },
  return3d: { title: '3日收益', width: 96, render: rSigned(2) },
  return5d: { title: '5日收益', width: 96, render: rSigned(2) },
  return10d: { title: '10日收益', width: 96, render: rSigned(2) },
  return20d: { title: '20日收益', width: 96, render: rSigned(2) },
  return60d: { title: '60日收益', width: 96, render: rSigned(2) },

  // ---- 连板 ----
  consecutiveLimitUpDays: { title: '连板', width: 54 },
  volumeTrend3d: { title: '3日量能', width: 92, render: rVolumeTrend },

  // ---- 流动性 ----
  turnoverRate: { title: '换手率', width: 90, render: rNum(2, '%') },
  amount: { title: '成交额', width: 108, render: rNum(2, '亿') },
  volRatio5: { title: '5日量比', width: 90, render: rNum(2) },
  volRatio20: { title: '20日量比', width: 90, render: rNum(2) },
  liqAmountMa5: { title: '5日均额', width: 100, render: rYuanToYi },
  liqMfi14: { title: 'MFI(14)', width: 80, render: rNum(1) },
  liqAmihud20: { title: 'Amihud', width: 85, render: rExp },

  // ---- 基本面 ----
  pe: { title: 'PE(TTM)', width: 92, render: rPositive(1) },
  pb: { title: 'PB', width: 80, render: rNum(2) },
  roe: { title: 'ROE(%)', width: 92, render: rRoe },
  psTtm: { title: 'PS(TTM)', width: 92, render: rPositive(1) },
  profitGrowth: { title: '利润增速', width: 92, render: rNum(1, '%') },
  totalMv: { title: '总市值', width: 100, render: rNum(2, '亿') },
  floatMv: { title: '流通市值', width: 100, render: rNum(2, '亿') },
  listedDays: { title: '上市天数', width: 85, render: rInt },

  // ---- 技术面 ----
  ma5: { title: 'MA5', width: 80, render: rNum(2) },
  ma10: { title: 'MA10', width: 80, render: rNum(2) },
  ma20: { title: 'MA20', width: 80, render: rNum(2) },
  maGap5: { title: '5日乖离', width: 92, render: rGap },
  maGap10: { title: '10日乖离', width: 92, render: rGap },
  maGap20: { title: '20日乖离', width: 92, render: rGap },
  rsi: { title: 'RSI(6)', width: 76, render: rRsi },
  rsi14: { title: 'RSI(14)', width: 76, render: rRsi },
  atr: { title: 'ATR', width: 80, render: rNum(3) },
  macdHist: { title: 'MACD', width: 80, render: rColored(3) },
  kdjK: { title: 'KDJ-K', width: 76, dataIndex: 'momKdjK', render: rNum(1) },
  kdjD: { title: 'KDJ-D', width: 76, dataIndex: 'momKdjD', render: rNum(1) },
  kdjJ: { title: 'KDJ-J', width: 76, dataIndex: 'momKdjJ', render: rNum(1) },
  bbPos: { title: 'BB位置', width: 80, dataIndex: 'techBbPos', render: rNum(2) },
  adx14: { title: 'ADX(14)', width: 80, dataIndex: 'techAdx14', render: rNum(1) },

  // ---- 动量 ----
  momRet1d: { title: '动1日', width: 80, render: rColored(3) },
  momRet3d: { title: '动3日', width: 80, render: rColored(3) },
  momRet5d: { title: '动5日', width: 80, render: rColored(3) },
  momRet10d: { title: '动10日', width: 80, render: rColored(3) },
  momRet20d: { title: '动20日', width: 80, render: rColored(3) },
  momRet60d: { title: '动60日', width: 80, render: rColored(3) },
  momEmaGap12: { title: 'EMA12偏离', width: 90, render: rNum(3) },

  // ---- 波动率 ----
  volStd5: { title: '波5日', width: 80, render: rNum(4) },
  volStd20: { title: '波20日', width: 80, render: rNum(4) },
  volStd60: { title: '波60日', width: 80, render: rNum(4) },
  volAtr14: { title: 'ATR14', width: 80, render: rNum(3) },
  volParkinson20: { title: 'Parkinson', width: 85, render: rNum(4) },
  volUpDownRatio: { title: '涨跌波比', width: 90, render: rNum(3) },
  volSkew: { title: '波动偏度', width: 85, render: rNum(3) },
  volRealizedRv: { title: '已实现波', width: 85, render: rNum(4) },

  // ---- 资金流 ----
  mainFlow: { title: '主力净流入', width: 100, render: rFlow },
  flowNetAmount: { title: '净流入', width: 100, render: rFlow },
  flowLargeNet: { title: '大单净额', width: 100, render: rFlow },
  flowMediumNet: { title: '中单净额', width: 100, render: rFlow },
  flowSmallNet: { title: '小单净额', width: 100, render: rFlow },
  flowNetRatio: { title: '净流入比', width: 90, render: rNum(2, '%') },
  flowLargeRatio: { title: '大单比', width: 80, render: rNum(2, '%') },
  flowImbalanceVolume: { title: '买卖失衡', width: 90, render: rNum(3) },
  flowMoneyFlowIndex: { title: '资金MFI', width: 85, render: rNum(1) },

  // ---- 风格 ----
  styleBeta20: { title: 'β20', width: 70, render: rNum(2) },
  styleBeta60: { title: 'β60', width: 70, render: rNum(2) },
  styleIdioVol20: { title: '特质波', width: 80, render: rNum(4) },
  styleValue20: { title: '价值', width: 70, render: rNum(2) },
  styleSize20: { title: '规模', width: 70, render: rNum(2) },
  styleMvRank: { title: '市值排名', width: 85, render: rInt },

  // ---- 行业 ----
  indStrength20: { title: '行业强度', width: 90, render: rNum(3) },
  indStrength60: { title: '行业强度60', width: 95, render: rNum(3) },
  indRet20: { title: '行业收益', width: 90, render: rColored(3) },
  indRelativeMomentum20: { title: '相对动量', width: 90, render: rColored(3) },
  indCrowding20: { title: '拥挤度', width: 80, render: rNum(3) },
  indRotationSpeed20: { title: '行业轮动', width: 90, render: rNum(3) },

  // ---- 筹码 ----
  chipProfitRatio20: { title: '获利盘20', width: 90, render: rNum(3) },
  chipProfitRatio60: { title: '获利盘60', width: 90, render: rNum(3) },
  chipProfitRatio120: { title: '获利盘120', width: 90, render: rNum(3) },
  chipCost90Width: { title: '成本90宽', width: 90, render: rNum(3) },
  chipConcentration20: { title: '集中度', width: 80, render: rNum(3) },
  chipPeakDistance: { title: '峰距', width: 80, render: rNum(3) },

  // ---- 概念 ----
  conceptHotScore: { title: '热度', width: 70, render: rNum(2) },
  conceptMomentumTop3: { title: '动量TOP3', width: 90, render: rNum(2) },
  conceptExposureTop1: { title: '暴露TOP1', width: 90, render: rNum(2) },
  conceptLeaderScore: { title: '龙头分', width: 80, render: rNum(2) },
  conceptVolumeRatio: { title: '概念量比', width: 90, render: rNum(3) },

  // ---- 情绪 ----
  sentimentLiquidityScore: { title: '流动性分', width: 90, render: rNum(2) },
  sentimentBuyPressure: { title: '买压', width: 70, render: rNum(2) },
  sentimentSellPressure: { title: '卖压', width: 70, render: rNum(2) },
  sentimentBodyRatio: { title: '实体比', width: 75, render: rNum(2) },
  sentimentIntradayVol: { title: '日内波', width: 75, render: rNum(2) },

  // ---- 微观结构 ----
  microVpin8: { title: 'VPIN8', width: 75, render: rNum(3) },
  microVpin20: { title: 'VPIN20', width: 75, render: rNum(3) },
  microVpin50: { title: 'VPIN50', width: 75, render: rNum(3) },
  microEspEqual: { title: '有效价差', width: 85, render: rNum(4) },
  microAmihudIlliquidity: { title: '非流动性', width: 90, render: rExp },
  microJumpFlag: {
    title: '跳跃',
    width: 60,
    render: (value) => (Number(value) === 1 ? <Tag color="orange" className="rounded-lg border-none font-bold">是</Tag> : DASH),
  },
  microDepthImbalance1: { title: '深度失衡', width: 85, render: rNum(3) },
  microRealizedSpread: { title: '已实现价差', width: 95, render: rNum(5) },

  // ---- 标签 ----
  sector: { title: '行业', width: 100, ellipsis: true },
  status: {
    title: '指数/状态',
    width: 160,
    custom: true,
    render: (_value, record) => (
      <div className="flex flex-wrap justify-center gap-1 whitespace-nowrap">
        {record.isSt && <Tag color="error" className="m-0 scale-90 text-[10px]">ST</Tag>}
        {record.isHs300 && <Tag color="blue" className="m-0 scale-90 text-[10px]">HS300</Tag>}
        {record.isCsi500 && <Tag color="cyan" className="m-0 scale-90 text-[10px]">ZZ500</Tag>}
        {record.isCsi1000 && <Tag color="purple" className="m-0 scale-90 text-[10px]">ZZ1000</Tag>}
      </div>
    ),
  },
};

const DEFAULT_COLUMN_WIDTH = 90;

/** 根据列 key 构建 antd 列配置 */
const buildColumn = (
  key: string,
  overrides: Partial<ColumnType<ResearchStockRow>> = {}
): ColumnType<ResearchStockRow> | null => {
  const def = COLUMN_DEFS[key];
  if (!def) return null;
  const column: ColumnType<ResearchStockRow> = {
    key,
    title: <span className="whitespace-nowrap">{def.title}</span>,
    width: def.width,
    align: 'center',
    ...(def.custom ? {} : { dataIndex: def.dataIndex ?? key }),
    ...(def.ellipsis ? { ellipsis: true } : {}),
    ...(def.render ? { render: def.render } : {}),
    ...overrides,
  };
  return column;
};

const buildColumns = (keys: string[]): ColumnsType<ResearchStockRow> =>
  keys.map((key) => buildColumn(key)).filter((item): item is ColumnType<ResearchStockRow> => item !== null);

const sumColumnWidth = (keys: string[]): number =>
  keys.reduce((total, key) => total + (COLUMN_DEFS[key]?.width ?? DEFAULT_COLUMN_WIDTH), 0);

/** 自选 / 研究池使用的精简列 */
const SIMPLE_TABLE_COLUMN_KEYS = [
  'rank', 'stock', 'score', 'latestChange', 'turnoverRate', 'amount', 'pe', 'roe', 'rsi', 'sector', 'status',
];

/* ------------------------------------------------------------------ *
 * 筛选侧栏配置
 * ------------------------------------------------------------------ */

interface FilterFieldConfig {
  key: keyof ResearchFiltersState;
  label: string;
  step?: number;
  suffix?: string;
  /** 引用 QUICK_TAGS 中的分组 key，为该字段渲染快捷标签 */
  quickTagGroup?: string;
}

interface FilterSectionConfig {
  key: FilterSectionKey;
  label: string;
  fields: FilterFieldConfig[];
}

const FILTER_SECTIONS: FilterSectionConfig[] = [
  {
    key: 'common',
    label: '核心指标',
    fields: [
      { key: 'minScore', label: '模型分数 (≥)', step: 0.01 },
      { key: 'limitUpDays', label: '连板天数 (≥)', suffix: '天' },
    ],
  },
  {
    key: 'market',
    label: '行情与流动性',
    fields: [
      { key: 'amountRange', label: '成交额 (亿)', suffix: '亿', quickTagGroup: 'amount' },
      { key: 'turnoverRange', label: '换手率 (%)', suffix: '%', step: 0.1, quickTagGroup: 'turnover' },
      { key: 'totalMvRange', label: '总市值 (亿)', suffix: '亿', quickTagGroup: 'totalMv' },
      { key: 'floatMvRange', label: '流通市值 (亿)', suffix: '亿', quickTagGroup: 'floatMv' },
      { key: 'volRatio5Range', label: '5日量比 (≥)', step: 0.5 },
      { key: 'volRatio20Range', label: '20日量比 (≥)', step: 0.5 },
    ],
  },
  {
    key: 'momentum',
    label: '动量与趋势',
    fields: [
      { key: 'return1dRange', label: '1日收益 (%)', suffix: '%', step: 0.1, quickTagGroup: 'return1d' },
      { key: 'return3dRange', label: '3日收益 (%)', suffix: '%', step: 0.1, quickTagGroup: 'return3d' },
      { key: 'return5dRange', label: '5日收益 (%)', suffix: '%', step: 0.1, quickTagGroup: 'return5d' },
      // 10/20/60 日收益无可用数据源：technical_indicators.return_* 自 2018 年起全为 NaN，
      // l1_factors.mom_ret_10d/20d/60d 约 35% 的值失真（|涨幅|>100%），不足以支撑筛选。
      // 中长期强弱改由本节末尾的”60日动量”表达。
      { key: 'maGap5Range', label: '5日乖离率 (%)', suffix: '%', step: 0.1, quickTagGroup: 'maGap' },
      { key: 'maGap20Range', label: '20日乖离率 (%)', suffix: '%', step: 0.1 },
      { key: 'rsiRange', label: 'RSI (6日)', step: 1, quickTagGroup: 'rsi' },
      { key: 'kdjKRange', label: 'KDJ-K', step: 1, quickTagGroup: 'kdjK' },
      { key: 'macdHistRange', label: 'MACD 柱', step: 0.01, quickTagGroup: 'macdHist' },
      { key: 'breakout20dRange', label: '60日动量', step: 0.01 },
    ],
  },
  {
    key: 'volatility',
    label: '波动率',
    fields: [
      { key: 'volStd5Range', label: '5日波动率', step: 0.001 },
      { key: 'volStd20Range', label: '20日波动率', step: 0.001, quickTagGroup: 'volStd20' },
      { key: 'volStd60Range', label: '60日波动率', step: 0.001 },
      { key: 'atr14Range', label: 'ATR(14)', step: 0.01, quickTagGroup: 'atr14' },
      { key: 'volDownside20Range', label: '涨跌波动比', step: 0.001 },
      { key: 'volUpside20Range', label: '波动偏度', step: 0.1 },
      { key: 'volRealizedRvRange', label: '已实现波动率', step: 0.001 },
    ],
  },
  {
    key: 'technical',
    label: '技术指标',
    fields: [
      { key: 'maGap10Range', label: '10日乖离率 (%)', suffix: '%', step: 0.1 },
      { key: 'rsi14Range', label: 'RSI (14日)', step: 1 },
      { key: 'mfi14Range', label: 'MFI (14日)', step: 1 },
      { key: 'bbPosRange', label: '布林带位置', step: 0.01 },
      { key: 'adx14Range', label: 'ADX (14日)', step: 1 },
    ],
  },
  {
    key: 'fundFlow',
    label: '资金流',
    fields: [
      { key: 'mainFlowRange', label: '主力净流入 (百万)', step: 10, quickTagGroup: 'mainFlow' },
      { key: 'flowNetAmountRange', label: '净流入 (百万)', step: 10, quickTagGroup: 'mainFlow' },
      { key: 'flowLargeNetRange', label: '大单净额 (百万)', step: 10 },
      { key: 'flowImbalanceRange', label: '买卖失衡', step: 0.01 },
      { key: 'flowMfiRange', label: '资金 MFI', step: 1 },
    ],
  },
  {
    key: 'style',
    label: '风格因子',
    fields: [
      { key: 'beta20Range', label: 'Beta (20日)', step: 0.1, quickTagGroup: 'beta20' },
      { key: 'beta60Range', label: 'Beta (60日)', step: 0.1 },
      { key: 'idioVol20Range', label: '特质波动率', step: 0.001 },
    ],
  },
  {
    key: 'industry',
    label: '行业因子',
    fields: [
      { key: 'indStrength20Range', label: '行业强度 (20日)', step: 0.01 },
      { key: 'indRet20Range', label: '行业收益 (20日)', step: 0.01 },
      { key: 'indRelativeMomentum20Range', label: '相对动量 (20日)', step: 0.01 },
    ],
  },
  {
    key: 'chip',
    label: '筹码分析',
    fields: [
      { key: 'chipProfitRatio20Range', label: '获利盘 (20日)', step: 0.01 },
      { key: 'chipProfitRatio60Range', label: '获利盘 (60日)', step: 0.01 },
      { key: 'chipFloatingRatioRange', label: '成本90分位宽度', step: 0.1 },
    ],
  },
  {
    key: 'fundamental',
    label: '基本面',
    fields: [
      { key: 'peRange', label: 'PE (TTM)', step: 1, quickTagGroup: 'pe' },
      { key: 'roeRange', label: 'ROE (%)', suffix: '%', step: 0.1, quickTagGroup: 'roe' },
      { key: 'profitGrowthRange', label: '利润增速 (%)', suffix: '%', step: 0.1, quickTagGroup: 'profitGrowth' },
      { key: 'pbRange', label: 'PB', step: 0.1, quickTagGroup: 'pb' },
      { key: 'psTtmRange', label: 'PS (TTM)', step: 0.1 },
      { key: 'instOwnershipRange', label: '机构持仓 (%)', suffix: '%', step: 0.1 },
      { key: 'listedDaysRange', label: '上市天数', suffix: '天' },
    ],
  },
  {
    key: 'sector',
    label: '行业/概念',
    fields: [],
  },
];

/**
 * 区间筛选字段 -> 行数据字段映射。
 * 仅当用户把区间从默认值改动过时才生效，保证默认状态即“全量候选”。
 */
interface RangeFilterBinding {
  filterKey: keyof ResearchFiltersState;
  field: keyof ResearchStockRow;
  /** 缺失值视为 0（保持历史行为） */
  coerceZero?: boolean;
}

const RANGE_FILTER_BINDINGS: RangeFilterBinding[] = [
  { filterKey: 'amountRange', field: 'amount' },
  { filterKey: 'turnoverRange', field: 'turnoverRate' },
  { filterKey: 'totalMvRange', field: 'totalMv', coerceZero: true },
  { filterKey: 'floatMvRange', field: 'floatMv', coerceZero: true },
  { filterKey: 'return1dRange', field: 'return1d' },
  { filterKey: 'return3dRange', field: 'return3d', coerceZero: true },
  { filterKey: 'return5dRange', field: 'return5d' },
  { filterKey: 'maGap5Range', field: 'maGap5' },
  { filterKey: 'maGap10Range', field: 'maGap10' },
  { filterKey: 'maGap20Range', field: 'maGap20' },
  { filterKey: 'rsiRange', field: 'rsi' },
  { filterKey: 'rsi14Range', field: 'rsi14' },
  { filterKey: 'kdjKRange', field: 'momKdjK' },
  { filterKey: 'macdHistRange', field: 'macdHist' },
  { filterKey: 'breakout20dRange', field: 'momRet60d' },
  { filterKey: 'volStd5Range', field: 'volStd5' },
  { filterKey: 'volStd20Range', field: 'volStd20' },
  { filterKey: 'volStd60Range', field: 'volStd60' },
  { filterKey: 'atr14Range', field: 'volAtr14' },
  { filterKey: 'volDownside20Range', field: 'volUpDownRatio' },
  { filterKey: 'volUpside20Range', field: 'volSkew' },
  { filterKey: 'volRealizedRvRange', field: 'volRealizedRv' },
  { filterKey: 'mfi14Range', field: 'liqMfi14' },
  { filterKey: 'bbPosRange', field: 'techBbPos' },
  { filterKey: 'adx14Range', field: 'techAdx14' },
  { filterKey: 'mainFlowRange', field: 'mainFlow' },
  { filterKey: 'flowNetAmountRange', field: 'flowNetAmount' },
  { filterKey: 'flowLargeNetRange', field: 'flowLargeNet' },
  { filterKey: 'flowImbalanceRange', field: 'flowImbalanceVolume' },
  { filterKey: 'flowMfiRange', field: 'flowMoneyFlowIndex' },
  { filterKey: 'beta20Range', field: 'styleBeta20' },
  { filterKey: 'beta60Range', field: 'styleBeta60' },
  { filterKey: 'idioVol20Range', field: 'styleIdioVol20' },
  { filterKey: 'indStrength20Range', field: 'indStrength20' },
  { filterKey: 'indRet20Range', field: 'indRet20' },
  { filterKey: 'indRelativeMomentum20Range', field: 'indRelativeMomentum20' },
  { filterKey: 'chipProfitRatio20Range', field: 'chipProfitRatio20' },
  { filterKey: 'chipProfitRatio60Range', field: 'chipProfitRatio60' },
  { filterKey: 'chipFloatingRatioRange', field: 'chipCost90Width' },
  { filterKey: 'peRange', field: 'pe' },
  { filterKey: 'roeRange', field: 'roe' },
  { filterKey: 'profitGrowthRange', field: 'profitGrowth' },
  { filterKey: 'pbRange', field: 'pb', coerceZero: true },
  { filterKey: 'psTtmRange', field: 'psTtm' },
  { filterKey: 'listedDaysRange', field: 'listedDays', coerceZero: true },
];

const SORT_OPTIONS: Array<{ key: SortKey; label: string; field: keyof ResearchStockRow }> = [
  { key: 'score', label: '分数', field: 'score' },
  { key: 'limitUp', label: '连板', field: 'consecutiveLimitUpDays' },
  { key: 'turnover', label: '换手', field: 'turnoverRate' },
  { key: 'amount', label: '成交额', field: 'amount' },
  { key: 'return1d', label: '1日', field: 'return1d' },
  { key: 'return20d', label: '60日动量', field: 'momRet60d' },
  { key: 'volStd20', label: '波动', field: 'volStd20' },
  { key: 'mainFlow', label: '资金', field: 'mainFlow' },
];

/**
 * 需要向 QuantDB 投影请求的字段集合。
 *
 * `/research/universe` 只返回 PG `stock_daily_latest` 的约 50 个字段，而筛选条件和
 * 表格列引用了 100+ 字段——差额全部来自 QuantDB parquet。因此这里由筛选绑定、
 * 表格列、排序字段共同推导出请求字段，避免手工维护列表与 UI 脱节。
 */
const QUANTDB_PROJECTION_FIELDS: string[] = Array.from(
  new Set<string>([
    ...RANGE_FILTER_BINDINGS.map((binding) => binding.field as string),
    ...Object.keys(COLUMN_DEFS),
    ...SORT_OPTIONS.map((option) => option.field as string),
  ])
);

/** 自选 / 研究池在特征缺失时的占位行 */
const makeFallbackRow = (key: string, code: string, name: string, score: number): ResearchStockRow => ({
  key,
  code,
  name,
  score,
  modelId: '',
  runId: '',
  rank: 0,
  signal: 'hold' as SignalType,
  latestChange: 0,
  totalReturn: null,
  consecutiveLimitUpDays: 0,
  volumeTrend3d: 0,
  volumeTrend5d: false,
  turnoverRate: 0,
  amount: 0,
  sector: '',
  concept: '',
  conceptTags: [],
  indexTags: [],
  riskFlags: [],
  closePrice: 0,
  pe: 0,
  roe: 0,
  profitGrowth: 0,
  rsi: 0,
  mainFlow: 0,
  flowNetAmount: 0,
  instOwnership: 0,
  ma5: 0,
  ma10: 0,
  maGap5: 0,
  maGap10: 0,
  maGap20: 0,
  volRatio5: 0,
  return1d: 0,
  return3d: 0,
  return5d: 0,
  return10d: 0,
  return20d: 0,
  return60d: 0,
  pb: 0,
  totalMv: 0,
  floatMv: 0,
  listedDays: 0,
  isSt: false,
  isTradable: true,
  isHs300: false,
  isCsi500: false,
  isCsi1000: false,
  thesis: '',
});

/* ------------------------------------------------------------------ *
 * 展示组件
 * ------------------------------------------------------------------ */

const ResearchMetricCard: React.FC<{
  icon: any;
  label: string;
  value: string | number;
  subLabel: string;
  accentColor: string;
}> = ({ icon: Icon, label, value, subLabel, accentColor }) => (
  <motion.div
    whileHover={{ y: -4, transition: { type: 'spring', stiffness: 400, damping: 15 } }}
    className="group relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs transition-all duration-300 hover:shadow-md hover:border-slate-300"
  >
    {/* 背景微光晕 */}
    <div
      className="pointer-events-none absolute -right-8 -top-8 h-28 w-28 rounded-full opacity-10 blur-2xl transition-all duration-500 group-hover:scale-125 group-hover:opacity-25"
      style={{ backgroundColor: accentColor }}
    />

    {/* 容器右上角统一图标胶囊 */}
    <div
      className="absolute top-4 right-4 flex h-10 w-10 items-center justify-center rounded-xl border transition-all duration-300 group-hover:scale-105 shadow-2xs"
      style={{
        backgroundColor: `${accentColor}12`,
        borderColor: `${accentColor}25`,
        color: accentColor,
      }}
    >
      <Icon className="h-5 w-5" style={{ color: accentColor }} />
    </div>

    {/* 指标文本内容 */}
    <div className="relative z-10 flex flex-col pr-12">
      <div className="flex items-center gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: accentColor }} />
        <span className="text-xs font-bold uppercase tracking-wider text-slate-500">{label}</span>
      </div>

      <div className="mt-2.5 mb-1 text-3xl font-extrabold tracking-tight text-slate-900 transition-colors group-hover:text-slate-800">
        {value}
      </div>

      <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400">
        <span className="truncate">{subLabel}</span>
      </div>
    </div>
  </motion.div>
);

/**
 * 快捷标签配置：点击后直接设置对应筛选字段的区间值。
 * 每个标签定义 label、对应的 filterKey 和区间 [min, max]。
 */
interface QuickTagConfig {
  label: string;
  filterKey: keyof ResearchFiltersState;
  range: [number, number];
}

const QUICK_TAGS: Record<string, QuickTagConfig[]> = {
  totalMv: [
    { label: '小市值', filterKey: 'totalMvRange', range: [0, 50] },
    { label: '中市值', filterKey: 'totalMvRange', range: [50, 300] },
    { label: '大市值', filterKey: 'totalMvRange', range: [300, 1000000] },
    { label: '巨型蓝筹', filterKey: 'totalMvRange', range: [2000, 1000000] },
  ],
  floatMv: [
    { label: '小盘', filterKey: 'floatMvRange', range: [0, 50] },
    { label: '中盘', filterKey: 'floatMvRange', range: [50, 200] },
    { label: '大盘', filterKey: 'floatMvRange', range: [200, 1000000] },
  ],
  amount: [
    { label: '低成交', filterKey: 'amountRange', range: [0, 3] },
    { label: '中成交', filterKey: 'amountRange', range: [3, 10] },
    { label: '高成交', filterKey: 'amountRange', range: [10, 100000] },
  ],
  turnover: [
    { label: '低换手', filterKey: 'turnoverRange', range: [0, 3] },
    { label: '中换手', filterKey: 'turnoverRange', range: [3, 8] },
    { label: '高换手', filterKey: 'turnoverRange', range: [8, 100] },
  ],
  pe: [
    { label: '低估值', filterKey: 'peRange', range: [0, 15] },
    { label: '合理估值', filterKey: 'peRange', range: [15, 30] },
    { label: '高估值', filterKey: 'peRange', range: [30, 100000] },
  ],
  roe: [
    { label: '高ROE', filterKey: 'roeRange', range: [15, 1000] },
    { label: '中ROE', filterKey: 'roeRange', range: [5, 15] },
  ],
  rsi: [
    { label: '超卖', filterKey: 'rsiRange', range: [0, 30] },
    { label: '中性', filterKey: 'rsiRange', range: [30, 70] },
    { label: '超买', filterKey: 'rsiRange', range: [70, 100] },
  ],
  return1d: [
    { label: '大涨', filterKey: 'return1dRange', range: [3, 100] },
    { label: '小涨', filterKey: 'return1dRange', range: [1, 100] },
    { label: '跌', filterKey: 'return1dRange', range: [-100, 0] },
    { label: '大跌', filterKey: 'return1dRange', range: [-100, -3] },
  ],
  return3d: [
    { label: '强势', filterKey: 'return3dRange', range: [5, 100] },
    { label: '温和', filterKey: 'return3dRange', range: [2, 100] },
    { label: '走弱', filterKey: 'return3dRange', range: [-100, 0] },
  ],
  return5d: [
    { label: '强势', filterKey: 'return5dRange', range: [8, 100] },
    { label: '温和', filterKey: 'return5dRange', range: [3, 100] },
    { label: '走弱', filterKey: 'return5dRange', range: [-100, -2] },
  ],
  maGap: [
    { label: '超跌', filterKey: 'maGap5Range', range: [-100, -5] },
    { label: '贴线', filterKey: 'maGap5Range', range: [-3, 3] },
    { label: '乖离放大', filterKey: 'maGap5Range', range: [5, 100] },
  ],
  volStd20: [
    { label: '低波动', filterKey: 'volStd20Range', range: [0, 0.02] },
    { label: '中波动', filterKey: 'volStd20Range', range: [0.02, 0.05] },
    { label: '高波动', filterKey: 'volStd20Range', range: [0.05, 100] },
  ],
  mainFlow: [
    { label: '资金净流入', filterKey: 'mainFlowRange', range: [500, 1000000] },
    { label: '大幅流入', filterKey: 'mainFlowRange', range: [2000, 1000000] },
    { label: '资金净流出', filterKey: 'mainFlowRange', range: [-1000000, -500] },
  ],
  profitGrowth: [
    { label: '高增长', filterKey: 'profitGrowthRange', range: [30, 100000] },
    { label: '正增长', filterKey: 'profitGrowthRange', range: [0, 100000] },
    { label: '负增长', filterKey: 'profitGrowthRange', range: [-100000, 0] },
  ],
  beta20: [
    { label: '防守(低Beta)', filterKey: 'beta20Range', range: [-3, 0.8] },
    { label: '中性', filterKey: 'beta20Range', range: [0.8, 1.2] },
    { label: '进攻(高Beta)', filterKey: 'beta20Range', range: [1.2, 3] },
  ],
  pb: [
    { label: '低PB', filterKey: 'pbRange', range: [0, 1.5] },
    { label: '中PB', filterKey: 'pbRange', range: [1.5, 3] },
    { label: '高PB', filterKey: 'pbRange', range: [3, 100000] },
  ],
  atr14: [
    { label: '低ATR', filterKey: 'atr14Range', range: [0, 0.5] },
    { label: '中ATR', filterKey: 'atr14Range', range: [0.5, 1.5] },
    { label: '高ATR', filterKey: 'atr14Range', range: [1.5, 1000] },
  ],
  kdjK: [
    { label: '超卖', filterKey: 'kdjKRange', range: [0, 20] },
    { label: '中性', filterKey: 'kdjKRange', range: [20, 80] },
    { label: '超买', filterKey: 'kdjKRange', range: [80, 100] },
  ],
  macdHist: [
    { label: '红柱(多头)', filterKey: 'macdHistRange', range: [0.01, 100] },
    { label: '绿柱(空头)', filterKey: 'macdHistRange', range: [-100, -0.01] },
  ],
};

/** 判断某个快捷标签是否处于激活状态（当前值与标签 range 完全一致） */
const isQuickTagActive = (tag: QuickTagConfig, currentValue: [number, number] | number): boolean => {
  if (!Array.isArray(currentValue)) return false;
  return currentValue[0] === tag.range[0] && currentValue[1] === tag.range[1];
};

/**
 * 范围输入组件 - 用于投研筛选器手动输入
 * 传入数组时渲染双端区间，传入数字时渲染单值阈值。
 * 支持通过 quickTags 属性在输入框上方显示快捷标签。
 */
const RangeInput: React.FC<{
  label?: string;
  value: [number, number] | number;
  onChange: (val: any) => void;
  placeholder?: [string, string] | string;
  prefix?: string;
  suffix?: string;
  step?: number;
  quickTags?: QuickTagConfig[];
  onQuickTagClick?: (tag: QuickTagConfig) => void;
}> = ({ label, value, onChange, placeholder, prefix, suffix, step = 1, quickTags, onQuickTagClick }) => {
  const isRange = Array.isArray(value);
  return (
    <div className="space-y-0.5">
      {label && <div className="truncate text-[10px] font-black uppercase tracking-tight text-slate-500">{label}</div>}
      {quickTags && quickTags.length > 0 && onQuickTagClick && (
        <div className="flex flex-wrap gap-1 pb-0.5">
          {quickTags.map((tag) => {
            const active = isQuickTagActive(tag, value);
            return (
              <button
                key={tag.label}
                type="button"
                onClick={() => onQuickTagClick(tag)}
                className={`rounded-md border px-1.5 py-px text-[9px] font-bold transition-all duration-200 ${
                  active
                    ? 'border-blue-500 bg-blue-500 text-white shadow-sm'
                    : 'border-slate-200 bg-slate-50 text-slate-500 hover:border-blue-300 hover:bg-blue-50 hover:text-blue-600'
                }`}
              >
                {tag.label}
              </button>
            );
          })}
        </div>
      )}
      <div className="flex items-center gap-1">
        <InputNumber
          className="research-next-input-number flex-1"
          size="small"
          placeholder={isRange ? (Array.isArray(placeholder) ? placeholder[0] : 'Min') : (typeof placeholder === 'string' ? placeholder : '阈值')}
          value={isRange ? value[0] : value}
          onChange={(v) => {
            if (isRange) onChange([v ?? 0, value[1]]);
            else onChange(v ?? 0);
          }}
          prefix={prefix}
          suffix={suffix}
          step={step}
          controls={false}
        />
        {isRange && (
          <>
            <div className="h-[1px] w-1.5 bg-slate-300" />
            <InputNumber
              className="research-next-input-number flex-1"
              size="small"
              placeholder={Array.isArray(placeholder) ? placeholder[1] : 'Max'}
              value={value[1]}
              onChange={(v) => onChange([value[0], v ?? 0])}
              prefix={prefix}
              suffix={suffix}
              step={step}
              controls={false}
            />
          </>
        )}
      </div>
    </div>
  );
};

/** 列显示控制：按 COLUMN_GROUPS 分组勾选 */
const ColumnVisibilityControl: React.FC<{
  visibleGroups: Set<string>;
  onChange: (next: Set<string>) => void;
}> = ({ visibleGroups, onChange }) => {
  const toggle = (key: string, checked: boolean) => {
    const next = new Set(visibleGroups);
    if (checked) next.add(key);
    else next.delete(key);
    onChange(next);
  };

  const visibleColumnCount = COLUMN_GROUPS
    .filter((group) => visibleGroups.has(group.key))
    .reduce((total, group) => total + group.columns.length, 0);

  const content = (
    <div className="w-[300px]">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-[11px] font-black uppercase tracking-widest text-slate-400">列分组显示</span>
        <span className="rounded-lg bg-slate-100 px-2 py-0.5 text-[10px] font-black text-slate-500">
          {visibleColumnCount} 列
        </span>
      </div>
      <div className="grid max-h-[320px] grid-cols-2 gap-y-2 overflow-y-auto custom-scrollbar pr-1">
        {COLUMN_GROUPS.map((group) => (
          <Checkbox
            key={group.key}
            checked={visibleGroups.has(group.key)}
            onChange={(e) => toggle(group.key, e.target.checked)}
          >
            <span className="text-xs font-bold text-slate-600">
              {group.label}
              <span className="ml-1 text-[10px] font-medium text-slate-400">({group.columns.length})</span>
            </span>
          </Checkbox>
        ))}
      </div>
      <div className="mt-3 flex gap-2 border-t border-slate-100 pt-3">
        <Button
          size="small"
          className="flex-1 rounded-lg text-[11px] font-bold"
          onClick={() => onChange(new Set(COLUMN_GROUPS.map((group) => group.key)))}
        >
          全选
        </Button>
        <Button
          size="small"
          className="flex-1 rounded-lg text-[11px] font-bold"
          onClick={() => onChange(new Set(COLUMN_GROUPS.filter((group) => group.defaultVisible).map((group) => group.key)))}
        >
          默认
        </Button>
        <Button
          size="small"
          className="flex-1 rounded-lg text-[11px] font-bold"
          onClick={() => onChange(new Set(['identity']))}
        >
          最简
        </Button>
      </div>
    </div>
  );

  return (
    <Popover content={content} trigger="click" placement="bottomRight">
      <Button
        icon={<Columns3 className="h-4 w-4" />}
        className="h-10 rounded-[18px] border-slate-200 px-3 text-xs font-bold text-slate-600 transition-all hover:border-blue-400 hover:text-blue-500"
      >
        列显示 ({visibleGroups.size})
      </Button>
    </Popover>
  );
};

/* ------------------------------------------------------------------ *
 * 主页面
 * ------------------------------------------------------------------ */

export const ResearchPlatformPage: React.FC = () => {
  const currentMarket = useAppSelector(selectCurrentMarket);
  const marketConfig = getMarketConfig(currentMarket);

  // ---- 数据源状态 ----
  const [availableModels, setAvailableModels] = React.useState<ResearchModelOption[]>([]);
  const [selectedModelId, setSelectedModelId] = React.useState<string>('');
  const [availableRuns, setAvailableRuns] = React.useState<ResearchRunOption[]>([]);
  const [selectedRunId, setSelectedRunId] = React.useState<string>('');
  // 选中数据日 T（pred.parquet 口径）——批次选择的唯一事实源，
  // 个股列表按日期直读 pred.parquet 全市场分数截面
  const [selectedDate, setSelectedDate] = React.useState<string>('');
  const [candidatePool, setCandidatePool] = React.useState<ResearchStockRow[]>([]);
  const [overview, setOverview] = React.useState<any>(null);
  const [overviewLoading, setOverviewLoading] = React.useState<boolean>(false);
  const [modelsLoading, setModelsLoading] = React.useState<boolean>(false);
  const [modelsError, setModelsError] = React.useState<string | null>(null);
  const [runsLoading, setRunsLoading] = React.useState<boolean>(false);
  const [runsError, setRunsError] = React.useState<string | null>(null);
  const [syncing, setSyncing] = React.useState<boolean>(false);
  const [refreshNonce, setRefreshNonce] = React.useState<number>(0);
  const [loadRange, setLoadRange] = React.useState<number>(500);
  // ---- 推理批次日历（数据源 pred.parquet）----
  const [calendarOpen, setCalendarOpen] = React.useState<boolean>(false);
  const [calendarMonth, setCalendarMonth] = React.useState<string>(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  });

  // ---- QuantDB 全字段因子缓存（ref 承担去重判断，state 触发重渲染） ----
  const [quantDbFeatures, setQuantDbFeatures] = React.useState<Record<string, Partial<ResearchStockRow>>>({});
  const quantDbFeaturesRef = React.useRef<Record<string, Partial<ResearchStockRow>>>({});
  const quantDbRawRef = React.useRef<Record<string, QuantDbFeatures>>({});
  // 详情弹窗需要原始分类结构（FactorPanel 按类别渲染），与摊平版本分开保存
  const [quantDbRaw, setQuantDbRaw] = React.useState<Record<string, QuantDbFeatures>>({});
  // 全池投影因子：筛选与排序在分页之前执行，必须覆盖整个候选池而非当前页
  const [universeFeatures, setUniverseFeatures] = React.useState<Record<string, Partial<ResearchStockRow>>>({});
  const [universeFeaturesLoading, setUniverseFeaturesLoading] = React.useState<boolean>(false);

  // ---- 视图状态 ----
  const [keyword, setKeyword] = React.useState<string>('');
  const [activeDataSource, setActiveDataSource] = React.useState<DataSourceTab>('candidates');
  const [sortKey, setSortKey] = React.useState<SortKey>('score');
  const [detailModalOpen, setDetailModalOpen] = React.useState<boolean>(false);
  const [selectedStockKey, setSelectedStockKey] = React.useState<string | null>(null);
  const [klineData, setKlineData] = React.useState<any[]>([]);
  const [klineLoading, setKlineLoading] = React.useState<boolean>(false);
  // ---- K线参考线：模式 + 数值 ----
  type RefLineMode = 'off' | 'above' | 'below' | 'range';
  const [refLineMode, setRefLineMode] = React.useState<RefLineMode>('off');
  const [refLineValue, setRefLineValue] = React.useState<number | null>(null);
  const [refLineValue2, setRefLineValue2] = React.useState<number | null>(null);
  const [riskScore, setRiskScore] = React.useState<import('../services/researchService').RiskScoreData | null>(null);
  const [riskLoading, setRiskLoading] = React.useState<boolean>(false);

  // ---- 列显示控制 ----
  const [tableDensity, setTableDensity] = React.useState<'compact' | 'default' | 'relaxed'>('compact');
  const [visibleGroups, setVisibleGroups] = React.useState<Set<string>>(() => {
    const fallback = new Set(COLUMN_GROUPS.filter((group) => group.defaultVisible).map((group) => group.key));
    try {
      const stored = window.localStorage.getItem(VISIBLE_GROUPS_STORAGE_KEY);
      if (!stored) return fallback;
      const parsed = JSON.parse(stored);
      if (!Array.isArray(parsed) || parsed.length === 0) return fallback;
      const known = parsed.filter((key: unknown) => typeof key === 'string' && COLUMN_GROUPS.some((group) => group.key === key));
      return known.length > 0 ? new Set(known as string[]) : fallback;
    } catch {
      return fallback;
    }
  });

  React.useEffect(() => {
    try {
      window.localStorage.setItem(VISIBLE_GROUPS_STORAGE_KEY, JSON.stringify(Array.from(visibleGroups)));
    } catch {
      // localStorage 不可用时静默降级，不影响主流程
    }
  }, [visibleGroups]);

  // ---- 分页状态 ----
  const [candidatePage, setCandidatePage] = React.useState<number>(1);
  const [candidatePageSize, setCandidatePageSize] = React.useState<number>(10);
  const [watchlistPage, setWatchlistPage] = React.useState<number>(1);
  const [watchlistPageSize, setWatchlistPageSize] = React.useState<number>(12);
  const [poolPage, setPoolPage] = React.useState<number>(1);
  const [poolPageSize, setPoolPageSize] = React.useState<number>(12);

  // ---- 自选 / 研究池 ----
  const [watchlistData, setWatchlistData] = React.useState<WatchlistRow[]>([]);
  const [watchlistLoading, setWatchlistLoading] = React.useState<boolean>(false);
  const [watchlistTotal, setWatchlistTotal] = React.useState<number>(0);
  const [poolData, setPoolData] = React.useState<ResearchPoolRow[]>([]);
  const [poolLoading, setPoolLoading] = React.useState<boolean>(false);
  const [poolTotal, setPoolTotal] = React.useState<number>(0);
  const [watchlistFeatures, setWatchlistFeatures] = React.useState<Record<string, ResearchStockRow>>({});
  const [poolFeatures, setPoolFeatures] = React.useState<Record<string, ResearchStockRow>>({});

  // ---- 筛选状态：草稿(draft) 与 已应用(applied) 分离 ----
  const [draftFilters, setDraftFilters] = React.useState<ResearchFiltersState>(() => cloneFilters(DEFAULT_RESEARCH_FILTERS));
  const [appliedFilters, setAppliedFilters] = React.useState<ResearchFiltersState>(() => cloneFilters(DEFAULT_RESEARCH_FILTERS));
  const [activePreset, setActivePreset] = React.useState<string | null>(null);
  const [activeFilterSections, setActiveFilterSections] = React.useState<FilterSectionKey[]>(['common']);

  // 用 ref 递增刷新计数：异步回调里读取 state 会拿到过期闭包值
  const refreshCounter = React.useRef<number>(0);
  const triggerRefresh = (): void => {
    refreshCounter.current += 1;
    setRefreshNonce(refreshCounter.current);
  };

  const setFilterField = <K extends keyof ResearchFiltersState>(key: K, value: ResearchFiltersState[K]): void => {
    setDraftFilters({ ...draftFilters, [key]: value });
  };

  const hasPendingFilterChanges = React.useMemo(
    () => JSON.stringify(draftFilters) !== JSON.stringify(appliedFilters),
    [draftFilters, appliedFilters]
  );

  const applyCurrentFilters = React.useCallback(() => {
    setAppliedFilters(cloneFilters(draftFilters));
    setCandidatePage(1);
    message.success('筛选条件已成功应用');
  }, [draftFilters]);

  const resetFilters = React.useCallback(() => {
    const fresh = cloneFilters(DEFAULT_RESEARCH_FILTERS);
    setDraftFilters(fresh);
    setAppliedFilters(cloneFilters(DEFAULT_RESEARCH_FILTERS));
    setActivePreset(null);
    setCandidatePage(1);
  }, []);

  /**
   * 参与筛选/排序的池：universe 基础字段优先，QuantDB 投影仅补空缺。
   * 声明在 applyPreset 之前——模板阈值要按本池的分位数实时推导。
   */
  const enrichedPool = React.useMemo(
    () => (Object.keys(universeFeatures).length ? mergePoolFeatures(candidatePool, universeFeatures) : candidatePool),
    [candidatePool, universeFeatures]
  );

  /**
   * 应用快速模板：先回到全量宽松状态，再叠加模板参数，
   * 同时写入 draft 与 applied，做到一键生效。
   */
  const applyPreset = React.useCallback((presetName: string) => {
    const config = PRESET_FILTER_MAP[presetName];
    if (!config) return;

    const next = cloneFilters(DEFAULT_RESEARCH_FILTERS);

    /**
     * 按当前池的分位数取阈值。
     *
     * 各批次的数据分布差异很大（不同模型、不同交易日，PG 与 QuantDB 的覆盖也不同：
     * 例如 chipProfitRatio20 在某批次 p25 就已到 1.00，而另一批次 p25 仅 0.04），
     * 写死阈值必然在部分批次上退化成“选不出”或“全选中”。这里改为按分位取值。
     */
    const quantile = (field: keyof ResearchStockRow, percentile: number): number | null => {
      const xs = enrichedPool
        .map((item) => item[field])
        .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
        .sort((a, b) => a - b);
      if (!xs.length) return null;
      const idx = Math.min(xs.length - 1, Math.max(0, Math.round((xs.length - 1) * percentile)));
      return xs[idx];
    };

    /** 取该字段的高分位作为下限（选“强”的一端） */
    const setMin = (
      key: keyof ResearchFiltersState,
      field: keyof ResearchStockRow,
      percentile: number,
      upper: number
    ): void => {
      const cut = quantile(field, percentile);
      if (cut !== null) (next[key] as [number, number]) = [cut, upper];
    };

    /** 取该字段的低分位作为上限（选“低/便宜”的一端） */
    const setMax = (
      key: keyof ResearchFiltersState,
      field: keyof ResearchStockRow,
      percentile: number,
      lower: number
    ): void => {
      const cut = quantile(field, percentile);
      if (cut !== null) (next[key] as [number, number]) = [lower, cut];
    };

    if (config.limitUpDays !== undefined) next.limitUpDays = config.limitUpDays;

    // 模型评分没有固定量纲（不同模型/批次可能整体为负），绝对阈值会失效
    if (config.scoreTopPercent !== undefined) {
      const cut = quantile('score', 1 - config.scoreTopPercent / 100);
      if (cut !== null) next.minScore = cut;
    }

    if (config.roeTop !== undefined) setMin('roeRange', 'roe', 1 - config.roeTop, 100000);
    if (config.totalMvTop !== undefined) setMin('totalMvRange', 'totalMv', 1 - config.totalMvTop, 1000000);
    if (config.turnoverTop !== undefined) setMin('turnoverRange', 'turnoverRate', 1 - config.turnoverTop, 100000);
    if (config.amountTop !== undefined) setMin('amountRange', 'amount', 1 - config.amountTop, 100000);
    if (config.volStd20Top !== undefined) setMin('volStd20Range', 'volStd20', 1 - config.volStd20Top, 100000);
    if (config.momRet60dTop !== undefined) setMin('breakout20dRange', 'momRet60d', 1 - config.momRet60dTop, 100000);
    if (config.indStrength20Top !== undefined)
      setMin('indStrength20Range', 'indStrength20', 1 - config.indStrength20Top, 100000);
    if (config.flowNetAmountTop !== undefined)
      setMin('flowNetAmountRange', 'flowNetAmount', 1 - config.flowNetAmountTop, 1000000);
    if (config.chipProfitRatio20Top !== undefined)
      setMin('chipProfitRatio20Range', 'chipProfitRatio20', 1 - config.chipProfitRatio20Top, 1);

    if (config.maGap20Bottom !== undefined) setMax('maGap20Range', 'maGap20', config.maGap20Bottom, -100000);
    if (config.rsiBottom !== undefined) setMax('rsiRange', 'rsi', config.rsiBottom, 0);
    if (config.peBottom !== undefined) setMax('peRange', 'pe', config.peBottom, 0);
    if (config.pbBottom !== undefined) setMax('pbRange', 'pb', config.pbBottom, 0);

    setDraftFilters(next);
    setAppliedFilters(cloneFilters(next));
    setActivePreset(presetName);
    setCandidatePage(1);
    message.success(`已应用模板：${presetName}`);
  }, [enrichedPool]);

  /* ------------------------------ 数据加载 ------------------------------ */

  // 初始化加载模型
  React.useEffect(() => {
    let cancelled = false;
    const loadModels = async () => {
      setModelsLoading(true);
      setModelsError(null);
      try {
        const models = await researchService.getAvailableModels(currentMarket);
        if (cancelled) return;
        setAvailableModels(models);
        // 该 effect 仅在市场切换时执行，因此总是重置到新市场的首个模型
        setSelectedModelId(models.length > 0 ? models[0].modelId : '');
      } catch (error) {
        console.error('[ResearchPlatformPage] load models failed:', error);
        if (!cancelled) setModelsError('加载模型列表失败');
      } finally {
        if (!cancelled) setModelsLoading(false);
      }
    };
    void loadModels();
    return () => { cancelled = true; };
  }, [currentMarket]);

  // 模型切换时加载批次
  React.useEffect(() => {
    if (!selectedModelId) {
      setAvailableRuns([]);
      setSelectedRunId('');
      setSelectedDate('');
      return;
    }
    let cancelled = false;
    const loadRuns = async () => {
      setRunsLoading(true);
      setRunsError(null);
      try {
        const runs = await researchService.getInferenceRuns(selectedModelId);
        if (cancelled) return;
        setAvailableRuns(runs);
        // 列表按日期倒序；默认选中最新日期。个股列表按日期直读
        // pred.parquet（B 套），训练测试集日期同样可查看全市场分数
        const first = runs[0];
        setSelectedDate(first?.inferenceDate || '');
        setSelectedRunId(first ? first.runId || `pred_${(first.inferenceDate || '').replaceAll('-', '')}` : '');
      } catch (error) {
        console.error('[ResearchPlatformPage] load runs failed:', error);
        if (!cancelled) setRunsError('加载推理批次失败');
      } finally {
        if (!cancelled) setRunsLoading(false);
      }
    };
    void loadRuns();
    return () => { cancelled = true; };
  }, [selectedModelId, refreshNonce]);

  // 批次切换或同步刷新时加载原始数据（按数据日直读 pred.parquet 全市场分数）
  React.useEffect(() => {
    if (!selectedModelId || !selectedDate) {
      setCandidatePool([]);
      return;
    }
    let cancelled = false;
    const loadUniverse = async () => {
      setOverviewLoading(true);
      try {
        const result = await researchService.getResearchUniverseByDate(selectedModelId, selectedDate, 10000);
        if (cancelled) return;
        setCandidatePool(
          (result.candidates || []).map((raw: any) => {
            const item = camelizeRow(raw || {});
            return {
              ...item,
              score: safeNum(item?.score, 0),
              // null 保留：universe（SDL 缺失）无值时留给 QuantDB 投影填充，
              // 若默认 0 会被 mergePoolFeatures 视为合法涨跌幅而不覆盖
              latestChange: item?.latestChange != null ? safeNum(item?.latestChange, 0) : null,
              consecutiveLimitUpDays: safeNum(item?.consecutiveLimitUpDays, 0),
              turnoverRate: item?.turnoverRate != null ? safeNum(item?.turnoverRate, 0) : null,
              amount: item?.amount != null ? safeNum(item?.amount, 0) : null,
              pe: item?.pe != null ? safeNum(item?.pe, 0) : null,
              roe: item?.roe != null ? normalizeRoe(item?.roe) : null,
              rsi: item?.rsi != null ? safeNum(item?.rsi, 0) : null,
              profitGrowth: item?.profitGrowth ?? null,
              mainFlow: item?.mainFlow ?? null,
              instOwnership: item?.instOwnership ?? null,
              ma5: item?.ma5 != null ? safeNum(item?.ma5, 0) : null,
              ma10: item?.ma10 != null ? safeNum(item?.ma10, 0) : null,
              ma20: item?.ma20 != null ? safeNum(item?.ma20, 0) : null,
              pb: item?.pb != null ? safeNum(item?.pb, 0) : null,
              totalMv: item?.totalMv ?? item?.marketCap ?? null,
              floatMv: item?.floatMv ?? null,
              listedDays: item?.listedDays ?? null,
              return3d: item?.return3d ?? null,
              maGap5: item?.maGap5 ?? null,
              maGap10: item?.maGap10 ?? null,
              maGap20: item?.maGap20 ?? null,
              rsi14: item?.rsi14 ?? item?.rsi ?? null,
              volRatio5: item?.volRatio5 ?? item?.volumeRatio5 ?? null,
              volRatio20: item?.volRatio20 ?? item?.volumeRatio20 ?? null,
              atr: item?.atr ?? item?.volAtr14 ?? null,
              macdHist: item?.macdHist ?? null,
              conceptTags: Array.isArray(item?.conceptTags) ? item.conceptTags : [],
              indexTags: Array.isArray(item?.indexTags) ? item.indexTags : [],
              riskFlags: Array.isArray(item?.riskFlags) ? item.riskFlags : [],
              concept: item?.concept || '',
              isSt: Boolean(item?.isSt),
              isTradable: item?.isTradable !== undefined ? Boolean(item?.isTradable) : true,
              isHs300: Boolean(item?.isHs300),
              isCsi500: Boolean(item?.isCsi500),
              isCsi1000: Boolean(item?.isCsi1000),
              confidence: item?.confidence || 'watch',
            } as ResearchStockRow;
          })
        );
        currentScoreDist = result?.summary?.scoreDistribution || null;
        setOverview(result);
      } catch (error) {
        console.error('[ResearchPlatformPage] load universe failed:', error);
        if (!cancelled) setCandidatePool([]);
      } finally {
        if (!cancelled) setOverviewLoading(false);
      }
    };
    void loadUniverse();
    return () => { cancelled = true; };
  }, [selectedModelId, selectedDate, appliedFilters.minScore, appliedFilters.excludeSt, refreshNonce, loadRange]);

  const handleSyncCandidates = async () => {
    if (!selectedModelId) {
      message.warning('请先选择研究模型');
      return;
    }
    setSyncing(true);
    try {
      triggerRefresh();
      message.success('候选池同步请求已发起');
    } finally {
      // 延迟一个 tick，避免按钮闪烁
      setTimeout(() => setSyncing(false), 300);
    }
  };

  // 加载自选数据（页面初始化时即加载，用于显示总数）
  React.useEffect(() => {
    let cancelled = false;
    const loadWatchlist = async () => {
      setWatchlistLoading(true);
      try {
        const result = await researchService.getWatchlist(100, 0);
        if (cancelled) return;
        setWatchlistData(result.items.map((item) => ({
          key: item.symbol,
          symbol: item.symbol,
          stockName: item.stockName,
          addedAt: item.addedAt,
          sourceRunId: item.sourceRunId,
          notes: item.notes,
          tags: item.tags,
        })));
        setWatchlistTotal(result.total || 0);
      } catch (error) {
        console.error('[ResearchPlatformPage] load watchlist failed:', error);
        if (!cancelled) {
          setWatchlistData([]);
          setWatchlistTotal(0);
        }
      } finally {
        if (!cancelled) setWatchlistLoading(false);
      }
    };
    void loadWatchlist();
    return () => { cancelled = true; };
  }, [refreshNonce]);

  // 加载研究池数据（页面初始化时即加载，用于显示总数）
  React.useEffect(() => {
    let cancelled = false;
    const loadPool = async () => {
      setPoolLoading(true);
      try {
        const result = await researchService.getResearchPool({ limit: 100, offset: 0 });
        if (cancelled) return;
        setPoolData(result.items.map((item) => ({
          key: item.symbol,
          symbol: item.symbol,
          stockName: item.stockName,
          addedAt: item.addedAt,
          sourceRunId: item.sourceRunId,
          modelId: item.modelId,
          fusionScore: item.fusionScore,
          thesisSummary: item.thesisSummary,
          status: item.status,
          notes: item.notes,
          tags: item.tags,
        })));
        setPoolTotal(result.total || 0);
      } catch (error) {
        console.error('[ResearchPlatformPage] load pool failed:', error);
        if (!cancelled) {
          setPoolData([]);
          setPoolTotal(0);
        }
      } finally {
        if (!cancelled) setPoolLoading(false);
      }
    };
    void loadPool();
    return () => { cancelled = true; };
  }, [refreshNonce]);

  // 富化自选特征数据
  React.useEffect(() => {
    if (!watchlistData.length) {
      setWatchlistFeatures({});
      return;
    }
    const symbols = watchlistData.map((item) => item.symbol);
    researchService.getFeaturesBySymbols(symbols)
      .then((features) => {
        const map: Record<string, ResearchStockRow> = {};
        features.forEach((f) => { map[f.code] = f; });
        setWatchlistFeatures(map);
      })
      .catch(() => setWatchlistFeatures({}));
  }, [watchlistData]);

  // 富化研究池特征数据
  React.useEffect(() => {
    if (!poolData.length) {
      setPoolFeatures({});
      return;
    }
    const symbols = poolData.map((item) => item.symbol);
    researchService.getFeaturesBySymbols(symbols)
      .then((features) => {
        const map: Record<string, ResearchStockRow> = {};
        features.forEach((f) => { map[f.code] = f; });
        setPoolFeatures(map);
      })
      .catch(() => setPoolFeatures({}));
  }, [poolData]);

  /* ------------------------------ 自选/研究池操作 ------------------------------ */

  const handleAddToWatchlist = async (stock: ResearchStockRow) => {
    try {
      await researchService.addToWatchlist(stock.code, {
        runId: stock.runId,
        stockName: stock.name,
        featuresSnapshot: stock as unknown as Record<string, unknown>,
      });
      message.success(`已加入自选: ${stock.name}`);
      triggerRefresh();
    } catch (error) {
      console.error('[ResearchPlatformPage] add to watchlist failed:', error);
      message.error('加入自选失败');
    }
  };

  const handleAddToResearchPool = async (stock: ResearchStockRow) => {
    try {
      await researchService.addToResearchPool(stock.code, {
        runId: stock.runId,
        stockName: stock.name,
        modelId: selectedModelId,
        fusionScore: stock.score,
        thesisSummary: stock.thesis,
        featuresSnapshot: stock as unknown as Record<string, unknown>,
      });
      message.success(`已加入研究池: ${stock.name}`);
      triggerRefresh();
    } catch (error) {
      console.error('[ResearchPlatformPage] add to research pool failed:', error);
      message.error('加入研究池失败');
    }
  };

  const handleRemoveFromWatchlist = async (symbol: string, stockName: string | null) => {
    try {
      await researchService.removeFromWatchlist(symbol);
      message.success(`已从自选移除: ${stockName || symbol}`);
      triggerRefresh();
    } catch (error) {
      console.error('[ResearchPlatformPage] remove from watchlist failed:', error);
      message.error('移出自选失败');
    }
  };

  const handleRemoveFromPool = async (symbol: string, stockName: string | null) => {
    try {
      await researchService.removeFromResearchPool(symbol);
      message.success(`已从研究池移除: ${stockName || symbol}`);
      triggerRefresh();
    } catch (error) {
      console.error('[ResearchPlatformPage] remove from pool failed:', error);
      message.error('移出研究池失败');
    }
  };

  /* ------------------------------ 筛选与排序 ------------------------------ */

  /** 只保留被用户改动过的区间条件，默认值一律跳过 */
  const activeRangeFilters = React.useMemo(
    () => RANGE_FILTER_BINDINGS.filter((binding) => {
      const applied = appliedFilters[binding.filterKey];
      const fallback = DEFAULT_RESEARCH_FILTERS[binding.filterKey];
      return Array.isArray(applied) && !isSameRange(applied, fallback);
    }),
    [appliedFilters]
  );

  /**
   * 全池 QuantDB 投影富化（按选中数据日 T 读历史截面）。
   *
   * 筛选与排序发生在分页之前，若只富化当前页，任何依赖 QuantDB 字段的条件
   * （动量/波动/资金流/筹码/风格等 29 项）都会因为字段为 undefined 而静默失效。
   * 因此候选池加载完成后，一次性按投影字段拉取整池。
   * 传 selectedDate：涨跌幅/收盘价/return_*（T 后 N 日真实收益）等字段
   * 只有按 T 所在行读取才有值，读最新行 return_* 永远是 NaN。
   */
  React.useEffect(() => {
    const symbols = Array.from(
      new Set(candidatePool.map((item) => toSuffixSymbol(item.code)).filter(Boolean))
    );
    if (!symbols.length) {
      setUniverseFeatures({});
      return;
    }

    let cancelled = false;
    setUniverseFeaturesLoading(true);
    void researchService
      .getProjectedQuantDbFeatures(symbols, QUANTDB_PROJECTION_FIELDS, selectedDate)
      .then((bySymbol) => {
        if (cancelled) return;
        const next: Record<string, Partial<ResearchStockRow>> = {};
        Object.entries(bySymbol).forEach(([symbol, values]) => {
          next[symbol] = flattenProjectedValues(values);
        });
        setUniverseFeatures(next);
      })
      .finally(() => {
        if (!cancelled) setUniverseFeaturesLoading(false);
      });
    return () => { cancelled = true; };
  }, [candidatePool, selectedDate]);

  /** 参与筛选/排序的池：universe 基础字段优先，QuantDB 投影仅补空缺 */

  const filteredRows = React.useMemo(() => {
    const matches: ResearchStockRow[] = [];
    const lowerKeyword = keyword.trim().toLowerCase();

    enrichedPool.forEach((item) => {
      // --- 核心阈值 ---
      if (safeNum(item.score, 0) < appliedFilters.minScore) return;
      if (safeNum(item.consecutiveLimitUpDays, 0) < appliedFilters.limitUpDays) return;

      // --- 高置信标的 ---
      if (appliedFilters.highConfidenceOnly && item.confidence !== 'high') return;

      // --- 量能持续放大 ---
      if (appliedFilters.volumeTrendOnly && !item.volumeTrend5d) return;

      // --- 剔除 ST / 退市：多维校验 ---
      if (appliedFilters.excludeSt) {
        const upperName = (item.name || '').toUpperCase();
        const isStByName = upperName.includes('ST');
        const isDelisting = (item.name || '').includes('退') || upperName.includes('退市');
        if (
          item.isSt ||
          item.isTradable === false ||
          item.riskFlags?.some((flag) => flag.includes('ST')) ||
          isStByName ||
          isDelisting
        ) return;
      }

      // --- 行业 / 概念 / 指数 ---
      if (appliedFilters.selectedSectors.length > 0 && !appliedFilters.selectedSectors.includes(item.sector)) return;

      if (appliedFilters.selectedConcepts.length > 0) {
        const itemConcepts = item.conceptTags || [];
        if (!appliedFilters.selectedConcepts.some((concept) => itemConcepts.includes(concept))) return;
      }

      if (appliedFilters.selectedIndices.length > 0) {
        const itemIndices = item.indexTags || [];
        if (!appliedFilters.selectedIndices.some((index) => itemIndices.includes(index))) return;
      }

      // --- 指数归属快捷筛选 ---
      const marketType = appliedFilters.marketType;
      if (marketType && marketType !== 'all' && marketType !== '全市场') {
        const idxTags = item.indexTags || [];
        if (marketType === 'hs300' && !idxTags.includes('沪深300')) return;
        if (marketType === 'zz500' && !idxTags.includes('中证500')) return;
        if (marketType === 'zz1000' && !idxTags.includes('中证1000')) return;
      }

      // --- 量比阈值（单值，> 0 才生效） ---
      if (appliedFilters.volRatio5Range > 0) {
        const vr = item.volRatio5;
        if (vr != null && vr < appliedFilters.volRatio5Range) return;
      }
      if (appliedFilters.volRatio20Range > 0) {
        const vr = item.volRatio20;
        if (vr != null && vr < appliedFilters.volRatio20Range) return;
      }

      // --- 机构持仓：底层数据存在负值异常，仅在用户明确设置下限时过滤 ---
      if (appliedFilters.instOwnershipRange[0] > 0) {
        if (item.instOwnership != null && item.instOwnership < appliedFilters.instOwnershipRange[0]) return;
      }

      // --- 通用区间条件（仅改动过的才参与） ---
      for (const binding of activeRangeFilters) {
        const [min, max] = appliedFilters[binding.filterKey] as [number, number];
        const raw = item[binding.field];
        if (raw == null) {
          if (!binding.coerceZero) continue;
          if (0 < min || 0 > max) return;
          continue;
        }
        const value = Number(raw);
        if (!Number.isFinite(value)) continue;
        if (value < min || value > max) return;
      }

      // --- 关键词 ---
      if (lowerKeyword) {
        const nameHit = (item.name || '').toLowerCase().includes(lowerKeyword);
        const codeHit = (item.code || '').toLowerCase().includes(lowerKeyword);
        if (!nameHit && !codeHit) return;
      }

      matches.push({ ...item, isMatched: true });
    });

    const sortField = SORT_OPTIONS.find((option) => option.key === sortKey)?.field ?? 'score';
    matches.sort((left, right) => {
      const leftValue = safeNum(left[sortField], Number.NEGATIVE_INFINITY);
      const rightValue = safeNum(right[sortField], Number.NEGATIVE_INFINITY);
      if (rightValue !== leftValue) return rightValue - leftValue;
      return safeNum(right.score, 0) - safeNum(left.score, 0);
    });

    return matches.slice(0, loadRange).map((item, index) => ({ ...item, rank: index + 1 }));
  }, [appliedFilters, activeRangeFilters, enrichedPool, keyword, sortKey, loadRange]);

  // 当前分页的行（表格展示范围）
  const visibleCandidateRows = React.useMemo(
    () => filteredRows.slice((candidatePage - 1) * candidatePageSize, candidatePage * candidatePageSize),
    [filteredRows, candidatePage, candidatePageSize]
  );

  /**
   * 仅为选中个股拉取 QuantDB 全量分类特征（详情弹窗的 FactorPanel 需要全部 371 字段）。
   * 表格列已由全池投影覆盖，无需在此重复拉取整页，避免单次数 MB 的响应。
   */
  React.useEffect(() => {
    if (!selectedStockKey) return;
    const selected = filteredRows.find((item) => item.key === selectedStockKey);
    const symbol = selected?.code ? toSuffixSymbol(selected.code) : '';
    if (!symbol || quantDbFeaturesRef.current[symbol] !== undefined) return;

    let cancelled = false;
    void researchService.getBatchQuantDbFeatures([symbol]).then((bySymbol) => {
      if (cancelled) return;
      const features = bySymbol[symbol];
      // 未命中也写入空对象，避免同一 symbol 被反复请求
      const next = {
        ...quantDbFeaturesRef.current,
        [symbol]: features ? flattenQuantDbFeatures(features) : {},
      };
      quantDbFeaturesRef.current = next;
      setQuantDbFeatures(next);
      quantDbRawRef.current = { ...quantDbRawRef.current, ...bySymbol };
      setQuantDbRaw(quantDbRawRef.current);
    });
    return () => { cancelled = true; };
  }, [selectedStockKey, filteredRows]);

  // 表格行：全池投影已在 enrichedPool 合并，这里只叠加选中个股的全量字段
  const enrichedCandidateRows = React.useMemo(
    () => mergeQuantDbFeatures(visibleCandidateRows, quantDbFeatures),
    [visibleCandidateRows, quantDbFeatures]
  );

  React.useEffect(() => {
    if (!filteredRows.length) {
      setSelectedStockKey(null);
      return;
    }
    if (!filteredRows.some((item) => item.key === selectedStockKey)) {
      setSelectedStockKey(filteredRows[0].key);
    }
  }, [filteredRows, selectedStockKey]);

  // 详情弹窗需要全字段，优先取已富化的行
  const selectedStock = React.useMemo(() => {
    const base = filteredRows.find((item) => item.key === selectedStockKey);
    if (!base) return null;
    const features = quantDbFeatures[toSuffixSymbol(base.code)];
    if (!features) return base;
    // 不能直接 { ...base, ...features }：QuantDB 全量路径的市值/资金流可能未换算，
    // 且不含 stock_name，无条件覆盖会导致"总市值 21 亿 亿"和"两行都显示代码"。
    const merged: Record<string, unknown> = { ...base };
    for (const [key, value] of Object.entries(features)) {
      const current = merged[key];
      if (current != null && current !== 0 && key !== 'name' && key !== 'code') {
        // 市值/资金流：PG 已换算为亿/百万，QuantDB 全量路径可能仍是原始元
        if (['totalMv', 'floatMv', 'marketCap', 'mainFlow', 'flowNetAmount', 'flowLargeNet', 'flowMediumNet', 'flowSmallNet', 'amount'].includes(key)) continue;
      }
      merged[key] = value;
    }
    return merged as unknown as ResearchStockRow;
  }, [filteredRows, selectedStockKey, quantDbFeatures]);

  /* ------------------------------ 表格列 ------------------------------ */

  /** 按 COLUMN_GROUPS 顺序展开可见分组的列 key */
  const visibleColumnKeys = React.useMemo(
    () => COLUMN_GROUPS
      .filter((group) => visibleGroups.has(group.key))
      .flatMap((group) => group.columns)
      .filter((key) => COLUMN_DEFS[key] !== undefined),
    [visibleGroups]
  );

  const columns = React.useMemo<ColumnsType<ResearchStockRow>>(
    () => buildColumns(visibleColumnKeys),
    [visibleColumnKeys]
  );

  const candidateScrollX = React.useMemo(
    () => Math.max(sumColumnWidth(visibleColumnKeys), 600),
    [visibleColumnKeys]
  );

  const watchlistColumns = React.useMemo<ColumnsType<ResearchStockRow>>(
    () => [
      ...buildColumns(SIMPLE_TABLE_COLUMN_KEYS).map((column) =>
        column.key === 'latestChange' ? { ...column, width: 102, render: rScaledChange } : column
      ),
      {
        key: 'actions',
        title: <span className="whitespace-nowrap">操作</span>,
        width: 80,
        fixed: 'right',
        align: 'center',
        render: (_value, record) => (
          <div className="flex items-center justify-center" onClick={(e) => e.stopPropagation()}>
            <Button
              size="small"
              type="text"
              danger
              onClick={() => handleRemoveFromWatchlist(record.code, record.name)}
              title="从自选移除"
            >
              <span className="text-[10px]">移除</span>
            </Button>
          </div>
        ),
      },
    ],
    []
  );

  const poolColumns = React.useMemo<ColumnsType<ResearchStockRow>>(
    () => [
      ...buildColumns(SIMPLE_TABLE_COLUMN_KEYS).map((column) =>
        column.key === 'latestChange' ? { ...column, width: 102, render: rScaledChange } : column
      ),
      {
        key: 'actions',
        title: <span className="whitespace-nowrap">操作</span>,
        width: 80,
        fixed: 'right',
        align: 'center',
        render: (_value, record) => (
          <div className="flex items-center justify-center" onClick={(e) => e.stopPropagation()}>
            <Button
              size="small"
              type="text"
              danger
              onClick={() => handleRemoveFromPool(record.code, record.name)}
              title="从研究池移除"
            >
              <span className="text-[10px]">移除</span>
            </Button>
          </div>
        ),
      },
    ],
    []
  );

  const simpleTableScrollX = React.useMemo(() => sumColumnWidth(SIMPLE_TABLE_COLUMN_KEYS) + 80, []);

  /** 自选表格数据（特征富化 + 分页 + 关键词过滤） */
  const filteredWatchlist = React.useMemo(
    () => watchlistData.filter(
      (item) => !keyword || item.symbol.includes(keyword) || (item.stockName?.includes(keyword) ?? false)
    ),
    [watchlistData, keyword]
  );

  const watchlistRows = React.useMemo<ResearchStockRow[]>(
    () => filteredWatchlist
      .slice((watchlistPage - 1) * watchlistPageSize, watchlistPage * watchlistPageSize)
      .map((item, index) => ({
        ...(watchlistFeatures[item.symbol] || makeFallbackRow(item.key, item.symbol, item.stockName || '-', 0)),
        rank: (watchlistPage - 1) * watchlistPageSize + index + 1,
        key: item.key,
      })),
    [filteredWatchlist, watchlistFeatures, watchlistPage, watchlistPageSize]
  );

  const filteredPool = React.useMemo(
    () => poolData.filter(
      (item) => !keyword || item.symbol.includes(keyword) || (item.stockName?.includes(keyword) ?? false)
    ),
    [poolData, keyword]
  );

  const poolRows = React.useMemo<ResearchStockRow[]>(
    () => filteredPool
      .slice((poolPage - 1) * poolPageSize, poolPage * poolPageSize)
      .map((item, index) => ({
        ...(poolFeatures[item.symbol] || makeFallbackRow(item.key, item.symbol, item.stockName || '-', item.fusionScore ?? 0)),
        rank: (poolPage - 1) * poolPageSize + index + 1,
        key: item.key,
      })),
    [filteredPool, poolFeatures, poolPage, poolPageSize]
  );

  /* ------------------------------ 详情面板衍生数据 ------------------------------ */

  const radarMetrics = React.useMemo(() => {
    if (!selectedStock) return null;

    const clamp = (value: number, min: number, max: number): number => Math.max(min, Math.min(max, value));
    const modelScore = clamp(safeNum(selectedStock.score, 0) * 100, 0, 100);
    const pe = safeNum(selectedStock.pe, 0);
    const valuationScore = clamp(100 - pe, 0, 100);
    const roe = safeNum(selectedStock.roe, 0);
    const profitabilityScore = clamp(roe <= 0 ? 0 : (roe / 50) * 100, 0, 100);
    const momentumScore = clamp(safeNum(selectedStock.rsi, 0), 0, 100);
    const activityScore = clamp((safeNum(selectedStock.turnoverRate, 0) / 30) * 100, 0, 100);
    // 波动率越低得分越高（0.05 日波动率视为满档风险）
    const stabilityScore = clamp(100 - (safeNum(selectedStock.volStd20, 0) / 0.05) * 100, 0, 100);
    // 获利盘比例本身是 0~1，直接映射
    const chipScore = clamp(safeNum(selectedStock.chipProfitRatio20, 0) * 100, 0, 100);

    return {
      indicator: [
        { name: '模型评分', max: 100 },
        { name: '估值水平', max: 100 },
        { name: '盈利能力', max: 100 },
        { name: '动量强度', max: 100 },
        { name: '活跃度', max: 100 },
        { name: '稳定性', max: 100 },
        { name: '筹码获利', max: 100 },
      ],
      value: [modelScore, valuationScore, profitabilityScore, momentumScore, activityScore, stabilityScore, chipScore],
    };
  }, [selectedStock]);

  // 加载 K 线数据
  React.useEffect(() => {
    if (!detailModalOpen || !selectedStock) {
      setKlineData([]);
      return;
    }
    let cancelled = false;
    const loadKline = async () => {
      setKlineLoading(true);
      try {
        const data = await researchService.getKlineData(normalizeSymbol(selectedStock.code), 120);
        if (cancelled) return;
        setKlineData(data);
      } catch (error) {
        console.error('[ResearchPlatformPage] load kline failed:', error);
        if (!cancelled) setKlineData([]);
      } finally {
        if (!cancelled) setKlineLoading(false);
      }
    };
    void loadKline();
    return () => { cancelled = true; };
  }, [detailModalOpen, selectedStock?.code]);

  // 推理批次日期：优先取选中的数据日（pred.parquet 口径），
  // 兜底从 runId（形如 run_YYYYMMDD_xxx / pred_YYYYMMDD）解析，作为评分基准日
  // 这样风险评分跟选股决策对齐到同一日，避免"用今天的状态评估当时的决策"
  const inferenceDate = React.useMemo(() => {
    if (selectedDate) return selectedDate;
    const matched = selectedRunId.match(/(?:run|pred)_(\d{4})(\d{2})(\d{2})/);
    return matched ? `${matched[1]}-${matched[2]}-${matched[3]}` : null;
  }, [selectedDate, selectedRunId]);

  // ---- 推理批次日历派生数据（数据源 pred.parquet，见 /research/runs）----
  const runsByDate = React.useMemo(() => {
    const map = new Map<string, ResearchRunOption>();
    for (const item of availableRuns) {
      if (item.inferenceDate && !map.has(item.inferenceDate)) map.set(item.inferenceDate, item);
    }
    return map;
  }, [availableRuns]);

  const selectedRunEntry = React.useMemo(
    () => (selectedDate ? availableRuns.find((item) => item.inferenceDate === selectedDate) || null : null),
    [availableRuns, selectedDate]
  );

  const shiftCalendarMonth = (delta: number) => {
    const [y, mo] = calendarMonth.split('-').map(Number);
    const d = new Date(y, mo - 1 + delta, 1);
    setCalendarMonth(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`);
  };

  const calendarCells = React.useMemo(() => {
    const [y, m] = calendarMonth.split('-').map(Number);
    const first = new Date(y, m - 1, 1);
    const daysInMonth = new Date(y, m, 0).getDate();
    const startWeek = (first.getDay() + 6) % 7; // 周一为 0
    const cells: (string | null)[] = Array(startWeek).fill(null);
    for (let d = 1; d <= daysInMonth; d++) cells.push(`${calendarMonth}-${String(d).padStart(2, '0')}`);
    while (cells.length % 7 !== 0) cells.push(null);
    return cells;
  }, [calendarMonth]);

  // 选中批次变化时，日历跳到该批次所在月份
  React.useEffect(() => {
    if (selectedDate && /^\d{4}-\d{2}/.test(selectedDate)) setCalendarMonth(selectedDate.slice(0, 7));
  }, [selectedDate]);

  // 加载风险评分卡
  React.useEffect(() => {
    if (!detailModalOpen || !selectedStock) {
      setRiskScore(null);
      return;
    }
    let cancelled = false;
    const loadRisk = async () => {
      setRiskLoading(true);
      try {
        const data = await researchService.getRiskScore(normalizeSymbol(selectedStock.code), inferenceDate);
        if (cancelled) return;
        setRiskScore(data);
      } catch (error) {
        console.error('[ResearchPlatformPage] load risk score failed:', error);
        if (!cancelled) setRiskScore(null);
      } finally {
        if (!cancelled) setRiskLoading(false);
      }
    };
    void loadRisk();
    return () => { cancelled = true; };
  }, [detailModalOpen, selectedStock?.code, inferenceDate]);

  // K 线图表配置
  // 参考线过滤：根据模式决定每根 K 线是否命中条件（组件作用域，供 useMemo 与 JSX 复用）
  const refActive = refLineMode !== 'off' && typeof refLineValue === 'number' && Number.isFinite(refLineValue);
  const refMatches = React.useCallback((d: any): boolean => {
    if (!refActive) return false;
    const close = Number(d?.close);
    if (!Number.isFinite(close)) return false;
    if (refLineMode === 'above') return close >= refLineValue!;
    if (refLineMode === 'below') return close <= refLineValue!;
    if (refLineMode === 'range' && typeof refLineValue2 === 'number' && Number.isFinite(refLineValue2)) {
      const lo = Math.min(refLineValue!, refLineValue2);
      const hi = Math.max(refLineValue!, refLineValue2);
      return close >= lo && close <= hi;
    }
    return false;
  }, [refActive, refLineMode, refLineValue, refLineValue2]);

  const klineOption = React.useMemo(() => {
    if (!klineData.length) return null;

    // 提取预测日期基准线（选中数据日，pred.parquet 口径）
    const predictionDate = inferenceDate;

    const dates = klineData.map((d) => d.date);
    const ohlc = klineData.map((d) => [d.open, d.close, d.low, d.high]);
    const volumes = klineData.map((d) => d.volume);

    // 计算移动平均线
    const calculateMA = (dayCount: number) => {
      const result: Array<number | string> = [];
      for (let i = 0, len = klineData.length; i < len; i++) {
        if (i < dayCount - 1) {
          result.push('-');
          continue;
        }
        let sum = 0;
        for (let j = 0; j < dayCount; j++) {
          sum += klineData[i - j].close;
        }
        result.push(+(sum / dayCount).toFixed(2));
      }
      return result;
    };

    const ma5 = calculateMA(5);
    const ma10 = calculateMA(10);

    // 以预测日为中心，左右各 30 天的默认缩放窗口
    const zoomWindow = (() => {
      if (!predictionDate || dates.length <= 1) return { start: 0, end: 100 };
      const idx = dates.indexOf(predictionDate);
      if (idx === -1) return { start: 0, end: 100 };

      let startIdx = idx - 30;
      let endIdx = idx + 30;

      if (endIdx > dates.length - 1) {
        const overflow = endIdx - (dates.length - 1);
        endIdx = dates.length - 1;
        startIdx = Math.max(0, startIdx - overflow);
      }
      if (startIdx < 0) {
        startIdx = 0;
        endIdx = Math.min(dates.length - 1, startIdx + 60);
      }

      const totalPoints = dates.length - 1;
      if (totalPoints <= 0) return { start: 0, end: 100 };
      return { start: (startIdx / totalPoints) * 100, end: (endIdx / totalPoints) * 100 };
    })();

    // 参考线 markLine 数据（水平价格线）
    const refMarkLine = refActive
      ? {
          symbol: ['none', 'none'],
          silent: true,
          data: [
            {
              yAxis: refLineValue!,
              label: {
                show: true,
                position: 'end',
                formatter: `${refLineValue!.toFixed(2)}`,
                backgroundColor: '#f59e0b',
                color: '#fff',
                padding: [2, 4],
                borderRadius: 4,
                fontSize: 10,
                fontWeight: 'bold',
              },
              lineStyle: { color: '#f59e0b', type: 'dashed', width: 1.5, opacity: 0.8 },
            },
            ...(refLineMode === 'range' && typeof refLineValue2 === 'number' && Number.isFinite(refLineValue2)
              ? [{
                  yAxis: refLineValue2,
                  label: {
                    show: true,
                    position: 'end',
                    formatter: `${refLineValue2.toFixed(2)}`,
                    backgroundColor: '#f59e0b',
                    color: '#fff',
                    padding: [2, 4],
                    borderRadius: 4,
                    fontSize: 10,
                    fontWeight: 'bold',
                  },
                  lineStyle: { color: '#f59e0b', type: 'dashed', width: 1.5, opacity: 0.8 },
                }]
              : []),
          ],
        }
      : undefined;

    // 参考线 markArea 数据（区间高亮底色）
    const refMarkArea =
      refActive && refLineMode === 'range' && typeof refLineValue2 === 'number' && Number.isFinite(refLineValue2)
        ? {
            silent: true,
            data: [[
              { yAxis: Math.min(refLineValue!, refLineValue2), itemStyle: { color: 'rgba(245,158,11,0.08)' } },
              { yAxis: Math.max(refLineValue!, refLineValue2) },
            ]],
          }
        : undefined;

    return {
      animation: false,
      legend: {
        show: true,
        data: ['K线', 'MA5', 'MA10'],
        top: 0,
        textStyle: { color: '#64748b', fontSize: 10, fontWeight: 'bold' },
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        borderWidth: 1,
        borderColor: '#ccc',
        padding: 10,
        textStyle: { color: '#000', fontSize: 11 },
        formatter: (params: any[]) => {
          if (!params?.length) return '';
          const idx = params[0].dataIndex;
          const d = klineData[idx];
          if (!d) return '';
          return `
            <div style="font-size: 11px;">
              <div style="font-weight: bold; margin-bottom: 4px;">${d.date} ${d.date === predictionDate ? '<span style="color: #3b82f6;">[预测基准]</span>' : ''}</div>
              <div style="display: grid; grid-template-cols: 1fr 1fr; gap: 8px;">
                <div>开盘: ${d.open.toFixed(2)}</div>
                <div>收盘: ${d.close.toFixed(2)}</div>
                <div>最高: ${d.high.toFixed(2)}</div>
                <div>最低: ${d.low.toFixed(2)}</div>
              </div>
              <div style="margin-top: 4px; border-top: 1px solid #eee; pt: 4px;">
                <span style="color: #6366f1;">MA5: ${ma5[idx] === '-' ? '-' : ma5[idx]}</span>
                <span style="color: #f59e0b; margin-left: 8px;">MA10: ${ma10[idx] === '-' ? '-' : ma10[idx]}</span>
              </div>
              <div style="color: #64748b; margin-top: 2px;">成交量: ${(d.volume / 10000).toFixed(2)}万</div>
            </div>
          `;
        },
      },
      grid: [
        { left: '8%', right: '4%', top: '15%', height: '50%' },
        { left: '8%', right: '4%', top: '72%', height: '18%' },
      ],
      xAxis: [
        { type: 'category', data: dates, boundaryGap: true, axisLine: { onZero: false }, splitLine: { show: false }, min: 'dataMin', max: 'dataMax' },
        { type: 'category', gridIndex: 1, data: dates, boundaryGap: true, axisLine: { onZero: false }, axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false }, min: 'dataMin', max: 'dataMax' },
      ],
      yAxis: [
        { scale: true, splitArea: { show: true } },
        { scale: true, gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false } },
      ],
      dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: zoomWindow.start, end: zoomWindow.end }],
      series: [
        {
          name: 'K线',
          type: 'candlestick',
          data: ohlc,
          barMaxWidth: 20,
          itemStyle: {
            color: (params: any) => {
              const d = klineData[params.dataIndex];
              if (refMatches(d)) return '#f59e0b';
              return d?.close >= d?.open ? '#ef4444' : '#22c55e';
            },
            color0: (params: any) => {
              const d = klineData[params.dataIndex];
              if (refMatches(d)) return '#f59e0b';
              return d?.close >= d?.open ? '#ef4444' : '#22c55e';
            },
            borderColor: (params: any) => {
              const d = klineData[params.dataIndex];
              if (refMatches(d)) return '#f59e0b';
              return d?.close >= d?.open ? '#ef4444' : '#22c55e';
            },
            borderColor0: (params: any) => {
              const d = klineData[params.dataIndex];
              if (refMatches(d)) return '#f59e0b';
              return d?.close >= d?.open ? '#ef4444' : '#22c55e';
            },
          },
          markLine: {
            ...(predictionDate ? {
              symbol: ['none', 'none'],
              data: [{
                xAxis: predictionDate,
                label: {
                  show: true,
                  position: 'end',
                  formatter: '预测日期',
                  backgroundColor: '#3b82f6',
                  color: '#fff',
                  padding: [2, 4],
                  borderRadius: 4,
                  fontSize: 10,
                  fontWeight: 'bold',
                },
                lineStyle: { color: '#3b82f6', type: 'dashed', width: 2, opacity: 0.8 },
              }],
            } : {}),
            ...(refMarkLine ? { silent: refMarkLine.silent, symbol: refMarkLine.symbol, data: refMarkLine.data } : {}),
          },
          markArea: refMarkArea,
        },
        {
          name: 'MA5',
          type: 'line',
          data: ma5,
          smooth: true,
          showSymbol: false,
          lineStyle: { opacity: 0.8, width: 1, color: '#6366f1' },
          itemStyle: { color: '#6366f1' },
        },
        {
          name: 'MA10',
          type: 'line',
          data: ma10,
          smooth: true,
          showSymbol: false,
          lineStyle: { opacity: 0.8, width: 1, color: '#f59e0b' },
          itemStyle: { color: '#f59e0b' },
        },
        {
          name: '成交量',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volumes,
          barMaxWidth: 20,
          itemStyle: {
            color: (params: any) => {
              const d = klineData[params.dataIndex];
              return d?.close >= d?.open ? '#ef4444' : '#22c55e';
            },
          },
        },
      ],
    };
  }, [klineData, inferenceDate, refActive, refMatches, refLineValue, refLineValue2, refLineMode]);

  /* ------------------------------ 概览统计 ------------------------------ */

  const sectorBreakdown = React.useMemo(() => {
    const counter = new Map<string, number>();
    filteredRows.forEach((item) => {
      counter.set(item.sector, (counter.get(item.sector) || 0) + 1);
    });
    return Array.from(counter.entries())
      .map(([name, count]) => ({ name, count }))
      .sort((left, right) => right.count - left.count)
      .slice(0, 5);
  }, [filteredRows]);

  // 从候选池提取可用的行业选项
  const availableSectorOptions = React.useMemo(() => {
    const counter = new Map<string, number>();
    candidatePool.forEach((item) => {
      if (item.sector) counter.set(item.sector, (counter.get(item.sector) || 0) + 1);
    });
    return Array.from(counter.entries())
      .map(([name, count]) => ({ value: name, label: `${name} (${count})` }))
      .sort((left, right) => right.label.localeCompare(left.label));
  }, [candidatePool]);

  // 从候选池提取可用的概念选项
  const availableConceptOptions = React.useMemo(() => {
    if (overview?.filters?.concepts?.length) {
      return overview.filters.concepts.map((name: string) => ({ value: name, label: name }));
    }
    const counter = new Map<string, number>();
    candidatePool.forEach((item) => {
      (item.conceptTags || []).forEach((tag: string) => {
        counter.set(tag, (counter.get(tag) || 0) + 1);
      });
    });
    return Array.from(counter.entries())
      .map(([name, count]) => ({ value: name, label: `${name} (${count})` }))
      .sort((left, right) => right.label.localeCompare(left.label))
      .slice(0, 50); // 限制选项数量
  }, [candidatePool, overview]);

  const availableIndexOptions = React.useMemo(() => {
    // 优先从后端 summary 获取精准全局统计
    const summary = overview?.summary;
    const items = [
      { name: '全市场', count: summary?.totalMarket || 0 },
      { name: '沪深300', count: summary?.hs300 || 0 },
      { name: '中证1000', count: summary?.zz1000 || 0 },
      { name: '两融标的', count: summary?.margin || 0 },
      { name: '创业板指数', count: summary?.chinext || 0 },
    ];

    const counter = new Map<string, number>();
    candidatePool.forEach((item) => {
      (item.indexTags || []).forEach((tag: string) => {
        counter.set(tag, (counter.get(tag) || 0) + 1);
      });
    });

    return items
      .map((index) => {
        const displayCount = index.count > 0 ? index.count : (counter.get(index.name) || 0);
        return { value: index.name, label: `${index.name} (${displayCount})` };
      })
      .filter((option) => option.label.indexOf('(0)') === -1);
  }, [candidatePool, overview]);

  /** 当前生效的筛选条件摘要（用于头部展示） */
  const activeConditionSummary = React.useMemo(() => {
    const summary: string[] = [];
    if (appliedFilters.minScore > DEFAULT_RESEARCH_FILTERS.minScore) {
      summary.push(`模型分数 ≥ ${appliedFilters.minScore.toFixed(2)}`);
    }
    if (appliedFilters.limitUpDays > 0) summary.push(`连板天数 ≥ ${appliedFilters.limitUpDays}`);
    if (appliedFilters.excludeSt) summary.push('剔除 ST / 退市');
    if (appliedFilters.highConfidenceOnly) summary.push('仅保留高置信标的');
    if (appliedFilters.volumeTrendOnly) summary.push('近 5 日量能持续放大');
    if (appliedFilters.volRatio5Range > 0) summary.push(`5日量比 ≥ ${appliedFilters.volRatio5Range}`);
    if (appliedFilters.volRatio20Range > 0) summary.push(`20日量比 ≥ ${appliedFilters.volRatio20Range}`);
    if (appliedFilters.instOwnershipRange[0] > 0) summary.push(`机构持仓 ≥ ${appliedFilters.instOwnershipRange[0]}%`);
    if (appliedFilters.selectedSectors.length) summary.push(`行业：${appliedFilters.selectedSectors.length} 个选中`);
    if (appliedFilters.selectedConcepts.length) summary.push(`概念：${appliedFilters.selectedConcepts.length} 个选中`);
    if (appliedFilters.selectedIndices.length) summary.push(`指数：${appliedFilters.selectedIndices.length} 个选中`);

    // 所有被改动过的区间条件
    const fieldLabels = new Map<string, string>();
    FILTER_SECTIONS.forEach((section) => {
      section.fields.forEach((field) => fieldLabels.set(field.key as string, field.label));
    });
    activeRangeFilters.forEach((binding) => {
      const [min, max] = appliedFilters[binding.filterKey] as [number, number];
      const label = fieldLabels.get(binding.filterKey as string) ?? (binding.filterKey as string);
      summary.push(`${label} ${min} ~ ${max}`);
    });

    return summary;
  }, [appliedFilters, activeRangeFilters]);

  const avgScore = React.useMemo(() => {
    if (!filteredRows.length) return '0.000';
    const total = filteredRows.reduce((sum, item) => sum + Math.max(safeNum(item.score, 0), 0), 0);
    return (total / filteredRows.length).toFixed(3);
  }, [filteredRows]);

  const selectedStockRiskBlocks = React.useMemo(() => {
    if (!selectedStock) return [];
    const blocks = [...(selectedStock.riskFlags || [])];
    if (!selectedStock.volumeTrend5d) blocks.push('近 5 日量能未持续放大');
    if (safeNum(selectedStock.turnoverRate, 0) > 20) blocks.push('换手率偏高，追涨风险上升');
    if (safeNum(selectedStock.latestChange, 0) > 5) blocks.push('短线涨幅较大，注意日内波动');
    return Array.from(new Set(blocks));
  }, [selectedStock]);

  /* ------------------------------ 导出 ------------------------------ */

  /** 导出 CSV：跟随当前可见列，保证「所见即所导」 */
  const handleExportCSV = () => {
    if (filteredRows.length === 0) {
      message.warning('暂无数据可导出');
      return;
    }

    const exportKeys = visibleColumnKeys.filter((key) => key !== 'status');
    const headers: string[] = [];
    exportKeys.forEach((key) => {
      if (key === 'stock') {
        headers.push('股票代码', '股票名称');
        return;
      }
      headers.push(COLUMN_DEFS[key]?.title ?? key);
    });

    const serialize = (value: unknown): string => {
      if (value === null || value === undefined) return '-';
      const text = String(value);
      // 逗号/引号/换行需按 RFC4180 转义，否则会破坏列结构
      return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    };

    const rows = filteredRows.map((item) => {
      const cells: string[] = [];
      exportKeys.forEach((key) => {
        if (key === 'stock') {
          cells.push(serialize(item.code), serialize(item.name));
          return;
        }
        const def = COLUMN_DEFS[key];
        const field = (def?.dataIndex ?? key) as keyof ResearchStockRow;
        cells.push(serialize(item[field]));
      });
      return cells;
    });

    const csvContent = [headers.map(serialize).join(','), ...rows.map((row) => row.join(','))].join('\n');
    const BOM = '﻿';
    const blob = new Blob([BOM + csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    const timestamp = new Date().toISOString().slice(0, 10);
    link.download = `投研候选池_${selectedModelId}_${timestamp}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    message.success(`已导出 ${filteredRows.length} 条数据`);
  };

  /* ------------------------------ 渲染 ------------------------------ */

  const selectStyleFilter = (
    fieldKey: 'selectedSectors' | 'selectedConcepts' | 'selectedIndices',
    label: string,
    placeholder: string,
    options: Array<{ value: string; label: string }>
  ) => (
    <div className="space-y-2">
      <div className="text-[11px] font-bold text-slate-500">{label}</div>
      <Select
        mode="multiple"
        className={`w-full ${FIELD_STYLES.select}`}
        value={draftFilters[fieldKey]}
        onChange={(value: string[]) => setFilterField(fieldKey, value)}
        placeholder={placeholder}
        options={options}
        maxTagCount={2}
        maxTagPlaceholder={(omitted) => `+${omitted.length}`}
        showSearch
        filterOption={(input, option) => {
          const optionLabel = (option as any)?.label;
          return typeof optionLabel === 'string' && optionLabel.toLowerCase().includes(input.toLowerCase());
        }}
      />
    </div>
  );

  const filterCollapseItems = FILTER_SECTIONS.map((section) => ({
    key: section.key,
    label: (
      <span className="text-xs font-bold uppercase tracking-wide text-slate-700">
        {section.label}
        {section.fields.length > 0 && (
          <span className="ml-1.5 text-[10px] font-medium text-slate-400">({section.fields.length})</span>
        )}
      </span>
    ),
    children: (
      <div className="space-y-3 pt-1">
        {section.key === 'common' && (
          <>
            <div className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50/50 px-3 py-1.5">
              <span className="text-[11px] font-bold text-slate-500">剔除 ST / 退市</span>
              <Switch
                size="small"
                checked={draftFilters.excludeSt}
                onChange={(checked) => setFilterField('excludeSt', checked)}
              />
            </div>
            <div className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50/50 px-3 py-1.5">
              <span className="text-[11px] font-bold text-slate-500">仅高置信标的</span>
              <Switch
                size="small"
                checked={draftFilters.highConfidenceOnly}
                onChange={(checked) => setFilterField('highConfidenceOnly', checked)}
              />
            </div>
            <div className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50/50 px-3 py-1.5">
              <span className="text-[11px] font-bold text-slate-500">近 5 日量能放大</span>
              <Switch
                size="small"
                checked={draftFilters.volumeTrendOnly}
                onChange={(checked) => setFilterField('volumeTrendOnly', checked)}
              />
            </div>
          </>
        )}

        {section.fields.length > 2 ? (
          <div className="grid grid-cols-2 gap-x-3 gap-y-2">
            {section.fields.map((field) => (
              <RangeInput
                key={field.key as string}
                label={field.label}
                value={draftFilters[field.key] as [number, number] | number}
                onChange={(value) => setFilterField(field.key, value)}
                suffix={field.suffix}
                step={field.step ?? 1}
                quickTags={field.quickTagGroup ? QUICK_TAGS[field.quickTagGroup] : undefined}
                onQuickTagClick={(tag) => setFilterField(tag.filterKey, [...tag.range])}
              />
            ))}
          </div>
        ) : (
          section.fields.map((field) => (
            <RangeInput
              key={field.key as string}
              label={field.label}
              value={draftFilters[field.key] as [number, number] | number}
              onChange={(value) => setFilterField(field.key, value)}
              suffix={field.suffix}
              step={field.step ?? 1}
              quickTags={field.quickTagGroup ? QUICK_TAGS[field.quickTagGroup] : undefined}
              onQuickTagClick={(tag) => setFilterField(tag.filterKey, [...tag.range])}
            />
          ))
        )}

        {section.key === 'sector' && (
          <>
            <div className="space-y-2">
              <div className="text-[11px] font-bold text-slate-500">市场范围</div>
              <Select
                className={`w-full ${FIELD_STYLES.select}`}
                value={draftFilters.marketType}
                onChange={(value: string) => setFilterField('marketType', value)}
                options={[
                  { value: 'all', label: '全市场' },
                  { value: 'hs300', label: '沪深 300' },
                  { value: 'zz500', label: '中证 500' },
                  { value: 'zz1000', label: '中证 1000' },
                ]}
              />
            </div>
            {selectStyleFilter('selectedSectors', '行业筛选', '选择行业（可多选）', availableSectorOptions)}
            {selectStyleFilter('selectedConcepts', '概念筛选', '选择概念（可多选）', availableConceptOptions)}
            {selectStyleFilter('selectedIndices', '指数筛选', '选择指数（可多选）', availableIndexOptions)}
          </>
        )}
      </div>
    ),
  }));

  const activeTableTotal = activeDataSource === 'candidates'
    ? filteredRows.length
    : activeDataSource === 'watchlist'
      ? filteredWatchlist.length
      : filteredPool.length;

  return (
    <>
      <div className={`${PAGE_LAYOUT.outerClass} research-platform-page`}>
        <div className={`${PAGE_LAYOUT.frameClass} overflow-y-auto custom-scrollbar`}>
          <header className={`${PAGE_LAYOUT.headerClass}`} style={{ height: `${PAGE_LAYOUT.headerHeight}px` }}>
            <div className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-500 via-indigo-500 to-violet-400 text-white shadow-lg shadow-blue-900/20">
                <Microscope className="h-5 w-5" />
              </div>
              <div>
                <h1 className="text-lg font-bold tracking-tight text-slate-900">投研平台 ({marketConfig.label})</h1>
                <p className="text-[10px] font-bold uppercase tracking-[0.24em] text-slate-500">Professional Quant Workspace</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                icon={<RefreshCw className="h-4 w-4" />}
                className={BUTTON_STYLES.headerRefresh}
                loading={overviewLoading || syncing}
                onClick={handleSyncCandidates}
              >
                刷新数据
              </Button>
              <Button
                icon={<Download className="h-4 w-4" />}
                className={BUTTON_STYLES.headerSave}
                onClick={handleExportCSV}
                disabled={filteredRows.length === 0}
              >
                导出结果
              </Button>
            </div>
          </header>

          <div className="flex flex-1 flex-col">
            <div className={`${PAGE_LAYOUT.contentOuterClass}`}>
              <div className="grid gap-4 xl:grid-cols-[340px_minmax(0,1fr)] 2xl:grid-cols-[360px_minmax(0,1fr)]">
                {/* ---------------- 左侧筛选侧栏 ---------------- */}
                <div className="sticky top-4 z-30 flex h-[calc(var(--app-h)-120px)] flex-col gap-4">
                  <div className="flex-shrink-0 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                    <div className="mb-3 flex items-center gap-2 text-[9px] font-black uppercase tracking-[0.2em] text-slate-500">
                      <LibraryBig className="h-3.5 w-3.5" />
                      候选池入口
                    </div>

                    <div className="space-y-3">
                      <div>
                        <div className="mb-1 text-[10px] font-semibold text-slate-500">研究模型</div>
                        <Select
                          className={`w-full ${FIELD_STYLES.select} mb-0.5`}
                          size="small"
                          value={selectedModelId}
                          onChange={setSelectedModelId}
                          loading={modelsLoading}
                          placeholder="请选择投研模型"
                          options={availableModels.map((item) => ({ value: item.modelId, label: item.name }))}
                        />
                        {modelsError && <div className="mb-1 text-[9px] text-red-500">{modelsError}</div>}
                      </div>

                      <div>
                        <div className="mb-1 flex items-center justify-between">
                          <div className="text-[10px] font-semibold text-slate-500">推理批次</div>
                          <div className="font-mono text-[9px] text-slate-400">
                            pred.parquet
                            {availableRuns.length > 0 && (
                              <> · {availableRuns[availableRuns.length - 1]?.inferenceDate}~{availableRuns[0]?.inferenceDate}</>
                            )}
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => setCalendarOpen(!calendarOpen)}
                          disabled={runsLoading || availableRuns.length === 0}
                          className="mb-0.5 flex w-full items-center justify-between rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-bold text-slate-700 transition-all duration-200 hover:border-blue-300 hover:text-blue-600 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          <span className="flex items-center gap-1.5 truncate">
                            <CalendarDays className="h-3.5 w-3.5 flex-shrink-0 text-blue-500" />
                            {runsLoading
                              ? '加载批次中…'
                              : selectedDate
                                ? `${selectedDate} 批次`
                                : '选择推理日期'}
                          </span>
                          <ChevronRight
                            className={`h-3 w-3 flex-shrink-0 text-slate-400 transition-transform duration-200 ${calendarOpen ? 'rotate-90' : ''}`}
                          />
                        </button>
                        {calendarOpen && availableRuns.length > 0 && (
                          <div className="mb-1 rounded-xl border border-slate-100 bg-slate-50/60 p-2">
                            <div className="mb-1.5 flex items-center justify-between">
                              <button
                                type="button"
                                onClick={() => shiftCalendarMonth(-1)}
                                className="rounded-md p-0.5 text-slate-400 transition-colors hover:bg-slate-200 hover:text-slate-600"
                              >
                                <ChevronLeft className="h-3.5 w-3.5" />
                              </button>
                              <span className="text-[10px] font-black text-slate-600">
                                {calendarMonth.replace('-', ' 年 ')} 月
                              </span>
                              <button
                                type="button"
                                onClick={() => shiftCalendarMonth(1)}
                                className="rounded-md p-0.5 text-slate-400 transition-colors hover:bg-slate-200 hover:text-slate-600"
                              >
                                <ChevronRight className="h-3.5 w-3.5" />
                              </button>
                            </div>
                            <div className="mb-1 grid grid-cols-7 gap-0.5 text-center text-[9px] font-semibold text-slate-400">
                              {['一', '二', '三', '四', '五', '六', '日'].map((w) => (
                                <div key={w}>{w}</div>
                              ))}
                            </div>
                            <div className="grid grid-cols-7 gap-0.5">
                              {calendarCells.map((d, i) => {
                                if (!d) return <div key={`empty-${i}`} className="h-6" />;
                                const run = runsByDate.get(d);
                                const hasData = Boolean(run);
                                const isSelected = selectedDate === d;
                                return (
                                  <button
                                    key={d}
                                    type="button"
                                    disabled={!hasData}
                                    onClick={() => {
                                      if (!run) return;
                                      setSelectedDate(d);
                                      setSelectedRunId(run.runId || `pred_${d.replaceAll('-', '')}`);
                                      setCalendarOpen(false);
                                    }}
                                    title={hasData ? `${d} 推理数据（pred.parquet）` : d}
                                    className={`flex h-6 flex-col items-center justify-center rounded-md text-[10px] leading-none transition-all duration-150 ${
                                      isSelected
                                        ? 'bg-blue-600 font-black text-white'
                                        : hasData
                                          ? 'font-bold text-slate-700 hover:bg-blue-50 hover:text-blue-600'
                                          : 'cursor-default text-slate-300'
                                    }`}
                                  >
                                    {Number(d.slice(8, 10))}
                                    <span
                                      className={`mt-0.5 h-1 w-1 rounded-full ${
                                        isSelected
                                          ? 'bg-white'
                                          : hasData
                                            ? 'bg-emerald-500'
                                            : 'bg-transparent'
                                      }`}
                                    />
                                  </button>
                                );
                              })}
                            </div>
                            <div className="mt-1.5 flex items-center justify-center gap-3 text-[9px] text-slate-400">
                              <span className="flex items-center gap-1">
                                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> 有推理数据
                              </span>
                            </div>
                          </div>
                        )}
                        {!runsLoading && !runsError && availableRuns.length === 0 && (
                          <div className="mb-1 text-[9px] text-amber-600">
                            暂无推理数据（pred.parquet），请先在模型管理中生成推理或补全。
                          </div>
                        )}
                        {runsError && <div className="mb-1 text-[9px] text-red-500">{runsError}</div>}
                      </div>

                      <div>
                        <div className="mb-1 text-[10px] font-semibold text-slate-500">默认加载范围</div>
                        <div className="flex flex-wrap gap-1.5">
                          {[100, 200, 500, 1000].map((range) => (
                            <button
                              key={range}
                              type="button"
                              onClick={() => setLoadRange(range)}
                              className={`flex-1 rounded-lg border px-2 py-1 text-[10px] font-bold transition-all duration-200 ${
                                loadRange === range
                                  ? 'border-blue-600 bg-blue-600 text-white shadow-md shadow-blue-500/20'
                                  : 'border-slate-200 bg-white text-slate-500 hover:border-blue-300 hover:text-blue-500'
                              }`}
                            >
                              {range}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div>
                        <div className="mb-1 text-[10px] font-semibold text-slate-500">快速模板</div>
                        <div className="grid grid-cols-3 gap-1.5">
                          {Object.keys(PRESET_FILTER_MAP).map((item) => (
                            <Tag
                              key={item}
                              className={`preset-tag cursor-pointer rounded-full border px-2 py-0.5 text-center text-[9px] font-bold transition-all duration-300 ${
                                activePreset === item ? TEMPLATE_BUTTON_STYLES.active : TEMPLATE_BUTTON_STYLES.idle
                              }`}
                              onClick={() => applyPreset(item)}
                            >
                              {item}
                            </Tag>
                          ))}
                          <Tag
                            className={`preset-tag cursor-pointer rounded-full border px-2 py-0.5 text-center text-[9px] font-bold transition-all duration-300 ${
                              !activePreset ? 'border-blue-600 bg-blue-600 text-white' : 'border-slate-200 bg-slate-50 text-slate-500'
                            }`}
                            onClick={resetFilters}
                          >
                            全量候选
                          </Tag>
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                    <div className="custom-scrollbar flex-1 overflow-y-auto p-4">
                      <div className="mb-3 flex items-center gap-2 text-[9px] font-black uppercase tracking-[0.2em] text-slate-500">
                        <Filter className="h-3.5 w-3.5" />
                        量化研究条件
                      </div>

                      <Collapse
                        className={FIELD_STYLES.collapse}
                        ghost
                        activeKey={activeFilterSections}
                        onChange={(keys) =>
                          setActiveFilterSections(
                            (Array.isArray(keys) ? keys : [keys]) as FilterSectionKey[]
                          )
                        }
                        items={filterCollapseItems}
                      />
                    </div>

                    <div className="absolute bottom-0 left-0 right-0 z-40 rounded-b-3xl border-t border-slate-200/80 bg-white/95 px-4 py-3 shadow-[0_-10px_30px_-15px_rgba(0,0,0,0.1)] backdrop-blur-xl supports-[backdrop-filter]:bg-white/90">
                      <div className="mb-2 text-center text-[10px] font-medium text-slate-500">
                        {universeFeaturesLoading ? (
                          <span className="text-sky-600">⏳ 正在加载 QuantDB 因子（{candidatePool.length} 只）…</span>
                        ) : hasPendingFilterChanges ? (
                          <span className="text-amber-600">⚠ 筛选条件已变更，点击应用后生效</span>
                        ) : (
                          <span className="text-emerald-600">✓ 当前筛选条件已同步</span>
                        )}
                      </div>
                      <div className="flex gap-2">
                        <Button size="small" className="flex-1 rounded-xl border-slate-200 text-[11px] font-bold" onClick={resetFilters}>
                          恢复默认
                        </Button>
                        <Button
                          size="small"
                          type="primary"
                          className={`flex-1 rounded-xl text-[11px] font-black shadow-md transition-all ${
                            hasPendingFilterChanges
                              ? 'bg-blue-600 hover:-translate-y-0.5 hover:bg-blue-500'
                              : 'border-none bg-slate-300 text-slate-50 shadow-none'
                          }`}
                          disabled={!hasPendingFilterChanges}
                          onClick={applyCurrentFilters}
                        >
                          应用筛选
                        </Button>
                      </div>
                    </div>
                  </div>
                </div>

                {/* ---------------- 右侧主内容 ---------------- */}
                <motion.div
                  className="flex min-w-0 flex-1 flex-col gap-4 pb-4"
                  initial="hidden"
                  animate="visible"
                  variants={{
                    hidden: { opacity: 0 },
                    visible: { opacity: 1, transition: { staggerChildren: 0.1 } },
                  }}
                >
                  <motion.div
                    variants={{ hidden: { opacity: 0, y: 20 }, visible: { opacity: 1, y: 0 } }}
                    className="grid flex-shrink-0 gap-4 md:grid-cols-2 xl:grid-cols-4"
                  >
                    <ResearchMetricCard
                      icon={LibraryBig}
                      label="候选池"
                      value={overview?.summary?.total || 0}
                      subLabel="当前批次预测总量"
                      accentColor="#3b82f6"
                    />
                    <ResearchMetricCard
                      icon={Filter}
                      label="筛选结果"
                      value={filteredRows.length}
                      subLabel="符合当前条件的个股"
                      accentColor="#8b5cf6"
                    />
                    <ResearchMetricCard
                      icon={Flame}
                      label="高强度标的"
                      value={overview?.summary?.strongCount || 0}
                      subLabel="模型高分命中 (≥0.05)"
                      accentColor="#f43f5e"
                    />
                    <ResearchMetricCard
                      icon={BarChart3}
                      label="平均分数"
                      value={avgScore}
                      subLabel="筛选结果均值"
                      accentColor="#0ea5e9"
                    />
                  </motion.div>

                  <motion.div
                    variants={{ hidden: { opacity: 0, y: 20 }, visible: { opacity: 1, y: 0 } }}
                    className="glass-panel flex min-h-0 flex-1 flex-col overflow-hidden rounded-3xl p-1 shadow-sm"
                  >
                    <motion.div
                      initial={{ opacity: 0, y: 15 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="glass-panel mb-4 flex-shrink-0 rounded-2xl border border-white/60 bg-white/40 p-4 shadow-xl shadow-slate-200/50"
                    >
                      <div className="flex flex-col justify-between gap-3 border-b border-slate-100/60 pb-3 md:flex-row md:items-end">
                        <div className="space-y-1">
                          <div className="flex items-center gap-2 text-[9px] font-black uppercase tracking-widest text-slate-400">
                            <Sparkles className="h-3 w-3 text-blue-500" />
                            当前研究模型与批次
                          </div>
                          <div className="flex items-end gap-3">
                            <h2 className="text-2xl font-black leading-none tracking-tight text-slate-900">
                              {availableModels.find((item) => item.modelId === selectedModelId)?.name || '未选择模型'}
                            </h2>
                            {selectedDate && (
                              <div className="mb-0.5 flex items-center gap-1 rounded-lg bg-slate-900 px-2 py-0.5 text-[10px] font-black text-white shadow-lg shadow-slate-900/20">
                                <Activity className="h-2.5 w-2.5" />
                                {selectedDate}
                              </div>
                            )}
                          </div>
                        </div>

                        <div className="flex flex-wrap items-center gap-3">
                          <div className="flex flex-col gap-0.5 border-r border-slate-100 pr-4">
                            <span className="text-[9px] font-bold uppercase tracking-wider text-slate-400">执行周期</span>
                            <div className="flex items-center gap-2">
                              <div className="flex items-center gap-1 text-[11px] font-black text-slate-700">
                                <Target className="h-3 w-3 text-blue-500" />
                                {selectedDate || '-'}
                              </div>
                              <div className="h-1 w-1 rounded-full bg-slate-300" />
                              <div className="flex items-center gap-1 text-[11px] font-black text-slate-700">
                                <CandlestickChart className="h-3 w-3 text-emerald-500" />
                                {selectedRunEntry?.targetDate || '-'}
                              </div>
                            </div>
                          </div>

                          <div className="flex flex-col gap-0.5">
                            <span className="text-[9px] font-bold uppercase tracking-wider text-slate-400">同步状态</span>
                            <Tag
                              color={hasPendingFilterChanges ? 'warning' : 'success'}
                              icon={hasPendingFilterChanges ? <RefreshCw className="h-2.5 w-2.5 animate-spin-slow" /> : <Search className="h-2.5 w-2.5" />}
                              className="m-0 flex items-center gap-1 rounded-lg border-none px-2.5 py-0.5 text-[10px] font-black uppercase tracking-wide shadow-sm"
                            >
                              {hasPendingFilterChanges ? '待应用' : '已同步'}
                            </Tag>
                          </div>
                        </div>
                      </div>

                      <div className="grid grid-cols-1 items-start gap-4 pt-3 lg:grid-cols-12">
                        <div className="space-y-1.5 lg:col-span-7">
                          <div className="flex items-center gap-1.5 text-[9px] font-black uppercase tracking-widest text-slate-400">
                            <Filter className="h-2.5 w-2.5" />
                            当前生效筛选条件
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            {activeConditionSummary.length > 0 ? (
                              activeConditionSummary.map((condition) => (
                                <motion.span
                                  key={condition}
                                  whileHover={{ y: -1 }}
                                  className="flex items-center gap-1 rounded-lg border border-slate-200/50 bg-slate-100/80 px-2 py-0.5 text-[10px] font-bold text-slate-600 shadow-sm transition-colors hover:bg-white"
                                >
                                  <div className="h-1 w-1 rounded-full bg-blue-400" />
                                  {condition}
                                </motion.span>
                              ))
                            ) : (
                              <span className="text-[10px] font-bold italic text-slate-400">未应用特定条件筛选</span>
                            )}
                          </div>
                        </div>

                        <div className="space-y-1.5 lg:col-span-5">
                          <div className="flex items-center gap-1.5 text-[9px] font-black uppercase tracking-widest text-slate-400">
                            <BarChart3 className="h-2.5 w-2.5" />
                            核心板块分布
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            {sectorBreakdown.slice(0, 3).map((item, idx) => (
                              <motion.div
                                key={item.name}
                                whileHover={{ scale: 1.05 }}
                                className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white/80 px-2 py-0.5 text-[10px] font-bold shadow-sm"
                              >
                                <span className="text-slate-600">{item.name}</span>
                                <span className={`rounded px-1 py-0.5 text-[9px] ${idx === 0 ? 'bg-blue-500 text-white' : 'bg-slate-100 text-slate-500'}`}>
                                  {item.count}
                                </span>
                              </motion.div>
                            ))}
                            {sectorBreakdown.length > 3 && (
                              <div
                                className="flex cursor-help items-center px-1.5 text-[9px] font-black text-slate-400"
                                title={sectorBreakdown.slice(3).map((item) => `${item.name}(${item.count})`).join(', ')}
                              >
                                + {sectorBreakdown.length - 3} OTHERS
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    </motion.div>

                    {/* 工具栏：数据源 / 排序 / 搜索 / 列显示 */}
                    <div className="mb-4 mt-2 flex flex-shrink-0 flex-wrap items-center justify-between gap-4 border-b border-slate-100 pb-4">
                      <Segmented
                        value={activeDataSource}
                        onChange={(value) => setActiveDataSource(value as DataSourceTab)}
                        options={[
                          { label: <div className="flex items-center gap-2 px-2"><LibraryBig className="h-3.5 w-3.5" />候选池 ({filteredRows.length})</div>, value: 'candidates' },
                          { label: <div className="flex items-center gap-2 px-2"><Quote className="h-3.5 w-3.5" />自选 ({watchlistTotal})</div>, value: 'watchlist' },
                          { label: <div className="flex items-center gap-2 px-2"><Microscope className="h-3.5 w-3.5" />研究池 ({poolTotal})</div>, value: 'pool' },
                        ]}
                        className="research-next-segmented p-1.5"
                      />
                      <div className="flex flex-wrap items-center gap-3">
                        {activeDataSource === 'candidates' && (
                          <div className="flex items-center gap-1 rounded-[18px] border border-slate-200 bg-slate-50/50 p-1">
                            {SORT_OPTIONS.map((item) => (
                              <button
                                key={item.key}
                                type="button"
                                onClick={() => setSortKey(item.key)}
                                className={`min-w-[48px] whitespace-nowrap rounded-xl px-2 py-1.5 text-[10.5px] font-black transition-all ${
                                  sortKey === item.key
                                    ? 'scale-[1.02] bg-slate-800 text-white shadow-lg shadow-slate-400/20'
                                    : 'text-slate-500 hover:bg-white hover:text-slate-700'
                                }`}
                              >
                                {item.label}
                              </button>
                            ))}
                          </div>
                        )}
                        <Input
                          className="premium-search-bar h-10 max-w-[240px] rounded-[18px] border-slate-200 font-bold"
                          placeholder="搜索代码/名称..."
                          prefix={<Search className="h-4 w-4 text-slate-400" />}
                          value={keyword}
                          onChange={(event) => setKeyword(event.target.value)}
                          allowClear
                        />
                        {activeDataSource === 'candidates' && (
                          <ColumnVisibilityControl visibleGroups={visibleGroups} onChange={setVisibleGroups} />
                        )}
                        {activeDataSource === 'candidates' && (
                          <div className="flex items-center gap-0.5 rounded-[18px] border border-slate-200 bg-slate-50/50 p-0.5">
                            {(['compact', 'default', 'relaxed'] as const).map((density) => (
                              <button
                                key={density}
                                type="button"
                                onClick={() => setTableDensity(density)}
                                className={`whitespace-nowrap rounded-xl px-2 py-1 text-[10px] font-black transition-all ${
                                  tableDensity === density
                                    ? 'bg-slate-800 text-white shadow-sm'
                                    : 'text-slate-500 hover:bg-white hover:text-slate-700'
                                }`}
                              >
                                {density === 'compact' ? '紧凑' : density === 'default' ? '标准' : '宽松'}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="flex flex-1 flex-col">
                      <div className="flex-1">
                        {activeDataSource === 'candidates' && (
                          <Table<ResearchStockRow>
                            className={FIELD_STYLES.table}
                            rowKey="key"
                            columns={columns}
                            dataSource={enrichedCandidateRows}
                            loading={overviewLoading}
                            pagination={false}
                            scroll={{ x: candidateScrollX }}
                            size={tableDensity === 'compact' ? 'small' : tableDensity === 'relaxed' ? 'large' : 'middle'}
                            locale={{ emptyText: <Empty description="暂无符合条件的候选个股。" /> }}
                            onRow={(record) => ({
                              onClick: () => {
                                setSelectedStockKey(record.key);
                                setDetailModalOpen(true);
                              },
                            })}
                            rowClassName={(record) =>
                              `cursor-pointer transition-all ${record.key === selectedStockKey ? 'research-table-row-selected' : ''} ${
                                record.isMatched === false ? 'opacity-40 grayscale-[0.5]' : 'font-medium'
                              }`
                            }
                          />
                        )}
                        {activeDataSource === 'watchlist' && (
                          <Table<ResearchStockRow>
                            className={FIELD_STYLES.table}
                            rowKey="key"
                            columns={watchlistColumns}
                            dataSource={watchlistRows}
                            loading={watchlistLoading}
                            pagination={false}
                            scroll={{ x: simpleTableScrollX }}
                            locale={{ emptyText: <Empty description="自选列表为空。" /> }}
                          />
                        )}
                        {activeDataSource === 'pool' && (
                          <Table<ResearchStockRow>
                            className={FIELD_STYLES.table}
                            rowKey="key"
                            columns={poolColumns}
                            dataSource={poolRows}
                            loading={poolLoading}
                            pagination={false}
                            scroll={{ x: simpleTableScrollX }}
                            locale={{ emptyText: <Empty description="研究池为空。" /> }}
                          />
                        )}
                      </div>
                      <div className="flex items-center justify-end border-t border-slate-100 bg-white/80 px-2 py-2 backdrop-blur-sm">
                        <Pagination
                          current={
                            activeDataSource === 'candidates' ? candidatePage : activeDataSource === 'watchlist' ? watchlistPage : poolPage
                          }
                          pageSize={
                            activeDataSource === 'candidates' ? candidatePageSize : activeDataSource === 'watchlist' ? watchlistPageSize : poolPageSize
                          }
                          total={activeTableTotal}
                          onChange={(page, pageSize) => {
                            if (activeDataSource === 'candidates') {
                              setCandidatePage(page);
                              setCandidatePageSize(pageSize);
                            } else if (activeDataSource === 'watchlist') {
                              setWatchlistPage(page);
                              setWatchlistPageSize(pageSize);
                            } else {
                              setPoolPage(page);
                              setPoolPageSize(pageSize);
                            }
                          }}
                          size="small"
                          showSizeChanger
                          showTotal={(total) => `共 ${total} 条`}
                        />
                      </div>
                    </div>
                  </motion.div>
                </motion.div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ---------------- 个股详情弹窗 ---------------- */}
      <Modal
        centered
        width={1200}
        open={detailModalOpen}
        onCancel={() => setDetailModalOpen(false)}
        destroyOnHidden
        title={
          selectedStock ? (
            <div className="flex items-center justify-between pr-8">
              <div className="flex items-center gap-2">
                <span className="font-black tracking-tight text-slate-800">{selectedStock.name}</span>
                <span className="text-sm font-bold text-slate-400">({selectedStock.code})</span>
                {selectedStock.isSt && <Tag color="error" className="ml-2 scale-90">ST</Tag>}
              </div>
              <div className="flex items-center gap-2">
                <Button
                  size="small"
                  icon={<Quote className="h-3.5 w-3.5" />}
                  onClick={() => handleAddToWatchlist(selectedStock)}
                  className="h-8 rounded-xl border-slate-200 text-xs font-bold transition-all hover:border-blue-400 hover:text-blue-500 active:scale-95"
                >
                  加入自选
                </Button>
                <Button
                  size="small"
                  type="primary"
                  icon={<Sparkles className="h-3.5 w-3.5" />}
                  onClick={() => handleAddToResearchPool(selectedStock)}
                  className="h-8 rounded-xl border-none bg-blue-600 text-xs font-bold shadow-md shadow-blue-500/20 transition-all hover:bg-blue-500 active:scale-95"
                >
                  加入研究池
                </Button>
              </div>
            </div>
          ) : (
            '详情'
          )
        }
        footer={null}
      >
        {selectedStock ? (
          <div className="custom-scrollbar max-h-[75vh] space-y-3 overflow-y-auto py-2 pr-2">
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              <div className="grid grid-cols-3 gap-2">
                <div className="rounded-xl bg-slate-50 p-3 text-center">
                  <div className="text-[9px] font-bold text-slate-400">模型分数</div>
                  <div className="text-lg font-black text-blue-500">{safeNum(selectedStock.score, 0).toFixed(3)}</div>
                </div>
                <div className="rounded-xl bg-slate-50 p-3 text-center">
                  <div className="text-[9px] font-bold text-slate-400">PE (TTM)</div>
                  <div className="text-lg font-black text-slate-700">{fmtPositiveOrDash(selectedStock.pe, 1)}</div>
                </div>
                <div className="rounded-xl bg-slate-50 p-3 text-center">
                  <div className="text-[9px] font-bold text-slate-400">ROE</div>
                  <div className="text-lg font-black text-rose-500">
                    {Math.abs(safeNum(selectedStock.roe, 0)) <= 100
                      ? fmtPositiveOrDash(selectedStock.roe, 1, '%')
                      : '-'}
                  </div>
                </div>
                <div className="rounded-xl bg-slate-50 p-3 text-center">
                  <div className="text-[9px] font-bold text-slate-400">RSI</div>
                  <div className="text-lg font-black text-emerald-500">{fmtPositiveOrDash(selectedStock.rsi ?? selectedStock.rsi14, 1)}</div>
                </div>
                <div className="rounded-xl bg-slate-50 p-3 text-center">
                  <div className="text-[9px] font-bold text-slate-400">20日波动</div>
                  <div className="text-lg font-black text-amber-500">{fmtPositiveOrDash(selectedStock.volStd20, 2)}</div>
                </div>
                <div className="rounded-xl bg-slate-50 p-3 text-center">
                  <div className="text-[9px] font-bold text-slate-400">获利盘</div>
                  <div className="text-lg font-black text-indigo-500">{fmtNullableFloat(selectedStock.chipProfitRatio20, 3)}</div>
                </div>
              </div>
              <div className="rounded-2xl border border-slate-100 bg-slate-50/50 p-2">
                <ReactECharts
                  option={{
                    radar: {
                      indicator: radarMetrics?.indicator || [],
                      radius: '65%',
                      axisName: { color: '#94a3b8', fontSize: 10, fontWeight: 'bold' },
                    },
                    series: [
                      {
                        type: 'radar',
                        data: radarMetrics
                          ? [
                              {
                                value: radarMetrics.value,
                                name: '综合评分',
                                itemStyle: { color: '#3b82f6' },
                                areaStyle: { color: 'rgba(59, 130, 246, 0.2)' },
                              },
                            ]
                          : [],
                      },
                    ],
                  }}
                  style={{ height: '180px' }}
                />
              </div>
            </div>

            {selectedStockRiskBlocks.length > 0 && (
              <div className="rounded-2xl border border-amber-200 bg-amber-50/60 p-3">
                <div className="mb-1.5 text-[10px] font-black uppercase tracking-widest text-amber-600">风险提示</div>
                <div className="flex flex-wrap gap-1.5">
                  {selectedStockRiskBlocks.map((flag) => (
                    <span key={flag} className="rounded-lg border border-amber-200 bg-white px-2 py-0.5 text-[10px] font-bold text-amber-700">
                      {flag}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
              <div className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-500">
                <Activity className="h-3.5 w-3.5" /> 技术与资金面透视
              </div>
              <div className="grid grid-cols-5 gap-2">
                {[
                  { label: 'MA5', val: fmt2(selectedStock.ma5) },
                  { label: 'MA10', val: fmt2(selectedStock.ma10) },
                  { label: '资金净流入', val: fmtMainFlowCn(selectedStock.flowNetAmount) },
                  { label: '主力资金', val: fmtMainFlowCn(selectedStock.mainFlow) },
                  { label: '利润增长', val: fmtPercent2(selectedStock.profitGrowth) },
                ].map((item) => (
                  <div key={item.label} className="rounded-lg border border-slate-50 bg-slate-50/50 p-2 text-center">
                    <div className="text-[8px] font-bold text-slate-400">{item.label}</div>
                    <div className="mt-0.5 text-xs font-black text-slate-800">{item.val}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
              <div className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-500">
                <BarChart3 className="h-3.5 w-3.5" /> 量化研究指标
              </div>
              <div className="grid grid-cols-2 gap-2 md:grid-cols-4 lg:grid-cols-6">
                {[
                  { label: '模型分数', val: safeNum(selectedStock.score, 0).toFixed(3) },
                  { label: '连板天数', val: `${safeNum(selectedStock.consecutiveLimitUpDays, 0)} 天` },
                  { label: '成交额', val: `${safeNum(selectedStock.amount, 0).toFixed(2)} 亿` },
                  { label: '换手率', val: fmtPercent2(selectedStock.turnoverRate) },
                  { label: '涨跌幅', val: fmtSignedPercent2(selectedStock.latestChange) },
                  { label: '1日收益', val: fmtNullableSignedPercent2(selectedStock.return1d) },
                  { label: '3日收益', val: fmtNullableSignedPercent2(selectedStock.return3d) },
                  { label: '5日收益', val: fmtNullableSignedPercent2(selectedStock.return5d) },
                  { label: '10日收益', val: fmtNullableSignedPercent2(selectedStock.return10d) },
                  { label: '20日收益', val: fmtNullableSignedPercent2(selectedStock.return20d) },
                  { label: '60日收益', val: fmtNullableSignedPercent2(selectedStock.return60d) },
                  { label: '行业', val: selectedStock.sector || '-' },
                  { label: '概念', val: (selectedStock.conceptTags || []).slice(0, 3).join(' / ') || selectedStock.concept || '-' },
                  { label: '指数', val: (selectedStock.indexTags || []).slice(0, 3).join(' / ') || '-' },
                  { label: '总市值', val: `${safeNum(selectedStock.totalMv, 0).toFixed(2)} 亿` },
                  { label: '流通市值', val: `${safeNum(selectedStock.floatMv, 0).toFixed(2)} 亿` },
                  { label: '主力净流入', val: fmtMainFlowCn(selectedStock.mainFlow) },
                  { label: '获利盘 (20日)', val: fmt2(selectedStock.chipProfitRatio20) },
                ].map((item) => (
                  <div key={item.label} className="min-h-[56px] rounded-xl border border-slate-100 bg-slate-50/70 p-2 text-center">
                    <div className="text-[8px] font-black uppercase tracking-wide text-slate-400">{item.label}</div>
                    <div className="mt-1 break-words text-xs font-black leading-4 text-slate-800">{item.val}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              <div className="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
                <div className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-500">
                  <Activity className="h-3.5 w-3.5" /> 资金流结构
                </div>
                <div className="grid grid-cols-3 gap-1.5">
                  {[
                    { label: '净流入', val: fmtNullableYi(selectedStock.flowNetAmount) },
                    { label: '大单净额', val: fmtNullableYi(selectedStock.flowLargeNet) },
                    { label: '中单净额', val: fmtNullableYi(selectedStock.flowMediumNet) },
                    { label: '小单净额', val: fmtNullableYi(selectedStock.flowSmallNet) },
                    { label: '净流入占比', val: fmtNullableFloat(selectedStock.flowNetRatio, 3) },
                    { label: '大单占比', val: fmtNullableFloat(selectedStock.flowLargeRatio, 3) },
                    { label: '量能失衡', val: fmtNullableFloat(selectedStock.flowImbalanceVolume, 3) },
                    { label: '资金流指数', val: fmtNullableFloat(selectedStock.flowMoneyFlowIndex, 2) },
                    { label: '大单成交比', val: fmtNullableFloat(selectedStock.flowBigTradeRatio, 3) },
                  ].map((item) => (
                    <div key={item.label} className="rounded-lg border border-slate-100 bg-slate-50/70 p-2 text-center">
                      <div className="text-[8px] font-bold text-slate-400">{item.label}</div>
                      <div className="mt-0.5 truncate text-[11px] font-black text-slate-800">{item.val}</div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
                <div className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-500">
                  <BarChart3 className="h-3.5 w-3.5" /> 微观结构 / 筹码
                </div>
                <div className="grid grid-cols-3 gap-1.5">
                  {[
                    { label: 'VPIN(8)', val: fmtNullableFloat(selectedStock.microVpin8, 3) },
                    { label: 'VPIN(20)', val: fmtNullableFloat(selectedStock.microVpin20, 3) },
                    { label: 'Kyle λ', val: fmtNullableExponential(selectedStock.microKyleLambda) },
                    { label: '有效价差', val: fmtNullableFloat(selectedStock.microEspEqual, 4) },
                    { label: '非流动性', val: fmtNullableExponential(selectedStock.microAmihudIlliquidity) },
                    { label: '深度失衡', val: fmtNullableFloat(selectedStock.microDepthImbalance1, 3) },
                    { label: '获利盘60', val: fmtNullableFloat(selectedStock.chipProfitRatio60, 3) },
                    { label: '筹码集中', val: fmtNullableFloat(selectedStock.chipConcentration20, 3) },
                    { label: '峰值距离', val: fmtNullableFloat(selectedStock.chipPeakDistance, 3) },
                  ].map((item) => (
                    <div key={item.label} className="rounded-lg border border-slate-100 bg-slate-50/70 p-2 text-center">
                      <div className="text-[8px] font-bold text-slate-400">{item.label}</div>
                      <div className="mt-0.5 truncate text-[11px] font-black text-slate-800">{item.val}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
              <FactorPanel features={quantDbRaw[toSuffixSymbol(selectedStock.code)] || null} />
            </div>

            <RiskScoreCard data={riskScore} loading={riskLoading} requestedDate={inferenceDate} />

            <div className="mt-2 rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
              <div className="mb-2 flex items-center justify-between gap-2">
                <div className="text-[10px] font-black uppercase tracking-wider text-slate-500">K 线走势 (近 120 日)</div>
                {/* 参考线控制 */}
                <div className="flex items-center gap-2">
                  <Segmented
                    size="small"
                    value={refLineMode}
                    onChange={(v) => setRefLineMode(v as RefLineMode)}
                    options={[
                      { label: '关闭', value: 'off' },
                      { label: '高于', value: 'above' },
                      { label: '低于', value: 'below' },
                      { label: '区间', value: 'range' },
                    ]}
                    className="!text-[10px]"
                  />
                  {(refLineMode === 'above' || refLineMode === 'below') && (
                    <InputNumber
                      size="small"
                      placeholder="价格"
                      value={refLineValue}
                      onChange={(v) => setRefLineValue(typeof v === 'number' ? v : null)}
                      className="!w-20 !text-[10px]"
                      step={0.01}
                    />
                  )}
                  {refLineMode === 'range' && (
                    <>
                      <InputNumber
                        size="small"
                        placeholder="低值"
                        value={refLineValue}
                        onChange={(v) => setRefLineValue(typeof v === 'number' ? v : null)}
                        className="!w-20 !text-[10px]"
                        step={0.01}
                      />
                      <span className="text-[10px] text-slate-400">~</span>
                      <InputNumber
                        size="small"
                        placeholder="高值"
                        value={refLineValue2}
                        onChange={(v) => setRefLineValue2(typeof v === 'number' ? v : null)}
                        className="!w-20 !text-[10px]"
                        step={0.01}
                      />
                    </>
                  )}
                  {refLineMode !== 'off' && (
                    <Tag className="m-0 border-0 bg-amber-50 text-amber-600 text-[9px] font-bold">
                      命中 {refActive ? klineData.filter(refMatches).length : 0} 根
                    </Tag>
                  )}
                </div>
              </div>
              {klineLoading ? (
                <div className="flex h-[240px] items-center justify-center text-slate-400">加载中...</div>
              ) : klineOption ? (
                <ReactECharts
                  key={`${selectedStock.code}-${selectedRunId}`}
                  option={klineOption}
                  style={{ height: '240px' }}
                  notMerge
                  lazyUpdate
                />
              ) : (
                <div className="flex h-[240px] items-center justify-center text-xs text-slate-400">暂无 K 线数据</div>
              )}
            </div>
          </div>
        ) : (
          <Empty description="请选择一只股票查看详情。" />
        )}
      </Modal>
    </>
  );
};

export default ResearchPlatformPage;
