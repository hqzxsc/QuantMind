/**
 * 后台管理 - 全局股票池服务（v2：单表元信息 + TXT 成员，保存即生效）
 *
 * 与后端 `/api/v1/admin/stock-pools/*` 一一对应。
 * 读侧（各功能下拉）走后端 `/api/v1/stock-pools/*`。
 */

import axios, { AxiosInstance } from 'axios';
import { authService } from '../../auth/services/authService';
import { SERVICE_ENDPOINTS, resolveWebSafeServiceBase } from '../../../config/services';

export interface StockPool {
    pool_id: string;
    code: string;
    name: string;
    description?: string | null;
    market: string;
    pool_type: 'system_index' | 'static' | 'imported';
    scope: 'global' | 'tenant' | 'user';
    tenant_id?: string | null;
    owner_user_id?: string | null;
    status: 'active' | 'archived';
    file_path?: string | null;
    symbol_count: number;
    checksum?: string | null;
    source_kind?: string | null;
    source_ref?: string | null;
    is_system: boolean;
    created_by?: string | null;
    updated_by?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
}

export interface PoolPreviewItem {
    symbol: string;
    api_symbol: string;
    name?: string | null;
    metrics?: Record<string, any>;
}

export interface PoolMembersResult {
    pool_id: string;
    code: string;
    market: string;
    file_path?: string | null;
    total: number;
    checksum?: string | null;
    symbols: string[];
    updated_at?: string | null;
}

export interface PoolMeta {
    markets: string[];
    pool_types: string[];
    scopes: string[];
    statuses: string[];
    target_types: string[];
    binding_modes: string[];
    member_max: number;
    pool_txt_dir: string;
    builtin_pools: Array<{
        code: string;
        name: string;
        market: string;
        index_symbol?: string | null;
        optional_source: boolean;
        description: string;
    }>;
}

export interface PoolResolveResult {
    ref: string;
    pool_id: string;
    code: string;
    market: string;
    source: string;
    unfiltered: boolean;
    symbol_count: number;
    checksum?: string | null;
    warnings: string[];
    sample: string[];
}

export interface SaveMembersResult {
    success: boolean;
    accepted: number;
    rejected: number;
    duplicates: number;
    rejected_samples: string[];
    symbol_count: number;
    checksum?: string | null;
    file_path?: string | null;
}

/** 上传解析明细行 */
export interface ParseRow {
    row_index: number;
    raw: string;
    token: string;
    status: 'matched' | 'not_in_index' | 'unrecognized';
    match_type:
        | 'code'
        | 'symbol'
        | 'prefix'
        | 'exchange_fixed'
        | 'name'
        | 'name_loose'
        | 'none';
    symbol?: string | null;
    api_symbol?: string | null;
    code?: string | null;
    name?: string | null;
    duplicate: boolean;
    reason?: string | null;
}

export interface ParseReport {
    summary: {
        total: number;
        matched: number;
        unmatched: number;
        duplicates: number;
        not_in_index: number;
        unique_symbols: number;
    };
    rows: ParseRow[];
    symbols: string[];
    members: Array<{ symbol: string; api_symbol?: string; name?: string; meta?: Record<string, any> }>;
    warnings: string[];
    encoding?: string | null;
    index_source?: string | null;
    index_size: number;
    truncated: boolean;
}

export interface CreateFromMembersResult {
    success: boolean;
    pool: StockPool;
    accepted: number;
    rejected: number;
    duplicates: number;
    rejected_samples: string[];
    checksum?: string | null;
    file_path?: string | null;
}

class StockPoolService {
    private axiosInstance: AxiosInstance;
    private readonly baseURL = resolveWebSafeServiceBase(
        (import.meta as any).env?.VITE_USER_API_URL,
        SERVICE_ENDPOINTS.USER_SERVICE,
    );

    constructor() {
        this.axiosInstance = axios.create({
            baseURL: this.baseURL,
            timeout: 60000,
            headers: { 'Content-Type': 'application/json' },
        });

        this.axiosInstance.interceptors.request.use((config) => {
            const token = authService.getAccessToken();
            if (token && config.headers) {
                (config.headers as any).Authorization = `Bearer ${token}`;
            }
            let tenantId = 'default';
            try {
                const raw = localStorage.getItem('user');
                if (raw) {
                    const u = JSON.parse(raw);
                    if (u?.tenant_id) tenantId = String(u.tenant_id).trim();
                }
            } catch (e) {
                /* ignore */
            }
            if (config.headers) {
                (config.headers as any)['X-Tenant-Id'] = tenantId;
            }
            return config;
        });

        this.axiosInstance.interceptors.response.use(
            (response) => response,
            async (error) => authService.handle401Error(error, this.axiosInstance),
        );
    }

    private unwrap<T>(resp: any): T {
        const d = resp?.data;
        if (d && d.success && d.data) return d.data as T;
        return d as T;
    }

    async getMeta(): Promise<PoolMeta> {
        const resp = await this.axiosInstance.get('/admin/stock-pools/meta');
        return this.unwrap(resp);
    }

    async listPools(params: {
        market?: string;
        pool_type?: string;
        status?: string;
        keyword?: string;
        limit?: number;
        offset?: number;
    }): Promise<{ total: number; items: StockPool[] }> {
        const resp = await this.axiosInstance.get('/admin/stock-pools', { params });
        return this.unwrap(resp);
    }

    async getPool(poolId: string): Promise<StockPool & { binding_count: number }> {
        const resp = await this.axiosInstance.get(`/admin/stock-pools/${encodeURIComponent(poolId)}`);
        return this.unwrap(resp);
    }

    async createPool(payload: Partial<StockPool>): Promise<StockPool> {
        const resp = await this.axiosInstance.post('/admin/stock-pools', payload);
        return this.unwrap(resp);
    }

    async updatePool(poolId: string, payload: Record<string, any>): Promise<StockPool> {
        const resp = await this.axiosInstance.patch(
            `/admin/stock-pools/${encodeURIComponent(poolId)}`,
            payload,
        );
        return this.unwrap(resp);
    }

    async archivePool(poolId: string): Promise<{ success: boolean }> {
        const resp = await this.axiosInstance.post(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/archive`,
        );
        return this.unwrap(resp);
    }

    async deletePool(poolId: string): Promise<{ success: boolean }> {
        const resp = await this.axiosInstance.delete(
            `/admin/stock-pools/${encodeURIComponent(poolId)}`,
        );
        return this.unwrap(resp);
    }

    /** 成员就是 TXT 内容：读出来即可用（保存即生效，无发布环节） */
    async getMembers(poolId: string): Promise<PoolMembersResult> {
        const resp = await this.axiosInstance.get(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/members`,
        );
        return this.unwrap(resp);
    }

    /** 整体覆盖成员（symbols 或粘贴文本二选一），保存后立即生效 */
    async saveMembers(
        poolId: string,
        payload: { symbols?: string[]; text?: string },
    ): Promise<SaveMembersResult & { pool: StockPool }> {
        const resp = await this.axiosInstance.put(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/members`,
            payload,
        );
        return this.unwrap(resp);
    }

    /** 向已有池导入 csv/txt 文本（覆盖成员） */
    async importMembers(
        poolId: string,
        payload: { content: string; fmt: 'csv' | 'txt' },
    ): Promise<SaveMembersResult> {
        const resp = await this.axiosInstance.post(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/members/import`,
            payload,
        );
        return this.unwrap(resp);
    }

    membersExportUrl(poolId: string): string {
        const prefix = this.baseURL || '';
        return `${prefix}/admin/stock-pools/${encodeURIComponent(poolId)}/export`;
    }

    /** 内置池：从 QuantDB 重新拉成分并覆盖 TXT */
    async refreshPool(poolId: string): Promise<SaveMembersResult & { pool: StockPool }> {
        const resp = await this.axiosInstance.post(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/refresh`,
            null,
            { timeout: 120000 },
        );
        return this.unwrap(resp);
    }

    /** 预览成分（含最新行情指标） */
    async preview(
        poolId: string,
        limit = 200,
    ): Promise<{ total: number; items: PoolPreviewItem[]; metrics_available: boolean }> {
        const resp = await this.axiosInstance.get(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/preview`,
            { params: { limit } },
        );
        return this.unwrap(resp);
    }

    async usages(poolId: string): Promise<{ total: number; items: any[] }> {
        const resp = await this.axiosInstance.get(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/usages`,
        );
        return this.unwrap(resp);
    }

    /** 登记一条池引用（长生命周期：策略/模型/账户），被引用后不可删除 */
    async bindPool(
        poolId: string,
        payload: { target_type: string; target_id: string; mode?: string; priority?: number },
    ): Promise<{ success: boolean }> {
        const resp = await this.axiosInstance.post(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/bindings`,
            payload,
        );
        return this.unwrap(resp);
    }

    /** 解除一条引用 */
    async unbindPool(
        poolId: string,
        targetType: string,
        targetId: string,
    ): Promise<{ success: boolean; removed: number }> {
        const resp = await this.axiosInstance.delete(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/bindings/${encodeURIComponent(
                targetType,
            )}/${encodeURIComponent(targetId)}`,
        );
        return this.unwrap(resp);
    }

    /** 从既有模型 metadata 回填引用（让守卫对存量数据生效） */
    async reconcileBindings(dryRun = true): Promise<{
        dry_run: boolean;
        scanned: number;
        bound: number;
        unresolved: number;
        unresolved_samples: string[];
    }> {
        const resp = await this.axiosInstance.post('/admin/stock-pools/bindings/reconcile', null, {
            params: { dry_run: dryRun },
            timeout: 120000,
        });
        return this.unwrap(resp);
    }

    async health(): Promise<any> {
        const resp = await this.axiosInstance.get('/admin/stock-pools/health');
        return this.unwrap(resp);
    }

    async resolve(ref: string, opts?: { market?: string }): Promise<PoolResolveResult> {
        const resp = await this.axiosInstance.get('/admin/stock-pools/resolve', {
            params: { ref, ...opts },
        });
        return this.unwrap(resp);
    }

    /**
     * 股票解析：上传 CSV/TXT，与 data/stocks/stocks_index.json 对比，返回匹配报告（不落库）。
     * contentBase64 保留原始字节，可正确处理 Excel 导出的 GBK 文件。
     */
    async parsePoolFile(payload: {
        content_base64?: string;
        content_text?: string;
        filename?: string;
        fmt?: 'csv' | 'txt';
        has_header?: boolean;
        column?: string;
        row_limit?: number;
    }): Promise<ParseReport> {
        const resp = await this.axiosInstance.post('/admin/stock-pools/parse', payload, {
            timeout: 120000,
        });
        return this.unwrap(resp);
    }

    /** 用解析确认后的成员建池（保存即可用，无发布步骤） */
    async createPoolFromMembers(payload: {
        code: string;
        name: string;
        description?: string;
        market?: string;
        pool_type?: string;
        symbols: string[];
    }): Promise<CreateFromMembersResult> {
        const resp = await this.axiosInstance.post(
            '/admin/stock-pools/create-from-members',
            payload,
        );
        return this.unwrap(resp);
    }
}

export const stockPoolService = new StockPoolService();
export default stockPoolService;
