from twilio.rest import Client as TwilioClient
from akrue.env import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WA_FROM, TWILIO_SMS_FROM
from akrue.supabase_client import get_client

def normalise_phone(phone: str) -> str:
    return phone.replace("whatsapp:", "").replace("+", "").strip()

def get_user_channel(phone: str) -> str:
    try:
        sb     = get_client()
        result = sb.table("users").select("channel").eq("phone_number", phone).limit(1).execute()
        rows   = result.data or []
        if rows:
            return rows[0].get("channel") or "whatsapp"
    except Exception:
        pass
    return "whatsapp"

def send_message(phone: str, body: str, channel: str = None):
    if channel is None:
        channel = get_user_channel(phone)
    client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    if channel == "sms":
        to  = f"+{phone}"
        frm = TWILIO_SMS_FROM
    else:
        to  = f"whatsapp:+{phone}"
        frm = TWILIO_WA_FROM
    msg = client.messages.create(body=body, from_=frm, to=to)
    print(f"[{channel.upper()} -> {phone}] SID: {msg.sid}")
