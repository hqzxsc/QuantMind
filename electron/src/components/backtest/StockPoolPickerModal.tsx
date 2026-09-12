/**
 * 股票池选择弹窗（共用）：回测 / 训练等功能页选择全局股票池。
 * 数据源为用户态只读接口，自定义池在上、内置指数池在下。
 */

import React, { useEffect, useState } from 'react';
import { Check, RefreshCw, X } from 'lucide-react';
import { listStockPoolOptions, StockPoolOption } from '../../services/stockPoolOptionService';

interface StockPoolPickerModalProps {
  open: boolean;
  onClose: () => void;
  onSelect: (pool: StockPoolOption) => void;
  selectedPoolId?: string | null;
  market?: string;
  title?: string;
}

const StockPoolRow: React.FC<{ pool: StockPoolOption; selected: boolean; onSelect: () => void }> = ({
  pool,
  selected,
  onSelect,
}) => (
  <button
    type="button"
    onClick={onSelect}
    className={`w-full text-left px-4 py-3 rounded-xl border mb-2 transition-all flex items-center gap-3 ${
      selected
        ? 'border-blue-500 bg-blue-50/60'
        : 'border-gray-100 bg-white hover:border-blue-200 hover:bg-slate-50'
    }`}
  >
    {/* 左：池名 + 类型徽标 + code */}
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-[14px] font-bold text-slate-800 truncate">{pool.name}</span>
        {!pool.is_system ? (
          <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-600 shrink-0">自定义</span>
        ) : (
          <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 shrink-0">指数</span>
        )}
      </div>
      <div className="text-[11px] text-slate-400 font-mono truncate mt-0.5">{pool.code}</div>
    </div>
    {/* 右：数量 + 哈希 */}
    <div className="shrink-0 text-right">
      <div className="text-[13px] font-black text-slate-700 font-mono leading-tight">
        {pool.symbol_count}<span className="text-[11px] font-bold text-slate-400"> 只</span>
      </div>
      {pool.checksum && (
        <div className="text-[10px] text-slate-400 font-mono leading-tight mt-0.5">{pool.checksum.slice(0, 8)}</div>
      )}
    </div>
    {selected && <Check className="w-4 h-4 text-blue-600 shrink-0" />}
  </button>
);

export const StockPoolPickerModal: React.FC<StockPoolPickerModalProps> = ({
  open,
  onClose,
  onSelect,
  selectedPoolId,
  market = 'CN',
  title = '自定义股票池',
}) => {
  const [pools, setPools] = useState<StockPoolOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setLoadError('');
    listStockPoolOptions({ market })
      .then((items) => {
        if (!cancelled) setPools(items);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : '股票池列表加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, market]);

  if (!open) return null;

  const customPools = pools.filter((p) => !p.is_system);
  const systemPools = pools.filter((p) => p.is_system);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="w-full max-w-lg max-h-[70vh] flex flex-col bg-white rounded-2xl shadow-xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div>
            <div className="text-sm font-bold text-slate-800">{title}</div>
            <div className="text-[11px] text-slate-500 mt-0.5">全局股票池（后台维护，保存即生效）· 选择后按池成分执行</div>
          </div>
          <button type="button" onClick={onClose} className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-600">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto custom-scrollbar p-3">
          {loading ? (
            <div className="flex items-center justify-center py-12 text-sm text-slate-400">
              <RefreshCw className="w-4 h-4 animate-spin mr-2" /> 加载股票池…
            </div>
          ) : loadError ? (
            <div className="py-10 text-center text-sm text-red-500">{loadError}</div>
          ) : pools.length === 0 ? (
            <div className="py-10 text-center">
              <div className="text-sm font-medium text-slate-600">暂无可用股票池</div>
              <div className="text-xs text-slate-400 mt-1">请到管理后台 → 数据管理 → 全局股票池维护</div>
            </div>
          ) : (
            <>
              {customPools.map((pool) => (
                <StockPoolRow key={pool.pool_id} pool={pool} selected={selectedPoolId === pool.pool_id} onSelect={() => onSelect(pool)} />
              ))}
              {systemPools.length > 0 && (
                <div className="px-2 pt-3 pb-1 text-[11px] font-bold text-slate-400 uppercase tracking-wider">内置指数池</div>
              )}
              {systemPools.map((pool) => (
                <StockPoolRow key={pool.pool_id} pool={pool} selected={selectedPoolId === pool.pool_id} onSelect={() => onSelect(pool)} />
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
};
