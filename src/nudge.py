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

PROMPT_WINDOW_MIN = 1
PROMPT_WINDOW_MAX = 50

MLB_PRE_GAME_STATUSES = {"Scheduled", "Pre-Game", "Warmup"}
MLB_FINAL_STATUSES    = {"Final", "Game Over", "Completed Early"}

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
                          datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()])
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
            datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(),
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

def get_all_predictions(pending: dict) -> dict:
    all_preds = {}
    for match_id in pending:
        all_preds[match_id] = get_predictions_for_match(match_id)
    return all_preds

def write_prediction_pending(user_phone: str, match_id: str):
    try:
        sheet = open_sheet().worksheet("Predictions")
        sheet.append_row([match_id, user_phone, "",
                          datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(), "pending"])
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

def log_double_down_to_sheet(user_phone, match_id, amount, sport):
    try:
        sheet = open_sheet().worksheet("Savings_Log")
        sheet.append_row([
            datetime.date.today().isoformat(), user_phone, amount,
            "double_down", match_id, current_week(), sport,
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
            f"{r['match_id']}:{r['user_phone']}"
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
            match_id, user_phone, direction, amount,
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
            if f"epl_{match['id']}" != match_id:
                continue

            status = match.get("status", "")
            if status != "PAUSED":  # PAUSED = halftime in football-data.org
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
                dd_key = f"{match_id}:{phone}"
                if dd_key in dd_sent:
                    continue

                user_pick = predictions.get(match_id, {}).get(phone)
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

                send_whatsapp(phone, msg)
                log_double_down_sent(match_id, phone, direction, dd_amount)
                dd_sent.add(dd_key)
                sent_any = True

    return sent_any

# ─────────────────────────────────────────────
# DOUBLE DOWN — MLB
#
# FIX: Instead of checking for an exact inning/state match (which
# a 15-minute cron often misses), we now trigger any time the game
# has progressed PAST the end of the 6th inning — i.e. currentInning
# is 7 or higher regardless of inningState, OR currentInning is
# exactly 6 and inningState is "End" (6th just finished).
#
# The Double_Down_Sent sheet prevents duplicate messages — so even
# if the cron fires multiple times during innings 7-9, the offer
# is only ever sent once per user per match.
#
# Also fixed: team_id and team_name were never extracted from data.
# Also fixed: home/away determined from linescore API directly
#             (not from get_mlb_recent which only has finished games).
# ─────────────────────────────────────────────

def get_mlb_live_score(game_pk: int) -> dict:
    """
    Fetch live linescore for an MLB game.
    Returns the full linescore dict including currentInning,
    inningState, teams.home.runs, teams.away.runs, and
    importantly teams.home.team.id / teams.away.team.id
    so we can determine home/away without a separate API call.
    """
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
    """
    Check if any pending MLB game has progressed past the end of the
    6th inning. Triggers a double down offer if:
      - currentInning >= 7  (we are in or past the 7th)
      - OR currentInning == 6 and inningState == "End" (6th just finished)

    This catches the window reliably regardless of when the 15-min
    cron fires relative to inning transitions.

    The Double_Down_Sent sheet deduplicates — the offer fires at most
    once per user per match even if this function runs many times.
    """
    sent_any = False

    for match_id, data in pending.items():
        if data.get("sport") != "mlb":
            continue

        # Extract fields from pending data
        team_id   = data["team_id"]
        team_name = data["team_name"]

        # Parse gamePk from match_id format "mlb_XXXXXX"
        try:
            game_pk = int(match_id.replace("mlb_", ""))
        except ValueError:
            print(f"[DD MLB] Could not parse game_pk from {match_id}")
            continue

        linescore = get_mlb_live_score(game_pk)
        if not linescore:
            print(f"[DD MLB] No linescore for {match_id} — game may not be live yet.")
            continue

        current_inning = linescore.get("currentInning", 0)
        inning_state   = linescore.get("inningState", "")  # Top / Middle / Bottom / End
        home_runs      = linescore.get("teams", {}).get("home", {}).get("runs", 0)
        away_runs      = linescore.get("teams", {}).get("away", {}).get("runs", 0)

        # ── INNING CHECK (the core fix) ──────────────────────────────
        # We want to trigger any time play has gone past the 6th inning.
        # Condition: inning 7+ started, OR inning 6 has ended.
        # inningState values: "Top", "Middle", "Bottom", "End"
        # "End" means that half-inning (and thus the full inning if Bottom)
        # is complete. We check >= 7 to cover all later innings too.
        past_sixth = (
            current_inning >= 7
            or (current_inning == 6 and inning_state == "End")
        )

        if not past_sixth:
            print(
                f"[DD MLB] {match_id} — inning {current_inning} {inning_state} "
                f"— not past 6th yet, skipping."
            )
            continue

        print(
            f"[DD MLB] {match_id} — inning {current_inning} {inning_state} "
            f"— past 6th, checking for double down opportunity."
        )

        # ── DETERMINE HOME/AWAY FROM LINESCORE ───────────────────────
        # The linescore API returns team IDs under teams.home/away.
        # This is more reliable than get_mlb_recent (which needs the
        # game to be finished) or any other workaround.
        home_team_id = (
            linescore.get("teams", {})
            .get("home", {})
            .get("team", {})
            .get("id")
        )

        if home_team_id is None:
            # Fallback: linescore didn't include team IDs (older API response).
            # We can't determine home/away, so skip this match safely.
            print(f"[DD MLB] {match_id} — could not determine home/away team from linescore, skipping.")
            continue

        team_home  = (home_team_id == team_id)
        team_score = home_runs if team_home else away_runs
        opp_score  = away_runs if team_home else home_runs

        # Score string: conventional away-home format e.g. "3-2"
        score_str = f"{away_runs}-{home_runs}"

        # ── SEND DOUBLE DOWN OFFERS ───────────────────────────────────
        for phone in data.get("users", []):
            dd_key = f"{match_id}:{phone}"
            if dd_key in dd_sent:
                # Already sent for this user/match — dedup handled by sheet
                continue

            user_pick = predictions.get(match_id, {}).get(phone)
            if not user_pick:
                # User hasn't placed a prediction — nothing to double down on
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
                # Score doesn't match prediction direction — no double down
                continue

            send_whatsapp(phone, msg)
            log_double_down_sent(match_id, phone, direction, dd_amount)
            dd_sent.add(dd_key)
            sent_any = True
            print(f"[DD MLB] Sent double down to {phone} for {match_id} ({direction}, ${dd_amount})")

    return sent_any

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

def check_pre_match(users: list, sent_ids: set, sport_key: str) -> bool:
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

    sent_ids  = get_sent_match_ids()
    pending   = get_pending_matches()
    dd_sent   = get_double_down_sent()
    all_preds = get_all_predictions(pending)

    fired = False

    for sport_key in SPORT_CONFIG:
        if check_pre_match(users, sent_ids, sport_key):
            fired = True

    if check_epl_double_down(pending, all_preds, dd_sent):  fired = True
    if check_mlb_double_down(pending, all_preds, dd_sent):  fired = True

    if check_post_match(pending):
        fired = True

    print("\n[Done] Prompts sent!" if fired else "\n[Done] No matches in window right now.")

if __name__ == "__main__":
    main()
