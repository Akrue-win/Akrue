# Webhook API Reference

Base URL (production): `https://project-free-kick-production.up.railway.app`

All JSON endpoints return `{"success": true, ...}` on success or `{"success": false, "error": "..."}` on failure.

---

## Health

### `GET /`
Returns service status.
```json
{"status": "ok", "service": "akrue-webhook"}
```

---

## Twilio inbound

### `POST /whatsapp`
Twilio webhook for WhatsApp replies. Processes WIN/DRAW/LOSS picks, INSURE, STOP/START/HELP, GOAL, BALANCE, STREAK, RANK commands.

### `POST /sms`
Same as `/whatsapp` but for SMS channel.

---

## Bet placement (web app)

### `POST /place-bet`
Place a prediction via the browser UI.

**Body:**
```json
{"phone": "12125551234", "match_id": "epl_491827_64", "pick": "win"}
```

**Response:**
```json
{"success": true, "pick": "win", "match_id": "...", "correct_amount": 10, "wrong_amount": 14}
```

---

## User data

### `GET /user?phone={phone}`
Fetch user profile by normalised phone number.

### `POST /update-user`
Update user profile fields (`epl_team`, `mlb_team`, `weekly_bankroll`, `bets_per_week`, `group_code`, `weekly_cap_multiplier`, `name`, `channel`).

### `POST /update-phone`
Change a user's phone number across all tables. Body: `{"old_phone": "...", "new_phone": "..."}`.

---

## Authentication (OTP)

### `POST /send-otp`
Send a 6-digit OTP to the user's phone. Body: `{"phone": "...", "channel": "whatsapp"}`.

### `POST /verify-otp`
Verify an OTP code. Body: `{"phone": "...", "code": "123456"}`.

---

## Signup

### `POST /signup`
Create a new user account. Requires `sms_consent: true`. Sends a welcome message.

---

## Bets & history

### `GET /pending-bets?phone={phone}`
Returns the user's open (pending) predictions with match details.

### `GET /bet-history?phone={phone}`
Returns the user's settled bets from `savings_log`, joined with match and prediction data.

### `GET /savings-history?phone={phone}`
Returns cumulative weekly savings history for the savings graph. Responds with `1M`, `3M`, `6M`, and `ALL` time ranges.

---

## Leaderboard

### `GET /leaderboard`
Returns all active users with weekly savings, total savings, goals hit, streak, and accuracy stats.

---

## Live scores

### `GET /live-score?match_id={mlb_match_id}`
Returns live MLB score via StatsAPI.

### `GET /live-score-epl?match_id={epl_match_id}`
Returns live EPL score via football-data.org.

---

## Misc stubs

### `GET /parlay-legs`
Returns `{"success": true, "legs": []}` — feature stub.

### `GET /krue-data?group_code={code}`
Returns group/krue members and a null matchup — feature stub.
