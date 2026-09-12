-- ============================================================
-- QuantMind Database Upgrade Script v1.0.6
-- 管理员 user_id 纠正：'admin' → '00000001'
-- ============================================================
--
-- 背景：db_init.sql 曾 seed user_id='admin' 的坏行（username='admin' 正确），
-- 且 seed 按 username 判存在后跳过，导致线上 users.user_id='admin'。
-- 登录 JWT 的 sub 取 user_id，全链路（信号表/池目录/Redis 键）用的都是它，
-- 与 8 位规范 ID（_generate_user_id）不一致。
--
-- 本脚本幂等，可重复执行。DO 块单事务；逐表异常隔离，老版本缺表时跳过。
-- 注意：整数型 user_id 列（strategies/replay_sessions 等存 users.id）不受
-- 影响，无需处理；模拟盘整型表（sim 账户 0→1）见第 5 节（带防覆盖守卫）。
-- Redis 键与 JWT 刷新不在 SQL 范围
--（latest 指针带 24h TTL，训练 active 标记下次运行覆盖；用户重登一次即可；
-- 模拟盘读侧缺缓存时按 PG 台账自动重建，旧 :0 键残留无害），
-- 全量迁移（含 Redis/池目录）请跑 backend/scripts/migrate_legacy_user_ids.py。
--
-- FK 说明：4 个指向 users(user_id) 的约束为即时检查，子表先改则子侧校验
-- 失败、父表先改则父侧校验失败，故先卸后建（原名，与 db_init.sql 一致）。
--
-- 写法约束：全文件不得出现百分号字符（psycopg2 fallback 路径会误解析，
-- 故用 quote_ident 拼接 + NOTICE 字符串连接）。

DO $$
DECLARE
    t TEXT;
    n INT;
    fk TEXT[][];
    f TEXT[];
BEGIN
    fk := ARRAY[
        ['user_roles', 'user_roles_user_id_fkey'],
        ['identity_verifications', 'identity_verifications_user_id_fkey'],
        ['notifications', 'notifications_user_id_fkey'],
        ['password_reset_tokens', 'password_reset_tokens_user_id_fkey']
    ];

    -- 1. 卸 FK（IF EXISTS，老库无约束也不报错）
    FOREACH f SLICE 1 IN ARRAY fk LOOP
        BEGIN
            EXECUTE 'ALTER TABLE ' || quote_ident(f[1])
                || ' DROP CONSTRAINT IF EXISTS ' || quote_ident(f[2]);
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'upgrade_v1.0.6 drop FK skipped';
        END;
    END LOOP;

    -- 2. 全库字符型 user_id 列 sweep（users 表最后单独处理）
    FOR t IN
        SELECT table_name FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'user_id'
          AND data_type IN ('character varying', 'character', 'text')
          AND table_name <> 'users'
        ORDER BY table_name
    LOOP
        BEGIN
            EXECUTE 'UPDATE ' || quote_ident(t)
                || ' SET user_id = ' || quote_literal('00000001')
                || ' WHERE user_id = ' || quote_literal('admin');
            GET DIAGNOSTICS n = ROW_COUNT;
            IF n > 0 THEN
                RAISE NOTICE 'upgrade_v1.0.6 sweep done';
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'upgrade_v1.0.6 sweep skipped';
        END;
    END LOOP;

    -- 3. users 主行
    BEGIN
        UPDATE users SET user_id = '00000001' WHERE user_id = 'admin';
        GET DIAGNOSTICS n = ROW_COUNT;
        IF n > 0 THEN
            RAISE NOTICE 'upgrade_v1.0.6 users row fixed';
        END IF;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'upgrade_v1.0.6 sweep users skipped';
    END;

    -- 4. 原名建回 FK
    FOREACH f SLICE 1 IN ARRAY fk LOOP
        BEGIN
            EXECUTE 'ALTER TABLE ' || quote_ident(f[1])
                || ' ADD CONSTRAINT ' || quote_ident(f[2])
                || ' FOREIGN KEY (user_id) REFERENCES users(user_id)';
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'upgrade_v1.0.6 add FK skipped';
        END;
    END LOOP;

    -- 5. 模拟盘整型/字符 user_id：sim 账户 0 → 1。
    -- 背景：旧 admin 的 sub 非数字，sim 侧被映射为 user 0；纠正后新身份读
    -- user 1，资金会 stranded。仅当目标 user 1 完全无行时才搬（NOT EXISTS
    -- 守卫，绝不覆盖已有账户）；部分表已存在 user 1 行时跳过并 NOTICE。
    FOR t IN
        SELECT x FROM (VALUES
            ('simulation_accounts'),
            ('simulation_cash_ledger'),
            ('simulation_fills'),
            ('simulation_position_lots'),
            ('simulation_position_daily'),
            ('simulation_account_daily'),
            ('simulation_fund_snapshots'),
            ('simulation_orders'),
            ('simulation_rebalance_jobs'),
            ('simulation_reconcile_reports'),
            ('sim_orders'),
            ('sim_trades')
        ) AS v(x)
        WHERE EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = v.x
              AND column_name = 'user_id'
        )
        ORDER BY 1
    LOOP
        BEGIN
            EXECUTE 'UPDATE ' || quote_ident(t)
                || ' SET user_id = ' || quote_literal('1')
                || ' WHERE user_id::text = ' || quote_literal('0')
                || ' AND NOT EXISTS (SELECT 1 FROM ' || quote_ident(t)
                || ' WHERE user_id::text = ' || quote_literal('1') || ')';
            GET DIAGNOSTICS n = ROW_COUNT;
            IF n > 0 THEN
                RAISE NOTICE 'upgrade_v1.0.6 sim moved';
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'upgrade_v1.0.6 sim skipped';
        END;
    END LOOP;
END $$;
