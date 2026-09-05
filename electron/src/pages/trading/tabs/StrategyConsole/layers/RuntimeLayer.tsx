import React from 'react';
import { Skeleton } from 'antd';
import type { Order, RealTradingStatus } from '../../../../../services/realTradingService';
import type { LatestInferenceRunInfo } from '../../../../../services/modelTrainingService';
import { RUN_STATE_META } from '../topologyTypes';
import type { RunState } from '../topologyTypes';

interface RuntimeLayerProps {
    runState: RunState;
    status: RealTradingStatus | null;
    loading: boolean;
    latestRun: LatestInferenceRunInfo | null;
    defaultModelName: string;
    recentOrders: Order[];
    ordersLoading: boolean;
    onOpenHistory?: () => void;
}

const ParamCell: React.FC<{ label: string; value: string; title?: string }> = ({ label, value, title }) => (
    <div className="rounded-xl bg-slate-50/70 p-2.5 border border-slate-100/50 min-w-0">
        <div className="text-[10px] font-black text-slate-400 uppercase mb-0.5">{label}</div>
        <div className="font-bold text-slate-700 text-xs truncate" title={title || value}>{value}</div>
    </div>
);

const orderStatusLabel = (value?: string | null): string => {
    const s = String(value || '').toLowerCase();
    if (s === 'filled') return '已成';
    if (s === 'partial_filled' || s === 'partially_filled') return '部成';
    if (s === 'cancelled' || s === 'canceled') return '已撤';
    if (s === 'rejected') return '已拒绝';
    if (s === 'submitted' || s === 'pending' || s === 'new') return '待成交';
    return value || '-';
};

const formatOrderTime = (value?: string | null): string => {
    if (!value) return '-';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value).slice(11, 16) || '-';
    return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

/**
 * L2 运行层：左列全部指标（运行策略 + 策略参数），右列交易记录（最近 10 条委托）。
 * 未启动时左列显示空状态引导，右列保持可看（历史委托不受运行状态影响）。
 */
const RuntimeLayer: React.FC<RuntimeLayerProps> = ({
    runState,
    status,
    loading,
    latestRun,
    defaultModelName,
    recentOrders,
    ordersLoading,
    onOpenHistory,
}) => {
    const meta = RUN_STATE_META[runState];
    const live = status?.live_trade_config;
    const exec = status?.execution_config;

    const scheduleText = live?.schedule_type === 'weekly'
        ? (live.trade_weekdays && live.trade_weekdays.length > 0 ? `每周 ${live.trade_weekdays.join(' / ')}` : '每周执行')
        : (live?.rebalance_days ? `每 ${live.rebalance_days} 个交易日` : '-');
    const timeText = live?.sell_time && live?.buy_time ? `${live.sell_time} / ${live.buy_time}` : '-';
    const orderText = live?.order_type
        ? `${live.order_type === 'MARKET' ? '市价' : '限价'}${typeof live.max_price_deviation === 'number' ? ` / 偏离 ${(live.max_price_deviation * 100).toFixed(1)}%` : ''}`
        : '-';
    const strategyName = status?.strategy?.name || status?.strategy?.id || '-';
    const progress = Number(status?.latest_hosted_task?.progress ?? NaN);
    const showIdleGuide = (runState === 'idle' || runState === 'stopped') && !status?.strategy;

    return (
        <section className="bg-white rounded-2xl border border-slate-200/80 shadow-xs p-4">
            <div className="flex items-center gap-2 mb-3">
                <span className="text-[10px] font-black px-1.5 py-0.5 rounded bg-blue-50 text-blue-500 tracking-widest">RUNTIME</span>
                <h3 className="font-bold text-slate-800 text-sm">运行状态</h3>
            </div>

            {/* 状态机横幅 */}
            <div className={`rounded-xl border px-4 py-2.5 mb-3 flex items-center gap-2.5 ${meta.banner}`}>
                <span className={`w-2.5 h-2.5 rounded-full ${meta.dot}`} />
                <span className="text-sm font-black">{loading && !status ? '加载中…' : meta.label}</span>
                {runState === 'observing' && (
                    <span className="text-xs font-medium">当前无可交易信号，只跑观察链路，不自动下单</span>
                )}
                {status?.mode && (
                    <span className="ml-auto text-[11px] font-bold opacity-70">
                        {status.mode === 'SIMULATION' ? '模拟运行' : status.mode === 'SHADOW' ? '影子运行' : '实盘运行'}
                        {status.orchestration_mode ? ` · ${status.orchestration_mode}` : ''}
                    </span>
                )}
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-5 gap-3">
                {/* 左列：全部指标 */}
                <div className="lg:col-span-3 flex flex-col gap-3">
                    {loading && !status ? (
                        <Skeleton active paragraph={{ rows: 4 }} />
                    ) : showIdleGuide ? (
                        <div className="border border-dashed border-slate-200 rounded-xl py-10 text-center text-xs text-slate-400">
                            尚未启动策略运行时
                            <div className="mt-1 text-[11px] text-slate-300">在顶部选择已验证策略并启动，运行状态与参数将显示在这里</div>
                        </div>
                    ) : (
                        <>
                            <div className="rounded-xl border border-slate-100 p-3 text-center">
                                <div className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-2">运行策略</div>
                                <div className="text-sm font-black text-slate-800 truncate" title={strategyName}>{strategyName}</div>
                                <div className="mt-2 grid grid-cols-2 gap-2">
                                    <ParamCell label="默认模型" value={defaultModelName} title={defaultModelName} />
                                    <ParamCell label="生产批次交易日" value={latestRun?.prediction_trade_date || '-'} />
                                </div>
                                {Number.isFinite(progress) && (
                                    <div className="mt-2.5">
                                        <div className="flex justify-between text-[10px] font-black text-slate-400 uppercase mb-1">
                                            <span>任务进度</span><span>{progress}%</span>
                                        </div>
                                        <div className="h-1.5 rounded-full bg-slate-100 overflow-hidden">
                                            <div
                                                className="h-full rounded-full bg-gradient-to-r from-blue-500 via-cyan-400 to-emerald-400 transition-all"
                                                style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
                                            />
                                        </div>
                                    </div>
                                )}
                            </div>
                            <div className="rounded-xl border border-slate-100 p-3 text-center">
                                <div className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-2">策略参数</div>
                                <div className="grid grid-cols-2 gap-2">
                                    <ParamCell label="调仓周期" value={scheduleText} title={scheduleText} />
                                    <ParamCell label="买卖时点" value={timeText} />
                                    <ParamCell label="委托方式" value={orderText} title={orderText} />
                                    <ParamCell
                                        label="单轮最大委托"
                                        value={typeof live?.max_orders_per_cycle === 'number' ? `${live.max_orders_per_cycle} 单/轮` : '-'}
                                    />
                                </div>
                                {exec && (
                                    <div className="mt-2 rounded-xl border border-indigo-100 bg-indigo-50/30 px-2.5 py-2 text-[11px] font-bold text-indigo-700">
                                        大跌拦截 {typeof exec.max_buy_drop === 'number' ? `${(exec.max_buy_drop * 100).toFixed(1)}%` : 'N/A'}
                                        <span className="mx-2 text-indigo-200">|</span>
                                        止损 {typeof exec.stop_loss === 'number' ? `${(exec.stop_loss * 100).toFixed(1)}%` : 'N/A'}
                                    </div>
                                )}
                            </div>
                        </>
                    )}
                </div>

                {/* 右列：交易记录 */}
                <div className="lg:col-span-2 rounded-xl border border-slate-100 p-3 flex flex-col min-h-[220px]">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">交易记录 · 最近 10 条</span>
                        {onOpenHistory && (
                            <button
                                type="button"
                                onClick={onOpenHistory}
                                className="text-[11px] font-bold text-blue-600 hover:text-blue-700"
                            >
                                查看全部 →
                            </button>
                        )}
                    </div>
                    {ordersLoading ? (
                        <Skeleton active paragraph={{ rows: 4 }} />
                    ) : recentOrders.length === 0 ? (
                        <div className="flex-1 flex items-center justify-center border border-dashed border-slate-100 rounded-xl text-xs text-slate-400 py-10">
                            暂无委托记录
                        </div>
                    ) : (
                        <div className="flex-1 overflow-y-auto custom-scrollbar divide-y divide-slate-100 max-h-80">
                            {recentOrders.map((order) => {
                                const isBuy = String(order.side || '').toLowerCase() === 'buy';
                                const price = order.average_price ?? order.price;
                                return (
                                    <div key={order.order_id || order.id} className="flex items-center gap-2 py-1.5">
                                        <span className={`shrink-0 w-5 h-5 rounded-md text-[11px] font-black flex items-center justify-center ${isBuy ? 'bg-red-50 text-red-600' : 'bg-emerald-50 text-emerald-600'}`}>
                                            {isBuy ? '买' : '卖'}
                                        </span>
                                        <div className="min-w-0 flex-1">
                                            <div className="text-xs font-bold text-slate-700 truncate" title={`${order.symbol} ${order.symbol_name || ''}`}>
                                                {order.symbol_name || order.symbol}
                                                <span className="ml-1 font-mono font-medium text-slate-400">{order.symbol}</span>
                                            </div>
                                            <div className="text-[11px] text-slate-400 font-mono">
                                                {order.filled_quantity ?? order.quantity} 股
                                                {typeof price === 'number' ? ` @ ${price.toFixed(2)}` : ''}
                                            </div>
                                        </div>
                                        <div className="shrink-0 text-right">
                                            <div className="text-[11px] font-bold text-slate-500">{orderStatusLabel(order.status)}</div>
                                            <div className="text-[10px] text-slate-400 font-mono">{formatOrderTime(order.created_at)}</div>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </div>
            </div>
        </section>
    );
};

export default RuntimeLayer;
