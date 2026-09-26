# Worker, storage and alerts (Phase 6)

`python -m app.worker` (`make worker`) runs the polling loop. It is the only writer to the SQLite database
(WAL mode); the web app only reads. Tables are created with `create_all` on start (no migration tool yet).

## Loop
Every 5 seconds a tick: writes the heartbeat, then, unless paused or backing off,
1. **Discovery.** Feed A every `discovery.poll_seconds`, Feed B every `discovery.smart_money_poll_seconds`
   (`docs/discovery.md`). New tokens (no snapshot yet) join a pending list.
2. **Deep analysis** of pending tokens, largest market cap first (`docs/bundle.md`, `docs/smart-money.md`,
   `docs/scoring.md`), stored as a snapshot with its clusters.
3. **Re-evaluation** of the watchlist (latest verdict not AVOID, deployed less than `discovery.max_age_hours`
   ago) once its last snapshot is older than `monitor.reeval_seconds`, oldest first.
4. Persists today's credit total.

## Credit pacing
Everything, discovery included, draws from an hourly share of `DAILY_CREDIT_BUDGET / 24`. A step only runs
when the share left this hour covers its estimated cost (`budget.estimated_analysis_credits` 45,
`estimated_reeval_credits` 15; feeds 1 and 6). `budget.max_deep_analyses_per_hour` is a further ceiling.
The client's own daily cap remains the hard stop: when it fires the worker pauses until the next UTC day but
keeps writing the heartbeat. Today's total is stored in `credit_usage` and restored on restart.

Rough costs: a first analysis is about 43 credits (bundle: 1 trades + up to 30 funder lookups + 5 holders;
smart money: 1 trades + 5 tiers + 1 token info), a re-evaluation about 13 once funder lookups are cached
(the cache is in memory, so a restart pays for them again). Feeds cost about 54 credits an hour at the default
cadence. At `DAILY_CREDIT_BUDGET=3000` (125 an hour) that leaves room for roughly one analysis an hour; a
budget below about 1,100 a day cannot afford any (the worker logs a warning at start). Raise the budget, or
slow the feeds, to analyse more.

## Failures
- One failing token is logged and retried up to 3 times, then dropped; it never stops the loop.
- A Nansen outage (5xx, 429, transport error, or a response that does not parse) backs off 30s, 60s, ... up to
  300s and resets on the next success.

## Alerts
`monitor.detect_events` compares a token's new snapshot with its previous one:
- **new_green**: the verdict became GREEN (including a first analysis that is GREEN).
- **verdict_downgrade**: GREEN to WATCH, WATCH to AVOID or GREEN to AVOID.
- **bundle_distributing**: the bundle status became `distributing` (not on a first snapshot).
- **sm_selling**: smart-money net flow turned negative (not on a first snapshot).

The same event type is not re-sent for a token within `alerts.cooldown_minutes` (60); sent alerts are stored
in `alerts_sent`, so a restart cannot repeat one. A send that fails is logged and not recorded; because events
are detected between consecutive snapshots, a failed alert is not retried.

Telegram messages carry chain, symbol, address, verdict, the top three reasons and, when `DASHBOARD_BASE_URL`
is set, a dashboard link. Token names and reasons are HTML-escaped. Without `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` alerts are only logged. The bot token is never logged: httpx request logging is silenced and a
redaction filter strips `bot<id>:<token>` from every log line.

## Health
The web app's `/healthz` returns 503 when the worker heartbeat is missing or older than 5 minutes, or the
database cannot be read.

## Soak test
`tests/unit/test_worker.py` simulates ten minutes on a fake clock against a scripted Nansen (no credits, no
network). `make soak` runs the same worker for ten real minutes; it is skipped by default. File-based replay
fixtures for every request body would need live recordings (credits, and stored Nansen data), so the scripted
client is used instead.
