Architecture & Design Decisions
This document records key technical decisions made during development of Project Free Kick, and the reasoning behind them.
---
Why WhatsApp (not SMS)?
SMS in the US requires A2P 10DLC registration for local numbers, and toll-free verification for toll-free numbers. Both take days and require business registration. Twilio's WhatsApp sandbox works immediately for up to 5 pilot users with no registration. When we scale beyond the sandbox, we'll evaluate registering a WhatsApp Business number.
Why GitHub Actions (not a server)?
For Phase 1, a persistent server is overkill and costs money. GitHub Actions gives us free scheduled jobs. The limitation is that Actions can't receive inbound webhooks — this is why we need Railway for catching WhatsApp replies.
Why Railway for the webhook?
Railway has a free-ish tier, simple Python/Flask deployment, and persistent uptime. The webhook only needs to do one thing: receive a WhatsApp reply ("1", "2", or "3"), look up the user, store the prediction, and acknowledge. It's a tiny service.
Why Google Sheets for Phase 1 data?
A full database (Postgres, Supabase) is the right call for Phase 2 when we have a web app. For Phase 1 with 2-5 users, Google Sheets is free, human-readable, shareable, and requires no infrastructure. The nudge script can read/write it via the Sheets API.
Multi-user design
Users are stored as a comma-separated list in the `USER_PHONE_NUMBERS` environment variable. The nudge script broadcasts to all users. Predictions are stored per-user per-match in the log file. When we add the web app, we'll move to a proper user table.
Saving amount randomisation
Amounts are randomised within a range per trigger type (e.g. $20–$40 for a correct win prediction). This adds a game-like element — you don't know exactly how much you'll save until the nudge arrives. Ranges are configurable via environment variables.
Why Liverpool only (for now)?
Keeps the football API usage minimal (free tier). The architecture is built to support any team ID from football-data.org — adding a new team is a one-line config change. Multi-team support comes in Phase 2 when users can select their team via the web app.
---
Future decisions to make
Database: Supabase vs PlanetScale vs Postgres on Railway
Web framework: Next.js vs Remix vs plain HTML
Auth: Supabase Auth vs Clerk vs NextAuth
Mobile app: React Native vs Flutter vs PWA
Bank integration: Plaid vs Finicity vs manual
