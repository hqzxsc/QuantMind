-- ============================================================================
-- 全局股票池模块 (Global Stock Pool)
-- 幂等：全部 IF NOT EXISTS，可重复执行
--
-- 口径约定（重要）：
--   成员 symbol 一律存「后缀式」600036.SH（QuantDB parquet / Qlib 口径）；
--   API 出入参走前缀式 SH600036，转换必须经 StockCodeUtil，禁止手写切片。
--
-- 存储策略（混合）：
--   成员数 <= qm_stock_pool_member 阈值（默认 2000）→ storage_mode='table'，
--   成员逐行落 qm_stock_pool_member；
--   超过阈值的大池（如 all_a）→ storage_mode='snapshot'，
--   成员落 parquet 快照（qm_stock_pool_version.snapshot_path），表中只存元信息。
-- ============================================================================

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

-- 同 scope + tenant 下 code 唯一（tenant_id 为 NULL 时按空串归组）
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
-- 3. 成员表（仅 storage_mode='table' 的池使用）
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
-- 4. 绑定表（哪个功能在用哪个池）
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
