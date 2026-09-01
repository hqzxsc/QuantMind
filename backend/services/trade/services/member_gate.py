"""
持仓实时行情会员门控

定义谁能用: QuantDB 付费会员在期 = user_subscriptions 存在 status='active'
且 end_date > now() 且对应套餐价格 > 0(subscription_plans.price) 的记录。
免费套餐(price=0)不计入付费会员; 过期/取消/未订阅一律拒绝。

应用位置:
- 路由层: tdx_quote_feed 全部 4 个端点走 require_paid_member 依赖, 非会员 403
- Feed 层: 账户非会员时停止拉行情/写 Redis/写 tick/触发提醒, 周期性重试

查询结果缓存 Redis 60s, 避免每 3s 循环打库; 查询异常按非会员处理(收费功能 fail-closed)。
"""
import logging
import time

logger = logging.getLogger(__name__)

MEMBER_CACHE_KEY = "trade:member_gate:{tenant_id}:{user_id}"
MEMBER_CACHE_TTL = 60  # 秒
MEMBER_RECHECK_SECONDS = 600  # Feed 循环内重新核验会员的间隔
MEMBER_RETRY_SECONDS = 60  # 非会员时的重试间隔


async def is_paid_member(tenant_id: str, user_id: str) -> bool:
    """账户是否为 QuantDB 付费会员在期（Redis 缓存 60s，失败按非会员处理）。"""
    try:
        from backend.services.trade_shared.redis_client import get_redis

        key = MEMBER_CACHE_KEY.format(tenant_id=tenant_id, user_id=user_id)
        cached = get_redis().get(key)
        if cached is not None:
            return bool(cached)

        allowed = await _query_paid_member(tenant_id, user_id)
        try:
            get_redis().set(key, 1 if allowed else 0, ttl=MEMBER_CACHE_TTL)
        except Exception as exc:
            logger.warning("[MemberGate] 会员标记写缓存失败: %s", exc)
        return allowed
    except Exception as exc:
        logger.warning("[MemberGate] 会员查询失败，按非会员处理(%s/%s): %s", tenant_id, user_id, exc)
        return False


async def _query_paid_member(tenant_id: str, user_id: str) -> bool:
    """直查 PG: 在期付费订阅存在即放行（与 api 服务订阅系统同库同表）。"""
    from sqlalchemy import text

    from backend.shared.database_manager_v2 import get_session

    sql = text(
        """
        SELECT us.id
        FROM user_subscriptions us
        JOIN subscription_plans sp ON sp.id = us.plan_id
        WHERE us.tenant_id = :tenant_id
          AND us.user_id = :user_id
          AND us.status = 'active'
          AND us.end_date > now()
          AND sp.price > 0
        LIMIT 1
        """
    )
    async with get_session(read_only=True) as db:
        row = (await db.execute(sql, {"tenant_id": tenant_id, "user_id": user_id})).first()
    return row is not None


async def member_gate_status(tenant_id: str, user_id: str) -> dict:
    """会员门控状态（status 端点展示用）。"""
    return {
        "enabled": True,
        "allowed": await is_paid_member(tenant_id, user_id),
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
