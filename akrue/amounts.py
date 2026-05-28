import datetime
from akrue.config import DEFAULT_CAP_MULTIPLIER, MAX_CAP_MULTIPLIER, CAP_WARNING_THRESHOLD

def get_week_bounds() -> tuple:
    today = datetime.date.today()
    days_since_friday = (today.weekday() - 4) % 7
    week_start = today - datetime.timedelta(days=days_since_friday)
    week_end   = week_start + datetime.timedelta(days=6)
    return week_start, week_end

def current_week() -> str:
    week_start, _ = get_week_bounds()
    iso = week_start.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"

def get_week_savings(user_phone: str) -> float:
    from akrue.supabase_client import get_client
    from akrue.messaging import normalise_phone
    try:
        week_start, week_end = get_week_bounds()
        sb     = get_client()
        result = sb.table("savings_log") \
            .select("amount") \
            .eq("user_phone", normalise_phone(user_phone)) \
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
