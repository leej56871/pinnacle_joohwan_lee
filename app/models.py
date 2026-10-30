import uuid
from datetime import datetime
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from app.database import Base


class ApiClient(Base):
    __tablename__ = "api_clients"

    id = Column(String(64), primary_key=True)
    client_name = Column(String(100), nullable=False)
    api_key = Column(String(128), unique=True, nullable=False, index=True)
    rate_limit_rpm = Column(Integer, nullable=False, default=60)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    transactions = relationship("PaymentTransaction", back_populates="client")


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id = Column(String(64), ForeignKey("api_clients.id"), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    order_id = Column(String(100), nullable=False)
    status = Column(String(32), nullable=False)
    response_payload = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    client = relationship("ApiClient", back_populates="transactions")


    __table_args__ = (
        UniqueConstraint(
            "client_id", "idempotency_key", name="uq_client_idempotency"
        ),
        Index("idx_transactions_client_key", "client_id", "idempotency_key"),
    )