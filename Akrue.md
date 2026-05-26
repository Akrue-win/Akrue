# Akrue — Project Reference

## What It Is
A WhatsApp/SMS-based mock sports gambling savings app. Users follow a sports team, get prompted before games to pick WIN/LOSS/DRAW, and save money based on their prediction accuracy — win or lose, they always save something.

## Stack
| Layer | Tool |
|---|---|
| Cron job | `nudge.py` (Railway, runs every 7 mins) |
| Webhook server | `app.py` — Flask, always-on (Railway) |
| Database | Supabase (migrated from Google Sheets) |
| Messaging | Twilio (WhatsApp + SMS) |
| Sports data | football-data.org (EPL), MLB StatsAPI, ESPN public API (NFL) |
| Frontend | Static HTML (`index.html`, `signup.html`, `app.html`, `leaderboard.html`) |

## File Structure
```
src/
  nudge.py          ← cron job: pre-match prompts, post-match results, reminders
webhook/ (or app/)
  app.py            ← Flask: handles WhatsApp replies + web app /place-bet
  Dockerfile
  Procfile
requirements.txt    ← root level
web/
  signup.html
  app.html
  leaderboard.html
index.html          ← landing page
```

## Database: Supabase Tables
| Table | Purpose |
|---|---|
| `users` | Active users, phone, team preferences, group_code |
| `predictions` | match_id, user_phone, prediction, timestamp, status, reminder_sent |
| `pending_matches` | Unsettled matches with users list, amounts, kickoff_utc |
| `sent_matches` | Dedup log per user per match |
| `savings_log` | Bet outcomes and amounts, trigger_type |
| `double_down_sent` | Mid-game double-down offers |

## Key Architecture Decisions
- **match_id format:** `mlb_824600_112` (sport_gameid_teamid) — one row per team per game
- **Phone format:** Digits only, no `whatsapp:+` prefix in storage. Always call `send_whatsapp()` with `f"whatsapp:+{phone_n}"`
- **Bet locking:** Status stays `pending` until kickoff, locked at game start via `lock_unpicked_started_matches()`
- **N/A picks:** If user didn't pick before kickoff, write `N/A` and set status to `locked`. Skip entirely in post-match — no message, no savings log entry
- **Amount structure:** `weekly_bankroll / bets_per_week` = correct amount; wrong pick = 1.4x correct; weekly cap = 1.25x bankroll
- **Savings log trigger_type format:** `epl_bet_win_correct` / `mlb_bet_loss_wrong`

## Sports Supported
- **EPL** — football-data.org API, allows DRAW
- **MLB** — statsapi, no draw
- **NFL** — ESPN public API (no key needed), no draw

## Twilio / Messaging Notes
- Using A2P 10DLC — messaging service registered
- Advanced Opt-Out enabled (STOP/START/HELP)
- Error 21610 = user opted out — handle gracefully, don't crash
- Error 63016 = outside 24hr WhatsApp window — use Message Templates for outbound
- All outbound messages use `send_whatsapp()` helper

## Common Bugs / Watch Points
- `gspread` (legacy) returned phone numbers as integers — always `str()` wrap
- Phone normalization: strip `whatsapp:`, `+`, spaces before comparing
- `Prediction` column header is capitalized in the old sheet — watch case sensitivity
- Lock function must check if pick already exists before writing N/A
- Reminder window: 0–40 mins before kickoff (widened to avoid race with lock)

## Deployment
- Two Railway services: cron (`nudge.py`) and webhook (`app.py`)
- Env vars: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `SUPABASE_URL`, `SUPABASE_KEY`, `FOOTBALL_API_KEY`
- Cron runs every 7 minutes