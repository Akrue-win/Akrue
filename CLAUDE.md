# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**Akrue** (formerly Project Free Kick) is a gamified savings platform delivered entirely via WhatsApp. Sports fans make predictions on upcoming matches; correct picks earn larger savings deposits than wrong picks. The system is sport-agnostic and currently supports EPL soccer and MLB baseball.

Core loop:
1. GitHub Actions runs `src/nudge.py` on a schedule — it detects matches starting in 5–45 minutes and sends WhatsApp prompts via Twilio
2. Users reply with their prediction (WIN / DRAW / LOSS); the Railway webhook (`webhook/app.py`) captures these replies and writes them to Supabase
3. After each match, `nudge.py` settles pending bets, calculates savings amounts, and notifies users of results

## Commands

### Local development setup

**Prerequisites:** Python 3.12+

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up environment variables
cp env.example .env
# Then edit .env with your Twilio, Football API, and Supabase credentials

# 3. (Optional) Set up webhook locally
cd webhook
pip install -r requirements.txt
```

### Running the nudge script

```bash
# Test mode: sends a confirmation WhatsApp to all active users
python src/nudge.py --test

# Full nudge cycle: match detection, prompts, settlement, reminders
python src/nudge.py
```

### Running the webhook locally

```bash
cd webhook
# Development server (single-threaded Flask)
python app.py

# Production-like (gunicorn, as Railway runs it)
pip install gunicorn
gunicorn app:app --bind 0.0.0.0:5000
```

### Running the web frontend

The frontend is plain HTML/CSS/JS (no build step required). For local testing:
```bash
# Serve static files on localhost:8000 (Python built-in)
python -m http.server 8000
# or with npm
npx serve --cors
```

Then visit `http://localhost:8000/index.html` and ensure the webhook (`webhook/app.py`) is running so the frontend can reach the API endpoints.

### Deploying the webhook to Railway

The `webhook/` directory deploys to Railway automatically via the `Dockerfile`:
1. Connect your GitHub repo to Railway
2. Set the **Root Directory** to `webhook/` in service settings
3. Add environment variables: `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `FOOTBALL_API_KEY`
4. Railway runs `gunicorn app:app` (see `Procfile` and `railway.json`)
5. Get your public URL from Railway dashboard → **Settings → Domains**
6. Configure Twilio webhook to point to: `https://YOUR-RAILWAY-URL/whatsapp`

### Triggering the nudge script via GitHub Actions
Go to Actions → "Akrue — Match Bet Scheduler" → Run workflow (manual `workflow_dispatch` only).

## Architecture

### Two separate processes

**`src/nudge.py`** — runs as a GitHub Actions job (ephemeral, scheduled/manual):
- Checks each active user's registered team(s) for upcoming matches in the 5–45 minute window
- Sends pre-match WhatsApp prompts, writes a `predictions` row and a `sent_matches` row per user per match
- Checks `pending_matches` for finished games and settles bets (writes to `savings_log`, notifies users)
- Offers in-game insurance at halftime (EPL) or innings 6–7 (MLB) when the user's pick is losing
- Sends reminders 15 minutes before kickoff for users who haven't picked yet

**`webhook/app.py`** — always-on Flask app on Railway:
- `POST /whatsapp` — Twilio webhook; receives user replies, validates picks against the sport's allowed options, writes the prediction to Supabase
- `POST /place-bet` — web app endpoint for placing bets via the browser UI
- `GET /user`, `GET /leaderboard`, `GET /pending-bets`, `GET /bet-history`, `GET /savings-history` — data endpoints for the web front-end
- `POST /update-user`, `POST /signup` — profile management
- `GET /live-score`, `GET /live-score-epl` — live score polling for the web app

### Web front-end (`web/`, `index.html`)
Static HTML/CSS/JS pages hosted as-is (no build step). They call the Railway webhook API directly:
- `index.html` — landing page / sign-in
- `web/app.html` — main user dashboard (pending bets, savings graph, bet history, settings)
- `web/signup.html` — new user registration flow
- `web/leaderboard.html` — group leaderboard

### Supabase schema (tables)
| Table | Purpose |
|---|---|
| `users` | Active users: phone, name, `epl_team`, `mlb_team`, `weekly_bankroll`, `bets_per_week`, `weekly_cap_multiplier`, `group_code`, `status` |
| `predictions` | One row per user per match: `match_id`, `user_phone`, `prediction`, `status` (`pending`/`locked`/`insured`), `correct_amount`, `wrong_amount`, `reminder_sent` |
| `pending_matches` | Matches that have been nudged but not yet settled: `match_id`, `sport`, `team_id`, `team_name`, `opponent`, `users[]`, `kickoff_utc`, `settled` |
| `sent_matches` | Deduplication log — prevents sending the same match prompt twice |
| `savings_log` | Final savings record per settled bet: `date`, `user_phone`, `amount`, `trigger`, `match_id`, `week`, `sport` |
| `insurance_offers` | Tracks mid-game insurance offers: `match_id`, `user_phone`, `amount`, `accepted` |

### Sport-agnostic design
Adding a new sport requires:
1. A new entry in `SPORT_CONFIG` (both `nudge.py` and `webhook/app.py`)
2. A new entry in `SPORT_TEAM_IDS`
3. API fetch functions (`get_X_upcoming`, `get_X_recent`, `result_for_X`, etc.)
4. A new entry in `SPORT_API_HANDLERS`

### Match ID format
Match IDs follow the pattern `{sport}_{api_match_id}_{team_id}` (e.g. `epl_491827_64`, `mlb_778834_111`). The `raw_match_id` (first two segments) is used when querying the sports API; the full ID (including team) is used as a unique Supabase key.

### Savings amount logic
- `correct_amount = round(weekly_bankroll / bets_per_week)`
- `wrong_amount = round(correct_amount * 1.4)`  
- A weekly cap (`weekly_bankroll × weekly_cap_multiplier`, max 2×) limits total weekly savings
- Amounts are calculated at prompt time and stored in the `predictions` row; the webhook uses the stored amounts, not a recalculation

## Environment Variables 

All credentials are loaded from environment variables (never hardcoded). See `env.example` for the full list. Required for `src/nudge.py`:

| Variable | Source |
|---|---|
| `TWILIO_ACCOUNT_SID` | Twilio dashboard |
| `TWILIO_AUTH_TOKEN` | Twilio dashboard |
| `TWILIO_FROM_NUMBER` | `whatsapp:+1...` format |
| `FOOTBALL_API_KEY` | football-data.org |
| `SUPABASE_URL` | Supabase project settings |
| `SUPABASE_SECRET_KEY` | Supabase service role key |

The webhook (`webhook/app.py`) needs `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, and `FOOTBALL_API_KEY`. These are set as Railway environment variables.

GitHub Actions secrets store all of the above for CI runs.
# Claude Working Preferences

## Communication Style

- **Ask before doing anything heavy.** If a task will require generating a full file, significant refactoring, or multiple API calls, confirm the approach first.
- **Targeted fixes only.** When debugging or editing, suggest specific replacements or patches — not full rewrites — unless explicitly asked for a new file.
- **Clarifying questions over assumptions.** If the intent is ambiguous, ask. Don't guess and generate.
- **Be concise.** Short, direct answers. No padding, no over-explaining obvious things.
- **Summarize sessions on request.** At the end of a working session, provide a clean bullet-point summary to carry into the next chat.

## Code Editing Rules

- Default to **showing the specific lines to change**, not the full file.
- Only output a complete file when explicitly asked ("give me the full file", "output a new version").
- When multiple small changes are needed, list them clearly so they can be applied one by one.
- Prefer **surgical patches** — show old code, then new code.

## Tech Stack

- **Backend:** Python 3.12 (Twilio SDK, supabase-py, Flask)
- **Frontend:** Plain HTML/CSS/JavaScript (no build step, single-page app calls REST API)
- **Database:** Supabase (PostgreSQL)
- **Messaging:** Twilio (WhatsApp + SMS)
- **CI/CD:** GitHub Actions (scheduled and manual workflows)
- **Hosting:** Railway (webhook), GitHub Actions (nudge script)

## Token / Context Efficiency

- Flag when context window is getting tight.
- Don't re-read files that are already in context.
- Don't generate boilerplate unless asked.