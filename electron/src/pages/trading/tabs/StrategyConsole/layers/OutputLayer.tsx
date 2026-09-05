import React from 'react';
import { Skeleton } from 'antd';
import { Activity, TerminalSquare } from 'lucide-react';
import type { RealTradingStatus } from '../../../../../services/realTradingService';
import type { LatestInferenceRunInfo } from '../../../../../services/modelTrainingService';

interface OutputLayerProps {
    status: RealTradingStatus | null;
    latestRun: LatestInferenceRunInfo | null;
    loading: boolean;
    logsOpen: boolean;
    onToggleLogs: () => void;
    onOpenManualTask?: () => void;
}

const taskTone = (value?: string | null): string => {
    const s = String(value || '').toLowerCase();
    if (s === 'completed') return 'bg-emerald-50 text-emerald-700 border-emerald-200';
    if (['running', 'dispatching', 'validating', 'queued'].includes(s)) return 'bg-blue-50 text-blue-700 border-blue-200';
    if (s === 'failed' || s === 'cancelled') return 'bg-rose-50 text-rose-700 border-rose-200';
    return 'bg-slate-50 text-slate-600 border-slate-200';
};

const taskLabel = (value?: string | null): string => {
    const s = String(value || '').toLowerCase();
    if (s === 'completed') return '已完成';
    if (s === 'running') return '执行中';
    if (s === 'dispatching') return '派发中';
    if (s === 'validating') return '校验中';
    if (s === 'queued') return '排队中';
    if (s === 'failed') return '已失败';
    if (s === 'cancelled') return '已取消';
    return value || '-';
};

/**
 * L3 输出层：下个交易日计划 + 最新任务汇报。
 * 数据源自 status.latest_hosted_task（后端已聚合），无新增接口。
 */
const OutputLayer: React.FC<OutputLayerProps> = ({ status, latestRun, loading, logsOpen, onToggleLogs, onOpenManualTask }) => {
    const task = status?.latest_hosted_task || null;
    const result = (task?.result_json || {}) as Record<string, unknown>;
    const request = (task?.request_json || {}) as Record<string, unknown>;
    const preview = (result?.preview_summary || {}) as Record<string, unknown>;
    const execWindow = (request?.execution_window || {}) as Record<string, string | undefined>;
    const success = Number(task?.success_count ?? (result?.success_count as number) ?? 0);
    const failed = Number(task?.failed_count ?? (result?.failed_count as number) ?? 0);
    const skipped = Number(preview?.skipped_count ?? 0);
    const horizon = preview?.target_horizon_days as number | undefined;

    return (
        <section className="bg-white rounded-2xl border border-slate-200/80 shadow-xs p-4">
            <div className="flex items-center gap-2 mb-3">
                <span className="text-[10px] font-black px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-500 tracking-widest">OUTPUT</span>
                <h3 className="font-bold text-slate-800 text-sm">输出状态</h3>
            </div>
            {loading && !task && !latestRun ? (
                <Skeleton active paragraph={{ rows: 2 }} />
            ) : !task ? (
                <div className="flex items-center justify-center gap-2 border border-dashed border-slate-100 rounded-xl py-6 text-xs text-slate-400">
                    <Activity size={16} className="opacity-30" />
                    今日暂未触发自动化托管任务
                </div>
            ) : (
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                    <div className="rounded-xl border border-slate-100 p-3">
                        <div className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1.5">下个交易日计划</div>
                        <div className="space-y-1.5 text-xs font-bold text-slate-700">
                            <div className="flex justify-between gap-2">
                                <span className="text-slate-400 font-medium">目标跨度</span>
                                <span>{horizon ? `${horizon} 个交易日` : '-'}</span>
                            </div>
                            <div className="flex justify-between gap-2">
                                <span className="text-slate-400 font-medium">执行窗口</span>
                                <span className="truncate" title={`${execWindow?.start || '-'} ~ ${execWindow?.end || '-'}`}>
                                    {execWindow?.start || '-'} ~ {execWindow?.end || '-'}
                                </span>
                            </div>
                            <div className="flex justify-between gap-2">
                                <span className="text-slate-400 font-medium">信号批次</span>
                                <span className="font-mono truncate" title={task.run_id}>{task.prediction_trade_date || '-'}</span>
                            </div>
                        </div>
                    </div>
                    <div className="rounded-xl border border-slate-100 p-3">
                        <div className="flex items-center justify-between mb-1.5">
                            <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">任务汇报</span>
                            <span className={`px-2 py-0.5 rounded-full text-[10px] font-black border ${taskTone(task.status)}`}>
                                {taskLabel(task.status)}
                            </span>
                        </div>
                        <div className="grid grid-cols-3 gap-2 text-center">
                            <div className="rounded-lg bg-emerald-50 border border-emerald-100 py-1.5">
                                <div className="text-[9px] font-black text-emerald-600/70">成功</div>
                                <div className="text-sm font-black text-emerald-700">{success}</div>
                            </div>
                            <div className="rounded-lg bg-rose-50 border border-rose-100 py-1.5">
                                <div className="text-[9px] font-black text-rose-600/70">失败</div>
                                <div className="text-sm font-black text-rose-700">{failed}</div>
                            </div>
                            <div className="rounded-lg bg-slate-50 border border-slate-200 py-1.5">
                                <div className="text-[9px] font-black text-slate-400">跳过</div>
                                <div className="text-sm font-black text-slate-700">{skipped}</div>
                            </div>
                        </div>
                    </div>
                    <div className="rounded-xl border border-slate-100 p-3 flex flex-col justify-center gap-2">
                        <button
                            type="button"
                            onClick={onToggleLogs}
                            className="w-full py-2.5 rounded-xl bg-slate-50 text-slate-800 text-[11px] font-black hover:bg-slate-100 transition-all flex items-center justify-center gap-2 border border-slate-200"
                        >
                            <TerminalSquare size={14} />
                            {logsOpen ? '收起任务日志' : '查看任务日志'}
                        </button>
                        {onOpenManualTask && (
                            <button
                                type="button"
                                onClick={onOpenManualTask}
                                className="w-full py-2.5 rounded-xl bg-blue-50 text-blue-700 text-[11px] font-black hover:bg-blue-100 transition-all flex items-center justify-center gap-2 border border-blue-100"
                            >
                                <Activity size={14} /> 查看详情
                            </button>
                        )}
                    </div>
                </div>
            )}
        </section>
    );
};

export default OutputLayer;
