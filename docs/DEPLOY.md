# Deployment

## Webhook service (Railway)

**Current config** (pre-merge): Root Directory = `webhook/`, Builder = Dockerfile.

**After merging `restructure` branch** — manually update in Railway dashboard:
1. Root Directory → blank (repo root)
2. Dockerfile Path → `webhook/Dockerfile`
3. Redeploy — Railway will build from repo root so the `akrue/` package is available

**Environment variables** to set in Railway → Variables:
- `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `FOOTBALL_API_KEY`
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`
- `TWILIO_SMS_FROM` (if using SMS channel)

**Smoke test after deploy:**
```bash
curl https://YOUR-RAILWAY-URL/
# Should return: {"service":"akrue-webhook","status":"ok"}
```

**Twilio webhook URL**: configure `POST /whatsapp` and `POST /sms` to `https://YOUR-RAILWAY-URL/whatsapp` and `/sms`.

## Nudge service (Railway — "7 Min Schedule Fire")

**Config:**
- Root Directory: blank (repo root)
- Builder: Railpack (Python 3.13)
- Start Command: `python src/nudge.py`
- Cron: `*/7 * * * *`

**After merging `restructure` branch** — add Custom Build Command:
```
pip install -e . && pip install -r requirements.txt
```

**Environment variables**: same set as webhook (all Twilio, Supabase, Football API vars).

## GitHub Pages (frontend)

- CNAME: `akrue.win`
- Source: `main` branch, root directory
- No build step — static HTML served as-is

## GitHub Actions (nudge fallback / manual trigger)

- Workflow: `.github/workflows/nudge.yml`
- Trigger: `workflow_dispatch` (manual only)
- Required secrets: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `TWILIO_SMS_FROM`, `FOOTBALL_API_KEY`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`
