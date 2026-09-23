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
| Markdown | Python-Markdown + Pygments + nh3 sanitizer |
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
├── models.py           # Post, Memo, Category, Tag, Series, UserProfile, InviteCode, ViewLog, Like, Comment, BanAppeal
├── views.py            # CBVs + AJAX endpoints
├── urls.py
├── admin.py
├── forms.py
├── middleware.py        # BanCheckMiddleware
├── context_processors.py
├── feeds.py            # RSS/Atom
└── management/         # Custom commands (seed_data, cleanup_view_logs, etc.)

blog/api/               # REST API (django-ninja, mounted at /api/v1/)
├── app.py              # NinjaAPI instance, exception handler, router mount table
├── auth.py             # the three Bearer auth tiers
├── common.py           # pagination / visibility / form validation / ref resolution
├── errors.py           # single error envelope
├── schema.py           # request & response models
├── serializers.py      # model → dict
└── routers/            # endpoint groups by domain

dashboard/              # Staff-only management dashboard
├── views.py
└── urls.py

templates/blog/         # Django templates (Tailwind styled)
├── base.html           # Root layout with dark theme + external link modal
├── index.html          # 3-column grid with sidebar
├── post_detail.html    # Single-column reading view
├── landing.html        # Landing page
├── about.html          # About page with server status, donation QR
├── terms.html          # Terms of service
├── privacy.html        # Privacy policy
├── memo_detail.html    # Memo detail with likes/comments
├── ban_appeal.html     # Ban appeal form
└── includes/           # Nav, footer, comments, like JS, user layout
```

## Features

- **Multi-user** platform with invite-code registration
- **Like & Comment** system — AJAX likes, form-based comments, "A, B and X others" display
- **Pure Markdown** content — write in Obsidian, sync via UUID (`Post.unique_id`), sanitized with nh3
- **CC License** selection per post (7 options: CC BY/BY-SA/BY-NC/BY-NC-SA/BY-ND/BY-NC-ND/CC0)
- **Dark mode** canvas (`#050a14`) with cyan-400 accents, 3-state theme toggle (light/dark/system)
- **Browser-fingerprint + IP view counting** with configurable cooldown (anti-incognito abuse)
- **Server-side Markdown rendering** with syntax highlighting, collapsible TOC, nh3 XSS sanitization
- **Semantic related posts** — weighted scoring (category + tag overlap)
- **Series** support with prev/next navigation
- **Memo** (microblogging) with per-user feeds, likes and comments
- **Drafts** box — private draft list per user
- **RSS feeds** per user and global
- **Image upload** with automatic WebP conversion, deduplication, EXIF stripping, 10MB client-side check
- **Avatar** crop/upload with DiceBear fallback
- **Responsive** 3-column layout with mobile hamburger menu
- **Nginx/X-Forwarded-For** aware for production
- **Ban system** — admin ban with invite-chain recursion, permanent ban flag, one-time appeal per ban
- **Staff dashboard** (`/dashboard/`) — site stats, server status, pending appeals, user invite tree
- **External link safety** — custom modal with Google Safe Browsing API integration
- **Terms of Service** + **Privacy Policy** pages
- **Donation** popup with WeChat/Alipay QR codes
- **Custom select** styling with themed chevron and focus ring

## REST API (`/api/v1`)

Built with django-ninja. Interactive docs at **`/api/v1/docs`** (Swagger UI), machine-readable schema at **`/api/v1/openapi.json`**, and a committed snapshot at `docs/openapi.json` (regenerate with `manage.py export_openapi`; a test fails if the snapshot drifts from the code).

**Auth**: personal access tokens (`mlc_` + 40 hex) sent as `Authorization: Bearer …`. The plaintext appears once at issue time; only a SHA-256 hash is stored, and revocation is a soft `revoked_at` flag. Four tiers: anonymous (`/health`, `/meta`, login, register), `OptionalBearerAuth` (all reads plus guest comments — an author token also reveals that author's drafts and private items), `BearerAuth` (content writes), `SessionOrBearerAuth` (`/auth/me`, token management, `/account/*`, `/inbox`, `/uploads`; browser sessions additionally pass a CSRF check).

**Coverage** (49 operations): site 2 · posts 6 · memos 4 · taxonomy 9 · discovery 5 · comments & likes 7 · inbox 3 · auth 6 · account 4 · uploads 3.

**Errors** share one shape: `{"error": {"code": "not_found", "message": "…"}}`, plus `details: {field: [msg]}` on validation failures and a `Retry-After` header when throttled. Lists return `{count, page, page_size, total_pages, results}` with `page_size` capped at 100.

**No second rulebook**: markdown rendering, like wording, comment moderation, rate limits, guest approval, honeypot, WebP conversion with MD5 dedup, the invite daily quota and the view-count cooldown all come from `blog/views.py`, so a mobile client can never be more privileged than the browser.

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
| `GOOGLE_SAFE_BROWSING_KEY` | (empty) | Google Safe Browsing API key for external link checking |

## Management Commands

```bash
uv run python manage.py seed_data              # Seed sample data
uv run python manage.py cleanup_view_logs       # Purge old view log entries
uv run python manage.py test                    # Run tests
uv run python manage.py export_openapi         # Rewrite the docs/openapi.json snapshot
```

## License

Code: MIT. User content: CC license of author's choice (default CC BY-NC 4.0).
