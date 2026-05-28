"""
Akrue — Webhook Server
-----------------------
Receives WhatsApp/SMS replies from Twilio and logs predictions to Supabase.
Sport-agnostic — reads sport from pending_matches table and validates
predictions against that sport's allowed options.

Deploys to Railway. Always-on Flask app.
"""

import os
import json
import random
import secrets
import datetime
import requests
import statsapi
from flask import Flask, request, jsonify
from flask_cors import CORS
from twilio.twiml.messaging_response import MessagingResponse

from akrue.env import FOOTBALL_API_KEY
from akrue.config import SPORT_CONFIG, DEFAULT_CAP_MULTIPLIER, MAX_CAP_MULTIPLIER, CAP_WARNING_THRESHOLD
from akrue.supabase_client import get_client
from akrue.messaging import normalise_phone, get_user_channel, send_message
from akrue.amounts import get_week_bounds, current_week, get_week_savings, calculate_amounts

app = Flask(__name__)
CORS(app)

PREDICTION_MAP = {
    "win":  "win",
    "w":    "win",
    "1":    "win",
    "draw": "draw",
    "d":    "draw",
    "2":    "draw",
    "loss": "loss",
    "l":    "loss",
    "3":    "loss",
}

INSURANCE_TRIGGERS = {"insure"}
STOP_TRIGGERS      = {"stop", "quit", "cancel", "end", "unsubscribe"}
START_TRIGGERS     = {"start"}
HELP_TRIGGERS    = {"help"}
GOAL_TRIGGERS    = {"goal", "progress"}
BALANCE_TRIGGERS = {"balance", "total"}
STREAK_TRIGGERS  = {"streak"}
RANK_TRIGGERS    = {"rank", "leaderboard"}

# ─────────────────────────────────────────────
# WELCOME VARIANTS
# ─────────────────────────────────────────────

WELCOME_VARIANTS = [
    (
        "👋 Welcome to Akrue, {name}!\n\n"
        "Here's how it works:\n"
        "• Before your team's matches, we'll text you\n"
        "• Predict win, draw, or loss\n"
        "• You pay yourself either way — more if you're right\n\n"
        "No risk. Just pay yourself + the game.\n\n"
        "Reply HELP anytime for instructions, STOP to unsubscribe."
    ),
    (
        "Hey {name}, you're in! ⚽\n\n"
        "Akrue turns match predictions into money you pay yourself. "
        "Before each game, we text you — predict the result and you pay yourself, right or wrong.\n\n"
        "Text HELP for commands or STOP to unsubscribe anytime."
    ),
]

HELP_TEXT = (
    "Akrue: Predict match results, pay yourself either way.\n\n"
    "Commands:\n"
    "  WIN / DRAW / LOSS — make your prediction\n"
    "  INSURE — cash out early mid-match\n"
    "  GOAL — weekly pay-yourself progress\n"
    "  BALANCE — all-time total paid to yourself\n"
    "  STREAK — your current correct-prediction streak\n"
    "  RANK — your leaderboard position\n"
    "  STOP — unsubscribe\n"
    "  START — resubscribe"
)

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def normalise_prediction(raw: str) -> str | None:
    cleaned = raw.strip().lower()
    if cleaned in PREDICTION_MAP:
        return PREDICTION_MAP[cleaned]
    for keyword in ("win", "draw", "loss"):
        if cleaned.endswith(keyword):
            return keyword
    return None

# ─────────────────────────────────────────────
# SUPABASE HELPERS
# ─────────────────────────────────────────────

def find_active_match(user_phone: str):
    """
    Find the most recent pending prediction for this user.
    Returns (row_id, row_data, sport_key) or (None, None, None).
    """
    try:
        sb     = get_client()
        result = sb.table("predictions") \
            .select("*") \
            .eq("user_phone", user_phone) \
            .eq("status", "pending") \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        rows = result.data or []
        print(f"[Lookup] Searching for: '{user_phone}' — found {len(rows)} pending rows")

        if not rows:
            return None, None, None

        row        = rows[0]
        match_info = get_match_info(row["match_id"])
        return row["id"], row, match_info["sport"]

    except Exception as e:
        print(f"[Lookup] Error: {e}")
        return None, None, None

def get_match_info(match_id: str) -> dict:
    try:
        sb     = get_client()
        result = sb.table("pending_matches") \
            .select("sport, kickoff_utc") \
            .eq("match_id", match_id) \
            .limit(1) \
            .execute()
        rows = result.data or []
        if rows:
            return {
                "sport":       rows[0].get("sport", "epl"),
                "kickoff_utc": rows[0].get("kickoff_utc", ""),
            }
    except Exception as e:
        print(f"[Match info lookup] Error: {e}")
    return {"sport": "epl", "kickoff_utc": ""}

def get_user_by_phone(phone: str) -> dict:
    try:
        sb     = get_client()
        result = sb.table("users") \
            .select("*") \
            .eq("phone_number", phone) \
            .limit(1) \
            .execute()
        rows = result.data or []
        return rows[0] if rows else {}
    except Exception as e:
        print(f"[User lookup] Error: {e}")
        return {}

def log_prediction(prediction_id: int, prediction: str,
                   correct_amount: int = None, wrong_amount: int = None):
    sb     = get_client()
    update = {
        "prediction": prediction,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if correct_amount is not None:
        update["correct_amount"] = correct_amount
    if wrong_amount is not None:
        update["wrong_amount"] = wrong_amount
    sb.table("predictions").update(update).eq("id", prediction_id).execute()

def mark_opted_out(phone: str):
    try:
        sb = get_client()
        sb.table("users").update({"status": "opted_out"}).eq("phone_number", phone).execute()
        print(f"[Opt-out] {phone} marked opted_out")
    except Exception as e:
        print(f"[Opt-out] Error: {e}")

def mark_opted_in(phone: str):
    try:
        sb = get_client()
        sb.table("users").update({"status": "active"}).eq("phone_number", phone).execute()
        print(f"[Opt-in] {phone} marked active")
    except Exception as e:
        print(f"[Opt-in] Error: {e}")

# ─────────────────────────────────────────────
# INBOUND QUERY HANDLERS
# ─────────────────────────────────────────────

def handle_goal_query(user_phone: str) -> str:
    try:
        sb = get_client()
        week_start, week_end = get_week_bounds()

        user_result = sb.table("users").select("name, weekly_bankroll").eq("phone_number", user_phone).limit(1).execute()
        user        = (user_result.data or [{}])[0]
        name        = user.get("name", "there")
        bankroll    = float(user.get("weekly_bankroll") or 50)

        savings_result = (
            sb.table("savings_log")
            .select("amount, trigger")
            .eq("user_phone", user_phone)
            .gte("date", week_start.isoformat())
            .lte("date", week_end.isoformat())
            .execute()
        )
        rows       = savings_result.data or []
        week_total = sum(float(r.get("amount", 0)) for r in rows)
        correct    = sum(1 for r in rows if r.get("trigger", "").endswith("_correct"))

        preds_result = (
            sb.table("predictions")
            .select("prediction")
            .eq("user_phone", user_phone)
            .gte("created_at", week_start.isoformat())
            .execute()
        )
        n_preds = len([
            p for p in (preds_result.data or [])
            if p.get("prediction") and p["prediction"].upper() not in ("", "N/A")
        ])

        pct    = min(100, round(week_total / bankroll * 100)) if bankroll > 0 else 0
        filled = round(pct / 10)
        bar    = "█" * filled + "░" * (10 - filled)

        return (
            f"📊 Week goal, {name}:\n\n"
            f"Paid yourself this week: ${round(week_total)} / ${round(bankroll)} target\n"
            f"{n_preds} predictions, {correct} correct\n\n"
            f"{bar}  {pct}%\n\n"
            f"Keep it up — next match coming soon."
        )
    except Exception as e:
        print(f"[GOAL query] Error: {e}")
        return "Couldn't fetch your weekly goal right now. Try again later."

def handle_balance_query(user_phone: str) -> str:
    try:
        sb = get_client()
        week_start, week_end = get_week_bounds()

        result = sb.table("savings_log").select("amount, date").eq("user_phone", user_phone).execute()
        rows   = result.data or []

        all_total  = sum(float(r.get("amount", 0)) for r in rows)
        week_total = sum(
            float(r.get("amount", 0)) for r in rows
            if r.get("date") and
            week_start <= datetime.date.fromisoformat(str(r["date"])[:10]) <= week_end
        )

        def _week_start(d):
            days = (d.weekday() - 4) % 7
            return d - datetime.timedelta(days=days)

        by_week = {}
        for r in rows:
            if not r.get("date"):
                continue
            d  = datetime.date.fromisoformat(str(r["date"])[:10])
            wk = _week_start(d).isoformat()
            by_week[wk] = by_week.get(wk, 0) + float(r.get("amount", 0))

        best_week = max(by_week.values(), default=0)

        return (
            f"💰 Your Akrue total:\n\n"
            f"All-time paid to yourself: ${round(all_total)}\n"
            f"This week: ${round(week_total)}\n"
            f"Best week: ${round(best_week)}"
        )
    except Exception as e:
        print(f"[BALANCE query] Error: {e}")
        return "Couldn't fetch your balance right now. Try again later."

def handle_streak_query(user_phone: str) -> str:
    try:
        sb = get_client()

        result  = (
            sb.table("savings_log")
            .select("trigger, date")
            .eq("user_phone", user_phone)
            .order("date", desc=False)
            .execute()
        )
        entries = [
            r for r in (result.data or [])
            if r.get("trigger", "").startswith(("epl_bet", "mlb_bet"))
        ]

        total_correct = sum(1 for e in entries if e.get("trigger", "").endswith("_correct"))
        total_picks   = len(entries)

        # Current streak: consecutive correct from most recent backwards
        streak = 0
        for e in reversed(entries):
            if e.get("trigger", "").endswith("_correct"):
                streak += 1
            else:
                break

        # Best streak: scan oldest to newest
        best_streak = current = 0
        for e in entries:
            if e.get("trigger", "").endswith("_correct"):
                current += 1
                best_streak = max(best_streak, current)
            else:
                current = 0

        return (
            f"🔥 Current streak: {streak} correct predictions in a row\n\n"
            f"Best streak: {best_streak}\n"
            f"Total correct: {total_correct} / {total_picks}"
        )
    except Exception as e:
        print(f"[STREAK query] Error: {e}")
        return "Couldn't fetch your streak right now. Try again later."

def handle_rank_query(user_phone: str) -> str:
    try:
        sb = get_client()
        week_start, week_end = get_week_bounds()

        users_result   = sb.table("users").select("phone_number, name").eq("status", "active").execute()
        savings_result = (
            sb.table("savings_log")
            .select("user_phone, amount")
            .gte("date", week_start.isoformat())
            .lte("date", week_end.isoformat())
            .execute()
        )

        by_user = {}
        for s in (savings_result.data or []):
            phone = normalise_phone(str(s.get("user_phone", "")))
            by_user[phone] = by_user.get(phone, 0) + float(s.get("amount", 0))

        rankings = [
            (u.get("name", ""), normalise_phone(str(u.get("phone_number", ""))),
             by_user.get(normalise_phone(str(u.get("phone_number", ""))), 0))
            for u in (users_result.data or [])
        ]
        rankings.sort(key=lambda x: x[2], reverse=True)

        user_rank  = next((i + 1 for i, (_, ph, _) in enumerate(rankings) if ph == user_phone), None)
        user_total = by_user.get(user_phone, 0)

        top3       = rankings[:3]
        top3_lines = "\n".join(
            f"{i+1}. {name} — ${round(total)}"
            for i, (name, _, total) in enumerate(top3)
        )
        week_str = week_start.strftime("%b %d")

        return (
            f"🏆 Leaderboard — Week of {week_str}\n\n"
            f"{top3_lines}\n\n"
            f"You're #{user_rank} with ${round(user_total)} paid to yourself."
        )
    except Exception as e:
        print(f"[RANK query] Error: {e}")
        return "Couldn't fetch leaderboard right now. Try again later."

# ─────────────────────────────────────────────
# INSURANCE HELPERS
# ─────────────────────────────────────────────

def get_pending_insurance(user_phone: str) -> dict:
    """Returns the most recent unaccepted insurance offer for this user."""
    try:
        sb     = get_client()
        result = sb.table("insurance_offers") \
            .select("*") \
            .eq("user_phone", user_phone) \
            .eq("accepted", False) \
            .order("sent_at", desc=True) \
            .limit(1) \
            .execute()
        rows = result.data or []
        if rows:
            return {
                "id":       rows[0]["id"],
                "match_id": rows[0]["match_id"],
                "amount":   int(rows[0].get("amount", 0)),
            }
    except Exception as e:
        print(f"[Insurance lookup] Error: {e}")
    return {}

def mark_insurance_accepted(offer_id: int):
    try:
        sb = get_client()
        sb.table("insurance_offers").update({"accepted": True}).eq("id", offer_id).execute()
    except Exception as e:
        print(f"[Insurance accept] Error: {e}")

def mark_prediction_insured(match_id: str, user_phone: str):
    try:
        sb = get_client()
        sb.table("predictions") \
            .update({"status": "insured"}) \
            .eq("match_id", match_id) \
            .eq("user_phone", user_phone) \
            .execute()
        print(f"[Insurance] Marked prediction insured for {user_phone} on {match_id}")
    except Exception as e:
        print(f"[Insurance] Error marking insured: {e}")

def log_insurance_savings(user_phone: str, match_id: str, amount: int, sport: str):
    try:
        sb = get_client()
        sb.table("savings_log").insert({
            "date":       datetime.date.today().isoformat(),
            "user_phone": user_phone,
            "amount":     amount,
            "trigger":    "insurance_buyout",
            "match_id":   match_id,
            "week":       current_week(),
            "sport":      sport,
        }).execute()
    except Exception as e:
        print(f"[Insurance savings log] Error: {e}")

# ─────────────────────────────────────────────
# INBOUND QUERY MESSAGE BUILDERS
# ─────────────────────────────────────────────

def build_goal_message(phone: str) -> str:
    try:
        sb               = get_client()
        user             = get_user_by_phone(phone)
        if not user:
            return "Could not find your account."
        week_start, week_end = get_week_bounds()
        rows = (
            sb.table("savings_log")
            .select("amount, trigger")
            .eq("user_phone", phone)
            .gte("date", week_start.isoformat())
            .lte("date", week_end.isoformat())
            .execute()
            .data or []
        )
        week_total   = sum(float(r.get("amount", 0)) for r in rows)
        n_correct    = sum(1 for r in rows if r.get("trigger", "").endswith("_correct"))
        bankroll     = float(user.get("weekly_bankroll") or 50)
        pct          = min(int(week_total / bankroll * 100), 100) if bankroll else 0
        bar          = "█" * (pct // 10) + "░" * (10 - pct // 10)
        name         = user.get("name", "there")
        return (
            f"Week goal, {name}:\n\n"
            f"Paid yourself this week: ${week_total:.0f} / ${bankroll:.0f} target\n"
            f"{len(rows)} predictions, {n_correct} correct\n\n"
            f"{bar}  {pct}%\n\n"
            f"Keep it up - next match coming soon."
        )
    except Exception as e:
        print(f"[Goal] Error: {e}")
        return "Could not load your goal data right now."


def build_balance_message(phone: str) -> str:
    try:
        sb               = get_client()
        user             = get_user_by_phone(phone)
        if not user:
            return "Could not find your account."
        week_start, week_end = get_week_bounds()
        rows = (
            sb.table("savings_log")
            .select("amount, date")
            .eq("user_phone", phone)
            .execute()
            .data or []
        )
        all_time   = sum(float(r.get("amount", 0)) for r in rows)
        week_total = sum(
            float(r.get("amount", 0)) for r in rows
            if r.get("date") and
            week_start <= datetime.date.fromisoformat(str(r["date"])[:10]) <= week_end
        )
        by_week = {}
        for r in rows:
            if not r.get("date"):
                continue
            d  = datetime.date.fromisoformat(str(r["date"])[:10])
            wk = (d - datetime.timedelta(days=(d.weekday() - 4) % 7)).isoformat()
            by_week[wk] = by_week.get(wk, 0) + float(r.get("amount", 0))
        best_week = max(by_week.values()) if by_week else 0
        name = user.get("name", "there")
        return (
            f"Your Akrue total, {name}:\n\n"
            f"All-time paid to yourself: ${all_time:.0f}\n"
            f"This week: ${week_total:.0f}\n"
            f"Best week: ${best_week:.0f}"
        )
    except Exception as e:
        print(f"[Balance] Error: {e}")
        return "Could not load your balance right now."


def build_streak_message(phone: str) -> str:
    try:
        sb   = get_client()
        rows = (
            sb.table("savings_log")
            .select("trigger")
            .eq("user_phone", phone)
            .order("date", desc=True)
            .execute()
            .data or []
        )
        total_picks   = len(rows)
        total_correct = sum(1 for r in rows if r.get("trigger", "").endswith("_correct"))
        streak = 0
        for r in rows:
            if r.get("trigger", "").endswith("_correct"):
                streak += 1
            else:
                break
        best, cur = 0, 0
        for r in reversed(rows):
            if r.get("trigger", "").endswith("_correct"):
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        return (
            f"Current streak: {streak} correct predictions in a row\n\n"
            f"Best streak: {best}\n"
            f"Total correct: {total_correct} / {total_picks}"
        )
    except Exception as e:
        print(f"[Streak] Error: {e}")
        return "Could not load your streak right now."


def build_rank_message(phone: str) -> str:
    try:
        sb               = get_client()
        week_start, week_end = get_week_bounds()
        users_list = (
            sb.table("users")
            .select("phone_number, name")
            .eq("status", "active")
            .execute()
            .data or []
        )
        week_rows = (
            sb.table("savings_log")
            .select("user_phone, amount")
            .gte("date", week_start.isoformat())
            .lte("date", week_end.isoformat())
            .execute()
            .data or []
        )
        by_phone = {}
        for r in week_rows:
            p = normalise_phone(str(r.get("user_phone", "")))
            by_phone[p] = by_phone.get(p, 0) + float(r.get("amount", 0))
        ranked = sorted(
            [(u.get("name", ""), normalise_phone(str(u.get("phone_number", "")))) for u in users_list],
            key=lambda x: by_phone.get(x[1], 0),
            reverse=True,
        )
        user_rank  = next((i + 1 for i, (_, p) in enumerate(ranked) if p == phone), None)
        user_total = by_phone.get(phone, 0)
        top3       = ranked[:3]
        medals     = ["1.", "2.", "3."]
        lines      = [f"{medals[i]} {name} - ${by_phone.get(p, 0):.0f}" for i, (name, p) in enumerate(top3)]
        body       = f"Leaderboard - Week of {week_start.strftime('%b %d')}\n\n" + "\n".join(lines)
        if user_rank:
            body += f"\n\nYou are #{user_rank} with ${user_total:.0f} this week."
        return body
    except Exception as e:
        print(f"[Rank] Error: {e}")
        return "Could not load the leaderboard right now."


# ─────────────────────────────────────────────
# LIVE SCORE
# ─────────────────────────────────────────────

@app.route('/live-score', methods=['GET'])
def live_score():
    match_id = request.args.get('match_id', '')
    try:
        game_id = int(match_id.split('_')[1])
    except:
        return jsonify({'error': 'invalid match_id'}), 400

    try:
        game        = statsapi.get('game', {'gamePk': game_id})
        linescore   = game['liveData']['linescore']
        away        = game['gameData']['teams']['away']['abbreviation']
        home        = game['gameData']['teams']['home']['abbreviation']
        away_score  = linescore['teams']['away'].get('runs', 0)
        home_score  = linescore['teams']['home'].get('runs', 0)
        inning      = linescore.get('currentInning', '')
        inning_half = linescore.get('inningHalf', '')
        status      = game['gameData']['status']['abstractGameState']

        return jsonify({
            'status': status,
            'score':  f"{away} {away_score} - {home_score} {home}",
            'inning': f"{inning_half} {inning}" if inning else ''
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/live-score-epl', methods=['GET'])
def live_score_epl():
    match_id = request.args.get('match_id', '')
    try:
        parts   = match_id.split('_')
        game_id = int(parts[1])
    except:
        return jsonify({'error': 'invalid match_id'}), 400

    try:
        url  = f"https://api.football-data.org/v4/matches/{game_id}"
        resp = requests.get(url, headers={"X-Auth-Token": FOOTBALL_API_KEY}, timeout=10)
        data = resp.json()
        home   = data['homeTeam']['shortName']
        away   = data['awayTeam']['shortName']
        score  = data['score']['fullTime']
        hg     = score.get('home') or 0
        ag     = score.get('away') or 0
        status = data['status']
        return jsonify({
            'status': status,
            'score':  f"{away} {ag} - {hg} {home}",
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─────────────────────────────────────────────
# WEBHOOK (WhatsApp + SMS unified)
# ─────────────────────────────────────────────

def handle_inbound(user_phone: str, incoming_msg: str, channel: str):
    resp = MessagingResponse()
    msg  = resp.message()
    word = incoming_msg.strip().lower()

    # ── STOP / opt-out (must be first — legal requirement) ──
    if word in STOP_TRIGGERS:
        mark_opted_out(user_phone)
        msg.body(
            "You've been unsubscribed from Akrue match alerts. No more messages will be sent.\n\n"
            "Reply START anytime to rejoin."
        )
        return str(resp)

    # ── START / re-subscribe ──
    if word in START_TRIGGERS:
        mark_opted_in(user_phone)
        msg.body(
            "You're back! Akrue match alerts are reactivated.\n\n"
            "Reply STOP to unsubscribe or HELP for a list of commands."
        )
        return str(resp)

    # ── HELP ──
    if word in HELP_TRIGGERS:
        msg.body(HELP_TEXT)
        return str(resp)

    # ── GOAL / PROGRESS ──
    if word in GOAL_TRIGGERS:
        msg.body(handle_goal_query(user_phone))
        return str(resp)

    # ── BALANCE / TOTAL ──
    if word in BALANCE_TRIGGERS:
        msg.body(handle_balance_query(user_phone))
        return str(resp)

    # ── STREAK ──
    if word in STREAK_TRIGGERS:
        msg.body(handle_streak_query(user_phone))
        return str(resp)

    # ── RANK / LEADERBOARD ──
    if word in RANK_TRIGGERS:
        msg.body(handle_rank_query(user_phone))
        return str(resp)

    # ── INSURANCE reply ──
    if word in INSURANCE_TRIGGERS:
        offer = get_pending_insurance(user_phone)
        if not offer:
            msg.body(
                "No active insurance offer for you right now. "
                "Watch for one mid-match if your prediction is looking shaky! 👀"
            )
            return str(resp)

        match_info = get_match_info(offer["match_id"])
        sport      = match_info["sport"]
        emoji      = SPORT_CONFIG.get(sport, {}).get("emoji", "⚽")

        mark_insurance_accepted(offer["id"])
        mark_prediction_insured(offer["match_id"], user_phone)
        log_insurance_savings(user_phone, offer["match_id"], offer["amount"], sport)
        msg.body(
            f"{emoji} Cashed out! "
            f"You're paying yourself *${offer['amount']}* and your prediction is closed. 💰\n\n"
            f"Smart move — locked in!"
        )
        return str(resp)

    # ── Normalise prediction input ──
    pick = normalise_prediction(incoming_msg)
    if not pick:
        return str(MessagingResponse())

    # ── Find active match ──
    prediction_id, row, sport_key = find_active_match(user_phone)
    if not prediction_id:
        return str(MessagingResponse())

    # ── Check if kickoff has already passed ──
    match_id    = row.get("match_id")
    match_info  = get_match_info(match_id)
    kickoff_str = match_info["kickoff_utc"]
    if kickoff_str:
        try:
            kickoff = datetime.datetime.fromisoformat(kickoff_str)
            now     = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            if kickoff.tzinfo:
                kickoff = kickoff.replace(tzinfo=None)
            if now > kickoff:
                emoji = SPORT_CONFIG.get(sport_key, {}).get("emoji", "⚽")
                msg.body(
                    f"{emoji} The game has already started — "
                    f"your prediction is locked in, no changes allowed!"
                )
                return str(resp)
        except Exception as e:
            print(f"[Kickoff check] Error: {e}")

    # ── Look up sport config ──
    allows_draw = SPORT_CONFIG.get(sport_key, {}).get("allows_draw", True)
    options     = SPORT_CONFIG.get(sport_key, {}).get("options", ["WIN", "DRAW", "LOSS"])
    emoji       = SPORT_CONFIG.get(sport_key, {}).get("emoji", "⚽")

    # ── Validate prediction ──
    if pick == "draw" and not allows_draw:
        options_str = " or ".join(o.lower() for o in options)
        msg.body(
            f"{emoji} That sport doesn't have draws!\n\n"
            f"Reply {options_str} to lock in your prediction."
        )
        return str(resp)

    # ── Use amounts stored in prediction row ──
    existing_correct = int(row.get("correct_amount") or 0)
    existing_wrong   = int(row.get("wrong_amount") or 0)

    if not existing_correct:
        user = get_user_by_phone(user_phone)
        if user:
            amt = calculate_amounts(user, user_phone)
            existing_correct = amt["correct_amount"]
            existing_wrong   = amt["wrong_amount"]

    # ── Log the prediction ──
    try:
        log_prediction(prediction_id, pick, existing_correct, existing_wrong)
        msg.body(
            f"{emoji} Locked in: *{pick.upper()}*!\n\n"
            f"Right prediction → pay yourself *${existing_correct}* 💰\n"
            f"Wrong prediction → you still pay yourself *${existing_wrong}* 💰\n\n"
            f"I'll message you after the match with your result!"
        )
    except Exception as e:
        print(f"[Error] {e}")
        msg.body("⚠️ Something went wrong logging your prediction. Please try again!")

    return str(resp)


@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    user_phone   = normalise_phone(request.values.get("From", "").strip())
    incoming_msg = request.values.get("Body", "").strip()
    print(f"[Incoming WA] {user_phone}: {incoming_msg}")
    return handle_inbound(user_phone, incoming_msg, "whatsapp")


@app.route("/sms", methods=["POST"])
def sms_reply():
    user_phone   = normalise_phone(request.values.get("From", "").strip())
    incoming_msg = request.values.get("Body", "").strip()
    print(f"[Incoming SMS] {user_phone}: {incoming_msg}")
    return handle_inbound(user_phone, incoming_msg, "sms")

# ─────────────────────────────────────────────
# PLACE BET (web app endpoint)
# ─────────────────────────────────────────────

@app.route("/place-bet", methods=["POST"])
def place_bet():
    data     = request.get_json(force=True)
    phone    = normalise_phone(data.get("phone", "").strip())
    match_id = data.get("match_id", "").strip()
    pick_raw = data.get("pick", "").strip()

    if not phone or not match_id or not pick_raw:
        return {"success": False, "error": "Missing fields"}, 400

    pick = normalise_prediction(pick_raw)
    if not pick:
        return {"success": False, "error": "Invalid pick"}, 400

    match_info  = get_match_info(match_id)
    sport       = match_info["sport"]
    kickoff_str = match_info["kickoff_utc"]

    options = SPORT_CONFIG.get(sport, {}).get("options", ["WIN", "LOSS"])
    if pick.upper() not in [o.upper() for o in options]:
        return {"success": False, "error": f"{pick} not valid for {sport}"}, 400

    if kickoff_str:
        try:
            kickoff = datetime.datetime.fromisoformat(kickoff_str)
            now     = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            if kickoff.tzinfo:
                kickoff = kickoff.replace(tzinfo=None)
            if now > kickoff:
                return {"success": False, "error": "Match has already started"}, 400
        except Exception as e:
            print(f"[Place Bet] Kickoff check error: {e}")

    try:
        sb     = get_client()
        result = sb.table("predictions") \
            .select("*") \
            .eq("user_phone", phone) \
            .eq("match_id", match_id) \
            .eq("status", "pending") \
            .limit(1) \
            .execute()

        rows = result.data or []
        if not rows:
            return {"success": False, "error": "No pending prediction found"}, 404

        pred_row       = rows[0]
        prediction_id  = pred_row["id"]
        correct_amount = int(pred_row.get("correct_amount") or 0)
        wrong_amount   = int(pred_row.get("wrong_amount") or 0)

        if not correct_amount:
            user = get_user_by_phone(phone)
            if user:
                amt = calculate_amounts(user, phone)
                correct_amount = amt["correct_amount"]
                wrong_amount   = amt["wrong_amount"]

        log_prediction(prediction_id, pick, correct_amount, wrong_amount)
        return {
            "success":        True,
            "pick":           pick,
            "match_id":       match_id,
            "correct_amount": correct_amount,
            "wrong_amount":   wrong_amount,
        }

    except Exception as e:
        print(f"[Place Bet] Error: {e}")
        return {"success": False, "error": str(e)}, 500

# ─────────────────────────────────────────────
# LEADERBOARD
# ─────────────────────────────────────────────

@app.route("/leaderboard", methods=["GET"])
def leaderboard():
    try:
        sb = get_client()

        users_result   = sb.table("users").select("*").eq("status", "active").execute()
        savings_result = sb.table("savings_log").select("*").execute()
        preds_result   = sb.table("predictions").select("*").execute()

        users   = users_result.data or []
        savings = savings_result.data or []
        preds   = preds_result.data or []

        today = datetime.date.today()
        days_since_friday = (today.weekday() - 4) % 7
        week_start = today - datetime.timedelta(days=days_since_friday)
        week_end   = week_start + datetime.timedelta(days=6)

        def get_week_start(d):
            days = (d.weekday() - 4) % 7
            return d - datetime.timedelta(days=days)

        leaderboard_data = []

        for user in users:
            phone = normalise_phone(str(user.get("phone_number", "")))

            user_savings = [
                s for s in savings
                if normalise_phone(str(s.get("user_phone", ""))) == phone
            ]

            week_saved = sum(
                float(s.get("amount", 0)) for s in user_savings
                if s.get("date") and
                week_start <= datetime.date.fromisoformat(str(s["date"])[:10]) <= week_end
            )

            total_saved = sum(float(s.get("amount", 0)) for s in user_savings)

            by_week = {}
            for s in user_savings:
                if not s.get("date"):
                    continue
                d  = datetime.date.fromisoformat(str(s["date"])[:10])
                wk = get_week_start(d).isoformat()
                by_week[wk] = by_week.get(wk, 0) + float(s.get("amount", 0))

            bankroll    = float(user.get("weekly_bankroll") or 50)
            goals_hit   = sum(1 for amt in by_week.values() if amt >= bankroll)
            goals_total = len(by_week)

            streak = 0
            for wk in sorted(by_week.keys(), reverse=True):
                if by_week[wk] >= bankroll:
                    streak += 1
                else:
                    break

            user_preds = [
                p for p in preds
                if normalise_phone(str(p.get("user_phone", ""))) == phone
                and p.get("prediction")
                and p.get("prediction", "").upper() not in ("", "N/A")
                and p.get("status") in ("locked", "insured")
            ]

            correct_bets = sum(
                1 for s in user_savings
                if s.get("trigger", "").endswith("_correct")
            )
            total_bets = len(user_preds)

            leaderboard_data.append({
                "name":         user.get("name", ""),
                "epl_team":     user.get("epl_team", ""),
                "mlb_team":     user.get("mlb_team", ""),
                "group_code":   user.get("group_code") or "",
                "saved_week":   round(week_saved, 2),
                "saved_total":  round(total_saved, 2),
                "goals_hit":    goals_hit,
                "goals_total":  goals_total,
                "streak":       streak,
                "correct_bets": correct_bets,
                "total_bets":   total_bets,
            })

        return jsonify({"success": True, "users": leaderboard_data})

    except Exception as e:
        print(f"[Leaderboard] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ─────────────────────────────────────────────
# USER LOOKUP (sign in)
# ─────────────────────────────────────────────

@app.route("/user", methods=["GET"])
def get_user():
    phone = normalise_phone(request.args.get("phone", ""))
    if not phone:
        return jsonify({"success": False, "error": "Missing phone"}), 400
    try:
        sb     = get_client()
        result = sb.table("users").select("*").eq("phone_number", phone).limit(1).execute()
        rows   = result.data or []
        if not rows:
            return jsonify({"success": False, "error": "User not found"}), 404
        u = rows[0]
        return jsonify({"success": True, "user": {
            "phone_number":          u.get("phone_number", ""),
            "name":                  u.get("name", ""),
            "epl_team":              u.get("epl_team", ""),
            "mlb_team":              u.get("mlb_team", ""),
            "weekly_bankroll":       u.get("weekly_bankroll", 50),
            "bets_per_week":         u.get("bets_per_week", 3),
            "weekly_cap_multiplier": u.get("weekly_cap_multiplier", 1.25),
            "group_code":            u.get("group_code", ""),
            "status":                u.get("status", "active"),
            "channel":               u.get("channel", "whatsapp"),
        }})
    except Exception as e:
        print(f"[User] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────
# OTP — VERIFY
# ─────────────────────────────────────────────

@app.route("/verify-otp", methods=["POST"])
def verify_otp():
    data  = request.get_json(force=True)
    phone = normalise_phone(str(data.get("phone", "")).strip())
    code  = str(data.get("code", "")).strip()

    if not phone or not code:
        return jsonify({"success": False, "error": "Missing phone or code"}), 400

    try:
        sb  = get_client()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        result = (
            sb.table("otp_codes")
            .select("*")
            .eq("phone_number", phone)
            .eq("code", code)
            .eq("used", False)
            .gte("expires_at", now)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return jsonify({"success": False, "error": "Invalid or expired code"}), 401

        sb.table("otp_codes").update({"used": True}).eq("id", rows[0]["id"]).execute()

        user_result = sb.table("users").select("phone_number, name, status").eq("phone_number", phone).limit(1).execute()
        user = (user_result.data or [{}])[0]

        return jsonify({
            "success": True,
            "user": {
                "phone_number": user.get("phone_number", phone),
                "name":         user.get("name", ""),
                "status":       user.get("status", ""),
            }
        })
    except Exception as e:
        print(f"[Verify OTP] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ─────────────────────────────────────────────
# PENDING BETS
# ─────────────────────────────────────────────

@app.route("/pending-bets", methods=["GET"])
def pending_bets():
    phone = normalise_phone(request.args.get("phone", ""))
    if not phone:
        return jsonify({"success": False, "error": "Missing phone"}), 400
    try:
        sb = get_client()

        preds_result = sb.table("predictions") \
            .select("*") \
            .eq("user_phone", phone) \
            .eq("status", "pending") \
            .execute()
        preds = preds_result.data or []

        if not preds:
            return jsonify({"success": True, "bets": []})

        match_ids = list({p["match_id"] for p in preds})
        matches_result = sb.table("pending_matches") \
            .select("*") \
            .in_("match_id", match_ids) \
            .execute()
        matches = {m["match_id"]: m for m in (matches_result.data or [])}

        bets = []
        for p in preds:
            m = matches.get(p["match_id"], {})
            bets.append({
                "match_id":       p["match_id"],
                "sport":          m.get("sport", "epl"),
                "team_name":      m.get("team_name", ""),
                "opponent":       m.get("opponent", ""),
                "prediction":     p.get("prediction", ""),
                "correct_amount": p.get("correct_amount", 0),
                "wrong_amount":   p.get("wrong_amount", 0),
                "kickoff_utc":    m.get("kickoff_utc", ""),
                "status":         p.get("status", "pending"),
            })

        return jsonify({"success": True, "bets": bets})
    except Exception as e:
        print(f"[Pending Bets] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ─────────────────────────────────────────────
# BET HISTORY
# ─────────────────────────────────────────────

@app.route("/bet-history", methods=["GET"])
def bet_history():
    phone = normalise_phone(request.args.get("phone", ""))
    if not phone:
        return jsonify({"success": False, "error": "Missing phone"}), 400
    try:
        sb = get_client()

        savings_result = sb.table("savings_log") \
            .select("*") \
            .eq("user_phone", phone) \
            .order("date", desc=True) \
            .execute()
        savings = savings_result.data or []

        preds_result = sb.table("predictions") \
            .select("match_id, prediction, correct_amount, wrong_amount") \
            .eq("user_phone", phone) \
            .execute()
        pred_map = {p["match_id"]: p for p in (preds_result.data or [])}

        match_ids = list({s["match_id"] for s in savings if s.get("match_id")})
        match_map = {}
        if match_ids:
            matches_result = sb.table("pending_matches") \
                .select("match_id, team_name, opponent") \
                .in_("match_id", match_ids) \
                .execute()
            match_map = {m["match_id"]: m for m in (matches_result.data or [])}

        bets = []
        for s in savings:
            match_id = s.get("match_id", "")
            pred     = pred_map.get(match_id, {})
            match    = match_map.get(match_id, {})
            trigger  = s.get("trigger", "")
            correct  = trigger.endswith("_correct")

            team     = match.get("team_name", "")
            opponent = match.get("opponent", "")
            label    = f"{team} vs {opponent}" if team and opponent else match_id

            bets.append({
                "date":       s.get("date", ""),
                "match":      label,
                "match_id":   match_id,
                "amount":     float(s.get("amount", 0)),
                "correct":    correct,
                "prediction": pred.get("prediction", ""),
                "week":       s.get("week", ""),
                "sport":      s.get("sport", ""),
                "trigger":    trigger,
            })

        return jsonify({"success": True, "bets": bets})
    except Exception as e:
        print(f"[Bet History] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ─────────────────────────────────────────────
# SAVINGS HISTORY (for graph)
# ─────────────────────────────────────────────

@app.route("/savings-history", methods=["GET"])
def savings_history():
    phone = normalise_phone(request.args.get("phone", ""))
    if not phone:
        return jsonify({"success": False, "error": "Missing phone"}), 400
    try:
        sb = get_client()

        result  = sb.table("savings_log") \
            .select("date, amount") \
            .eq("user_phone", phone) \
            .order("date") \
            .execute()
        rows = result.data or []

        def get_week_start(d):
            days = (d.weekday() - 4) % 7
            return d - datetime.timedelta(days=days)

        by_week = {}
        for r in rows:
            if not r.get("date"):
                continue
            d  = datetime.date.fromisoformat(str(r["date"])[:10])
            wk = get_week_start(d).isoformat()
            by_week[wk] = by_week.get(wk, 0) + float(r.get("amount", 0))

        sorted_weeks = sorted(by_week.keys())
        running = 0
        all_labels, all_values = [], []
        for wk in sorted_weeks:
            running += by_week[wk]
            all_labels.append(wk)
            all_values.append(round(running, 2))

        now = datetime.date.today()

        def filter_range(days):
            cutoff = now - datetime.timedelta(days=days)
            pairs  = [(l, v) for l, v in zip(all_labels, all_values)
                      if datetime.date.fromisoformat(l) >= cutoff]
            if not pairs:
                return {"labels": [], "values": []}
            ls, vs = zip(*pairs)
            return {"labels": list(ls), "values": list(vs)}

        return jsonify({
            "success": True,
            "history": {
                "1M":  filter_range(30),
                "3M":  filter_range(90),
                "6M":  filter_range(182),
                "ALL": {"labels": all_labels, "values": all_values},
            }
        })
    except Exception as e:
        print(f"[Savings History] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ─────────────────────────────────────────────
# UPDATE USER (profile save)
# ─────────────────────────────────────────────

@app.route("/update-user", methods=["POST"])
def update_user():
    data  = request.get_json(force=True)
    phone = normalise_phone(data.get("phone_number", "").strip())
    if not phone:
        return jsonify({"success": False, "error": "Missing phone"}), 400
    try:
        sb = get_client()
        updates = {
            "epl_team":              data.get("epl_team", ""),
            "mlb_team":              data.get("mlb_team", ""),
            "weekly_bankroll":       data.get("weekly_bankroll", 50),
            "bets_per_week":         data.get("bets_per_week", 3),
            "group_code":            data.get("group_code", ""),
            "weekly_cap_multiplier": data.get("weekly_cap_multiplier", 1.25),
        }
        if data.get("name") is not None:
            updates["name"] = data["name"]
        if data.get("channel") in ("sms", "whatsapp"):
            updates["channel"] = data["channel"]
        sb.table("users").update(updates).eq("phone_number", phone).execute()
        return jsonify({"success": True})
    except Exception as e:
        print(f"[Update User] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/update-phone", methods=["POST"])
def update_phone():
    data      = request.get_json(force=True)
    old_phone = normalise_phone(str(data.get("old_phone", "")).strip())
    new_phone = normalise_phone(str(data.get("new_phone", "")).strip())
    if not old_phone or not new_phone:
        return jsonify({"success": False, "error": "Missing old_phone or new_phone"}), 400
    if old_phone == new_phone:
        return jsonify({"success": False, "error": "New number matches existing number"}), 400
    try:
        sb = get_client()
        existing = sb.table("users").select("id").eq("phone_number", new_phone).execute()
        if existing.data:
            return jsonify({"success": False, "error": "That number is already registered"}), 409
        sb.table("users").update({"phone_number": new_phone}).eq("phone_number", old_phone).execute()
        for tbl in ("predictions", "savings_log", "insurance_offers", "sent_matches"):
            try:
                sb.table(tbl).update({"user_phone": new_phone}).eq("user_phone", old_phone).execute()
            except Exception:
                pass
        return jsonify({"success": True})
    except Exception as e:
        print(f"[Update Phone] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────
# PARLAY LEGS (stub — feature coming soon)
# ─────────────────────────────────────────────

@app.route("/parlay-legs", methods=["GET"])
def parlay_legs():
    return jsonify({"success": True, "legs": []})

# ─────────────────────────────────────────────
# KRUE DATA (stub — feature coming soon)
# ─────────────────────────────────────────────

@app.route("/krue-data", methods=["GET"])
def krue_data():
    group_code = request.args.get("group_code", "").strip().upper()
    if not group_code:
        return jsonify({"success": False, "error": "Missing group_code"}), 400
    try:
        sb = get_client()
        members_result = sb.table("users") \
            .select("name, epl_team, mlb_team, phone_number") \
            .eq("group_code", group_code) \
            .eq("status", "active") \
            .execute()
        members = members_result.data or []
        return jsonify({
            "success": True,
            "krue":    {"name": group_code},
            "members": [{"name": m.get("name",""), "epl_team": m.get("epl_team",""), "mlb_team": m.get("mlb_team",""), "accuracy": 0} for m in members],
            "matchup": None,
        })
    except Exception as e:
        print(f"[Krue Data] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ─────────────────────────────────────────────
# SIGNUP
# ─────────────────────────────────────────────

@app.route("/signup", methods=["POST"])
def signup():
    data        = request.get_json(force=True)
    phone       = normalise_phone(str(data.get("phone", "")).strip())
    sms_consent = data.get("sms_consent", False)
    channel     = data.get("channel", "whatsapp")

    if not phone:
        return jsonify({"success": False, "error": "Missing phone"}), 400

    if not sms_consent:
        return jsonify({"success": False, "error": "SMS consent is required to create an account"}), 400

    try:
        sb = get_client()
        existing = sb.table("users").select("id").eq("phone_number", phone).execute()
        if existing.data:
            return jsonify({"success": False, "error": "User already exists"}), 409

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        sb.table("users").insert({
            "name":                  data.get("name", ""),
            "phone_number":          phone,
            "status":                "active",
            "channel":               channel,
            "epl_team":              data.get("epl_team", ""),
            "mlb_team":              data.get("mlb_team", ""),
            "weekly_bankroll":       int(data.get("weekly_bankroll", 50)),
            "bets_per_week":         int(data.get("bets_per_week", 3)),
            "group_code":            data.get("group_code", ""),
            "weekly_cap_multiplier": 1.25,
            "sms_consent":           True,
            "sms_consent_at":        now,
            "sms_consent_method":    "signup_form",
        }).execute()

        name    = data.get("name", "there")
        welcome = random.choice(WELCOME_VARIANTS).format(name=name)
        try:
            send_message(phone, welcome, channel=channel)
        except Exception as e:
            print(f"[Signup] Welcome message failed: {e}")

        return jsonify({"success": True})
    except Exception as e:
        print(f"[Signup] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ─────────────────────────────────────────────
# 2FA — OTP
# ─────────────────────────────────────────────

@app.route("/send-otp", methods=["POST"])
def send_otp():
    data    = request.get_json(force=True)
    phone   = normalise_phone(str(data.get("phone", "")).strip())
    channel = data.get("channel", "whatsapp")
    if not phone:
        return jsonify({"success": False, "error": "Missing phone"}), 400

    code    = str(secrets.randbelow(900000) + 100000)
    expires = (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)
    ).isoformat()

    try:
        sb = get_client()
        sb.table("otp_codes").update({"used": True}).eq("phone_number", phone).eq("used", False).execute()
        sb.table("otp_codes").insert({
            "phone_number": phone,
            "code":         code,
            "expires_at":   expires,
            "used":         False,
        }).execute()

        body = (
            f"Your Akrue verification code is: {code}\n\n"
            f"Expires in 10 minutes. Do not share this with anyone."
        )
        send_message(phone, body, channel=channel)
        return jsonify({"success": True})
    except Exception as e:
        print(f"[OTP] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500



# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return {"status": "ok", "service": "akrue-webhook"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
