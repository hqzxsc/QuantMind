import React, { useEffect, useState } from 'react';
import {
  Check,
  Copy,
  Eye,
  EyeOff,
  Key,
  RefreshCw,
  Settings,
  ShieldCheck,
  Cable,
  Wifi,
  Server,
  Send,
  SlidersHorizontal,
} from 'lucide-react';
import { SERVICE_URLS } from '../../../config/services';

// 与后端 ApiKeyInfo 对齐：/api-keys/init 是幂等接口，永不返回 secret_key
interface ApiKeyInfo {
  id: number;
  access_key: string;
  name: string;
  permissions: string[];
  is_active: boolean;
  created_at: string;
  expires_at?: string | null;
  last_used_at?: string | null;
}

interface RotateSecretInfo {
  access_key: string;
  secret_key: string;
}

interface TdxConfig {
  enabled: boolean;
  bridge_url: string;
  bridge_token_configured: boolean;
  real_trading_enabled: boolean;
  broker_type: string;
  health?: { status?: string; tdx_connected?: boolean; error?: string } | null;
}

interface TdxOverview {
  available: boolean;
  error?: string;
  bridge?: {
    hostname?: string;
    local_ips?: string[];
    bridge_url?: string;
    port?: number;
    tdx_connected?: boolean;
    server_time?: string;
    token_configured?: boolean;
    shared_dir?: string;
  };
  account?: {
    currency?: string;
    balance?: number;
    cash?: number;
    asset?: number;
    market_value?: number;
    position_count?: number;
  };
  positions?: Array<Record<string, unknown>>;
  orders?: Array<Record<string, unknown>>;
  cache?: {
    stock_info?: number;
    kline?: number;
    sector_stocks?: number;
    market_snapshot?: number;
    tdx_log?: number;
    financial?: number;
    trade_log?: number;
    mem_hit_rate?: number;
    mem_entries?: number;
  };
  security?: {
    banned_ips?: number;
    active_ips?: number;
    write_active?: number;
  };
}

interface SettingsCenterProps {
  userId: string;
  isActive: boolean;
}

const SettingsCenter: React.FC<SettingsCenterProps> = ({ userId, isActive }) => {
  const apiGatewayBase = SERVICE_URLS.API_GATEWAY.replace(/\/+$/, '');
  const authHeader = () => ({
    'Content-Type': 'application/json',
    Authorization: `Bearer ${localStorage.getItem('access_token') || ''}`,
  });

  const [copied, setCopied] = useState('');
  const [keyInfo, setKeyInfo] = useState<ApiKeyInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [showAccessKey, setShowAccessKey] = useState(false);
  const [showSecretKey, setShowSecretKey] = useState(false);
  const [secretKey, setSecretKey] = useState<string | null>(null);

  // 通达信桥配置
  const [tdxConfig, setTdxConfig] = useState<TdxConfig | null>(null);
  const [tdxLoading, setTdxLoading] = useState(false);
  const [tdxNewToken, setTdxNewToken] = useState('');
  const [tdxNewUrl, setTdxNewUrl] = useState('');
  const [tdxMsg, setTdxMsg] = useState('');
  const [tdxError, setTdxError] = useState('');

  const extractApiError = async (res: Response): Promise<string> => {
    try {
      const data = await res.json();
      return data.detail || data.message || data.error || '';
    } catch {
      return '';
    }
  };

  // 通达信桥局域网信息（聚合统计）
  const [tdxOverview, setTdxOverview] = useState<TdxOverview | null>(null);
  const [tdxOverviewLoading, setTdxOverviewLoading] = useState(false);
  const [pushLoading, setPushLoading] = useState(false);
  const [pushResult, setPushResult] = useState<{
    ok: boolean;
    pushed?: number;
    skipped?: number;
    runId?: string;
    stocks?: Array<{ rank: number; symbol: string; name: string; score: number }>;
    error?: string;
  } | null>(null);
  const [rollingLoading, setRollingLoading] = useState(false);
  const [rollingResult, setRollingResult] = useState<{
    ok: boolean;
    buys?: Array<{ symbol: string; name: string; score: number; volume: number; close: number }>;
    sells?: Array<{ symbol: string; name: string; score: number | null; reason: string }>;
    market?: { above_ma20: boolean; detail: string };
    placedOrders?: Array<{ symbol: string; side: string; volume: number; status: string; message: string }>;
    failedOrders?: Array<{ symbol: string; side: string; error: string }>;
    error?: string;
  } | null>(null);
  const [rollingThreshold, setRollingThreshold] = useState('2.2');
  const [rollingAmount, setRollingAmount] = useState('10000');
  const [rollingExecuteMode, setRollingExecuteMode] = useState<'off' | 'tdx' | 'paper'>('off');
  const [rollingDate, setRollingDate] = useState('');
  const [rollingCfgMsg, setRollingCfgMsg] = useState('');
  const [activeTab, setActiveTab] = useState<'credentials' | 'tdx'>('credentials');

  // 持仓股止损止盈提醒配置（/tdx/sltp-config，仅提醒不下单）
  const [sltpStopLoss, setSltpStopLoss] = useState('8');
  const [sltpTakeProfit, setSltpTakeProfit] = useState('');
  const [sltpTrailing, setSltpTrailing] = useState('');
  const [sltpEnabled, setSltpEnabled] = useState(true);
  const [sltpMsg, setSltpMsg] = useState('');

  const handleCopy = async (text: string, key: string) => {
    await navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(''), 2000);
  };

  const maskValue = (value: string) => value.replace(/(.{8}).*(.{4})$/, '$1••••••••••••$2');

  const fetchBootstrap = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${apiGatewayBase}/api/v1/api-keys/init`, {
        method: 'POST',
        headers: authHeader(),
      });
      if (!res.ok) {
        throw new Error('init failed');
      }
      const data: ApiKeyInfo = await res.json();
      setKeyInfo({
        ...data,
        access_key: String(data.access_key || '').trim(),
      });
    } catch (e) {
      console.error('Failed to init api key', e);
    } finally {
      setLoading(false);
    }
  };

  const rotateSecret = async () => {
    if (!keyInfo?.access_key) return;
    setLoading(true);
    try {
      const res = await fetch(
        `${apiGatewayBase}/api/v1/api-keys/${keyInfo.access_key}/rotate-secret`,
        {
          method: 'POST',
          headers: authHeader(),
        }
      );
      if (!res.ok) {
        throw new Error('rotate secret failed');
      }
      const data: RotateSecretInfo = await res.json();
      setSecretKey(String(data.secret_key || '').trim());
      setShowSecretKey(true);
    } catch (e) {
      console.error('Failed to rotate secret key', e);
    } finally {
      setLoading(false);
    }
  };

  const fetchTdxConfig = async () => {
    setTdxLoading(true);
    setTdxError('');
    try {
      const res = await fetch(`${apiGatewayBase}/api/v1/tdx/config`, {
        headers: authHeader(),
      });
      if (res.ok) {
        const data: TdxConfig = await res.json();
        setTdxConfig(data);
        setTdxNewToken('');
        setTdxNewUrl(data.bridge_url || '');
      } else {
        const detail = await extractApiError(res);
        setTdxError(detail || `HTTP ${res.status}`);
      }
    } catch (e) {
      console.error('Failed to fetch tdx config', e);
      setTdxError(String(e));
    } finally {
      setTdxLoading(false);
    }
  };

  const updateTdxConfig = async () => {
    setTdxLoading(true);
    setTdxMsg('');
    try {
      const payload: Record<string, string> = {};
      if (tdxNewToken.trim()) payload.bridge_token = tdxNewToken.trim();
      if (tdxNewUrl.trim()) payload.bridge_url = tdxNewUrl.trim();
      const res = await fetch(`${apiGatewayBase}/api/v1/tdx/config`, {
        method: 'POST',
        headers: authHeader(),
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error('update failed');
      setTdxMsg('✅ 通达信桥配置已更新');
      await fetchTdxConfig();
    } catch (e) {
      setTdxMsg(`❌ 更新失败: ${e}`);
    } finally {
      setTdxLoading(false);
    }
  };

  const fetchTdxOverview = async () => {
    setTdxOverviewLoading(true);
    try {
      const res = await fetch(`${apiGatewayBase}/api/v1/tdx/overview`, {
        headers: authHeader(),
      });
      if (res.ok) {
        setTdxOverview(await res.json());
      }
    } catch (e) {
      console.error('Failed to fetch tdx overview', e);
    } finally {
      setTdxOverviewLoading(false);
    }
  };

  const pushSignalsToTdx = async () => {
    setPushLoading(true);
    setPushResult(null);
    try {
      const res = await fetch(`${apiGatewayBase}/api/v1/tdx/push-signals`, {
        method: 'POST',
        headers: { ...authHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ top_n: 20 }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setPushResult({
          ok: true,
          pushed: data.pushed,
          skipped: data.skipped?.length,
          runId: data.run_id,
          stocks: data.stocks,
        });
      } else {
        setPushResult({ ok: false, error: data.error || data.detail || `HTTP ${res.status}` });
      }
    } catch (e) {
      setPushResult({ ok: false, error: String(e) });
    } finally {
      setPushLoading(false);
    }
  };

  const fetchRollingConfig = async () => {
    try {
      const res = await fetch(`${apiGatewayBase}/api/v1/tdx/rolling-config`, {
        headers: authHeader(),
      });
      if (res.ok) {
        const data = await res.json();
        setRollingThreshold(String(data.score_threshold ?? '2.2'));
        setRollingAmount(String(data.fixed_buy_amount ?? '10000'));
        setRollingExecuteMode(
          data.execute_mode === 'tdx' || data.execute_mode === 'paper'
            ? data.execute_mode
            : data.auto_place
              ? 'tdx'
              : 'off',
        );
      }
    } catch (e) {
      console.error('Failed to fetch rolling config', e);
    }
  };

  const fetchSltpConfig = async () => {
    try {
      const res = await fetch(`${apiGatewayBase}/api/v1/tdx/sltp-config`, {
        headers: authHeader(),
      });
      if (res.status === 403) {
        // 会员门控：实时提醒仅 QuantDB 付费会员在期可用
        setSltpMsg('⛔ 实时行情提醒为 QuantDB 付费会员专属功能，请保持会员在期');
        setSltpEnabled(false);
        return;
      }
      if (res.ok) {
        const data = await res.json();
        setSltpStopLoss(String((data.stop_loss_pct ?? 0.08) * 100));
        setSltpTakeProfit(data.take_profit_pct != null ? String(data.take_profit_pct * 100) : '');
        setSltpTrailing(data.trailing_stop_pct != null ? String(data.trailing_stop_pct * 100) : '');
        setSltpEnabled(Boolean(data.enabled));
        setSltpMsg('');
      }
    } catch (e) {
      console.error('Failed to fetch sltp config', e);
    }
  };

  const saveSltpConfig = async () => {
    setSltpMsg('');
    const parsePct = (v: string): number | null => {
      const n = parseFloat(v);
      return Number.isFinite(n) && n > 0 ? n : null;
    };
    const stopLoss = parsePct(sltpStopLoss);
    const takeProfit = parsePct(sltpTakeProfit);
    const trailing = parsePct(sltpTrailing);
    if (!stopLoss && !takeProfit && !trailing) {
      setSltpMsg('❌ 至少填写止损、止盈、移动止损中的一项（%）');
      return;
    }
    try {
      const res = await fetch(`${apiGatewayBase}/api/v1/tdx/sltp-config`, {
        method: 'PUT',
        headers: { ...authHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          stop_loss_pct: stopLoss != null ? stopLoss / 100 : 0,
          take_profit_pct: takeProfit != null ? takeProfit / 100 : null,
          trailing_stop_pct: trailing != null ? trailing / 100 : null,
          enabled: sltpEnabled,
        }),
      });
      if (res.ok) {
        setSltpMsg('✅ 已保存，盘中现价触发即提醒（站内通知 + 通达信预警）');
        await fetchSltpConfig();
      } else {
        const detail = await extractApiError(res);
        setSltpMsg(`❌ 保存失败: ${detail || `HTTP ${res.status}`}`);
      }
    } catch (e) {
      setSltpMsg(`❌ 保存失败: ${e}`);
    }
  };

  const saveRollingConfig = async () => {
    setRollingCfgMsg('');
    const threshold = parseFloat(rollingThreshold);
    const amount = parseFloat(rollingAmount);
    if (!Number.isFinite(threshold) || threshold <= 0 || threshold > 10) {
      setRollingCfgMsg('❌ 阈值需在 0-10 之间');
      return;
    }
    if (!Number.isFinite(amount) || amount <= 0) {
      setRollingCfgMsg('❌ 每只金额需大于 0');
      return;
    }
    try {
      const res = await fetch(`${apiGatewayBase}/api/v1/tdx/rolling-config`, {
        method: 'PUT',
        headers: { ...authHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          score_threshold: threshold,
          fixed_buy_amount: amount,
          execute_mode: rollingExecuteMode,
        }),
      });
      if (res.ok) {
        setRollingCfgMsg('✅ 已保存，推理自动推送即时生效');
        await fetchRollingConfig();
      } else {
        const detail = await extractApiError(res);
        setRollingCfgMsg(`❌ 保存失败: ${detail || `HTTP ${res.status}`}`);
      }
    } catch (e) {
      setRollingCfgMsg(`❌ 保存失败: ${e}`);
    }
  };

  const runRollingCheck = async () => {
    setRollingLoading(true);
    setRollingResult(null);
    try {
      const body: Record<string, unknown> = {};
      if (rollingDate.trim()) body.trade_date = rollingDate.trim();
      const res = await fetch(`${apiGatewayBase}/api/v1/tdx/rolling-signals`, {
        method: 'POST',
        headers: { ...authHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setRollingResult({
          ok: true,
          buys: data.buys,
          sells: data.sells,
          market: data.market,
          placedOrders: data.placed_orders,
          failedOrders: data.failed_orders,
        });
      } else {
        setRollingResult({ ok: false, error: data.error || data.detail || `HTTP ${res.status}` });
      }
    } catch (e) {
      setRollingResult({ ok: false, error: String(e) });
    } finally {
      setRollingLoading(false);
    }
  };

  useEffect(() => {
    if (!isActive) return;
    fetchBootstrap();
    fetchTdxConfig();
    fetchTdxOverview();
    fetchRollingConfig();
    fetchSltpConfig();
    const timer = setInterval(fetchTdxOverview, 8000);
    return () => clearInterval(timer);
  }, [isActive, userId]);

  if (!isActive) return null;

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="px-4 pt-4 pb-3 border-b border-gray-200 bg-gray-50/30 shrink-0">
        <h3 className="text-xl font-bold text-gray-800 flex items-center">
          <Settings className="mr-3 text-blue-600" size={24} />
          模拟交易设置
        </h3>
        <p className="text-xs text-gray-500 mt-1">
          管理接入凭证与 API 密钥。
        </p>
      </div>

      {/* 顶部切换按钮 */}
      <div className="px-4 py-3 bg-gray-50/30 shrink-0 flex items-center gap-2">
        <button
          onClick={() => setActiveTab('credentials')}
          className={`px-4 py-2 rounded-xl text-xs font-bold transition-colors ${
            activeTab === 'credentials'
              ? 'bg-white text-indigo-700 border border-indigo-200 shadow-sm'
              : 'bg-white/60 text-gray-500 border border-gray-200 hover:text-gray-700'
          }`}
        >
          <Key size={13} className="inline mr-1.5 -mt-0.5" />
          接入凭证 / API 密钥
        </button>
        <button
          onClick={() => setActiveTab('tdx')}
          className={`px-4 py-2 rounded-xl text-xs font-bold transition-colors ${
            activeTab === 'tdx'
              ? 'bg-white text-emerald-700 border border-emerald-200 shadow-sm'
              : 'bg-white/60 text-gray-500 border border-gray-200 hover:text-gray-700'
          }`}
        >
          <Cable size={13} className="inline mr-1.5 -mt-0.5" />
          通达信交易桥 / 滚动买卖
        </button>
      </div>

      {/* 内容区：两个面板各自独立滚动 */}
      <div className="flex-1 min-h-0 px-4 pb-4">
        <div
          className={`h-full bg-white rounded-3xl border border-gray-200 shadow-sm overflow-y-auto custom-scrollbar ${
            activeTab === 'credentials' ? '' : 'hidden'
          }`}
        >
          <div className="p-5 flex flex-col gap-4">
          <div className="space-y-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm font-bold text-gray-900">接入凭证</div>
                <div className="text-xs text-gray-500 mt-1">
                  Access Key 用于鉴权，Secret Key 仅在重置后展示一次，请立即保存。
                </div>
              </div>
              <button
                onClick={fetchBootstrap}
                disabled={loading}
                className="shrink-0 text-xs text-indigo-500 hover:text-indigo-700 font-medium flex items-center gap-1"
              >
                <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
                刷新
              </button>
            </div>

            <div className="space-y-3">
              <div className="rounded-2xl border border-gray-100 bg-white px-4 py-3">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-indigo-50 rounded-xl text-indigo-600 shrink-0">
                    <Key size={18} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-xs text-gray-500 mb-1">Access Key</div>
                    <div className="flex items-center gap-2 min-w-0 bg-white px-3 py-2 rounded-2xl border border-gray-100">
                      <code className="text-xs font-mono text-indigo-700 truncate flex-1">
                        {keyInfo ? (showAccessKey ? keyInfo.access_key : maskValue(keyInfo.access_key)) : '-'}
                      </code>
                      {keyInfo && (
                        <>
                          <button onClick={() => setShowAccessKey(!showAccessKey)} className="p-1 text-gray-500 hover:text-gray-700">
                            {showAccessKey ? <EyeOff size={14} /> : <Eye size={14} />}
                          </button>
                          <button onClick={() => handleCopy(keyInfo.access_key, 'access_key')} className="p-1 text-gray-500 hover:text-indigo-600">
                            {copied === 'access_key' ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
                          </button>
                          <div className={`hidden sm:flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold ${keyInfo.is_active ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                            <ShieldCheck size={10} />
                            {keyInfo.is_active ? '可用' : '已禁用'}
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              <div className="rounded-2xl border border-gray-100 bg-white px-4 py-3">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-amber-50 rounded-xl text-amber-700 shrink-0">
                    <Key size={18} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-xs text-gray-500 mb-1">Secret Key</div>
                    <div className="flex items-center gap-3">
                      <div className="flex items-center gap-2 bg-white px-3 py-2 rounded-2xl border border-gray-100 flex-1 min-w-0">
                        <code className="text-xs font-mono text-amber-900 truncate flex-1">
                          {secretKey ? (showSecretKey ? secretKey : maskValue(secretKey)) : '未展示，点击右侧按钮重新生成'}
                        </code>
                        {secretKey && (
                          <>
                            <button onClick={() => setShowSecretKey(!showSecretKey)} className="p-1 text-gray-500 hover:text-gray-700">
                              {showSecretKey ? <EyeOff size={14} /> : <Eye size={14} />}
                            </button>
                            <button onClick={() => handleCopy(secretKey, 'secret_key')} className="p-1 text-gray-500 hover:text-amber-700">
                              {copied === 'secret_key' ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
                            </button>
                          </>
                        )}
                      </div>
                      <button
                        onClick={rotateSecret}
                        disabled={!keyInfo || loading}
                        className="shrink-0 px-3 py-2 rounded-xl bg-gray-900 text-white text-xs font-bold hover:bg-black disabled:opacity-50"
                      >
                        重置密钥
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        </div>

        {/* 通达信交易桥卡片 */}
        <div
          className={`h-full bg-white rounded-3xl border border-gray-200 shadow-sm overflow-y-auto custom-scrollbar ${
            activeTab === 'tdx' ? '' : 'hidden'
          }`}
        >
        <div className="p-5 flex flex-col gap-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-sm font-bold text-gray-900 flex items-center">
                <Cable className="mr-2 text-emerald-600" size={16} />
                通达信交易桥
              </div>
              <div className="text-xs text-gray-500 mt-1">
                QuantMind 通过桥连接 Windows 通达信，推送选股/下单/拉取账户状态。
              </div>
            </div>
            <button
              onClick={fetchTdxConfig}
              disabled={tdxLoading}
              className="shrink-0 text-xs text-emerald-600 hover:text-emerald-700 font-medium flex items-center gap-1"
            >
              <RefreshCw size={14} className={tdxLoading ? 'animate-spin' : ''} />
              刷新
            </button>
          </div>

          {tdxError && (
            <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">
              {tdxConfig ? `桥配置刷新失败（显示旧数据）: ${tdxError}` : `通达信桥配置加载失败: ${tdxError}`}
            </div>
          )}

          {tdxConfig && (
            <>
              <div className="flex flex-wrap gap-2">
                <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold ${tdxConfig.enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-500'}`}>
                  <Wifi size={11} />
                  自动推送: {tdxConfig.enabled ? '开启' : '关闭'}
                </span>
                <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold ${tdxConfig.real_trading_enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-500'}`}>
                  <ShieldCheck size={11} />
                  实盘: {tdxConfig.real_trading_enabled ? '开启' : '关闭'}
                </span>
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold bg-blue-100 text-blue-700">
                  桥: {tdxConfig.bridge_url || '-'}
                </span>
                <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold ${tdxConfig.bridge_token_configured ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                  Token: {tdxConfig.bridge_token_configured ? '已配置' : '未配置'}
                </span>
              </div>

              {tdxConfig.health && (
                <div className={`rounded-2xl border px-4 py-3 text-xs ${tdxConfig.health.error ? 'border-red-200 bg-red-50 text-red-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`}>
                  {tdxConfig.health.error
                    ? `桥不可达: ${tdxConfig.health.error}`
                    : `桥在线 · 通达信客户端: ${tdxConfig.health.tdx_connected ? '已连接' : '未登录(17709)'}`}
                </div>
              )}

              {/* 局域网桥信息 */}
              {tdxOverview?.available && (
                <div className="rounded-2xl border border-slate-200 bg-slate-50/50 overflow-hidden">
                  <div className="px-4 py-2.5 flex items-center justify-between border-b border-slate-200/70">
                    <span className="text-xs font-bold text-slate-700 flex items-center gap-1.5">
                      <Server size={13} className="text-emerald-600" />
                      局域网桥信息
                    </span>
                    <span className="text-[10px] text-slate-400 font-medium flex items-center gap-1">
                      <RefreshCw size={11} className={tdxOverviewLoading ? 'animate-spin' : ''} />
                      {tdxOverview.bridge?.server_time ? `同步于 ${String(tdxOverview.bridge.server_time).slice(11, 19)}` : '每 8s 自动刷新'}
                    </span>
                  </div>
                  <div className="px-4 py-2 flex items-center justify-between border-b border-slate-200/70 bg-white/50">
                    <span className="text-[11px] text-slate-500">
                      模型推理选股 → 通达信板块/预警
                    </span>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={pushSignalsToTdx}
                        disabled={pushLoading || !tdxOverview?.bridge?.tdx_connected}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                      >
                        <Send size={12} className={pushLoading ? 'animate-pulse' : ''} />
                        {pushLoading ? '推送中…' : '推送今日选股'}
                      </button>
                      <button
                        onClick={runRollingCheck}
                        disabled={rollingLoading || !tdxOverview?.bridge?.tdx_connected}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                      >
                        <RefreshCw size={12} className={rollingLoading ? 'animate-spin' : ''} />
                        {rollingLoading ? '检查中…' : rollingDate ? `推${rollingDate}分数` : '滚动买卖检查'}
                      </button>
                    </div>
                  </div>

                  {/* 滚动买卖配置 */}
                  <div className="px-4 py-3 border-b border-slate-200/70 bg-white/50 space-y-2">
                    <div className="text-[11px] font-bold text-slate-600 flex items-center gap-1.5">
                      <SlidersHorizontal size={12} className="text-emerald-600" />
                      滚动买卖配置（阈值与金额可自己改）
                    </div>
                    <div className="flex flex-wrap items-end gap-3">
                      <label className="flex flex-col gap-1">
                        <span className="text-[10px] text-slate-400 font-medium">买入分数阈值（{'>'}此分买入，低于则卖出）</span>
                        <input
                          type="number"
                          step="0.1"
                          min="0"
                          max="10"
                          value={rollingThreshold}
                          onChange={(e) => setRollingThreshold(e.target.value)}
                          className="w-28 px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-mono font-bold text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                        />
                      </label>
                      <label className="flex flex-col gap-1">
                        <span className="text-[10px] text-slate-400 font-medium">每只固定买入金额（元）</span>
                        <input
                          type="number"
                          step="500"
                          min="1000"
                          value={rollingAmount}
                          onChange={(e) => setRollingAmount(e.target.value)}
                          className="w-32 px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-mono font-bold text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                        />
                      </label>
                      <label className="flex flex-col gap-1">
                        <span className="text-[10px] text-slate-400 font-medium">推历史日期（YYYY-MM-DD，留空=最新）</span>
                        <input
                          type="date"
                          value={rollingDate}
                          onChange={(e) => setRollingDate(e.target.value)}
                          className="w-40 px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-mono text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                        />
                      </label>
                      <button
                        onClick={saveRollingConfig}
                        className="px-3 py-1.5 rounded-lg text-[11px] font-bold bg-slate-700 text-white hover:bg-slate-800 transition-colors"
                      >
                        保存配置
                      </button>
                    </div>
                    <div className="text-[10px] text-slate-400 font-medium">执行模式（直接下单为 QuantDB 付费会员专属）</div>
                    <div className="flex items-center gap-1.5">
                      {(
                        [
                          ['off', '仅预警', '只推通达信预警，不下单'],
                          ['tdx', '通达信下单', '生成真实委托，客户端确认后成交'],
                          ['paper', '模拟盘直接下单', '本地模拟盘自动成交，免确认、零风险'],
                        ] as const
                      ).map(([mode, label, hint]) => (
                        <button
                          key={mode}
                          type="button"
                          onClick={() => setRollingExecuteMode(mode)}
                          title={hint}
                          className={`px-3 py-1.5 rounded-lg text-[11px] font-bold transition-colors ${
                            rollingExecuteMode === mode
                              ? mode === 'paper'
                                ? 'bg-violet-600 text-white shadow-sm'
                                : mode === 'tdx'
                                  ? 'bg-emerald-600 text-white shadow-sm'
                                  : 'bg-slate-700 text-white shadow-sm'
                              : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                          }`}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <div className="text-[10px] text-slate-400 leading-relaxed">
                      卖单市价、买单收盘价限价；先卖后买。模拟盘直接下单在 QuantMind 模拟盘账户本地撮合成交，无需通达信客户端确认。
                    </div>
                    {rollingCfgMsg && (
                      <div className={`text-[11px] font-medium ${rollingCfgMsg.startsWith('✅') ? 'text-emerald-600' : 'text-red-600'}`}>
                        {rollingCfgMsg}
                      </div>
                    )}
                    <div className="text-[10px] text-slate-400 leading-relaxed">
                      规则：分数 {'>'} 阈值 → 买入；持仓分数 ≤ 阈值 → 卖出；大盘低于 MA20 → 只卖不买。推历史日期时跳过当日大盘过滤。执行模式选「通达信下单」或「模拟盘直接下单」后，信号直接生成委托（卖先买后）。
                    </div>
                  </div>

                  {/* 止损止盈实时提醒配置（仅限持仓股，盘中现价触发即提醒） */}
                  <div className="px-4 py-3 border-b border-slate-200/70 bg-white/50 space-y-2">
                    <div className="text-[11px] font-bold text-slate-600 flex items-center gap-1.5">
                      <ShieldCheck size={12} className="text-rose-600" />
                      止损止盈实时提醒（仅持仓股 · 现价触发即推送，不下自动单）
                    </div>
                    <div className="flex flex-wrap items-end gap-3">
                      <label className="flex flex-col gap-1">
                        <span className="text-[10px] text-slate-400 font-medium">止损幅度 %（现价 ≤ 成本×(1-x%)）</span>
                        <input
                          type="number"
                          step="1"
                          min="0"
                          max="50"
                          value={sltpStopLoss}
                          onChange={(e) => setSltpStopLoss(e.target.value)}
                          className="w-24 px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-mono font-bold text-slate-700 focus:outline-none focus:ring-2 focus:ring-rose-400/40"
                        />
                      </label>
                      <label className="flex flex-col gap-1">
                        <span className="text-[10px] text-slate-400 font-medium">止盈幅度 %（留空不启用）</span>
                        <input
                          type="number"
                          step="1"
                          min="0"
                          max="100"
                          value={sltpTakeProfit}
                          onChange={(e) => setSltpTakeProfit(e.target.value)}
                          placeholder="如 10"
                          className="w-24 px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-mono text-slate-700 focus:outline-none focus:ring-2 focus:ring-rose-400/40"
                        />
                      </label>
                      <label className="flex flex-col gap-1">
                        <span className="text-[10px] text-slate-400 font-medium">移动止损 %（离持仓最高价回撤）</span>
                        <input
                          type="number"
                          step="1"
                          min="0"
                          max="50"
                          value={sltpTrailing}
                          onChange={(e) => setSltpTrailing(e.target.value)}
                          placeholder="如 5"
                          className="w-24 px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-mono text-slate-700 focus:outline-none focus:ring-2 focus:ring-rose-400/40"
                        />
                      </label>
                      <button
                        onClick={saveSltpConfig}
                        className="px-3 py-1.5 rounded-lg text-[11px] font-bold bg-rose-600 text-white hover:bg-rose-700 transition-colors"
                      >
                        保存提醒
                      </button>
                    </div>
                    <label className="flex items-center gap-2 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={sltpEnabled}
                        onChange={(e) => setSltpEnabled(e.target.checked)}
                        className="w-3.5 h-3.5 accent-rose-600"
                      />
                      <span className="text-[11px] font-bold text-slate-700">
                        启用实时止损止盈提醒
                        <span className="text-slate-400 font-medium">（触发时站内通知 + 通达信预警弹窗；通达信守护进程负责真实止损单）</span>
                      </span>
                    </label>
                    {sltpMsg && (
                      <div className={`text-[11px] font-medium ${sltpMsg.startsWith('✅') ? 'text-emerald-600' : 'text-red-600'}`}>
                        {sltpMsg}
                      </div>
                    )}
                    <div className="text-[10px] text-slate-400 leading-relaxed">
                      行情链路：通达信实时快照 → 行情 Feed → Redis → WebSocket；每 3s 校验一次持仓股现价，触发后 5 分钟内同股不重复提醒。
                    </div>
                  </div>
                  {rollingResult && (
                    <div className={`px-4 py-2.5 text-[11px] border-b ${rollingResult.ok ? 'border-emerald-100 bg-emerald-50/60 text-emerald-700' : 'border-red-100 bg-red-50/60 text-red-700'}`}>
                      {rollingResult.ok ? (
                        <div className="space-y-1.5">
                          <div className="font-bold flex items-center gap-2">
                            {rollingResult.market?.above_ma20 ? '大盘站上 MA20 · 可买可卖' : '大盘低于 MA20 · 只卖不买'}
                            <span className="text-[10px] font-mono font-medium text-emerald-600/80">{rollingResult.market?.detail}</span>
                          </div>
                          {rollingResult.buys && rollingResult.buys.length > 0 && (
                            <div>
                              <span className="font-bold text-rose-600">买入预警 {rollingResult.buys.length} 只</span>
                              <div className="mt-1 flex flex-wrap gap-1">
                                {(rollingResult.buys || []).map((b) => (
                                  <span key={b.symbol} className="px-1.5 py-0.5 rounded bg-white border border-rose-100 font-mono text-[10px]">
                                    {b.symbol} {b.name} <b>{b.score}</b> {b.volume}股
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}
                          {rollingResult.sells && rollingResult.sells.length > 0 && (
                            <div>
                              <span className="font-bold text-emerald-600">卖出预警 {rollingResult.sells.length} 只</span>
                              <div className="mt-1 flex flex-wrap gap-1">
                                {(rollingResult.sells || []).map((s) => (
                                  <span key={s.symbol} className="px-1.5 py-0.5 rounded bg-white border border-emerald-100 font-mono text-[10px]">
                                    {s.symbol} {s.name} {s.score ?? '无分数'}
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}
                          {(!rollingResult.buys?.length && !rollingResult.sells?.length) && (
                            <span>无买卖动作（持仓均 {'>'} {rollingThreshold} 分且无新增候选）</span>
                          )}
                          {rollingResult.placedOrders && rollingResult.placedOrders.length > 0 && (
                            <div>
                              <span className="font-bold text-amber-600">已推通达信下单 {rollingResult.placedOrders.length} 笔（客户端弹确认框）</span>
                              <div className="mt-1 flex flex-wrap gap-1">
                                {rollingResult.placedOrders.map((o, i) => (
                                  <span key={`${o.symbol}-${i}`} className={`px-1.5 py-0.5 rounded bg-white border font-mono text-[10px] ${o.side === 'buy' ? 'border-rose-100' : 'border-emerald-100'}`}>
                                    {o.side === 'buy' ? '买' : '卖'} {o.symbol} {o.volume}股 · {o.status}
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}
                          {rollingResult.failedOrders && rollingResult.failedOrders.length > 0 && (
                            <div>
                              <span className="font-bold text-red-500">下单失败 {rollingResult.failedOrders.length} 笔</span>
                              <div className="mt-1 flex flex-wrap gap-1">
                                {rollingResult.failedOrders.map((f, i) => (
                                  <span key={`${f.symbol}-${i}`} className="px-1.5 py-0.5 rounded bg-white border border-red-100 font-mono text-[10px]" title={f.error}>
                                    {f.side === 'buy' ? '买' : '卖'} {f.symbol}: {f.error}
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      ) : (
                        <span>滚动检查失败: {rollingResult.error}</span>
                      )}
                    </div>
                  )}
                  <div className="p-4 grid grid-cols-2 gap-3">
                    <div className="rounded-xl bg-white border border-slate-100 p-3">
                      <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">桥主机</div>
                      <div className="text-sm font-bold text-slate-800 font-mono truncate">{tdxOverview.bridge?.hostname || '-'}</div>
                      <div className="text-[10px] text-slate-500 font-mono mt-0.5 truncate">{(tdxOverview.bridge?.local_ips || []).join(' / ')}</div>
                    </div>
                    <div className="rounded-xl bg-white border border-slate-100 p-3">
                      <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">通达信连接</div>
                      <div className="flex items-center gap-1.5">
                        <span className={`w-2 h-2 rounded-full ${tdxOverview.bridge?.tdx_connected ? 'bg-emerald-500' : 'bg-red-500'}`} />
                        <span className="text-sm font-bold text-slate-800">{tdxOverview.bridge?.tdx_connected ? '已连接' : '未连接'}</span>
                      </div>
                      <div className="text-[10px] text-slate-500 font-mono mt-0.5 truncate">端口 {tdxOverview.bridge?.port ?? '-'}</div>
                    </div>
                    <div className="rounded-xl bg-white border border-slate-100 p-3">
                      <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">账户资产</div>
                      <div className="text-sm font-bold text-red-600 font-mono">
                        {(tdxOverview.account?.asset ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
                      </div>
                      <div className="text-[10px] text-slate-500 mt-0.5">
                        可用 <span className="font-mono font-semibold text-slate-700">{(tdxOverview.account?.cash ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</span>
                      </div>
                    </div>
                    <div className="rounded-xl bg-white border border-slate-100 p-3">
                      <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">持仓 / 市值</div>
                      <div className="text-sm font-bold text-slate-800 font-mono">
                        {(tdxOverview.account?.position_count ?? 0)} 只
                      </div>
                      <div className="text-[10px] text-slate-500 mt-0.5">
                        市值 <span className="font-mono font-semibold text-slate-700">{(tdxOverview.account?.market_value ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</span>
                      </div>
                    </div>
                    <div className="rounded-xl bg-white border border-slate-100 p-3">
                      <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">当日委托 / 持仓明细</div>
                      <div className="text-sm font-bold text-slate-800">
                        委托 <span className="font-mono">{tdxOverview.orders?.length ?? 0}</span>
                        <span className="mx-1 text-slate-300">·</span>
                        持仓 <span className="font-mono">{tdxOverview.positions?.length ?? 0}</span>
                      </div>
                      <div className="text-[10px] text-slate-500 mt-0.5 truncate">
                        {(tdxOverview.positions || []).map((p: any) => p.symbol || p.stock_code || p.code).filter(Boolean).slice(0, 3).join(', ') || '无持仓'}
                      </div>
                    </div>
                    <div className="rounded-xl bg-white border border-slate-100 p-3">
                      <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">缓存 / 安全</div>
                      <div className="text-xs font-bold text-slate-700">
                        K线 <span className="font-mono">{tdxOverview.cache?.kline ?? 0}</span>
                        <span className="mx-1 text-slate-300">·</span>
                        快照 <span className="font-mono">{tdxOverview.cache?.market_snapshot ?? 0}</span>
                      </div>
                      <div className="text-[10px] text-slate-500 mt-0.5">
                        活跃IP <span className="font-mono font-semibold text-slate-700">{tdxOverview.security?.active_ips ?? 0}</span>
                        {tdxOverview.security?.banned_ips ? <span className="text-red-500 font-mono"> · 封禁 {tdxOverview.security?.banned_ips}</span> : null}
                      </div>
                    </div>
                  </div>
                  {pushResult && (
                    <div className={`px-4 py-2.5 text-[11px] border-t ${pushResult.ok ? 'border-emerald-100 bg-emerald-50/60 text-emerald-700' : 'border-red-100 bg-red-50/60 text-red-700'}`}>
                      {pushResult.ok ? (
                        <div>
                          <span className="font-bold">已推送 {pushResult.pushed} 只</span>
                          {pushResult.skipped ? <span className="ml-1 text-emerald-600/70">（过滤 {pushResult.skipped}）</span> : null}
                          <span className="ml-2 font-mono text-[10px] text-emerald-600/80">{pushResult.runId}</span>
                          <div className="mt-1 flex flex-wrap gap-1">
                            {(pushResult.stocks || []).map((s) => (
                              <span key={s.rank} className="px-1.5 py-0.5 rounded bg-white border border-emerald-100 font-mono text-[10px]">
                                {s.rank}.{s.symbol} {s.name} <b>{s.score}</b>
                              </span>
                            ))}
                          </div>
                        </div>
                      ) : (
                        <span>推送失败: {pushResult.error}</span>
                      )}
                    </div>
                  )}
                </div>
              )}

              {tdxOverview && !tdxOverview.available && (
                <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
                  局域网桥信息暂不可用: {tdxOverview.error || '未知原因'}
                </div>
              )}

              <div className="space-y-3">
                <div>
                  <div className="text-xs text-gray-500 mb-1">桥地址</div>
                  <input
                    type="text"
                    value={tdxNewUrl}
                    onChange={(e) => setTdxNewUrl(e.target.value)}
                    placeholder="http://192.168.31.31:8550"
                    className="w-full px-3 py-2 rounded-xl border border-gray-200 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-emerald-500"
                  />
                </div>
                <div>
                  <div className="text-xs text-gray-500 mb-1">桥 Token (64位 hex, 与 Windows 侧一致)</div>
                  <input
                    type="text"
                    value={tdxNewToken}
                    onChange={(e) => setTdxNewToken(e.target.value)}
                    placeholder="输入新 token (留空则保持现有)"
                    className="w-full px-3 py-2 rounded-xl border border-gray-200 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-emerald-500"
                  />
                </div>
                {tdxMsg && <div className="text-xs font-medium text-gray-700">{tdxMsg}</div>}
                <button
                  onClick={updateTdxConfig}
                  disabled={tdxLoading || (!tdxNewToken.trim() && !tdxNewUrl.trim())}
                  className="px-4 py-2 rounded-xl bg-emerald-600 text-white text-xs font-bold hover:bg-emerald-700 disabled:opacity-50"
                >
                  保存配置
                </button>
              </div>
            </>
          )}
        </div>
        </div>
      </div>
    </div>
  );
};

export default SettingsCenter;
