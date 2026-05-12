"""
Project Free Kick — Core Script
---------------------------------
Reads active users from Google Sheets, sends pre-match bet prompts
to users whose team is playing, and settles bets after matches finish.

All credentials loaded from environment variables only.
Never hardcode secrets in this file.
"""

import os
import sys
import json
import random
import datetime
import requests
import gspread
from twilio.rest import Client
from google.oauth2.service_account import Credentials

# ─────────────────────────────────────────────
# CONFIG — all from environment variables
# ─────────────────────────────────────────────

TWILIO_ACCOUNT_SID  = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN   = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM_NUMBER  = os.environ["TWILIO_FROM_NUMBER"]
FOOTBALL_API_KEY    = os.environ["FOOTBALL_API_KEY"]
SHEET_ID            = os.environ["SHEET_ID"]
GOOGLE_CREDS_JSON   = os.environ["GOOGLE_CREDS_JSON"]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Bet amount ranges per outcome (USD)
BET_RANGES = {
    "scheduled":    (5,  25),
    "win_correct":  (20, 40),
    "win_wrong":    (8,  12),
    "draw_correct": (15, 25),
    "draw_wrong":   (8,  12),
    "loss_correct": (25, 40),
    "loss_wrong":   (8,  12),
}

# Map EPL team names to football-data.org team IDs
EPL_TEAM_IDS = {
    "Arsenal": 57, "Aston Villa": 58, "Bournemouth": 1044,
    "Brentford": 402, "Brighton": 397, "Chelsea": 61,
    "Crystal Palace": 354, "Everton": 62, "Fulham": 63,
    "Ipswich Town": 349, "Leicester City": 338, "Liverpool": 64,
    "Manchester City": 65, "Manchester United": 66, "Newcastle United": 67,
    "Nottingham Forest": 351, "Southampton": 340, "Tottenham Hotspur": 73,
    "West Ham United": 563, "Wolverhampton": 76,
}

LOG_FILE = "freekick_log.json"

# ─────────────────────────────────────────────
# GOOGLE SHEETS
# ─────────────────────────────────────────────

def get_sheets_client():
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)

def get_active_users() -> list:
    try:
        client = get_sheets_client()
        sheet  = client.open_by_key(SHEET_ID).worksheet("Users")
        users  = sheet.get_all_records()
        active = [u for u in users if u.get("status", "").lower() == "active"]
        print(f"[Users] Found {len(active)} active users.")
        return active
    except Exception as e:
        print(f"[Users] Error reading sheet: {e}")
        return []

def log_bet_to_sheet(user_phone, match_id, prediction, amount, result, week):
    try:
        client = get_sheets_client()
        sheet  = client.open_by_key(SHEET_ID).worksheet("Savings_Log")
        sheet.append_row([
            datetime.date.today().isoformat(),
            user_phone, amount,
            f"epl_bet_{result}", match_id, week,
        ])
        print(f"[Sheet] Logged ${amount} for {user_phone}")
    except Exception as e:
        print(f"[Sheet] Error logging bet: {e}")

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def load_log() -> dict:
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
            return json.load(f)
    return {"sent_match_ids": [], "settled_match_ids": [],
            "pending_matches": {}, "predictions": {}, "last_scheduled": None}

def save_log(log: dict):
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)

def random_amount(trigger: str) -> int:
    lo, hi = BET_RANGES[trigger]
    return random.randint(lo, hi)

def send_whatsapp(to_number: str, message: str):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    msg = client.messages.create(body=message, from_=TWILIO_FROM_NUMBER, to=to_number)
    print(f"[WhatsApp -> {to_number}] SID: {msg.sid}")

def current_week() -> str:
    today = datetime.date.today()
    return f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"

# ─────────────────────────────────────────────
# FOOTBALL API
# ─────────────────────────────────────────────

def get_upcoming_matches(team_id: int) -> list:
    today   = datetime.date.today()
    date_to = (today + datetime.timedelta(days=2)).isoformat()
    url = (f"https://api.football-data.org/v4/teams/{team_id}/matches"
           f"?status=SCHEDULED&dateTo={date_to}")
    resp = requests.get(url, headers={"X-Auth-Token": FOOTBALL_API_KEY}, timeout=10)
    if resp.status_code != 200:
        print(f"[Football API] Error {resp.status_code}: {resp.text}")
        return []
    return resp.json().get("matches", [])

def get_recent_matches(team_id: int) -> list:
    today     = datetime.date.today()
    date_from = (today - datetime.timedelta(days=3)).isoformat()
    date_to   = today.isoformat()
    url = (f"https://api.football-data.org/v4/teams/{team_id}/matches"
           f"?status=FINISHED&dateFrom={date_from}&dateTo={date_to}")
    resp = requests.get(url, headers={"X-Auth-Token": FOOTBALL_API_KEY}, timeout=10)
    if resp.status_code != 200:
        print(f"[Football API] Error {resp.status_code}")
        return []
    return resp.json().get("matches", [])

def result_for_team(match: dict, team_id: int) -> str:
    home_id = match["homeTeam"]["id"]
    score   = match["score"]["fullTime"]
    home_g  = score["home"]
    away_g  = score["away"]
    if home_g == away_g:
        return "draw"
    team_is_home     = (home_id == team_id)
    home_scored_more = (home_g > away_g)
    if (team_is_home and home_scored_more) or (not team_is_home and not home_scored_more):
        return "win"
    return "loss"

# ─────────────────────────────────────────────
# TRIGGER 1 — Weekly scheduled bet reminder
# ─────────────────────────────────────────────

def check_scheduled_bets(log: dict, users: list) -> bool:
    today   = datetime.date.today()
    weekday = today.weekday()
    if weekday not in (0, 2, 4):
        print("[Scheduled] Not a bet day today.")
        return False
    if log.get("last_scheduled") == str(today):
        print("[Scheduled] Already sent today.")
        return False

    sent_any = False
    day_name = today.strftime("%A")

    for user in users:
        phone       = user["phone_number"]
        name        = user.get("name", "there")
        bankroll    = user.get("weekly_bankroll", 50)
        bets_per_wk = int(user.get("bets_per_week", 3))
        send_days   = [0, 2, 4, 1, 3, 5, 6][:bets_per_wk]
        if weekday not in send_days:
            continue
        amount  = random_amount("scheduled")
        message = (
            f"Hey {name}! {day_name} savings bet — stash *${amount}* today?\n\n"
            f"Weekly bankroll goal: *${bankroll}*\n"
            f"Reply *YES* to log it 💰"
        )
        send_whatsapp(phone, message)
        sent_any = True

    log["last_scheduled"] = str(today)
    return sent_any

# ─────────────────────────────────────────────
# TRIGGER 2 — Pre-match bet prompt
# ─────────────────────────────────────────────

def check_pre_match(log: dict, users: list) -> bool:
    if not users:
        return False

    teams_to_users = {}
    for user in users:
        team = user.get("epl_team", "")
        if team:
            teams_to_users.setdefault(team, []).append(user)

    sent_any = False
    now = datetime.datetime.utcnow()

    for team_name, team_users in teams_to_users.items():
        team_id = EPL_TEAM_IDS.get(team_name)
        if not team_id:
            print(f"[Pre-match] Unknown team: {team_name}")
            continue

        for match in get_upcoming_matches(team_id):
            match_id = str(match["id"])
            if match_id in log.get("sent_match_ids", []):
                continue

            kickoff = datetime.datetime.fromisoformat(
                match["utcDate"].replace("Z", "+00:00")
            ).replace(tzinfo=None)
            mins_to_kickoff = (kickoff - now).total_seconds() / 60

            if not (25 <= mins_to_kickoff <= 45):
                print(f"[Pre-match] {team_name} match {match_id} in {mins_to_kickoff:.0f} mins.")
                continue

            home_team   = match["homeTeam"]["shortName"]
            away_team   = match["awayTeam"]["shortName"]
            opponent    = away_team if match["homeTeam"]["id"] == team_id else home_team
            win_amount  = random_amount("win_correct")
            draw_amount = random_amount("draw_correct")
            loss_amount = random_amount("loss_correct")
            base_amount = random_amount("win_wrong")

            for user in team_users:
                phone = user["phone_number"]
                name  = user.get("name", "there")
                message = (
                    f"Hey {name}! *{home_team} vs {away_team}* kicks off in ~30 mins!\n\n"
                    f"Place your bet:\n"
                    f"1 - {team_name} Win  -> save *${win_amount}* correct / *${base_amount}* wrong\n"
                    f"2 - Draw           -> save *${draw_amount}* correct / *${base_amount}* wrong\n"
                    f"3 - {team_name} Loss -> save *${loss_amount}* correct / *${base_amount}* wrong\n\n"
                    f"Reply *1*, *2*, or *3* to lock in your bet"
                )
                send_whatsapp(phone, message)

            log.setdefault("pending_matches", {})[match_id] = {
                "team_id": team_id, "team_name": team_name, "opponent": opponent,
                "win_amount": win_amount, "draw_amount": draw_amount,
                "loss_amount": loss_amount, "base_amount": base_amount,
                "users": [u["phone_number"] for u in team_users],
            }
            log.setdefault("sent_match_ids", []).append(match_id)
            sent_any = True

    return sent_any

# ─────────────────────────────────────────────
# TRIGGER 3 — Post-match settlement
# ─────────────────────────────────────────────

def check_post_match(log: dict) -> bool:
    pending  = log.get("pending_matches", {})
    settled  = log.get("settled_match_ids", [])
    sent_any = False
    week     = current_week()

    for match_id, match_data in list(pending.items()):
        if match_id in settled:
            continue

        team_id = match_data["team_id"]
        matches = get_recent_matches(team_id)
        finished = {str(m["id"]): m for m in matches}

        if match_id not in finished:
            print(f"[Post-match] Match {match_id} not finished yet.")
            continue

        match     = finished[match_id]
        result    = result_for_team(match, team_id)
        team_name = match_data["team_name"]
        opponent  = match_data["opponent"]
        home_g    = match["score"]["fullTime"]["home"]
        away_g    = match["score"]["fullTime"]["away"]
        score_str = f"{home_g}-{away_g}"

        emoji    = "🟢" if result == "win" else ("🟡" if result == "draw" else "🔴")
        headline = (
            f"{team_name} beat {opponent} {score_str}!" if result == "win"
            else f"{team_name} drew with {opponent} {score_str}" if result == "draw"
            else f"{team_name} lost to {opponent} {score_str}"
        )

        predictions = log.get("predictions", {}).get(match_id, {})

        for phone in match_data.get("users", []):
            user_pick = predictions.get(phone)
            if user_pick == result:
                amount   = match_data[f"{result}_amount"]
                pick_msg = f"You called it! Save *${amount}* 💰"
            elif user_pick:
                amount   = match_data["base_amount"]
                pick_msg = f"Unlucky — you picked {user_pick}. Base save: *${amount}* 💰"
            else:
                amount   = match_data["base_amount"]
                pick_msg = f"No bet placed. Base save: *${amount}* 💰"

            send_whatsapp(phone, f"{emoji} *{headline}*\n\n{pick_msg}\n\nKeep building that bankroll!")
            log_bet_to_sheet(phone, match_id, user_pick or "none", amount, result, week)

        log.setdefault("settled_match_ids", []).append(match_id)
        sent_any = True

    return sent_any

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print(f"\n=== Project Free Kick — {datetime.datetime.now()} ===\n")
    log   = load_log()
    fired = False

    test_mode = "--test" in sys.argv or os.getenv("TEST_MODE") == "1"

    if test_mode:
        users = get_active_users()
        for user in users:
            amount = random_amount("scheduled")
            send_whatsapp(
                user["phone_number"],
                f"Free Kick test — hey {user.get('name','there')}! Save *${amount}* today? Reply YES"
            )
        print(f"[Test] Sent to {len(users)} users.")
        save_log(log)
        return

    users = get_active_users()
    if not users:
        print("[Main] No active users found. Exiting.")
        save_log(log)
        return

    if check_scheduled_bets(log, users): fired = True
    if check_pre_match(log, users):      fired = True
    if check_post_match(log):            fired = True

    save_log(log)
    print("\n[Done] Bets sent!" if fired else "\n[Done] No bets to send right now.")

if __name__ == "__main__":
    main()
