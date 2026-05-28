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

import json
import os
import random
import sys
import datetime
import requests
import statsapi

from akrue.env import FOOTBALL_API_KEY
from akrue.config import (
    SPORT_CONFIG, SPORT_TEAM_IDS,
    PROMPT_WINDOW_MIN, PROMPT_WINDOW_MAX,
    MLB_PRE_GAME_STATUSES, MLB_FINAL_STATUSES,
    CAP_WARNING_THRESHOLD, DEFAULT_CAP_MULTIPLIER, MAX_CAP_MULTIPLIER,
)
from akrue.supabase_client import get_client
from akrue.messaging import normalise_phone, get_user_channel, send_message
from akrue.amounts import get_week_bounds, current_week, get_week_savings, calculate_amounts

# ─────────────────────────────────────────────
# SUPABASE — USERS
# ─────────────────────────────────────────────

def get_active_users() -> list:
    try:
        sb     = get_client()
        result = sb.table("users").select("*").eq("status", "active").execute()
        users  = result.data or []
        print(f"[Users] Found {len(users)} active users.")
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

# ─────────────────────────────────────────────
# SUPABASE — SAVINGS LOG
# ─────────────────────────────────────────────

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

                msg = random.choice(INSURANCE_EPL_VARIANTS).format(
                    score=score_str, amount=insurance_amount,
                )

                send_message(phone_n, msg)
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

        team_id = data["team_id"]

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

            msg = random.choice(INSURANCE_MLB_VARIANTS).format(
                score=score_str, amount=insurance_amount, inning=current_inning,
            )

            send_message(phone_n, msg)
            log_insurance_offered(match_id, phone_n, insurance_amount)
            insurance_offered.add(offer_key)
            sent_any = True
            print(f"[Insurance MLB] Offered ${insurance_amount} to {phone_n} for {match_id}.")

    return sent_any

# ─────────────────────────────────────────────
# MESSAGING — VARIANT LISTS
# ─────────────────────────────────────────────

PROMPT_EPL_VARIANTS = [
    (
        "Hey {name}! {team} vs {opponent} kicks off in about {mins} minutes.\n\n"
        "Pay yourself ${correct} if you are right!\n"
        "   Win\n   Draw\n   Loss\n\n"
        "Wrong prediction still pays you ${wrong}. Reply win, draw, or loss to lock in your pick."
    ),
    (
        "Game day, {name}! {team} vs {opponent} in about {mins} minutes.\n\n"
        "Make your prediction, it pays either way:\n"
        "  Nail it: pay yourself ${correct}\n"
        "  Miss it: still pay yourself ${wrong}\n\n"
        "Reply win, draw, or loss."
    ),
    (
        "{team} vs {opponent} kicks off in about {mins} minutes, {name}.\n\n"
        "Right pays you ${correct}, wrong still pays you ${wrong}.\n\n"
        "Reply win, draw, or loss to go."
    ),
]

PROMPT_MLB_VARIANTS = [
    (
        "Hey {name}! {team} vs {opponent} - first pitch in about {mins} minutes.\n\n"
        "Pay yourself ${correct} if you are right!\n"
        "   Win\n   Loss\n\n"
        "Wrong prediction still pays you ${wrong}. Reply win or loss to lock in your pick."
    ),
    (
        "Game day, {name}! {team} vs {opponent} in about {mins} minutes.\n\n"
        "Make your prediction, it pays either way:\n"
        "  Nail it: pay yourself ${correct}\n"
        "  Miss it: still pay yourself ${wrong}\n\n"
        "Reply win or loss."
    ),
    (
        "{team} vs {opponent} - first pitch in about {mins} minutes, {name}.\n\n"
        "Right pays you ${correct}, wrong still pays you ${wrong}.\n\n"
        "Reply win or loss to go."
    ),
]

REMINDER_VARIANTS = [
    (
        "Hey {name}, {team} vs {opponent} kicks off in about {mins} minutes - coming up fast!\n\n"
        "You have not predicted yet. Reply {options} before the whistle blows."
    ),
    (
        "Under 15 minutes, {name}!\n\n"
        "{team} vs {opponent} in about {mins} min - you have not picked yet.\n"
        "Reply {options} before the whistle blows."
    ),
    (
        "Last call, {name}!\n\n"
        "{team} starts in about {mins} min. Miss the window and you miss out.\n"
        "Reply {options} now!"
    ),
]

RESULT_WIN_VARIANTS = [
    "{emoji} {team} win! {score}.",
    "{emoji} {team} get the W, {score}.",
    "{emoji} Result: {team} {score}.",
]

RESULT_LOSS_VARIANTS = [
    "{emoji} {team} fall {score}.",
    "{emoji} Rough one - {team} go down {score}.",
    "{emoji} {team} {score}. Did not go your way.",
]

RESULT_DRAW_VARIANTS = [
    "{emoji} {team} draw {score} with {opponent}.",
    "{emoji} All square - {team} {score}.",
]

INSURANCE_EPL_VARIANTS = [
    (
        "Half time score is {score} - not looking great for your prediction.\n\n"
        "Want to cash out early? Pay yourself ${amount} right now and close out.\n\n"
        "Reply INSURE to take it, or do nothing and ride it out."
    ),
    (
        "Halftime - {score}.\n\n"
        "Things are not looking great. Pay yourself ${amount} now and walk away, "
        "or roll the dice in the second half.\n\n"
        "Reply INSURE to take it, or sit tight."
    ),
    (
        "{score} at the break.\n\n"
        "Your prediction is on thin ice - but you can still pay yourself ${amount} right now.\n\n"
        "INSURE to take it. Nothing to ride it out."
    ),
]

INSURANCE_MLB_VARIANTS = [
    (
        "After {inning} innings - {score} - not looking great for your prediction.\n\n"
        "Want to cash out early? Pay yourself ${amount} right now and close out.\n\n"
        "Reply INSURE to take it, or do nothing and ride it out."
    ),
    (
        "Inning {inning} - {score}.\n\n"
        "Things are not looking great. Pay yourself ${amount} now and walk away.\n\n"
        "Reply INSURE to take it, or sit tight."
    ),
    (
        "{score} after {inning} innings.\n\n"
        "Your prediction is on thin ice - but you can still pay yourself ${amount} right now.\n\n"
        "INSURE to take it. Nothing to ride it out."
    ),
]


def build_prompt_message(name: str, team: str, opponent: str, sport_key: str,
                         correct_amount: int, wrong_amount: int,
                         mins_until_kickoff: int = 30,
                         near_cap: bool = False,
                         cap_exhausted: bool = False,
                         remaining: float = 0) -> str:
    variants = PROMPT_EPL_VARIANTS if sport_key == "epl" else PROMPT_MLB_VARIANTS
    body = random.choice(variants).format(
        name=name, team=team, opponent=opponent,
        mins=mins_until_kickoff, correct=correct_amount, wrong=wrong_amount,
    )
    if cap_exhausted:
        body += "\n\nYou have hit your weekly savings cap - no further savings will be deposited this week."
    elif near_cap:
        body += "\n\nYou are close to your weekly cap - amounts adjusted."
    return body


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
    if not pick or pick.upper() == "N/A":
        return None

    cfg    = SPORT_CONFIG[sport_key]
    amount = correct_amount if pick == result else wrong_amount

    if result == "win":
        emoji = cfg["win_emoji"]
        score = reorder_score(score, result)
        head  = random.choice(RESULT_WIN_VARIANTS).format(
            emoji=emoji, team=team_name, score=score, opponent=opponent,
        )
    elif result == "draw":
        emoji = cfg.get("draw_emoji", "🟡")
        head  = random.choice(RESULT_DRAW_VARIANTS).format(
            emoji=emoji, team=team_name, score=score, opponent=opponent,
        )
    else:
        emoji = cfg["loss_emoji"]
        score = reorder_score(score, result)
        head  = random.choice(RESULT_LOSS_VARIANTS).format(
            emoji=emoji, team=team_name, score=score, opponent=opponent,
        )

    if pick == result:
        return f"{head}\nYou paid yourself ${amount}."
    else:
        return f"{head}\nYou still paid yourself ${amount}."

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
                    name=name,
                    team=team_name,
                    opponent=opp,
                    sport_key=sport_key,
                    correct_amount=amt["correct_amount"],
                    wrong_amount=amt["wrong_amount"],
                    mins_until_kickoff=max(1, round(mins)),
                    near_cap=amt["near_cap"],
                    cap_exhausted=amt["cap_exhausted"],
                    remaining=amt["remaining"],
                )

                channel = u.get("channel", "whatsapp")
                send_message(phone_n, msg, channel=channel)
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
        # Only return rows where prediction is empty
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
            kickoff = datetime.datetime.fromisoformat(kickoff_str).replace(tzinfo=None)
        except ValueError:
            continue

        mins = (kickoff - now).total_seconds() / 60
        if not (0 < mins < 15):
            continue

        sport_key = match_data.get("sport", "epl")
        cfg       = SPORT_CONFIG[sport_key]
        reply_str = " or ".join(o.lower() for o in cfg["options"])
        team_name = match_data.get("team_name", "your team")
        opponent  = match_data.get("opponent", "")

        user    = user_lookup.get(phone_n, {})
        name    = user.get("name", "there")
        channel = user.get("channel", "whatsapp")

        msg = random.choice(REMINDER_VARIANTS).format(
            name=name, team=team_name, opponent=opponent,
            mins=max(1, round(mins)), options=reply_str,
        )

        send_message(phone_n, msg, channel=channel)
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
                kickoff = datetime.datetime.fromisoformat(kickoff_str).replace(tzinfo=None)
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
# POSTPONED MATCH DETECTION
# ─────────────────────────────────────────────

def get_epl_match_status(match_api_id: int) -> str:
    url  = f"https://api.football-data.org/v4/matches/{match_api_id}"
    resp = requests.get(url, headers={"X-Auth-Token": FOOTBALL_API_KEY}, timeout=10)
    if resp.status_code != 200:
        return ""
    return resp.json().get("status", "")


def check_postponed_matches(pending: dict) -> bool:
    sent_any = False

    for match_id, data in list(pending.items()):
        if data.get("sport") != "epl":
            continue

        try:
            api_match_id = int(match_id.split("_")[1])
        except (ValueError, IndexError):
            continue

        status = get_epl_match_status(api_match_id)
        if status not in ("POSTPONED", "CANCELLED"):
            continue

        team_name = data.get("team_name", "")
        opponent  = data.get("opponent", "")

        for phone in data.get("users", []):
            phone_n = normalise_phone(phone)
            msg = (
                f"Heads up - {team_name} vs {opponent} has been postponed.\n\n"
                f"Your pending pick has been cancelled. We will send a new prompt "
                f"when the match is rescheduled."
            )
            send_message(phone_n, msg)

        try:
            sb = get_client()
            sb.table("predictions").update({"status": "cancelled"}).eq("match_id", match_id).execute()
            sb.table("pending_matches").update({"settled": True}).eq("match_id", match_id).execute()
        except Exception as e:
            print(f"[Postponed] Error cancelling {match_id}: {e}")

        print(f"[Postponed] {match_id} {status} - notified {len(data.get('users', []))} users.")
        sent_any = True

    return sent_any


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
            send_message(
                normalise_phone(str(user["phone_number"])),
                (
                    f"Akrue test - hey {user.get('name','there')}!\n"
                    f"Teams: {' | '.join(teams) or 'none set'}\n"
                    f"System is live and ready for match prompts!"
                ),
                channel=user.get("channel", "whatsapp"),
            )
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
    for sport_key in SPORT_CONFIG:
        if check_pre_match(users, sent_per_user, sport_key):
            fired = True
    if check_postponed_matches(pending):                             fired = True
    if check_epl_insurance(pending, all_preds, insurance_offered): fired = True
    if check_mlb_insurance(pending, all_preds, insurance_offered): fired = True
    if check_post_match(pending):                                   fired = True
    if check_reminders(users, pending):                             fired = True

    print("\n[Done] Prompts sent!" if fired else "\n[Done] No matches in window right now.")

if __name__ == "__main__":
    main()
