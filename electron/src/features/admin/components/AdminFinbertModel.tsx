/**
 * FinBERT 中文金融情感模型（管理员）
 *
 * 展示 FinBERT 模型介绍与部署指南，并提供实时健康状态探测。
 * 独立 tab，与词条/标签管理职责分离。
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Card,
  Col,
  Modal,
  Row,
  Space,
  Tag,
  Typography,
} from 'antd';
import {
  ApiOutlined,
  BookOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
  ExperimentOutlined,
  FileTextOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { newsService } from '../../news/services/newsService';

const { Title, Text, Paragraph } = Typography;

interface FinbertStatus {
  available: boolean;
  use_finbert: boolean;
  model: string;
  device: number;
  sample_inference: { label: string; confidence: number } | null;
  db_total_24h: number;
  db_finbert_ratio_24h: number | null;
  tip: string;
}

export const AdminFinbertModel: React.FC = () => {
  const [guideOpen, setGuideOpen] = useState(false);
  const [finbertStatus, setFinbertStatus] = useState<FinbertStatus | null>(null);

  const loadFinbertStatus = useCallback(async () => {
    try {
      const s = await newsService.adminFinbertStatus();
      setFinbertStatus(s as unknown as FinbertStatus);
    } catch {
      setFinbertStatus(null);
    }
  }, []);

  useEffect(() => {
    loadFinbertStatus();
  }, [loadFinbertStatus]);

  return (
    <div className="p-6 space-y-4">
      {/* 顶部标题 */}
      <div className="flex items-center justify-between pb-1">
        <div>
          <Title level={4} style={{ margin: 0, fontWeight: 700 }}>
            <ExperimentOutlined style={{ marginRight: 8, color: '#6366f1' }} />
            FinBERT 中文金融情感模型
          </Title>
          <Text type="secondary" style={{ fontSize: 13 }}>
            对 Huntly RSS 资讯做中文金融情感打分 · 与词典法融合（0.6 词法 + 0.4 FinBERT）
          </Text>
        </div>
        <Space>
          <Button icon={<FileTextOutlined />} onClick={() => setGuideOpen(true)} style={{ borderRadius: 6 }}>
            完整部署指南
          </Button>
          <Button icon={<ReloadOutlined />} onClick={loadFinbertStatus} style={{ borderRadius: 6 }}>
            重新探测
          </Button>
        </Space>
      </div>

      <Card
        style={{ borderRadius: 10, border: '1px solid #e2e8f0' }}
        title={
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <ApiOutlined style={{ color: '#6366f1' }} />
            <span style={{ fontWeight: 600 }}>实时健康状态</span>
            {finbertStatus ? (
              finbertStatus.available ? (
                <Tag color="success" icon={<CheckCircleFilled />}>已就绪</Tag>
              ) : finbertStatus.use_finbert ? (
                <Tag color="warning" icon={<CloseCircleFilled />}>加载失败</Tag>
              ) : (
                <Tag icon={<CloseCircleFilled />}>已关闭</Tag>
              )
            ) : (
              <Tag>探测中…</Tag>
            )}
          </span>
        }
      >
        {finbertStatus ? (
          <Row gutter={[24, 12]}>
            <Col xs={24} lg={10}>
              <div style={{ fontSize: 13, color: '#475569', marginBottom: 6 }}>
                <b>模型：</b>{finbertStatus.model || 'bardsai/finance-sentiment-zh-base'}
                <span style={{ marginLeft: 8, color: '#94a3b8' }}>(RoBERTa-zh ≈100MB 三分类)</span>
              </div>
              <div style={{ fontSize: 13, color: '#475569', marginBottom: 6 }}>
                <b>推理设备：</b>
                {finbertStatus.device === -1 ? 'CPU' : `GPU${finbertStatus.device}`}
                <span style={{ marginLeft: 8, color: '#94a3b8' }}>
                  · 启用={String(finbertStatus.use_finbert)}
                </span>
              </div>
              <div style={{ fontSize: 13, color: '#475569', marginBottom: 6 }}>
                <b>近 24h 写入：</b>{finbertStatus.db_total_24h} 篇，
                <b style={{ marginLeft: 4 }}>+finbert 占比：</b>
                {finbertStatus.db_finbert_ratio_24h == null
                  ? '—'
                  : `${(finbertStatus.db_finbert_ratio_24h * 100).toFixed(0)}%`}
              </div>
              {finbertStatus.sample_inference && (
                <div style={{ fontSize: 13, color: '#475569', marginBottom: 6 }}>
                  <b>样例推理：</b>
                  <Tag
                    color={
                      finbertStatus.sample_inference.label === 'bullish'
                        ? 'red'
                        : finbertStatus.sample_inference.label === 'bearish'
                        ? 'green'
                        : 'default'
                    }
                    style={{ margin: '0 4px' }}
                  >
                    {finbertStatus.sample_inference.label}
                  </Tag>
                  conf={finbertStatus.sample_inference.confidence.toFixed(3)}
                </div>
              )}
              <div
                style={{
                  fontSize: 12,
                  color: '#64748b',
                  marginTop: 8,
                  paddingTop: 8,
                  borderTop: '1px dashed #e2e8f0',
                }}
              >
                <ThunderboltOutlined style={{ marginRight: 4, color: '#f59e0b' }} />
                {finbertStatus.tip}
              </div>
            </Col>
            <Col xs={24} lg={14}>
              <Paragraph style={{ marginBottom: 6, fontSize: 13 }}>
                <b>作用：</b>对 Huntly RSS 标题做中文金融情感打分（<Tag color="red" style={{ margin: 0 }}>利好</Tag> / <Tag color="green" style={{ margin: 0 }}>利空</Tag> / <Tag style={{ margin: 0 }}>中性</Tag>）。
              </Paragraph>
              <Paragraph style={{ marginBottom: 6, fontSize: 13 }}>
                <b>生效标记：</b><code>news_article_enrichment.model_version</code> 含
                <Tag color="purple" style={{ margin: '0 4px' }}>+finbert</Tag>
                后缀即代表 FinBERT 真实参与推理。
              </Paragraph>
              <Paragraph style={{ marginBottom: 0, fontSize: 12, color: '#64748b' }}>
                部署位置：<code>backend/services/api/news/sentiment.py</code>（懒加载）·
                权重下载：<code>backend/scripts/download_finbert.py</code>（ModelScope → hf-mirror → HF 三源回退）·
                调度：Celery <code>news_enrich_recent</code>（每分钟）
              </Paragraph>
            </Col>
          </Row>
        ) : (
          <div style={{ fontSize: 12, color: '#94a3b8' }}>无法连接后端 /enrichment/finbert-status</div>
        )}
      </Card>

      {/* ============ FinBERT 模型介绍 & 部署指南 Modal ============ */}
      <Modal
        open={guideOpen}
        onCancel={() => setGuideOpen(false)}
        footer={null}
        width={720}
        destroyOnHidden
        title={
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <ExperimentOutlined style={{ color: '#6366f1' }} />
            <span>FinBERT 中文金融情感模型 · 简介</span>
            <Tag color="purple" style={{ marginLeft: 4 }}>+finbert</Tag>
          </span>
        }
      >
        {/* 顶部：模型一句话 + 当前健康状态小条 */}
        <div
          style={{
            background: 'linear-gradient(135deg, #eef2ff 0%, #f5f3ff 100%)',
            border: '1px solid #c7d2fe',
            borderRadius: 8,
            padding: 14,
            marginBottom: 16,
          }}
        >
          <div style={{ fontSize: 14, color: '#1e293b', fontWeight: 600, marginBottom: 6 }}>
            <BookOutlined style={{ marginRight: 6, color: '#6366f1' }} />
            bardsai/finance-sentiment-zh-base （RoBERTa-zh，≈100MB，三分类情感）
          </div>
          <div style={{ fontSize: 12, color: '#475569' }}>
            对 Huntly RSS 资讯做中文金融情感打分：
            <Tag color="red" style={{ margin: '0 4px' }}>利好 bullish</Tag>
            <Tag color="green" style={{ margin: '0 4px' }}>利空 bearish</Tag>
            <Tag style={{ margin: '0 4px' }}>中性 neutral</Tag>
            ，与本地词典法加权融合（0.6 词法 + 0.4 FinBERT，置信度 ≥ 0.55 时启用）。
          </div>
        </div>

        {/* 一、与词典法的对比 */}
        <Title level={5} style={{ marginTop: 0, marginBottom: 8 }}>
          <span style={{ background: '#6366f1', color: '#fff', borderRadius: 4, padding: '2px 8px', fontSize: 12, marginRight: 8 }}>1</span>
          与「词典法」对比
        </Title>
        <Row gutter={12} style={{ marginBottom: 12 }}>
          <Col span={12}>
            <div style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 6, padding: 10 }}>
              <div style={{ fontWeight: 600, fontSize: 12, color: '#64748b', marginBottom: 6 }}>词典法（默认）</div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: '#334155' }}>
                <li>速度：微秒级，几乎零开销</li>
                <li>准确：依赖词表维护，召回低</li>
                <li>多义词：易误判（"利好兑现"同时命中）</li>
                <li>资源：几乎为零</li>
              </ul>
            </div>
          </Col>
          <Col span={12}>
            <div style={{ background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: 6, padding: 10 }}>
              <div style={{ fontWeight: 600, fontSize: 12, color: '#16a34a', marginBottom: 6 }}>FinBERT</div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: '#334155' }}>
                <li>速度：CPU 单条约 30–80ms / GPU 2–5ms</li>
                <li>准确：上下文建模，显著优于纯词法</li>
                <li>多义词：可识别语境（业绩超预期/暴雷）</li>
                <li>资源：加载约 350MB 内存，CPU 推理可能占满单核</li>
              </ul>
            </div>
          </Col>
        </Row>

        {/* 二、部署步骤（针对当前部署链路：宿主机重建镜像补 PyTorch） */}
        <Title level={5} style={{ marginTop: 16, marginBottom: 8 }}>
          <span style={{ background: '#6366f1', color: '#fff', borderRadius: 4, padding: '2px 8px', fontSize: 12, marginRight: 8 }}>2</span>
          部署步骤
        </Title>
        <Paragraph style={{ fontSize: 13, color: '#334155', marginBottom: 10 }}>
          离线/常规镜像默认 <code>TORCH_DEVICE=skip</code>（不含 PyTorch）。在宿主机用
          <code style={{ background: '#0f172a', color: '#e2e8f0', padding: '1px 6px', borderRadius: 3, margin: '0 4px' }}>
            deploy/install-model-deps.sh
          </code>
          重建镜像补 torch（重启不丢）。
        </Paragraph>

        {/* 3.1 安装 PyTorch */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <div style={{ width: 22, height: 22, borderRadius: 5, background: '#eef2ff', color: '#4f46e5', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, fontSize: 12, fontWeight: 700 }}>1</div>
          <div style={{ fontSize: 13, color: '#334155', lineHeight: 1.6 }}>
            <b>安装 PyTorch（CPU）：</b>
            <code style={{ background: '#0f172a', color: '#e2e8f0', padding: '1px 6px', borderRadius: 3, margin: '0 4px' }}>
              sudo bash deploy/install-model-deps.sh
            </code>
            <div style={{ marginTop: 4, fontSize: 12, color: '#64748b' }}>
              GPU 环境改用 <code>--gpu</code>（CUDA 版 + 本地训练镜像），需 NVIDIA 驱动 + nvidia-container-toolkit，构建较慢。
            </div>
          </div>
        </div>

        {/* 3.2 下载权重 */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <div style={{ width: 22, height: 22, borderRadius: 5, background: '#eef2ff', color: '#4f46e5', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, fontSize: 12, fontWeight: 700 }}>2</div>
          <div style={{ fontSize: 13, color: '#334155', lineHeight: 1.6 }}>
            <b>下载模型权重：</b>
            <code style={{ background: '#0f172a', color: '#e2e8f0', padding: '1px 6px', borderRadius: 3, margin: '0 4px' }}>
              docker exec quantmind python3 /app/backend/scripts/download_finbert.py
            </code>
            <div style={{ marginTop: 4, fontSize: 12, color: '#64748b' }}>
              三源回退：魔搭 ModelScope（国内首选）→ hf-mirror → HuggingFace。
            </div>
          </div>
        </div>

        {/* 3.3 启用开关 */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <div style={{ width: 22, height: 22, borderRadius: 5, background: '#eef2ff', color: '#4f46e5', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, fontSize: 12, fontWeight: 700 }}>3</div>
          <div style={{ fontSize: 13, color: '#334155', lineHeight: 1.6 }}>
            <b>启用（默认关闭，避免打满 celery worker）：</b>
            在 <code>/opt/quantmind/.env</code> 设 <code>NEWS_USE_FINBERT=true</code> 后重建，或 CPU/GPU 部署时显式开启。
          </div>
        </div>

        {/* 3.4 触发重算 */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          <div style={{ width: 22, height: 22, borderRadius: 5, background: '#eef2ff', color: '#4f46e5', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, fontSize: 12, fontWeight: 700 }}>4</div>
          <div style={{ fontSize: 13, color: '#334155', lineHeight: 1.6 }}>
            <b>触发历史重算：</b>
            <code style={{ background: '#0f172a', color: '#e2e8f0', padding: '1px 6px', borderRadius: 3, margin: '0 4px' }}>
              POST /api/v1/news/enrichment/rebuild-all?force=true
            </code>
            <span style={{ marginLeft: 4, fontSize: 12, color: '#64748b' }}>
              （日常新资讯由 Celery <code>news_enrich_recent</code> 每分钟自动处理）
            </span>
          </div>
        </div>

        {/* 三、验证是否生效 */}
        <Title level={5} style={{ marginTop: 16, marginBottom: 8 }}>
          <span style={{ background: '#6366f1', color: '#fff', borderRadius: 4, padding: '2px 8px', fontSize: 12, marginRight: 8 }}>3</span>
          验证生效
        </Title>
        <div
          style={{
            background: '#0f172a',
            color: '#e2e8f0',
            padding: 12,
            borderRadius: 6,
            fontFamily: 'JetBrains Mono, Menlo, Consolas, monospace',
            fontSize: 12,
            lineHeight: 1.6,
            marginBottom: 12,
          }}
        >
          <div style={{ color: '#94a3b8' }}># 1. 后端健康探测（本页右上「重新探测」亦同）</div>
          <div>curl -s http://&lt;api&gt;:8000/api/v1/news/enrichment/finbert-status | jq</div>
          <div style={{ color: '#94a3b8', marginTop: 4 }}># 2. DB 真实生效占比（model_version 带 +finbert 即代表真正参与推理）</div>
          <div>docker exec quantmind-db psql -U quantmind -d quantmind -c "SELECT model_version, count(*) FROM news_article_enrichment GROUP BY model_version;"</div>
        </div>

        <div
          style={{
            marginTop: 16,
            padding: '10px 12px',
            background: '#fff7ed',
            border: '1px solid #fed7aa',
            borderRadius: 6,
            fontSize: 12,
            color: '#9a3412',
          }}
        >
          <ThunderboltOutlined style={{ marginRight: 6, color: '#ea580c' }} />
          <b>提示：</b>若 <code>+finbert</code> 占比为 0，按上方 4 步完成「装 torch → 下权重 → 开开关 → 重算」后，本页状态及 DB 占比即刷新。
        </div>

        {/* 四、关联文件 */}
        <Title level={5} style={{ marginTop: 16, marginBottom: 6 }}>
          <span style={{ background: '#6366f1', color: '#fff', borderRadius: 4, padding: '2px 8px', fontSize: 12, marginRight: 8 }}>4</span>
          关联文件
        </Title>
        <div style={{ fontSize: 12, color: '#475569', lineHeight: 1.9 }}>
          <div><code>deploy/install-model-deps.sh</code> — 宿主机重建镜像补装 PyTorch（torch CPU/GPU）</div>
          <div><code>backend/scripts/download_finbert.py</code> — FinBERT 权重下载（三源回退）</div>
          <div><code>backend/services/api/news/sentiment.py</code> — 模型懒加载与推理</div>
          <div><code>docs/FinBERT 中文金融情感模型.md</code> — 完整部署指南（仓库内文档）</div>
        </div>
      </Modal>
    </div>
  );
};

export default AdminFinbertModel;