import React, { useCallback, useEffect, useState } from 'react';
import { Button, Card, Table, Tag, Typography, Spin, Empty, Collapse, Progress, Modal, message } from 'antd';
import { Database, Calendar, RefreshCw, Zap, CheckCircle2, AlertTriangle, Clock } from 'lucide-react';
import { modelTrainingService } from '../services/modelTrainingService';

const { Text } = Typography;

export const InferenceCoveragePanel: React.FC<{ modelId: string }> = ({ modelId }) => {
  const [loading, setLoading] = useState(true);
  const [coverage, setCoverage] = useState<any>(null);
  const [backfilling, setBackfilling] = useState(false);
  const [task, setTask] = useState<any>(null);

  const load = useCallback(async () => {
    if (!modelId) return;
    setLoading(true);
    try {
      const data = await modelTrainingService.getInferenceCoverage(modelId);
      setCoverage(data);
    } catch (e: any) {
      // 后端未部署时友好空态
      setCoverage({ min_date: null, max_date: null, count: 0, gap_dates: [], latest_trade_date: null, is_up_to_date: false, reason: e?.message });
    } finally {
      setLoading(false);
    }
  }, [modelId]);

  useEffect(() => { void load(); }, [load]);

  const gapCount = coverage?.gap_dates?.length ?? 0;
  const isUpToDate = coverage?.is_up_to_date ?? (gapCount === 0 && coverage?.max_date);

  const handleBackfill = () => {
    Modal.confirm({
      title: '一键补全推理历史',
      content: `将从 ${coverage?.max_date ?? '—'} 的下一交易日起补至 ${coverage?.latest_trade_date ?? '最新交易日'}，共 ${gapCount} 个交易日，逐日推理并追加到 pred.parquet。是否继续？`,
      okText: '开始补全',
      cancelText: '取消',
      onOk: async () => {
        setBackfilling(true);
        setTask({ status: 'running', progress: 0, logs: '' });
        try {
          const res = await modelTrainingService.triggerInferenceBackfill(modelId);
          const taskId = res?.task_id || res?.taskId;
          if (!taskId) {
            message.success('已触发补全');
            setBackfilling(false);
            void load();
            return;
          }
          // 轮询
          const poll = async () => {
            for (let i = 0; i < 120; i++) {
              await new Promise((r) => setTimeout(r, 2000));
              try {
                const st = await modelTrainingService.getBackfillStatus(modelId, taskId);
                setTask(st);
                if (st?.status === 'completed' || st?.status === 'failed') {
                  if (st?.status === 'completed') message.success(`补全完成，追加 ${st?.appended ?? gapCount} 日`);
                  else message.error(st?.error || '补全失败');
                  setBackfilling(false);
                  void load();
                  return;
                }
              } catch {
                // 忽略轮询错误
              }
            }
            setBackfilling(false);
          };
          void poll();
        } catch (e: any) {
          message.error(e?.message ?? '触发失败');
          setBackfilling(false);
        }
      },
    });
  };

  if (loading) return <div className="flex justify-center py-12"><Spin /></div>;

  return (
    <div className="space-y-4">
      <Card size="small" className="rounded-2xl">
        <Collapse
          ghost
          items={[{
            key: 'explain',
            label: <span className="text-xs font-black text-slate-700 flex items-center gap-1.5"><Database size={12} className="text-blue-500"/>口径说明</span>,
            children: <div className="text-xs text-slate-600 leading-relaxed space-y-1">
              <div>覆盖基于 `pred.parquet`（`storage_path/pred.parquet`）的 `trade_date` 去重，与个股终端历史推理同源；`engine_signal_scores` 为 `completed` 落库。</div>
              <div>补全按交易日逐日跑 `inference.py` 并追加到 `pred.parquet`，目标至 `latest_trade_date`（`SSE` 日历）。</div>
            </div>,
          }]}
        />
      </Card>

      <div className="grid grid-cols-3 gap-3">
        <Card size="small" className="rounded-2xl text-center">
          <div className="text-[11px] text-slate-400 font-bold">覆盖区间</div>
          <div className="text-xs font-mono font-black text-slate-800 mt-1">{coverage?.min_date ?? '—'} ~ {coverage?.max_date ?? '—'}</div>
          <div className="text-[11px] text-slate-400">共 {coverage?.count ?? 0} 个交易日</div>
        </Card>
        <Card size="small" className="rounded-2xl text-center">
          <div className="text-[11px] text-slate-400 font-bold">最新交易日</div>
          <div className="text-lg font-mono font-black text-slate-800">{coverage?.latest_trade_date ?? '—'}</div>
          <div className="text-[11px] mt-1 flex items-center justify-center gap-1">
            {isUpToDate ? <><CheckCircle2 size={12} className="text-emerald-500"/> <span className="text-emerald-600 font-bold">已是最新</span></> : <><AlertTriangle size={12} className="text-amber-500"/> <span className="text-amber-600 font-bold">缺口 {gapCount} 日</span></>}
          </div>
        </Card>
        <Card size="small" className="rounded-2xl text-center">
          <div className="text-[11px] text-slate-400 font-bold">操作</div>
          <div className="mt-2 flex flex-col gap-1.5 items-center">
            <Button type="primary" icon={<Zap size={14} />} onClick={handleBackfill} loading={backfilling} disabled={isUpToDate || backfilling} className="rounded-xl font-bold">
              一键补全至最新
            </Button>
            <Button icon={<RefreshCw size={12} />} onClick={() => void load()} size="small" className="rounded-full">刷新</Button>
          </div>
        </Card>
      </div>

      {backfilling && task && (
        <Card size="small" className="rounded-2xl">
          <div className="flex items-center gap-2 mb-2"><Clock size={12} className="text-blue-500"/><Text className="text-xs font-bold">补全进度</Text></div>
          <Progress percent={task.progress ?? 0} size="small" />
          {task.logs && <pre className="mt-2 p-2 bg-slate-900 text-slate-100 rounded-xl text-[10px] max-h-32 overflow-auto">{String(task.logs).slice(-2000)}</pre>}
        </Card>
      )}

      <Card size="small" className="rounded-2xl">
        <div className="flex items-center justify-between mb-2">
          <Text className="text-xs font-black text-slate-700 flex items-center gap-1.5"><Calendar size={12} className="text-slate-400"/>覆盖明细（近 90 日缺口）</Text>
          <Tag className="rounded-full">{gapCount} 个缺口日</Tag>
        </div>
        {coverage?.gap_dates?.length ? (
          <Table
            size="small"
            rowKey={(r: any) => String(r)}
            dataSource={coverage.gap_dates.map((d: string) => ({ date: d }))}
            pagination={{ pageSize: 20 }}
            columns={[
              { title: '缺口日期', dataIndex: 'date', key: 'date', render: (v: string) => <span className="font-mono text-xs">{v}</span> },
              { title: '状态', key: 'status', render: () => <Tag color="gold">待补</Tag> },
            ]}
          />
        ) : (
          <Empty description={isUpToDate ? '已覆盖至最新交易日' : '暂无缺口，或后端未部署覆盖接口'} image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </Card>
    </div>
  );
};

export default InferenceCoveragePanel;
