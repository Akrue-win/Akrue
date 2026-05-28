# Messaging

## Twilio 24-hour session window (WhatsApp)

WhatsApp only allows free-form outbound messages within 24 hours of the user's last inbound message. Outside that window, you must use an approved Message Template (HSM). Akrue does not use templates — all messages are plain body text. This means:

- Pre-match prompts sent more than 24h after the user's last message will fail with error 63016
- In practice this is rare because match prompts are triggered by user-relevant events
- If session window issues become common, register an HSM template for nudges

## Dual-channel: WhatsApp vs SMS

Each user has a `channel` field in the `users` table (`'whatsapp'` or `'sms'`). The `send_message()` function in `akrue/messaging.py` reads this at send time:

- `'whatsapp'` → sends to `whatsapp:+{phone}` from `TWILIO_FROM_NUMBER`
- `'sms'` → sends to `+{phone}` from `TWILIO_SMS_FROM`

`TWILIO_SMS_FROM` is optional — only needed if any user has `channel = 'sms'`.

## A2P 10DLC (SMS)

For bulk SMS in the US, carriers require A2P 10DLC registration. This is already registered for Akrue's SMS sender number. If SMS volume grows significantly, a dedicated short code or toll-free number may be needed.

## Inbound message handling

The webhook (`webhook/app.py`) handles inbound messages from both `/whatsapp` and `/sms` routes via the shared `handle_inbound()` function. Supported commands:

| Message | Action |
|---|---|
| WIN / DRAW / LOSS (and variants) | Record prediction |
| INSURE | Accept pending insurance offer |
| GOAL / PROGRESS | Show weekly savings progress |
| BALANCE / TOTAL | Show all-time savings total |
| STREAK | Show current and best prediction streak |
| RANK / LEADERBOARD | Show top 3 and user's rank for the week |
| HELP | Show command list |
| STOP | Opt out (legal requirement, must be processed first) |
| START | Re-subscribe |
