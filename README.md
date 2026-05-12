# ⚽ Project Free Kick

> A prediction-driven savings platform for sports fans. Make your pick before kickoff, save more when you're right.

---

## What is Project Free Kick?

Project Free Kick is a gamified savings app that lets sports fans make predictions on upcoming matches and save money based on their predictions. Get a WhatsApp notification before kickoff, pick your result, and save more if you called it right.

**Core loop:**
1. 30 minutes before kickoff → WhatsApp prompt sent to all users
2. Pick your prediction: Win / Draw / Loss
3. After the match → result logged, savings amount calculated
4. Correct prediction = save more. Wrong prediction = save base amount
5. Weekly leaderboard tracks who's saving the most

---

## Project Status

🚧 **Phase 1 — In Development**

- [x] WhatsApp messaging via Twilio
- [x] Liverpool FC match triggers
- [x] Scheduled savings nudges
- [x] GitHub Actions automation
- [ ] Pre-match prediction system
- [ ] User management
- [ ] Web app (settings + leaderboard)
- [ ] Multi-team EPL support
- [ ] Railway webhook for reply handling

---

## Repository Structure

```
project-free-kick/
├── src/
│   ├── triggers/        # Match detection, scheduled nudges
│   ├── messaging/       # WhatsApp send/receive logic
│   └── api/             # Football data, future stock/news APIs
├── docs/                # Architecture, setup guides, decisions
├── tests/               # Test scripts
└── .github/
    ├── workflows/       # GitHub Actions (nudge scheduler)
    └── ISSUE_TEMPLATE/  # Bug reports, feature requests
```

---

## Getting Started (Contributors)

### Prerequisites
- Python 3.12+
- A Twilio account (WhatsApp sandbox)
- A football-data.org API key
- GitHub account

### Setup

1. **Clone the repo**
   ```bash
   git clone https://github.com/YOUR_USERNAME/project-free-kick.git
   cd project-free-kick
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set environment variables**
   
   Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   cp .env.example .env
   ```

4. **Run in test mode**
   ```bash
   python src/nudge.py --test
   ```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `TWILIO_ACCOUNT_SID` | Your Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | Your Twilio Auth Token |
| `TWILIO_FROM_NUMBER` | Your Twilio WhatsApp number (`whatsapp:+1...`) |
| `FOOTBALL_API_KEY` | football-data.org API key |

> ⚠️ **Never commit credentials to the repo.** Always use environment variables or GitHub Secrets.

---

## Contributing

We welcome contributions! Please read [CONTRIBUTING.md](docs/CONTRIBUTING.md) before submitting a pull request.

**Quick guide:**
- 🐛 Found a bug? [Open a bug report](.github/ISSUE_TEMPLATE/bug_report.md)
- 💡 Have an idea? [Open a feature request](.github/ISSUE_TEMPLATE/feature_request.md)
- 🔧 Want to contribute code? Fork the repo, make your changes, open a Pull Request

---

## Roadmap

### Phase 1 — Core Prediction Engine *(current)*
- Pre-match WhatsApp predictions
- Post-match result checking + savings logging
- Multi-user support via Google Sheets

### Phase 2 — Web App
- User signup + settings page
- EPL team selection (all 20 teams)
- Weekly savings goal configuration
- Leaderboard

### Phase 3 — Scale
- Native iOS + Android app
- Additional sports (NFL, NBA)
- Stock price + news event triggers
- Bank account integration (Plaid)
- Group savings pools

---

## License

MIT License — see [LICENSE](LICENSE)

---

*Built with ⚽ and 💰*
