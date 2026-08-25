# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

QuantMind is a quantitative trading platform with Python backend (FastAPI) and Electron/React/TypeScript frontend. The OSS edition uses single-container deployment where all backend services run in one container.

## Backend Services (all via `backend/main_oss.py`)

| Service | Port | Responsibility |
|---------|------|----------------|
| api | 8000 | User auth, strategy management, community |
| engine | 8001 | Qlib backtesting, AI strategy generation, model inference |
| trade | 8002 | Order management, positions, risk control |
| stream | 8003 | Real-time quotes, WebSocket push |

## Commands

### Backend
```bash
# Start all services (Docker)
docker-compose up -d

# Run single service locally
SERVICE_MODE=api python backend/main_oss.py

# Tests (run from project root)
python backend/run_tests.py unit        # Unit tests
python backend/run_tests.py integration # Integration tests
python backend/run_tests.py all         # All tests
python backend/run_tests.py trade-long-short  # QMT MVP chain tests

# Lint/format
ruff check backend/
ruff format backend/
```

### Frontend (Electron app in `electron/`)
```bash
npm install              # Install dependencies
npm run dev              # Development (Electron desktop)
npm run dev:web          # Development (Web browser)
npm run typecheck        # Type check
npm run dashboard:build  # Production build
```

## Architecture Notes

- **Feature engineering**: 48-dim features written to `market_data_daily` table by external service
- **Trade service**: Enforces "local-first" order persistence before external submission
- **Redis DB allocation**: 0=general, 1=auth, 2=trade, 3=market, 4=backtest, 5=cache
- **Shared modules**: `backend/shared/` contains cross-service code (DB manager, Redis client, config, logging)
- **Strategy storage**: `backend/shared/strategy_storage.py` is the single entry point for all strategy CRUD operations

## Stock Code Standardization (CRITICAL)

- **Mandatory Format**: Prefix-based (e.g., `SH600036`). **All internal Redis keys, database fields, and API parameters MUST use this format.**
- **Forbidden Format**: Suffix-based (e.g., `600036.SH`). **Do NOT use this format in any new code or configuration.**
- **Normalization Utilities**: 
  - **Backend**: `backend/shared/stock_utils.py` -> `StockCodeUtil.to_prefix(code)`
  - **Frontend**: `electron/src/utils/portfolioUtils.ts` -> `normalizeStockCode(code)`
- **Redis Key Patterns**:
  - Snapshot: `market:snapshot:sh600036` (lowercase prefix for snapshot keys)
  - Series: `market:series:SH600036` (Standard Prefix-based for sequences)
- **Market Auto-Identification**:
  - `SH`: 6xxxxx, 9xxxxx
  - `SZ`: 0xxxxx, 3xxxxx, 2xxxxx
  - `BJ`: 4xxxxx, 8xxxxx

## Environment

Required `.env` keys (defaults in `docker-compose.yml`):
- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
- `REDIS_HOST`, `REDIS_PORT`
- `SECRET_KEY`, `JWT_SECRET_KEY`
- `STORAGE_MODE=local` for OSS edition

## Code Style

- Python: Line length 88, use ruff for linting/formatting
- TypeScript: Run `npm run typecheck` before committing frontend changes

## Development & Deployment Workflow

### 1. Frontend Development (NPM Mode)
- **Local Dev Mode**: 前端统一使用本地 `npm run dev`（Electron 桌面端 / Vite Web 模式，自带 HMR 热重载）。
- **前端修改规则**: **修改前端（electron/src）代码后，不需要每次重新构建或重启服务器上的 `web` 容器**，本地可实时热重载预览调试。提交前运行 `npm run typecheck` 保证类型安全即可。

### 2. Backend Sync & Deployment
- **后端修改规则**: **修改后端（backend/）代码后，必须推送到仓库并同步重启远程服务器上的后端容器**。

```bash
# 1. 本地提交并推送
git add .
git commit -m "descriptive message"
git push gitee NEXT

# 2. 同步并重启 quant-server 后端服务
ssh quant-server "cd /root/quantmindoss && git pull && docker compose restart quantmind quantmind-celery"
```

### 3. 镜像构建规则（是否需重新打包）
- **后端代码走 bind mount**（`./backend:/app/backend` 等挂载进容器），镜像只含 Python 依赖环境。
- **纯代码改动（未新增 pip 依赖、未改 Dockerfile/构建参数）**：只需 `git pull && docker compose restart`，**无需重新打包镜像**。
- **需要重build 的场景**：①新增了 `requirements.txt` 未收录的 Python 依赖；②升级 torch/qlib/duckdb 等底层库；③全新服务器首次部署无现成镜像。
- **重build 方式**（利用 Docker build cache，通常仅增量安装新增包）：服务器上执行 `docker compose build quantmind`，再 `docker compose up -d`。
- 本地 Windows 无法直接构建 linux/amd64 镜像，重build 一律在服务器或 CI 上进行。

## Key Files

- `backend/main_oss.py` - Unified entry point for all backend services
- `backend/run_tests.py` - Test runner with multiple modes
- `backend/shared/` - Shared modules across services
- `docker-compose.yml` - Local deployment configuration
