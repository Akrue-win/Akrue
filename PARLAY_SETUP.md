# Parlay Feature Setup Checklist

## ✓ Completed (June 10, 2026)

- [x] World Cup sport config added to `akrue/config.py`
- [x] World Cup adapter (`src/worldcup_adapter.py`) — football-data.org integration
- [x] Web interface (`web/parlay.html`) — parlay picking interface
- [x] API endpoints (`webhook/app.py`) — `/parlay/worldcup/*` routes
- [x] Settlement logic (`src/parlay_settlement.py`) — bonus calculation & alerts
- [x] Reminders (`src/parlay_reminders.py`) — pre-lock WhatsApp prompts
- [x] Database schema + migration (`migrations/001_create_parlay_tables.sql`)
- [x] Documentation (`docs/PARLAY_FEATURE.md`)

## 📋 Next Steps (Manual Execution Required)

### 1. Set Up Supabase Tables

Log into Supabase and execute the migration:

```sql
-- Copy & paste from migrations/001_create_parlay_tables.sql
-- This creates: parlay_picks and parlay_results tables
```

Or use supabase-cli:

```bash
supabase db push
```

### 2. Verify Environment Variables

Make sure `.env` includes:

```
FOOTBALL_DATA_API_KEY=your-api-key-from-football-data-org
```

Get a free API key: https://www.football-data.org/client/register

### 3. Sync World Cup Matches

Run once to populate World Cup matches:

```bash
python src/sync_worldcup_matches.py
```

**Verify:**
```bash
# Check that pending_matches now has worldcup records
# SELECT COUNT(*) FROM pending_matches WHERE sport='worldcup';
# Should show N > 0
```

### 4. Test API Endpoints (Local)

Start the webhook server:

```bash
gunicorn webhook.app:app --bind 0.0.0.0:5000
```

In another terminal:

```bash
# Test: get matches
curl -X GET "http://localhost:5000/parlay/worldcup/matches?phone=%2B1234567890"

# Test: save picks
curl -X POST "http://localhost:5000/parlay/worldcup/picks" \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "+1234567890",
    "picks": [
      {"match_id": "worldcup_123", "prediction": "win"}
    ]
  }'

# Test: lock parlay (need 10+ picks first)
curl -X POST "http://localhost:5000/parlay/worldcup/lock" \
  -H "Content-Type: application/json" \
  -d '{"phone": "+1234567890"}'
```

### 5. Test Web Interface (Local)

```bash
cd web
python -m http.server 8000
# Visit: http://localhost:8000/parlay.html
```

Requires being logged in (localStorage needs `user_phone`).

### 6. Set Up Cron Jobs (Railway)

Add to `railway.yaml` (or equivalent):

```yaml
# Pre-lock reminders (daily or as needed)
worldcup-reminder:
  schedule: "0 9 * * *"  # 9 AM daily
  command: python -c "from src.parlay_reminders import send_parlay_lock_reminders; send_parlay_lock_reminders('worldcup')"

# Settlement (after World Cup matches, e.g., every Sunday 10 PM)
worldcup-settle:
  schedule: "0 22 * * 0"  # 10 PM Sundays
  command: python -c "from src.parlay_settlement import settle_parlay_picks; settle_parlay_picks('worldcup')"
```

### 7. Link from Main App

Add link to `web/app.html` to point users to parlay interface:

```html
<!-- In the nav or buttons section -->
<a href="parlay.html" class="nav-link">🌍 World Cup Parlay</a>
```

### 8. Update WhatsApp Prompt (Optional)

In `src/nudge.py`, you can add a note about parlays in the welcome message or help text.

## 🧪 Testing Checklist

- [ ] Created parlay_picks & parlay_results tables in Supabase
- [ ] Synced World Cup matches (`src/sync_worldcup_matches.py`)
- [ ] `/parlay/worldcup/matches` returns matches (via curl or browser)
- [ ] `/parlay/worldcup/picks` accepts POST requests
- [ ] `/parlay/worldcup/lock` works when ≥10 picks
- [ ] Web interface (`web/parlay.html`) loads in browser
- [ ] Pick selection works & saves to DB
- [ ] Lock button becomes enabled after 10+ picks selected
- [ ] Test user receives WhatsApp reminder before first match
- [ ] Settlement script runs and sends results alert
- [ ] Perfect 10/10 alert has special message (🎉)

## 🗑️ Cleanup (Post-Tournament)

After World Cup ends (December 2026), delete:

```bash
# Files to delete
rm web/parlay.html
rm src/worldcup_adapter.py
rm src/sync_worldcup_matches.py
rm src/parlay_reminders.py  # Or keep as template for NFL

# Database cleanup
DELETE FROM parlay_results WHERE sport = 'worldcup';
DELETE FROM parlay_picks WHERE sport = 'worldcup';
DELETE FROM predictions WHERE sport = 'worldcup';
DELETE FROM pending_matches WHERE sport = 'worldcup';

# Remove cron jobs from railway.yaml
# Remove /parlay/worldcup/* routes from webhook/app.py
# Remove 'worldcup' from SPORT_CONFIG (akrue/config.py)
```

## 🚀 NFL Migration (Future)

To transition to NFL:

1. Create `src/nfl_adapter.py` (similar to `worldcup_adapter.py`)
2. Add NFL to SPORT_CONFIG
3. Create `web/parlay-nfl.html` or parameterize `web/parlay.html`
4. Add `/parlay/nfl/*` routes to `webhook/app.py`
5. Create NFL cron jobs in `railway.yaml`
6. Document in PARLAY_FEATURE.md

---

**Branch:** `claude/adoring-hawking-VsRYJ`  
**Status:** Ready for Supabase setup & testing  
**Created:** June 10, 2026
