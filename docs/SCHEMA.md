# Supabase Schema

## users

| Column | Type | Notes |
|---|---|---|
| `id` | bigint | auto PK |
| `phone_number` | text | unique, normalised (no `+` or `whatsapp:`) |
| `name` | text | |
| `epl_team` | text | nullable — EPL team name |
| `mlb_team` | text | nullable — MLB team name |
| `weekly_bankroll` | numeric | default 50 |
| `bets_per_week` | int | default 3 |
| `weekly_cap_multiplier` | numeric | default 1.25, max 2.0 |
| `group_code` | text | nullable |
| `status` | text | `'active'`, `'inactive'`, or `'opted_out'` |
| `channel` | text | `'whatsapp'` (default) or `'sms'` |
| `sms_consent` | bool | required at signup |
| `sms_consent_at` | timestamptz | when consent was given |
| `sms_consent_method` | text | e.g. `'signup_form'` |

## predictions

| Column | Type | Notes |
|---|---|---|
| `id` | bigint | auto PK |
| `match_id` | text | `{sport}_{api_id}_{team_id}` format |
| `user_phone` | text | normalised phone number |
| `prediction` | text | `'win'`, `'draw'`, `'loss'`, `'N/A'`, or `''` (empty = not yet picked) |
| `status` | text | `'pending'`, `'locked'`, `'insured'`, `'cancelled'` |
| `correct_amount` | int | amount if prediction is correct |
| `wrong_amount` | int | amount if prediction is wrong |
| `reminder_sent` | bool | default false |
| `created_at` | timestamptz | |

## pending_matches

| Column | Type | Notes |
|---|---|---|
| `id` | bigint | auto PK |
| `match_id` | text | unique, `{sport}_{api_id}_{team_id}` format |
| `sport` | text | `'epl'` or `'mlb'` |
| `team_id` | int | sports API team ID |
| `team_name` | text | |
| `opponent` | text | |
| `users` | text[] | array of normalised phone numbers |
| `kickoff_utc` | text | ISO datetime string |
| `settled` | bool | false until post-match settlement runs |

## sent_matches

| Column | Type | Notes |
|---|---|---|
| `id` | bigint | auto PK |
| `match_id` | text | |
| `sport` | text | |
| `team` | text | team name |
| `user_phone` | text | normalised phone number |

## savings_log

| Column | Type | Notes |
|---|---|---|
| `id` | bigint | auto PK |
| `date` | date | date of settlement |
| `user_phone` | text | normalised phone number |
| `amount` | numeric | amount saved |
| `trigger` | text | e.g. `epl_bet_win_correct`, `mlb_bet_loss_wrong`, `insurance_buyout` |
| `match_id` | text | |
| `week` | text | ISO week string e.g. `2025-W21` |
| `sport` | text | `'epl'` or `'mlb'` |

## insurance_offers

| Column | Type | Notes |
|---|---|---|
| `id` | bigint | auto PK |
| `match_id` | text | |
| `user_phone` | text | normalised phone number |
| `amount` | int | insurance payout amount |
| `accepted` | bool | false until user replies INSURE |
| `sent_at` | timestamptz | ? — confirm in Supabase dashboard |

## otp_codes

| Column | Type | Notes |
|---|---|---|
| `id` | bigint | auto PK |
| `phone_number` | text | normalised phone number |
| `code` | text | 6-digit OTP |
| `expires_at` | timestamptz | 10 minutes after creation |
| `used` | bool | false until verified |
| `created_at` | timestamptz | |
