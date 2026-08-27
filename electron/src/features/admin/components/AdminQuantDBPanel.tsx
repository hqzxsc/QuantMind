import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    Alert, Button, Card, Checkbox, Col, Descriptions, Progress, Row, Space,
    Statistic, Tag, Typography, message,
} from 'antd';
import {
    ApiOutlined, CheckCircleFilled, CloseCircleFilled,
    DatabaseOutlined, KeyOutlined, ReloadOutlined,
} from '@ant-design/icons';
import { dataPlatformService, QuantDBDataset } from '../services/dataPlatformService';
import { QuantDBCatalogPanel } from './quantdb/QuantDBCatalogPanel';
import { QuantDBPreviewDrawer } from './quantdb/QuantDBPreviewDrawer';
import { describeError } from './quantdb/utils';
import { SyncSchedulePanel } from './data-management/SyncSchedulePanel';

const { Text } = Typography;

const USAGE_WARN_PERCENT = 70;
const USAGE_DANGER_PERCENT = 90;
const LOW_QUOTA_GB = 5;

interface QuantDBInfo {
    installed: boolean;
    api_key_configured: boolean;
    connected: boolean;
    version?: string;
    account?: { username: string; email: string };
    usage?: {
        used_gb: number;
        limit_gb: number;
        remaining_gb: number;
        credit_gb?: number;
        subscription?: { status: string };
    };
    error?: string;
}

export const AdminQuantDBPanel: React.FC = () => {
    const navigate = useNavigate();
    const [loading, setLoading] = useState(false);
    const [info, setInfo] = useState<QuantDBInfo | null>(null);
    const [previewDataset, setPreviewDataset] = useState<QuantDBDataset | null>(null);
    const [catalogRefreshSignal, setCatalogRefreshSignal] = useState(0);
    const refreshCounter = useRef(0);
    const bumpCatalogRefresh = useCallback(() => {
        refreshCounter.current += 1;
        setCatalogRefreshSignal(refreshCounter.current);
    }, []);
    const [sources, setSources] = useState<Array<{ source: string; label: string; enabled: boolean }>>([]);
    const [sourcesLoading, setSourcesLoading] = useState(false);

    const loadInfo = useCallback(async () => {
        setLoading(true);
        try {
            const resp = await dataPlatformService.getQuantDBInfo();
            setInfo(resp.quantdb);
        } catch (error: unknown) {
            message.error(`获取 QuantDB 状态失败: ${describeError(error)}`);
        } finally {
            setLoading(false);
        }
    }, []);

    const loadSources = useCallback(async () => {
        setSourcesLoading(true);
        try {
            const resp = await dataPlatformService.getMarketDataSources('quantdb');
            setSources(resp.sources);
        } catch (error: unknown) {
            message.error(`加载数据源配置失败: ${describeError(error)}`);
        } finally {
            setSourcesLoading(false);
        }
    }, []);

    const saveSources = useCallback(async (source: string, enabled: boolean) => {
        const next = sources.map((s) => (s.source === source ? { ...s, enabled } : s));
        setSources(next);
        try {
            const payload: Record<string, boolean> = {};
            next.forEach((s) => { payload[s.source] = s.enabled; });
            await dataPlatformService.saveMarketDataSources('quantdb', payload);
            message.success('A股数据源配置已保存');
        } catch (error: unknown) {
            message.error(`保存数据源配置失败: ${describeError(error)}`);
            loadSources();
        }
    }, [sources, loadSources]);

    useEffect(() => {
        loadSources();
    }, [loadSources]);

    useEffect(() => {
        loadInfo();
    }, [loadInfo]);

    const usagePercent = info?.usage && info.usage.limit_gb > 0
        ? Math.round((info.usage.used_gb / info.usage.limit_gb) * 100)
        : 0;

    return (
        <div className="space-y-4">
            {/* QuantDB SDK / 账号状态卡片 */}
            <Card
                size="small"
                title={
                    <Space>
                        <DatabaseOutlined />
                        <span>QuantDB 云端直供状态 (A股)</span>
                        <Tag color={info?.connected ? 'green' : 'red'}>
                            {info?.connected ? '已连接' : '未连接'}
                        </Tag>
                    </Space>
                }
                extra={
                    <Button
                        size="small"
                        icon={<ReloadOutlined />}
                        onClick={() => { loadInfo(); loadSources(); }}
                        loading={loading}
                    >
                        刷新
                    </Button>
                }
            >
                {info?.error && <Alert type="error" message={info.error} className="mb-4" showIcon />}

                <Row gutter={16}>
                    <Col span={6}>
                        <Statistic
                            title="SDK 状态"
                            value={info?.installed ? '已安装' : '未安装'}
                            prefix={info?.installed
                                ? <CheckCircleFilled style={{ color: '#52c41a' }} />
                                : <CloseCircleFilled style={{ color: '#ff4d4f' }} />}
                            valueStyle={{ fontSize: 16 }}
                        />
                        {info?.version && <Text type="secondary" className="text-xs">v{info.version}</Text>}
                    </Col>
                    <Col span={6}>
                        <Statistic
                            title="API Key"
                            value={info?.api_key_configured ? '已配置' : '未配置'}
                            prefix={<ApiOutlined />}
                            valueStyle={{ fontSize: 16, color: info?.api_key_configured ? '#52c41a' : '#ff4d4f' }}
                        />
                    </Col>
                    <Col span={6}>
                        <Statistic
                            title="已用流量"
                            value={info?.usage?.used_gb?.toFixed(2) ?? '-'}
                            suffix="GB"
                            prefix={<DatabaseOutlined />}
                            valueStyle={{ fontSize: 16 }}
                        />
                    </Col>
                    <Col span={6}>
                        <Statistic
                            title="剩余流量"
                            value={info?.usage?.remaining_gb?.toFixed(2) ?? '-'}
                            suffix="GB"
                            valueStyle={{
                                fontSize: 16,
                                color: (info?.usage?.remaining_gb ?? 0) < LOW_QUOTA_GB ? '#ff4d4f' : '#52c41a',
                            }}
                        />
                    </Col>
                </Row>

                {info?.usage && (
                    <div className="mt-4">
                        <Progress
                            percent={usagePercent}
                            status={usagePercent > USAGE_DANGER_PERCENT
                                ? 'exception'
                                : usagePercent > USAGE_WARN_PERCENT ? 'active' : 'normal'}
                            format={() => `${info.usage!.used_gb.toFixed(1)} / ${info.usage!.limit_gb} GB`}
                        />
                        <div className="flex gap-4 mt-2">
                            {info.usage.subscription && (
                                <Tag color="blue">订阅: {info.usage.subscription.status}</Tag>
                            )}
                            {info.usage.credit_gb !== undefined && info.usage.credit_gb > 0 && (
                                <Tag color="green">赠送: {info.usage.credit_gb} GB</Tag>
                            )}
                        </div>
                    </div>
                )}

                {info?.account && (
                    <Descriptions size="small" column={2} className="mt-4">
                        <Descriptions.Item label="用户名">{info.account.username}</Descriptions.Item>
                        <Descriptions.Item label="邮箱">{info.account.email}</Descriptions.Item>
                    </Descriptions>
                )}
            </Card>

            {/* 数据源勾选配置 */}
            <div className="p-3 bg-gray-50 rounded">
                <Space direction="vertical" className="w-full" size="small">
                    <Space>
                        <DatabaseOutlined />
                        <Text strong>数据源</Text>
                        <Text type="secondary" className="text-xs">默认 QuantDB A股/akshare/北向/南向；雅虎默认关闭不勾选</Text>
                    </Space>
                    <Space wrap size="small">
                        {sources.map((s) => (
                            <Checkbox
                                key={s.source}
                                checked={s.enabled}
                                disabled={sourcesLoading}
                                onChange={(e) => saveSources(s.source, e.target.checked)}
                            >
                                <Text className="text-xs">{s.label}</Text>
                                <Text type="secondary" className="text-xs">({s.source})</Text>
                            </Checkbox>
                        ))}
                    </Space>
                </Space>
            </div>

            {/* API Key 状态与个人中心设置入口 */}
            <div className="flex items-center justify-between bg-white border border-slate-200 rounded-xl px-4 py-2.5 shadow-2xs">
                <Space size="middle">
                    <KeyOutlined className="text-blue-500" />
                    <Text className="text-xs font-semibold">API Key 授权状态:</Text>
                    <Tag
                        color={info?.api_key_configured ? 'green' : 'red'}
                        icon={<ApiOutlined />}
                        className="m-0"
                    >
                        {info?.api_key_configured ? '已授权配置' : '未配置密钥'}
                    </Tag>
                    {info?.account?.username && (
                        <Text type="secondary" className="text-xs">
                            账户: <Text code>{info.account.username}</Text>
                        </Text>
                    )}
                </Space>
                <Button
                    type="link"
                    size="small"
                    className="text-xs text-blue-600 hover:text-blue-700 p-0 font-medium"
                    onClick={() => navigate('/user-center?tab=data-platform')}
                >
                    前往「个人中心 - 数据平台」绑定或更新密钥 →
                </Button>
            </div>

            {/* 定时同步调度面板 (默认 24:00 以后如 00:30) */}
            <SyncSchedulePanel market="A" defaultDays={5} />

            {/* QuantDB 数据集目录与详情 */}
            <QuantDBCatalogPanel
                connected={Boolean(info?.connected)}
                onPreview={setPreviewDataset}
                refreshSignal={catalogRefreshSignal}
            />

            {/* 数据集抽屉预览；抽屉内增量同步完成后刷新目录统计 */}
            <QuantDBPreviewDrawer
                dataset={previewDataset}
                onClose={() => setPreviewDataset(null)}
                onSynced={bumpCatalogRefresh}
            />
        </div>
    );
};

export default AdminQuantDBPanel;
