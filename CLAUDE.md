# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                          # Install dependencies
uv run python manage.py runserver  # Start dev server (http://127.0.0.1:8000)
uv run python manage.py migrate    # Apply migrations
uv run python manage.py seed_data  # Seed sample data (creates admin user debris/admin)
uv run python manage.py test       # Run tests
```

## Architecture

This is a single-app Django personal blog (MeLi Cosmos v2.0). The Django project is `my_cosmos/` and the sole app is `blog/`.

**Models** (`blog/models.py`): `Category`, `Tag`, `Post`, `Memo`. `Post.unique_id` is a UUID field that serves as the ingestion anchor for syncing with local Obsidian notes — always use `unique_id` (not auto-increment PK) for idempotent operations, especially in sync/API endpoints. `Post.status` has three states: `draft`, `published`, `private`. `Memo` is a microblogging/short-update component; only the latest public memo is shown on the index.

**Views** (`blog/views.py`): Class-Based Views. `IndexView` fetches the latest 10 published posts + the latest public memo. `PostDetailView` supports lookup by either `slug` or `pk`, renders Markdown server-side via Python-Markdown + Pygments (extensions configured in `settings.MARKDOWN_EXTENSIONS`), and uses session-based view counting (`post_viewed_{pk}` session key) to prevent F5 view inflation.

**Templates**: Tailwind CSS v4 loaded from CDN (no build step). The admin panel is at `/admin/`.

## UI Constraints

- **Never create standalone `.css` files.** All styling goes into Tailwind utility classes in HTML templates.
- **Dark theme tokens**: canvas `#050a14`, cards `slate-950/60`, borders `slate-900`, text `slate-300`, headings `slate-100`, accent `cyan-400`.
- **No heavy JS frameworks.** Keep interactivity to vanilla JS or Django template tags (`{% if %}`).
- Layout: index uses a 3-column responsive grid (`lg:col-span-2` + `lg:col-span-1` sidebar). Post detail is single-column focused reading.

## Configuration

- Language: `zh-hans`, timezone: `Asia/Shanghai`
- Nginx-aware: `SECURE_PROXY_SSL_HEADER`, `USE_X_FORWARDED_HOST`, and `USE_X_FORWARDED_PORT` are enabled — respect `X-Forwarded-For` for client IP logic in production.
- Database defaults to SQLite; PostgreSQL is the production target (configurable via `DB_ENGINE` / `DB_NAME` env vars).
