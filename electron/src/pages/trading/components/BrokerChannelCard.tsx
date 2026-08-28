/** 实盘券商通道卡（策略管理页）：按市场展示可用券商、配置状态、当前选中，可直接切换。 */
import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Tag, message } from 'antd';
import { BankOutlined, CheckCircleOutlined, SettingOutlined } from '@ant-design/icons';
import { authService } from '../../../features/auth/services/authService';
import { SERVICE_URLS } from '../../../config/services';

const apiBase = `${SERVICE_URLS.API_GATEWAY}/api/v1`;

interface BrokerItem {
    broker: string;
    label: string;
    configured: boolean;
}

const MARKET_LABEL: Record<string, string> = {
    HK: '港股',
    US: '美股',
    FUTURES: '期货',
    CRYPTO: '加密货币',
};

const T0_MARKETS = new Set(['HK', 'US', 'FUTURES', 'CRYPTO']);

export const BrokerChannelCard: React.FC<{ market: string }> = ({ market }) => {
    const [items, setItems] = useState<BrokerItem[]>([]);
    const [selected, setSelected] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState<string | null>(null);

    const authHeaders = () => {
        const token = authService.getAccessToken();
        return token ? { Authorization: `Bearer ${token}` } : undefined;
    };

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const resp = await fetch(`${apiBase}/broker-config-status?market=${market}`, {
                headers: authHeaders(),
            });
            const data = await resp.json();
            setItems(data?.brokers ?? []);
            setSelected(data?.selected ?? null);
        } catch {
            /* 静默：显示为未配置 */
        } finally {
            setLoading(false);
        }
    }, [market]);

    useEffect(() => {
        void load();
    }, [load]);

    const selectBroker = async (broker: string) => {
        setSaving(broker);
        try {
            const resp = await fetch(`${apiBase}/broker-config/selected/${market}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', ...authHeaders() },
                body: JSON.stringify({ broker }),
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            setSelected(broker);
            message.success(`已选择 ${broker.toUpperCase()} 作为该市场实盘通道`);
        } catch (e: any) {
            message.error(e?.message || '选择失败');
        } finally {
            setSaving(null);
        }
    };

    const goSettings = () => {
        // 跳到交易页「设置」tab（RealTradingPage 监听该事件切 tab）
        window.dispatchEvent(new CustomEvent('goto-trading-settings', { detail: { market } }));
    };

    const marketLabel = MARKET_LABEL[market.toUpperCase()] || market;
    const t0 = T0_MARKETS.has(market.toUpperCase());

    return (
        <div className="bg-indigo-50/70 border border-indigo-200 rounded-2xl p-4 space-y-3">
            <div className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-3 flex-1">
                    <div className="shrink-0 mt-0.5">
                        <Alert type="info" showIcon className="!mb-0 !p-1.5" />
                    </div>
                    <div className="text-xs leading-5 text-slate-600">
                        <span className="font-bold text-slate-800">
                            {marketLabel}实盘走券商 OpenAPI 通道
                            {t0 ? '（T+0 交易，与 A 股 T+1 规则不同）' : ''}。
                        </span>
                        选择该市场使用的券商，并完成凭证配置；未配置的券商无法接收实盘委托。
                    </div>
                </div>
                <Button size="small" icon={<SettingOutlined />} onClick={goSettings}>
                    去配置凭证
                </Button>
            </div>

            <div className="flex flex-wrap gap-2">
                {items.map(({ broker, label, configured }) => {
                    const isSelected = selected === broker;
                    return (
                        <button
                            key={broker}
                            onClick={() => selectBroker(broker)}
                            disabled={saving === broker}
                            className={`px-3 py-2 rounded-xl text-xs font-bold border transition-colors flex items-center gap-2 ${
                                isSelected
                                    ? 'bg-indigo-600 text-white border-indigo-500 shadow-sm'
                                    : 'bg-white text-slate-700 border-slate-200 hover:border-indigo-300'
                            }`}
                        >
                            <BankOutlined className={isSelected ? '' : 'text-slate-400'} />
                            {label}
                            {configured ? (
                                <Tag color="green" className="!text-[10px] !mr-0 inline-flex items-center gap-0.5">
                                    <CheckCircleOutlined /> 已配置
                                </Tag>
                            ) : (
                                <Tag className="!text-[10px] !mr-0">未配置</Tag>
                            )}
                            {isSelected && <Tag color="blue" className="!text-[10px] !mr-0">使用中</Tag>}
                        </button>
                    );
                })}
                {items.length === 0 && !loading && (
                    <span className="text-xs text-slate-400">当前市场暂无可用券商通道</span>
                )}
            </div>
        </div>
    );
};

export default BrokerChannelCard;
