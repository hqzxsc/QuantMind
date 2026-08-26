import React from 'react';
import { Card, Divider, Select, Button, InputNumber, Alert, DatePicker, Tag, Typography, Tooltip, Switch } from 'antd';
import { Target, ArrowRightLeft, Info, CalendarRange, Activity, RefreshCcw } from 'lucide-react';
import { clsx } from 'clsx';
import { Dayjs } from 'dayjs';
import dayjs from 'dayjs';
import {
  TrainingTarget,
  TimePeriodMap,
  TargetMode,
  SplitKey,
  TARGET_PRESETS,
  buildLabelFormula,
  buildEffectiveTradeDate,
  daysBetween,
  formatRange,
  WfaConfig,
  TrainingFactorFilterConfig,
  DEFAULT_FACTOR_FILTER,
} from './trainingUtils';
import { AdminModelFeatureDataCoverage } from '../../features/admin/types';

const { RangePicker } = DatePicker;

interface TrainingNodeInfo {
  id: string;
  name: string;
  type?: string;
  online?: boolean;
  readiness?: string;
  readiness_label?: string;
  status_desc?: string;
  gpu_summary?: string;
  error?: string;
}

interface TrainingTargetConfigProps {
  target: TrainingTarget;
  timePeriods: TimePeriodMap;
  onTargetChange: (target: TrainingTarget) => void;
  onTimeChange: (key: SplitKey, values: [Dayjs, Dayjs]) => void;
  dataCoverage?: AdminModelFeatureDataCoverage | null;
  wfa?: WfaConfig;
  onWfaChange?: (wfa: WfaConfig) => void;
  // 训练执行配置（训练节点 + 最长训练时长）
  trainingNodes?: TrainingNodeInfo[];
  selectedNode?: string;
  onNodeChange?: (id: string) => void;
  nodesLoading?: boolean;
  onRefreshNodes?: () => void;
  maxTimeMinutes?: number;
  onMaxTimeChange?: (value: number) => void;
  // 因子筛选开关与阈值（默认开启，阈值对齐后端 ic_icir 默认）
  factorFilter?: TrainingFactorFilterConfig;
  onFactorFilterChange?: (filter: TrainingFactorFilterConfig) => void;
}

const SectionHeader: React.FC<{ title: string; desc: string; icon?: React.ReactNode }> = ({ title, desc, icon }) => (
  <div className="flex items-start justify-between gap-4">
    <div>
      <div className="flex items-center gap-2">
        {icon}
        <Typography.Title level={4} className="!mb-0 !text-slate-900">
          {title}
        </Typography.Title>
      </div>
      <Typography.Paragraph className="!mb-0 !mt-2 !text-xs !text-slate-500 leading-relaxed">
        {desc}
      </Typography.Paragraph>
    </div>
  </div>
);

export const TrainingTargetConfig: React.FC<TrainingTargetConfigProps> = ({
  target,
  timePeriods,
  onTargetChange,
  onTimeChange,
  dataCoverage,
  wfa,
  onWfaChange,
  trainingNodes = [],
  selectedNode,
  onNodeChange,
  nodesLoading = false,
  onRefreshNodes,
  maxTimeMinutes,
  onMaxTimeChange,
  factorFilter,
  onFactorFilterChange,
}) => {
  const labelFormula = buildLabelFormula(target);
  const effectiveTradeDate = buildEffectiveTradeDate(target, timePeriods.test[0]);
  const selectedNodeObj = trainingNodes.find((n) => n.id === selectedNode) || trainingNodes[0];
  
  const trainDays = daysBetween(timePeriods.train);
  const valDays = daysBetween(timePeriods.val);
  const testDays = daysBetween(timePeriods.test);
  const totalDays = trainDays + valDays + testDays;
  
  const minDataDate = dataCoverage?.min_date ? dayjs(dataCoverage.min_date) : null;
  const maxDataDate = dataCoverage?.max_date ? dayjs(dataCoverage.max_date) : null;

  const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

  const handleRangeChange = (key: SplitKey, values: any) => {
    if (values && values[0] && values[1]) {
      onTimeChange(key, [values[0], values[1]]);
    }
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[1.05fr_0.95fr]">
      <Card className="rounded-3xl border-slate-200 shadow-sm" styles={{ body: { padding: 20 } }}>
        <SectionHeader
          title="第二步：训练目标 T+N"
          desc="把训练目标与标签 horizon 独立出来，不再复用回测周期字段。"
          icon={<Target size={18} className="text-indigo-500" />}
        />
        <Divider className="my-4" />
        <div className="space-y-4">
          <div>
            <div className="mb-2 text-sm font-semibold text-slate-800">目标类型</div>
            <Select
              value={target.mode}
              onChange={(value) => onTargetChange({ ...target, mode: value as TargetMode })}
              className="w-full"
              options={[
                { label: '回归目标（未来收益率）', value: 'return' },
                { label: '分类目标（涨跌方向）', value: 'classification' },
              ]}
            />
            <div className="mt-2 text-xs text-slate-500">
              {target.mode === 'classification'
                ? '适合做方向预测、事件识别等离散标签任务。'
                : '适合做未来收益率、rank score 等连续标签任务。'}
            </div>
          </div>

          <div>
            <div className="mb-2 flex items-center justify-between">
              <div className="text-sm font-semibold text-slate-800">T+N 参数</div>
              <div className="text-xs text-slate-500">允许 1~30 个交易日</div>
            </div>
            <div className="flex flex-wrap gap-2">
              {TARGET_PRESETS.map((preset) => (
                <Button
                  key={preset}
                  size="small"
                  type={target.horizonDays === preset ? 'primary' : 'default'}
                  className={clsx('h-8 rounded-xl font-bold px-3', target.horizonDays === preset && 'bg-indigo-600')}
                  onClick={() => onTargetChange({ ...target, horizonDays: preset })}
                >
                  T+{preset}
                </Button>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-3">
              <InputNumber
                min={1}
                max={30}
                value={target.horizonDays}
                onChange={(value) => onTargetChange({ ...target, horizonDays: clamp(Number(value ?? target.horizonDays), 1, 30) })}
                className="w-28"
              />
              <span className="text-sm text-slate-500">交易日</span>
            </div>
          </div>

          {/* ── 多周期训练 ── */}
          <div className="rounded-2xl border border-indigo-100 bg-gradient-to-r from-indigo-50/60 to-slate-50 p-4">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <Activity size={15} className="text-indigo-500" />
                <div className="text-sm font-semibold text-slate-800">多周期训练</div>
              </div>
              <Switch
                checked={(target.horizonDaysList?.length ?? 0) >= 2}
                onChange={(checked) => {
                  if (checked) {
                    onTargetChange({ ...target, horizonDays: target.horizonDays, horizonDaysList: [1, 3, 5, 10] });
                  } else {
                    const { horizonDaysList, ...rest } = target;
                    onTargetChange({ ...rest });
                  }
                }}
              />
            </div>
            <div className="mt-1.5 text-xs text-slate-500 leading-relaxed">
              一次训练产出 T+1/T+3/T+5/T+10 四个周期模型，并自动创建 ICIR 加权融合模型，利用跨周期一致性提升选股稳定性。
            </div>
            {(target.horizonDaysList?.length ?? 0) >= 2 && (
              <>
                <div className="mt-3 flex flex-wrap gap-2">
                  {[1, 3, 5, 10].map((h) => (
                    <Button
                      key={h}
                      size="small"
                      type={target.horizonDaysList?.includes(h) ? 'primary' : 'default'}
                      className={clsx('h-8 rounded-xl font-bold px-3', target.horizonDaysList?.includes(h) && 'bg-indigo-600')}
                      onClick={() => {
                        const cur = target.horizonDaysList ?? [];
                        const next = cur.includes(h) ? cur.filter((x) => x !== h) : [...cur, h].sort((a, b) => a - b);
                        onTargetChange({ ...target, horizonDays: next[0] ?? target.horizonDays, horizonDaysList: next });
                      }}
                    >
                      T+{h}
                    </Button>
                  ))}
                </div>
                <div className="mt-2 text-[11px] text-slate-400 font-mono">
                  将产出 {target.horizonDaysList?.length ?? 0} 个模型 + 1 个融合模型（训练耗时约 ×{target.horizonDaysList?.length ?? 4}）
                </div>
                {wfa?.enabled && onWfaChange && (
                  <Alert
                    className="mt-2 rounded-lg border-amber-100 bg-amber-50/60"
                    type="warning"
                    showIcon
                    message="多周期训练会禁用 WFA 诊断"
                    description="避免 4 周期 × 4 窗口 = 16 次训练导致超时，训练结束后可单独在模型详情查看 WFA。"
                  />
                )}
              </>
            )}
          </div>

          {/* ── 因子筛选（IC/ICIR）── */}
          <div className="rounded-2xl border border-slate-200 bg-slate-50/60 p-4">
            <div className="flex items-center justify-between gap-3">
              <Tooltip title="日频 Rank IC 初筛 → 相关性剪枝 → 稳定性检验。筛选理由与每特征 IC/ICIR/覆盖率在训练日志与结果页展示，不再黑盒。">
                <div className="text-[10px] uppercase font-bold text-slate-400 cursor-help">因子筛选（IC/ICIR）</div>
              </Tooltip>
              <Switch
                size="small"
                checked={factorFilter?.enabled ?? DEFAULT_FACTOR_FILTER.enabled}
                onChange={(v) => onFactorFilterChange?.({ ...DEFAULT_FACTOR_FILTER, ...factorFilter, enabled: v })}
              />
            </div>
            {factorFilter?.enabled ?? DEFAULT_FACTOR_FILTER.enabled ? (
              <>
                <div className="mt-3 flex flex-wrap items-end gap-3">
                  <div>
                    <div className="mb-1 text-[10px] text-slate-500">目标特征数 top-N</div>
                    <InputNumber
                      size="small" min={10} max={300}
                      value={factorFilter?.nTop ?? DEFAULT_FACTOR_FILTER.nTop}
                      onChange={(v) => onFactorFilterChange?.({ ...DEFAULT_FACTOR_FILTER, ...factorFilter, nTop: Number(v ?? DEFAULT_FACTOR_FILTER.nTop) })}
                      className="w-24"
                    />
                  </div>
                  <div>
                    <div className="mb-1 text-[10px] text-slate-500">|IC| ≥</div>
                    <InputNumber
                      size="small" min={0} max={0.1} step={0.005}
                      value={factorFilter?.icThreshold ?? DEFAULT_FACTOR_FILTER.icThreshold}
                      onChange={(v) => onFactorFilterChange?.({ ...DEFAULT_FACTOR_FILTER, ...factorFilter, icThreshold: Number(v ?? DEFAULT_FACTOR_FILTER.icThreshold) })}
                      className="w-24"
                    />
                  </div>
                  <div>
                    <div className="mb-1 text-[10px] text-slate-500">|ICIR| ≥</div>
                    <InputNumber
                      size="small" min={0} max={1} step={0.05}
                      value={factorFilter?.icirThreshold ?? DEFAULT_FACTOR_FILTER.icirThreshold}
                      onChange={(v) => onFactorFilterChange?.({ ...DEFAULT_FACTOR_FILTER, ...factorFilter, icirThreshold: Number(v ?? DEFAULT_FACTOR_FILTER.icirThreshold) })}
                      className="w-24"
                    />
                  </div>
                  <div>
                    <div className="mb-1 text-[10px] text-slate-500">相关性 &lt; </div>
                    <InputNumber
                      size="small" min={0.5} max={1} step={0.01}
                      value={factorFilter?.correlationThreshold ?? DEFAULT_FACTOR_FILTER.correlationThreshold}
                      onChange={(v) => onFactorFilterChange?.({ ...DEFAULT_FACTOR_FILTER, ...factorFilter, correlationThreshold: Number(v ?? DEFAULT_FACTOR_FILTER.correlationThreshold) })}
                      className="w-24"
                    />
                  </div>
                </div>
                <div className="mt-1.5 text-[10px] text-slate-400 leading-relaxed">
                  放松阈值入选更多特征，收紧则更保守。全选 321 个特征时，筛选后实际进入训练的特征数会小于全选数。
                </div>
              </>
            ) : (
              <div className="mt-2 text-[10px] text-amber-600 leading-relaxed">已关闭：全部选中特征直接进入训练，不做筛选。</div>
            )}
          </div>

          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
            <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">标签预览</div>
            <div className="mt-2 text-sm font-medium text-slate-800">{target.mode === 'classification' ? '预测未来 N 日涨跌方向' : `预测未来 ${target.horizonDays} 日收益率`}</div>
            <div className="mt-3 rounded-xl bg-white p-3 font-mono text-xs text-slate-700">{labelFormula}</div>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">生效日期预览</div>
                <div className="mt-1 text-sm font-semibold text-slate-900">{effectiveTradeDate}</div>
              </div>
              <div className="rounded-2xl bg-indigo-50 px-3 py-2 text-xs text-indigo-700">
                按交易日历校正时将由后端覆盖
              </div>
            </div>
          </div>

          <Alert
            type="info"
            showIcon
            message="设计说明"
            description="T+N 是训练标签 horizon，不是回测周期。训练请求、模型元数据和模型管理页都应使用同一字段口径。"
            className="rounded-2xl border-blue-100 bg-blue-50/70"
          />
        </div>
      </Card>

      <Card className="rounded-3xl border-slate-200 shadow-sm" styles={{ body: { padding: 20 } }}>
        <SectionHeader
          title="时空切分预览 (Time Split)"
          desc="设置样本的训练、验证与测试区间，各阶段日期自动防重叠。"
          icon={<ArrowRightLeft size={18} className="text-indigo-500" />}
        />
        <Divider className="my-4" />
        
        {dataCoverage && (
          <div className="mb-4 rounded-xl border border-slate-200 bg-gradient-to-r from-slate-50 to-indigo-50/30 p-3">
            <div className="flex items-center gap-2 text-xs">
              <CalendarRange size={14} className="text-indigo-500" />
              <span className="font-semibold text-slate-700">数据有效期</span>
              <Tag className="m-0 rounded-lg border-0 bg-white/80 text-slate-600 font-mono text-[11px]">
                {dataCoverage.min_date} ~ {dataCoverage.max_date}
              </Tag>
              <Tooltip title={`共 ${dataCoverage.total_rows?.toLocaleString() ?? 0} 条记录，${dataCoverage.file_count ?? 0} 个 parquet 文件`}>
                <Info size={12} className="text-slate-400 cursor-help" />
              </Tooltip>
            </div>
          </div>
        )}
        
        <div className="space-y-4">
          {([
            { key: 'train', label: '训练集 (Training)', color: 'indigo', desc: '用于拟合模型参数' },
            { key: 'val', label: '验证集 (Validation)', color: 'amber', desc: '用于早停逻辑与超参调优' },
            { key: 'test', label: '测试集 (Testing)', color: 'emerald', desc: '用于样本外(OOS)最终检验' },
          ] as const).map((item) => {
            const range = timePeriods[item.key];
            const days = daysBetween(range);
            const width = (days / totalDays) * 100;
            const colorMap: Record<string, string> = {
              indigo: 'bg-indigo-500',
              amber: 'bg-amber-400',
              emerald: 'bg-emerald-500',
            };
            const barBgMap: Record<string, string> = {
              indigo: 'bg-indigo-50/50',
              amber: 'bg-amber-50/50',
              emerald: 'bg-emerald-50/50',
            };

            return (
              <div key={item.key} className={clsx('rounded-2xl border p-4 transition-colors', barBgMap[item.color], 'border-slate-200 hover:border-indigo-300')}>
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-sm font-bold text-slate-800">{item.label}</div>
                    <div className="text-[11px] text-slate-500">{item.desc}</div>
                  </div>
                  <Tag className="m-0 rounded-lg border-0 bg-white shadow-sm text-slate-700 font-mono px-2">{days}d</Tag>
                </div>
                <RangePicker
                  value={range as [Dayjs, Dayjs]}
                  onChange={(values) => handleRangeChange(item.key, values)}
                  className="mt-3 w-full rounded-xl border-slate-200 shadow-sm"
                  allowClear={false}
                  placeholder={['开始日期', '结束日期']}
                  disabledDate={(current) => {
                    if (!current) return false;
                    if (minDataDate && current.isBefore(minDataDate, 'day')) return true;
                    if (maxDataDate && current.isAfter(maxDataDate, 'day')) return true;
                    if (item.key === 'train') {
                      return current.isAfter(timePeriods.val[0].subtract(1, 'day'));
                    }
                    if (item.key === 'val') {
                      return (
                        current.isBefore(timePeriods.train[1].add(1, 'day')) ||
                        current.isAfter(timePeriods.test[0].subtract(1, 'day'))
                      );
                    }
                    if (item.key === 'test') {
                      return current.isBefore(timePeriods.val[1].add(1, 'day'));
                    }
                    return false;
                  }}
                />
                <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/80">
                  <div className={clsx('h-full rounded-full transition-all duration-500', colorMap[item.color])} style={{ width: `${width}%` }} />
                </div>
                <div className="mt-2 flex items-center justify-between text-[10px] font-medium text-slate-400 uppercase tracking-tighter">
                  <span>{formatRange(range)}</span>
                  <span className="text-slate-500">{width.toFixed(1)}% 占比</span>
                </div>
              </div>
            );
          })}
        </div>

        {/* ── 训练执行配置：训练节点 + 最长训练时长 ── */}
        <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50/60 p-4">
          <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400 mb-3">训练执行配置</div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-xl bg-white p-3 border border-slate-200">
              <div className="flex items-center justify-between mb-2">
                <div className="text-[10px] uppercase font-bold text-slate-400">训练节点</div>
                <button
                  type="button"
                  onClick={onRefreshNodes}
                  disabled={nodesLoading}
                  className="text-[10px] text-slate-400 hover:text-blue-600 flex items-center gap-1 transition-colors disabled:opacity-50"
                  title="刷新节点就绪状态"
                >
                  <RefreshCcw className={clsx('w-2.5 h-2.5', nodesLoading && 'animate-spin')} />
                  <span>{nodesLoading ? '检测中' : '刷新'}</span>
                </button>
              </div>
              <Select
                size="small"
                className="w-full"
                value={selectedNode}
                loading={nodesLoading && trainingNodes.length === 0}
                onChange={onNodeChange}
                placeholder="选择训练节点"
                options={trainingNodes.map((node) => ({
                  value: node.id,
                  label: `${node.type === 'remote' ? '☁️' : '💻'} ${node.name} · ${node.readiness_label || (node.online ? '就绪' : '离线')}`,
                }))}
              />
              {selectedNodeObj && (
                <div className="mt-2 text-[10px] text-slate-500 truncate">
                  {selectedNodeObj.status_desc || selectedNodeObj.gpu_summary || (selectedNodeObj.type === 'remote' ? '远程 GPU 节点' : '本地 Docker 节点')}
                </div>
              )}
              {selectedNodeObj && ['offline', 'warning'].includes(selectedNodeObj.readiness) && (
                <details className="mt-2 text-[10px] text-amber-700">
                  <summary className="cursor-pointer select-none hover:text-amber-900">查看节点提示</summary>
                  <div className="mt-1.5 rounded bg-amber-50 p-2 leading-relaxed">
                    {selectedNodeObj.readiness === 'offline'
                      ? selectedNodeObj.error || '请先在云服务商控制台开机或检查连接配置。'
                      : <>本机训练镜像尚未就绪。<code className="mt-1 block select-all rounded bg-amber-100 px-1 py-0.5 font-mono text-[9px]">docker build -f docker/Dockerfile.trainer -t quantmind-trainer:latest .</code></>}
                  </div>
                </details>
              )}
            </div>
            <div className="rounded-xl bg-white p-3 border border-slate-200">
              <div className="text-[10px] uppercase font-bold text-slate-400 mb-2">最长训练时长</div>
              <Select
                size="small"
                value={maxTimeMinutes}
                onChange={onMaxTimeChange}
                style={{ width: '100%' }}
                options={[
                  { value: 60, label: '1 小时（快速验证）' },
                  { value: 120, label: '2 小时（默认）' },
                  { value: 360, label: '6 小时' },
                  { value: 720, label: '12 小时（DL 模型推荐）' },
                  { value: 1440, label: '24 小时（上限）' },
                ]}
              />
              <div className="mt-1.5 text-[10px] text-slate-400">超时后任务会自动停止。</div>
            </div>
          </div>
        </div>
      {/* ── WFA 稳定性诊断配置 ── */}
      {onWfaChange && (
        <Card className="mt-4 rounded-3xl border-slate-200 shadow-sm" styles={{ body: { padding: 20 } }}>
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2">
                <Activity size={18} className="text-violet-500" />
                <Typography.Title level={4} className="!mb-0 !text-slate-900">
                  Walk-Forward 稳定性诊断
                </Typography.Title>
              </div>
              <Typography.Paragraph className="!mb-0 !mt-2 !text-xs !text-slate-500 leading-relaxed">
                滚动窗口训练并输出每个窗口的 IC，用于评估模型在不同历史区间上的稳定性与参数漂移。诊断在正式训练前执行，不产生正式模型。
              </Typography.Paragraph>
            </div>
            <Switch
              checked={!!wfa?.enabled}
              onChange={(checked) => onWfaChange({ ...(wfa || { enabled: false, strategy: 'rolling', nWindows: 4, trainYears: 3, valMonths: 12, stepMonths: 12 }), enabled: checked })}
            />
          </div>

          {wfa?.enabled && (
            <>
              <Divider className="my-4" />
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
                <div>
                  <div className="mb-2 text-sm font-semibold text-slate-800">窗口策略</div>
                  <Select
                    value={wfa.strategy}
                    onChange={(v) => onWfaChange({ ...wfa, strategy: v })}
                    className="w-full"
                    options={[
                      { label: '滚动窗口（固定训练长度）', value: 'rolling' },
                      { label: '扩张窗口（数据累积）', value: 'expanding' },
                    ]}
                  />
                  <div className="mt-1 text-[11px] text-slate-500">
                    {wfa.strategy === 'rolling' ? '每窗训练长度固定，避免老数据影响' : '训练集从起点累积，贴近实盘迭代'}
                  </div>
                </div>
                <div>
                  <div className="mb-2 text-sm font-semibold text-slate-800">窗口数</div>
                  <InputNumber
                    min={1}
                    max={12}
                    value={wfa.nWindows}
                    onChange={(v) => onWfaChange({ ...wfa, nWindows: Number(v ?? 4) })}
                    className="w-full"
                  />
                  <div className="mt-1 text-[11px] text-slate-500">验证段数量（个）</div>
                </div>
                <div>
                  <div className="mb-2 text-sm font-semibold text-slate-800">训练长度</div>
                  <InputNumber
                    min={1}
                    max={8}
                    value={wfa.trainYears}
                    onChange={(v) => onWfaChange({ ...wfa, trainYears: Number(v ?? 3) })}
                    className="w-full"
                    disabled={wfa.strategy === 'expanding'}
                  />
                  <div className="mt-1 text-[11px] text-slate-500">每窗训练长度（年）</div>
                </div>
                <div>
                  <div className="mb-2 text-sm font-semibold text-slate-800">验证长度</div>
                  <InputNumber
                    min={1}
                    max={36}
                    value={wfa.valMonths}
                    onChange={(v) => onWfaChange({ ...wfa, valMonths: Number(v ?? 12) })}
                    className="w-full"
                  />
                  <div className="mt-1 text-[11px] text-slate-500">每窗验证长度（月）</div>
                </div>
                <div>
                  <div className="mb-2 text-sm font-semibold text-slate-800">步长</div>
                  <InputNumber
                    min={1}
                    max={36}
                    value={wfa.stepMonths}
                    onChange={(v) => onWfaChange({ ...wfa, stepMonths: Number(v ?? 12) })}
                    className="w-full"
                  />
                  <div className="mt-1 text-[11px] text-slate-500">窗口推进步长（月）</div>
                </div>
              </div>
              <Alert
                className="mt-4 rounded-xl border-violet-100 bg-violet-50/50"
                type="info"
                showIcon
                message="诊断说明"
                description="WFA 会额外运行多个窗口的训练，耗时约为基础训练的 2-3 倍。支持树模型（LightGBM/XGBoost/CatBoost）和线性模型，深度学习模型因耗时过长不参与诊断。"
              />
            </>
          )}
        </Card>
      )}
      </Card>
    </div>
  );
};
