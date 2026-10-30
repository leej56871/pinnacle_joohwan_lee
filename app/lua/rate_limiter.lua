-- KEYS[1]: Rate limit key (e.g., ratelimit:{client_id})
-- ARGV[1]: Current UNIX timestamp in milliseconds
-- ARGV[2]: Window size in milliseconds (e.g., 60000 for 1 minute)
-- ARGV[3]: Max requests allowed in one Window Size
-- ARGV[4]: Unique request identifier to prevent collision

-- Sliding Window Rate Limiter via Sorted Set
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local request_id = ARGV[4]

-- Fallback: Ensure member is never nil (prevent Lua argument type crash)
if not request_id or request_id == '' then
    request_id = tostring(now) .. ':' .. tostring(redis.call('INCR', key .. ':seq'))
end

local clear_before = now - window

-- 1. Remove expired entries outside the sliding window
redis.call('ZREMRANGEBYSCORE', key, '-inf', clear_before)

-- 2. Count active requests within the window
local current_requests = redis.call('ZCARD', key)

if current_requests < limit then
    -- Quota available: Record request timestamp and set window expiration
    redis.call('ZADD', key, now, request_id)
    redis.call('PEXPIRE', key, window)
    redis.call('PEXPIRE', key .. ':seq', window)
    
    local remaining = limit - current_requests - 1
    return {1, remaining, 0}
else
    -- Quota exceeded: Query oldest request to calculate retry duration
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after_ms = 0
    if oldest and #oldest >= 2 then
        local oldest_time = tonumber(oldest[2])
        retry_after_ms = math.max(0, oldest_time + window - now)
    end
    
    return {0, 0, retry_after_ms}
end