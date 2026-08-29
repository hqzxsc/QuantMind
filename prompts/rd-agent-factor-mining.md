---
name: rd-agent-factor-mining
title: 因子挖掘（RD-Agent）
category: 策略·因子·模型·回测
description: RD-Agent 因子演化端到端流水线：启动 → 轮询 → 回测评估 → IC/Sharpe 排序 → 入库
outputs: 因子报告 + 入库结果
---

> 复制下方提示词到 QuantBot（QwenPaw 控制台）即可使用；`{占位符}` 处替换为你的实际内容。

请帮我挖掘新因子：市场 {CN/HK/US/CRYPTO/FUTURES}，{可选：关注方向，如量价类/基本面类}。

请读取 skills/rd-agent-factor-mining/SKILL.md 并走完整流水线：preflight 环境检查 → 启动演化 → 轮询完成 → 批量回测评估 → IC/Sharpe 排序 → 解释 → 入库 → Markdown 报告。挖因子耗时较长，先给我预计时间并分段汇报进度。
