import React, { useEffect, useRef, useState } from 'react';
import * as echarts from 'echarts';
import { SERVICE_ENDPOINTS } from '../../../config/services';

const MARKET_ANALYSIS_API = `${SERVICE_ENDPOINTS.USER_SERVICE}/market-analysis`;

export interface FlowItem {
  id: string;
  name: string;
  symbol?: string;
  pct_change: number;
  net_inflow: number; // 单位：元
  main_ratio: number; // 主力占比 %
  super_large: number;
  large: number;
  medium: number;
  small: number;
  trend_20d?: number[];
}

interface CapitalFlowHorizontalBarChartProps {
  period?: '1d' | '3d' | '5d' | '10d' | '20d';
  dimension?: 'sector' | 'stock';
  categoryMode?: 'shenwan' | 'concept';
  height?: number | string;
  onItemClick?: (item: FlowItem) => void;
}

export const CapitalFlowHorizontalBarChart: React.FC<CapitalFlowHorizontalBarChartProps> = ({
  period = '5d',
  dimension = 'sector',
  categoryMode = 'shenwan',
  height = 540,
  onItemClick,
}) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);
  const [data, setData] = useState<FlowItem[]>([]);
  const [loading, setLoading] = useState(false);

  // 1. 数据请求
  useEffect(() => {
    fetchData();
  }, [period, dimension, categoryMode]);

  const fetchData = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem('access_token') || '';
      const res = await fetch(
        `${MARKET_ANALYSIS_API}/money-flow/period?period=${period}&dimension=${dimension}&category=${categoryMode}&limit=25`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (res.ok) {
        const json = await res.json();
        if (json.items && json.items.length > 0) {
          setData(json.items);
          setLoading(false);
          return;
        }
      }
    } catch (e) {
      console.warn('资金流向接口请求异常', e);
    }

    setData([]);
    setLoading(false);
  };

  // 2. 渲染 ECharts 横向柱状图
  useEffect(() => {
    if (!chartRef.current || data.length === 0) return;

    if (!instanceRef.current) {
      instanceRef.current = echarts.init(chartRef.current);
    }
    const chart = instanceRef.current;

    // 按资金净流入升序排列，使得流入最大的显示在横轴最上方
    const sortedData = [...data].sort((a, b) => a.net_inflow - b.net_inflow);

    const categories = sortedData.map((d) => d.name);
    const valuesInYi = sortedData.map((d) => Number((d.net_inflow / 100000000).toFixed(2)));

    // 🎯 关键技术实现: 计算最大绝对值，设置 symmetrically 0-centered min/max 确保中线绝对居中
    const maxAbs = Math.max(...valuesInYi.map((v) => Math.abs(v)), 1) * 1.25;

    const option: echarts.EChartsOption = {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: 'rgba(15, 23, 42, 0.92)',
        borderColor: 'rgba(139, 92, 246, 0.4)',
        borderWidth: 1,
        textStyle: { color: '#f8fafc', fontSize: 12 },
        formatter: (params: any) => {
          if (!params || !params.length) return '';
          const p = params[0];
          const item = sortedData[p.dataIndex];
          if (!item) return '';

          const netYi = (item.net_inflow / 100000000).toFixed(2);
          const isNetPos = item.net_inflow >= 0;
          const netColor = isNetPos ? '#f43f5e' : '#10b981';

          const superYi = (item.super_large / 100000000).toFixed(2);
          const largeYi = (item.large / 100000000).toFixed(2);
          const medYi = (item.medium / 100000000).toFixed(2);
          const smYi = (item.small / 100000000).toFixed(2);

          return `
            <div style="padding: 4px 6px; font-family: system-ui, sans-serif; min-width: 210px;">
              <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid rgba(255,255,255,0.12); padding-bottom:6px; margin-bottom:8px;">
                <span style="font-weight:800; font-size:13px; color:#fff;">${item.name} ${item.symbol ? `(${item.symbol})` : ''}</span>
                <span style="font-size:11px; font-weight:700; color:${item.pct_change >= 0 ? '#f43f5e' : '#10b981'}; bg-color:rgba(255,255,255,0.1); padding:1px 6px; border-radius:4px;">
                  ${item.pct_change >= 0 ? '+' : ''}${item.pct_change}%
                </span>
              </div>
              <div style="display:flex; justify-content:space-between; margin-bottom:6px; font-size:12px;">
                <span style="color:#94a3b8;">${period.toUpperCase()} 资金净流向:</span>
                <span style="font-weight:800; color:${netColor}; font-family:monospace;">${isNetPos ? '+' : ''}${netYi} 亿</span>
              </div>
              <div style="display:flex; justify-content:space-between; margin-bottom:8px; font-size:11px;">
                <span style="color:#94a3b8;">主力资金净占比:</span>
                <span style="font-weight:700; color:#c084fc;">${item.main_ratio}%</span>
              </div>
              
              <div style="border-top:1px dashed rgba(255,255,255,0.1); pt:6px; font-size:11px;">
                <div style="color:#cbd5e1; margin-bottom:4px; font-weight:700;">筹码单细分拆解:</div>
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:4px; font-size:10px;">
                  <span style="color:#ef4444;">🔴 超大单: ${superYi}亿</span>
                  <span style="color:#f97316;">🟠 大单: ${largeYi}亿</span>
                  <span style="color:#eab308;">🟡 中单: ${medYi}亿</span>
                  <span style="color:#10b981;">🟢 小单: ${smYi}亿</span>
                </div>
              </div>
              <div style="margin-top:8px; font-size:10px; color:#64748b; text-align:right;">点击可下钻详细数据</div>
            </div>
          `;
        },
      },
      grid: {
        top: 30,
        bottom: 30,
        left: 95,
        right: 95,
        containLabel: false,
      },
      xAxis: {
        type: 'value',
        name: '资金净流向 (亿元)',
        nameTextStyle: { color: '#64748b', fontSize: 11, fontWeight: 'bold' },
        min: -maxAbs, // 🎯 强制左右对称，0 点锁定绝对居中
        max: maxAbs,  // 🎯 强制左右对称
        axisLine: { show: true, lineStyle: { color: '#94a3b8', width: 1.5 } },
        axisTick: { show: true },
        splitLine: { lineStyle: { color: '#e2e8f0', type: 'dashed' } },
        axisLabel: {
          color: '#64748b',
          fontSize: 11,
          fontFamily: 'monospace',
          formatter: (val: number) => {
            if (val === 0) return '0 轴 (居中)';
            return `${val > 0 ? '+' : ''}${val}亿`;
          },
        },
      },
      yAxis: {
        type: 'category',
        data: categories,
        axisLine: { lineStyle: { color: '#cbd5e1' } },
        axisTick: { show: false },
        axisLabel: {
          color: '#334155',
          fontSize: 12,
          fontWeight: 700,
          margin: 12,
        },
      },
      series: [
        {
          name: '资金净流入',
          type: 'bar',
          barWidth: categories.length > 20 ? 14 : 18,
          data: valuesInYi.map((val) => {
            const isPos = val >= 0;
            return {
              value: val,
              itemStyle: {
                borderRadius: isPos ? [0, 8, 8, 0] : [8, 0, 0, 8],
                color: isPos
                  ? new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                      { offset: 0, color: '#f43f5e' },
                      { offset: 1, color: '#8b5cf6' },
                    ])
                  : new echarts.graphic.LinearGradient(1, 0, 0, 0, [
                      { offset: 0, color: '#10b981' },
                      { offset: 1, color: '#059669' },
                    ]),
              },
            };
          }),
          label: {
            show: true,
            position: 'outside',
            color: '#475569',
            fontSize: 11,
            fontWeight: 800,
            fontFamily: 'monospace',
            formatter: (p: any) => {
              const val = p.value as number;
              const original = sortedData[p.dataIndex];
              const pct = original ? original.pct_change : 0;
              const pctStr = `${pct >= 0 ? '+' : ''}${pct}%`;
              return `${val > 0 ? '+' : ''}${val}亿  (${pctStr})`;
            },
          },
          markLine: {
            symbol: 'none',
            silent: true,
            data: [
              {
                xAxis: 0,
                lineStyle: {
                  color: '#8b5cf6',
                  width: 2,
                  type: 'solid',
                },
                label: {
                  show: true,
                  formatter: '0 轴居中',
                  position: 'end',
                  color: '#7c3aed',
                  fontSize: 10,
                  fontWeight: 'bold',
                },
              },
            ],
          },
        },
      ],
    };

    chart.setOption(option, true);

    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);

    chart.off('click');
    chart.on('click', (params: any) => {
      if (params.dataIndex !== undefined && sortedData[params.dataIndex]) {
        onItemClick?.(sortedData[params.dataIndex]);
      }
    });

    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, [data, period]);

  return (
    <div className="relative w-full flex flex-col items-center justify-center">
      {!loading && data.length === 0 ? (
        <div
          className="w-full flex items-center justify-center text-xs text-slate-400 bg-slate-50/60 rounded-2xl border border-dashed border-slate-200"
          style={{ height: typeof height === 'number' ? `${height}px` : height }}
        >
          暂无资金流向数据
        </div>
      ) : (
        <div ref={chartRef} style={{ width: '100%', height: typeof height === 'number' ? `${height}px` : height }} />
      )}
      {loading && (
        <div className="absolute inset-0 bg-white/60 backdrop-blur-xs flex items-center justify-center rounded-2xl">
          <span className="text-xs text-purple-600 font-bold animate-pulse">数据加载中...</span>
        </div>
      )}
    </div>
  );
};
