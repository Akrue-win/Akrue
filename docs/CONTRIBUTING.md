# Contributing to Akrue

## Discuss first

Before writing any code, open an Issue describing what you want to build or fix. This avoids duplicate work and ensures the idea fits the roadmap.

## Fork and branch

```bash
git clone https://github.com/YOUR_USERNAME/Akrue.git
cd Akrue

# Branch naming
git checkout -b feature/your-feature-name
git checkout -b fix/bug-description
```

## Make your changes

- One feature or fix per PR
- Follow existing code style (clear variable names, comments on non-obvious logic)
- Never hardcode credentials — always use environment variables
- Run `python src/nudge.py --test` to verify messaging still works

## Open a Pull Request

- Clear title: `Add NFL support` or `Fix duplicate match alert bug`
- Describe what changed and why
- Link to the Issue: `Closes #12`

## Ground rules

- No secrets in code — ever. Use `.env` locally, GitHub Secrets in CI.
- No breaking API changes without discussion — the frontend depends on stable endpoints.
- Keep it simple — don't over-engineer.
- Be constructive — feedback should help, not hurt.
