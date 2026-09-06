import React from 'react';
import {
  Sparkles, Download, Heart, ShieldCheck, TrendingUp, TrendingDown,
  Layers, User, Calendar, HardDrive, Eye
} from 'lucide-react';
import { Button, Tag, Tooltip } from 'antd';
import { clsx } from 'clsx';
import { HubModelItem } from '../../services/modelHubService';

interface ModelHubCardProps {
  model: HubModelItem;
  onViewDetail: (model: HubModelItem) => void;
  onImport: (model: HubModelItem) => void;
  onLike?: (modelId: string) => void;
  importing?: boolean;
}

export const ModelHubCard: React.FC<ModelHubCardProps> = ({
  model,
  onViewDetail,
  onImport,
  onLike,
  importing = false,
}) => {
  // 生成简易 SVG 净值走势曲线 (Mini Sparkline)
  const renderSparkline = (curve?: Array<{ date: string; value: number }>) => {
    if (!curve || curve.length < 2) {
      return (
        <div className="h-10 w-full flex items-center justify-center bg-slate-50/50 rounded-lg text-[10px] text-slate-300">
          暂无净值曲线数据
        </div>
      );
    }

    const values = curve.map((p) => p.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const width = 280;
    const height = 40;
    const padding = 4;

    const points = curve
      .map((p, i) => {
        const x = (i / (curve.length - 1)) * (width - padding * 2) + padding;
        const y = height - padding - ((p.value - min) / range) * (height - padding * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');

    const isPositive = values[values.length - 1] >= values[0];
    const strokeColor = isPositive ? '#ef4444' : '#10b981'; // A股红涨绿跌
    const fillColor = isPositive ? 'rgba(239, 68, 68, 0.08)' : 'rgba(16, 185, 129, 0.08)';

    const areaPoints = `${padding},${height} ${points} ${width - padding},${height}`;

    return (
      <div className="relative w-full h-10 overflow-hidden rounded-md">
        <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-full">
          <polygon points={areaPoints} fill={fillColor} />
          <polyline
            fill="none"
            stroke={strokeColor}
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            points={points}
          />
        </svg>
      </div>
    );
  };

  const formattedSize = (bytes?: number) => {
    if (!bytes || bytes <= 0) return '—';
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const getAlgoColor = (algo: string) => {
    switch (algo.toLowerCase()) {
      case 'catboost':
        return 'orange';
      case 'lightgbm':
        return 'blue';
      case 'xgboost':
        return 'purple';
      case 'gru':
      case 'lstm':
        return 'cyan';
      default:
        return 'geekblue';
    }
  };

  return (
    <div className="group relative rounded-2xl border border-slate-200/80 bg-white p-4 shadow-sm hover:shadow-md hover:border-blue-400/80 transition-all duration-200 flex flex-col justify-between">
      {/* 顶部标题与创作者 */}
      <div>
        <div className="flex items-start justify-between gap-2 mb-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 mb-1">
              <Tag color={getAlgoColor(model.algorithm)} className="!text-[10px] !px-1.5 !py-0 !rounded-md font-bold shrink-0">
                {model.algorithm}
              </Tag>
              <Tag color="default" className="!text-[10px] !px-1.5 !py-0 !rounded-md shrink-0">
                {model.target_horizon || 'T+5'}
              </Tag>
              {model.is_verified && (
                <Tooltip title="官方已验真策略">
                  <span className="flex items-center gap-0.5 text-[10px] text-blue-600 bg-blue-50 px-1.5 py-0.2 rounded font-semibold shrink-0">
                    <ShieldCheck size={11} /> 验真
                  </span>
                </Tooltip>
              )}
            </div>
            <h4
              onClick={() => onViewDetail(model)}
              className="text-sm font-bold text-slate-800 group-hover:text-blue-600 transition-colors truncate cursor-pointer"
              title={model.name}
            >
              {model.name}
            </h4>
          </div>

          <button
            onClick={() => onLike?.(model.id)}
            className="text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-lg p-1.5 transition-colors shrink-0"
            title="点赞"
          >
            <Heart size={14} />
          </button>
        </div>

        {/* 策略描述 */}
        <p className="text-xs text-slate-500 line-clamp-2 h-8 leading-relaxed mb-3">
          {model.description || '创作者暂未填写详细描述。'}
        </p>

        {/* 核心指标 4 宫格 */}
        <div className="grid grid-cols-2 gap-2 bg-slate-50/80 rounded-xl p-2.5 mb-3 border border-slate-100">
          <div>
            <div className="text-[10px] text-slate-400 font-semibold">夏普比率 (Sharpe)</div>
            <div className="text-sm font-black text-slate-800">
              {model.sharpe_ratio ? model.sharpe_ratio.toFixed(2) : '—'}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-slate-400 font-semibold">测试集 IC / Rank IC</div>
            <div className="text-sm font-black text-slate-800">
              {model.test_ic ? model.test_ic.toFixed(3) : '—'} / {model.rank_ic ? model.rank_ic.toFixed(3) : '—'}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-slate-400 font-semibold">年化收益率</div>
            <div className={clsx(
              "text-xs font-black",
              model.annual_return > 0 ? "text-red-600" : model.annual_return < 0 ? "text-emerald-600" : "text-slate-700"
            )}>
              {model.annual_return ? `${(model.annual_return * 100).toFixed(1)}%` : '—'}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-slate-400 font-semibold">最大回撤</div>
            <div className="text-xs font-black text-slate-700">
              {model.max_drawdown ? `${(model.max_drawdown * 100).toFixed(1)}%` : '—'}
            </div>
          </div>
        </div>

        {/* 净值走势预览 */}
        <div className="mb-3">
          <div className="flex items-center justify-between text-[10px] text-slate-400 font-semibold mb-1">
            <span>净值回测走势</span>
            {model.calmar_ratio ? <span>Calmar: {model.calmar_ratio.toFixed(2)}</span> : null}
          </div>
          {renderSparkline(model.equity_curve)}
        </div>
      </div>

      {/* 底部作者与操作 */}
      <div>
        <div className="flex items-center justify-between text-[11px] text-slate-400 border-t border-slate-100 pt-2.5 mb-3">
          <div className="flex items-center gap-1.5 truncate max-w-[130px]" title={model.author_username}>
            <div className="w-4 h-4 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center text-[9px] font-black">
              {model.author_username?.charAt(0)?.toUpperCase() || 'U'}
            </div>
            <span className="truncate font-medium text-slate-600">{model.author_username}</span>
          </div>

          <div className="flex items-center gap-2 text-[10px]">
            <span className="flex items-center gap-0.5">
              <Download size={11} /> {model.downloads_count || 0}
            </span>
            <span className="flex items-center gap-0.5">
              <HardDrive size={11} /> {formattedSize(model.file_size_bytes)}
            </span>
          </div>
        </div>

        <div className="flex gap-2">
          <Button
            size="small"
            className="flex-1 rounded-xl font-bold text-xs h-8"
            onClick={() => onViewDetail(model)}
          >
            详情
          </Button>
          <Button
            type="primary"
            size="small"
            icon={<Download size={12} />}
            loading={importing}
            className="flex-1 rounded-xl bg-blue-600 hover:bg-blue-500 font-bold text-xs h-8 border-none shadow-sm"
            onClick={() => onImport(model)}
          >
            一键导入
          </Button>
        </div>
      </div>
    </div>
  );
};
