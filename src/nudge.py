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
from flask import Flask, request
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

DOUBLE_DOWN_TRIGGERS = {"dd", "doubledown", "double down", "double-down"}

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
    """Returns (week_start, week_end) for the current Fri–Thu savings week."""
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

def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds      = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client     = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)

def find_active_match(user_phone: str):
    sheet = get_sheet().worksheet("Predictions")
    rows  = sheet.get_all_records()
    print(f"[Lookup] Searching for: '{user_phone}'")
    for i in range(len(rows) - 1, -1, -1):
        row = rows[i]
        print(f"[Lookup] Row phone: '{row['user_phone']}' status: '{row['status']}'")
        if str(row["user_phone"]) == str(user_phone) and row["status"] == "pending":
            print(f"[Lookup] MATCH FOUND at row {i+2}")
            sport = get_match_sport(row["match_id"])
            return i + 2, row, sport
    return None, None, None

def get_match_sport(match_id: str) -> str:
    try:
        sheet = get_sheet().worksheet("Pending_Matches")
        rows  = sheet.get_all_records()
        for row in rows:
            if row.get("match_id") == match_id:
                return row.get("sport", "epl")
    except Exception as e:
        print(f"[Sport lookup] Error: {e}")
    return "epl"

def get_match_kickoff(match_id: str) -> str:
    try:
        sheet = get_sheet().worksheet("Pending_Matches")
        rows  = sheet.get_all_records()
        for row in rows:
            if row.get("match_id") == match_id:
                return row.get("kickoff_utc", "")
    except Exception as e:
        print(f"[Kickoff lookup] Error: {e}")
    return ""

def get_user_by_phone(phone: str) -> dict:
    try:
        sheet = get_sheet().worksheet("Users")
        rows  = sheet.get_all_records()
        for row in rows:
            if normalise_phone(str(row.get("phone_number", ""))) == phone:
                return row
    except Exception as e:
        print(f"[User lookup] Error: {e}")
    return {}

def get_week_savings(user_phone: str) -> float:
    """Sums all Savings_Log entries for this user in the current Fri–Thu week."""
    try:
        week_start, week_end = get_week_bounds()
        sheet   = get_sheet().worksheet("Savings_Log")
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
    """
    Returns correct_amount, wrong_amount, and cap metadata for a user.
    Reads weekly_bankroll, bets_per_week, weekly_cap_multiplier from user row.
    """
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
    sheet = get_sheet().worksheet("Predictions")
    sheet.update_cell(row_index, 3, prediction)
    sheet.update_cell(row_index, 4, datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat())
    if correct_amount is not None:
        sheet.update_cell(row_index, 7, correct_amount)
    if wrong_amount is not None:
        sheet.update_cell(row_index, 8, wrong_amount)

def get_pending_double_down(user_phone: str) -> dict:
    try:
        sheet   = get_sheet().worksheet("Double_Down_Sent")
        records = sheet.get_all_records()
        for r in reversed(records):
            if normalise_phone(str(r.get("user_phone", ""))) == user_phone and r.get("accepted") != "yes":
                return {
                    "match_id":  r.get("match_id"),
                    "direction": r.get("direction"),
                    "amount":    int(r.get("amount", 0)),
                    "row":       records.index(r) + 2,
                }
    except Exception as e:
        print(f"[DD lookup] Error: {e}")
    return {}

def mark_double_down_accepted(row: int):
    try:
        get_sheet().worksheet("Double_Down_Sent").update_cell(row, 6, "yes")
    except Exception as e:
        print(f"[DD accept] Error: {e}")

def log_double_down_savings(user_phone: str, match_id: str,
                            amount: int, sport: str):
    try:
        week = current_week()
        get_sheet().worksheet("Savings_Log").append_row([
            datetime.date.today().isoformat(), user_phone, amount,
            "double_down", match_id, week, sport,
        ])
    except Exception as e:
        print(f"[DD savings log] Error: {e}")

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

    # ── DOUBLE DOWN reply ──
    if incoming_msg.lower().strip() in DOUBLE_DOWN_TRIGGERS:
        dd = get_pending_double_down(user_phone)
        if not dd:
            msg.body(
                "No active double down offer for you right now. "
                "Watch for the next one mid-match! 👀"
            )
            return str(resp)
        mark_double_down_accepted(dd["row"])
        sport    = get_match_sport(dd["match_id"])
        dd_emoji = SPORT_EMOJI.get(sport, "⚽")
        log_double_down_savings(user_phone, dd["match_id"], dd["amount"], sport)
        msg.body(
            f"{dd_emoji} Double down locked in! "
            f"Extra *${dd['amount']}* added to your savings if it holds. 💰\n\n"
            f"Good instincts — now sit tight!"
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
    kickoff_str = get_match_kickoff(match_id)
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

    # ── Fetch amounts already stored in the prediction row ──
    # Amounts were written by nudge.py at reminder time.
    # Use them as-is — no recalculation needed.
    existing_correct = row.get("correct_amount", 0)
    existing_wrong   = row.get("wrong_amount", 0)

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

    sport   = get_match_sport(match_id)
    options = SPORT_OPTIONS.get(sport, ["WIN", "LOSS"])
    if pick.upper() not in [o.upper() for o in options]:
        return {"success": False, "error": f"{pick} not valid for {sport}"}, 400

    # ── Check kickoff ──
    kickoff_str = get_match_kickoff(match_id)
    if kickoff_str:
        try:
            kickoff = datetime.datetime.fromisoformat(kickoff_str)
            now     = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            if now > kickoff:
                return {"success": False, "error": "Match has already started"}, 400
        except Exception as e:
            print(f"[Place Bet] Kickoff check error: {e}")

    try:
        sheet     = get_sheet().worksheet("Predictions")
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

        # Use amounts already stored in the row (written at reminder time by nudge.py)
        correct_amount = int(pred_row.get("correct_amount") or 0)
        wrong_amount   = int(pred_row.get("wrong_amount") or 0)

        # If amounts are missing (edge case), recalculate from user record
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
