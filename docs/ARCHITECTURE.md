# Architecture

## Why WhatsApp

SMS in the US requires A2P 10DLC registration for local numbers (takes days, requires business registration). Twilio's WhatsApp works immediately with no registration for small pilots. Production is registered on A2P 10DLC for SMS fallback. See [docs/MESSAGING.md](MESSAGING.md).

## Two-process design

**`src/nudge.py`** — ephemeral cron job (Railway, every 7 min):
- Detects upcoming matches (5–45 min window) per active user's registered team
- Sends pre-match prompts and writes `predictions` + `sent_matches` rows to Supabase
- Settles finished matches: writes to `savings_log`, notifies users of results
- Offers mid-game insurance at halftime (EPL) or innings 6–7 (MLB) when pick is losing
- Sends reminders 15 min before kickoff for users who haven't picked yet

**`webhook/app.py`** — always-on Flask app (Railway):
- `POST /whatsapp` and `POST /sms` — Twilio inbound handlers; validates picks, writes to Supabase
- Web frontend API — see [docs/WEBHOOK_API.md](WEBHOOK_API.md) for all routes

## Shared `akrue/` package

Common helpers used by both processes live in `akrue/`:
- `akrue/config.py` — SPORT_CONFIG, SPORT_TEAM_IDS, cap constants
- `akrue/env.py` — centralised env reads
- `akrue/messaging.py` — `send_message`, `normalise_phone`, `get_user_channel`
- `akrue/amounts.py` — `calculate_amounts`, `get_week_savings`, `get_week_bounds`, `current_week`
- `akrue/supabase_client.py` — `get_client()`

## Sport-agnostic design

Adding a new sport: see [docs/SPORT_ADAPTERS.md](SPORT_ADAPTERS.md).

## Match ID format

`{sport}_{api_match_id}_{team_id}` (e.g. `epl_491827_64`, `mlb_778834_111`). The first two segments (`raw_match_id`) are used when querying the sports API; the full ID is the unique Supabase key.

## Savings amount logic

- `correct_amount = round(weekly_bankroll / bets_per_week)`
- `wrong_amount = round(correct_amount × 1.4)`
- Weekly cap = `weekly_bankroll × weekly_cap_multiplier` (default 1.25×, max 2×)
- Amounts calculated at prompt time, stored in the `predictions` row; webhook uses stored amounts
