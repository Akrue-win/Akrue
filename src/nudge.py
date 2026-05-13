"""
Akrue — Core Script
--------------------
Reads active users from Google Sheets, sends pre-match bet prompts
to users whose team is playing, settles bets after matches finish.

Supports EPL (football-data.org) and MLB (MLB Stats API).
All credentials loaded from environment variables only.
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

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM_NUMBER = os.environ["TWILIO_FROM_NUMBER"]
FOOTBALL_API_KEY   = os.environ["FOOTBALL_API_KEY"]
SHEET_ID           = os.environ["SHEET_ID"]
GOOGLE_CREDS_JSON  = os.environ["GOOGLE_CREDS_JSON"]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

BET_RANGES = {
    "scheduled":    (5,  25),
    "win_correct":  (20, 40),
    "win_wrong":    (8,  12),
    "draw_correct": (15, 25),
    "draw_wrong":   (8,  12),
    "loss_correct": (25, 40),
    "loss_wrong":   (8,  12),
}

EPL_TEAM_IDS = {
    "Arsenal": 57, "Aston Villa": 58, "Bournemouth": 1044,
    "Brentford": 402, "Brighton": 397, "Chelsea": 61,
    "Crystal Palace": 354, "Everton": 62, "Fulham": 63,
    "Ipswich Town": 349, "Leicester City": 338, "Liverpool": 64,
    "Manchester City": 65, "Manchester United": 66, "Newcastle United": 67,
    "Nottingham Forest": 351, "Southampton": 340, "Tottenham Hotspur": 73,
    "West Ham United": 563, "Wolverhampton": 76,
}

MLB_TEAM_IDS = {
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
}

LOG_FILE = "akrue_log.json"
MLB_API  = "https://statsapi.mlb.com/api/v1"

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
        print(f"[Users] Error: {e}")
        return []

def log_bet_to_sheet(user_phone, match_id, prediction, amount, result, week, sport):
    try:
        client = get_sheets_client()
        sheet  = client.open_by_key(SHEET_ID).worksheet("Savings_Log")
        sheet.append_row([
            datetime.date.today().isoformat(),
            user_phone, amount,
            f"{sport}_bet_{result}", match_id, week, sport,
        ])
        print(f"[Sheet] Logged ${amount} for {user_phone} ({sport})")
    except Exception as e:
        print(f"[Sheet] Error: {e}")

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
    msg = client.messages.create(
        body=message, from_=TWILIO_FROM_NUMBER, to=to_number)
    print(f"[WhatsApp -> {to_number}] SID: {msg.sid}")

def current_week() -> str:
    today = datetime.date.today()
    return f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"

# ─────────────────────────────────────────────
# EPL API
# ─────────────────────────────────────────────

def get_epl_upcoming(team_id: int) -> list:
    today   = datetime.date.today()
    date_to = (today + datetime.timedelta(days=2)).isoformat()
    url = (f"https://api.football-data.org/v4/teams/{team_id}/matches"
           f"?status=SCHEDULED&dateTo={date_to}")
    resp = requests.get(url, headers={"X-Auth-Token": FOOTBALL_API_KEY}, timeout=10)
    if resp.status_code != 200:
        print(f"[EPL API] Error {resp.status_code}")
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
        print(f"[EPL API] Error {resp.status_code}")
        return []
    return resp.json().get("matches", [])

def epl_result_for_team(match: dict, team_id: int) -> str:
    home_id = match["homeTeam"]["id"]
    score   = match["score"]["fullTime"]
    home_g, away_g = score["home"], score["away"]
    if home_g == away_g: return "draw"
    team_home = (home_id == team_id)
    home_more = (home_g > away_g)
    return "win" if (team_home and home_more) or (not team_home and not home_more) else "loss"

# ─────────────────────────────────────────────
# MLB API (no key needed)
# ─────────────────────────────────────────────

def get_mlb_upcoming(team_id: int) -> list:
    today   = datetime.date.today()
    date_to = (today + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
    url = (f"{MLB_API}/schedule?sportId=1&teamId={team_id}"
           f"&startDate={today.strftime('%Y-%m-%d')}&endDate={date_to}"
           f"&gameType=R&hydrate=team")
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200: return []
        return [g for d in resp.json().get("dates", [])
                for g in d.get("games", [])
                if g.get("status", {}).get("abstractGameState") == "Preview"]
    except Exception as e:
        print(f"[MLB API] {e}")
        return []

def get_mlb_recent(team_id: int) -> list:
    today     = datetime.date.today()
    date_from = (today - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
    url = (f"{MLB_API}/schedule?sportId=1&teamId={team_id}"
           f"&startDate={date_from}&endDate={today.strftime('%Y-%m-%d')}"
           f"&gameType=R&hydrate=team,linescore")
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200: return []
        return [g for d in resp.json().get("dates", [])
                for g in d.get("games", [])
                if g.get("status", {}).get("abstractGameState") == "Final"]
    except Exception as e:
        print(f"[MLB API] {e}")
        return []

def mlb_result_for_team(game: dict, team_id: int) -> str:
    home_id    = game["teams"]["home"]["team"]["id"]
    home_score = game["teams"]["home"].get("score", 0)
    away_score = game["teams"]["away"].get("score", 0)
    team_home  = (home_id == team_id)
    if team_home: return "win" if home_score > away_score else "loss"
    return "win" if away_score > home_score else "loss"

def mlb_game_time_utc(game: dict):
    try:
        return datetime.datetime.fromisoformat(
            game.get("gameDate", "").replace("Z", "+00:00")
        ).replace(tzinfo=None)
    except Exception:
        return None

# ─────────────────────────────────────────────
# TRIGGER 1 — Scheduled weekly bet reminder
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
        if weekday not in send_days: continue
        amount = random_amount("scheduled")
        send_whatsapp(phone, (
            f"Hey {name}! {day_name} savings bet — stash *${amount}* today?\n\n"
            f"Weekly bankroll goal: *${bankroll}*\nReply *YES* to log it"
        ))
        sent_any = True
    log["last_scheduled"] = str(today)
    return sent_any

# ─────────────────────────────────────────────
# TRIGGER 2 — EPL pre-match bet prompt
# ─────────────────────────────────────────────

def check_epl_pre_match(log: dict, users: list) -> bool:
    epl_users = [u for u in users if u.get("epl_team")]
    if not epl_users: return False
    teams_to_users = {}
    for u in epl_users:
        teams_to_users.setdefault(u["epl_team"], []).append(u)
    sent_any = False
    now = datetime.datetime.utcnow()
    for team_name, team_users in teams_to_users.items():
        team_id = EPL_TEAM_IDS.get(team_name)
        if not team_id: continue
        for match in get_epl_upcoming(team_id):
            match_id = f"epl_{match['id']}"
            if match_id in log.get("sent_match_ids", []): continue
            kickoff = datetime.datetime.fromisoformat(
                match["utcDate"].replace("Z", "+00:00")).replace(tzinfo=None)
            mins = (kickoff - now).total_seconds() / 60
            if not (25 <= mins <= 45):
                print(f"[EPL] {team_name} in {mins:.0f} mins — not time yet.")
                continue
            home  = match["homeTeam"]["shortName"]
            away  = match["awayTeam"]["shortName"]
            opp   = away if match["homeTeam"]["id"] == team_id else home
            win_a = random_amount("win_correct")
            drw_a = random_amount("draw_correct")
            los_a = random_amount("loss_correct")
            base  = random_amount("win_wrong")
            for u in team_users:
                send_whatsapp(u["phone_number"], (
                    f"Hey {u.get('name','there')}! *{home} vs {away}* kicks off in ~30 mins!\n\n"
                    f"Place your bet:\n"
                    f"1 - {team_name} Win  -> save *${win_a}* correct / *${base}* wrong\n"
                    f"2 - Draw           -> save *${drw_a}* correct / *${base}* wrong\n"
                    f"3 - {team_name} Loss -> save *${los_a}* correct / *${base}* wrong\n\n"
                    f"Reply *1*, *2*, or *3* to lock in your bet"
                ))
            log.setdefault("pending_matches", {})[match_id] = {
                "sport": "epl", "team_id": team_id, "team_name": team_name,
                "opponent": opp, "win_amount": win_a, "draw_amount": drw_a,
                "loss_amount": los_a, "base_amount": base,
                "users": [u["phone_number"] for u in team_users],
            }
            log.setdefault("sent_match_ids", []).append(match_id)
            sent_any = True
    return sent_any

# ─────────────────────────────────────────────
# TRIGGER 3 — MLB pre-match bet prompt
# ─────────────────────────────────────────────

def check_mlb_pre_match(log: dict, users: list) -> bool:
    mlb_users = [u for u in users if u.get("mlb_team")]
    if not mlb_users:
        print("[MLB] No users with MLB teams.")
        return False
    teams_to_users = {}
    for u in mlb_users:
        teams_to_users.setdefault(u["mlb_team"], []).append(u)
    sent_any = False
    now = datetime.datetime.utcnow()
    for team_name, team_users in teams_to_users.items():
        team_id = MLB_TEAM_IDS.get(team_name)
        if not team_id: continue
        for game in get_mlb_upcoming(team_id):
            game_id = f"mlb_{game['gamePk']}"
            if game_id in log.get("sent_match_ids", []): continue
            game_time = mlb_game_time_utc(game)
            if not game_time: continue
            mins = (game_time - now).total_seconds() / 60
            if not (25 <= mins <= 45):
                print(f"[MLB] {team_name} in {mins:.0f} mins — not time yet.")
                continue
            home  = game["teams"]["home"]["team"]["name"]
            away  = game["teams"]["away"]["team"]["name"]
            opp   = away if game["teams"]["home"]["team"]["id"] == team_id else home
            win_a = random_amount("win_correct")
            los_a = random_amount("loss_correct")
            base  = random_amount("win_wrong")
            for u in team_users:
                send_whatsapp(u["phone_number"], (
                    f"Hey {u.get('name','there')}! *{away} @ {home}* first pitch in ~30 mins!\n\n"
                    f"Place your bet:\n"
                    f"1 - {team_name} Win  -> save *${win_a}* correct / *${base}* wrong\n"
                    f"2 - {team_name} Loss -> save *${los_a}* correct / *${base}* wrong\n\n"
                    f"Reply *1* or *2* to lock in your bet"
                ))
            log.setdefault("pending_matches", {})[game_id] = {
                "sport": "mlb", "team_id": team_id, "team_name": team_name,
                "opponent": opp, "win_amount": win_a, "loss_amount": los_a,
                "base_amount": base, "users": [u["phone_number"] for u in team_users],
            }
            log.setdefault("sent_match_ids", []).append(game_id)
            sent_any = True
    return sent_any

# ─────────────────────────────────────────────
# TRIGGER 4 — Post-match settlement
# ─────────────────────────────────────────────

def check_post_match(log: dict) -> bool:
    pending  = log.get("pending_matches", {})
    settled  = log.get("settled_match_ids", [])
    sent_any = False
    week     = current_week()
    for match_id, data in list(pending.items()):
        if match_id in settled: continue
        sport   = data.get("sport", "epl")
        team_id = data["team_id"]
        if sport == "epl":
            matches  = get_epl_recent(team_id)
            finished = {f"epl_{m['id']}": m for m in matches}
            if match_id not in finished:
                print(f"[Post] EPL {match_id} not finished yet.")
                continue
            match  = finished[match_id]
            result = epl_result_for_team(match, team_id)
            hg     = match["score"]["fullTime"]["home"]
            ag     = match["score"]["fullTime"]["away"]
            score  = f"{hg}-{ag}"
            emoji  = "🟢" if result == "win" else ("🟡" if result == "draw" else "🔴")
            head   = (f"{data['team_name']} beat {data['opponent']} {score}!" if result == "win"
                      else f"{data['team_name']} drew with {data['opponent']} {score}" if result == "draw"
                      else f"{data['team_name']} lost to {data['opponent']} {score}")
        else:
            games    = get_mlb_recent(team_id)
            finished = {f"mlb_{g['gamePk']}": g for g in games}
            if match_id not in finished:
                print(f"[Post] MLB {match_id} not finished yet.")
                continue
            game   = finished[match_id]
            result = mlb_result_for_team(game, team_id)
            hs     = game["teams"]["home"].get("score", 0)
            as_    = game["teams"]["away"].get("score", 0)
            score  = f"{as_}-{hs}"
            emoji  = "⚾" if result == "win" else "😬"
            head   = (f"{data['team_name']} beat {data['opponent']} {score}!"
                      if result == "win"
                      else f"{data['team_name']} lost to {data['opponent']} {score}")
        predictions = log.get("predictions", {}).get(match_id, {})
        for phone in data.get("users", []):
            pick = predictions.get(phone)
            if pick == result:
                amount   = data.get(f"{result}_amount", data["base_amount"])
                pick_msg = f"You called it! Save *${amount}*"
            elif pick:
                amount   = data["base_amount"]
                pick_msg = f"Unlucky — you picked *{pick}*. Base save: *${amount}*"
            else:
                amount   = data["base_amount"]
                pick_msg = f"No bet placed. Base save: *${amount}*"
            send_whatsapp(phone,
                f"{emoji} *{head}*\n\n{pick_msg}\n\nKeep building that bankroll!")
            log_bet_to_sheet(phone, match_id, pick or "none",
                             amount, result, week, sport)
        log.setdefault("settled_match_ids", []).append(match_id)
        sent_any = True
    return sent_any

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print(f"\n=== Akrue — {datetime.datetime.now()} ===\n")
    log   = load_log()
    fired = False
    test_mode = "--test" in sys.argv or os.getenv("TEST_MODE") == "1"
    if test_mode:
        users = get_active_users()
        for user in users:
            amount = random_amount("scheduled")
            sports = []
            if user.get("epl_team"): sports.append(f"EPL: {user['epl_team']}")
            if user.get("mlb_team"): sports.append(f"MLB: {user['mlb_team']}")
            send_whatsapp(user["phone_number"], (
                f"Akrue test — hey {user.get('name','there')}!\n"
                f"Teams: {' | '.join(sports) or 'none set'}\n"
                f"Save *${amount}* today? Reply YES"
            ))
        print(f"[Test] Sent to {len(users)} users.")
        save_log(log)
        return
    users = get_active_users()
    if not users:
        print("[Main] No active users. Exiting.")
        save_log(log)
        return
    if check_scheduled_bets(log, users):  fired = True
    if check_epl_pre_match(log, users):   fired = True
    if check_mlb_pre_match(log, users):   fired = True
    if check_post_match(log):             fired = True
    save_log(log)
    print("\n[Done] Bets sent!" if fired else "\n[Done] No bets to send right now.")

if __name__ == "__main__":
    main()
