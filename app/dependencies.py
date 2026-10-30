import hashlib
import json
import time
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.models import ApiClient
from app.redis_client import get_lua_manager, get_redis_client


@dataclass(frozen=True)
class IdempotencyContext:
    key: str
    redis_key: str
    payload_hash: str
    action: str
    cached_payload: dict[str, Any] | None


def calculate_payload_hash(payload_data: dict[str, Any]) -> str:
    """
    Computes a deterministic SHA-256 digest of the request payload.
    Keys are sorted to prevent false mismatches caused by JSON field ordering.
    """
    serialized = json.dumps(payload_data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def get_current_client(
    x_api_key: Annotated[str | None, Header(description="Tenant API Key")] = None,
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis_client),
) -> ApiClient:
    """
    Authenticates the client via X-API-Key header.
    Utilizes Redis as an L2 cache to prevent database connection pool exhaustion under high RPS.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key header 'X-API-Key' is missing",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    clean_api_key = x_api_key.strip()
    cache_key = f"cache:client:{clean_api_key}"

    # 1. L2 Cache Lookup
    cached_client_json = await redis.get(cache_key)
    if cached_client_json:
        data = json.loads(cached_client_json)
        return ApiClient(
            id=data["id"],
            client_name=data["client_name"],
            api_key=data["api_key"],
            rate_limit_rpm=data["rate_limit_rpm"],
            is_active=data["is_active"],
        )

    # 2. Database Fallback
    stmt = select(ApiClient).where(ApiClient.api_key == clean_api_key)
    result = await db.execute(stmt)
    client = result.scalar_one_or_none()

    if not client:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key provided",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if not client.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client tenant account is suspended or inactive",
        )

    # 3. Populate L2 Cache with 300 seconds TTL
    client_payload = {
        "id": client.id,
        "client_name": client.client_name,
        "api_key": client.api_key,
        "rate_limit_rpm": client.rate_limit_rpm,
        "is_active": client.is_active,
    }
    await redis.set(cache_key, json.dumps(client_payload), ex=300)

    return client


async def rate_limiter_dependency(
    response: Response,
    client: ApiClient = Depends(get_current_client),
    redis: Redis = Depends(get_redis_client),
) -> None:
    """
    Evaluates sliding window rate limits via precompiled Redis Lua script.
    Appends rate limit headers to the response, or raises 429 when quota is exceeded.
    """
    try:
        active_lua_manager = get_lua_manager()
    except RuntimeError as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Rate limiter Lua script is not initialized on the server",
        ) from err

    if not active_lua_manager.rate_limiter_sha:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Rate limiter Lua script is not initialized on the server",
        )

    redis_key = f"ratelimit:{client.id}"
    now_ms = int(time.time() * 1000)
    window_ms = 60000
    limit = client.rate_limit_rpm
    request_probe_id = f"{now_ms}:{time.monotonic_ns()}"

    eval_result = await redis.evalsha(
        active_lua_manager.rate_limiter_sha,
        1,
        redis_key,
        now_ms,
        window_ms,
        limit,
        request_probe_id,  # Passes ARGV[4] to ensure uniqueness and non-nil argument
    )

    allowed = int(eval_result[0])
    remaining = max(0, int(eval_result[1]))
    retry_after_ms = int(eval_result[2])
    retry_after_sec = max(1, int(retry_after_ms / 1000)) if retry_after_ms > 0 else 0

    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(retry_after_sec)

    if allowed == 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Maximum {limit} requests per minute allowed.",
            headers={
                "Retry-After": str(retry_after_sec),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(retry_after_sec),
            },
        )


async def idempotency_guard(
    request: Request,
    idempotency_key: Annotated[
        str | None,
        Header(
            description="Unique client-generated idempotency key (UUID)",
            alias="Idempotency-Key",
        ),
    ] = None,
    client: ApiClient = Depends(get_current_client),
    redis: Redis = Depends(get_redis_client),
) -> IdempotencyContext:
    """
    Evaluates idempotency state in Redis before the request reaches the controller.
    Acquires an atomic distributed lock or extracts cached response payload.
    """
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Header 'Idempotency-Key' is mandatory for transactional operations",
        )

    try:
        active_lua_manager = get_lua_manager()
    except RuntimeError as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Idempotency Lua script is not initialized on the server",
        ) from err

    if not active_lua_manager.idempotency_sha:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Idempotency Lua script is not initialized on the server",
        )

    clean_key = idempotency_key.strip()
    redis_key = f"idempotency:{client.id}:{clean_key}"

    body_bytes = await request.body()
    try:
        body_json = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except json.JSONDecodeError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON body content",
        ) from err

    payload_hash = calculate_payload_hash(body_json)

    eval_result = await redis.evalsha(
        active_lua_manager.idempotency_sha,
        1,
        redis_key,
        settings.LOCK_TIMEOUT_SECONDS,
        payload_hash,
    )

    action_status = str(eval_result[0])
    cached_payload_str = str(eval_result[1])

    if action_status == "PAYLOAD_MISMATCH":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency key reuse detected with differing payload contents",
        )

    if action_status == "CONFLICT":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A concurrent transaction with identical idempotency key is currently executing",
        )

    cached_payload = None
    if action_status == "COMPLETED" and cached_payload_str:
        cached_payload = json.loads(cached_payload_str)

    return IdempotencyContext(
        key=clean_key,
        redis_key=redis_key,
        payload_hash=payload_hash,
        action=action_status,
        cached_payload=cached_payload,
    )