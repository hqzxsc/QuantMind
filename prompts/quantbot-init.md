---
name: quantbot-init
title: QuantBot 环境初始化
category: 平台运营
description: 首次使用 QuantBot 时，检查并安装 QuantMind 技能与人格配置
outputs: QwenPaw 技能池 + default 工作区
---

> 复制下方提示词到 QuantBot（QwenPaw 控制台）即可使用；`{占位符}` 处替换为你的实际内容。

请完成 QuantBot（QwenPaw）与 QuantMind 平台的对接检查：

1. 检查技能池：确认 QuantMind 技能（daily-review、stock-research 等）是否已安装到我的工作区；
2. 如缺失：读取 /quantmind/scripts/quantbot_init.sh 了解安装流程，或提示用户在服务器执行 `bash scripts/quantbot_init.sh --skills-only`（装技能）和 `--persona-only`（装人格）；
3. 环境自检：确认你能访问后端 API（http://quantmind:8000，内部认证见 AGENTS.md）、能读写 /data/reports/、能 docker exec 到 quantmind 容器；
4. 输出一份环境就绪清单：哪些能力可用、哪些缺失、如何补齐。
