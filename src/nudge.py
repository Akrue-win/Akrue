"""
Project Free Kick — Core Nudge Script
--------------------------------------
Sends WhatsApp prediction prompts before Liverpool matches
and scheduled savings nudges on set days.

All credentials loaded from environment variables only.
Never hardcode secrets in this file.
"""

import os
import random
import json
import datetime
import requests
from twilio.rest import Client

# ─────────────────────────────────────────────
# CONFIG — all from environment variables
# ─────────────────────────────────────────────

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM_NUMBER = os.environ["TWILIO_FROM_NUMBER"]
FOOTBALL_API_KEY   = os.environ["FOOTBALL_API_KEY"]

# Support multiple users — comma separated in env var
# e.g. "whatsapp:+12039394042,whatsapp:+12035551234"
USER_NUMBERS = [
    n.strip() for n in os.environ["USER_PHONE_NUMBERS"].split(",")
]

# Saving amount ranges per trigger (USD)
SAVE_RANGES = {
    "scheduled":       (5,  25),   # Regular weekly nudge
    "win_correct":     (20, 40),   # Predicted win correctly
    "win_wrong":       (8,  12),   # Got result wrong (base amount)
    "draw_correct":    (15, 25),   # Predicted draw correctly
    "draw_wrong":      (8,  12),
    "loss_correct":    (25, 40),   # Predicted loss correctly (hardest)
    "loss_wrong":      (8,  12),
}

LIVERPOOL_TEAM_ID = 64
LOG_FILE = "nudge_log.json"

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def load_log() -> dict:
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
            return json.load(f)
    return {
        "sent_match_ids": [],
        "predictions": {},        # {match_id: {user_number: "win"/"draw"/"loss"}}
        "last_scheduled": None,
    }


def save_log(log: dict):
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


def random_amount(trigger: str) -> int:
    lo, hi = SAVE_RANGES[trigger]
    return random.randint(lo, hi)


def send_sms(to_number: str, message: str):
    """Send a WhatsApp message to a single user."""
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    msg = client.messages.create(
        body=message,
        from_=TWILIO_FROM_NUMBER,
        to=to_number,
    )
    print(f"[SMS → {to_number}] SID: {msg.sid}")


def broadcast(message: str):
    """Send a WhatsApp message to all users."""
    for number in USER_NUMBERS:
        send_sms(number, message)


# ─────────────────────────────────────────────
# TRIGGER 1 — Scheduled random nudge
# ─────────────────────────────────────────────

def check_scheduled_nudge(log: dict) -> bool:
    today = datetime.date.today()
    weekday = today.weekday()  # 0=Mon, 2=Wed, 4=Fri

    nudge_days = [int(d) for d in os.getenv("NUDGE_DAYS", "0,2,4").split(",")]

    if weekday not in nudge_days:
        print("[Scheduled] Not a nudge day today.")
        return False

    if log.get("last_scheduled") == str(today):
        print("[Scheduled] Already sent today.")
        return False

    amount = random_amount("scheduled")
    day_name = today.strftime("%A")
    message = (
        f"⏰ {day_name} savings nudge — stash ${amount} today? "
        f"Reply YES to log it and keep your streak going 💰"
    )
    broadcast(message)
    log["last_scheduled"] = str(today)
    return True


# ─────────────────────────────────────────────
# TRIGGER 2 — Liverpool pre-match prediction
# ─────────────────────────────────────────────

def get_upcoming_liverpool_matches() -> list:
    """Fetch Liverpool matches in the next 2 days."""
    today = datetime.date.today()
    date_to = (today + datetime.timedelta(days=2)).isoformat()

    url = (
        f"https://api.football-data.org/v4/teams/{LIVERPOOL_TEAM_ID}/matches"
        f"?status=SCHEDULED&dateTo={date_to}"
    )
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}
    resp = requests.get(url, headers=headers, timeout=10)

    if resp.status_code != 200:
        print(f"[Football API] Error {resp.status_code}: {resp.text}")
        return []

    return resp.json().get("matches", [])


def get_recent_liverpool_matches() -> list:
    """Fetch Liverpool matches finished in the last 3 days."""
    today = datetime.date.today()
    date_from = (today - datetime.timedelta(days=3)).isoformat()

    url = (
        f"https://api.football-data.org/v4/teams/{LIVERPOOL_TEAM_ID}/matches"
        f"?status=FINISHED&dateFrom={date_from}"
    )
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}
    resp = requests.get(url, headers=headers, timeout=10)

    if resp.status_code != 200:
        print(f"[Football API] Error {resp.status_code}")
        return []

    return resp.json().get("matches", [])


def result_for_liverpool(match: dict) -> str:
    """Return 'win', 'draw', or 'loss' from Liverpool's perspective."""
    home_id = match["homeTeam"]["id"]
    score   = match["score"]["fullTime"]
    home_g  = score["home"]
    away_g  = score["away"]

    if home_g == away_g:
        return "draw"

    liverpool_home = (home_id == LIVERPOOL_TEAM_ID)
    liverpool_scored_more = (home_g > away_g)

    if (liverpool_home and liverpool_scored_more) or \
       (not liverpool_home and not liverpool_scored_more):
        return "win"
    return "loss"


def check_pre_match(log: dict) -> bool:
    """Send prediction prompt ~30 mins before kickoff."""
    matches = get_upcoming_liverpool_matches()
    if not matches:
        print("[Pre-match] No upcoming matches.")
        return False

    sent_any = False
    now = datetime.datetime.utcnow()

    for match in matches:
        match_id = str(match["id"])
        if match_id in log.get("sent_match_ids", []):
            continue

        # Parse kickoff time
        kickoff_str = match["utcDate"]  # e.g. "2026-05-15T19:45:00Z"
        kickoff = datetime.datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
        kickoff = kickoff.replace(tzinfo=None)

        mins_to_kickoff = (kickoff - now).total_seconds() / 60

        # Send prompt if within 25–45 mins of kickoff
        if not (25 <= mins_to_kickoff <= 45):
            print(f"[Pre-match] Match {match_id} kickoff in {mins_to_kickoff:.0f} mins — not time yet.")
            continue

        opponent_key = "awayTeam" if match["homeTeam"]["id"] == LIVERPOOL_TEAM_ID else "homeTeam"
        opponent = match[opponent_key]["shortName"]
        home_team = match["homeTeam"]["shortName"]
        away_team = match["awayTeam"]["shortName"]

        win_amount  = random_amount("win_correct")
        draw_amount = random_amount("draw_correct")
        loss_amount = random_amount("loss_correct")
        base_amount = random_amount("win_wrong")

        message = (
            f"⚽ *{home_team} vs {away_team}* kicks off in ~30 mins!\n\n"
            f"Make your prediction:\n"
            f"1️⃣  Liverpool Win  → save *${win_amount}* if correct (${base_amount} if not)\n"
            f"2️⃣  Draw          → save *${draw_amount}* if correct (${base_amount} if not)\n"
            f"3️⃣  Liverpool Loss → save *${loss_amount}* if correct (${base_amount} if not)\n\n"
            f"Reply *1*, *2*, or *3* to lock in your pick 🔒"
        )

        broadcast(message)

        # Store the amounts so post-match knows what to log
        log.setdefault("pending_matches", {})[match_id] = {
            "opponent": opponent,
            "win_amount": win_amount,
            "draw_amount": draw_amount,
            "loss_amount": loss_amount,
            "base_amount": base_amount,
        }
        log.setdefault("sent_match_ids", []).append(match_id)
        sent_any = True

    return sent_any


def check_post_match(log: dict) -> bool:
    """Check finished matches and send result + savings amount."""
    matches = get_recent_liverpool_matches()
    if not matches:
        print("[Post-match] No recent finished matches.")
        return False

    sent_any = False
    pending = log.get("pending_matches", {})
    settled = log.get("settled_match_ids", [])

    for match in matches:
        match_id = str(match["id"])

        if match_id in settled:
            continue

        if match_id not in pending:
            continue

        result = result_for_liverpool(match)
        match_data = pending[match_id]
        opponent = match_data["opponent"]

        home_goals = match["score"]["fullTime"]["home"]
        away_goals = match["score"]["fullTime"]["away"]
        score_str  = f"{home_goals}–{away_goals}"

        if result == "win":
            emoji = "🔴"
            headline = f"Liverpool beat {opponent} {score_str}!"
            correct_key = "win"
        elif result == "draw":
            emoji = "😤"
            headline = f"Liverpool drew with {opponent} {score_str}"
            correct_key = "draw"
        else:
            emoji = "😩"
            headline = f"Liverpool lost to {opponent} {score_str}"
            correct_key = "loss"

        # Send personalised result to each user
        predictions = log.get("predictions", {}).get(match_id, {})

        for number in USER_NUMBERS:
            user_pick = predictions.get(number)

            if user_pick == correct_key:
                amount    = match_data[f"{correct_key}_amount"]
                pick_msg  = f"✅ You called it! Save *${amount}* to celebrate 💰"
            elif user_pick:
                amount   = match_data["base_amount"]
                pick_msg = f"❌ Unlucky — you picked {user_pick}. Base save: *${amount}* 💰"
            else:
                amount   = match_data["base_amount"]
                pick_msg = f"You didn't pick this one. Base save: *${amount}* 💰"

            message = f"{emoji} {headline}\n\n{pick_msg}"
            send_sms(number, message)

        log.setdefault("settled_match_ids", []).append(match_id)
        sent_any = True

    return sent_any


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    import sys
    print(f"\n=== Project Free Kick — {datetime.datetime.now()} ===\n")
    log = load_log()
    fired = False

    test_mode = "--test" in sys.argv or os.getenv("TEST_MODE") == "1"

    if test_mode:
        amount = random_amount("scheduled")
        broadcast(f"🧪 Free Kick test — save ${amount} today? Reply YES 💰")
        print("[Test] Broadcast sent!")
        save_log(log)
        return

    if check_scheduled_nudge(log):
        fired = True

    if check_pre_match(log):
        fired = True

    if check_post_match(log):
        fired = True

    save_log(log)

    if not fired:
        print("\n[Done] No nudges to send right now.")
    else:
        print("\n[Done] Nudge(s) sent!")


if __name__ == "__main__":
    main()
