/** 因子报告（Alphalens 式）类型定义 —— 与 backend/services/engine/factor_report 对齐 */

export interface FactorSummary {
  name: string;
  /** alpha158 / alpha101 / gtja191 */
  library: string;
  ic_mean: number;
  icir: number;
  t_value: number;
  win_rate: number;
  /** 10 个分位的平均前瞻收益（升序：Q1 = 因子值最小） */
  quantiles: number[];
  /** 多空价差（Q10 − Q1） */
  ls_mean: number;
  /** 分位收益单调性（分位序号与收益的秩相关，±1 表示完美单调） */
  monotonicity: number | null;
  /** 单边换手率（0~1） */
  turnover: number;
}

export interface FactorReportMeta {
  generated_at: string;
  horizon: string;
  start: string;
  end: string;
  n_dates: number;
  n_factors: number;
  universe: string;
  step?: number;
  elapsed_sec?: number;
}

export interface FactorSummaryResponse {
  available: boolean;
  reason?: string;
  meta?: FactorReportMeta;
  total?: number;
  factors: FactorSummary[];
}

export interface FactorDetail {
  factor: string;
  horizon: string;
  empty: boolean;
  reason?: string;
  dates: string[];
  quantile_mean: number[];
  quantile_curves: number[][];
  ls_curve: number[];
  ic_series: (number | null)[];
  ic_rolling: (number | null)[];
  ic_mean: number | null;
  ic_std: number | null;
  turnover_dates: string[];
  turnover_series: number[];
  turnover_mean: number | null;
  coverage_mean: number | null;
  n_dates: number;
  start: string;
  end: string;
}

export interface FactorCorrelation {
  available: boolean;
  reason?: string;
  factors: string[];
  matrix: number[][];
}

export interface FactorRelated {
  factor: string;
  related: Array<{ name: string; corr: number }>;
}
