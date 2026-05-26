"""
Akrue — Core Script
--------------------
Fires ONLY when a user's registered team has a match starting in 5-45 mins.
No scheduled reminders — match-triggered only.

Sport-agnostic architecture — adding a new sport requires only:
  1. A new entry in SPORT_CONFIG
  2. A new entry in SPORT_TEAM_IDS
  3. New API fetch functions (get_X_upcoming / get_X_recent / result_for_X)
  4. A new entry in SPORT_API_HANDLERS

All credentials loaded from environment variables only.
"""

import os
import sys
import random
import datetime
import requests
import statsapi
from supabase import create_client, Client
from twilio.rest import Client as TwilioClient

# ─────────────────────────────────────────────
# ENV CONFIG
# ─────────────────────────────────────────────

TWILIO_ACCOUNT_SID          = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN           = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM_NUMBER          = os.environ["TWILIO_FROM_NUMBER"]
FOOTBALL_API_KEY            = os.environ["FOOTBALL_API_KEY"]
SUPABASE_URL                = os.environ["SUPABASE_URL"]
SUPABASE_SECRET_KEY         = os.environ["SUPABASE_SECRET_KEY"]
MESSAGE_CHANNEL             = os.environ.get("MESSAGE_CHANNEL", "whatsapp")
TWILIO_MESSAGING_SERVICE_SID = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "")

# ─────────────────────────────────────────────
# SPORT CONFIG
# ─────────────────────────────────────────────

SPORT_CONFIG = {
    "epl": {
        "name":             "Premier League",
        "emoji":            "⚽",
        "allows_draw":      True,
        "options":          ["WIN", "DRAW", "LOSS"],
        "user_field":       "epl_team",
        "match_id_prefix":  "epl_",
        "win_emoji":        "🟢",
        "draw_emoji":       "🟡",
        "loss_emoji":       "🔴",
        "start_label":      "kicks off",
    },
    "mlb": {
        "name":             "MLB",
        "emoji":            "⚾",
        "allows_draw":      False,
        "options":          ["WIN", "LOSS"],
        "user_field":       "mlb_team",
        "match_id_prefix":  "mlb_",
        "win_emoji":        "⚾",
        "draw_emoji":       None,
        "loss_emoji":       "😬",
        "start_label":      "first pitch",
    },
}

# ─────────────────────────────────────────────
# TEAM IDs PER SPORT
# ─────────────────────────────────────────────

SPORT_TEAM_IDS = {
    "epl": {
        "Arsenal": 57, "Aston Villa": 58, "Bournemouth": 1044,
        "Brentford": 402, "Brighton": 397, "Burnley": 328,
        "Chelsea": 61, "Crystal Palace": 354, "Everton": 62,
        "Fulham": 63, "Leeds United": 341, "Liverpool": 64,
        "Manchester City": 65, "Manchester United": 66, "Newcastle United": 67,
        "Nottingham Forest": 351, "Sunderland": 356, "Tottenham Hotspur": 73,
        "West Ham United": 563, "Wolverhampton": 76,
    },
    "mlb": {
        "Baltimore Orioles": 110, "Boston Red Sox": 111, "New York Yankees": 147,
        "Tampa Bay Rays": 139, "Toronto Blue Jays": 141, "Chicago White Sox": 145,
        "Cleveland Guardians": 114, "Detroit Tigers": 116, "Kansas City Royals": 118,
        "Minnesota Twins": 142, "Houston Astros": 117, "Los Angeles Angels": 108,
        "Oakland Athletics": 133, "Seattle Mariners": 136, "Texas Rangers": 140,
        "Atlanta Braves": 144, "Miami Marlins": 146, "New York Mets": 121,
        "Philadelphia Phillies": 143, "Washington Nationals": 120, "Chicago Cubs": 112,
        "Cincinnati Reds": 113, "Milwaukee Brewers": 158, "Pittsburgh Pirates": 134,
        "St. Louis Cardinals": 138, "Arizona Diamondbacks": 109, "Colorado Rockies": 115,
        "Los Angeles Dodgers": 119, "San Diego Padres": 135, "San Francisco Giants": 137,
    },
}

PROMPT_WINDOW_MIN = 5
PROMPT_WINDOW_MAX = 45

MLB_PRE_GAME_STATUSES = {"Scheduled", "Pre-Game", "Warmup", "Preview"}
MLB_FINAL_STATUSES    = {"Final", "Game Over", "Completed Early"}

CAP_WARNING_THRESHOLD  = 0.75
DEFAULT_CAP_MULTIPLIER = 1.25
MAX_CAP_MULTIPLIER     = 2.0

# ─────────────────────────────────────────────
# MESSAGE VARIANTS
# ─────────────────────────────────────────────

PROMPT_VARIANTS = {
    "epl": [
        (
            "Hey {name}! {away} @ {home} kicks off in ~{mins} minutes!\n\n"
            "{team_name} (Pay yourself ${correct_amount} if you're right!)\n"
            "   Win\n   Draw\n   Loss\n\n"
            "Wrong prediction still pays you ${wrong_amount} 💰🤝\n\n"
            "Reply win, draw, or loss to lock in your pick"
        ),
        (
            "⏰ Game day, {name}! {team_name} vs {opp} — in ~{mins} minutes.\n\n"
            "Make your prediction, it pays either way:\n"
            "✅ Nail it → pay yourself ${correct_amount}\n"
            "🤝 Miss it → still pay yourself ${wrong_amount}\n\n"
            "Reply win, draw, or loss"
        ),
        (
            "{name} — {team_name} is up in ~{mins} minutes.\n\n"
            "Choose your prediction and get paid 💰\n"
            "Right → ${correct_amount} | Wrong → still ${wrong_amount}\n\n"
            "win / draw / loss — go!"
        ),
        (
            "🔔 {team_name} vs {opp} in ~{mins} min.\n\n"
            "{name}, what's your prediction?\n"
            "   Win\n   Draw\n   Loss\n\n"
            "Right: pay yourself ${correct_amount}. Wrong: still pay yourself ${wrong_amount}. Reply to lock in."
        ),
    ],
    "mlb": [
        (
            "Hey {name}! {away} @ {home} first pitch in ~{mins} minutes!\n\n"
            "{team_name} (Pay yourself ${correct_amount} if you're right!)\n"
            "   Win\n   Loss\n\n"
            "Wrong prediction still pays you ${wrong_amount} 💰🤝\n\n"
            "Reply win or loss to lock in your pick"
        ),
        (
            "⏰ Game day, {name}! {team_name} vs {opp} — in ~{mins} minutes.\n\n"
            "Make your prediction, it pays either way:\n"
            "✅ Nail it → pay yourself ${correct_amount}\n"
            "🤝 Miss it → still pay yourself ${wrong_amount}\n\n"
            "Reply win or loss"
        ),
        (
            "{name} — {team_name} is up in ~{mins} minutes.\n\n"
            "Choose your prediction and get paid 💰\n"
            "Right → ${correct_amount} | Wrong → still ${wrong_amount}\n\n"
            "win / loss — go!"
        ),
        (
            "🔔 {team_name} vs {opp} in ~{mins} min.\n\n"
            "{name}, what's your prediction?\n"
            "   Win\n   Loss\n\n"
            "Right: pay yourself ${correct_amount}. Wrong: still pay yourself ${wrong_amount}. Reply to lock in."
        ),
    ],
}

REMINDER_VARIANTS = {
    "epl": [
        (
            "🚨 Hey {name}! {team_name} kicks off in ~{mins} minutes — coming up fast!\n\n"
            "Lock in your pick now: reply win, draw, or loss"
        ),
        (
            "⏱️ Under 15 minutes, {name}!\n\n"
            "{team_name} vs {opp} in ~{mins} min — you haven't predicted yet.\n"
            "Reply win, draw, or loss before the whistle blows 🎯"
        ),
        (
            "Last call, {name} ⚡\n\n"
            "{team_name} starts in ~{mins} min. Miss the window and you miss out on paying yourself.\n"
            "win, draw, or loss — reply now!"
        ),
    ],
    "mlb": [
        (
            "🚨 Hey {name}! {team_name} first pitch in ~{mins} minutes — coming up fast!\n\n"
            "Lock in your pick now: reply win or loss"
        ),
        (
            "⏱️ Under 15 minutes, {name}!\n\n"
            "{team_name} vs {opp} in ~{mins} min — you haven't predicted yet.\n"
            "Reply win or loss before first pitch 🎯"
        ),
        (
            "Last call, {name} ⚡\n\n"
            "{team_name} starts in ~{mins} min. Miss the window and you miss out on paying yourself.\n"
            "win or loss — reply now!"
        ),
    ],
}

RESULT_VARIANTS = {
    "win": [
        "{emoji} {team_name} win! {score} over {opp}.\nYou paid yourself ${amount} 💰",
        "{emoji} {team_name} get the W, {score} vs {opp}. Called it — pay yourself ${amount}! 💰",
        "{emoji} Result: {team_name} {score} {opp}. Right prediction. You pay yourself ${amount} 💪",
    ],
    "loss": [
        "{emoji} {team_name} fall {score} to {opp}.\nYou still paid yourself ${amount} 🤝",
        "{emoji} Rough one — {team_name} go down {score}. But you pay yourself ${amount} regardless 🤝",
        "{emoji} {team_name} {score} {opp}. Didn't go your way, but you still pay yourself ${amount} 💸",
    ],
    "draw": [
        "🟡 {team_name} draw {score} with {opp}.\nYou paid yourself ${amount} 💰",
        "🟡 It's all square — {team_name} {score} {opp}. Called it, pay yourself ${amount} 💰",
    ],
}

INSURANCE_VARIANTS = {
    "epl": [
        (
            "⚽ Half time - {score}\n\n"
            "Not looking great for your prediction... 😬\n\n"
            "Want to cash out early? Pay yourself ${amount} right now and close out.\n\n"
            "Reply INSURE to take it, or do nothing and ride it out!"
        ),
        (
            "⏸️ Halftime — {team_name} {score} {opp}.\n\n"
            "Things aren't looking great. Pay yourself ${amount} now and walk away, "
            "or roll the dice in the second half.\n\n"
            "Reply INSURE to take it, or sit tight."
        ),
        (
            "⚽ {score} at the break.\n\n"
            "Your prediction is on thin ice — but you can still pay yourself ${amount} right now.\n\n"
            "INSURE to take it. Nothing to ride it out."
        ),
    ],
    "mlb": [
        (
            "⚾ After {innings} innings - {score}\n\n"
            "Not looking great for your prediction... 😬\n\n"
            "Want to cash out early? Pay yourself ${amount} right now and close out.\n\n"
            "Reply INSURE to take it, or do nothing and ride it out!"
        ),
        (
            "⏸️ After {innings} — {team_name} {score} {opp}.\n\n"
            "Things aren't looking great. Pay yourself ${amount} now and walk away, "
            "or roll the dice on the final innings.\n\n"
            "Reply INSURE to take it, or sit tight."
        ),
        (
            "⚾ {score} after {innings} innings.\n\n"
            "Your prediction is on thin ice — but you can still pay yourself ${amount} right now.\n\n"
            "INSURE to take it. Nothing to ride it out."
        ),
    ],
}

POSTPONED_MSG = (
    "📅 Heads up, {name} — {team_name} vs {opp} has been postponed.\n\n"
    "Your pending pick has been cancelled. We'll send a new prompt when the match is rescheduled."
)

# ─────────────────────────────────────────────
# SUPABASE CLIENT
# ─────────────────────────────────────────────

def get_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def normalise_phone(phone: str) -> str:
    return phone.replace("whatsapp:", "").replace("+", "").strip()

def get_week_bounds() -> tuple:
    today = datetime.date.today()
    days_since_friday = (today.weekday() - 4) % 7
    week_start = today - datetime.timedelta(days=days_since_friday)
    week_end   = week_start + datetime.timedelta(days=6)
    return week_start, week_end

def current_week() -> str:
    week_start, _ = get_week_bounds()
    iso = week_start.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"

# ─────────────────────────────────────────────
# SUPABASE — USERS
# ─────────────────────────────────────────────

def get_active_users() -> list:
    try:
        sb     = get_client()
        result = (
            sb.table("users")
            .select("*")
            .eq("status", "active")
            .eq("sms_consent", True)
            .execute()
        )
        users  = result.data or []
        print(f"[Users] Found {len(users)} active users with SMS consent.")
        return users
    except Exception as e:
        print(f"[Users] Error: {e}")
        return []

# ─────────────────────────────────────────────
# SUPABASE — SENT MATCHES
# ─────────────────────────────────────────────

def get_sent_match_ids() -> dict:
    try:
        sb      = get_client()
        result  = sb.table("sent_matches").select("user_phone, match_id").execute()
        sent    = {}
        for r in (result.data or []):
            phone    = normalise_phone(str(r.get("user_phone", "")))
            match_id = r.get("match_id", "")
            if phone and match_id:
                sent.setdefault(phone, set()).add(match_id)
        print(f"[Sent_Matches] Per-user log loaded for {len(sent)} users.")
        return sent
    except Exception as e:
        print(f"[Sent_Matches] Error: {e}")
        return {}

def log_sent_match(match_id: str, sport: str, team: str, user_phone: str):
    try:
        sb = get_client()
        sb.table("sent_matches").insert({
            "match_id":   match_id,
            "sport":      sport,
            "team":       team,
            "user_phone": normalise_phone(user_phone),
        }).execute()
    except Exception as e:
        print(f"[Sent_Matches] Error writing: {e}")

# ─────────────────────────────────────────────
# SUPABASE — PENDING MATCHES
# ─────────────────────────────────────────────

def get_pending_matches() -> dict:
    try:
        sb      = get_client()
        result  = sb.table("pending_matches").select("*").eq("settled", False).execute()
        pending = {}
        for r in (result.data or []):
            if r.get("match_id"):
                pending[r["match_id"]] = {
                    "sport":       r.get("sport"),
                    "team_id":     int(r.get("team_id", 0)),
                    "team_name":   r.get("team_name"),
                    "opponent":    r.get("opponent"),
                    "users":       r.get("users") or [],
                    "id":          r.get("id"),
                    "kickoff_utc": r.get("kickoff_utc", ""),
                }
        print(f"[Pending] {len(pending)} unsettled matches.")
        return pending
    except Exception as e:
        print(f"[Pending] Error: {e}")
        return {}

def log_pending_match(match_id: str, data: dict):
    try:
        sb = get_client()
        sb.table("pending_matches").insert({
            "match_id":    match_id,
            "sport":       data["sport"],
            "team_id":     data["team_id"],
            "team_name":   data["team_name"],
            "opponent":    data["opponent"],
            "users":       data["users"],
            "settled":     False,
            "kickoff_utc": data.get("kickoff_utc", ""),
        }).execute()
    except Exception as e:
        print(f"[Pending] Error writing: {e}")

def append_users_to_pending(match_id: str, new_phones: list):
    try:
        sb     = get_client()
        result = sb.table("pending_matches").select("id, users").eq("match_id", match_id).eq("settled", False).execute()
        rows   = result.data or []
        if not rows:
            return
        row      = rows[0]
        existing = row.get("users") or []
        merged   = list(set(existing + new_phones))
        sb.table("pending_matches").update({"users": merged}).eq("id", row["id"]).execute()
        print(f"[Pending] Appended {new_phones} to {match_id}")
    except Exception as e:
        print(f"[Pending] Error appending users to {match_id}: {e}")

def mark_match_settled(match_id: str):
    try:
        sb = get_client()
        sb.table("pending_matches").update({"settled": True}).eq("match_id", match_id).execute()
    except Exception as e:
        print(f"[Pending] Error settling: {e}")

# ─────────────────────────────────────────────
# SUPABASE — PREDICTIONS
# ─────────────────────────────────────────────

def get_predictions_for_match(match_id: str) -> dict:
    try:
        sb     = get_client()
        result = sb.table("predictions").select("*").eq("match_id", match_id).execute()
        out    = {}
        for r in (result.data or []):
            if r.get("prediction"):
                phone = normalise_phone(str(r["user_phone"]))
                out[phone] = {
                    "prediction":     r["prediction"],
                    "correct_amount": r.get("correct_amount", 0),
                    "wrong_amount":   r.get("wrong_amount", 0),
                    "status":         r.get("status", ""),
                }
        return out
    except Exception as e:
        print(f"[Predictions] Error: {e}")
        return {}

def get_all_predictions(pending: dict) -> dict:
    all_preds = {}
    for match_id in pending:
        all_preds[match_id] = get_predictions_for_match(match_id)
    return all_preds

def write_prediction_pending(user_phone: str, match_id: str,
                             correct_amount: int, wrong_amount: int):
    try:
        sb = get_client()
        sb.table("predictions").insert({
            "match_id":       match_id,
            "user_phone":     normalise_phone(user_phone),
            "prediction":     "",
            "status":         "pending",
            "reminder_sent":  False,
            "correct_amount": correct_amount,
            "wrong_amount":   wrong_amount,
        }).execute()
    except Exception as e:
        print(f"[Predictions] Error writing pending: {e}")

def cancel_predictions_for_match(match_id: str):
    try:
        sb = get_client()
        sb.table("predictions").update({"status": "cancelled"}).eq("match_id", match_id).execute()
        print(f"[Predictions] Cancelled all predictions for {match_id}")
    except Exception as e:
        print(f"[Predictions] Error cancelling: {e}")

# ─────────────────────────────────────────────
# SUPABASE — SAVINGS LOG
# ─────────────────────────────────────────────

def get_week_savings(user_phone: str) -> float:
    try:
        week_start, week_end = get_week_bounds()
        sb     = get_client()
        result = sb.table("savings_log") \
            .select("amount") \
            .eq("user_phone", normalise_phone(user_phone)) \
            .gte("date", week_start.isoformat()) \
            .lte("date", week_end.isoformat()) \
            .execute()
        return sum(float(r.get("amount", 0)) for r in (result.data or []))
    except Exception as e:
        print(f"[Savings] Error fetching week savings for {user_phone}: {e}")
        return 0.0

def log_bet_to_sheet(user_phone, match_id, prediction, amount, result, sport):
    try:
        sb      = get_client()
        trigger = f"{sport}_bet_{result}_correct" if prediction == result else f"{sport}_bet_{result}_wrong"
        sb.table("savings_log").insert({
            "date":       datetime.date.today().isoformat(),
            "user_phone": normalise_phone(user_phone),
            "amount":     amount,
            "trigger":    trigger,
            "match_id":   match_id,
            "week":       current_week(),
            "sport":      sport,
        }).execute()
    except Exception as e:
        print(f"[Savings_Log] Error: {e}")

# ─────────────────────────────────────────────
# SUPABASE — INSURANCE
# ─────────────────────────────────────────────

def get_insurance_offered() -> set:
    try:
        sb     = get_client()
        result = sb.table("insurance_offers").select("match_id, user_phone").execute()
        return {
            f"{r['match_id']}:{normalise_phone(str(r['user_phone']))}"
            for r in (result.data or [])
            if r.get("match_id") and r.get("user_phone")
        }
    except Exception as e:
        print(f"[Insurance_Offers] Error reading: {e}")
        return set()

def log_insurance_offered(match_id: str, user_phone: str, amount: int):
    try:
        sb = get_client()
        sb.table("insurance_offers").insert({
            "match_id":   match_id,
            "user_phone": normalise_phone(user_phone),
            "amount":     amount,
            "accepted":   False,
        }).execute()
    except Exception as e:
        print(f"[Insurance_Offers] Error writing: {e}")

# ─────────────────────────────────────────────
# AMOUNT CALCULATION
# ─────────────────────────────────────────────

def calculate_amounts(user: dict, user_phone: str) -> dict:
    try:
        bankroll   = float(user.get("weekly_bankroll", 0))
        bets       = int(user.get("bets_per_week", 1)) or 1
        multiplier = float(user.get("weekly_cap_multiplier") or DEFAULT_CAP_MULTIPLIER)
        multiplier = min(multiplier, MAX_CAP_MULTIPLIER)
    except (ValueError, TypeError):
        bankroll, bets, multiplier = 0.0, 1, DEFAULT_CAP_MULTIPLIER

    correct_amount = round(bankroll / bets)
    wrong_amount   = round(correct_amount * 1.4)
    cap            = round(bankroll * multiplier)
    week_savings   = get_week_savings(user_phone)
    remaining      = max(0.0, cap - week_savings)

    capped        = False
    near_cap      = (week_savings / cap >= CAP_WARNING_THRESHOLD) if cap > 0 else False
    cap_exhausted = remaining <= 0

    if cap_exhausted:
        correct_amount = 0
        wrong_amount   = 0
    elif correct_amount > remaining:
        correct_amount = round(remaining)
        wrong_amount   = round(remaining * 1.4)
        capped         = True
        near_cap       = True

    return {
        "correct_amount": correct_amount,
        "wrong_amount":   wrong_amount,
        "cap":            cap,
        "week_savings":   week_savings,
        "remaining":      remaining,
        "capped":         capped,
        "near_cap":       near_cap,
        "cap_exhausted":  cap_exhausted,
    }

# ─────────────────────────────────────────────
# MESSAGING
# ─────────────────────────────────────────────

def send_message(to_number: str, message: str):
    client  = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    phone_n = normalise_phone(to_number)
    if MESSAGE_CHANNEL == "sms":
        msg = client.messages.create(
            body=message,
            from_=TWILIO_FROM_NUMBER,
            to=f"+{phone_n}",
        )
    else:
        msg = client.messages.create(
            body=message,
            from_=TWILIO_FROM_NUMBER,
            to=f"whatsapp:+{phone_n}",
        )
    print(f"[{MESSAGE_CHANNEL.upper()} -> +{phone_n}] SID: {msg.sid}")

# kept as alias so existing callers outside this file aren't broken
def send_whatsapp(to_number: str, message: str):
    send_message(to_number, message)

def build_prompt_message(name: str, home: str, away: str, sport_key: str,
                         correct_amount: int, wrong_amount: int,
                         team_name: str = "",
                         near_cap: bool = False,
                         cap_exhausted: bool = False,
                         remaining: float = 0,
                         mins_until_kickoff: int = 30,
                         opponent: str = "") -> str:
    cfg      = SPORT_CONFIG[sport_key]
    label    = team_name or "Your team"
    opp      = opponent or away

    variant = random.choice(PROMPT_VARIANTS[sport_key])
    base = variant.format(
        name=name,
        home=home,
        away=away,
        team_name=label,
        opp=opp,
        mins=mins_until_kickoff,
        correct_amount=correct_amount,
        wrong_amount=wrong_amount,
    )

    if cap_exhausted:
        base += "\n\nYou've hit your weekly cap — no further amounts will be deposited this week."
    elif near_cap:
        base += "\n\nYou're close to your weekly cap — prediction amounts adjusted."

    return base

def reorder_score(score: str, result: str) -> str:
    try:
        a, b = score.split("-")
        a, b = int(a), int(b)
        if result in ("win", "loss"):
            return f"{max(a,b)}-{min(a,b)}"
        return score
    except Exception:
        return score

def build_result_message(sport_key: str, team_name: str, opponent: str,
                         score: str, result: str, pick: str | None,
                         correct_amount: int, wrong_amount: int) -> str:
    cfg = SPORT_CONFIG[sport_key]

    if result == "win":
        score = reorder_score(score, result)
    elif result == "loss":
        score = reorder_score(score, result)

    if pick == result:
        amount = correct_amount
    elif pick:
        amount = wrong_amount
    else:
        return None

    emoji = {
        "win":  cfg["win_emoji"],
        "draw": cfg.get("draw_emoji", "🟡"),
        "loss": cfg["loss_emoji"],
    }.get(result, "")

    variants = RESULT_VARIANTS.get(result, RESULT_VARIANTS["loss"])
    variant  = random.choice(variants)
    return variant.format(
        emoji=emoji,
        team_name=team_name,
        opp=opponent,
        score=score,
        amount=amount,
    )

# ─────────────────────────────────────────────
# INSURANCE — EPL (halftime check)
# ─────────────────────────────────────────────

def get_epl_live(team_id: int) -> list:
    url = (f"https://api.football-data.org/v4/teams/{team_id}/matches"
           f"?status=IN_PLAY,PAUSED")
    resp = requests.get(url, headers={"X-Auth-Token": FOOTBALL_API_KEY}, timeout=10)
    if resp.status_code != 200:
        print(f"[EPL API] Error {resp.status_code} - {resp.text}")
        return []
    return resp.json().get("matches", [])

def check_epl_insurance(pending: dict, predictions: dict,
                        insurance_offered: set) -> bool:
    sent_any = False

    for match_id, data in pending.items():
        if data.get("sport") != "epl":
            continue

        team_id   = data["team_id"]
        team_name = data.get("team_name", "")
        opponent  = data.get("opponent", "")
        live      = get_epl_live(team_id)

        for match in live:
            if f"epl_{match['id']}_{team_id}" != match_id:
                continue

            status = match.get("status", "")
            if status != "PAUSED":
                print(f"[Insurance EPL] {match_id} not at halftime (status: {status})")
                continue

            score     = match["score"]["halfTime"]
            home_g    = score.get("home", 0) or 0
            away_g    = score.get("away", 0) or 0
            home_id   = match["homeTeam"]["id"]
            team_home = (home_id == team_id)

            team_score = home_g if team_home else away_g
            opp_score  = away_g if team_home else home_g
            score_str  = f"{home_g}-{away_g}"

            for phone in data.get("users", []):
                phone_n   = normalise_phone(phone)
                offer_key = f"{match_id}:{phone_n}"
                if offer_key in insurance_offered:
                    continue

                pred_data = predictions.get(match_id, {}).get(phone_n)
                if not pred_data:
                    continue

                if pred_data.get("status") == "insured":
                    continue

                user_pick      = pred_data.get("prediction")
                correct_amount = int(pred_data.get("correct_amount") or 0)
                wrong_amount   = int(pred_data.get("wrong_amount") or 0)

                pick_is_losing = (
                    (user_pick == "win"  and team_score < opp_score) or
                    (user_pick == "loss" and team_score > opp_score) or
                    (user_pick == "draw" and team_score != opp_score)
                )
                if not pick_is_losing:
                    continue

                insurance_amount = round((correct_amount + wrong_amount) / 2)

                variant = random.choice(INSURANCE_VARIANTS["epl"])
                msg = variant.format(
                    score=score_str,
                    team_name=team_name,
                    opp=opponent,
                    amount=insurance_amount,
                )

                send_message(f"whatsapp:+{phone_n}", msg)
                log_insurance_offered(match_id, phone_n, insurance_amount)
                insurance_offered.add(offer_key)
                sent_any = True
                print(f"[Insurance EPL] Offered ${insurance_amount} to {phone_n} for {match_id}.")

    return sent_any

# ─────────────────────────────────────────────
# INSURANCE — MLB (after 6th inning)
# ─────────────────────────────────────────────

def get_mlb_live_score(game_pk: int) -> dict:
    try:
        url  = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/linescore"
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            print(f"[MLB Live] HTTP {resp.status_code} for game {game_pk}")
            return {}
        return resp.json()
    except Exception as e:
        print(f"[MLB Live] Error fetching linescore for {game_pk}: {e}")
        return {}

def check_mlb_insurance(pending: dict, predictions: dict,
                        insurance_offered: set) -> bool:
    sent_any = False

    for match_id, data in pending.items():
        if data.get("sport") != "mlb":
            continue

        team_id   = data["team_id"]
        team_name = data.get("team_name", "")
        opponent  = data.get("opponent", "")

        try:
            game_pk = int(match_id.split("_")[1])
        except ValueError:
            print(f"[Insurance MLB] Could not parse game_pk from {match_id}")
            continue

        linescore = get_mlb_live_score(game_pk)
        if not linescore:
            print(f"[Insurance MLB] No linescore for {match_id} - game may not be live yet.")
            continue

        current_inning = linescore.get("currentInning", 0)
        home_runs      = linescore.get("teams", {}).get("home", {}).get("runs", 0)
        away_runs      = linescore.get("teams", {}).get("away", {}).get("runs", 0)

        if current_inning not in (6, 7):
            print(f"[Insurance MLB] {match_id} - inning {current_inning} - outside window.")
            continue

        try:
            game_info = statsapi.schedule(game_id=game_pk)
            if not game_info:
                continue
            home_team_id = game_info[0].get("home_id")
        except Exception as e:
            print(f"[Insurance MLB] {match_id} - statsapi error: {e}")
            continue

        if not home_team_id:
            continue

        team_home  = (home_team_id == team_id)
        team_score = home_runs if team_home else away_runs
        opp_score  = away_runs if team_home else home_runs
        score_str  = f"{away_runs}-{home_runs}"

        for phone in data.get("users", []):
            phone_n   = normalise_phone(phone)
            offer_key = f"{match_id}:{phone_n}"
            if offer_key in insurance_offered:
                continue

            pred_data = predictions.get(match_id, {}).get(phone_n)
            if not pred_data:
                continue

            if pred_data.get("status") == "insured":
                continue

            user_pick      = pred_data.get("prediction")
            correct_amount = int(pred_data.get("correct_amount") or 0)
            wrong_amount   = int(pred_data.get("wrong_amount") or 0)

            pick_is_losing = (
                (user_pick == "win"  and team_score < opp_score) or
                (user_pick == "loss" and team_score > opp_score)
            )
            if not pick_is_losing:
                continue

            insurance_amount = round((correct_amount + wrong_amount) / 2)

            variant = random.choice(INSURANCE_VARIANTS["mlb"])
            msg = variant.format(
                score=score_str,
                team_name=team_name,
                opp=opponent,
                innings=current_inning,
                amount=insurance_amount,
            )

            send_message(f"whatsapp:+{phone_n}", msg)
            log_insurance_offered(match_id, phone_n, insurance_amount)
            insurance_offered.add(offer_key)
            sent_any = True
            print(f"[Insurance MLB] Offered ${insurance_amount} to {phone_n} for {match_id}.")

    return sent_any

# ─────────────────────────────────────────────
# EPL API
# ─────────────────────────────────────────────

def get_epl_upcoming(team_id: int) -> list:
    today     = datetime.date.today()
    date_from = today.isoformat()
    date_to   = (today + datetime.timedelta(days=2)).isoformat()
    url = (f"https://api.football-data.org/v4/teams/{team_id}/matches"
           f"?status=TIMED,SCHEDULED&dateFrom={date_from}&dateTo={date_to}")
    resp = requests.get(url, headers={"X-Auth-Token": FOOTBALL_API_KEY}, timeout=10)
    if resp.status_code != 200:
        print(f"[EPL API] Error {resp.status_code} - {resp.text}")
        return []
    return resp.json().get("matches", [])

def get_epl_recent(team_id: int) -> list:
    today     = datetime.date.today()
    date_from = (today - datetime.timedelta(days=3)).isoformat()
    date_to   = today.isoformat()
    url = (f"https://api.football-data.org/v4/teams/{team_id}/matches"
           f"?status=FINISHED&dateFrom={date_from}&dateTo={date_to}")
    resp = requests.get(url, headers={"X-Auth-Token": FOOTBALL_API_KEY}, timeout=10)
    if resp.status_code != 200:
        print(f"[EPL API] Error {resp.status_code} - {resp.text}")
        return []
    return resp.json().get("matches", [])

def get_epl_postponed(team_id: int) -> list:
    today     = datetime.date.today()
    date_from = (today - datetime.timedelta(days=1)).isoformat()
    date_to   = (today + datetime.timedelta(days=7)).isoformat()
    url = (f"https://api.football-data.org/v4/teams/{team_id}/matches"
           f"?status=POSTPONED,CANCELLED,SUSPENDED&dateFrom={date_from}&dateTo={date_to}")
    resp = requests.get(url, headers={"X-Auth-Token": FOOTBALL_API_KEY}, timeout=10)
    if resp.status_code != 200:
        return []
    return resp.json().get("matches", [])

def epl_result_for_team(match: dict, team_id: int) -> str:
    home_id = match["homeTeam"]["id"]
    score   = match["score"]["fullTime"]
    hg, ag  = score["home"], score["away"]
    if hg == ag: return "draw"
    team_home = (home_id == team_id)
    home_more = (hg > ag)
    return "win" if (team_home and home_more) or (not team_home and not home_more) else "loss"

def epl_teams_from_match(match: dict, team_id: int) -> tuple:
    home = match["homeTeam"]["shortName"]
    away = match["awayTeam"]["shortName"]
    opp  = away if match["homeTeam"]["id"] == team_id else home
    return home, away, opp

def epl_score_str(match: dict) -> str:
    s = match["score"]["fullTime"]
    return f"{s['home']}-{s['away']}"

def epl_kickoff_utc(match: dict):
    return datetime.datetime.fromisoformat(
        match["utcDate"].replace("Z", "+00:00")
    ).replace(tzinfo=None)

def epl_match_key(match: dict) -> str:
    return f"epl_{match['id']}"

def epl_finished_dict(team_id: int) -> dict:
    return {epl_match_key(m): m for m in get_epl_recent(team_id)}

# ─────────────────────────────────────────────
# MLB API
# ─────────────────────────────────────────────

def get_mlb_upcoming(team_id: int) -> list:
    today   = datetime.date.today()
    date_to = (today + datetime.timedelta(days=2)).strftime("%m/%d/%Y")
    try:
        games = statsapi.schedule(
            team=team_id,
            start_date=today.strftime("%m/%d/%Y"),
            end_date=date_to,
        )
        print(f"[MLB API] {len(games)} games found for team {team_id}, statuses: {[g.get('status') for g in games]}")
        return [g for g in games if g.get("status") in MLB_PRE_GAME_STATUSES]
    except Exception as e:
        print(f"[MLB API] Upcoming error: {e}")
        return []

def get_mlb_recent(team_id: int) -> list:
    today     = datetime.date.today()
    date_from = (today - datetime.timedelta(days=3)).strftime("%m/%d/%Y")
    try:
        games = statsapi.schedule(
            team=team_id,
            start_date=date_from,
            end_date=today.strftime("%m/%d/%Y"),
        )
        return [g for g in games if g.get("status") in MLB_FINAL_STATUSES]
    except Exception as e:
        print(f"[MLB API] Recent error: {e}")
        return []

def mlb_result_for_team(game: dict, team_id: int) -> str:
    home_id    = game.get("home_id")
    home_score = game.get("home_score", 0)
    away_score = game.get("away_score", 0)
    team_home  = (home_id == team_id)
    if team_home:
        return "win" if home_score > away_score else "loss"
    return "win" if away_score > home_score else "loss"

def mlb_teams_from_game(game: dict, team_id: int) -> tuple:
    home = game.get("home_name", "")
    away = game.get("away_name", "")
    opp  = away if game.get("home_id") == team_id else home
    return home, away, opp

def mlb_score_str(game: dict) -> str:
    return f"{game.get('away_score', 0)}-{game.get('home_score', 0)}"

def mlb_kickoff_utc(game: dict):
    try:
        return datetime.datetime.fromisoformat(
            game.get("game_datetime", "").replace("Z", "+00:00")
        ).replace(tzinfo=None)
    except Exception:
        return None

def mlb_match_key(game: dict) -> str:
    return f"mlb_{game['game_id']}"

def mlb_finished_dict(team_id: int) -> dict:
    return {mlb_match_key(g): g for m in get_mlb_recent(team_id) for g in [m]}

# ─────────────────────────────────────────────
# SPORT API HANDLERS
# ─────────────────────────────────────────────

SPORT_API_HANDLERS = {
    "epl": {
        "get_upcoming":   get_epl_upcoming,
        "get_finished":   epl_finished_dict,
        "result_fn":      epl_result_for_team,
        "teams_fn":       epl_teams_from_match,
        "score_fn":       epl_score_str,
        "kickoff_fn":     epl_kickoff_utc,
        "match_key_fn":   epl_match_key,
    },
    "mlb": {
        "get_upcoming":   get_mlb_upcoming,
        "get_finished":   mlb_finished_dict,
        "result_fn":      mlb_result_for_team,
        "teams_fn":       mlb_teams_from_game,
        "score_fn":       mlb_score_str,
        "kickoff_fn":     mlb_kickoff_utc,
        "match_key_fn":   mlb_match_key,
    },
}

# ─────────────────────────────────────────────
# POSTPONED MATCH DETECTION
# ─────────────────────────────────────────────

def check_postponed(pending: dict, users: list) -> bool:
    """Alert users and cancel predictions for any pending EPL match that is now postponed."""
    sent_any    = False
    user_lookup = {normalise_phone(str(u["phone_number"])): u for u in users}

    for match_id, data in list(pending.items()):
        if data.get("sport") != "epl":
            continue

        team_id   = data["team_id"]
        team_name = data.get("team_name", "")
        opponent  = data.get("opponent", "")

        postponed = get_epl_postponed(team_id)
        postponed_ids = {f"epl_{m['id']}_{team_id}" for m in postponed}

        if match_id not in postponed_ids:
            continue

        print(f"[Postponed] {match_id} is postponed — notifying users.")

        for phone in data.get("users", []):
            phone_n = normalise_phone(phone)
            user    = user_lookup.get(phone_n, {})
            name    = user.get("name", "there")

            msg = POSTPONED_MSG.format(
                name=name,
                team_name=team_name,
                opp=opponent,
            )
            send_message(phone_n, msg)
            sent_any = True

        cancel_predictions_for_match(match_id)
        mark_match_settled(match_id)

    return sent_any

# ─────────────────────────────────────────────
# GENERIC PRE-MATCH TRIGGER
# ─────────────────────────────────────────────

def check_pre_match(users: list, sent_per_user: dict, sport_key: str) -> bool:
    cfg      = SPORT_CONFIG[sport_key]
    handlers = SPORT_API_HANDLERS[sport_key]
    team_ids = SPORT_TEAM_IDS[sport_key]
    field    = cfg["user_field"]

    sport_users = [u for u in users if u.get(field)]
    if not sport_users:
        print(f"[{sport_key.upper()}] No users with {field} set.")
        return False

    teams_to_users = {}
    for u in sport_users:
        teams_to_users.setdefault(u[field], []).append(u)

    sent_any = False
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    for team_name, team_users in teams_to_users.items():
        team_id = team_ids.get(team_name)
        if not team_id:
            print(f"[{sport_key.upper()}] Unknown team: {team_name}")
            continue

        for event in handlers["get_upcoming"](team_id):
            match_id = f"{handlers['match_key_fn'](event)}_{team_id}"
            kickoff  = handlers["kickoff_fn"](event)
            if not kickoff:
                continue
            mins = (kickoff - now).total_seconds() / 60
            if not (PROMPT_WINDOW_MIN <= mins <= PROMPT_WINDOW_MAX):
                print(f"[{sport_key.upper()}] {team_name} in {mins:.0f} mins - outside window.")
                continue

            home, away, opp = handlers["teams_fn"](event, team_id)
            newly_notified  = []

            for u in team_users:
                phone   = str(u["phone_number"])
                phone_n = normalise_phone(phone)

                if match_id in sent_per_user.get(phone_n, set()):
                    print(f"[{sport_key.upper()}] {match_id} already sent to {phone_n}.")
                    continue

                amt = calculate_amounts(u, phone_n)

                if amt["cap_exhausted"]:
                    print(f"[{sport_key.upper()}] {phone_n} has hit weekly cap - skipping prompt.")
                    continue

                name = u.get("name", "there")
                msg  = build_prompt_message(
                    name, home, away, sport_key,
                    amt["correct_amount"], amt["wrong_amount"],
                    team_name=team_name,
                    near_cap=amt["near_cap"],
                    cap_exhausted=amt["cap_exhausted"],
                    remaining=amt["remaining"],
                    mins_until_kickoff=round(mins),
                    opponent=opp,
                )

                send_message(phone_n, msg)
                write_prediction_pending(phone, match_id, amt["correct_amount"], amt["wrong_amount"])
                log_sent_match(match_id, sport_key, team_name, phone)
                sent_per_user.setdefault(phone_n, set()).add(match_id)
                newly_notified.append(phone_n)
                sent_any = True
                print(f"[{sport_key.upper()}] Prompt sent to {phone_n} for {match_id} "
                      f"(correct=${amt['correct_amount']}, wrong=${amt['wrong_amount']}).")

            if newly_notified:
                existing_pending = get_pending_matches()
                if match_id not in existing_pending:
                    log_pending_match(match_id, {
                        "sport":       sport_key,
                        "team_id":     team_id,
                        "team_name":   team_name,
                        "opponent":    opp,
                        "users":       newly_notified,
                        "kickoff_utc": kickoff.isoformat() if kickoff else "",
                    })
                else:
                    append_users_to_pending(match_id, newly_notified)

    return sent_any

# ─────────────────────────────────────────────
# GENERIC POST-MATCH SETTLEMENT
# ─────────────────────────────────────────────

def check_post_match(pending: dict) -> bool:
    sent_any = False
    for match_id, data in list(pending.items()):
        sport_key = data.get("sport", "epl")
        handlers  = SPORT_API_HANDLERS.get(sport_key)
        if not handlers:
            print(f"[Post] Unknown sport: {sport_key} for {match_id}")
            continue

        team_id      = data["team_id"]
        raw_match_id = "_".join(match_id.split("_")[:2])
        finished     = handlers["get_finished"](team_id)

        if raw_match_id not in finished:
            print(f"[Post] {sport_key.upper()} {match_id} not finished yet.")
            continue

        event       = finished[raw_match_id]
        result      = handlers["result_fn"](event, team_id)
        score       = handlers["score_fn"](event)
        predictions = get_predictions_for_match(match_id)

        for phone in data.get("users", []):
            phone_n   = normalise_phone(phone)
            pred_data = predictions.get(phone_n)

            if not pred_data:
                print(f"[Post] {match_id} - {phone_n} no prediction row, skipping.")
                continue

            if pred_data.get("status") == "insured":
                print(f"[Post] {match_id} - {phone_n} took insurance, skipping.")
                continue

            pick = pred_data.get("prediction", "")
            if not pick or pick.upper() == "N/A":
                print(f"[Post] {match_id} - {phone_n} no pick / N/A, skipping.")
                continue

            correct_amount = int(pred_data.get("correct_amount") or 0)
            wrong_amount   = int(pred_data.get("wrong_amount") or 0)
            logged_amount  = correct_amount if pick == result else wrong_amount

            msg = build_result_message(
                sport_key, data["team_name"], data["opponent"],
                score, result, pick, correct_amount, wrong_amount,
            )

            if msg is None:
                print(f"[Post] {match_id} - {phone_n} no pick, skipping message.")
                continue

            send_message(phone_n, msg)
            log_bet_to_sheet(phone_n, match_id, pick, logged_amount, result, sport_key)
            print(f"[Post] {match_id} - {phone_n} picked {pick}, result {result}, "
                  f"logged ${logged_amount}.")

        mark_match_settled(match_id)
        sent_any = True
    return sent_any

# ─────────────────────────────────────────────
# REMINDERS
# ─────────────────────────────────────────────

def get_predictions_pending_reminder() -> list:
    try:
        sb     = get_client()
        result = sb.table("predictions") \
            .select("*") \
            .eq("status", "pending") \
            .eq("reminder_sent", False) \
            .execute()
        return [r for r in (result.data or []) if not r.get("prediction", "").strip()]
    except Exception as e:
        print(f"[Predictions] Error fetching pending reminders: {e}")
        return []

def mark_reminder_sent(prediction_id: int):
    try:
        sb = get_client()
        sb.table("predictions").update({"reminder_sent": True}).eq("id", prediction_id).execute()
    except Exception as e:
        print(f"[Predictions] Error marking reminder sent: {e}")

def check_reminders(users: list, pending: dict) -> bool:
    sent_any = False
    now      = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    user_lookup   = {normalise_phone(str(u["phone_number"])): u for u in users}
    pending_preds = get_predictions_pending_reminder()

    for pred in pending_preds:
        match_id = pred.get("match_id")
        phone_n  = normalise_phone(str(pred.get("user_phone", "")))

        match_data = pending.get(match_id)
        if not match_data:
            continue

        kickoff_str = match_data.get("kickoff_utc", "")
        if not kickoff_str:
            continue

        try:
            kickoff = datetime.datetime.fromisoformat(kickoff_str)
        except ValueError:
            continue

        mins = (kickoff - now).total_seconds() / 60
        if not (0 < mins < 15):
            continue

        sport_key = match_data.get("sport", "epl")
        user      = user_lookup.get(phone_n, {})
        name      = user.get("name", "there")
        team_name = match_data.get("team_name", "Your team")
        opponent  = match_data.get("opponent", "")

        variant = random.choice(REMINDER_VARIANTS[sport_key])
        msg = variant.format(
            name=name,
            team_name=team_name,
            opp=opponent,
            mins=max(1, round(mins)),
        )

        send_message(phone_n, msg)
        mark_reminder_sent(pred["id"])
        sent_any = True
        print(f"[Reminder] Sent to {phone_n} for {match_id}.")

    return sent_any

# ─────────────────────────────────────────────
# LOCK UNPICKED BETS
# ─────────────────────────────────────────────

def lock_unpicked_started_matches(pending: dict):
    try:
        sb     = get_client()
        result = sb.table("predictions").select("*").eq("status", "pending").execute()
        now    = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

        for r in (result.data or []):
            match_id   = r.get("match_id", "")
            match_data = pending.get(match_id)
            if not match_data:
                continue

            kickoff_str = match_data.get("kickoff_utc", "")
            if not kickoff_str:
                continue

            try:
                kickoff = datetime.datetime.fromisoformat(kickoff_str)
            except ValueError:
                continue

            if now >= kickoff:
                update = {"status": "locked"}
                if not r.get("prediction", "").strip():
                    update["prediction"] = "N/A"
                sb.table("predictions").update(update).eq("id", r["id"]).execute()
                print(f"[Lock] Auto-locked for {r.get('user_phone')} on {match_id}")

    except Exception as e:
        print(f"[Lock] Error: {e}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print(f"\n=== Akrue - {datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)} UTC ===\n")
    test_mode = "--test" in sys.argv or os.getenv("TEST_MODE") == "1"
    if test_mode:
        users = get_active_users()
        for user in users:
            teams = [
                f"{cfg['name']}: {user.get(cfg['user_field'], '')}"
                for sport_key, cfg in SPORT_CONFIG.items()
                if user.get(cfg["user_field"])
            ]
            send_message(normalise_phone(str(user['phone_number'])), (
                f"Akrue test — hey {user.get('name','there')}!\n"
                f"Teams: {' | '.join(teams) or 'none set'}\n"
                f"System is live and ready for match prompts!"
            ))
        print(f"[Test] Sent to {len(users)} users.")
        return

    users = get_active_users()
    if not users:
        print("[Main] No active users. Exiting.")
        return

    sent_per_user     = get_sent_match_ids()
    pending           = get_pending_matches()
    lock_unpicked_started_matches(pending)
    insurance_offered = get_insurance_offered()
    all_preds         = get_all_predictions(pending)

    fired = False
    if check_postponed(pending, users):                               fired = True
    for sport_key in SPORT_CONFIG:
        if check_pre_match(users, sent_per_user, sport_key):
            fired = True
    if check_epl_insurance(pending, all_preds, insurance_offered): fired = True
    if check_mlb_insurance(pending, all_preds, insurance_offered): fired = True
    if check_post_match(pending):                                   fired = True
    if check_reminders(users, pending):                             fired = True

    print("\n[Done] Prompts sent!" if fired else "\n[Done] No matches in window right now.")

if __name__ == "__main__":
    main()
