import { apiClient } from './api-client';

export interface SystemCapabilities {
  edition: 'oss' | 'enterprise';
  features: {
    sms: boolean;
    cos: boolean;
    multi_strategy: boolean;
    advanced_factors: boolean;
    rbac_enhanced: boolean;
    audit_logs: boolean;
    local_storage: boolean;
    k8s_deployment: boolean;
  };
}

export interface SystemUpdateInfo {
  /** 本部署落后上游的提交数 */
  behind: number;
  /** 上游 commits 超过单次 API 上限，behind 为下限（前端可显示 N+） */
  behind_capped: boolean;
  upstream_branch: string;
  checked_at: number;
  is_up_to_date: boolean;
}

export interface SystemVersion {
  version: string;
  edition: 'oss' | 'enterprise';
  /** deploy/update.sh 写入的完整 HEAD SHA */
  commit?: string;
  branch?: string;
  /** 上游更新检查结果；未走 update.sh 或无外网时为空 */
  update?: SystemUpdateInfo | null;
}

export const systemService = {
  /**
   * 获取系统能力与版本信息
   */
  getCapabilities: async (): Promise<SystemCapabilities> => {
    return apiClient.get<SystemCapabilities>('/api/v1/system/capabilities');
  },

  /**
   * 获取当前运行代码版本，并附带上游更新检查（deploy/update.sh 更新后写入 version.json）
   * @param force true 时绕过磁盘缓存，强制实时请求上游平台
   */
  getVersion: async (force = false): Promise<SystemVersion> => {
    return apiClient.get<SystemVersion>('/api/v1/system/version', { params: { force } });
  }
};
