import React, { useEffect, useMemo, useState } from 'react';
import { Card, Spin, Typography, Tag, Collapse, Empty } from 'antd';
import { TrendingUp, Info } from 'lucide-react';
import ReactECharts from 'echarts-for-react';
import { modelTrainingService } from '../services/modelTrainingService';

const { Text } = Typography;

export const MarketRegimePanel: React.FC<{ modelId: string }> = ({ modelId }) => {
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    if (!modelId) return;
    setLoading(true);
    modelTrainingService
      .getMarketRegime(modelId, 90)
      .then(setData)
      .catch(() => setData({ series: [] }))
      .finally(() => setLoading(false));
  }, [modelId]);

  const series: any[] = data?.series ?? [];
  const thresholds = data?.thresholds ?? { bull: 0.08, bear: 0.02 };
  const current = data?.current ?? null;

  const option = useMemo(() => {
    if (!series.length) return null;
    const dates = series.map((s: any) => s.trade_date);
    const vals = series.map((s: any) => s.avg_score);
    const colors = series.map((s: any) => s.color);
    return {
      tooltip: { trigger: 'axis', formatter: (ps: any) => {
        const p = ps[0];
        const row = series[p.dataIndex];
        return `${row.trade_date}<br/>avg ${row.avg_score} median ${row.median_score} cnt ${row.count}<br/>${row.regime}`;
      }},
      grid: { left: 48, right: 16, top: 24, bottom: 28 },
      xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { fontSize: 10, color: '#64748b' } },
      yAxis: {
        type: 'value',
        min: -0.15, max: 0.15,
        axisLabel: { fontSize: 10, color: '#64748b', formatter: (v: number) => v.toFixed(2) },
        splitLine: { lineStyle: { color: '#f1f5f9' } },
      },
      visualMap: { show: false, dimension: 1, pieces: [
        { gt: thresholds.bull, color: '#ef4444' },
        { gte: thresholds.bear, lte: thresholds.bull, color: '#94a3b8' },
        { lt: thresholds.bear, color: '#10b981' },
      ]},
      series: [
        {
          type: 'line',
          data: vals,
          smooth: true,
          symbol: 'circle',
          symbolSize: 4,
          lineStyle: { width: 2 },
          itemStyle: { color: (p: any) => colors[p.dataIndex] },
          markLine: {
            symbol: ['none','none'],
            data: [
              { yAxis: thresholds.bull, lineStyle: { color: '#ef4444', type: 'solid', width: 1.5 }, label: { formatter: `牛市 ≥${thresholds.bull}`, color: '#ef4444', fontSize: 10 } },
              { yAxis: thresholds.bear, lineStyle: { color: '#10b981', type: 'dashed', width: 1.5 }, label: { formatter: `熊市 <${thresholds.bear}`, color: '#10b981', fontSize: 10 } },
            ],
          },
          areaStyle: { color: 'rgba(148,163,184,0.08)' },
        },
      ],
    };
  }, [series, thresholds]);

  if (loading) return <div className="flex justify-center py-12"><Spin /></div>;
  if (!series.length) return <Empty description="暂无推理均分时序（需先产生 completed 推理）" />;

  const regimeLabel = current?.regime === 'bull' ? '牛市' : current?.regime === 'bear' ? '熊市' : '震荡';
  const regimeColor = current?.regime === 'bull' ? 'red' : current?.regime === 'bear' ? 'green' : 'default';

  return (
    <div className="space-y-4">
      <Card size="small" className="rounded-2xl" >
        <Collapse
          ghost
          items={[{
            key: 'explain',
            label: <span className="text-xs font-black text-slate-700 flex items-center gap-1.5"><Info size={12} className="text-blue-500"/>口径说明</span>,
            children: <div className="text-xs text-slate-600 leading-relaxed space-y-1">
              <div>均分 = 当日全市场 `fusion_score/pred` 均值（与个股终端一致读历史推理数据），`牛市 ≥0.08 红实线 / 震荡 0.02-0.08 灰 / 熊市 &lt;0.02 绿虚线`。</div>
              <div>仅展示最近 90 交易日，`y` 域 `[-0.15,0.15]`，点色三态，横线阈值。</div>
            </div>,
          }]}
        />
      </Card>

      <div className="grid grid-cols-3 gap-3">
        <Card size="small" className="rounded-2xl text-center">
          <div className="text-[11px] text-slate-400 font-bold">当前三态</div>
          <Tag color={regimeColor} className="mt-1 font-black rounded-full px-3">{regimeLabel}</Tag>
          <div className="text-[11px] text-slate-500 mt-1">{current?.trade_date} avg {Number(current?.avg_score ?? 0).toFixed(4)}</div>
        </Card>
        <Card size="small" className="rounded-2xl text-center">
          <div className="text-[11px] text-slate-400 font-bold">阈值</div>
          <div className="text-xs font-mono font-bold text-slate-700 mt-1">牛 {thresholds.bull} / 熊 {thresholds.bear}</div>
          <div className="text-[11px] text-slate-400">震荡区间 {(thresholds.bear).toFixed(2)}~{(thresholds.bull).toFixed(2)}</div>
        </Card>
        <Card size="small" className="rounded-2xl text-center">
          <div className="text-[11px] text-slate-400 font-bold">样本</div>
          <div className="text-lg font-mono font-black text-slate-800">{series.length} 日</div>
          <div className="text-[11px] text-slate-400">近 {series[0]?.trade_date} ~ {series[series.length-1]?.trade_date}</div>
        </Card>
      </div>

      <Card className="rounded-2xl" size="small">
        <div className="flex items-center gap-2 mb-2">
          <TrendingUp size={14} className="text-blue-500" />
          <Text className="text-xs font-black text-slate-700">均分时序（横线分区）</Text>
        </div>
        {option && <ReactECharts option={option} style={{ height: 320 }} />}
      </Card>
    </div>
  );
};

export default MarketRegimePanel;
