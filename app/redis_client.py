import os
from redis.asyncio import ConnectionPool, Redis

from app.config import settings

# Global connection pool for high-throughput async Redis operations
redis_pool = ConnectionPool.from_url(
    settings.REDIS_URL,
    max_connections=50,
    decode_responses=True,
)


def get_redis_client() -> Redis:
    """Return a Redis client instance bound to the shared connection pool."""
    return Redis(connection_pool=redis_pool)


class LuaScriptManager:
    """Loads and caches Lua scripts for atomic Redis transactions."""

    def __init__(self, redis: Redis):
        self.redis = redis
        self.rate_limiter_sha: str | None = None
        self.idempotency_sha: str | None = None

    async def load_scripts(self) -> None:
        lua_base_path = os.path.join(os.path.dirname(__file__), "lua")

        rate_limit_path = os.path.join(lua_base_path, "rate_limiter.lua")
        with open(rate_limit_path, "r", encoding="utf-8") as f:
            rate_limit_script = f.read()
            self.rate_limiter_sha = await self.redis.script_load(rate_limit_script)

        idempotency_path = os.path.join(lua_base_path, "idempotency.lua")
        with open(idempotency_path, "r", encoding="utf-8") as f:
            idempotency_script = f.read()
            self.idempotency_sha = await self.redis.script_load(idempotency_script)


# Module-level singleton instance holder
lua_manager: LuaScriptManager | None = None


async def init_redis_scripts() -> None:
    """Initialize Redis scripts during application startup lifespan."""
    global lua_manager
    client = get_redis_client()
    manager = LuaScriptManager(client)
    await manager.load_scripts()
    lua_manager = manager


def get_lua_manager() -> LuaScriptManager:
    """
    Safely retrieve the initialized LuaScriptManager singleton at runtime.
    Prevents stale module-level import reference traps across different files.
    """
    global lua_manager
    if lua_manager is None or lua_manager.idempotency_sha is None or lua_manager.rate_limiter_sha is None:
        raise RuntimeError("Idempotency Lua script is not initialized on the server")
    return lua_manager