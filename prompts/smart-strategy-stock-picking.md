---
name: smart-strategy-stock-picking
title: 条件选股
category: 研究分析
description: 基于 QuantDB 的条件选股：自然语言或结构化条件筛选、构建股票池
outputs: 股票池列表
---

> 复制下方提示词到 QuantBot（QwenPaw 控制台）即可使用；`{占位符}` 处替换为你的实际内容。

请帮我选股，条件：{自然语言条件，如：市值 100-500 亿、PE < 30、近 20 日主力资金净流入、行业为半导体}。

请读取 skills/smart-strategy-stock-picking/SKILL.md，把我的条件转成结构化筛选并执行。结果按市值/涨跌幅排序给表格，注明每列单位与数据截止日期；超出 50 只时只展示前 50 并说明总量。
