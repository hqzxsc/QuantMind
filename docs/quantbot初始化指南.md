# QuantBot 初始化指南

AI（QwenPaw）安装完成后，一键完成 **技能安装 + 人格设定**，把 QuantBot 变成股票/量化方向的投研助手。

## 一、初始化脚本（推荐）

```bash
cd /opt/quantmind  # 或项目根目录

# 全量初始化：12 个量化技能包 + 量化人格（SOUL/PROFILE/AGENTS）
bash scripts/quantbot_init.sh

# 只装技能
bash scripts/quantbot_init.sh --skills-only

# 只写人格
bash scripts/quantbot_init.sh --persona-only
```

环境变量（默认值通常无需改动）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `QWENPAW_BASE_URL` | `http://127.0.0.1:8089` | QwenPaw API 地址（宿主机执行时） |
| `QWENPAW_AGENT_ID` | `default` | 目标工作区 ID |

脚本做的事：
1. 把 `skills/` 下 12 个 QuantMind 技能打成 zip 上传到 QwenPaw 技能池（同名旧技能先删除再传，幂等）
2. 广播到目标工作区并启用
3. 把 `config/qwenpaw/{SOUL,PROFILE,AGENTS}.md` 写入工作区（容器内直接写，宿主机走 docker cp）

执行完重启使技能生效：

```bash
docker restart qwenpaw
```

## 二、装了什么

### 12 个量化技能

| 技能 | 触发场景 |
|------|---------|
| `ai-ide-strategy-writing` | 写策略、生成 Qlib 策略代码、自然语言条件选股 |
| `backtest-center` | 快速回测、专家模式、策略对比、参数优化 |
| `batch-inference-analysis` | 批量推理结果分析、每日信号榜单、行业轮动 |
| `quantdb-sdk` | QuantDB 数据查询、API Key、数据集/字段 |
| `quantdb-fields` | QuantDB 字段单位速查手册（成交量股/手、成交额万元、% vs 小数等实测陷阱） |
| `quantmind-deploy` | 部署运维、问题排查、数据库初始化 |
| `quantmind-operations` | 模型训练/管理、后台数据更新、RSS |
| `rd-agent-factor-mining` | RD-Agent 因子挖掘、演化进度 |
| `simulation-trading` | 模拟交易下单、持仓、资金 |
| `smart-strategy-stock-picking` | 条件选股、智能选股 |
| `stock-market-analysis` | 全市场扫描、行业轮动、个股分析、数据导出 |
| `trading-agents` | 个股深度投研、智能体自主分析报告 |

> 技能源码在 `skills/<name>/SKILL.md`。更新技能后重跑 `--skills-only` 即可（先删后传，幂等）。

### 量化人格

| 文件 | 内容 |
|------|------|
| `config/qwenpaw/SOUL.md` | 核心人格：QuantBot = 量化投研助手；数据说话、风险先说、先结论后细节、中文交流 |
| `config/qwenpaw/PROFILE.md` | 身份+用户资料模板（名字/称呼留空，AI 首聊时确认） |
| `config/qwenpaw/AGENTS.md` | 工作区规则：12 技能路由表（触发词→技能）、平台 API 连接信息、挂载目录地图、工作流规则 |

## 三、需要挂载的目录（docker-compose 已配好）

QwenPaw 容器挂载与 `quantmind` 容器保持一致，AI 可以直接读数据/代码/模型：

| 挂载 | 容器内路径 | 用途 |
|------|-----------|------|
| `./backend`（只读） | `/app/backend` | 后端源码，查接口/报错时读 |
| `./config`（只读） | `/app/config` | 平台配置 |
| `./models` | `/app/models` | 模型文件（metadata/结果） |
| `./db` | `/app/db` | 特征快照 parquet |
| `./data` | `/data` | 行情/报告/回测结果 |
| `./logs` | `/app/logs` | 服务日志 |
| `./`（只读） | `/quantmind` | 项目根（含技能源码） |
| `./scripts`（只读） | `/app/scripts` | 平台脚本 |
| `qwenpaw-data` 卷 | `/app/working` | QwenPaw 工作目录（技能池、工作区、人格） |
| `qwenpaw-secrets` 卷 | `/app/working.secret` | 密钥 |
| `qwenpaw-backups` 卷 | `/app/working.backups` | 备份 |
| `qwenpaw-shared` 卷 | `/qwenpaw-shared` | 与平台共享文件 |
| `/var/run/docker.sock` | `/var/run/docker.sock` | 操作宿主机 Docker（训练容器等） |

独立部署（不用 docker-compose）时参考 `docs/quantbot代理服务.md` 的 `docker run` 命令，把上述挂载带上。

## 四、手工操作（不用脚本时）

### 装技能

```bash
# 1. 打包（zip 根目录是技能目录）
cd skills && zip -qr /tmp/qm_skills.zip ./

# 2. 删除池中同名旧技能（避免冲突）
curl -X DELETE http://127.0.0.1:8089/api/skills/pool/<skill-name>

# 3. 上传到技能池
curl -X POST http://127.0.0.1:8089/api/skills/pool/upload-zip -F "file=@/tmp/qm_skills.zip"

# 4. 广播到 default 工作区（overwrite 幂等）
curl -X POST http://127.0.0.1:8089/api/skills/pool/download \
  -H "Content-Type: application/json" \
  -d '{"skill_name":"<skill-name>","targets":[{"workspace_id":"default"}],"overwrite":true}'
```

### 写人格

```bash
docker cp config/qwenpaw/SOUL.md    qwenpaw:/app/working/workspaces/default/SOUL.md
docker cp config/qwenpaw/PROFILE.md qwenpaw:/app/working/workspaces/default/PROFILE.md
docker cp config/qwenpaw/AGENTS.md  qwenpaw:/app/working/workspaces/default/AGENTS.md
docker restart qwenpaw
```

### 验证

```bash
# 技能池（应含 12 个量化技能，source=local）
curl -s http://127.0.0.1:8089/api/skills/pool | python3 -c "
import sys, json
d = json.load(sys.stdin)
skills = d if isinstance(d, list) else d.get('skills', [])
qm = [s['name'] for s in skills if s.get('source') != 'builtin']
print(len(qm), '量化技能:', sorted(qm))"

# 工作区技能清单
curl -s http://127.0.0.1:8089/api/skills/workspaces | python3 -m json.tool

# 人格文件
docker exec qwenpaw head -8 /app/working/workspaces/default/SOUL.md
```

## 五、注意事项

- 工作区技能删除 API（`DELETE /api/skills/{name}`）按请求上下文解析工作区——跨工作区操作技能用 `/api/skills/pool/download` 的 `targets` 显式指定，别依赖 `X-Agent-Id` 头。
- 技能池上传同名会 409 冲突（返回 `suggested_name`），脚本已自动先删后传。
- QA Agent 工作区（`QwenPaw_QA_Agent_0.2`）是 QwenPaw 内置的文档问答 agent，量化技能默认只装 `default`，别混装。
- 人格文件里 AGENTS.md 会随每次会话作为系统提示注入；改完重启 qwenpaw 生效。
