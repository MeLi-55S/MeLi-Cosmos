# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                                         # Install dependencies
uv run python manage.py runserver               # Start dev server (http://127.0.0.1:8000)
uv run python manage.py migrate                 # Apply migrations
uv run python manage.py seed_data               # Seed sample data (creates admin user debris/admin)
uv run python manage.py test                    # Run tests (86 tests)
./tailwindcss-cli -i static/css/tailwind-input.css -o static/css/tailwind.min.css --minify  # Rebuild CSS
uv run python manage.py collectstatic --noinput  # Collect static files
```

## Architecture

Read `ARCHITECTURE.md` (gitignored, local reference) for the full architecture document — models, URLs, security measures, deployment, etc. Update it when the architecture changes.

This is a single-app Django personal blog (MeLi Cosmos v3.0). The Django project is `my_cosmos/` and the main app is `blog/`. A secondary `dashboard/` app provides staff-only management tools.

**Models** (`blog/models.py`): `Post`, `Memo`, `Category`, `Tag`, `Series`, `UserProfile`, `InviteCode`, `ViewLog`, `Like`, `Comment`, `BanAppeal`, `UploadedImage`. `Like` and `Comment` use GenericForeignKey to target both `Post` and `Memo`.

**Views** (`blog/views.py`): CBVs + function views (~1300 lines). Markdown rendered via `_render_markdown()` which runs python-markdown then nh3 sanitization. All `next` redirects validated by `url_has_allowed_host_and_scheme`.

**Templates**: Tailwind CSS v4 **pre-compiled** to `static/css/tailwind.min.css` (93KB). No browser CDN, no build step. Input file: `static/css/tailwind-input.css`. Dark theme via class strategy (`.dark` on `<html>`). Admin panel at `/admin/`.

## UI Constraints

- **Never create standalone `.css` files.** All styling goes into Tailwind utility classes in HTML templates. The only CSS files are `tailwind.min.css` (compiled) and `fonts/fonts.css`.
- **Dark theme tokens**: canvas `#050a14`, cards `slate-950/60`, borders `slate-700`, text `slate-300`, headings `slate-100`, accent `cyan-400`.
- **No heavy JS frameworks.** Keep interactivity to vanilla JS or Django template tags.
- Layout: index uses a 3-column responsive grid. Post detail is single-column focused reading.
- Select/dropdown styling is in the Tailwind input CSS (`@layer base`).

## Configuration

- Language: `zh-hans`, timezone: `Asia/Shanghai`
- Nginx-aware: respects `X-Forwarded-For` for client IP logic in production
- Database defaults to SQLite; PostgreSQL is the production target
- Package management: `uv`
