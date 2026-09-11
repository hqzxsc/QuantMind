/**
 * 后台管理 - 全局股票池服务
 *
 * 与后端 `/api/v1/admin/stock-pools/*` 一一对应。
 * 读侧（各功能下拉）走后端 `/api/v1/stock-pools/*`，见 `stockPoolQueryService`。
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
    pool_type: 'system_index' | 'static' | 'imported' | 'dynamic' | 'eligibility';
    scope: 'global' | 'tenant' | 'user';
    tenant_id?: string | null;
    owner_user_id?: string | null;
    status: 'draft' | 'published' | 'archived';
    visibility: string;
    definition: Record<string, any>;
    refresh_policy: Record<string, any>;
    current_version: number;
    symbol_count: number;
    checksum?: string | null;
    source_kind?: string | null;
    source_ref?: string | null;
    is_system: boolean;
    created_by?: string | null;
    updated_by?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
    has_draft_changes?: boolean;
}

export interface PoolMember {
    symbol: string;
    api_symbol?: string | null;
    name?: string | null;
    weight?: number | null;
    industry?: string | null;
    effective_from?: string | null;
    effective_to?: string | null;
    meta?: Record<string, any>;
    metrics?: Record<string, any>;
}

export interface PoolVersion {
    pool_id: string;
    version: number;
    status: string;
    market?: string | null;
    member_count: number;
    checksum?: string | null;
    storage_mode: 'table' | 'snapshot';
    snapshot_path?: string | null;
    changelog?: string | null;
    published_by?: string | null;
    published_at?: string | null;
}

export interface PoolMeta {
    markets: string[];
    pool_types: string[];
    scopes: string[];
    statuses: string[];
    target_types: string[];
    binding_modes: string[];
    member_table_max: number;
    storage_modes: string[];
    snapshot_dir: string;
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
    version?: number | null;
    market: string;
    source: string;
    unfiltered: boolean;
    symbol_count: number;
    checksum?: string | null;
    storage_mode?: string;
    warnings: string[];
    sample: string[];
}

export interface ImportResult {
    total: number;
    accepted: number;
    rejected: number;
    duplicates: number;
    rejected_samples: string[];
    version?: number | null;
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
    pool_id: string;
    code: string;
    accepted: number;
    rejected: number;
    rejected_samples: string[];
    in_index: number;
    version?: number | null;
    published: boolean;
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

    async getPool(poolId: string): Promise<StockPool & { versions: PoolVersion[]; staging_version: number }> {
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

    async listMembers(
        poolId: string,
        params: { scope?: 'draft' | 'published'; limit?: number; offset?: number },
    ): Promise<{ total: number; items: PoolMember[]; version: number; scope: string }> {
        const resp = await this.axiosInstance.get(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/members`,
            { params },
        );
        return this.unwrap(resp);
    }

    async replaceMembers(
        poolId: string,
        members: Array<{ symbol: string; name?: string; weight?: number }>,
        changelog?: string,
    ): Promise<{ success: boolean; accepted: number; rejected: number; staging_version: number }> {
        const resp = await this.axiosInstance.put(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/members`,
            { members, changelog },
        );
        return this.unwrap(resp);
    }

    async importMembers(
        poolId: string,
        payload: { content: string; fmt: 'csv' | 'txt'; has_header?: boolean },
        publish = true,
    ): Promise<ImportResult> {
        const resp = await this.axiosInstance.post(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/members/import`,
            payload,
            { params: { publish } },
        );
        return this.unwrap(resp);
    }

    async importNewPool(
        payload: { content: string; fmt: 'csv' | 'txt'; has_header?: boolean },
        params: { pool_code: string; pool_name: string; market?: string; description?: string; publish?: boolean },
    ): Promise<ImportResult> {
        const resp = await this.axiosInstance.post('/admin/stock-pools/import', payload, { params });
        return this.unwrap(resp);
    }

    membersExportUrl(poolId: string, scope: 'draft' | 'published' = 'published'): string {
        const prefix = this.baseURL || '';
        return `${prefix}/admin/stock-pools/${encodeURIComponent(poolId)}/members/export?scope=${scope}`;
    }

    async publish(poolId: string, changelog?: string): Promise<PoolVersion> {
        const resp = await this.axiosInstance.post(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/publish`,
            null,
            { params: changelog ? { changelog } : undefined },
        );
        return this.unwrap(resp);
    }

    async listVersions(poolId: string, limit = 50): Promise<{ total: number; items: PoolVersion[] }> {
        const resp = await this.axiosInstance.get(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/versions`,
            { params: { limit } },
        );
        return this.unwrap(resp);
    }

    async rollback(poolId: string, version: number, changelog?: string): Promise<any> {
        const resp = await this.axiosInstance.post(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/rollback`,
            null,
            { params: { version, ...(changelog ? { changelog } : {}) } },
        );
        return this.unwrap(resp);
    }

    async diffVersions(poolId: string, from: number, to: number): Promise<any> {
        const resp = await this.axiosInstance.get(
            `/admin/stock-pools/${encodeURIComponent(poolId)}/diff`,
            { params: { from, to } },
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
    ): Promise<{ success: boolean; total: number; items: any[] }> {
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
    ): Promise<{ success: boolean; removed: number; total: number }> {
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

    async resolve(ref: string, opts?: { market?: string; version?: number }): Promise<PoolResolveResult> {
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

    /** 用解析确认后的成员建池（可选立即发布） */
    async createPoolFromMembers(payload: {
        code: string;
        name: string;
        description?: string;
        market?: string;
        pool_type?: string;
        members: Array<{ symbol: string; name?: string; weight?: number }>;
        publish?: boolean;
        changelog?: string;
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
