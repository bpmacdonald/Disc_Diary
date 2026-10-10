# Disc Diary

Letterboxd-style music logging app. Users search, rate, and diary albums via MusicBrainz release groups.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.x · Alembic · PostgreSQL · Jinja2 + HTMX

See [SPEC.md](SPEC.md) for product rules and conventions.

## Quick start

```bash
cp .env.example .env
docker compose up -d
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

App endpoints and models are not set up yet.
