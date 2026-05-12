Contributing to Project Free Kick
Thanks for wanting to help build Free Kick! Here's everything you need to know.
---
How to contribute
1. Discuss first
Before writing any code, open an Issue to describe what you want to build or fix. This avoids duplicate work and makes sure the idea fits the roadmap.
2. Fork and branch
```bash
# Fork the repo on GitHub, then clone your fork
git clone https://github.com/YOUR_USERNAME/project-free-kick.git
cd project-free-kick

# Create a branch named for what you're working on
git checkout -b feature/stock-price-trigger
git checkout -b fix/duplicate-match-alerts
```
3. Make your changes
Keep changes focused — one feature or fix per PR
Follow the existing code style (clear variable names, comments on anything non-obvious)
Never hardcode credentials — always use environment variables
4. Test your changes
```bash
# Run in test mode to verify messaging works
python src/nudge.py --test

# Run any tests
python -m pytest tests/
```
5. Open a Pull Request
Give your PR a clear title: `Add stock price trigger` or `Fix duplicate match alert bug`
Describe what you changed and why
Link to the Issue it resolves: `Closes #12`
---
Project structure
```
src/
├── nudge.py          # Main script — entry point
├── triggers/         # Individual trigger modules (matches, stocks, news)
├── messaging/        # WhatsApp send/receive logic
└── api/              # External API wrappers (football, stocks, news)

docs/                 # Architecture decisions, setup guides
tests/                # Test scripts
```
---
Ground rules
No secrets in code — ever. Use `.env` locally, GitHub Secrets in CI.
No breaking changes without discussion — existing users depend on the current flow.
Keep it simple — we're building for scalability but not over-engineering Phase 1.
Be kind — this is a collaborative project, feedback should be constructive.
---
Ideas we'd love help with
Check the Issues tab for open tasks. Good first issues are tagged `good first issue`.
Current priorities:
Railway webhook for catching WhatsApp replies
Web app (Next.js) for user settings + leaderboard
Multi-team support (all 20 EPL teams)
Stock price trigger module
Google Sheets savings logger
---
Questions?
Open a Discussion or drop a comment on any Issue.
