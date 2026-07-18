# MeLi Cosmos v3.0

> Minimalist multi-user Django blog — pure Markdown, Tailwind CSS, zero framework bloat.

## Quick Start

```bash
uv sync                                    # Install dependencies
uv run python manage.py migrate            # Apply migrations
uv run python manage.py seed_data          # Seed sample data (creates admin user debris/admin)
uv run python manage.py runserver          # Start dev server → http://127.0.0.1:8000
```

Admin panel: http://127.0.0.1:8000/admin/ (username: `debris`, password: `admin`)

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Django 6.x (Python 3.14+) |
| Frontend CSS | Tailwind CSS v4 (CDN, no build step) |
| Markdown | Python-Markdown + Pygments |
| Database | SQLite (dev) / PostgreSQL (production) |
| WSGI | Gunicorn + Nginx + Cloudflare |
| Package Manager | uv |

## Project Structure

```
my_cosmos/              # Django project config
├── settings.py
├── urls.py
└── wsgi.py / asgi.py

blog/                   # Django app
├── models.py           # Post, Memo, Category, Tag, Series, UserProfile, InviteCode, ViewLog
├── views.py            # CBVs + AJAX endpoints
├── urls.py
├── admin.py
├── forms.py
├── context_processors.py
├── feeds.py            # RSS/Atom
└── management/         # Custom commands (seed_data, cleanup_view_logs, etc.)

templates/blog/         # Django templates (Tailwind styled)
├── base.html           # Root layout with dark theme
├── index.html          # 3-column grid with sidebar
├── post_detail.html    # Single-column reading view
├── landing.html        # Landing page
├── about.html          # About page with server status
└── includes/           # Nav, footer, memo banner, user layout
```

## Features

- **Multi-user** platform with invite-code registration
- **Pure Markdown** content — write in Obsidian, sync via UUID (`Post.unique_id`)
- **Dark mode** canvas (`#050a14`) with cyan-400 accents, 3-state theme toggle (light/dark/system)
- **Browser-fingerprint + IP view counting** with configurable cooldown (anti-incognito abuse)
- **Server-side Markdown rendering** with syntax highlighting and collapsible TOC
- **Series** support with prev/next navigation
- **Memo** (microblogging) with per-user feeds
- **RSS feeds** per user and global
- **Image upload** with automatic WebP conversion, deduplication, and stripping
- **Avatar** crop/upload with DiceBear fallback
- **Responsive** 3-column layout with mobile hamburger menu
- **Nginx/X-Forwarded-For** aware for production

## Configuration

| Env / Setting | Default | Description |
|---|---|---|
| `DJANGO_DEBUG` | `True` | Debug mode |
| `DJANGO_SECRET_KEY` | (built-in dev key) | Secret key |
| `DJANGO_ALLOWED_HOSTS` | `*` | Allowed hosts (comma-separated) |
| `DB_ENGINE` | `django.db.backends.sqlite3` | Database engine |
| `DB_NAME` | `db.sqlite3` | Database name / path |
| `VIEW_LOG_COOLDOWN_HOURS` | `1` | Hours before same fingerprint+IP re-counts |
| `VIEW_LOG_RETENTION_DAYS` | `90` | Days to keep view log entries |
| `SERVER_DISPLAY_NAME` | (hostname) | Custom server label on about page |
| `INVITE_CODE_EXPIRE_HOURS` | `24` | Invite code TTL |
| `INVITE_DAILY_LIMIT` | `1` | Max unused invite codes per user per day |

## Management Commands

```bash
uv run python manage.py seed_data              # Seed sample data
uv run python manage.py cleanup_view_logs       # Purge old view log entries
uv run python manage.py test                    # Run tests
```
