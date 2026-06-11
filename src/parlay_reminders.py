"""
Parlay pre-lock reminders: prompt users to lock their parlays before first match.
Called from cron (daily or as needed).
"""

import datetime
from akrue.supabase_client import get_client
from akrue.messaging import send_message


def send_parlay_lock_reminders(sport: str = "worldcup"):
    """
    Send pre-lock reminders to users who haven't locked their parlay yet.
    Only sends to users with channel='whatsapp'.
    """
    print(f"\n[Parlay Reminders] Checking for users to remind...")

    sb = get_client()

    # Get earliest match for this sport
    matches_result = sb.table("pending_matches") \
        .select("kickoff_utc") \
        .eq("sport", sport) \
        .order("kickoff_utc", desc=False) \
        .limit(1) \
        .execute()

    if not matches_result.data:
        print("[Parlay Reminders] No upcoming matches found.")
        return

    earliest_kickoff = matches_result.data[0]["kickoff_utc"]
    kickoff_time = datetime.datetime.fromisoformat(earliest_kickoff.replace("Z", "+00:00"))
    now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)

    # Only send reminder if first match is in the future
    if now >= kickoff_time:
        print("[Parlay Reminders] First match has already started.")
        return

    # Get all users with active WhatsApp channel who haven't locked parlays
    users_result = sb.table("users") \
        .select("phone_number, name") \
        .eq("status", "active") \
        .eq("channel", "whatsapp") \
        .execute()

    if not users_result.data:
        print("[Parlay Reminders] No active users with WhatsApp.")
        return

    users_to_remind = []

    for user in users_result.data:
        phone = user["phone_number"]

        # Check if user already has a locked parlay
        locked_result = sb.table("parlay_picks") \
            .select("id") \
            .eq("user_phone", phone) \
            .eq("sport", sport) \
            .eq("parlay_locked", True) \
            .limit(1) \
            .execute()

        if not locked_result.data:
            users_to_remind.append(phone)

    if not users_to_remind:
        print("[Parlay Reminders] All users have locked parlays.")
        return

    # Format kickoff time
    kickoff_str = kickoff_time.strftime("%I:%M %p %Z")

    # Send reminders
    for phone in users_to_remind:
        message = (
            f"🌍 It's time for parlay picks! "
            f"Go 10 for 10 and save extra this week!\n\n"
            f"Lock in before {kickoff_str} in the app!\n\n"
            f"akrue.win/parlay"
        )

        try:
            send_message(phone, message)
            print(f"  ✓ Reminder sent to {phone}")
        except Exception as e:
            print(f"  ✗ Failed to send reminder to {phone}: {e}")

    print(f"[Parlay Reminders] Sent {len(users_to_remind)} reminders.")
