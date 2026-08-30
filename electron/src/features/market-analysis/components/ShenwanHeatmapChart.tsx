import React, { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

export interface ShenwanSectorItem {
  name: string;
  value: number; // 市值规模 (亿)
  pct_change: number; // 涨跌幅 %
  leader?: string;
  leader_pct?: number;
}

interface ShenwanHeatmapChartProps {
  data?: ShenwanSectorItem[];
  height?: number | string;
}

export const ShenwanHeatmapChart: React.FC<ShenwanHeatmapChartProps> = ({
  data,
  height = 460,
}) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);

  const sectorItems = data && data.length > 0 ? data : [];

  useEffect(() => {
    if (!chartRef.current || sectorItems.length === 0) return;

    if (!instanceRef.current) {
      instanceRef.current = echarts.init(chartRef.current);
    }
    const chart = instanceRef.current;

    const formattedData = sectorItems.map((item) => ({
      name: item.name,
      // 🎯 非线性压缩(幂0.4)，避免超大板块(如半导体/银行)吞掉整张图；tooltip 用 raw_value 显示真实市值
      value: Math.pow(Math.max(item.value || 0, 1), 0.4),
      raw_value: item.value,
      pct_change: item.pct_change,
      leader: item.leader,
      leader_pct: item.leader_pct,
      itemStyle: {
        color:
          item.pct_change > 2
            ? '#ef4444'
            : item.pct_change > 0
            ? '#f87171'
            : item.pct_change === 0
            ? '#94a3b8'
            : item.pct_change > -2
            ? '#34d399'
            : '#10b981',
      },
    }));

    const option: echarts.EChartsOption = {
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(15, 23, 42, 0.92)',
        borderColor: '#334155',
        textStyle: { color: '#ffffff' },
        formatter: (info: any) => {
          const d = info.data || {};
          const cap = (d.raw_value ?? d.value) ?? info.value ?? 0;
          const pct = d.pct_change ?? 0;
          const color = pct >= 0 ? '#f87171' : '#34d399';
          const leaderText = d.leader ? `<div style="margin-top: 2px; color: #cbd5e1;">领涨龙头: <b>${d.leader} (${d.leader_pct >= 0 ? '+' : ''}${d.leader_pct}%)</b></div>` : '';
          return `
            <div style="font-family: sans-serif; font-size: 12px; padding: 2px 4px;">
              <div style="font-weight: bold; font-size: 13px; margin-bottom: 4px; border-bottom: 1px solid #475569; padding-bottom: 4px;">${info.name}</div>
              <div>市值权重规模: <b>${cap} 亿</b></div>
              <div>平均涨跌幅: <span style="color: ${color}; font-weight: bold;">${pct >= 0 ? '+' : ''}${pct}%</span></div>
              ${leaderText}
            </div>
          `;
        },
      },
      series: [
        {
          type: 'treemap',
          top: 4,
          bottom: 4,
          left: 4,
          right: 4,
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          label: {
            show: true,
            // 色块内水平+垂直居中：默认 'inside' 贴左上角，改为锚点定位到色块中心再居中对齐文本块（默认贴左上角显得排版失衡）
            position: ['50%', '50%'],
            align: 'center',
            verticalAlign: 'middle',
            formatter: (params: any) => {
              const d = params.data || {};
              const pct = d.pct_change ?? 0;
              return `{name|${params.name}}\n{pct|${pct >= 0 ? '+' : ''}${pct}%}`;
            },
            rich: {
              name: {
                fontSize: 11,
                fontWeight: 'bold',
                color: '#ffffff',
                lineHeight: 15,
              },
              pct: {
                fontSize: 10,
                fontWeight: 'bold',
                color: '#ffffff',
                fontFamily: 'monospace',
              },
            },
          },
          itemStyle: {
            borderColor: '#ffffff',
            borderWidth: 1.5,
            gapWidth: 1.5,
          },
          data: formattedData,
        },
      ],
    };

    chart.setOption(option);

    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.dispose();
      instanceRef.current = null;
    };
  }, [sectorItems]);

  if (sectorItems.length === 0) {
    return (
      <div
        className="w-full flex items-center justify-center text-xs text-slate-400 bg-slate-50/60 rounded-2xl border border-dashed border-slate-200"
        style={{ height }}
      >
        暂无热力图数据
      </div>
    );
  }

  return <div ref={chartRef} style={{ width: '100%', height }} className="w-full" />;
};
