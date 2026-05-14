"""
Akrue — Webhook Server
-----------------------
Receives WhatsApp replies from Twilio and logs predictions to Google Sheets.
Deploys to Railway. Always-on Flask app that listens for incoming messages.
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

SHEET_ID = os.environ["SHEET_ID"]

# The service account JSON is stored as a single environment variable
# (Railway lets you paste multi-line JSON into env vars)
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# ─────────────────────────────────────────────
# GOOGLE SHEETS HELPERS
# ─────────────────────────────────────────────

def get_sheet():
    """Authenticate and return the Sheet object."""
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)


def find_active_match(user_phone: str):
    """
    Find the most recent prediction prompt sent to this user that
    hasn't been answered yet (status=pending).
    """
    sheet = get_sheet().worksheet("Predictions")
    rows = sheet.get_all_records()

    # Look from newest to oldest for a pending entry for this user
    for i in range(len(rows) - 1, -1, -1):
        row = rows[i]
        if row["user_phone"] == user_phone and row["status"] == "pending":
            return i + 2, row  # +2 for header row + 1-indexing
    return None, None


def log_prediction(row_index: int, prediction: str):
    """Update the prediction and status for a pending row."""
    sheet = get_sheet().worksheet("Predictions")
    sheet.update_cell(row_index, 3, prediction)            # column C = prediction
    sheet.update_cell(row_index, 4, datetime.datetime.utcnow().isoformat())
    sheet.update_cell(row_index, 5, "locked")              # column E = status


# ─────────────────────────────────────────────
# WEBHOOK ENDPOINT
# ─────────────────────────────────────────────

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    """Twilio POSTs here whenever a user sends a WhatsApp message."""

    incoming_msg = request.values.get("Body", "").strip()
    user_phone   = request.values.get("From", "").strip()

    print(f"[Incoming] {user_phone}: {incoming_msg}")

    resp = MessagingResponse()
    msg  = resp.message()

    # Parse the prediction
    prediction_map = {
        "win":  "win",
        "draw": "draw",
        "loss": "loss",
        "w":    "win",
        "d":    "draw",
        "l":    "loss",
        "1":    "win",
        "2":    "draw",
        "3":    "loss",
    }

    pick = prediction_map.get(incoming_msg.lower())

    if not pick:
        msg.body(
            "Hey! 👋 Reply *WIN*, *DRAW*, or *LOSS* when you get a match "
            "prompt to lock in your bet.\n\n"
            "For MLB games reply *WIN* or *LOSS*.\n\n"
            "Sit tight — your next prompt arrives before kickoff!"
        )
        return str(resp)

    # Find the active match for this user
    row_index, row = find_active_match(user_phone)

    if not row_index:
        msg.body(
            "I don't see an active match prompt for you right now. "
            "Wait for the next pre-match notification! ⚽"
        )
        return str(resp)

    # Log the prediction
    try:
        log_prediction(row_index, pick)
        msg.body(
            f"✅ Locked in: *{pick.upper()}*!\n\n"
            f"I'll message you after the match with the result and your savings amount 💰"
        )
    except Exception as e:
        print(f"[Error] {e}")
        msg.body("⚠️ Something went wrong logging your pick. Try again in a moment!")

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
