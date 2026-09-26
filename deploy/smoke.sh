#!/usr/bin/env bash
# Smoke test of the whole stack in replay mode (run from the repository root; used by CI):
#   1. web, worker and caddy start and /healthz turns healthy
#   2. the dashboard needs a login, /healthz does not, a wrong password is refused
#   3. caddy refuses to start with no users unless ALLOW_NO_AUTH=1 (fails closed)
#   4. a crashed worker (its process killed from outside, like an out-of-memory kill) restarts and
#      /healthz recovers. `docker kill` does not count: Docker treats it as a deliberate stop.
set -euo pipefail

COMPOSE="docker compose -f docker-compose.yml -f deploy/docker-compose.ci.yml"
URL="http://localhost"

fail() {
	echo "SMOKE TEST FAILED: $*" >&2
	$COMPOSE ps || true
	$COMPOSE logs --tail=60 || true
	exit 1
}

wait_healthy() {
	local what="$1" tries="${2:-60}"
	for _ in $(seq 1 "$tries"); do
		if curl -fsS "$URL/healthz" >/dev/null 2>&1; then
			echo "ok: $what"
			return 0
		fi
		sleep 2
	done
	fail "$what: /healthz did not become healthy"
}

status_of() {
	curl -s -o /dev/null -w '%{http_code}' "$@"
}

if [ -e .env ] && [ "${CI:-}" != "true" ]; then
	echo "Refusing to run: this test writes .env and would overwrite yours. It is meant for CI." >&2
	exit 2
fi

trap '$COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true' EXIT

cat >.env <<'ENVEOF'
NANSEN_API_KEY=ci-test-key
NANSEN_MODE=replay
DAILY_CREDIT_BUDGET=3000
CHAINS=solana,base,bnb,robinhood
DATABASE_URL=sqlite:////data/lp-radar.db
LOG_LEVEL=INFO
ALLOW_NO_AUTH=1
ENVEOF

echo "== 1. start the stack"
$COMPOSE up -d --build
wait_healthy "stack is healthy (worker heartbeat seen through web)"

echo "== 2. login"
PASSWORD="$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 40)"
HASH="$(docker run --rm caddy:2 caddy hash-password --plaintext "$PASSWORD")"
USERS_B64="$(printf 'tester %s\n' "$HASH" | base64 -w0)"
sed -i '/^ALLOW_NO_AUTH=/d' .env
echo "DASHBOARD_USERS_B64=$USERS_B64" >>.env
$COMPOSE up -d --force-recreate caddy
wait_healthy "healthz needs no login"

[ "$(status_of "$URL/")" = "401" ] || fail "dashboard without a login should be 401"
[ "$(status_of -u "tester:wrong-password" "$URL/")" = "401" ] || fail "wrong password should be 401"
[ "$(status_of -u "tester:$PASSWORD" "$URL/")" = "200" ] || fail "right password should be 200"
[ "$(status_of -u "tester:$PASSWORD" "$URL/status")" = "200" ] || fail "/status with login should be 200"
echo "ok: login enforced, /healthz open"

echo "== 3. fails closed"
sed -i '/^DASHBOARD_USERS_B64=/d' .env
if $COMPOSE run --rm --no-deps -T caddy >/dev/null 2>&1; then
	fail "caddy started without users and without ALLOW_NO_AUTH"
fi
echo "ok: caddy refuses to serve without a login"
echo "DASHBOARD_USERS_B64=$USERS_B64" >>.env
$COMPOSE up -d --force-recreate caddy

echo "== 4. worker restart"
worker="$($COMPOSE ps -q worker)"
[ -n "$worker" ] || fail "no worker container"
pid="$(docker inspect -f '{{.State.Pid}}' "$worker")"
[ "$pid" -gt 1 ] || fail "could not find the worker process"
sudo kill -9 "$pid"
for _ in $(seq 1 30); do
	if [ -n "$($COMPOSE ps --status running -q worker)" ]; then
		break
	fi
	sleep 2
done
[ -n "$($COMPOSE ps --status running -q worker)" ] || fail "worker did not restart after being killed"
wait_healthy "healthz recovered after the worker was killed"

echo "SMOKE TEST PASSED"
