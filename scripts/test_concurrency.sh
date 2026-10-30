#!/usr/bin/env bash
set -eo pipefail

BASE_URL="http://localhost:8000"
STANDARD_API_KEY="test_key_standard_12345"
ENTERPRISE_API_KEY="test_key_enterprise_67890"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

RACE_LOG="/tmp/race_results_$$.log"
trap 'rm -f "$RACE_LOG"' EXIT

echo -e "${BLUE}================================================================${NC}"
echo -e "${BLUE}  Pinnacle Backend Concurrency & Reliability Verification Suite  ${NC}"
echo -e "${BLUE}================================================================${NC}"

# Check Service Health
echo -e "\n${YELLOW}[Test 0] Checking System Health...${NC}"
HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/health")
if [ "$HEALTH_STATUS" -eq 200 ]; then
    echo -e "${GREEN}✓ System is healthy and reachable.${NC}"
else
    echo -e "${RED}✗ System unreachable. HTTP Status: ${HEALTH_STATUS}${NC}"
    exit 1
fi

IDEMP_KEY="test-key-$(date +%s)-$RANDOM"
PAYLOAD_ORIGINAL='{"order_id":"ORD-HK-101","amount":"1500.50","currency":"HKD"}'
PAYLOAD_TAMPERED='{"order_id":"ORD-HK-101","amount":"9999.00","currency":"HKD"}'

# Test 1: First Transaction (Uses Enterprise Key to ensure repeatability across runs)
echo -e "\n${YELLOW}[Test 1] Executing Initial Transaction with Key: ${IDEMP_KEY}...${NC}"
RES_1=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST "${BASE_URL}/api/v1/payments" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${ENTERPRISE_API_KEY}" \
  -H "Idempotency-Key: ${IDEMP_KEY}" \
  -d "${PAYLOAD_ORIGINAL}")

HTTP_CODE_1=$(echo "$RES_1" | grep "HTTP_CODE:" | cut -d':' -f2)
BODY_1=$(echo "$RES_1" | sed '/HTTP_CODE:/d')

if [ "$HTTP_CODE_1" -eq 201 ]; then
    echo -e "${GREEN}✓ Initial transaction successful (HTTP 201 Created).${NC}"
    echo "Response: ${BODY_1}"
else
    echo -e "${RED}✗ Initial transaction failed. HTTP Code: ${HTTP_CODE_1}${NC}"
    echo "Response: ${BODY_1}"
    exit 1
fi

# Test 2: Idempotent Replay (Should Return 200 OK with identical payload and flag)
echo -e "\n${YELLOW}[Test 2] Replaying Transaction with identical Idempotency-Key...${NC}"
RES_2=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST "${BASE_URL}/api/v1/payments" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${ENTERPRISE_API_KEY}" \
  -H "Idempotency-Key: ${IDEMP_KEY}" \
  -d "${PAYLOAD_ORIGINAL}")

HTTP_CODE_2=$(echo "$RES_2" | grep "HTTP_CODE:" | cut -d':' -f2)
BODY_2=$(echo "$RES_2" | sed '/HTTP_CODE:/d')

if [ "$HTTP_CODE_2" -eq 200 ]; then
    echo -e "${GREEN}✓ Idempotent replay verified (HTTP 200 OK from Cache).${NC}"
    echo "Response: ${BODY_2}"
else
    echo -e "${RED}✗ Expected HTTP 200 for replay, got: ${HTTP_CODE_2}${NC}"
    echo "Response: ${BODY_2}"
    exit 1
fi

# Test 3: Payload Tampering Attack Detection (Should Return 422 Unprocessable Entity)
echo -e "\n${YELLOW}[Test 3] Tampering Payload under existing Idempotency-Key...${NC}"
RES_3=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST "${BASE_URL}/api/v1/payments" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${ENTERPRISE_API_KEY}" \
  -H "Idempotency-Key: ${IDEMP_KEY}" \
  -d "${PAYLOAD_TAMPERED}")

HTTP_CODE_3=$(echo "$RES_3" | grep "HTTP_CODE:" | cut -d':' -f2)
BODY_3=$(echo "$RES_3" | sed '/HTTP_CODE:/d')

if [ "$HTTP_CODE_3" -eq 422 ]; then
    echo -e "${GREEN}✓ Payload tampering blocked successfully (HTTP 422 Unprocessable Entity).${NC}"
    echo "Response: ${BODY_3}"
else
    echo -e "${RED}✗ Expected HTTP 422 for tampering, got: ${HTTP_CODE_3}${NC}"
    echo "Response: ${BODY_3}"
    exit 1
fi

# Test 4: Atomic Concurrency Race Condition (Multiple simultaneous threads with same key)
RACE_KEY="race-key-$(date +%s)-$RANDOM"
echo -e "\n${YELLOW}[Test 4] Dispatching 5 Concurrent Requests with identical key: ${RACE_KEY}...${NC}"

rm -f "$RACE_LOG"

for i in {1..5}; do
    (
        CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${BASE_URL}/api/v1/payments" \
          -H "Content-Type: application/json" \
          -H "X-API-Key: ${ENTERPRISE_API_KEY}" \
          -H "Idempotency-Key: ${RACE_KEY}" \
          -d "${PAYLOAD_ORIGINAL}")
        echo "$CODE" >> "$RACE_LOG"
    ) &
done

wait

SUCCESS_COUNT=$(grep -c "201" "$RACE_LOG" || true)
CONFLICT_COUNT=$(grep -c "409" "$RACE_LOG" || true)
CACHED_COUNT=$(grep -c "200" "$RACE_LOG" || true)

echo -e "Race Results -> 201 Created: ${SUCCESS_COUNT}, 409 Conflict: ${CONFLICT_COUNT}, 200 Replayed: ${CACHED_COUNT}"

if [ "$SUCCESS_COUNT" -eq 1 ] && [ $((CONFLICT_COUNT + CACHED_COUNT)) -eq 4 ]; then
    echo -e "${GREEN}✓ Concurrency race handled perfectly: Exactly 1 transaction processed, duplicates safely rejected/replayed.${NC}"
else
    echo -e "${RED}✗ Concurrency race condition failed! Multiple transactions processed.${NC}"
    exit 1
fi

# Test 5: Sliding Window Rate Limiting (Standard Tier: 10 RPM quota)
echo -e "\n${YELLOW}[Test 5] Flooding Standard Tier Client (Limit: 10 RPM) with 15 Rapid Requests...${NC}"
RATE_LIMIT_HIT=0

for i in {1..15}; do
    UNIQUE_KEY="ratelimit-probe-$i-$(date +%s%N)"
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${BASE_URL}/api/v1/payments" \
      -H "Content-Type: application/json" \
      -H "X-API-Key: ${STANDARD_API_KEY}" \
      -H "Idempotency-Key: ${UNIQUE_KEY}" \
      -d "{\"order_id\":\"ORD-RL-$i\",\"amount\":\"10.00\",\"currency\":\"HKD\"}")
    
    if [ "$CODE" -eq 429 ]; then
        RATE_LIMIT_HIT=$((RATE_LIMIT_HIT + 1))
    fi
done

if [ "$RATE_LIMIT_HIT" -ge 5 ]; then
    echo -e "${GREEN}✓ Rate limiter verified: Correctly throttled burst traffic with HTTP 429 Too Many Requests (${RATE_LIMIT_HIT}/15 blocked).${NC}"
else
    echo -e "${RED}✗ Rate limiting failed to trigger expected 429 responses. 429 count: ${RATE_LIMIT_HIT}${NC}"
    exit 1
fi

echo -e "\n${BLUE}================================================================${NC}"
echo -e "${GREEN}  All Verification Tests Passed. Service is Production-Ready!   ${NC}"
echo -e "${BLUE}================================================================${NC}"