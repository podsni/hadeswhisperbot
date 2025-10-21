# Repository Guidelines

## Project Structure & Module Organization
- Core bot entrypoints live in `app/main.py` (polling) and `app/webhook.py` (webhooks). Configure env loading via `app/config.py`.
- Telegram logic is split into `app/handlers/` (commands, history, media), reusable middleware in `app/middlewares/`, and provider clients in `app/services/` (Groq, Deepgram, Together, Telethon, queue, caching).
- Shared assets such as the SQLite store (`transcriptions.db`) and `.env` configuration sit at the repository root; requirements files define the default and optimized dependency sets.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` — Create and activate the local virtual environment (use `.venv\Scripts\activate` on Windows).
- `pip install -r requirements.txt` — Install the runtime dependencies; use `requirements-optimized.txt` only when deploying the high-throughput flavor.
- `python -m app.main` — Start the polling bot locally. Pass `TELEGRAM_*` and provider keys through `.env`.
- `python -m app.webhook` — Launch the webhook server; pair with your preferred reverse proxy or tunneling tool when testing externally.

## Coding Style & Naming Conventions
- Target Python 3.9+ with 4-space indentation, snake_case for modules/functions, UpperCamelCase for classes, and ALL_CAPS for configuration constants.
- Keep handlers small and delegate provider or business logic to `app/services/`. Prefer dependency injection through function parameters instead of module-level globals.
- Type-hint public functions and return structured dicts or dataclasses; logging should use the existing `rich` console helpers for consistent output.

## Testing Guidelines
- Adopt `pytest` for new coverage. Mirror module layout under `tests/` (e.g., `tests/services/test_transcription.py`) and name tests `test_<scenario>`.
- Use lightweight fixtures that mock external APIs—avoid real network calls. Seed the local SQLite DB with temporary tables and drop them in `teardown`.
- Run `pytest -q` before opening a pull request and ensure new behavior is verified through unit or integration tests when touching handlers or services.

## Commit & Pull Request Guidelines
- Follow the existing history: concise, imperative commits (`Add v2.1 features`, `Enable persistent Telethon session`). Scope each change narrowly and reference issues with `Refs #id` when applicable.
- PRs should include a brief summary, configuration or migration notes, screenshots of Telegram flows when UI changes occur, and a checklist confirming tests, lint, and env updates.

## Security & Configuration Tips
- Never commit `.env`, API keys, or generated SQLite files; rely on `.env.example` for new secrets.
- Rotate provider tokens regularly and document any new required variables in both `.env.example` and `README.md`.
