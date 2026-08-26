-- Market analysis schema.
-- 大盘分析: 行业热力图、资金流向、标签热度、板块情绪、异动监控

BEGIN;

-- 行业/概念板块定义
CREATE TABLE IF NOT EXISTS qm_market_sectors (
    sector_id VARCHAR(64) PRIMARY KEY,
    sector_type VARCHAR(16) NOT NULL,
    name VARCHAR(128) NOT NULL,
    code VARCHAR(32) NOT NULL,
    parent_sector_id VARCHAR(64),
    metadata_json JSON NOT NULL DEFAULT '{}'::json,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_qm_sectors_type CHECK (sector_type IN ('industry','concept','index')),
    CONSTRAINT uq_qm_sectors_code UNIQUE (sector_type, code)
);

CREATE INDEX IF NOT EXISTS idx_qm_sectors_type ON qm_market_sectors (sector_type);

-- 板块成分股映射
CREATE TABLE IF NOT EXISTS qm_sector_constituents (
    id BIGSERIAL PRIMARY KEY,
    sector_id VARCHAR(64) NOT NULL,
    instrument VARCHAR(16) NOT NULL,
    weight FLOAT,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_qm_constituents_sector FOREIGN KEY (sector_id)
        REFERENCES qm_market_sectors(sector_id) ON DELETE CASCADE,
    CONSTRAINT uq_qm_constituents_sector_inst UNIQUE (sector_id, instrument)
);

CREATE INDEX IF NOT EXISTS idx_qm_constituents_sector ON qm_sector_constituents (sector_id);
CREATE INDEX IF NOT EXISTS idx_qm_constituents_inst ON qm_sector_constituents (instrument);

-- 板块每日指标快照
CREATE TABLE IF NOT EXISTS qm_sector_daily_metrics (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    sector_id VARCHAR(64) NOT NULL,
    avg_pct_change FLOAT,
    median_pct_change FLOAT,
    total_market_cap FLOAT,
    avg_turnover_rate FLOAT,
    advance_count INTEGER,
    decline_count INTEGER,
    flat_count INTEGER,
    net_inflow FLOAT,
    sentiment_score FLOAT,
    sentiment_label VARCHAR(16),
    details JSON NOT NULL DEFAULT '{}'::json,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_qm_daily_metrics_sector FOREIGN KEY (sector_id)
        REFERENCES qm_market_sectors(sector_id) ON DELETE CASCADE,
    CONSTRAINT uq_qm_daily_metrics_date_sector UNIQUE (trade_date, sector_id)
);

CREATE INDEX IF NOT EXISTS idx_qm_daily_metrics_date ON qm_sector_daily_metrics (trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_qm_daily_metrics_sector ON qm_sector_daily_metrics (sector_id, trade_date DESC);

-- 异动监控
CREATE TABLE IF NOT EXISTS qm_market_anomalies (
    anomaly_id VARCHAR(36) PRIMARY KEY,
    trade_date DATE NOT NULL,
    anomaly_type VARCHAR(32) NOT NULL,
    sector_id VARCHAR(64),
    instrument VARCHAR(16),
    severity VARCHAR(16) NOT NULL DEFAULT 'info',
    title VARCHAR(256) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    details JSON NOT NULL DEFAULT '{}'::json,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_qm_anomaly_type CHECK (anomaly_type IN (
        'volume_surge','price_limit_up','price_limit_down',
        'sector_rotation','flow_reversal','breadth_divergence'
    )),
    CONSTRAINT ck_qm_anomaly_severity CHECK (severity IN ('info','warning','critical'))
);

CREATE INDEX IF NOT EXISTS idx_qm_anomalies_date ON qm_market_anomalies (trade_date DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qm_anomalies_type ON qm_market_anomalies (anomaly_type, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_qm_anomalies_sector ON qm_market_anomalies (sector_id, trade_date DESC);

COMMIT;
