"""
Akrue — Core Script
--------------------
Fires ONLY when a user's registered team has a match starting in 20-60 mins.
No scheduled reminders — match-triggered only.
Sent match IDs stored in Google Sheets (Sent_Matches tab) for persistence
across GitHub Actions runs.

Sport-agnostic architecture — adding a new sport requires only:
  1. A new entry in SPORT_CONFIG
  2. A new entry in SPORT_TEAM_IDS
  3. New API fetch functions (get_X_upcoming / get_X_recent / result_for_X)
  4. A new entry in SPORT_API_HANDLERS

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
# ENV CONFIG
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

# ─────────────────────────────────────────────
# SPORT CONFIG
# Adding a new sport = add one entry here only.
# allows_draw     → whether DRAW is a valid prediction
# options         → valid prediction values shown to users
# user_field      → column name in Users tab
# match_id_prefix → prefix used when building match IDs
# win_emoji       → shown on result message
# loss_emoji      → shown on result message
# draw_emoji      → shown on result message (only if allows_draw)
# start_label     → "kicks off" / "first pitch" etc.
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
    # ── Add NBA, NFL etc. here when ready ──
    # "nba": {
    #     "name":            "NBA",
    #     "emoji":           "🏀",
    #     "allows_draw":     False,
    #     "options":         ["WIN", "LOSS"],
    #     "user_field":      "nba_team",
    #     "match_id_prefix": "nba_",
    #     "win_emoji":       "🏀",
    #     "draw_emoji":      None,
    #     "loss_emoji":      "😬",
    #     "start_label":     "tip-off",
    # },
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

# ─────────────────────────────────────────────
# BET AMOUNTS
# ─────────────────────────────────────────────

BET_RANGES = {
    "win_correct":  (20, 40),
    "win_wrong":    (8,  12),
    "draw_correct": (15, 25),
    "draw_wrong":   (8,  12),
    "loss_correct": (25, 40),
    "loss_wrong":   (8,  12),
}

PROMPT_WINDOW_MIN = 20
PROMPT_WINDOW_MAX = 60
MLB_API = "https://statsapi.mlb.com/api/v1"

# ─────────────────────────────────────────────
# GOOGLE SHEETS
# ─────────────────────────────────────────────

def get_sheets_client():
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)

def open_sheet():
    return get_sheets_client().open_by_key(SHEET_ID)

def get_active_users() -> list:
    try:
        sheet  = open_sheet().worksheet("Users")
        users  = sheet.get_all_records()
        active = [u for u in users if u.get("status", "").lower() == "active"]
        print(f"[Users] Found {len(active)} active users.")
        return active
    except Exception as e:
        print(f"[Users] Error: {e}")
        return []

def get_sent_match_ids() -> set:
    try:
        sheet   = open_sheet().worksheet("Sent_Matches")
        records = sheet.get_all_records()
        ids     = {r["match_id"] for r in records if r.get("match_id")}
        print(f"[Sent_Matches] {len(ids)} already sent.")
        return ids
    except Exception as e:
        print(f"[Sent_Matches] Error: {e}")
        return set()

def log_sent_match(match_id: str, sport: str, team: str, users_notified: int):
    try:
        sheet = open_sheet().worksheet("Sent_Matches")
        sheet.append_row([match_id, sport, team, users_notified,
                          datetime.datetime.utcnow().isoformat()])
    except Exception as e:
        print(f"[Sent_Matches] Error writing: {e}")

def get_pending_matches() -> dict:
    try:
        sheet   = open_sheet().worksheet("Pending_Matches")
        records = sheet.get_all_records()
        pending = {}
        for r in records:
            if r.get("match_id") and r.get("settled") != "yes":
                pending[r["match_id"]] = {
                    "sport":       r.get("sport"),
                    "team_id":     int(r.get("team_id", 0)),
                    "team_name":   r.get("team_name"),
                    "opponent":    r.get("opponent"),
                    "win_amount":  int(r.get("win_amount", 20)),
                    "draw_amount": int(r.get("draw_amount", 15)),
                    "loss_amount": int(r.get("loss_amount", 25)),
                    "base_amount": int(r.get("base_amount", 10)),
                    "users":       json.loads(r.get("users", "[]")),
                    "row":         records.index(r) + 2,
                }
        print(f"[Pending] {len(pending)} unsettled matches.")
        return pending
    except Exception as e:
        print(f"[Pending] Error: {e}")
        return {}

def log_pending_match(match_id: str, data: dict):
    try:
        sheet = open_sheet().worksheet("Pending_Matches")
        sheet.append_row([
            match_id, data["sport"], data["team_id"], data["team_name"],
            data["opponent"], data["win_amount"], data["draw_amount"],
            data["loss_amount"], data["base_amount"],
            json.dumps(data["users"]), "no",
            datetime.datetime.utcnow().isoformat(),
        ])
    except Exception as e:
        print(f"[Pending] Error writing: {e}")

def mark_match_settled(row: int):
    try:
        open_sheet().worksheet("Pending_Matches").update_cell(row, 11, "yes")
    except Exception as e:
        print(f"[Pending] Error settling: {e}")

def get_predictions_for_match(match_id: str) -> dict:
    try:
        sheet   = open_sheet().worksheet("Predictions")
        records = sheet.get_all_records()
        return {
            r["user_phone"]: r["Prediction"]
            for r in records
            if r.get("match_id") == match_id and r.get("Prediction")
        }
    except Exception as e:
        print(f"[Predictions] Error: {e}")
        return {}

def write_prediction_pending(user_phone: str, match_id: str):
    try:
        sheet = open_sheet().worksheet("Predictions")
        sheet.append_row([match_id, user_phone, "",
                          datetime.datetime.utcnow().isoformat(), "pending"])
    except Exception as e:
        print(f"[Predictions] Error writing pending: {e}")

def log_bet_to_sheet(user_phone, match_id, prediction, amount, result, sport):
    try:
        sheet = open_sheet().worksheet("Savings_Log")
        sheet.append_row([
            datetime.date.today().isoformat(), user_phone, amount,
            f"{sport}_bet_{result}", match_id, current_week(), sport,
        ])
    except Exception as e:
        print(f"[Savings_Log] Error: {e}")

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

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

def build_prompt_message(name: str, home: str, away: str, sport_key: str,
                         amounts: dict, base: int) -> str:
    """Build a pre-match WhatsApp prompt from sport config — sport agnostic."""
    cfg     = SPORT_CONFIG[sport_key]
    lines   = []
    for opt in cfg["options"]:
        key    = opt.lower()
        amount = amounts.get(key, base)
        lines.append(f"{opt}  -> save *${amount}* correct / *${base}* wrong")
    options_str = "\n".join(lines)
    reply_str   = " or ".join(f"*{o}*" for o in cfg["options"])
    return (
        f"Hey {name}! *{away} @ {home}* {cfg['start_label']} in ~30 mins!\n\n"
        f"Place your bet:\n{options_str}\n\n"
        f"Reply {reply_str} to lock in your bet"
    )

def build_result_message(sport_key: str, team_name: str, opponent: str,
                         score: str, result: str, pick: str | None,
                         amounts: dict, base: int) -> str:
    """Build a post-match result message — sport agnostic."""
    cfg = SPORT_CONFIG[sport_key]

    if result == "win":
        emoji = cfg["win_emoji"]
        head  = f"{team_name} beat {opponent} {score}!"
    elif result == "draw":
        emoji = cfg.get("draw_emoji", "🟡")
        head  = f"{team_name} drew with {opponent} {score}"
    else:
        emoji = cfg["loss_emoji"]
        head  = f"{team_name} lost to {opponent} {score}"

    if pick == result:
        amount   = amounts.get(result, base)
        pick_msg = f"You called it! Save *${amount}* 💰"
    elif pick:
        amount   = base
        pick_msg = f"Unlucky — you picked *{pick.upper()}*. Base save: *${amount}* 💰"
    else:
        amount   = base
        pick_msg = f"No bet placed. Base save: *${amount}* 💰"

    return f"{emoji} *{head}*\n\n{pick_msg}\n\nKeep building that bankroll!"

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
    url = (f"https://api.football-data.org/v4/teams/{team_id}/matches"
           f"?status=FINISHED&dateFrom={date_from}&dateTo={today.isoformat()}")
    resp = requests.get(url, headers={"X-Auth-Token": FOOTBALL_API_KEY}, timeout=10)
    if resp.status_code != 200:
        print(f"[EPL API] Error {resp.status_code}")
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

def mlb_teams_from_game(game: dict, team_id: int) -> tuple:
    home = game["teams"]["home"]["team"]["name"]
    away = game["teams"]["away"]["team"]["name"]
    opp  = away if game["teams"]["home"]["team"]["id"] == team_id else home
    return home, away, opp

def mlb_score_str(game: dict) -> str:
    hs = game["teams"]["home"].get("score", 0)
    as_ = game["teams"]["away"].get("score", 0)
    return f"{as_}-{hs}"

def mlb_kickoff_utc(game: dict):
    try:
        return datetime.datetime.fromisoformat(
            game.get("gameDate", "").replace("Z", "+00:00")
        ).replace(tzinfo=None)
    except Exception:
        return None

def mlb_match_key(game: dict) -> str:
    return f"mlb_{game['gamePk']}"

def mlb_finished_dict(team_id: int) -> dict:
    return {mlb_match_key(g): g for g in get_mlb_recent(team_id)}

# ─────────────────────────────────────────────
# SPORT API HANDLERS
# Maps sport key → functions for upcoming, finished, result, teams, score, time, key
# Adding a new sport = add one entry here.
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
# GENERIC PRE-MATCH TRIGGER (all sports)
# ─────────────────────────────────────────────

def check_pre_match(users: list, sent_ids: set, sport_key: str) -> bool:
    """
    Sport-agnostic pre-match prompt.
    Works for any sport defined in SPORT_CONFIG and SPORT_API_HANDLERS.
    """
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
    now = datetime.datetime.utcnow()

    for team_name, team_users in teams_to_users.items():
        team_id = team_ids.get(team_name)
        if not team_id:
            print(f"[{sport_key.upper()}] Unknown team: {team_name}")
            continue

        for event in handlers["get_upcoming"](team_id):
            match_id = handlers["match_key_fn"](event)
            if match_id in sent_ids:
                print(f"[{sport_key.upper()}] {match_id} already sent.")
                continue

            kickoff = handlers["kickoff_fn"](event)
            if not kickoff:
                continue
            mins = (kickoff - now).total_seconds() / 60
            if not (PROMPT_WINDOW_MIN <= mins <= PROMPT_WINDOW_MAX):
                print(f"[{sport_key.upper()}] {team_name} in {mins:.0f} mins — outside window.")
                continue

            home, away, opp = handlers["teams_fn"](event, team_id)

            # Build amounts dict for options
            amounts = {}
            base = random_amount("win_wrong")
            for opt in cfg["options"]:
                amounts[opt.lower()] = random_amount(f"{opt.lower()}_correct")

            for u in team_users:
                name = u.get("name", "there")
                msg  = build_prompt_message(name, home, away, sport_key, amounts, base)
                send_whatsapp(u["phone_number"], msg)
                write_prediction_pending(u["phone_number"], match_id)

            match_data = {
                "sport":       sport_key,
                "team_id":     team_id,
                "team_name":   team_name,
                "opponent":    opp,
                "win_amount":  amounts.get("win", base),
                "draw_amount": amounts.get("draw", 0),
                "loss_amount": amounts.get("loss", base),
                "base_amount": base,
                "users":       [u["phone_number"] for u in team_users],
            }
            log_pending_match(match_id, match_data)
            log_sent_match(match_id, sport_key, team_name, len(team_users))
            sent_ids.add(match_id)
            sent_any = True

    return sent_any

# ─────────────────────────────────────────────
# GENERIC POST-MATCH SETTLEMENT (all sports)
# ─────────────────────────────────────────────

def check_post_match(pending: dict) -> bool:
    """
    Sport-agnostic post-match settlement.
    Reads sport from pending match data and uses appropriate API handler.
    """
    sent_any = False

    for match_id, data in list(pending.items()):
        sport_key = data.get("sport", "epl")
        handlers  = SPORT_API_HANDLERS.get(sport_key)
        if not handlers:
            print(f"[Post] Unknown sport: {sport_key} for {match_id}")
            continue

        team_id  = data["team_id"]
        finished = handlers["get_finished"](team_id)

        if match_id not in finished:
            print(f"[Post] {sport_key.upper()} {match_id} not finished yet.")
            continue

        event  = finished[match_id]
        result = handlers["result_fn"](event, team_id)
        score  = handlers["score_fn"](event)

        amounts = {
            "win":  data.get("win_amount",  data["base_amount"]),
            "draw": data.get("draw_amount", data["base_amount"]),
            "loss": data.get("loss_amount", data["base_amount"]),
        }

        predictions = get_predictions_for_match(match_id)

        for phone in data.get("users", []):
            pick = predictions.get(phone)
            msg  = build_result_message(
                sport_key, data["team_name"], data["opponent"],
                score, result, pick, amounts, data["base_amount"]
            )
            send_whatsapp(phone, msg)
            log_bet_to_sheet(phone, match_id, pick or "none",
                             amounts.get(result, data["base_amount"]),
                             result, sport_key)

        mark_match_settled(data["row"])
        sent_any = True

    return sent_any

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print(f"\n=== Akrue — {datetime.datetime.utcnow()} UTC ===\n")

    test_mode = "--test" in sys.argv or os.getenv("TEST_MODE") == "1"

    if test_mode:
        users = get_active_users()
        for user in users:
            teams = [
                f"{cfg['name']}: {user.get(cfg['user_field'], '')}"
                for sport_key, cfg in SPORT_CONFIG.items()
                if user.get(cfg["user_field"])
            ]
            send_whatsapp(user["phone_number"], (
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

    sent_ids = get_sent_match_ids()
    pending  = get_pending_matches()

    fired = False

    # Run pre-match check for every active sport
    for sport_key in SPORT_CONFIG:
        if check_pre_match(users, sent_ids, sport_key):
            fired = True

    if check_post_match(pending):
        fired = True

    print("\n[Done] Prompts sent!" if fired else "\n[Done] No matches in window right now.")

if __name__ == "__main__":
    main()
