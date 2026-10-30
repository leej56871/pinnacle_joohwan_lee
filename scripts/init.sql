-- Initial database setup

-- Table: Clients and API Keys
CREATE TABLE IF NOT EXISTS api_clients (
    id VARCHAR(64) PRIMARY KEY,
    client_name VARCHAR(100) NOT NULL,
    api_key VARCHAR(128) NOT NULL UNIQUE,
    rate_limit_rpm INT NOT NULL DEFAULT 60,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Table: Processed Transactions (Payment Simulation)
-- PostgreSQL 13+ includes gen_random_uuid() natively without external extensions
CREATE TABLE IF NOT EXISTS payment_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id VARCHAR(64) NOT NULL REFERENCES api_clients(id),
    idempotency_key VARCHAR(128) NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    order_id VARCHAR(100) NOT NULL,
    status VARCHAR(32) NOT NULL,
    response_payload JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_client_idempotency UNIQUE (client_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_transactions_client_key ON payment_transactions (client_id, idempotency_key);

-- Seed Initial Test Clients
INSERT INTO api_clients (id, client_name, api_key, rate_limit_rpm, is_active)
VALUES 
    ('client_tier_standard', 'Standard Tier Partner', 'test_key_standard_12345', 10, TRUE),
    ('client_tier_enterprise', 'Enterprise Tier Partner', 'test_key_enterprise_67890', 100, TRUE)
ON CONFLICT (id) DO NOTHING;