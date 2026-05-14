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
from google.oauth2.service_account import Credentials
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

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
# SPORT CONFIG — mirrors nudge.py
# Adding a new sport = add one entry here only.
# ─────────────────────────────────────────────

SPORT_CONFIG = {
    "epl": {
        "name":        "Premier League",
        "emoji":       "⚽",
        "allows_draw": True,
        "options":     ["WIN", "DRAW", "LOSS"],
    },
    "mlb": {
        "name":        "MLB",
        "emoji":       "⚾",
        "allows_draw": False,
        "options":     ["WIN", "LOSS"],
    },
    # "nba": {
    #     "name":        "NBA",
    #     "emoji":       "🏀",
    #     "allows_draw": False,
    #     "options":     ["WIN", "LOSS"],
    # },
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

# ─────────────────────────────────────────────
# GOOGLE SHEETS
# ─────────────────────────────────────────────

def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds      = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client     = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)

def find_active_match(user_phone: str):
    """
    Find the most recent pending prediction for this user.
    Returns (row_index, row_data, sport_key) or (None, None, None).
    """
    sheet = get_sheet().worksheet("Predictions")
    rows  = sheet.get_all_records()

    for i in range(len(rows) - 1, -1, -1):
        row = rows[i]
        if row["user_phone"] == user_phone and row["status"] == "pending":
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
    sheet.update_cell(row_index, 4, datetime.datetime.utcnow().isoformat())
    sheet.update_cell(row_index, 5, "locked")

# ─────────────────────────────────────────────
# WEBHOOK
# ─────────────────────────────────────────────

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    incoming_msg = request.values.get("Body", "").strip()
    user_phone   = request.values.get("From", "").strip()

    print(f"[Incoming] {user_phone}: {incoming_msg}")

    resp = MessagingResponse()
    msg  = resp.message()

    # Normalise input
    pick = PREDICTION_MAP.get(incoming_msg.lower())

    if not pick:
        msg.body(
            "Hey! 👋 Reply *WIN*, *DRAW*, or *LOSS* when you get a match "
            "prompt to lock in your bet.\n\n"
            "For MLB and NBA games reply *WIN* or *LOSS*.\n\n"
            "Sit tight — your next prompt arrives before the match!"
        )
        return str(resp)

    # Find active match and its sport
    row_index, row, sport_key = find_active_match(user_phone)

    if not row_index:
        msg.body(
            "I don't see an active match prompt for you right now. "
            "You'll get a message before your next match! ⚽"
        )
        return str(resp)

    # Look up sport config
    cfg = SPORT_CONFIG.get(sport_key, SPORT_CONFIG["epl"])

    # Validate prediction against sport's allowed options
    if pick == "draw" and not cfg["allows_draw"]:
        options_str = " or ".join(f"*{o}*" for o in cfg["options"])
        msg.body(
            f"{cfg['emoji']} {cfg['name']} doesn't have draws!\n\n"
            f"Reply {options_str} to lock in your bet."
        )
        return str(resp)

    # Log the prediction
    try:
        log_prediction(row_index, pick)
        msg.body(
            f"{cfg['emoji']} Locked in: *{pick.upper()}*!\n\n"
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
