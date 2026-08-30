import React, { useEffect, useState, useCallback } from 'react';
import {
  Button, Tag, Typography, Empty, Spin, Table,
} from 'antd';
import { clsx } from 'clsx';
import { Trash2 } from 'lucide-react';
import { modelTrainingService, InferenceRunRecord, InferenceRankingResult } from '../../services/modelTrainingService';
import { InferenceRunDetailView } from './InferenceRunDetailView';

const { Text } = Typography;

interface Props {
  modelId: string;
  onDelete: (runId: string) => Promise<void> | void;
}

const STATUS_META: Record<string, { color: string; label: string }> = {
  completed: { color: 'green', label: '成功' },
  running: { color: 'processing', label: '运行中' },
  failed: { color: 'error', label: '失败' },
};

/** 市场分数展示：低于空仓线红、达标绿、中间灰 */
const MarketScore: React.FC<{ value: number | null | undefined; threshold?: number; emptyBelow?: number }> = ({ value, threshold, emptyBelow }) => {
  if (value === null || value === undefined) return <Text className="text-xs text-slate-300">—</Text>;
  const v = Number(value);
  let cls = 'text-slate-500';
  if (emptyBelow !== undefined && v < emptyBelow) cls = 'text-rose-600';
  else if (threshold !== undefined && v >= threshold) cls = 'text-emerald-600';
  return <Text className={`text-xs font-mono font-bold ${cls}`}>{v.toFixed(4)}</Text>;
};

export const InferenceHistoryPanel: React.FC<Props> = ({ modelId, onDelete }) => {
  const [items, setItems] = useState<InferenceRunRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(false);
  // 详情视图状态：null = 列表，非 null = 正在查看该 run 的详情
  const [viewingRunId, setViewingRunId] = useState<string | null>(null);
  const [detailResult, setDetailResult] = useState<InferenceRankingResult | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await modelTrainingService.listInferenceHistory(modelId, {
        page,
        pageSize,
      });
      setItems(resp.items);
      setTotal(resp.total);
    } catch {
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [modelId, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadDetail = useCallback(async (runId: string) => {
    setViewingRunId(runId);
    setDetailResult(null);
    setDetailLoading(true);
    try {
      const r = await modelTrainingService.getInferenceResult(runId);
      setDetailResult(r);
    } catch {
      setDetailResult(null);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  // 按推理日期导航：用该日期的 run 加载详情（±1 天切换 / 指定日期）
  const navigateToDate = useCallback(async (inferenceDate: string) => {
    if (viewingRunId === null) return;
    setDetailLoading(true);
    try {
      const resp = await modelTrainingService.listInferenceHistory(modelId, {
        inferenceDate,
        page: 1,
        pageSize: 1,
      });
      const target = resp.items?.[0];
      if (target?.run_id) {
        setViewingRunId(target.run_id);
        const r = await modelTrainingService.getInferenceResult(target.run_id);
        setDetailResult(r);
      } else {
        setDetailResult(null);
      }
    } catch {
      setDetailResult(null);
    } finally {
      setDetailLoading(false);
    }
  }, [modelId, viewingRunId]);

  const handleBackToList = useCallback(() => {
    setViewingRunId(null);
    setDetailResult(null);
  }, []);

  const handleDelete = async (run: InferenceRunRecord) => {
    await onDelete(run.run_id);
    void load();
  };

  const columns = [
    {
      title: '推理日期',
      dataIndex: 'inference_date',
      width: 105,
      defaultSortOrder: 'descend' as const,
      sorter: (a: InferenceRunRecord, b: InferenceRunRecord) => (a.inference_date || '').localeCompare(b.inference_date || ''),
      render: (v: string, r: InferenceRunRecord) => (
        <div>
          <Text className="text-xs font-mono font-bold text-slate-700 block">{v || r.data_trade_date || '—'}</Text>
          {r.calendar_adjusted && r.requested_inference_date && r.requested_inference_date !== v && (
            <Text className="text-[11px] text-amber-500 block">原请求 {r.requested_inference_date}</Text>
          )}
        </div>
      ),
    },
    {
      title: '目标日',
      dataIndex: 'target_date',
      width: 100,
      render: (v: string, r: InferenceRunRecord) => (
        <Text className="text-xs font-mono text-slate-500">{v || r.prediction_trade_date || '—'}</Text>
      ),
    },
    {
      title: '信号数',
      dataIndex: 'signals_count',
      width: 70,
      sorter: (a: InferenceRunRecord, b: InferenceRunRecord) => a.signals_count - b.signals_count,
      render: (v: number) => <Text className="text-xs font-mono font-bold text-slate-700">{v || '—'}</Text>,
    },
    {
      title: '板块avg',
      dataIndex: 'board_top1_avg',
      width: 80,
      render: (v: number | null) => <MarketScore value={v} />,
    },
    {
      title: '行业avg',
      dataIndex: 'industry_avg_top1',
      width: 80,
      render: (v: number | null, r: InferenceRunRecord) => (
        <MarketScore value={v} threshold={0.09} emptyBelow={0.06} />
      ),
    },
    {
      title: '强行业数',
      dataIndex: 'strong_industry_count',
      width: 84,
      render: (v: number, r: InferenceRunRecord) => {
        if (v === null || v === undefined) return <Text className="text-xs text-slate-300">—</Text>;
        const cls = v >= 3 ? 'text-emerald-600' : v >= 2 ? 'text-amber-600' : 'text-slate-500';
        return <Text className={`text-xs font-mono font-bold ${cls}`}>{v}</Text>;
      },
    },
    {
      title: '覆盖行业',
      dataIndex: 'industry_top1_count',
      width: 74,
      render: (v: number) => <Text className="text-xs font-mono text-slate-500">{v || '—'}</Text>,
    },
    {
      title: '信号',
      dataIndex: 'market_signal',
      width: 90,
      render: (v: any) => {
        const label = v?.label;
        if (!label) return <Text className="text-[11px] text-slate-300">—</Text>;
        const colorMap: Record<string, string> = {
          '可入场': 'text-emerald-600 bg-emerald-50 border-emerald-200',
          '谨慎': 'text-amber-600 bg-amber-50 border-amber-200',
          '空仓观望': 'text-rose-600 bg-rose-50 border-rose-200',
        };
        const cls = colorMap[label] || 'text-slate-500 bg-slate-100 border-slate-200';
        return <span className={clsx('inline-block rounded-lg border px-2 py-0.5 text-[11px] font-black', cls)}>{label}</span>;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 70,
      render: (v: string) => {
        const meta = STATUS_META[v] || { color: 'default', label: v };
        return <Tag color={meta.color} className="m-0 border-0 text-[11px] font-black px-2 rounded-md">{meta.label}</Tag>;
      },
    },
    {
      title: '操作',
      width: 60,
      render: (_: unknown, r: InferenceRunRecord) => (
        <div className="flex items-center gap-1">
          <Button size="small" type="text" danger icon={<Trash2 size={13} />} className="rounded-lg p-0 h-6 w-6 flex items-center justify-center opacity-60 hover:opacity-100"
            onClick={(e) => { e.stopPropagation(); void handleDelete(r); }} />
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      {/* 表格 / 详情 */}
      {viewingRunId ? (
        <InferenceRunDetailView
          runId={viewingRunId}
          result={detailResult}
          loading={detailLoading}
          onBack={handleBackToList}
          onRetry={() => void loadDetail(viewingRunId)}
          onNavigateDate={(date) => void navigateToDate(date)}
        />
      ) : (
        <div className="glass-panel rounded-3xl p-5 border border-slate-100/50">
          <Spin spinning={loading}>
            {items.length === 0 && !loading ? (
              <div className="flex justify-center py-10">
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span className="text-xs text-slate-400 font-medium">暂无推理历史记录</span>} />
              </div>
            ) : (
              <Table
                dataSource={items}
                columns={columns}
                rowKey="run_id"
                size="small"
                onRow={(record) => ({
                  onClick: () => void loadDetail(record.run_id),
                  className: 'cursor-pointer',
                })}
                pagination={{
                  current: page,
                  pageSize,
                  total,
                  showSizeChanger: true,
                  pageSizeOptions: ['10', '20', '50', '100'],
                  onChange: (p, ps) => { setPage(p); setPageSize(ps); },
                  className: 'text-xs',
                }}
                rowClassName={(_, idx) => clsx(idx % 2 === 0 ? 'bg-white' : 'bg-slate-50/30')}
              />
            )}
          </Spin>
        </div>
      )}
    </div>
  );
};
