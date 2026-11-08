# Contributing

Thanks for helping improve the AI News Intelligence Telegram Bot!

## Development setup
```bash
python -m venv .venv
```
Activate it (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate` on Linux/macOS), then:
```bash
pip install -r requirements-dev.txt
```
```bash
python -m pytest -q
```
The test suite needs no internet and no API keys. Try the bot without keys:
```bash
python -m app.main --preview morning --test-mode
```

## Guidelines
- **Never commit secrets.** `.env` is git-ignored; use `.env.example` for new settings (with placeholders).
- **Keep the safety rules:** the bot must never give buy/sell advice, invent facts, or present analysis as fact.
- **Only legal sources:** public RSS feeds and official APIs. No paywall bypassing or scraping against a site's terms.
- Add tests for new behaviour; keep `python -m pytest -q` green.
- Keep changes focused; explain the "why" in your pull request.

## Reporting bugs
Open an issue with what you expected, what happened, and the relevant log lines from `logs/app.log`.
Remove any tokens or keys first (the bot masks them, but double-check).
