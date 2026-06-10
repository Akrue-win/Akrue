"""
Parlay settlement logic: settle locked parlays after all matches finish.
Called from nudge.py post-match settlement.
"""

import datetime
from typing import List, Dict, Optional
from akrue.supabase_client import get_client
from akrue.messaging import send_message
from src.worldcup_adapter import WorldCupAdapter
from akrue.config import SPORT_CONFIG
from akrue.amounts import current_week


def settle_parlay_picks(sport: str = "worldcup"):
    """
    Settle all locked parlays for a given sport.
    1. Get all users with locked parlays
    2. Check their predictions against final results
    3. Calculate bonuses (20% of bankroll if 10/10 correct, capped at $20)
    4. Send WhatsApp alert with results
    """
    print(f"\n[Parlay] Settling {sport} parlays...")

    sb = get_client()

    # Get all locked parlays for this sport
    locked_parlays = sb.table("parlay_picks") \
        .select("*") \
        .eq("sport", sport) \
        .eq("parlay_locked", True) \
        .eq("settled", False) \
        .execute()

    if not locked_parlays.data:
        print(f"[Parlay] No locked parlays to settle.")
        return

    for parlay in locked_parlays.data:
        user_phone = parlay["user_phone"]
        parlay_id = parlay["id"]
        picks_locked = parlay["picks_locked"]

        print(f"[Parlay] Settling parlay {parlay_id} for {user_phone}...")

        # Get user's predictions
        predictions = sb.table("predictions") \
            .select("match_id, prediction") \
            .eq("user_phone", user_phone) \
            .eq("sport", sport) \
            .eq("status", "locked") \
            .execute()

        if not predictions.data:
            print(f"  ✗ No predictions found for {user_phone}")
            continue

        predictions_map = {p["match_id"]: p["prediction"] for p in predictions.data}

        # Score the parlay
        picks_correct = 0
        results = []

        for match_id, user_prediction in predictions_map.items():
            # Get match result
            match_result = get_match_result(sport, match_id)

            if match_result is None:
                print(f"  ⚠ Could not get result for {match_id}, skipping...")
                continue

            is_correct = match_result
            if is_correct:
                picks_correct += 1
                results.append({"match_id": match_id, "correct": True})
            else:
                results.append({"match_id": match_id, "correct": False})

        # Calculate bonus
        bonus = 0.0
        if picks_correct == picks_locked:
            # User got all picks correct
            user = sb.table("users") \
                .select("weekly_bankroll") \
                .eq("phone_number", user_phone) \
                .single() \
                .execute()

            if user.data:
                bankroll = float(user.data.get("weekly_bankroll", 50))
                bonus = min(bankroll * 0.2, 20.0)  # 20% of bankroll, capped at $20

        # Log parlay result
        week = current_week()
        sb.table("parlay_results").insert({
            "user_phone": user_phone,
            "sport": sport,
            "week_id": week,
            "parlay_id": parlay_id,
            "picks_locked": picks_locked,
            "picks_correct": picks_correct,
            "bonus_earned": bonus,
            "settled": True,
            "status": "won" if picks_correct == picks_locked else "lost",
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        }).execute()

        # Update parlay_picks as settled
        sb.table("parlay_picks") \
            .update({"settled": True}) \
            .eq("id", parlay_id) \
            .execute()

        # Send WhatsApp alert
        send_parlay_settlement_alert(user_phone, picks_correct, picks_locked, bonus, sport)

        print(f"  ✓ {user_phone}: {picks_correct}/{picks_locked} correct, bonus: ${bonus:.2f}")


def get_match_result(sport: str, match_id: str) -> Optional[bool]:
    """
    Get whether user's prediction is correct.
    Returns True if correct, False if incorrect, None if match not finished.
    """
    if sport == "worldcup":
        # Extract the API ID from match_id (worldcup_12345 -> 12345)
        api_id = match_id.replace("worldcup_", "")

        adapter = WorldCupAdapter()
        match = adapter.get_match_details(api_id)

        if not match:
            return None

        # Get user's prediction from DB
        sb = get_client()
        pred = sb.table("predictions") \
            .select("prediction") \
            .eq("match_id", match_id) \
            .single() \
            .execute()

        if not pred.data:
            return None

        user_prediction = pred.data["prediction"]
        home_score = match["home_score"]
        away_score = match["away_score"]

        return WorldCupAdapter.get_result(home_score, away_score, user_prediction)

    # Add other sports here as needed
    return None


def send_parlay_settlement_alert(user_phone: str, picks_correct: int, picks_locked: int,
                                 bonus: float, sport: str):
    """Send post-settlement WhatsApp message with results."""
    emoji = "🎉" if picks_correct == picks_locked else "📊"
    sport_emoji = SPORT_CONFIG.get(sport, {}).get("emoji", "⚽")

    if picks_correct == picks_locked:
        # Perfect 10!
        message = (
            f"{emoji} PERFECT {picks_correct}!\n\n"
            f"You went {picks_correct}/{picks_locked} this week to save a total of ${bonus:.2f}.\n\n"
            f"See your results at akrue.win"
        )
    else:
        message = (
            f"{emoji} That's a wrap!\n\n"
            f"You went {picks_correct}/{picks_locked} this week to save a total of ${bonus:.2f}.\n\n"
            f"See your results at akrue.win"
        )

    send_message(user_phone, message)
