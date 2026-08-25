import React, { useEffect, useRef, useState } from 'react';
import * as echarts from 'echarts';

interface SankeyNode {
  name: string;
}

interface SankeyLink {
  source: string;
  target: string;
  value: number;
}

interface CapitalFlowSankeyChartProps {
  nodes?: SankeyNode[];
  links?: SankeyLink[];
  height?: number;
}

export const CapitalFlowSankeyChart: React.FC<CapitalFlowSankeyChartProps> = ({
  nodes,
  links,
  height = 380,
}) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);
  const [renderError, setRenderError] = useState(false);

  const sankeyNodes = nodes && nodes.length > 0 ? nodes : [];
  const sankeyLinks = links && links.length > 0 ? links : [];

  useEffect(() => {
    if (!chartRef.current || sankeyNodes.length === 0 || sankeyLinks.length === 0) return;

    if (!instanceRef.current) {
      instanceRef.current = echarts.init(chartRef.current);
    }
    const chart = instanceRef.current;

    const option: echarts.EChartsOption = {
      tooltip: {
        trigger: 'item',
        triggerOn: 'mousemove',
      },
      series: [
        {
          type: 'sankey',
          data: sankeyNodes,
          links: sankeyLinks,
          emphasis: { focus: 'adjacency' },
          lineStyle: {
            color: 'gradient',
            curveness: 0.5,
            opacity: 0.45,
          },
          label: {
            fontSize: 12,
            fontWeight: 'bold',
            color: '#334155',
          },
          itemStyle: {
            borderWidth: 1,
            borderColor: '#cbd5e1',
          },
        },
      ],
    };

    try {
      chart.setOption(option);
      setRenderError(false);
    } catch (e) {
      // 桑基图要求有向无环图，数据异常（如成环）时 ECharts 会抛错，
      // 兜底为占位提示而不是击穿整个页面
      console.error('Sankey chart render failed:', e);
      setRenderError(true);
    }

    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.dispose();
      instanceRef.current = null;
    };
  }, [sankeyNodes, sankeyLinks]);

  if (sankeyNodes.length === 0 || sankeyLinks.length === 0) {
    return (
      <div
        className="w-full flex items-center justify-center text-xs text-slate-400 bg-slate-50/60 rounded-2xl border border-dashed border-slate-200"
        style={{ height }}
      >
        暂无资金流动数据
      </div>
    );
  }

  return (
    <div className="relative" style={{ width: '100%', height }}>
      <div ref={chartRef} style={{ width: '100%', height }} />
      {renderError && (
        <div className="absolute inset-0 flex items-center justify-center text-xs text-slate-400 bg-slate-50/60 rounded-2xl border border-dashed border-slate-200">
          资金流动数据异常，无法渲染桑基图
        </div>
      )}
    </div>
  );
};
