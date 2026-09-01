-- ============================================================
-- QuantMind Database Upgrade Script v1.0.1
-- ============================================================

-- 1. real_trading_preflight_snapshots：补上唯一约束 uq_preflight_snapshot_daily
--    （旧表建表未带该约束，而代码用 ON CONFLICT ON CONSTRAINT 依赖它；
--      缺失会导致每次落库抛 UndefinedObjectError 并刷屏。此处幂等，可重复执行。）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_preflight_snapshot_daily'
    ) THEN
        ALTER TABLE real_trading_preflight_snapshots
            ADD CONSTRAINT uq_preflight_snapshot_daily
            UNIQUE (tenant_id, user_id, trading_mode, snapshot_date);
    END IF;
END $$;