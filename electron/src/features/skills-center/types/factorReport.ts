/** 因子报告（Alphalens 式）类型定义 —— 与 backend/services/engine/factor_report 对齐 */

export interface FactorSummary {
  name: string;
  /** 因子库：alpha158 / alpha101 / gtja191 / L1 / L2 */
  library: string;
  /** 中文名（来自平台因子字典，可能为空） */
  display_name?: string | null;
  /** 中文分类（如「动量」「换手与流动性」「信息不对称与毒性」） */
  category_name?: string | null;
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

/** 报告页可选的数据集及其快照状态 */
export interface FactorDatasetInfo {
  dataset: string;
  label: string;
  available: boolean;
  horizon?: string | null;
  n_factors?: number | null;
  start?: string | null;
  end?: string | null;
  generated_at?: string | null;
}

export interface FactorDatasetList {
  default: string;
  items: FactorDatasetInfo[];
}

export interface FactorReportMeta {
  generated_at: string;
  dataset?: string;
  horizon: string;
  label_mode?: string;
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
  dataset?: string;
  meta?: FactorReportMeta;
  total?: number;
  factors: FactorSummary[];
}

export interface FactorDetail {
  dataset?: string;
  factor: string;
  horizon: string;
  empty: boolean;
  reason?: string;
  source?: 'series_snapshot' | 'partition_scan';
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
