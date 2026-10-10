# Disc Diary — Spec

Letterboxd-style music logging app. Users search, rate, and diary albums (MusicBrainz **release groups**).

## Stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 |
| API / app | FastAPI |
| ORM | SQLAlchemy 2.x |
| Migrations | Alembic |
| Database | PostgreSQL (Docker) |
| UI | Jinja2 + HTMX (no JS framework) |
| Tests | pytest + httpx |

## Product rules

- Users log **release groups** (albums as a concept), never individual releases.
- Ratings are integers **1–10** (half-star scale: 1 = ★½, 10 = ★★★★★).
- Cover art from [Cover Art Archive](https://coverartarchive.org/); always handle missing art gracefully (placeholder / omit, never crash).

## MusicBrainz integration

- **External keys:** MusicBrainz IDs (MBIDs) on cached entities.
- **Cache:** Store `fetched_at` on every entity synced from MusicBrainz / Cover Art Archive.
- **Lookup order:** Check the local DB before calling the API; upsert when a response returns.
- **Rate limit:** Max **1 request per second**. All MusicBrainz (and related) HTTP calls go through **one client** with a shared rate limiter.
- **Resilience:** Retry with backoff on HTTP **503** and **429**.
- **User-Agent:** Required; format includes app name, version, and contact email. Value read from an environment variable (never hardcode secrets or contact details in source).
- **Tests:** Never hit the live API. Mock all remote responses.

## Architecture conventions

- Type hints everywhere.
- Prefer small, focused functions.
- **No business logic in route handlers** — handlers parse/validate input, call services, return responses/templates.
- All schema changes via **Alembic migrations** only (no ad-hoc `create_all` in production paths).
- Secrets and config (DB URL, MusicBrainz User-Agent, etc.) only via **environment variables**.

## Working rules (for contributors / agents)

1. Implement **one feature at a time**.
2. Explain any **non-trivial SQL**.
3. **Ask before adding dependencies**.
4. **Never commit** unless explicitly asked.
5. **Never run destructive database commands** (drop, truncate, reset, force-push migrations, etc.) without asking first.
