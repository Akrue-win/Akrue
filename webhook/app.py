"""
Akrue — Webhook Server
-----------------------
Receives WhatsApp replies from Twilio and logs predictions to Supabase.
Sport-agnostic — reads sport from pending_matches table and validates
predictions against that sport's allowed options.

Deploys to Railway. Always-on Flask app.
"""

import os
import datetime
import requests
import statsapi
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)
CORS(app)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

SUPABASE_URL        = os.environ["SUPABASE_URL"]
SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]
FOOTBALL_API_KEY    = os.environ["FOOTBALL_API_KEY"]

# ─────────────────────────────────────────────
# SPORT CONFIG
# ─────────────────────────────────────────────

SPORT_ALLOWS_DRAW = {
    "epl": True,
    "mlb": False,
}

SPORT_OPTIONS = {
    "epl": ["WIN", "DRAW", "LOSS"],
    "mlb": ["WIN", "LOSS"],
}

SPORT_EMOJI = {
    "epl": "⚽",
    "mlb": "⚾",
}

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

DEFAULT_CAP_MULTIPLIER = 1.25
MAX_CAP_MULTIPLIER     = 2.0
CAP_WARNING_THRESHOLD  = 0.75

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

def normalise_prediction(raw: str) -> str | None:
    cleaned = raw.strip().lower()
    if cleaned in PREDICTION_MAP:
        return PREDICTION_MAP[cleaned]
    for keyword in ("win", "draw", "loss"):
        if cleaned.endswith(keyword):
            return keyword
    return None

def get_week_bounds():
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

def get_week_savings(user_phone: str) -> float:
    try:
        week_start, week_end = get_week_bounds()
        sb     = get_client()
        result = sb.table("savings_log") \
            .select("amount") \
            .eq("user_phone", user_phone) \
            .gte("date", week_start.isoformat()) \
            .lte("date", week_end.isoformat()) \
            .execute()
        return sum(float(r.get("amount", 0)) for r in (result.data or []))
    except Exception as e:
        print(f"[Savings] Error fetching week savings for {user_phone}: {e}")
        return 0.0

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

# ─────────────────────────────────────────────
# INSURANCE HELPERS
# ─────────────────────────────────────────────

def get_pending_insurance(user_phone: str) -> dict:
    """
    Returns the most recent unaccepted insurance offer for this user.
    """
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
# WEBHOOK
# ─────────────────────────────────────────────

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    incoming_msg = request.values.get("Body", "").strip()
    user_phone   = normalise_phone(request.values.get("From", "").strip())
    print(f"[Incoming] {user_phone}: {incoming_msg}")
    resp = MessagingResponse()
    msg  = resp.message()

    # ── INSURANCE reply ──
    if incoming_msg.lower().strip() in INSURANCE_TRIGGERS:
        offer = get_pending_insurance(user_phone)
        if not offer:
            msg.body(
                "No active insurance offer for you right now. "
                "Watch for one mid-match if your pick is looking shaky! 👀"
            )
            return str(resp)

        match_info = get_match_info(offer["match_id"])
        sport      = match_info["sport"]
        emoji      = SPORT_EMOJI.get(sport, "⚽")

        mark_insurance_accepted(offer["id"])
        mark_prediction_insured(offer["match_id"], user_phone)
        log_insurance_savings(user_phone, offer["match_id"], offer["amount"], sport)
        msg.body(
            f"{emoji} Insurance locked in! "
            f"*${offer['amount']}* saved and your bet is closed. 💰\n\n"
            f"Smart move — bankroll protected!"
        )
        return str(resp)

    # ── Normalise input ──
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
                emoji = SPORT_EMOJI.get(sport_key, "⚽")
                msg.body(
                    f"{emoji} The game has already started — "
                    f"your bet is locked in, no changes allowed!"
                )
                return str(resp)
        except Exception as e:
            print(f"[Kickoff check] Error: {e}")

    # ── Look up sport config ──
    allows_draw = SPORT_ALLOWS_DRAW.get(sport_key, True)
    options     = SPORT_OPTIONS.get(sport_key, ["WIN", "DRAW", "LOSS"])
    emoji       = SPORT_EMOJI.get(sport_key, "⚽")

    # ── Validate prediction ──
    if pick == "draw" and not allows_draw:
        options_str = " or ".join(f"*{o}*" for o in options)
        msg.body(
            f"{emoji} That sport doesn't have draws!\n\n"
            f"Reply {options_str} to lock in your bet."
        )
        return str(resp)

    # ── Use amounts stored in prediction row ──
    existing_correct = int(row.get("correct_amount") or 0)
    existing_wrong   = int(row.get("wrong_amount") or 0)

    # Fallback: recalculate if amounts are missing
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
            f"Correct pick → save *${existing_correct}* 💰\n"
            f"Wrong pick → you still save *${existing_wrong}* 💰\n\n"
            f"I'll message you after the match with your result!"
        )
    except Exception as e:
        print(f"[Error] {e}")
        msg.body("⚠️ Something went wrong logging your pick. Please try again!")

    return str(resp)

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

    options = SPORT_OPTIONS.get(sport, ["WIN", "LOSS"])
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
            "phone_number":        u.get("phone_number", ""),
            "name":                u.get("name", ""),
            "epl_team":            u.get("epl_team", ""),
            "mlb_team":            u.get("mlb_team", ""),
            "weekly_bankroll":     u.get("weekly_bankroll", 50),
            "bets_per_week":       u.get("bets_per_week", 3),
            "weekly_cap_multiplier": u.get("weekly_cap_multiplier", 1.25),
            "group_code":          u.get("group_code", ""),
            "status":              u.get("status", "active"),
        }})
    except Exception as e:
        print(f"[User] Error: {e}")
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
        sb.table("users").update({
            "epl_team":              data.get("epl_team", ""),
            "mlb_team":              data.get("mlb_team", ""),
            "weekly_bankroll":       data.get("weekly_bankroll", 50),
            "bets_per_week":         data.get("bets_per_week", 3),
            "group_code":            data.get("group_code", ""),
            "weekly_cap_multiplier": data.get("weekly_cap_multiplier", 1.25),
        }).eq("phone_number", phone).execute()
        return jsonify({"success": True})
    except Exception as e:
        print(f"[Update User] Error: {e}")
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
# HEALTH CHECK
# ─────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return {"status": "ok", "service": "akrue-webhook"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
