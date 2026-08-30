# components

用途：可复用的 UI 组件。

## 说明
- 归属路径：electron\src\components
- 修改本目录代码后请同步更新本 README
- `inference/InferenceRunDetailView.tsx`：页头「返回列表」按钮与右侧「前一天 / 后一天」日期导航按钮统一风格（同 `rounded-lg h-8 text-[10px] font-bold border-slate-200`，箭头图标统一 13px）；「后一天」改用 `ArrowRight` 图标，不再用 `ArrowLeft` + `rotate-180`。
- 推理历史页面统一圆角与居中规范（涉及 `inference/InferenceHistoryPanel.tsx`、`InferenceRunDetailView.tsx`、`StrategyDashboard.tsx`、`ScoreDistributionPanel.tsx`）：大容器 `rounded-3xl`、子容器 `rounded-2xl`、按钮/控件统一 `rounded-xl`（卡片内小行 `rounded-xl`，表格行删除按钮 `rounded-lg`）；所有空态/占位内容（Empty、暂无数据提示）在容器内水平居中（`flex justify-center` 包裹）。
- 推理历史页面视觉重构（涉及上述 4 个文件 + `StockScoreChart.tsx`）：① 字号规范统一：8/9px → 11px、10/11px → 12px（text-xs），关键数值升 1-2 档（分数分布统计卡 text-base→text-lg、区间计数 text-sm→text-base），消灭看不清的微型字；② `InferenceRunDetailView` 去掉 StrategyDashboard 外层的 glass-panel 双层卡片嵌套；③ `StrategyDashboard` 行业信号强度卡：三个指标瓦片改居中（text-center）、行业 Top1 列表改 `mx-auto max-w-5xl flex flex-wrap justify-center` 整体居中，卡片标题升 text-base、图标 15→16；④ 直方图高度 56→68。
- 删除 `StrategyDashboard` 中的「个股分数区间」（ScoreBucketCard）与「3天分数趋势」（TrendCard）两张卡片（用户指定）：同步移除布局第二行、`TrendingUp`/`Activity` 图标与 `ScoreBucketStat`/`TrendStats` 类型导入；分数区间筛选交互由 `ScoreDistributionPanel` 承担，`StrategyDashboard` 的 `activeBucket`/`onSelectBucket` props 一并移除。
