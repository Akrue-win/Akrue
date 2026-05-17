""""
Akrue — Core Script
--------------------
Fires ONLY when a user's registered team has a match starting in 20-60 mins.
No scheduled reminders — match-triggered only.

Two key fixes in this version:
  1. Sent_Matches dedup is now per-user per-match (not per-match globally).
     New users signing up after a prompt was sent will still receive it.
  2. Phone numbers are normalised (whatsapp: prefix stripped) before
     comparison everywhere, fixing "no bet placed" false negatives.

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
import statsapi
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

PROMPT_WINDOW_MIN = 5
PROMPT_WINDOW_MAX = 45

MLB_PRE_GAME_STATUSES = {"Scheduled", "Pre-Game", "Warmup"}
MLB_FINAL_STATUSES    = {"Final", "Game Over", "Completed Early"}

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def normalise_phone(phone: str) -> str:
    return phone.replace("whatsapp:", "").replace("+", "").strip()

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

def get_sent_match_ids() -> dict:
    """
    Returns {normalised_phone: set(match_ids)}.
    Dedup is now per-user per-match — not per-match globally.
    A new user signing up after a match prompt was sent will still
    receive their prompt on the next cron run.

    Sent_Matches sheet columns: match_id | sport | team_name | user_phone | sent_at
    """
    try:
        sheet   = open_sheet().worksheet("Sent_Matches")
        records = sheet.get_all_records()
        sent    = {}
        for r in records:
            phone = normalise_phone(str(r.get("user_phone", "")))
            match_id = r.get("match_id", "")
            if phone and match_id:
                sent.setdefault(phone, set()).add(match_id)
        print(f"[Sent_Matches] Per-user log loaded for {len(sent)} users.")
        return sent
    except Exception as e:
        print(f"[Sent_Matches] Error: {e}")
        return {}

def log_sent_match(match_id: str, sport: str, team: str, user_phone: str):
    """
    Log one row per user per match.
    Column order: match_id | sport | team_name | user_phone | sent_at
    """
    try:
        sheet = open_sheet().worksheet("Sent_Matches")
        sheet.append_row([
            match_id, sport, team,
            normalise_phone(user_phone),
            datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(),
        ])
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
                    "kickoff_utc": r.get("kickoff_utc", ""),  # ← added
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
            datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(),
            data.get("kickoff_utc", ""),
        ])
    except Exception as e:
        print(f"[Pending] Error writing: {e}")

def append_users_to_pending(match_id: str, new_phones: list):
    """
    Adds newly notified users to an existing Pending_Matches row.
    Called when a match was already logged but a new user signed up
    after the initial prompt was sent and needs to be included in
    post-match settlement.
    """
    try:
        sheet   = open_sheet().worksheet("Pending_Matches")
        records = sheet.get_all_records()
        for i, r in enumerate(records):
            if r.get("match_id") == match_id and r.get("settled") != "yes":
                existing = json.loads(r.get("users", "[]"))
                merged   = list(set(existing + new_phones))
                sheet.update_cell(i + 2, 10, json.dumps(merged))
                print(f"[Pending] Appended {new_phones} to {match_id}")
                return
    except Exception as e:
        print(f"[Pending] Error appending users to {match_id}: {e}")

def mark_match_settled(row: int):
    try:
        open_sheet().worksheet("Pending_Matches").update_cell(row, 11, "yes")
    except Exception as e:
        print(f"[Pending] Error settling: {e}")

def get_predictions_for_match(match_id: str) -> dict:
    """
    Returns {normalised_phone: prediction} for a given match.
    Phone numbers are normalised so whatsapp: prefix differences
    between sheets don't cause missed lookups.
    """
    try:
        sheet   = open_sheet().worksheet("Predictions")
        records = sheet.get_all_records()
        return {
            normalise_phone(str(r["user_phone"])): r["Prediction"]
            for r in records
            if r.get("match_id") == match_id and r.get("Prediction")
        }
    except Exception as e:
        print(f"[Predictions] Error: {e}")
        return {}

def get_all_predictions(pending: dict) -> dict:
    all_preds = {}
    for match_id in pending:
        all_preds[match_id] = get_predictions_for_match(match_id)
    return all_preds

def write_prediction_pending(user_phone: str, match_id: str):
    """
    Writes a pending prediction row using the normalised phone number
    so lookups always match regardless of source format.
    """
    try:
        sheet = open_sheet().worksheet("Predictions")
        sheet.append_row([
            match_id,
            normalise_phone(user_phone),
            "",
            datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(),
            "pending",
        ])
    except Exception as e:
        print(f"[Predictions] Error writing pending: {e}")

def log_bet_to_sheet(user_phone, match_id, prediction, amount, result, sport):
    try:
        sheet = open_sheet().worksheet("Savings_Log")
        sheet.append_row([
            datetime.date.today().isoformat(),
            normalise_phone(user_phone),
            amount,
            f"{sport}_bet_{result}",
            match_id,
            current_week(),
            sport,
        ])
    except Exception as e:
        print(f"[Savings_Log] Error: {e}")

def log_double_down_to_sheet(user_phone, match_id, amount, sport):
    try:
        sheet = open_sheet().worksheet("Savings_Log")
        sheet.append_row([
            datetime.date.today().isoformat(),
            normalise_phone(user_phone),
            amount,
            "double_down",
            match_id,
            current_week(),
            sport,
        ])
        print(f"[Savings_Log] Double down ${amount} for {user_phone}")
    except Exception as e:
        print(f"[Savings_Log] Double down error: {e}")

# ─────────────────────────────────────────────
# DOUBLE DOWN LOG
# ─────────────────────────────────────────────

def get_double_down_sent() -> set:
    try:
        sheet   = open_sheet().worksheet("Double_Down_Sent")
        records = sheet.get_all_records()
        return {
            f"{r['match_id']}:{normalise_phone(str(r['user_phone']))}"
            for r in records
            if r.get("match_id") and r.get("user_phone")
        }
    except Exception as e:
        print(f"[Double_Down_Sent] Error reading: {e}")
        return set()

def log_double_down_sent(match_id: str, user_phone: str,
                         direction: str, amount: int):
    try:
        sheet = open_sheet().worksheet("Double_Down_Sent")
        sheet.append_row([
            match_id,
            normalise_phone(user_phone),
            direction,
            amount,
            datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(),
        ])
    except Exception as e:
        print(f"[Double_Down_Sent] Error writing: {e}")

# ─────────────────────────────────────────────
# DOUBLE DOWN — EPL (halftime check)
# ─────────────────────────────────────────────

def get_epl_live(team_id: int) -> list:
    url = (f"https://api.football-data.org/v4/teams/{team_id}/matches"
           f"?status=IN_PLAY,PAUSED")
    resp = requests.get(url, headers={"X-Auth-Token": FOOTBALL_API_KEY}, timeout=10)
    if resp.status_code != 200:
        print(f"[EPL API] Error {resp.status_code} — {resp.text}")
        return []
    return resp.json().get("matches", [])

def check_epl_double_down(pending: dict, predictions: dict,
                          dd_sent: set) -> bool:
    sent_any = False

    for match_id, data in pending.items():
        if data.get("sport") != "epl":
            continue

        team_id   = data["team_id"]
        team_name = data["team_name"]
        live      = get_epl_live(team_id)

        for match in live:
            if f"epl_{match['id']}_{team_id}" != match_id:
                continue

            status = match.get("status", "")
            if status != "PAUSED":
                print(f"[DD EPL] {match_id} not at halftime (status: {status})")
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
                phone_n  = normalise_phone(phone)
                dd_key   = f"{match_id}:{phone_n}"
                if dd_key in dd_sent:
                    continue

                user_pick = predictions.get(match_id, {}).get(phone_n)
                if not user_pick:
                    continue

                if team_score > opp_score and user_pick == "win":
                    direction = "win"
                    dd_amount = max(1, round(data["win_amount"] * 0.5))
                    msg = (
                        f"⚽ Half time — *{score_str}*\n\n"
                        f"Your {team_name} is looking good! 🔥\n\n"
                        f"Want to double down? Save an extra *${dd_amount}* "
                        f"if they hold on.\n\n"
                        f"Reply *DD* to lock it in."
                    )
                elif team_score < opp_score and user_pick == "loss":
                    direction = "loss"
                    dd_amount = max(1, round(data["loss_amount"] * 0.5))
                    msg = (
                        f"⚽ Half time — *{score_str}*\n\n"
                        f"Your instincts are looking correct... 👀\n\n"
                        f"Want to double down? Save an extra *${dd_amount}* "
                        f"if it stays this way.\n\n"
                        f"Reply *DD* to lock it in."
                    )
                else:
                    continue

                send_whatsapp(f"whatsapp:+{phone_n}", msg)
                log_double_down_sent(match_id, phone_n, direction, dd_amount)
                dd_sent.add(dd_key)
                sent_any = True

    return sent_any

# ─────────────────────────────────────────────
# DOUBLE DOWN — MLB (after 6th inning)
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

def check_mlb_double_down(pending: dict, predictions: dict,
                          dd_sent: set) -> bool:
    sent_any = False

    for match_id, data in pending.items():
        if data.get("sport") != "mlb":
            continue

        team_id   = data["team_id"]
        team_name = data["team_name"]

        try:
            game_pk = int(match_id.split("_")[1])
        except ValueError:
            print(f"[DD MLB] Could not parse game_pk from {match_id}")
            continue

        linescore = get_mlb_live_score(game_pk)
        if not linescore:
            print(f"[DD MLB] No linescore for {match_id} — game may not be live yet.")
            continue

        current_inning = linescore.get("currentInning", 0)
        inning_state   = linescore.get("inningState", "")
        home_runs      = linescore.get("teams", {}).get("home", {}).get("runs", 0)
        away_runs      = linescore.get("teams", {}).get("away", {}).get("runs", 0)

        # Only offer double down in 6th or 7th inning
        in_window = current_inning in (6, 7)
        if not in_window:
            print(f"[DD MLB] {match_id} — inning {current_inning} — outside double down window.")
            continue

        # Get home team from statsapi instead of linescore
        try:
            game_info = statsapi.schedule(game_id=game_pk)
            if not game_info:
                print(f"[DD MLB] {match_id} — no game info from statsapi.")
                continue
            home_team_id = game_info[0].get("home_id")
        except Exception as e:
            print(f"[DD MLB] {match_id} — statsapi error: {e}")
            continue

        if not home_team_id:
            print(f"[DD MLB] {match_id} — could not determine home team.")
            continue

        team_home  = (home_team_id == team_id)
        team_score = home_runs if team_home else away_runs
        opp_score  = away_runs if team_home else home_runs
        score_str  = f"{away_runs}-{home_runs}"

        # Only offer double down if within 3 runs
        run_diff = abs(team_score - opp_score)
        if run_diff > 3:
            print(f"[DD MLB] {match_id} — run diff {run_diff} — too large for double down.")
            continue

        print(f"[DD MLB] {match_id} — inning {current_inning}, diff {run_diff} — checking double down.")

        for phone in data.get("users", []):
            phone_n  = normalise_phone(phone)
            dd_key   = f"{match_id}:{phone_n}"
            if dd_key in dd_sent:
                continue

            user_pick = predictions.get(match_id, {}).get(phone_n)
            if not user_pick:
                continue

            if team_score > opp_score and user_pick == "win":
                direction = "win"
                dd_amount = max(1, round(data["win_amount"] * 0.5))
                msg = (
                    f"⚾ After {current_inning} — *{score_str}*\n\n"
                    f"Your {team_name} is looking good! 🔥\n\n"
                    f"Want to double down? Save an extra *${dd_amount}* "
                    f"if they hold on.\n\n"
                    f"Reply *DD* to lock it in."
                )
            elif team_score < opp_score and user_pick == "loss":
                direction = "loss"
                dd_amount = max(1, round(data["loss_amount"] * 0.5))
                msg = (
                    f"⚾ After {current_inning} — *{score_str}*\n\n"
                    f"Your instincts are looking correct... 👀\n\n"
                    f"Want to double down? Save an extra *${dd_amount}* "
                    f"if it stays this way.\n\n"
                    f"Reply *DD* to lock it in."
                )
            else:
                continue

            send_whatsapp(f"whatsapp:+{phone_n}", msg)
            log_double_down_sent(match_id, phone_n, direction, dd_amount)
            dd_sent.add(dd_key)
            sent_any = True
            print(f"[DD MLB] Sent double down to {phone_n} for {match_id} ({direction}, ${dd_amount})")

    return sent_any
# ─────────────────────────────────────────────
# UTILITY
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
                         amounts: dict, base: int,
                         team_name: str = "") -> str:
    cfg     = SPORT_CONFIG[sport_key]
    label   = team_name or "Your team"
    lines   = []
    for opt in cfg["options"]:
        amount = amounts.get(opt.lower(), base)
        lines.append(f"   {opt.title()} → ${amount}")
    options_str = "\n".join(lines)
    reply_str   = " or ".join(f"*{o}*" for o in cfg["options"])
    return (
        f"Hey {name}! *{away} @ {home}* {cfg['start_label']} soon!\n\n"
        f"{label}:\n{options_str}\n\n"
        f"If you pick wrong, no sweat — save *${base}* anyway 💰\n\n"
        f"Reply {reply_str} to lock in your bet"
    )
def reorder_score(score: str, result: str) -> str:
    """Ensure winning team's score is always listed first."""
    try:
        a, b = score.split("-")
        a, b = int(a), int(b)
        if result == "win":
            return f"{max(a,b)}-{min(a,b)}"
        elif result == "loss":
            return f"{min(a,b)}-{max(a,b)}"
        else:
            return score  # draw, order doesn't matter
    except:
        return score

def build_result_message(sport_key: str, team_name: str, opponent: str,
                         score: str, result: str, pick: str | None,
                         amounts: dict, base: int) -> str:
    cfg = SPORT_CONFIG[sport_key]

    if result == "win":
        emoji = cfg["win_emoji"]
        score = reorder_score(score, result)
        head  = f"{team_name} win! Defeat {opponent} {score}"
    elif result == "draw":
        emoji = cfg.get("draw_emoji", "🟡")
        head  = f"{team_name} draw with {opponent} {score}"
    else:
        emoji = cfg["loss_emoji"]
        score = reorder_score(score, result)
        head  = f"{team_name} lose to {opponent} {score}"

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
    today     = datetime.date.today()
    date_from = today.isoformat()
    date_to   = (today + datetime.timedelta(days=2)).isoformat()
    url = (f"https://api.football-data.org/v4/teams/{team_id}/matches"
           f"?status=TIMED,SCHEDULED&dateFrom={date_from}&dateTo={date_to}")
    resp = requests.get(url, headers={"X-Auth-Token": FOOTBALL_API_KEY}, timeout=10)
    if resp.status_code != 200:
        print(f"[EPL API] Error {resp.status_code} — {resp.text}")
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
        print(f"[EPL API] Error {resp.status_code} — {resp.text}")
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
    return {mlb_match_key(g): g for g in get_mlb_recent(team_id)}

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
# GENERIC PRE-MATCH TRIGGER
# ─────────────────────────────────────────────

def check_pre_match(users: list, sent_per_user: dict, sport_key: str) -> bool:
    """
    Per-user dedup — checks whether each individual user has already
    been sent a given match prompt, not whether the match was sent
    to anyone at all. Allows users who sign up after a prompt was
    sent to still receive it on the next cron run.
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
                print(f"[{sport_key.upper()}] {team_name} in {mins:.0f} mins — outside window.")
                continue
            home, away, opp = handlers["teams_fn"](event, team_id)
            # Calculate amounts once per match — same for all users
            amounts = {}
            base = random_amount("win_wrong")
            for opt in cfg["options"]:
                amounts[opt.lower()] = random_amount(f"{opt.lower()}_correct")
            # Send to each user individually, checking their personal sent log
            newly_notified = []
            for u in team_users:
                phone   = str(u["phone_number"])  # ← str() wrap
                phone_n = normalise_phone(phone)
                if match_id in sent_per_user.get(phone_n, set()):
                    print(f"[{sport_key.upper()}] {match_id} already sent to {phone_n}.")
                    continue
                name = u.get("name", "there")
                msg  = build_prompt_message(name, home, away,
                                            sport_key, amounts, base, team_name)
                send_whatsapp(f"whatsapp:+{phone_n}", msg)
                write_prediction_pending(phone, match_id)
                log_sent_match(match_id, sport_key, team_name, phone)
                # Update local cache to prevent double-sending this run
                sent_per_user.setdefault(phone_n, set()).add(match_id)
                newly_notified.append(phone_n)
                sent_any = True
                print(f"[{sport_key.upper()}] Prompt sent to {phone_n} for {match_id}.")
            # Update Pending_Matches with newly notified users
            if newly_notified:
                existing_pending = get_pending_matches()
                if match_id not in existing_pending:
                    match_data = {
                        "sport":       sport_key,
                        "team_id":     team_id,
                        "team_name":   team_name,
                        "opponent":    opp,
                        "win_amount":  amounts.get("win", base),
                        "draw_amount": amounts.get("draw", 0),
                        "loss_amount": amounts.get("loss", base),
                        "base_amount": base,
                        "users":       newly_notified,
                        "kickoff_utc": kickoff.isoformat() if kickoff else "",
                    }
                    log_pending_match(match_id, match_data)
                else:
                    # Match already exists — add new users to the row
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
        event  = finished[raw_match_id]
        result = handlers["result_fn"](event, team_id)
        score  = handlers["score_fn"](event)
        amounts = {
            "win":  data.get("win_amount",  data["base_amount"]),
            "draw": data.get("draw_amount", data["base_amount"]),
            "loss": data.get("loss_amount", data["base_amount"]),
        }
        predictions = get_predictions_for_match(match_id)
        for phone in data.get("users", []):
            phone_n = normalise_phone(phone)
            pick    = predictions.get(phone_n)

            if not pick or pick.upper() == "N/A":
                print(f"[Post] {match_id} — {phone_n} no pick, skipping.")
                continue

            print(f"[Post] {match_id} — {phone_n} picked {pick}.")

            if pick == result:
                logged_amount = amounts.get(result, data["base_amount"])
            else:
                logged_amount = data["base_amount"]

            msg = build_result_message(
                sport_key, data["team_name"], data["opponent"],
                score, result, pick, amounts, data["base_amount"]
            )
            send_whatsapp(f"whatsapp:+{phone_n}", msg)
            log_bet_to_sheet(
                phone_n, match_id, pick,
                logged_amount,
                result, sport_key,
            )
        mark_match_settled(data["row"])
        sent_any = True
    return sent_any

def get_predictions_pending_reminder() -> list:
    """
    Returns all prediction rows where status is 'pending'
    and reminder_sent (col F) is not 'yes'.
    """
    try:
        sheet   = open_sheet().worksheet("Predictions")
        records = sheet.get_all_records()
        result  = []
        for i, r in enumerate(records):
            if r.get("status") == "pending" and r.get("reminder_sent", "") != "yes":
                result.append({**r, "row": i + 2})
        return result
    except Exception as e:
        print(f"[Predictions] Error fetching pending reminders: {e}")
        return []

def mark_reminder_sent(row: int):
    try:
        open_sheet().worksheet("Predictions").update_cell(row, 6, "yes")
    except Exception as e:
        print(f"[Predictions] Error marking reminder sent: {e}")


def check_reminders(users: list, pending: dict) -> bool:
    sent_any = False
    now      = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    user_lookup = {normalise_phone(str(u["phone_number"])): u for u in users}

    pending_preds = get_predictions_pending_reminder()

    for pred in pending_preds:
        match_id = pred.get("match_id")
        phone_n = normalise_phone(str(pred.get("user_phone", "")))

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
        emoji     = SPORT_CONFIG[sport_key]["emoji"]
        cfg       = SPORT_CONFIG[sport_key]
        reply_str = " or ".join(f"*{o}*" for o in cfg["options"])

        user      = user_lookup.get(phone_n, {})
        name      = user.get("name", "there")
        phone_raw = user.get("phone_number", phone_n)

        msg = (
            f"{emoji} Hey {name}! Kickoff is coming up soon!\n\n"
            f"Don't forget to lock in your pick — "
            f"reply {reply_str} before it's too late 👀"
        )

        send_whatsapp(f"whatsapp:+{phone_n}", msg)
        mark_reminder_sent(pred["row"])
        sent_any = True
        print(f"[Reminder] Sent to {phone_n} for {match_id}.")

    return sent_any
# ─────────────────────────────────────────────
# Lock Unpicked Bets
# ─────────────────────────────────────────────

def lock_unpicked_started_matches(pending: dict):
    """
    For any Predictions row still 'pending' where the match has already
    kicked off, write 'N/A' as the pick and set status to 'locked'.
    """
    try:
        sheet   = open_sheet().worksheet("Predictions")
        records = sheet.get_all_records()
        now     = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

        for i, r in enumerate(records):
            if r.get("status") != "pending":
                continue

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
                row_index = i + 2
                sheet.update_cell(row_index, 3, "N/A")    # Prediction col
                sheet.update_cell(row_index, 5, "locked") # Status col
                print(f"[Lock] Auto-locked N/A for {r.get('user_phone')} on {match_id}")

    except Exception as e:
        print(f"[Lock] Error: {e}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print(f"\n=== Akrue — {datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)} UTC ===\n")
    test_mode = "--test" in sys.argv or os.getenv("TEST_MODE") == "1"
    if test_mode:
        users = get_active_users()
        for user in users:
            teams = [
                f"{cfg['name']}: {user.get(cfg['user_field'], '')}"
                for sport_key, cfg in SPORT_CONFIG.items()
                if user.get(cfg["user_field"])
            ]
            send_whatsapp(f"whatsapp:+{normalise_phone(str(user['phone_number']))}", (
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
    sent_per_user = get_sent_match_ids()
    pending       = get_pending_matches()
    lock_unpicked_started_matches(pending)
    dd_sent       = get_double_down_sent()
    all_preds     = get_all_predictions(pending)
    fired = False
    for sport_key in SPORT_CONFIG:
        if check_pre_match(users, sent_per_user, sport_key):
            fired = True
    if check_epl_double_down(pending, all_preds, dd_sent):  fired = True
    if check_mlb_double_down(pending, all_preds, dd_sent):  fired = True
    if check_post_match(pending):
        fired = True
    if check_reminders(users, pending):
        fired = True
    print("\n[Done] Prompts sent!" if fired else "\n[Done] No matches in window right now.")

if __name__ == "__main__":
    main()
