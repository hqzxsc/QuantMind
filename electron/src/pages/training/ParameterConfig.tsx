import React from 'react';
import { Card, Divider, Input, Button, Row, Col, InputNumber, Select, Alert, Typography, Tag, Checkbox, Switch, Tooltip } from 'antd';
import { Settings2, MonitorPlay, TreePine, Cpu, Ruler, AlertTriangle } from 'lucide-react';
import { clsx } from 'clsx';
import {
  TrainingParams,
  TrainingContext,
  TrainingTarget,
  WfaConfig,
  DealPrice,
  ModelType,
  ModelCategory,
  ModelTypeOption,
  MODEL_TYPE_OPTIONS,
  EnsembleMethod,
  MODEL_DL_DEFAULTS,
} from './trainingUtils';
import type { AppMarket } from '../../store/slices/uiSlice';

const MARKET_BENCHMARKS: Record<string, { label: string; value: string }[]> = {
  CN: [
    { label: '沪深300', value: 'SH000300' },
    { label: '中证500', value: 'SH000905' },
    { label: '中证1000', value: 'SH000852' },
  ],
  HK: [
    { label: '恒生指数', value: 'HSI' },
    { label: '恒生国企', value: 'HSCEI' },
    { label: '恒生科技', value: 'HSTECH' },
  ],
  US: [
    { label: '标普500', value: 'SPX' },
    { label: '纳斯达克100', value: 'NDX' },
    { label: '道琼斯30', value: 'DJI' },
  ],
  CRYPTO: [
    { label: '比特币', value: 'BTC' },
    { label: '以太坊', value: 'ETH' },
  ],
  FUTURES: [
    { label: '原油', value: 'CL.FUT' },
    { label: '沪铜', value: 'CU.FUT' },
    { label: '螺纹钢', value: 'RB.FUT' },
    { label: '黄金', value: 'AU.FUT' },
  ],
};

interface ParameterConfigProps {
  params: TrainingParams;
  context: TrainingContext;
  onParamsChange: (params: TrainingParams) => void;
  onContextChange: (context: TrainingContext) => void;
  displayName: string;
  onDisplayNameChange: (name: string, mode: 'auto' | 'manual') => void;
  autoDisplayName: string;
  market?: AppMarket;
  target: TrainingTarget;
  onTargetChange: (target: TrainingTarget) => void;
  wfa?: WfaConfig;
  onWfaChange?: (wfa: WfaConfig) => void;
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

export const ParameterConfig: React.FC<ParameterConfigProps> = ({
  params,
  context,
  onParamsChange,
  onContextChange,
  displayName,
  onDisplayNameChange,
  autoDisplayName,
  market = 'CN',
  target,
  onTargetChange,
  wfa,
  onWfaChange,
}) => {
  const benchmarkOptions = MARKET_BENCHMARKS[market] || MARKET_BENCHMARKS.CN;
  const isMultiHorizon = (target.horizonDaysList?.length ?? 0) >= 2;
  const isSingleLgb = params.model_types.length === 1 && params.model_type === 'lightgbm';
  const quantileDisabled = market !== 'CN' || !isSingleLgb || isMultiHorizon;
  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_0.9fr]">
      <Card className="rounded-3xl border-slate-200 shadow-sm" styles={{ body: { padding: 20 } }}>
        <SectionHeader
          title="第三步：参数配置"
          desc="把模型超参与训练上下文拆开，避免配置语义混在一起。"
          icon={<Settings2 size={18} className="text-indigo-500" />}
        />
        <Divider className="my-4" />
        <div className="space-y-4">
          {/* 模型类型选择 */}
          <Card className="rounded-2xl border-slate-200" size="small" title="模型类型">
            <div className="space-y-3">
              <div className="text-xs text-slate-500">
                选择训练模型。树模型适合快速实验，线性模型作为基线 sanity check，深度学习模型在大数据集上潜力更大。支持多选进行集成训练。
              </div>
              <Checkbox.Group
                value={params.model_types}
                className="w-full"
                onChange={(checkedValues) => {
                  const selected = checkedValues as ModelType[];
                  if (selected.length === 0) return;
                  const primary = selected[0];
                  // 切换模型类型时，自动填充该模型的推荐 DL 默认参数
                  const dlDefaults = MODEL_DL_DEFAULTS[primary] || {};
                  // 对于非 DL 模型，不覆盖已设置的 DL 参数
                  const updated: TrainingParams = {
                    ...params,
                    model_type: primary,
                    model_types: selected,
                    ensemble_method: selected.length > 1 ? (params.ensemble_method || 'none') : 'none',
                    prediction_mode: selected.length === 1 && primary === 'lightgbm' ? params.prediction_mode : 'point',
                  };
                  if (dlDefaults.dl_hidden_size !== undefined) {
                    Object.assign(updated, dlDefaults);
                  }
                  onParamsChange(updated);
                }}
              >
                <div className="space-y-3">
                  <div className="text-xs font-medium text-slate-600 flex items-center gap-1">
                    <TreePine size={12} /> 树模型
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-2 pl-1">
                    {MODEL_TYPE_OPTIONS.filter(m => m.category === 'tree').map(m => (
                      <Checkbox
                        key={m.value}
                        value={m.value}
                        className="!inline-flex !items-center [&_.ant-checkbox]:top-0 [&_.ant-checkbox]:self-center"
                      >
                        <span className="inline-flex items-center flex-wrap gap-x-1.5 leading-normal">
                          <Tooltip title={m.tooltip} placement="topLeft" styles={{ root: { maxWidth: 360 } }}>
                            <span className="text-sm cursor-help border-b border-dashed border-slate-300 font-medium text-slate-700">{m.label}</span>
                          </Tooltip>
                          <span className="text-xs text-slate-400">{m.description}</span>
                        </span>
                      </Checkbox>
                    ))}
                  </div>
                  <div className="text-xs font-medium text-slate-600 flex items-center gap-1">
                    <Ruler size={12} /> 线性基线
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-2 pl-1">
                    {MODEL_TYPE_OPTIONS.filter(m => m.category === 'linear').map(m => (
                      <Checkbox
                        key={m.value}
                        value={m.value}
                        className="!inline-flex !items-center [&_.ant-checkbox]:top-0 [&_.ant-checkbox]:self-center"
                      >
                        <span className="inline-flex items-center flex-wrap gap-x-1.5 leading-normal">
                          <Tooltip title={m.tooltip} placement="topLeft" styles={{ root: { maxWidth: 360 } }}>
                            <span className="text-sm cursor-help border-b border-dashed border-slate-300 font-medium text-slate-700">{m.label}</span>
                          </Tooltip>
                          <span className="text-xs text-slate-400">{m.description}</span>
                        </span>
                      </Checkbox>
                    ))}
                  </div>
                  <div className="text-xs font-medium text-slate-600 flex items-center gap-1">
                    <Cpu size={12} /> 深度学习
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-2 pl-1">
                    {MODEL_TYPE_OPTIONS.filter(m => m.category === 'deep_learning').map(m => (
                      <Checkbox
                        key={m.value}
                        value={m.value}
                        className="!inline-flex !items-center [&_.ant-checkbox]:top-0 [&_.ant-checkbox]:self-center"
                      >
                        <span className="inline-flex items-center flex-wrap gap-x-1.5 leading-normal">
                          <Tooltip title={m.tooltip} placement="topLeft" styles={{ root: { maxWidth: 360 } }}>
                            <span className="text-sm cursor-help border-b border-dashed border-slate-300 font-medium text-slate-700">{m.label}</span>
                          </Tooltip>
                          <span className="text-xs text-slate-400">{m.description}</span>
                        </span>
                      </Checkbox>
                    ))}
                  </div>
                </div>
              </Checkbox.Group>
              {params.model_types.length > 1 && (
                <div className="flex flex-wrap gap-1.5">
                  {params.model_types.map(mt => {
                    const opt = MODEL_TYPE_OPTIONS.find(m => m.value === mt);
                    return (
                      <Tag key={mt} color="blue" className="rounded-lg">
                        {opt?.label ?? mt}
                      </Tag>
                    );
                  })}
                </div>
              )}
              {(() => {
                const hasTree = params.model_types.some(mt => {
                  const opt = MODEL_TYPE_OPTIONS.find(m => m.value === mt);
                  return opt?.category === 'tree' || opt?.category === 'linear';
                });
                const hasDL = params.model_types.some(mt => {
                  const opt = MODEL_TYPE_OPTIONS.find(m => m.value === mt);
                  return opt?.category === 'deep_learning';
                });
                return hasTree && hasDL;
              })() && (
                <Alert
                  type="warning"
                  showIcon
                  icon={<AlertTriangle size={14} />}
                  message="树模型与深度学习模型混合训练时，集成方法暂不支持，将分别独立训练"
                  className="rounded-xl"
                />
              )}
              {params.model_types.length > 1 && !(() => {
                const hasTree = params.model_types.some(mt => {
                  const opt = MODEL_TYPE_OPTIONS.find(m => m.value === mt);
                  return opt?.category === 'tree' || opt?.category === 'linear';
                });
                const hasDL = params.model_types.some(mt => {
                  const opt = MODEL_TYPE_OPTIONS.find(m => m.value === mt);
                  return opt?.category === 'deep_learning';
                });
                return hasTree && hasDL;
              })() && (
                <div className="space-y-1">
                  <div className="text-xs text-slate-500">集成方法</div>
                  <Select
                    value={params.ensemble_method}
                    className="w-full"
                    onChange={(value) => onParamsChange({ ...params, ensemble_method: value as EnsembleMethod })}
                    options={[
                      { label: '无集成 (各自独立训练)', value: 'none' },
                      { label: 'Stacking 集成', value: 'stacking' },
                    ]}
                  />
                </div>
              )}
              {params.ensemble_method === 'stacking' && (
                <Row gutter={[12, 12]}>
                  <Col span={12}>
                    <div className="space-y-1">
                      <div className="text-xs text-slate-500">OOF 折数 (n_folds)</div>
                      <InputNumber
                        value={params.n_folds ?? 3}
                        min={2}
                        max={10}
                        step={1}
                        className="w-full"
                        onChange={(v) => onParamsChange({ ...params, n_folds: Number(v ?? 3) })}
                      />
                      <div className="text-[10px] text-slate-400">时序扩展窗口折数，越多越稳但越慢</div>
                    </div>
                  </Col>
                  <Col span={12}>
                    <div className="space-y-1">
                      <div className="text-xs text-slate-500">元学习器正则 (alpha)</div>
                      <InputNumber
                        value={params.meta_alpha ?? 1.0}
                        min={0.01}
                        max={100}
                        step={0.5}
                        className="w-full"
                        onChange={(v) => onParamsChange({ ...params, meta_alpha: Number(v ?? 1.0) })}
                      />
                      <div className="text-[10px] text-slate-400">Ridge 元学习器 L2 系数，越大越保守</div>
                    </div>
                  </Col>
                </Row>
              )}
              {params.model_types.some(mt => {
                const opt = MODEL_TYPE_OPTIONS.find(m => m.value === mt);
                return opt?.category === 'deep_learning';
              }) && (
                <Alert
                  type="info"
                  showIcon
                  message="深度学习模型需要 GPU 和 PyTorch 环境，训练时间较长"
                  className="rounded-xl"
                />
              )}
            </div>
          </Card>

          <Card className="rounded-2xl border-slate-200" size="small" title="模型命名">
            <div className="space-y-2">
              <div className="text-xs text-slate-500">
                display_name 用于模型管理页展示和训练结果命名，自动规则为“日期_T+N_模型维度_版本”。
              </div>
              <div className="flex items-center gap-2">
                <Input
                  value={displayName}
                  onChange={(event) => onDisplayNameChange(event.target.value, 'manual')}
                  placeholder={autoDisplayName}
                  className="rounded-xl flex-1"
                  maxLength={128}
                />
                <Button
                  className="rounded-xl flex-shrink-0"
                  onClick={() => onDisplayNameChange(autoDisplayName, 'auto')}
                >
                  恢复自动
                </Button>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-400">
                <span>当前自动示例：{autoDisplayName}</span>
                <span>{displayName.trim().length}/128</span>
              </div>
            </div>
          </Card>

          <Card className="rounded-2xl border-slate-200" size="small" title="训练超参">
            <div className="space-y-4">
              {/* Objective & Metric - 共享 */}
              <Row gutter={[12, 12]}>
                <Col span={12}>
                  <div className="space-y-1">
                    <div className="text-xs text-slate-500">Objective</div>
                    <Select
                      value={params.objective}
                      className="w-full"
                      onChange={(value) => onParamsChange({ ...params, objective: value as TrainingParams['objective'] })}
                      options={[
                        { label: '回归 (regression)', value: 'regression' },
                        { label: '二分类 (binary)', value: 'binary' },
                      ]}
                    />
                  </div>
                </Col>
                <Col span={12}>
                  <div className="space-y-1">
                    <div className="text-xs text-slate-500">Metric</div>
                    <Select
                      value={params.metric}
                      className="w-full"
                      onChange={(value) => onParamsChange({ ...params, metric: value as TrainingParams['metric'] })}
                      options={[
                        { label: 'L2', value: 'l2' },
                        { label: 'RMSE', value: 'rmse' },
                        { label: 'MAE', value: 'mae' },
                        { label: 'AUC', value: 'auc' },
                        { label: 'Binary Logloss', value: 'binary_logloss' },
                      ]}
                    />
                  </div>
                </Col>
              </Row>

              {/* LightGBM 专属参数 */}
              {params.model_types.includes('lightgbm') && (
                <>
                  <div className="text-xs font-medium text-slate-500 border-t pt-3">LightGBM 超参</div>
                  <Row gutter={[12, 12]}>
                    {[
                      ['num_leaves', '叶子数', { min: 1, max: 1024, step: 1 }],
                      ['min_data_in_leaf', '叶子最小样本', { min: 1, max: 10000, step: 1 }],
                      ['min_child_samples', '子节点最小样本', { min: 1, max: 10000, step: 1 }],
                      ['path_smooth', '路径平滑', { min: 0, max: 10, step: 0.1 }],
                      ['bagging_freq', 'Bagging 频率', { min: 0, max: 100, step: 1 }],
                      ['lambda_l1', 'L1 正则', { min: 0, max: 10, step: 0.1 }],
                      ['lambda_l2', 'L2 正则', { min: 0, max: 10, step: 0.1 }],
                      ['feature_fraction', '特征采样', { min: 0.1, max: 1, step: 0.01 }],
                      ['bagging_fraction', '行采样', { min: 0.1, max: 1, step: 0.01 }],
                      ['num_boost_round', '最大迭代轮数', { min: 1, max: 10000, step: 10 }],
                      ['early_stopping_rounds', '早停轮数', { min: 1, max: 1000, step: 5 }],
                    ].map(([key, label, lim]) => (
                      <Col span={12} key={key as string}>
                        <div className="space-y-1">
                          <div className="text-xs text-slate-500">{label as string}</div>
                          <InputNumber
                            value={params[key as keyof TrainingParams] as number}
                            min={(lim as any)?.min}
                            max={(lim as any)?.max}
                            step={(lim as any)?.step}
                            className="w-full"
                            onChange={(v) => onParamsChange({ ...params, [key as string]: Number(v ?? params[key as keyof TrainingParams]) })}
                          />
                        </div>
                      </Col>
                    ))}
                    <Col span={12}>
                      <div className="space-y-1">
                        <div className="text-xs text-slate-500">学习率 (lgb_learning_rate)</div>
                        <InputNumber
                          value={params.lgb_learning_rate ?? params.learning_rate}
                          min={0.0001}
                          max={1}
                          step={0.001}
                          className="w-full"
                          onChange={(v) => onParamsChange({ ...params, lgb_learning_rate: Number(v ?? params.learning_rate) })}
                        />
                      </div>
                    </Col>
                    <Col span={12}>
                      <div className="space-y-1">
                        <div className="text-xs text-slate-500">最大深度 (lgb_max_depth)</div>
                        <InputNumber
                          value={params.lgb_max_depth ?? params.max_depth}
                          min={-1}
                          max={64}
                          step={1}
                          className="w-full"
                          onChange={(v) => onParamsChange({ ...params, lgb_max_depth: Number(v ?? params.max_depth) })}
                        />
                      </div>
                    </Col>
                  </Row>
                </>
              )}

              {/* XGBoost 专属参数 */}
              {params.model_types.includes('xgboost') && (
                <>
                  <div className="text-xs font-medium text-slate-500 border-t pt-3">XGBoost 超参</div>
                  <Row gutter={[12, 12]}>
                    {[
                      ['xgb_subsample', '行采样 (subsample)', { min: 0.1, max: 1, step: 0.05 }],
                      ['xgb_colsample_bytree', '列采样', { min: 0.1, max: 1, step: 0.05 }],
                      ['xgb_reg_alpha', 'L1 正则', { min: 0, max: 10, step: 0.1 }],
                      ['xgb_reg_lambda', 'L2 正则', { min: 0, max: 10, step: 0.1 }],
                      ['xgb_min_child_weight', '最小叶子权重', { min: 1, max: 1000, step: 10 }],
                      ['num_boost_round', '最大迭代轮数', { min: 1, max: 10000, step: 10 }],
                      ['early_stopping_rounds', '早停轮数', { min: 1, max: 1000, step: 5 }],
                    ].map(([key, label, lim]) => (
                      <Col span={12} key={key as string}>
                        <div className="space-y-1">
                          <div className="text-xs text-slate-500">{label as string}</div>
                          <InputNumber
                            value={params[key as keyof TrainingParams] as number}
                            min={(lim as any)?.min}
                            max={(lim as any)?.max}
                            step={(lim as any)?.step}
                            className="w-full"
                            onChange={(v) => onParamsChange({ ...params, [key as string]: Number(v ?? params[key as keyof TrainingParams]) })}
                          />
                        </div>
                      </Col>
                    ))}
                    <Col span={12}>
                      <div className="space-y-1">
                        <div className="text-xs text-slate-500">学习率 (xgb_learning_rate)</div>
                        <InputNumber
                          value={params.xgb_learning_rate ?? params.learning_rate}
                          min={0.0001}
                          max={1}
                          step={0.001}
                          className="w-full"
                          onChange={(v) => onParamsChange({ ...params, xgb_learning_rate: Number(v ?? params.learning_rate) })}
                        />
                      </div>
                    </Col>
                    <Col span={12}>
                      <div className="space-y-1">
                        <div className="text-xs text-slate-500">最大深度 (xgb_max_depth)</div>
                        <InputNumber
                          value={params.xgb_max_depth ?? params.max_depth}
                          min={1}
                          max={16}
                          step={1}
                          className="w-full"
                          onChange={(v) => onParamsChange({ ...params, xgb_max_depth: Number(v ?? params.max_depth) })}
                        />
                      </div>
                    </Col>
                  </Row>
                </>
              )}

              {/* CatBoost 专属参数 */}
              {params.model_types.includes('catboost') && (
                <>
                  <div className="text-xs font-medium text-slate-500 border-t pt-3">CatBoost 超参</div>
                  <Row gutter={[12, 12]}>
                    {[
                      ['cb_l2_leaf_reg', 'L2 正则', { min: 0, max: 10, step: 0.5 }],
                      ['cb_random_strength', '随机扰动', { min: 0, max: 10, step: 0.5 }],
                      ['cb_bagging_temperature', 'Bagging 温度', { min: 0, max: 10, step: 0.5 }],
                      ['cb_od_wait', '早停等待轮数', { min: 1, max: 1000, step: 10 }],
                      ['num_boost_round', '最大迭代轮数', { min: 1, max: 10000, step: 10 }],
                      ['early_stopping_rounds', '早停轮数', { min: 1, max: 1000, step: 5 }],
                    ].map(([key, label, lim]) => (
                      <Col span={12} key={key as string}>
                        <div className="space-y-1">
                          <div className="text-xs text-slate-500">{label as string}</div>
                          <InputNumber
                            value={params[key as keyof TrainingParams] as number}
                            min={(lim as any)?.min}
                            max={(lim as any)?.max}
                            step={(lim as any)?.step}
                            className="w-full"
                            onChange={(v) => onParamsChange({ ...params, [key as string]: Number(v ?? params[key as keyof TrainingParams]) })}
                          />
                        </div>
                      </Col>
                    ))}
                    <Col span={12}>
                      <div className="space-y-1">
                        <div className="text-xs text-slate-500">学习率 (cb_learning_rate)</div>
                        <InputNumber
                          value={params.cb_learning_rate ?? params.learning_rate}
                          min={0.001}
                          max={1}
                          step={0.01}
                          className="w-full"
                          onChange={(v) => onParamsChange({ ...params, cb_learning_rate: Number(v ?? params.learning_rate) })}
                        />
                      </div>
                    </Col>
                    <Col span={12}>
                      <div className="space-y-1">
                        <div className="text-xs text-slate-500">树深度 (cb_depth)</div>
                        <InputNumber
                          value={params.cb_depth ?? params.max_depth}
                          min={1}
                          max={16}
                          step={1}
                          className="w-full"
                          onChange={(v) => onParamsChange({ ...params, cb_depth: Number(v ?? params.max_depth) })}
                        />
                      </div>
                    </Col>
                  </Row>
                </>
              )}

              {/* Linear 专属参数 */}
              {params.model_types.includes('linear') && (
                <>
                  <div className="text-xs font-medium text-slate-500 border-t pt-3">Ridge 回归超参</div>
                  <Row gutter={[12, 12]}>
                    <Col span={12}>
                      <div className="space-y-1">
                        <div className="text-xs text-slate-500">正则化系数 (alpha)</div>
                        <InputNumber
                          value={params.linear_alpha ?? 1.0}
                          min={0.0001}
                          max={1000}
                          step={0.1}
                          className="w-full"
                          onChange={(v) => onParamsChange({ ...params, linear_alpha: Number(v ?? 1.0) })}
                        />
                      </div>
                    </Col>
                  </Row>
                </>
              )}

              {/* 深度学习模型参数 */}
              {params.model_types.some(mt => {
                const opt = MODEL_TYPE_OPTIONS.find(m => m.value === mt);
                return opt?.category === 'deep_learning';
              }) && (
                <>
                  <div className="text-xs font-medium text-slate-500 border-t pt-3">
                    深度学习超参 (主模型: {params.model_type ?
                      MODEL_TYPE_OPTIONS.find(m => m.value === params.model_type)?.label :
                      params.model_types.filter(mt => {
                        const opt = MODEL_TYPE_OPTIONS.find(m => m.value === mt);
                        return opt?.category === 'deep_learning';
                      }).map(mt => MODEL_TYPE_OPTIONS.find(m => m.value === mt)?.label).join(', ')}
                    )
                    <span className="ml-2 text-slate-400">— 切换模型时自动填充推荐默认值</span>
                  </div>
                  <Row gutter={[12, 12]}>
                    {[
                      ['dl_hidden_size', '隐藏维度', { min: 16, max: 512, step: 16 }],
                      ['dl_num_layers', '网络层数', { min: 1, max: 8, step: 1 }],
                      ['dl_dropout', 'Dropout', { min: 0, max: 0.9, step: 0.05 }],
                      ['dl_n_epochs', '训练轮数', { min: 10, max: 1000, step: 10 }],
                      ['dl_batch_size', 'Batch Size', { min: 64, max: 10000, step: 64 }],
                      ['dl_lr', '学习率', { min: 0.00001, max: 0.1, step: 0.0001 }],
                      ['dl_step_len', '序列长度', { min: 5, max: 120, step: 5 }],
                    ].map(([key, label, lim]) => (
                      <Col span={12} key={key as string}>
                        <div className="space-y-1">
                          <div className="text-xs text-slate-500">{label as string}</div>
                          <InputNumber
                            value={params[key as keyof TrainingParams] as number}
                            min={(lim as any)?.min}
                            max={(lim as any)?.max}
                            step={(lim as any)?.step}
                            className="w-full"
                            onChange={(v) => onParamsChange({ ...params, [key as string]: Number(v ?? params[key as keyof TrainingParams]) })}
                          />
                        </div>
                      </Col>
                    ))}
                  </Row>
                  {/* DL 模型专属参数：按主模型动态显示 */}
                  {params.model_type === 'tcn' && (
                    <Row gutter={[12, 12]} className="mt-3">
                      <Col span={12}>
                        <div className="space-y-1">
                          <div className="text-xs text-slate-500">卷积核大小 (kernel_size)</div>
                          <InputNumber
                            value={params.tcn_kernel_size ?? 5}
                            min={3}
                            max={15}
                            step={2}
                            className="w-full"
                            onChange={(v) => onParamsChange({ ...params, tcn_kernel_size: Number(v ?? 5) })}
                          />
                          <div className="text-[10px] text-slate-400">增大到 7+ 捕捉更长期依赖</div>
                        </div>
                      </Col>
                    </Row>
                  )}
                  {params.model_type === 'nativetft' && (
                    <Row gutter={[12, 12]} className="mt-3">
                      <Col span={12}>
                        <div className="space-y-1">
                          <div className="text-xs text-slate-500">注意力头数 (num_heads)</div>
                          <InputNumber
                            value={params.tft_num_heads ?? 4}
                            min={1}
                            max={16}
                            step={1}
                            className="w-full"
                            onChange={(v) => onParamsChange({ ...params, tft_num_heads: Number(v ?? 4) })}
                          />
                          <div className="text-[10px] text-slate-400">需能被隐藏维度整除</div>
                        </div>
                      </Col>
                    </Row>
                  )}
                </>
              )}
            </div>
          </Card>

        </div>
      </Card>

      <Card className="rounded-3xl border-slate-200 shadow-sm" styles={{ body: { padding: 20 } }}>
        <SectionHeader
          title="训练上下文"
          desc="记录训练时的资产、基准与交易成本，方便后续回放与模型管理页对齐。"
          icon={<MonitorPlay size={18} className="text-indigo-500" />}
        />
        <Divider className="my-4" />
        <div className="space-y-4">
          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div>
                <div className="mb-1 text-xs text-slate-500">初始资金</div>
                <InputNumber
                  value={context.initialCapital}
                  min={1000}
                  step={10000}
                  className="w-full"
                  onChange={(value) => onContextChange({ ...context, initialCapital: Number(value ?? context.initialCapital) })}
                />
              </div>
              <div>
                <div className="mb-1 text-xs text-slate-500">基准指数</div>
                <Select
                  value={context.benchmark}
                  className="w-full"
                  onChange={(value) => onContextChange({ ...context, benchmark: value })}
                  options={benchmarkOptions}
                />
              </div>
              <div>
                <div className="mb-1 text-xs text-slate-500">手续费率</div>
                <InputNumber
                  value={context.commissionRate}
                  min={0}
                  max={1}
                  step={0.0001}
                  className="w-full"
                  onChange={(value) => onContextChange({ ...context, commissionRate: Number(value ?? context.commissionRate) })}
                />
              </div>
              <div>
                <div className="mb-1 text-xs text-slate-500">滑点</div>
                <InputNumber
                  value={context.slippage}
                  min={0}
                  max={1}
                  step={0.0001}
                  className="w-full"
                  onChange={(value) => onContextChange({ ...context, slippage: Number(value ?? context.slippage) })}
                />
              </div>
              <div className="md:col-span-2">
                <div className="mb-1 text-xs text-slate-500">成交价格</div>
                <Select
                  value={context.dealPrice}
                  className="w-full"
                  onChange={(value) => onContextChange({ ...context, dealPrice: value as DealPrice })}
                  options={[
                    { label: '开盘价 (open)', value: 'open' },
                    { label: '收盘价 (close)', value: 'close' },
                  ]}
                />
              </div>
            </div>
          </div>

          {/* ── 行业编码作为特征 ── */}
          <div className="rounded-2xl border border-indigo-100 bg-white px-3 py-2">
            <div className="flex items-center justify-between gap-3">
              <div className="space-y-0.5">
                <div className="text-xs font-semibold text-slate-700">行业编码作为特征</div>
                <div className="text-[11px] text-slate-400 leading-relaxed">
                  将行业编码作为特征加入模型，CatBoost 原生支持类别特征
                </div>
              </div>
              <Tooltip title="将行业编码作为特征加入模型，CatBoost原生支持类别特征">
                <Switch
                  checked={!!context.industry_as_feature}
                  onChange={(checked) => onContextChange({ ...context, industry_as_feature: checked })}
                />
              </Tooltip>
            </div>
          </div>

          {/* ── 特征截面预处理 ── */}
          <div className="rounded-2xl border border-indigo-100 bg-white px-3 py-2">
            <div className="flex items-center justify-between gap-3">
              <div className="space-y-0.5">
                <div className="text-xs font-semibold text-slate-700">特征截面预处理</div>
                <div className="text-[11px] text-slate-400 leading-relaxed">
                  按交易日截面：中位数填充缺失 + 分位缩尾(1%/99%) + Z-score 标准化。消除量纲差异与极端值
                </div>
              </div>
              <Tooltip title="对特征做截面预处理：每交易日按特征中位数填充缺失（停牌）、1%/99% 分位缩尾、截面 Z-score。开启后模型输入分布更规范，但会改变特征量纲（与旧模型不可直接对比）">
                <Switch
                  checked={!!params.preprocessingEnabled}
                  onChange={(checked) => onParamsChange({ ...params, preprocessingEnabled: checked })}
                />
              </Tooltip>
            </div>
          </div>

          {/* ── 收益率分位推理 ── */}
          <div className="rounded-2xl border border-indigo-100 bg-white px-3 py-2">
            <div className="flex items-center justify-between gap-3">
              <div className="space-y-0.5">
                <div className="text-xs font-semibold text-slate-700">收益率分位推理（P10 / P50 / P90）</div>
                <div className="text-[11px] text-slate-400 leading-relaxed">
                  训练三个 LightGBM 分位模型并做验证集校准。P50 保持作为交易信号；区间仅用于个股推理展示。
                </div>
              </div>
              <Tooltip title="训练三个 LightGBM 分位模型并做验证集校准，用于输出收益率的 P10/P50/P90 区间（仅支持 A 股单模型，且不可与多周期训练同时开启）">
                <Switch
                  checked={params.prediction_mode === 'quantile'}
                  disabled={quantileDisabled}
                  onChange={(checked) => onParamsChange({ ...params, prediction_mode: checked ? 'quantile' : 'point' })}
                />
              </Tooltip>
            </div>
            {isMultiHorizon ? (
              <div className="mt-2 text-[11px] text-amber-600">多周期训练模式下，后端按周期分别产出模型且融合子任务不生成分位模型，故禁用收益率分位推理。</div>
            ) : (market !== 'CN' || !isSingleLgb) ? (
              <div className="mt-2 text-[11px] text-amber-600">首版仅支持 A 股单 LightGBM；目标类型还需选择“回归目标（未来收益率）”。</div>
            ) : null}
          </div>

          {/* ── 多周期训练 ── */}
          <div className="rounded-2xl border border-indigo-100 bg-white px-3 py-2">
            <div className="flex items-center justify-between gap-3">
              <div className="space-y-0.5">
                <div className="text-xs font-semibold text-slate-700">多周期训练</div>
                <div className="text-[11px] text-slate-400 leading-relaxed">
                  一次训练产出 T+1/T+3/T+5/T+10 四个周期模型，并自动创建 ICIR 加权融合模型，利用跨周期一致性提升选股稳定性。
                </div>
              </div>
              <Switch
                checked={(target.horizonDaysList?.length ?? 0) >= 2}
                onChange={(checked) => {
                  if (checked) {
                    // 多周期与收益率分位推理互斥：开启多周期时强制把分位关掉，
                    // 避免提交 q分位+multi-horizon 的矛盾配置。
                    onTargetChange({ ...target, horizonDays: target.horizonDays, horizonDaysList: [1, 3, 5, 10] });
                    if (params.prediction_mode === 'quantile') {
                      onParamsChange({ ...params, prediction_mode: 'point' });
                    }
                  } else {
                    const { horizonDaysList, ...rest } = target;
                    onTargetChange({ ...rest });
                  }
                }}
              />
            </div>
            <div className="mt-1.5 text-xs text-slate-500 leading-relaxed">
              一次训练产出 T+1/T+3/T+5/T+10 四个周期模型，并自动创建 ICIR 加权融合模型，利用跨周期一致性提升选股稳定性。周期选择在此处与第二步「T+N 参数」联动。
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
                {wfa?.enabled && (
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

          {/* ── Walk-Forward 稳定性诊断 ── */}
          {onWfaChange && (
            <div className="rounded-2xl border border-indigo-100 bg-white px-3 py-2">
              <div className="flex items-start justify-between gap-3">
                <div className="space-y-0.5">
                  <div className="text-xs font-semibold text-slate-700">Walk-Forward 稳定性诊断</div>
                  <div className="text-[11px] text-slate-400 leading-relaxed">
                    滚动窗口训练并输出每个窗口的 IC，评估模型在不同历史区间上的稳定性与参数漂移。诊断在正式训练前执行，不产生正式模型。
                  </div>
                </div>
                <Switch
                  checked={!!wfa?.enabled}
                  onChange={(checked) => onWfaChange({ ...(wfa || { enabled: false, strategy: 'rolling', nWindows: 4, trainYears: 3, valMonths: 12, stepMonths: 12 }), enabled: checked })}
                />
              </div>

              {wfa?.enabled && (
                <>
                  <div className="mt-3 grid gap-3 md:grid-cols-2">
                    <div className="md:col-span-2">
                      <div className="mb-1 text-xs text-slate-500">窗口策略</div>
                      <Select
                        value={wfa.strategy}
                        onChange={(v) => onWfaChange({ ...wfa, strategy: v })}
                        className="w-full"
                        options={[
                          { label: '滚动窗口（固定训练长度）', value: 'rolling' },
                          { label: '扩张窗口（数据累积）', value: 'expanding' },
                        ]}
                      />
                      <div className="mt-1 text-[10px] text-slate-400">
                        {wfa.strategy === 'rolling' ? '每窗训练长度固定，避免老数据影响' : '训练集从起点累积，贴近实盘迭代'}
                      </div>
                    </div>
                    <div>
                      <div className="mb-1 text-xs text-slate-500">窗口数</div>
                      <InputNumber
                        min={1}
                        max={12}
                        value={wfa.nWindows}
                        onChange={(v) => onWfaChange({ ...wfa, nWindows: Number(v ?? 4) })}
                        className="w-full"
                      />
                      <div className="mt-1 text-[10px] text-slate-400">验证段数量（个）</div>
                    </div>
                    <div>
                      <div className="mb-1 text-xs text-slate-500">训练长度（年）</div>
                      <InputNumber
                        min={1}
                        max={8}
                        value={wfa.trainYears}
                        onChange={(v) => onWfaChange({ ...wfa, trainYears: Number(v ?? 3) })}
                        className="w-full"
                        disabled={wfa.strategy === 'expanding'}
                      />
                      <div className="mt-1 text-[10px] text-slate-400">每窗训练长度</div>
                    </div>
                    <div>
                      <div className="mb-1 text-xs text-slate-500">验证长度（月）</div>
                      <InputNumber
                        min={1}
                        max={36}
                        value={wfa.valMonths}
                        onChange={(v) => onWfaChange({ ...wfa, valMonths: Number(v ?? 12) })}
                        className="w-full"
                      />
                      <div className="mt-1 text-[10px] text-slate-400">每窗验证长度</div>
                    </div>
                    <div>
                      <div className="mb-1 text-xs text-slate-500">步长（月）</div>
                      <InputNumber
                        min={1}
                        max={36}
                        value={wfa.stepMonths}
                        onChange={(v) => onWfaChange({ ...wfa, stepMonths: Number(v ?? 12) })}
                        className="w-full"
                      />
                      <div className="mt-1 text-[10px] text-slate-400">窗口推进步长</div>
                    </div>
                  </div>
                  <Alert
                    className="mt-3 rounded-xl border-violet-100 bg-white/60"
                    type="info"
                    showIcon
                    message="诊断说明"
                    description="WFA 会额外运行多个窗口的训练，耗时约为基础训练的 2-3 倍。支持树模型（LightGBM/XGBoost/CatBoost）和线性模型，深度学习模型因耗时过长不参与诊断。"
                  />
                </>
              )}
            </div>
          )}

          {/* ── Optuna 自动超参搜索 ── */}
          <div className="rounded-2xl border border-indigo-100 bg-white px-3 py-2">
            <div className="flex items-center justify-between gap-3">
              <div className="space-y-0.5">
                <div className="text-xs font-semibold text-slate-700">Optuna 自动超参搜索</div>
                <div className="text-[11px] text-slate-400 leading-relaxed">
                  自动搜索树模型最优超参（LGB/XGB/CatBoost），以验证集 Rank ICIR 为目标。开启后训练耗时约 ×trial 数
                </div>
              </div>
              <Switch
                checked={!!params.optunaEnabled}
                onChange={(checked) => onParamsChange({ ...params, optunaEnabled: checked })}
              />
            </div>
            {params.optunaEnabled && (
              <div className="mt-3 flex items-center gap-2">
                <span className="text-xs text-slate-500">搜索次数</span>
                <InputNumber
                  value={params.optunaTrials ?? 20}
                  min={10}
                  max={100}
                  step={5}
                  className="w-28"
                  onChange={(v) => onParamsChange({ ...params, optunaTrials: Number(v ?? 20) })}
                />
                <span className="text-[10px] text-slate-400">默认 20 次，耗时约为普通训练的 20 倍</span>
              </div>
            )}
            {params.optunaEnabled && isMultiHorizon && (
              <Alert
                className="mt-2 rounded-lg border-amber-100 bg-amber-50/60"
                type="warning"
                showIcon
                message="Optuna 与多周期训练叠加会显著放大耗时"
                description={`每次超参搜索 ×{params.optunaTrials ?? 20} 次 × 多周期 {(target.horizonDaysList?.length ?? 0)} 个周期 + 1 个融合模型，总耗时约为普通训练的 {((params.optunaTrials ?? 20) * ((target.horizonDaysList?.length ?? 0) + 1)).toFixed(0)} 倍，可能触发训练超时。建议缩短搜索次数或关闭其一。`}
              />
            )}
          </div>

          <Alert
            type="warning"
            showIcon
            message="口径提醒"
            description="训练上下文会写入请求预览和模型元数据，保证模型管理页、回测中心和训练页使用同一套参数口径。"
            className="rounded-2xl border-amber-100 bg-amber-50/70"
          />
        </div>
      </Card>
    </div>
  );
};
