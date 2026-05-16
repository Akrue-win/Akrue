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
# SPORT CONFIG — minimal version
# Only what the webhook actually needs.
# Full config lives in nudge.py.
# Adding a new sport = add one entry here only.
# ─────────────────────────────────────────────

SPORT_ALLOWS_DRAW = {
    "epl": True,
    "mlb": False,
    # "nba": False,
    # "nfl": False,
}

SPORT_OPTIONS = {
    "epl": ["WIN", "DRAW", "LOSS"],
    "mlb": ["WIN", "LOSS"],
    # "nba": ["WIN", "LOSS"],
    # "nfl": ["WIN", "LOSS"],
}

SPORT_EMOJI = {
    "epl": "⚽",
    "mlb": "⚾",
    # "nba": "🏀",
    # "nfl": "🏈",
}

# Raw input → normalised prediction value
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

DOUBLE_DOWN_TRIGGERS = {"dd", "doubledown", "double down", "double-down"}

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
    """Look up sport for a match_id from the Pending_Matches tab."""
    try:
        sheet = get_sheet().worksheet("Pending_Matches")
        rows  = sheet.get_all_records()
        for row in rows:
            if row.get("match_id") == match_id:
                return row.get("sport", "epl")
    except Exception as e:
        print(f"[Sport lookup] Error: {e}")
    return "epl"  # safe default — EPL allows all options

def log_prediction(row_index: int, prediction: str):
    """Write prediction and lock the row."""
    sheet = get_sheet().worksheet("Predictions")
    sheet.update_cell(row_index, 3, prediction)
    sheet.update_cell(row_index, 4, datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat())
    sheet.update_cell(row_index, 5, "locked")

def get_pending_double_down(user_phone: str) -> dict:
    """
    Find the most recent double down offer sent to this user
    that hasn't been accepted yet.
    Returns dict with match_id, direction, amount or empty dict.
    """
    try:
        sheet   = get_sheet().worksheet("Double_Down_Sent")
        records = sheet.get_all_records()
        for r in reversed(records):
            if r.get("user_phone") == user_phone and r.get("accepted") != "yes":
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
    """Mark a double down as accepted in Double_Down_Sent tab."""
    try:
        get_sheet().worksheet("Double_Down_Sent").update_cell(row, 6, "yes")
    except Exception as e:
        print(f"[DD accept] Error: {e}")

def log_double_down_savings(user_phone: str, match_id: str,
                            amount: int, sport: str):
    """Log the double down savings to Savings_Log."""
    try:
        from datetime import date
        week = f"{date.today().isocalendar().year}-W{date.today().isocalendar().week:02d}"
        get_sheet().worksheet("Savings_Log").append_row([
            date.today().isoformat(), user_phone, amount,
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
    user_phone = normalise_phone(request.values.get("From", "").strip())

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

    # Unrecognised input — stay silent.
    if not pick:
        return str(MessagingResponse())

    # ── Find active match ──
    row_index, row, sport_key = find_active_match(user_phone)

    # No active prediction for this user — stay silent.
    if not row_index:
        return str(MessagingResponse())

    # ── Look up sport config ──
    allows_draw = SPORT_ALLOWS_DRAW.get(sport_key, True)
    options     = SPORT_OPTIONS.get(sport_key, ["WIN", "DRAW", "LOSS"])
    emoji       = SPORT_EMOJI.get(sport_key, "⚽")

    # ── Validate prediction against sport's allowed options ──
    if pick == "draw" and not allows_draw:
        options_str = " or ".join(f"*{o}*" for o in options)
        msg.body(
            f"{emoji} That sport doesn't have draws!\n\n"
            f"Reply {options_str} to lock in your bet."
        )
        return str(resp)

    # ── Log the prediction ──
    try:
        log_prediction(row_index, pick)
        msg.body(
            f"{emoji} Locked in: *{pick.upper()}*!\n\n"
            f"I'll message you after the match with your result and savings amount 💰"
        )
    except Exception as e:
        print(f"[Error] {e}")
        msg.body("⚠️ Something went wrong logging your pick. Please try again!")

    return str(resp)


@app.route("/place-bet", methods=["POST"])
def place_bet():
    data     = request.get_json(force=True)
    phone    = data.get("phone", "").strip()
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
    try:
        sheet     = get_sheet().worksheet("Predictions")
        records   = sheet.get_all_records()
        row_index = None
        for i, r in enumerate(records):
            if (normalise_phone(str(r.get("user_phone", ""))) == normalise_phone(phone)
                    and r.get("match_id") == match_id
                    and r.get("status") == "pending"):
                row_index = i + 2
                break
        if not row_index:
            return {"success": False, "error": "No pending prediction found"}, 404
        log_prediction(row_index, pick)
        return {"success": True, "pick": pick, "match_id": match_id}
    except Exception as e:
        print(f"[Place Bet] Error: {e}")
        return {"success": False, "error": str(e)}, 500
    # ── Normalise input ──
    pick = normalise_prediction(incoming_msg)

    # Unrecognised input — stay silent.
    # Prevents spam bots draining Twilio credits.
    if not pick:
        return str(MessagingResponse())

    # ── Find active match ──
    row_index, row, sport_key = find_active_match(user_phone)

    # No active prediction for this user — stay silent.
    if not row_index:
        return str(MessagingResponse())

    # ── Look up sport config ──
    allows_draw = SPORT_ALLOWS_DRAW.get(sport_key, True)
    options     = SPORT_OPTIONS.get(sport_key, ["WIN", "DRAW", "LOSS"])
    emoji       = SPORT_EMOJI.get(sport_key, "⚽")

    # ── Validate prediction against sport's allowed options ──
    if pick == "draw" and not allows_draw:
        options_str = " or ".join(f"*{o}*" for o in options)
        msg.body(
            f"{emoji} That sport doesn't have draws!\n\n"
            f"Reply {options_str} to lock in your bet."
        )
        return str(resp)

    # ── Log the prediction ──
    try:
        log_prediction(row_index, pick)
        msg.body(
            f"{emoji} Locked in: *{pick.upper()}*!\n\n"
            f"I'll message you after the match with your result and savings amount 💰"
        )
    except Exception as e:
        print(f"[Error] {e}")
        msg.body("⚠️ Something went wrong logging your pick. Please try again!")

    return str(resp)

# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return {"status": "ok", "service": "akrue-webhook"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
