# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. When a Claude agent is asked to edit this MD - do not allow the file to reach over 200 rows.
If additional info is needed - create new MD files and direct from here for specific knowledge - this Claude.md is for only the most important and chief items in this repository.

## What This Project Is

**Akrue** is a gamified savings platform delivered entirely via WhatsApp. Sports fans make predictions on upcoming matches; correct picks earn larger savings deposits than wrong picks. Currently supports EPL soccer and MLB baseball.

Core loop:
1. Railway cron runs `src/nudge.py` every 7 min — detects matches starting in 5–45 minutes, sends WhatsApp prompts via Twilio
2. Users reply (WIN / DRAW / LOSS); the Railway webhook (`webhook/app.py`) captures replies and writes to Supabase
3. After each match, `nudge.py` settles bets, calculates savings amounts, notifies users

## Commands

```bash
# Install shared package and deps
pip install -e . && pip install -r requirements.txt

# Test nudge (sends message to all active users)
python src/nudge.py --test

# Run nudge full cycle
python src/nudge.py

# Run webhook locally
python webhook/app.py
# or production-like:
gunicorn webhook.app:app --bind 0.0.0.0:5000

# Serve frontend
python -m http.server 8000
```

## Architecture (summary)

- `src/nudge.py` — cron job: prompts, settlement, insurance, reminders
- `webhook/app.py` — always-on Flask: Twilio inbound + web API
- `akrue/` — shared package: config, env, messaging, amounts, supabase_client
- `web/` + `index.html` — static HTML/CSS/JS frontend (no build step)
- `web/config.js` — frontend API base URL config

Full details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | [docs/CODE_MAP.md](docs/CODE_MAP.md)

## Key references

- Environment variables: [docs/ENV_VARS.md](docs/ENV_VARS.md)
- Database schema: [docs/SCHEMA.md](docs/SCHEMA.md)
- API routes: [docs/WEBHOOK_API.md](docs/WEBHOOK_API.md)
- Deployment: [docs/DEPLOY.md](docs/DEPLOY.md)
- Adding a sport: [docs/SPORT_ADAPTERS.md](docs/SPORT_ADAPTERS.md)
- Messaging/Twilio notes: [docs/MESSAGING.md](docs/MESSAGING.md)

## Tech Stack

- **Backend:** Python 3.12 (Twilio SDK, supabase-py, Flask)
- **Frontend:** Plain HTML/CSS/JavaScript (no build step)
- **Database:** Supabase (PostgreSQL)
- **Messaging:** Twilio (WhatsApp + SMS)
- **CI/CD:** GitHub Actions (manual `workflow_dispatch`)
- **Hosting:** Railway (webhook + nudge cron), GitHub Pages (frontend)

## Working Preferences

- **Ask before doing anything heavy.** Confirm approach before generating full files or large refactors.
- **Targeted fixes only.** Show specific replacements, not full rewrites, unless asked.
- **Clarifying questions over assumptions.** If intent is ambiguous, ask first.
- **Be concise.** Short, direct answers. No padding.
- **Summarize sessions on request.** Bullet-point summary at end of session.

### Code Editing Rules
- Default to showing specific lines to change, not full files
- Prefer surgical patches — show old code, then new code
- Never hardcode credentials
