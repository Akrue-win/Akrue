# Parlay Feature Documentation

## Overview

The parlay feature allows users to make 10+ match predictions and earn a 20% bonus (capped at $20) if they get all predictions correct.

**Current Status:** World Cup 2026 (testing vehicle)  
**Future:** NFL, MLB, other sports

## Architecture

### Web-First Design

Unlike the traditional WhatsApp-first flow, parlays are **web-first**:

1. **User visits** `web/parlay.html` (via link in app or WhatsApp reminder)
2. **Picks 10+ matches** from available fixtures (World Cup, etc.)
3. **Locks parlay** before first match starts (cannot edit after)
4. **WhatsApp alerts:**
   - Pre-lock reminder: "Lock in before X time!"
   - Post-settlement: "You went X/10 to save $Y" (special message if 10/10)

### Key Components

| Component | File | Purpose |
|---|---|---|
| **Web UI** | `web/parlay.html` | Parlay picking interface |
| **API Endpoints** | `webhook/app.py` | `/parlay/worldcup/*` routes |
| **World Cup Adapter** | `src/worldcup_adapter.py` | Fetch World Cup matches from football-data.org |
| **Settlement Logic** | `src/parlay_settlement.py` | Score parlays, calculate bonuses, send alerts |
| **Reminders** | `src/parlay_reminders.py` | Pre-lock WhatsApp reminders |
| **Sync Script** | `src/sync_worldcup_matches.py` | Populate World Cup matches into DB |

## Endpoints

### `GET /parlay/worldcup/matches`

Fetch available World Cup matches and user's current picks.

**Query params:**
- `phone` (required): User's phone number

**Response:**
```json
{
  "success": true,
  "matches": [
    {
      "id": "worldcup_123",
      "home": "Germany",
      "away": "France",
      "kickoff_utc": "2026-06-15T18:00:00Z",
      "sport": "worldcup"
    }
  ],
  "userPicks": {
    "worldcup_123": "win",
    "worldcup_124": "draw"
  },
  "parlayStat": {
    "locked": false,
    "lockDeadline": "2026-06-15T18:00:00Z"
  }
}
```

### `POST /parlay/worldcup/picks`

Save user's picks (draft, not locked yet). Can be called multiple times.

**Body:**
```json
{
  "phone": "+1234567890",
  "picks": [
    { "match_id": "worldcup_123", "prediction": "win" },
    { "match_id": "worldcup_124", "prediction": "draw" }
  ]
}
```

**Response:**
```json
{
  "success": true,
  "picks_saved": 2
}
```

### `POST /parlay/worldcup/lock`

Lock the parlay. User must have ≥10 picks. Once locked, predictions cannot be edited.

**Body:**
```json
{
  "phone": "+1234567890"
}
```

**Response:**
```json
{
  "success": true,
  "parlay_id": "uuid-here",
  "picks_locked": 10,
  "message": "Parlay locked with 10 picks!"
}
```

## Database Schema

### `parlay_picks`

Tracks individual parlay attempts.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | Primary key |
| `user_phone` | text | User's phone number |
| `sport` | text | `'worldcup'`, `'nfl'`, etc. |
| `parlay_locked` | bool | True once user locks |
| `picks_locked` | int | Number of picks at lock time |
| `locked_at` | timestamptz | When user locked |
| `created_at` | timestamptz | When parlay created |

### `parlay_results`

Tracks settled parlay results and bonuses.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | Primary key |
| `user_phone` | text | User's phone number |
| `sport` | text | `'worldcup'`, `'nfl'`, etc. |
| `week_id` | text | ISO week (e.g., `2026-W24`) |
| `parlay_id` | uuid | FK to parlay_picks |
| `picks_locked` | int | Total picks in parlay |
| `picks_correct` | int | Correct predictions (0-10) |
| `bonus_earned` | numeric | 20% of bankroll, capped $20 |
| `settled` | bool | True after settlement |
| `status` | text | `'pending'`, `'won'`, `'lost'` |
| `created_at` | timestamptz | Result timestamp |

## Setup & Usage

### 1. Create Parlay Tables in Supabase

Run the migration:

```bash
# Log into Supabase and execute migrations/001_create_parlay_tables.sql
```

Or using supabase-cli:

```bash
supabase db push
```

### 2. Sync World Cup Matches

Fetch matches from football-data.org and populate `pending_matches`:

```bash
export FOOTBALL_DATA_API_KEY="your-api-key"
python src/sync_worldcup_matches.py
```

### 3. Send Pre-Lock Reminders (Cron)

Run every day (or before World Cup matches start):

```bash
python -c "from src.parlay_reminders import send_parlay_lock_reminders; send_parlay_lock_reminders('worldcup')"
```

### 4. Settle Parlays (Post-Match)

Run after all World Cup matches finish for the week:

```bash
python -c "from src.parlay_settlement import settle_parlay_picks; settle_parlay_picks('worldcup')"
```

## Testing

### Local Testing

```bash
# Start webhook server
gunicorn webhook.app:app --bind 0.0.0.0:5000

# In another terminal, test API
curl -X GET "http://localhost:5000/parlay/worldcup/matches?phone=%2B1234567890"

curl -X POST "http://localhost:5000/parlay/worldcup/picks" \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "+1234567890",
    "picks": [
      {"match_id": "worldcup_123", "prediction": "win"}
    ]
  }'
```

### Test WhatsApp Alerts

```bash
python -c "from src.parlay_reminders import send_parlay_lock_reminders; send_parlay_lock_reminders('worldcup')"
```

## Migration Strategy (Post-Tournament)

To delete World Cup code and tables after tournament ends:

1. **Delete web interface:**
   - Delete `web/parlay.html` (or create `web/parlay-nfl.html` for NFL instead)
   - Remove link from main app navigation

2. **Delete backend routes:**
   - Remove `/parlay/worldcup/*` routes from `webhook/app.py`

3. **Delete adapter:**
   - Delete `src/worldcup_adapter.py`

4. **Delete data:**
   ```sql
   -- Delete World Cup parlay data
   DELETE FROM parlay_results WHERE sport = 'worldcup';
   DELETE FROM parlay_picks WHERE sport = 'worldcup';
   DELETE FROM predictions WHERE sport = 'worldcup';
   DELETE FROM pending_matches WHERE sport = 'worldcup';
   ```

5. **Delete scripts:**
   - Delete `src/sync_worldcup_matches.py`
   - Delete `src/parlay_reminders.py` (move to NFL as `parlay_settlement.py` remains generic)

## Future: Adding New Sports

To add NFL or other sports:

1. **Add to `SPORT_CONFIG`** in `akrue/config.py`
2. **Create adapter** (e.g., `src/nfl_adapter.py`) similar to `worldcup_adapter.py`
3. **Add API routes** to `webhook/app.py`: `/parlay/nfl/*`
4. **Create UI** (e.g., `web/parlay-nfl.html`) or make `web/parlay.html` sport-agnostic
5. **Scheduling**: Integrate with existing cron jobs for reminders & settlement

## Bonus Calculation

```
IF picks_correct == picks_locked (e.g., 10/10):
  bonus = min(user.weekly_bankroll * 0.2, 20.0)
ELSE:
  bonus = 0
```

Example:
- User with $100/week bankroll hits 10/10: bonus = min($20, 20) = **$20**
- User with $50/week bankroll hits 10/10: bonus = min($10, 20) = **$10**
- User hits 9/10: bonus = **$0** (no partial credit)

## Isolation Notes

- World Cup code is in separate adapter file (`src/worldcup_adapter.py`) and routes (`/parlay/worldcup/*`)
- Web UI is in dedicated file (`web/parlay.html`)
- Database records are tagged with `sport = 'worldcup'`
- Easy to delete after tournament without affecting NFL/future sports

---

**Last Updated:** June 2026  
**Status:** World Cup testing phase
