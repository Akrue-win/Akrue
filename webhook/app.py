"""
Akrue — Webhook Server
-----------------------
Receives WhatsApp replies from Twilio and logs predictions to Google Sheets.
Sport-agnostic — reads sport from Pending_Matches tab and validates
predictions against that sport's allowed options.

Deploys to Railway. Always-on Flask app.
"""

import os
import json
import datetime
import gspread
import statsapi
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from google.oauth2.service_account import Credentials
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)
CORS(app)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

SHEET_ID          = os.environ["SHEET_ID"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]
FOOTBALL_API_KEY  = os.environ["FOOTBALL_API_KEY"]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

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
# GOOGLE SHEETS
# ─────────────────────────────────────────────

def open_sheet():
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds      = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client     = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)

def find_active_match(user_phone: str):
    sheet = open_sheet().worksheet("Predictions")
    rows  = sheet.get_all_records()
    print(f"[Lookup] Searching for: '{user_phone}'")
    for i in range(len(rows) - 1, -1, -1):
        row = rows[i]
        print(f"[Lookup] Row phone: '{row['user_phone']}' status: '{row['status']}'")
        if str(row["user_phone"]) == str(user_phone) and row["status"] == "pending":
            print(f"[Lookup] MATCH FOUND at row {i+2}")
            match_info = get_match_info(row["match_id"])
            return i + 2, row, match_info["sport"]
    return None, None, None

def get_match_info(match_id: str) -> dict:
    try:
        sheet = open_sheet().worksheet("Pending_Matches")
        rows  = sheet.get_all_records()
        for row in rows:
            if row.get("match_id") == match_id:
                return {
                    "sport":       row.get("sport", "epl"),
                    "kickoff_utc": row.get("kickoff_utc", ""),
                }
    except Exception as e:
        print(f"[Match info lookup] Error: {e}")
    return {"sport": "epl", "kickoff_utc": ""}

def get_user_by_phone(phone: str) -> dict:
    try:
        sheet = open_sheet().worksheet("Users")
        rows  = sheet.get_all_records()
        for row in rows:
            if normalise_phone(str(row.get("phone_number", ""))) == phone:
                return row
    except Exception as e:
        print(f"[User lookup] Error: {e}")
    return {}

def get_week_savings(user_phone: str) -> float:
    try:
        week_start, week_end = get_week_bounds()
        sheet   = open_sheet().worksheet("Savings_Log")
        records = sheet.get_all_records()
        total   = 0.0
        for r in records:
            if normalise_phone(str(r.get("user_phone", ""))) != user_phone:
                continue
            try:
                entry_date = datetime.date.fromisoformat(str(r.get("date", ""))[:10])
            except (ValueError, TypeError):
                continue
            if week_start <= entry_date <= week_end:
                try:
                    total += float(r.get("amount", 0))
                except (ValueError, TypeError):
                    pass
        return total
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

def log_prediction(row_index: int, prediction: str,
                   correct_amount: int = None, wrong_amount: int = None):
    sheet = open_sheet().worksheet("Predictions")
    sheet.update_cell(row_index, 3, prediction)
    sheet.update_cell(row_index, 4, datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat())
    if correct_amount is not None:
        sheet.update_cell(row_index, 7, correct_amount)
    if wrong_amount is not None:
        sheet.update_cell(row_index, 8, wrong_amount)

# ─────────────────────────────────────────────
# INSURANCE HELPERS
# ─────────────────────────────────────────────

def get_pending_insurance(user_phone: str) -> dict:
    """
    Returns the most recent unaccepted insurance offer for this user.
    Insurance_Offers columns: match_id | user_phone | amount | sent_at | Correct?
    """
    try:
        sheet   = open_sheet().worksheet("Insurance_Offers")
        records = sheet.get_all_records()
        for i, r in enumerate(reversed(records)):
            if (normalise_phone(str(r.get("user_phone", ""))) == user_phone
                    and r.get("Correct?", "") != "yes"):
                return {
                    "match_id": r.get("match_id"),
                    "amount":   int(r.get("amount", 0)),
                    "row":      len(records) - i + 1,
                }
    except Exception as e:
        print(f"[Insurance lookup] Error: {e}")
    return {}

def mark_insurance_accepted(row: int):
    """Marks column E (Correct?) as yes."""
    try:
        open_sheet().worksheet("Insurance_Offers").update_cell(row, 5, "yes")
    except Exception as e:
        print(f"[Insurance accept] Error: {e}")

def mark_prediction_insured(match_id: str, user_phone: str):
    """Sets the prediction status to 'insured' so post-match skips this user."""
    try:
        sheet   = open_sheet().worksheet("Predictions")
        records = sheet.get_all_records()
        for i, r in enumerate(reversed(records)):
            if (normalise_phone(str(r.get("user_phone", ""))) == user_phone
                    and r.get("match_id") == match_id):
                row_index = len(records) - i + 1
                sheet.update_cell(row_index, 5, "insured")
                print(f"[Insurance] Marked prediction insured for {user_phone} on {match_id}")
                return
    except Exception as e:
        print(f"[Insurance] Error marking insured: {e}")

def log_insurance_savings(user_phone: str, match_id: str, amount: int, sport: str):
    try:
        open_sheet().worksheet("Savings_Log").append_row([
            datetime.date.today().isoformat(),
            user_phone,
            amount,
            "insurance_buyout",
            match_id,
            current_week(),
            sport,
        ])
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
        game = statsapi.get('game', {'gamePk': game_id})
        linescore = game['liveData']['linescore']
        away = game['gameData']['teams']['away']['abbreviation']
        home = game['gameData']['teams']['home']['abbreviation']
        away_score = linescore['teams']['away'].get('runs', 0)
        home_score = linescore['teams']['home'].get('runs', 0)
        inning = linescore.get('currentInning', '')
        inning_half = linescore.get('inningHalf', '')
        status = game['gameData']['status']['abstractGameState']

        return jsonify({
            'status': status,
            'score': f"{away} {away_score} - {home_score} {home}",
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

        mark_insurance_accepted(offer["row"])
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
    row_index, row, sport_key = find_active_match(user_phone)
    if not row_index:
        return str(MessagingResponse())

    # ── Check if kickoff has already passed ──
    match_id    = row.get("match_id")
    match_info  = get_match_info(match_id)
    kickoff_str = match_info["kickoff_utc"]
    if kickoff_str:
        try:
            kickoff = datetime.datetime.fromisoformat(kickoff_str)
            now     = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
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

    # ── Use amounts stored in prediction row at reminder time ──
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
        log_prediction(row_index, pick, existing_correct, existing_wrong)
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
            if now > kickoff:
                return {"success": False, "error": "Match has already started"}, 400
        except Exception as e:
            print(f"[Place Bet] Kickoff check error: {e}")

    try:
        sheet     = open_sheet().worksheet("Predictions")
        records   = sheet.get_all_records()
        row_index = None
        pred_row  = None

        for i, r in enumerate(records):
            if (normalise_phone(str(r.get("user_phone", ""))) == phone
                    and r.get("match_id") == match_id
                    and r.get("status") == "pending"):
                row_index = i + 2
                pred_row  = r
                break

        if not row_index:
            return {"success": False, "error": "No pending prediction found"}, 404

        correct_amount = int(pred_row.get("correct_amount") or 0)
        wrong_amount   = int(pred_row.get("wrong_amount") or 0)

        # Fallback: recalculate if amounts are missing
        if not correct_amount:
            user = get_user_by_phone(phone)
            if user:
                amt = calculate_amounts(user, phone)
                correct_amount = amt["correct_amount"]
                wrong_amount   = amt["wrong_amount"]

        log_prediction(row_index, pick, correct_amount, wrong_amount)
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
# HEALTH CHECK
# ─────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return {"status": "ok", "service": "akrue-webhook"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
