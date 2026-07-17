# 🌌 MeLi Cosmos v2.0

> Minimalist Django personal blog — pure Markdown, Tailwind CSS, zero template bloat.

## Quick Start

```bash
# Install dependencies
uv sync

# Run migrations
uv run python manage.py migrate

# Seed sample data
uv run python manage.py seed_data

# Start dev server
uv run python manage.py runserver
```

Then visit http://127.0.0.1:8000

Admin panel: http://127.0.0.1:8000/admin/
- Username: `debris`
- Password: `admin`

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Django 6.x (Python 3.14+) |
| Frontend CSS | Tailwind CSS v4 (CDN) |
| Markdown | Python-Markdown + Pygments |
| Database | SQLite (dev) / PostgreSQL (prod target) |
| Package Manager | uv |

## Project Structure

```
my_cosmos/          # Django project config
├── settings.py     # Project settings
├── urls.py         # Root URL routing
├── wsgi.py         # WSGI entry point
└── asgi.py         # ASGI entry point

blog/               # Django app
├── models.py       # Category, Tag, Post, Memo
├── views.py        # IndexView, PostDetailView
├── urls.py         # App URL routing
├── admin.py        # Admin configuration
└── management/     # Custom commands

templates/blog/     # Django templates (Tailwind styled)
├── base.html       # Base layout with dark theme
├── index.html      # 3-column home page
├── post_detail.html # Single-column article page
└── includes/       # Reusable components
```

## Features

- **Pure Markdown** content ingestion — write in Obsidian, sync via UUID
- **Dark mode** canvas (`#050a14`) with cyan-400 cyber accents
- **Session-protected view counting** — anti F5 bloating
- **Server-side Markdown rendering** with syntax highlighting
- **Admin panel** with full CRUD and search
- **Nginx/X-Forwarded-For** aware for production deployment
