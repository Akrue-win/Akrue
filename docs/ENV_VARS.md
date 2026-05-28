# Environment Variables

All credentials are loaded from environment variables. Copy `env.example` to `.env` for local development.

| Variable | Required | Service | Description | Where to get it |
|---|---|---|---|---|
| `TWILIO_ACCOUNT_SID` | Yes | nudge, webhook | Twilio account SID (34 chars, starts `AC`) | Twilio dashboard |
| `TWILIO_AUTH_TOKEN` | Yes | nudge, webhook | Twilio auth token (32 chars) | Twilio dashboard |
| `TWILIO_FROM_NUMBER` | Yes | nudge, webhook | WhatsApp sender number in `whatsapp:+1...` format | Twilio dashboard → Active Numbers |
| `TWILIO_SMS_FROM` | Optional | nudge, webhook | SMS sender number in `+1...` format — only needed if any user has `channel = "sms"` | Twilio dashboard → Active Numbers |
| `SUPABASE_URL` | Yes | nudge, webhook | Supabase project URL (`https://xxx.supabase.co`) | Supabase → Settings → API |
| `SUPABASE_SECRET_KEY` | Yes | nudge, webhook | Supabase service role key (full access) | Supabase → Settings → API → service_role |
| `FOOTBALL_API_KEY` | Yes | nudge, webhook | football-data.org API key | football-data.org dashboard |
| `PORT` | Auto | webhook | Injected by Railway — do not set manually | Railway injects at runtime |
| `TEST_MODE` | Optional | nudge only | Set to `1` to run in test mode (same as `--test` flag) | Set manually if needed |
