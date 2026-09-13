/** 因子研究（factor-lib-demo 改造版）类型定义 —— 与 /api/v1/factor-research 响应一一对应 */

export interface FactorMeta {
  code: string;
  name_cn: string;
  l1: string;
  l2: string;
  /** 1=正（越大越好），-1=负 */
  direction: number;
  description: string;
  formula: string;
  wind_source: string;
  env_tag: string;
  time_tag: string;
  available: boolean;
  unavailable_reason: string;
}

export interface CatalogResponse {
  factors: FactorMeta[];
  l1_order: string[];
  meta: Record<string, unknown>;
}

export interface SeriesPoint {
  date: string;
  value: number;
}

export interface FactorKpi {
  annual_return?: number | null;
  sharpe?: number | null;
  max_drawdown?: number | null;
  win_rate?: number | null;
  calmar?: number | null;
  n_months?: number;
  ic_mean?: number | null;
  ic_std?: number | null;
  ic_ir?: number | null;
  ic_win_rate?: number | null;
}

export interface LeaderboardRow extends FactorKpi {
  rank: number;
  code: string;
  name_cn: string;
  l1: string;
  l2: string;
  composite: number;
}

export interface LeaderboardResponse {
  leaderboard: LeaderboardRow[];
  meta: Record<string, unknown>;
}

export interface HoldingRow {
  symbol: string;
  name?: string;
  industry?: string;
  score?: number;
}

export interface FactorDetail {
  code: string;
  name_cn: string;
  l1: string;
  l2: string;
  direction: number;
  description: string;
  formula: string;
  wind_source: string;
  env_tag: string;
  time_tag: string;
  kpi: FactorKpi;
  nav: SeriesPoint[];
  ic: SeriesPoint[];
  benchmark: SeriesPoint[];
  holdings: HoldingRow[];
  holdings_date: string | null;
  available: boolean;
}

export interface CorrPair {
  factor_a: string;
  factor_b: string;
  corr: number;
}

export interface CompareFactor {
  code: string;
  name_cn: string;
  l1: string;
  l2: string;
  kpi: FactorKpi;
  nav: SeriesPoint[];
  ic: SeriesPoint[];
}

export interface CompareResponse {
  factors: CompareFactor[];
  corr: CorrPair[] | null;
  benchmark: SeriesPoint[];
}

export interface ComposeResponse {
  kpi: FactorKpi;
  nav: SeriesPoint[];
  turnover: SeriesPoint[];
  benchmark: SeriesPoint[];
  holdings: HoldingRow[];
  holdings_date: string | null;
  weights: Record<string, number>;
  top_n: number;
  threshold: number | null;
}

export interface ComposeRequest {
  weights: Record<string, number>;
  top_n: number;
  threshold?: number | null;
}

// ---------------------------------------------------------------------------
// 因子筛选（质量门槛 + 同源去重，screen_factors.py 产物）
// ---------------------------------------------------------------------------
export interface ScreeningRow {
  name: string;
  display_name: string;
  library: string;
  l1?: string;
  l2?: string;
  ic_mean: number | null;
  icir: number | null;
  turnover: number | null;
}

export interface ScreeningDropDup {
  name: string;
  library: string;
  duplicate_of: string;
  abs_corr: number;
}

export interface ScreeningGateDrop {
  name: string;
  library: string;
  reason: string;
}

export interface ScreeningResponse {
  generated_at: string;
  gates: { min_abs_ic: number; min_abs_icir: number; corr_threshold: number };
  cross_corr: string;
  counts: {
    candidates: number;
    kept: number;
    gated_out: number;
    deduped: number;
    total_considered: number;
  };
  kept: ScreeningRow[];
  dropped_gated: ScreeningGateDrop[];
  dropped_duplicate: ScreeningDropDup[];
  cluster_summary?: Record<string, unknown>;
}
