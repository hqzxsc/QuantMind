/**
 * 技能中心（Skills Center）— 原「股票报告」页面升级版
 *
 * 上区：提示词库（复制到 QuantBot 即可使用对应技能）
 * 下区：报告档案（复用 ReportManagerPage 的文件树 + PDF 预览，
 *        数据源为统一报告目录 /data/reports/trading_agents）
 */

import React from 'react';
import PromptsLibrary from '../components/PromptsLibrary';
import ReportManagerPage from '../../trading-agents/pages/ReportManagerPage';

const SkillsCenterPage: React.FC = () => {
  return (
    <div className="h-full w-full overflow-y-auto bg-[#f1f5f9] p-6 font-sans box-border">
      <div className="mx-auto flex max-w-[1400px] flex-col gap-6">
        <div>
          <h1 className="text-[20px] font-bold text-slate-900">技能中心</h1>
          <p className="mt-1 text-[13px] text-slate-500">
            一键复制提示词给 QuantBot 执行量化任务，或直接浏览已生成的分析报告
          </p>
        </div>

        <PromptsLibrary />

        <div className="flex items-baseline justify-between pt-2">
          <h2 className="text-[16px] font-semibold text-slate-800">报告档案</h2>
          <span className="text-[12px] text-slate-400">
            QuantBot 生成的报告自动落盘，可在线预览 PDF
          </span>
        </div>
        <div className="h-[640px] overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <ReportManagerPage />
        </div>
      </div>
    </div>
  );
};

export default SkillsCenterPage;
