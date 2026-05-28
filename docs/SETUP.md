# Setup

## Local development

**Prerequisites:** Python 3.12+

```bash
# Install shared package and root-level dependencies
pip install -e . && pip install -r requirements.txt

# Set up environment variables
cp env.example .env
# Edit .env with your Twilio, Supabase, and Football API credentials
```

See [docs/ENV_VARS.md](ENV_VARS.md) for the full variable reference.

## Run nudge script

```bash
# Test mode: sends a confirmation message to all active users
python src/nudge.py --test

# Full cycle: match detection, prompts, settlement, reminders
python src/nudge.py
```

## Run webhook locally

```bash
# Development (Flask built-in, single-threaded)
python webhook/app.py

# Production-like (gunicorn, as Railway runs it)
pip install gunicorn
gunicorn webhook.app:app --bind 0.0.0.0:5000
```

## Run web frontend

```bash
# Serve static files on localhost:8000
python -m http.server 8000
```

Visit `http://localhost:8000/index.html`. The webhook must be running for API calls to work. Frontend API base URL is configured in `web/config.js`.

## Trigger via GitHub Actions

Actions → "Akrue — Match Bet Scheduler" → Run workflow (manual `workflow_dispatch` only).
