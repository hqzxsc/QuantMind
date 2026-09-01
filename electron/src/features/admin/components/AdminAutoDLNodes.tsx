import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
    Button,
    Card,
    Col,
    Descriptions,
    Empty,
    Row,
    Space,
    Spin,
    Tag,
    Tooltip,
    Typography,
    Progress,
} from 'antd';
import {
    ReloadOutlined,
    SyncOutlined,
    CheckCircleFilled,
    CloseCircleFilled,
    ThunderboltFilled,
    RocketFilled,
    ClusterOutlined,
} from '@ant-design/icons';
import { adminService } from '../services/adminService';

const { Title, Text } = Typography;

interface NodeInfo {
    id: string;
    type: string;
    name: string;
    host?: string;
    available?: boolean;
}

/** 节点配置详情（getTrainingNodeDetail 返回，不含明文密码） */
interface NodeConfigDetail {
    id: string;
    name?: string;
    host?: string;
    port?: number;
    user?: string;
    work_dir?: string;
    docker_image?: string;
    gpus?: string;
    has_password?: boolean;
    has_key?: boolean;
}

interface NodeStatus {
    id: string;
    name: string;
    host: string;
    online: boolean;
    error?: string;
    is_local?: boolean;
    cpu_cores?: number;
    cpu_load?: number;
    mem_total_mb?: number;
    mem_used_mb?: number;
    disk_total_kb?: number;
    disk_used_kb?: number;
    net_rx_bytes?: number | null;
    net_tx_bytes?: number | null;
    gpus?: Array<{
        util: number;
        mem_used_mb: number;
        mem_total_mb: number;
        temp_c: number;
        name: string;
    }>;
    gpu_error?: string;
    containers?: Array<{ name: string; status: string }>;
    training_active?: boolean;
    ping_ms?: number | null;
}

const fmtMB = (mb?: number): string =>
    mb === undefined ? '—' : mb >= 1024 ? `${(mb / 1024).toFixed(1)}GB` : `${mb}MB`;

const fmtKB = (kb?: number): string =>
    kb === undefined ? '—' : kb >= 1048576 ? `${(kb / 1048576).toFixed(1)}GB` : `${(kb / 1024).toFixed(1)}MB`;

const fmtBytes = (b?: number | null): string =>
    !b ? '—' : b >= 1073741824 ? `${(b / 1073741824).toFixed(2)}GB` : b >= 1048576 ? `${(b / 1048576).toFixed(1)}MB` : `${(b / 1024).toFixed(0)}KB`;

export const AdminAutoDLNodes: React.FC = () => {
    const [nodes, setNodes] = useState<NodeInfo[]>([]);
    const [statusMap, setStatusMap] = useState<Record<string, NodeStatus>>({});
    // 每个远端节点的配置详情（host/port/user/work_dir 等）
    const [configMap, setConfigMap] = useState<Record<string, NodeConfigDetail>>({});
    const [loading, setLoading] = useState(false);
    const [refreshing, setRefreshing] = useState(false);
    const [autoRefresh, setAutoRefresh] = useState(true);
    const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const fetchAll = useCallback(async () => {
        try {
            setRefreshing(true);
            const resp = await adminService.listTrainingNodes();
            const nodeList: NodeInfo[] = resp?.nodes || [];
            setNodes(nodeList);

            const statuses: Record<string, NodeStatus> = {};
            const configs: Record<string, NodeConfigDetail> = {};
            const remote = nodeList.filter((n) => n.type === 'remote');
            if (remote.length > 0) {
                await Promise.all(
                    remote.map(async (n) => {
                        // 并行采集状态 + 读取配置详情
                        await Promise.all([
                            adminService.getTrainingNodeStatus(n.id)
                                .then((st) => { statuses[n.id] = st; })
                                .catch(() => {
                                    statuses[n.id] = { id: n.id, name: n.name, host: n.host || '', online: false, error: '采集失败' };
                                }),
                            adminService.getTrainingNodeDetail(n.id)
                                .then((d) => {
                                    if (d?.node) configs[n.id] = d.node;
                                })
                                .catch(() => { /* 详情读取失败不阻塞卡片 */ }),
                        ]);
                    }),
                );
            }
            setStatusMap(statuses);
            setConfigMap(configs);
        } finally {
            setRefreshing(false);
        }
    }, []);

    // 仅展示 AutoDL 远端节点（页面专注远端训练节点，隐藏本地 Docker 卡）
    const remoteNodes = nodes.filter((n) => n.type === 'remote');

    // 初次加载
    useEffect(() => {
        setLoading(true);
        fetchAll().finally(() => setLoading(false));
        return () => {
            if (timerRef.current) clearInterval(timerRef.current);
        };
    }, [fetchAll]);

    // 自动刷新
    useEffect(() => {
        if (timerRef.current) clearInterval(timerRef.current);
        if (autoRefresh) {
            timerRef.current = setInterval(() => void fetchAll(), 30000);
        }
        return () => {
            if (timerRef.current) clearInterval(timerRef.current);
        };
    }, [autoRefresh, fetchAll]);

    const renderGpuInfo = (st: NodeStatus) => {
        if (!st.gpus || st.gpus.length === 0) {
            return (
                <Text type="secondary" style={{ fontSize: 11 }}>
                    {st.gpu_error ? `GPU: ${st.gpu_error}` : 'GPU 不可用/未检测到'}
                </Text>
            );
        }
        return (
            <Space size="small" wrap>
                {st.gpus.map((g, i) => (
                    <Tag key={i} color={g.util > 80 ? 'red' : g.util > 30 ? 'orange' : 'green'}>
                        {g.name || 'GPU'}: {g.util}% · {fmtMB(g.mem_used_mb)}/{fmtMB(g.mem_total_mb)} · {g.temp_c}°C
                    </Tag>
                ))}
            </Space>
        );
    };

    const renderRemoteConfig = (cfg?: NodeConfigDetail, host?: string) => {
        const auth = cfg
            ? cfg.has_password
                ? '密码'
                : cfg.has_key
                    ? 'SSH Key'
                    : '未配置'
            : '—';
        return (
            <Descriptions
                size="small"
                column={1}
                bordered
                labelStyle={{ width: 76, fontSize: 11, color: '#8c8c8c' }}
                contentStyle={{ fontSize: 11 }}
                className="mb-2"
            >
                <Descriptions.Item label="主机">
                    <Text code style={{ fontSize: 11 }}>{cfg?.host || host || '—'}:{cfg?.port || 22}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="用户">
                    <Text style={{ fontSize: 11 }}>{cfg?.user || '—'}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="工作目录">
                    <Tooltip title={cfg?.work_dir}>
                        <Text style={{ fontSize: 11 }}>{cfg?.work_dir || '—'}</Text>
                    </Tooltip>
                </Descriptions.Item>
                <Descriptions.Item label="镜像">
                    <Tooltip title={cfg?.docker_image}>
                        <Text code style={{ fontSize: 11 }}>{cfg?.docker_image || '—'}</Text>
                    </Tooltip>
                </Descriptions.Item>
                <Descriptions.Item label="GPU">
                    <Text style={{ fontSize: 11 }}>{cfg?.gpus || 'all'}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="认证">
                    <Tag color={cfg?.has_password || cfg?.has_key ? 'green' : 'red'} style={{ fontSize: 10 }}>{auth}</Tag>
                </Descriptions.Item>
            </Descriptions>
        );
    };

    const renderRemoteStatus = (st?: NodeStatus) => {
        if (!st) {
            return <Text type="secondary" style={{ fontSize: 11 }}>状态加载中...</Text>;
        }
        if (!st.online) {
            return (
                <Space direction="vertical" size={2}>
                    <Text type="danger" style={{ fontSize: 12 }}><CloseCircleFilled /> 节点不可达</Text>
                    {st.error && <Text type="secondary" style={{ fontSize: 11 }}>{st.error}</Text>}
                </Space>
            );
        }
        return (
            <Space direction="vertical" size="small" style={{ width: '100%' }}>
                <Space wrap>
                    <Text style={{ fontSize: 11 }}>💻 CPU: {st.cpu_cores ?? '—'}核 · 负载 {st.cpu_load ?? '—'}</Text>
                    <Text style={{ fontSize: 11 }}>🧠 内存: {fmtMB(st.mem_used_mb)}/{fmtMB(st.mem_total_mb)}</Text>
                    {st.ping_ms != null && <Text style={{ fontSize: 11 }}>📡 {st.ping_ms}ms</Text>}
                </Space>
                <Space wrap>
                    <Text style={{ fontSize: 11 }}>💾 硬盘: {fmtKB(st.disk_used_kb)}/{fmtKB(st.disk_total_kb)}</Text>
                    <Text style={{ fontSize: 11 }}>📥 下行: {fmtBytes(st.net_rx_bytes)}</Text>
                    <Text style={{ fontSize: 11 }}>📤 上行: {fmtBytes(st.net_tx_bytes)}</Text>
                </Space>
                <div>{renderGpuInfo(st)}</div>
                {st.training_active && (
                    <div>
                        <Space>
                            <ThunderboltFilled style={{ color: '#fa8c16' }} />
                            <Text strong style={{ fontSize: 12 }}>训练中</Text>
                        </Space>
                        <Progress percent={50} size="small" status="active" />
                        {(st.containers || []).map((c, i) => (
                            <Text key={i} type="secondary" style={{ fontSize: 11, display: 'block' }}>
                                {c.name}: {c.status}
                            </Text>
                        ))}
                    </div>
                )}
            </Space>
        );
    };

    return (
        <div className="p-4 space-y-4">
            <div className="flex items-center justify-between">
                <div>
                    <Title level={4} style={{ margin: 0 }}>AutoDL 训练节点</Title>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                        监控各 AutoDL GPU 服务器状态与训练进度
                    </Text>
                </div>
                <Space>
                    <Button
                        size="small"
                        onClick={() => setAutoRefresh(!autoRefresh)}
                        icon={<SyncOutlined spin={autoRefresh} />}
                    >
                        {autoRefresh ? '自动刷新 30s' : '自动刷新关'}
                    </Button>
                    <Button size="small" type="primary" icon={<ReloadOutlined />} onClick={() => void fetchAll()} loading={refreshing}>
                        刷新状态
                    </Button>
                </Space>
            </div>

            {loading ? (
                <div className="text-center py-10"><Spin /></div>
            ) : remoteNodes.length === 0 ? (
                <Empty description="未配置 AutoDL 节点（在 config/training_nodes.yaml 中添加）" />
            ) : (
                <Row gutter={[16, 16]}>
                    {remoteNodes.map((n) => {
                        const st = statusMap[n.id];
                        const cfg = configMap[n.id];
                        const online = !!st?.online;
                        return (
                            <Col span={12} style={{ display: 'flex' }} key={n.id}>
                                <Card
                                    size="small"
                                    style={{ width: '100%', display: 'flex', flexDirection: 'column' }}
                                    title={
                                        <Space>
                                            <RocketFilled style={{ color: '#eb2f96' }} />
                                            <Text strong>{n.name}</Text>
                                            {st?.training_active && <Tag color="processing">训练中</Tag>}
                                        </Space>
                                    }
                                    extra={
                                        online
                                            ? <Tag color="green"><CheckCircleFilled /> 在线</Tag>
                                            : <Tag color="red"><CloseCircleFilled /> 离线</Tag>
                                    }
                                >
                                    <Text type="secondary" style={{ fontSize: 11, marginBottom: 6 }}>
                                        <ClusterOutlined /> AutoDL 远程 GPU 训练节点
                                    </Text>
                                    {/* 节点配置 */}
                                    {renderRemoteConfig(cfg, n.host)}
                                    {/* 在线状态 */}
                                    {renderRemoteStatus(st)}
                                </Card>
                            </Col>
                        );
                    })}
                </Row>
            )}
        </div>
    );
};

export default AdminAutoDLNodes;