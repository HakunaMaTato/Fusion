#!/bin/sh
# Builds Caddy's basic-auth block from DASHBOARD_USERS_B64, then starts Caddy.
#
# DASHBOARD_USERS_B64 is base64 of lines "username bcrypt-hash". Base64 keeps the `$` characters of a
# bcrypt hash out of .env and compose interpolation. Fails closed: with no users it refuses to start
# unless ALLOW_NO_AUTH=1 (local tests only).
set -eu

mkdir -p /run/caddy
auth=/run/caddy/auth.caddy

if [ -n "${DASHBOARD_USERS_B64:-}" ]; then
	decoded=$(printf '%s' "$DASHBOARD_USERS_B64" | base64 -d)
	{
		echo "basic_auth {"
		printf '%s\n' "$decoded" | while read -r name hash; do
			[ -n "$name" ] && [ -n "$hash" ] || continue
			echo "	$name $hash"
		done
		echo "}"
	} >"$auth"
	if ! grep -q '\$2' "$auth"; then
		echo "DASHBOARD_USERS_B64 holds no bcrypt hashes; refusing to start." >&2
		exit 1
	fi
elif [ "${ALLOW_NO_AUTH:-}" = "1" ]; then
	echo "WARNING: no dashboard users configured; serving WITHOUT a login (ALLOW_NO_AUTH=1)." >&2
	: >"$auth"
else
	echo "DASHBOARD_USERS_B64 is not set. Refusing to serve the dashboard without a login." >&2
	echo "Set it (see docs/deploy.md), or set ALLOW_NO_AUTH=1 for a local test." >&2
	exit 1
fi

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
