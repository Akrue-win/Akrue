# Code Map

## Repository root
| Path | Purpose |
|---|---|
| `src/nudge.py` | Cron job: pre-match prompts, post-match settlement, insurance, reminders |
| `webhook/app.py` | Always-on Flask server: Twilio inbound, web frontend API |
| `webhook/Dockerfile` | Builds webhook service from repo root (includes akrue/ package) |
| `akrue/config.py` | SPORT_CONFIG, SPORT_TEAM_IDS, cap constants |
| `akrue/env.py` | Centralised environment variable reads |
| `akrue/messaging.py` | send_message, normalise_phone, get_user_channel |
| `akrue/amounts.py` | calculate_amounts, get_week_savings, get_week_bounds, current_week |
| `akrue/supabase_client.py` | get_client() factory |
| `pyproject.toml` | Python package declaration for akrue/ |
| `requirements.txt` | Root-level deps (nudge + shared) |
| `webhook/requirements.txt` | Webhook-specific deps |
| `env.example` | Template for .env file |

## Web frontend
| Path | Purpose |
|---|---|
| `index.html` | Landing page / sign-in |
| `web/app.html` | Main user dashboard |
| `web/signup.html` | New user registration |
| `web/leaderboard.html` | Group leaderboard |
| `web/config.js` | Frontend API base URL config |

## Docs
| Path | Purpose |
|---|---|
| `docs/SETUP.md` | Local dev setup |
| `docs/ARCHITECTURE.md` | System design overview |
| `docs/DEPLOY.md` | Railway, GitHub Pages, Actions deployment |
| `docs/ENV_VARS.md` | All environment variable reference |
| `docs/SCHEMA.md` | Supabase table definitions |
| `docs/WEBHOOK_API.md` | All HTTP endpoints |
| `docs/SPORT_ADAPTERS.md` | How to add a new sport |
| `docs/MESSAGING.md` | Twilio / WhatsApp / SMS notes |
| `docs/CONTRIBUTING.md` | Contribution guide |
| `CLAUDE.md` | Claude Code project instructions |
