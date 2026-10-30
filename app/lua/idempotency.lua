-- KEYS[1]: Idempotency key (e.g., idempotency:{client_id}:{idempotency_key})
-- ARGV[1]: Lock TTL in seconds (e.g., 30 during processing)
-- ARGV[2]: Payload hash or signature for payload validation

local key = KEYS[1]
local lock_ttl = tonumber(ARGV[1])
local payload_hash = ARGV[2]

local exists = redis.call('EXISTS', key)

-- Key does not exist, proceed
if exists == 0 then
    redis.call('HSET', key, 'status', 'IN_PROGRESS', 'payload_hash', payload_hash)
    redis.call('EXPIRE', key, lock_ttl)
    return {'ACQUIRED', ''}
end

-- Key exists, Evaluate state
local status = redis.call('HGET', key, 'status')
local saved_hash = redis.call('HGET', key, 'payload_hash')

-- Validate payload tampering
if saved_hash and saved_hash ~= payload_hash then
    return {'PAYLOAD_MISMATCH', ''}
end

if status == 'IN_PROGRESS' then
    -- Concurrent duplicate request is currently executing
    return {'CONFLICT', ''}
elseif status == 'COMPLETED' then
    -- Return cached response payload
    local response_payload = redis.call('HGET', key, 'response')
    return {'COMPLETED', response_payload}
else
    return {'UNKNOWN_STATE', ''}
end