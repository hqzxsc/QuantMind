-- ============================================================
-- QuantMind Database Upgrade Script v1.0.4
-- 全局股票池（Global Stock Pool）：新建 4 张 qm_stock_pool* 表
-- ============================================================

-- 背景：股票池成为回测 / 训练 / 推理 / 模拟盘 / 实盘共用的唯一事实源。
-- 成员 symbol 一律存「后缀式」600036.SH（QuantDB parquet / Qlib 口径）；
-- API 出入参走前缀式 SH600036，转换必须经 StockCodeUtil。
--
-- 说明：beta(29ba6c8) 起服务启动期 ensure_tables 也会幂等补建本组表，
-- 本脚本用于升级流程留痕与手工兜底；幂等（IF NOT EXISTS），可重复执行。
-- 新建环境由 backend/shared/db_init.sql 第 64 节直接建全。
-- 内置池（csi300 等 11 条）由启动期 seed 写入，无需手工插数据。

-- ---------------------------------------------------------------------------
-- 1. 池主表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qm_stock_pool (
    pool_id         TEXT PRIMARY KEY,
    code            TEXT NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    market          TEXT NOT NULL DEFAULT 'CN',
    pool_type       TEXT NOT NULL DEFAULT 'static',
    scope           TEXT NOT NULL DEFAULT 'global',
    tenant_id       TEXT,
    owner_user_id   TEXT,
    status          TEXT NOT NULL DEFAULT 'draft',
    visibility      TEXT NOT NULL DEFAULT 'internal',
    definition      JSONB NOT NULL DEFAULT '{}'::jsonb,
    refresh_policy  JSONB NOT NULL DEFAULT '{}'::jsonb,
    current_version INTEGER NOT NULL DEFAULT 0,
    symbol_count    INTEGER NOT NULL DEFAULT 0,
    checksum        TEXT,
    source_kind     TEXT,
    source_ref      TEXT,
    is_system       BOOLEAN NOT NULL DEFAULT FALSE,
    created_by      TEXT,
    updated_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_qm_stock_pool_code
    ON qm_stock_pool (scope, COALESCE(tenant_id, ''), code);

CREATE INDEX IF NOT EXISTS idx_qm_stock_pool_list
    ON qm_stock_pool (market, pool_type, status);

CREATE INDEX IF NOT EXISTS idx_qm_stock_pool_scope
    ON qm_stock_pool (scope, tenant_id, owner_user_id);

-- ---------------------------------------------------------------------------
-- 2. 版本表（发布历史，可回滚）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qm_stock_pool_version (
    id              BIGSERIAL PRIMARY KEY,
    pool_id         TEXT NOT NULL REFERENCES qm_stock_pool(pool_id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'published',
    market          TEXT,
    member_count    INTEGER NOT NULL DEFAULT 0,
    checksum        TEXT,
    storage_mode    TEXT NOT NULL DEFAULT 'table',
    snapshot_path   TEXT,
    changelog       TEXT,
    published_by    TEXT,
    published_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pool_id, version)
);

CREATE INDEX IF NOT EXISTS idx_qm_stock_pool_version_pool
    ON qm_stock_pool_version (pool_id, version DESC);

-- ---------------------------------------------------------------------------
-- 3. 成员表（仅 storage_mode='table' 的池使用；大池走 parquet 快照）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qm_stock_pool_member (
    id              BIGSERIAL PRIMARY KEY,
    pool_id         TEXT NOT NULL REFERENCES qm_stock_pool(pool_id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    symbol          TEXT NOT NULL,
    name            TEXT,
    weight          DOUBLE PRECISION,
    industry        TEXT,
    effective_from  DATE,
    effective_to    DATE,
    meta            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pool_id, version, symbol)
);

CREATE INDEX IF NOT EXISTS idx_qm_stock_pool_member_pool
    ON qm_stock_pool_member (pool_id, version);

CREATE INDEX IF NOT EXISTS idx_qm_stock_pool_member_symbol
    ON qm_stock_pool_member (symbol);

-- ---------------------------------------------------------------------------
-- 4. 绑定表（哪个功能在用哪个池；被引用池禁止删除）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qm_stock_pool_binding (
    id              BIGSERIAL PRIMARY KEY,
    pool_id         TEXT NOT NULL REFERENCES qm_stock_pool(pool_id) ON DELETE CASCADE,
    target_type     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'filter',
    priority        INTEGER NOT NULL DEFAULT 100,
    tenant_id       TEXT,
    user_id         TEXT,
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pool_id, target_type, target_id)
);

CREATE INDEX IF NOT EXISTS idx_qm_stock_pool_binding_target
    ON qm_stock_pool_binding (target_type, target_id);

CREATE INDEX IF NOT EXISTS idx_qm_stock_pool_binding_pool
    ON qm_stock_pool_binding (pool_id);
