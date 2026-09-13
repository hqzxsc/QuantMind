/** 因子研究 API 服务层（/api/v1/factor-research，引擎服务经网关转发） */

import { SERVICE_ENDPOINTS } from '../../../config/services';
import type {
  CatalogResponse,
  CompareResponse,
  ComposeRequest,
  ComposeResponse,
  FactorDetail,
  LeaderboardResponse,
} from '../types/factorResearch';

const BASE = `${SERVICE_ENDPOINTS.USER_SERVICE}/factor-research`;

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('access_token') || '';
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function requestJson<T>(path: string, init: RequestInit = {}, timeoutMs = 60000): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...authHeaders(), ...(init.headers || {}) },
      signal: controller.signal,
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => '');
      throw new Error(`因子研究接口失败 ${res.status}: ${detail.slice(0, 160)}`);
    }
    return (await res.json()) as T;
  } finally {
    window.clearTimeout(timer);
  }
}

/** 因子目录（82 个 + 快照元信息） */
export function getCatalog(): Promise<CatalogResponse> {
  return requestJson<CatalogResponse>('/catalog');
}

/** 排行榜 */
export function getLeaderboard(sort = 'composite'): Promise<LeaderboardResponse> {
  const qs = sort && sort !== 'composite' ? `?sort=${encodeURIComponent(sort)}` : '';
  return requestJson<LeaderboardResponse>(`/leaderboard${qs}`);
}

/** 单因子详情 */
export function getFactorDetail(code: string): Promise<FactorDetail> {
  return requestJson<FactorDetail>(`/factor/${encodeURIComponent(code)}`, {}, 90000);
}

/** 多因子对比 */
export function getCompare(codes: string[]): Promise<CompareResponse> {
  return requestJson<CompareResponse>(`/compare?codes=${encodeURIComponent(codes.join(','))}`, {}, 90000);
}

/** 多因子合成回测 */
export function postCompose(req: ComposeRequest): Promise<ComposeResponse> {
  return requestJson<ComposeResponse>('/compose', { method: 'POST', body: JSON.stringify(req) }, 120000);
}

/** 因子筛选结果（质量门槛 + 同源去重） */
export function getScreening(): Promise<import('../types/factorResearch').ScreeningResponse> {
  return requestJson('/screening');
}
