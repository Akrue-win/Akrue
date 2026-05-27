"""
Twilio WhatsApp Content Template SIDs.

Populated after each template is created via the Content API and approved by Meta.
Until a template is approved, the corresponding send_message() call falls back to
free-form body (which works in the sandbox and for SMS).

Creation command (run once per template):
  curl -X POST 'https://content.twilio.com/v1/Content' \
    -H 'Content-Type: application/json' \
    -u $TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN \
    -d '<JSON body>'

Approval submission (run after creation, using returned sid):
  curl -X POST 'https://content.twilio.com/v1/Content/{sid}/ApprovalRequests/whatsapp' \
    -H 'Content-Type: application/json' \
    -u $TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN \
    -d '{"name": "<friendly_name>", "category": "UTILITY"}'
"""

TEMPLATES: dict[str, str] = {
    # Authentication
    "otp":                  "",   # HXxxx — akrue_otp

    # Onboarding
    "welcome_a":            "",   # HXxxx — akrue_welcome_a
    "welcome_b":            "",   # HXxxx — akrue_welcome_b

    # Pre-match prompts — EPL
    "pre_match_epl_a":      "",   # HXxxx — akrue_pre_match_epl_a
    "pre_match_epl_b":      "",   # HXxxx — akrue_pre_match_epl_b
    "pre_match_epl_c":      "",   # HXxxx — akrue_pre_match_epl_c

    # Pre-match prompts — MLB
    "pre_match_mlb_a":      "",   # HXxxx — akrue_pre_match_mlb_a
    "pre_match_mlb_b":      "",   # HXxxx — akrue_pre_match_mlb_b
    "pre_match_mlb_c":      "",   # HXxxx — akrue_pre_match_mlb_c

    # Reminders
    "reminder_a":           "",   # HXxxx — akrue_reminder_a
    "reminder_b":           "",   # HXxxx — akrue_reminder_b
    "reminder_c":           "",   # HXxxx — akrue_reminder_c

    # Results — EPL
    "result_epl_win_a":     "",
    "result_epl_win_b":     "",
    "result_epl_win_c":     "",
    "result_epl_loss_a":    "",
    "result_epl_loss_b":    "",
    "result_epl_loss_c":    "",
    "result_epl_draw_a":    "",
    "result_epl_draw_b":    "",

    # Results — MLB
    "result_mlb_win_a":     "",
    "result_mlb_win_b":     "",
    "result_mlb_win_c":     "",
    "result_mlb_loss_a":    "",
    "result_mlb_loss_b":    "",
    "result_mlb_loss_c":    "",

    # Insurance
    "insurance_epl_a":      "",
    "insurance_epl_b":      "",
    "insurance_epl_c":      "",
    "insurance_mlb_a":      "",
    "insurance_mlb_b":      "",
    "insurance_mlb_c":      "",

    # Alerts
    "postponed":            "",   # HXxxx — akrue_postponed

    # Query responses (only needed for WhatsApp; SMS uses free-form)
    "query_goal":           "",
    "query_balance":        "",
    "query_streak":         "",
    "query_rank":           "",
}
