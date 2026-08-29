import React, { useCallback, useEffect, useState } from 'react';
import {
  Database, Key, ShieldCheck, RefreshCw, HardDrive, Activity, Eye, EyeOff, Save, Zap, ExternalLink
} from 'lucide-react';
import { Button, Input, Tag, message, Alert } from 'antd';
import { dataPlatformService, QuantDBConfig, QuantDBInfo } from '../../admin/services/dataPlatformService';

export const QuantDBSettings: React.FC = () => {
  const [config, setConfig] = useState<QuantDBConfig | null>(null);
  const [info, setInfo] = useState<QuantDBInfo | null>(null);
  const [apiKey, setApiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    try {
      setRefreshing(true);
      const [cfg, sdkInfo] = await Promise.allSettled([
        dataPlatformService.getQuantDBConfig(),
        dataPlatformService.getQuantDBInfo(),
      ]);

      if (cfg.status === 'fulfilled') {
        setConfig(cfg.value);
      }
      if (sdkInfo.status === 'fulfilled') {
        setInfo(sdkInfo.value?.quantdb || (sdkInfo.value as any));
      }
    } catch (e: any) {
      console.error('加载 QuantDB 状态失败:', e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSave = async () => {
    const trimmed = apiKey.trim();
    if (trimmed.length < 8) {
      message.warning('请输入完整的 API Key（至少 8 位）');
      return;
    }

    setSaving(true);
    setVerifyError(null);
    try {
      const result = await dataPlatformService.saveQuantDBConfig(trimmed);
      if (result.verified) {
        message.success('QuantDB API Key 已成功保存并验证通过！');
      } else {
        setVerifyError(result.error ?? '未知原因');
        message.warning('API Key 已保存，但连接测试未通过，请检查 Key 有效性');
      }
      setApiKey('');
      await loadData();
    } catch (error: any) {
      message.error(`保存失败: ${error?.message || '未知错误'}`);
    } finally {
      setSaving(false);
    }
  };

  const openQuantDBWebsite = () => {
    window.open('https://www.quantdb.cn/index.html', '_blank');
  };

  const isConfigured = Boolean(config?.api_key_configured);
  const isInstalled = Boolean(info?.installed);

  // 流量数值取整（只显示整数 GB）
  const fmtGB = (v?: number) => (v != null ? `${Math.floor(v)}` : null);
  const renderUsedTraffic = () => {
    const intGb = fmtGB(info?.usage?.used_gb);
    if (intGb != null) return `${intGb} GB`;
    return info?.used_bytes_human || (info?.used_traffic != null ? `${Math.floor(Number(info.used_traffic))}` : '0 GB');
  };
  const renderRemainingTraffic = () => {
    const intGb = fmtGB(info?.usage?.remaining_gb);
    if (intGb != null) return `${intGb} GB`;
    return info?.remaining_bytes_human || (info?.remaining_traffic != null ? `${Math.floor(Number(info.remaining_traffic))}` : '—');
  };

  return (
    <div className="w-full space-y-3">
      {/* 顶部标题卡片 (精简单行，全宽铺开) */}
      <div className="bg-white rounded-2xl border border-slate-200/80 px-5 py-3.5 shadow-xs flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center text-white shadow-sm">
            <Database className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-black text-slate-800 m-0">数据平台 (QuantDB)</h3>
              <Tag color={isConfigured ? 'green' : 'default'} className="rounded-md text-[10px] font-bold m-0 border-0 px-1.5 py-0">
                {isConfigured ? '已授权' : '未授权'}
              </Tag>
            </div>
            <p className="text-[11px] text-slate-400 m-0 leading-tight">
              A股、行业、财务与机器学习因子等数据仓库的访问凭据
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="small"
            icon={<ExternalLink className="w-3 h-3 text-blue-600" />}
            onClick={openQuantDBWebsite}
            className="rounded-lg font-bold text-xs h-7 px-3 bg-gradient-to-r from-blue-50 to-indigo-50 border-blue-200 text-blue-600 hover:text-blue-700 hover:border-blue-300"
          >
            注册 / 官网
          </Button>
          <Button
            size="small"
            icon={<RefreshCw className={`w-3 h-3 ${refreshing ? 'animate-spin' : ''}`} />}
            onClick={loadData}
            loading={refreshing}
            className="rounded-lg font-bold text-xs h-7 px-3"
          >
            刷新
          </Button>
        </div>
      </div>

      {/* 状态指标条 (4列紧凑卡片) */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
        {/* 1. SDK 状态 */}
        <div className="bg-white rounded-xl border border-slate-200/80 p-3 shadow-2xs text-center">
          <div className="flex items-center justify-center text-slate-400 text-[11px] font-semibold gap-1.5">
            <span>SDK 状态</span>
            <HardDrive className="w-3.5 h-3.5 text-blue-500" />
          </div>
          <div className="mt-1 flex items-center justify-center gap-1.5">
            <span className={`w-1.5 h-1.5 rounded-full ${isInstalled ? 'bg-emerald-500 animate-pulse' : 'bg-slate-300'}`} />
            <span className="text-sm font-black text-slate-800">
              {isInstalled ? '已就绪' : '未就绪'}
            </span>
          </div>
          <span className="text-[10px] text-slate-400 font-mono block truncate">
            {info?.version ? `v${info.version}` : 'Python SDK 运行时'}
          </span>
        </div>

        {/* 2. API Key 状态 */}
        <div className="bg-white rounded-xl border border-slate-200/80 p-3 shadow-2xs text-center">
          <div className="flex items-center justify-center text-slate-400 text-[11px] font-semibold gap-1.5">
            <span>API Key 状态</span>
            <Key className="w-3.5 h-3.5 text-indigo-500" />
          </div>
          <div className="mt-1 flex items-center justify-center gap-1.5">
            <span className={`w-1.5 h-1.5 rounded-full ${isConfigured ? 'bg-emerald-500' : 'bg-rose-400'}`} />
            <span className="text-sm font-black text-slate-800">
              {isConfigured ? '已配置' : '未配置'}
            </span>
          </div>
          <span className="text-[10px] text-slate-400 font-mono block truncate">
            {config?.api_key_masked || '未绑定 API Key'}
          </span>
        </div>

        {/* 3. 已用流量 */}
        <div className="bg-white rounded-xl border border-slate-200/80 p-3 shadow-2xs text-center">
          <div className="flex items-center justify-center text-slate-400 text-[11px] font-semibold gap-1.5">
            <span>已用流量</span>
            <Activity className="w-3.5 h-3.5 text-amber-500" />
          </div>
          <div className="mt-1">
            <span className="text-sm font-black font-mono text-slate-800">
              {renderUsedTraffic()}
            </span>
          </div>
          <span className="text-[10px] text-slate-400 block truncate">
            {info?.traffic_reset_date ? `重置: ${info.traffic_reset_date}` : '本计费周期统计'}
          </span>
        </div>

        {/* 4. 剩余配额 */}
        <div className="bg-white rounded-xl border border-slate-200/80 p-3 shadow-2xs text-center">
          <div className="flex items-center justify-center text-slate-400 text-[11px] font-semibold gap-1.5">
            <span>剩余配额</span>
            <Zap className="w-3.5 h-3.5 text-emerald-500" />
          </div>
          <div className="mt-1">
            <span className="text-sm font-black font-mono text-emerald-600">
              {renderRemainingTraffic()}
            </span>
          </div>
          <span className="text-[10px] text-slate-400 block truncate">
            {info?.tier_name ? `套餐: ${info.tier_name}` : '高速同步配额'}
          </span>
        </div>
      </div>

      {/* API Key 输入与管理卡片 (紧凑单体) */}
      <div className="bg-white rounded-2xl border border-slate-200/80 p-4 shadow-xs space-y-3">
        <div className="flex items-center justify-between border-b border-slate-100 pb-2">
          <div>
            <h4 className="text-xs font-bold text-slate-800 m-0">API Key 授权与即时验证</h4>
            <p className="text-[11px] text-slate-400 m-0">
              输入 QuantDB 访问密钥，系统将自动进行热重载与远端连通性测试
            </p>
          </div>
          <div className="flex items-center gap-2">
            {!isConfigured && (
              <Button
                type="link"
                size="small"
                icon={<ExternalLink className="w-3 h-3" />}
                onClick={openQuantDBWebsite}
                className="text-[11px] text-blue-600 hover:text-blue-700 p-0 font-medium h-auto"
              >
                没有账号？前往 QuantDB 注册获取 Key →
              </Button>
            )}
            {isConfigured && (
              <span className="text-[10px] font-mono font-bold text-slate-500 bg-slate-100 px-2 py-0.5 rounded">
                指纹: {config?.api_key_masked}
              </span>
            )}
          </div>
        </div>

        <div className="space-y-2.5">
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Input
                type={showKey ? 'text' : 'password'}
                placeholder={isConfigured ? `已配置 ${config?.api_key_masked} (输入新 Key 进行覆盖)` : '粘贴 QuantDB API Key (如 qk_...)'}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                onPressEnter={handleSave}
                className="rounded-xl h-9 font-mono text-xs pr-10"
                autoComplete="off"
              />
              <button
                type="button"
                onClick={() => setShowKey(!showKey)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 transition-colors p-1"
                title={showKey ? '隐藏明文' : '显示明文'}
              >
                {showKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              </button>
            </div>
            <Button
              type="primary"
              icon={<Save className="w-3.5 h-3.5" />}
              onClick={handleSave}
              loading={saving}
              className="rounded-xl h-9 px-4 font-bold bg-blue-600 shadow-sm text-xs shrink-0"
            >
              保存并验证
            </Button>
          </div>

          {verifyError && (
            <Alert
              type="warning"
              showIcon
              message="Key 已写入，但 QuantDB 握手验证未通过"
              description={verifyError}
              closable
              onClose={() => setVerifyError(null)}
              className="rounded-xl text-xs py-1"
            />
          )}

          {/* 紧凑安全说明 (单行) */}
          <div className="flex items-center gap-1.5 text-[11px] text-slate-400 bg-slate-50 px-3 py-1.5 rounded-lg border border-slate-100">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
            <span className="truncate">
              安全说明：Key 加密落盘至本地 <code className="text-slate-600 font-mono">{config?.runtime_env_file || 'config/runtime.env'}</code>，页面仅显示脱敏指纹，环境变量 <code className="text-slate-600 font-mono">QUANTDB_API_KEY</code> 优先。
            </span>
          </div>
        </div>
      </div>

      {/* 运行时路径与存储信息 (单行胶囊) */}
      {config && (
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="px-3 py-2 bg-white rounded-xl border border-slate-200/80 shadow-2xs flex items-center justify-between">
            <span className="text-[11px] text-slate-400 font-medium shrink-0">数据仓库目录</span>
            <span className="font-mono text-slate-700 font-bold text-[11px] truncate ml-2">{config.data_dir || '/data/quantdb'}</span>
          </div>
          <div className="px-3 py-2 bg-white rounded-xl border border-slate-200/80 shadow-2xs flex items-center justify-between">
            <span className="text-[11px] text-slate-400 font-medium shrink-0">密钥配置文件</span>
            <span className="font-mono text-slate-700 font-bold text-[11px] truncate ml-2">{config.runtime_env_file || 'config/runtime.env'}</span>
          </div>
        </div>
      )}
    </div>
  );
};

export default QuantDBSettings;
