/**
 * 技能中心（Skills Center）— 原「股票报告」页面升级版
 *
 * 与项目风格一致的单卡片布局：
 *   左列：提示词分类 + 标题列表
 *   中列：提示词详情（复制到 QuantBot 即可使用）
 *   右列：报告目录（仅文件树，PDF 点击后弹窗预览）
 */

import React, { useState } from 'react';
import { Modal } from 'antd';
import PromptsLibrary from '../components/PromptsLibrary';
import ReportManagerPage from '../../trading-agents/pages/ReportManagerPage';
import PdfPreview from '../../trading-agents/components/PdfPreview';

const ENGINE_BASE = '/api/v1/trading-agents';

const SkillsCenterPage: React.FC = () => {
  const [previewFile, setPreviewFile] = useState<string | null>(null);

  return (
    <div className="w-full h-full bg-[#f8fafc] p-6 flex overflow-hidden font-sans box-border select-none">
      {/* 主一体化框架 (32px 大圆角，与全站页面风格一致) — 横向三列：左提示词分类 / 中提示词详情 / 右报告档案 */}
      <div className="bg-white border border-gray-200 shadow-sm w-full h-full rounded-[32px] flex overflow-hidden">
        {/* 左 + 中：提示词库（分类列表 / 提示词详情） */}
        <div className="flex-1 min-w-0 flex overflow-hidden">
          <PromptsLibrary />
        </div>

        {/* 右列：报告目录（单列文件树，PDF 弹窗预览），与左列提示词分类列表等宽 */}
        <div className="w-64 shrink-0 border-l border-gray-200 overflow-hidden">
          <ReportManagerPage embedded previewMode="modal" onPreviewFile={setPreviewFile} />
        </div>
      </div>

      {/* PDF 预览弹窗 */}
      <Modal
        open={!!previewFile}
        title={
          <span className="text-sm font-bold text-slate-800 break-all">{previewFile}</span>
        }
        onCancel={() => setPreviewFile(null)}
        footer={null}
        width="82vw"
        centered
        destroyOnClose
      >
        {previewFile && (
          <PdfPreview
            url={`${ENGINE_BASE}/files/pdf/${encodeURIComponent(previewFile)}`}
            filename={previewFile}
            height="calc(100vh - 220px)"
          />
        )}
      </Modal>
    </div>
  );
};

export default SkillsCenterPage;
