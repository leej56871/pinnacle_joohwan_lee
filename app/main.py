from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import engine, get_db_session
from app.dependencies import (
    IdempotencyContext,
    get_current_client,
    idempotency_guard,
    rate_limiter_dependency,
)
from app.models import ApiClient, PaymentTransaction
from app.redis_client import get_redis_client, init_redis_scripts, redis_pool
from app.schemas import ErrorResponse, PaymentRequest, PaymentResponse

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Initializes Lua script cache on startup and gracefully tears down connection pools on shutdown.
    """
    await init_redis_scripts()
    yield
    await redis_pool.disconnect()
    await engine.dispose()


app = FastAPI(
    title="Pinnacle Distributed Transaction & Payment Gateway",
    version="1.0.0",
    description="High-reliability payment backend with atomic distributed idempotency and sliding-window rate limiting.",
    lifespan=lifespan,
)


@app.get("/health", tags=["Monitoring"])
async def health_check():
    """
    Health check probe for orchestrators and load balancers.
    """
    return {"status": "healthy", "service": "payment-gateway"}


@app.post(
    "/api/v1/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Transactions"],
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
)
async def process_payment(
    payload: PaymentRequest,
    response: Response,
    client: ApiClient = Depends(get_current_client),
    _: None = Depends(rate_limiter_dependency),
    idemp_ctx: IdempotencyContext = Depends(idempotency_guard),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis_client),
):
    """
    Processes financial transactions with strict idempotency and distributed concurrency guards.
    Dependency order ensures rate limiting executes BEFORE acquiring idempotency locks.
    """
    # 1. Fast Path: Instant Idempotent Replay from Redis Cache
    if idemp_ctx.action == "COMPLETED" and idemp_ctx.cached_payload:
        response.headers["X-Cache-Lookup"] = "HIT"
        cached_data = dict(idemp_ctx.cached_payload)
        cached_data["idempotent_replay"] = True
        return JSONResponse(status_code=status.HTTP_200_OK, content=cached_data)

    # 2. Slow Path: Execute Settlement (Lock already acquired by idempotency_guard)
    try:
        transaction = PaymentTransaction(
            client_id=client.id,
            idempotency_key=idemp_ctx.key,
            amount=payload.amount,
            currency=payload.currency,
            order_id=payload.order_id,
            status="SETTLED",
            response_payload={},
        )

        db.add(transaction)
        await db.flush()

        tx_timestamp = (
            getattr(transaction, "created_at", None)
            or getattr(transaction, "processed_at", None)
            or datetime.now(timezone.utc)
        )

        response_data = PaymentResponse(
            transaction_id=transaction.id,
            client_id=transaction.client_id,
            idempotency_key=transaction.idempotency_key,
            amount=transaction.amount,
            currency=transaction.currency,
            order_id=transaction.order_id,
            status=transaction.status,
            processed_at=tx_timestamp,
            idempotent_replay=False,
        )

        response_dict = response_data.model_dump(mode="json")
        transaction.response_payload = response_dict

        await db.commit()
        await db.refresh(transaction)

        # Transition Redis state from IN_PROGRESS lock to COMPLETED replay cache
        await redis.hset(
            idemp_ctx.redis_key,
            mapping={
                "status": "COMPLETED",
                "response": json.dumps(response_dict),
            },
        )
        await redis.expire(idemp_ctx.redis_key, settings.IDEMPOTENCY_TTL_SECONDS)

        response.headers["X-Cache-Lookup"] = "MISS"
        return response_data

    except IntegrityError as err:
        await db.rollback()
        await redis.delete(idemp_ctx.redis_key)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Database integrity constraint violated. Duplicate transaction detected.",
        ) from err

    except Exception as err:
        await db.rollback()
        await redis.delete(idemp_ctx.redis_key)
        logger.exception("Unhandled settlement failure for key=%s", idemp_ctx.key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal payment settlement error occurred.",
        ) from err