# AGENTS.md — MeLi Cosmos

## Commands

```bash
uv sync                                                  # install deps
uv run python manage.py runserver                        # dev → 127.0.0.1:8000
uv run python manage.py migrate                          # apply migrations
uv run python manage.py test                             # 232 tests (104 web + 128 API)
uv run python manage.py test blog.tests_api              # API 层测试单独跑
uv run python manage.py export_openapi                   # 重新生成 docs/openapi.json 快照
uv run python manage.py seed_data                        # admin user debris/admin
uv run python manage.py collectstatic --noinput           # static files
./tailwindcss-cli -i static/css/tailwind-input.css -o static/css/tailwind.min.css --minify  # rebuild CSS
./start.sh [dev] [port]                                  # alternative: gunicorn or runserver
gunicorn my_cosmos.wsgi -b 127.0.0.1:9999               # production
```

Env loaded from `.env` via `python-dotenv` (see `.env.example`). Project config: `my_cosmos/settings.py`.

## Architecture

- **Django project**: `my_cosmos/` — root urls mount `admin/`, `dashboard/`, `blog/`
- **Main app**: `blog/` — models, views (~1800 lines), middleware, feeds, sitemaps, context processors
- **Dashboard**: `dashboard/` — staff-only single-page view
- **CSS**: Tailwind v4 pre-compiled (no build step, no CDN). Run the CLI manually when changing utility classes in templates. **Never create standalone `.css` files.** Input: `static/css/tailwind-input.css`, output: `static/css/tailwind.min.css`. Dark theme via `.dark` class on `<html>`.
- **Markdown**: `Post.body` → python-markdown (extra, codehilite, fenced_code, toc, nl2br) → nh3 sanitization → `Post.body_html` (rendered with `|safe` in templates)
- **Like/Comment**: GenericForeignKey targets both `Post` and `Memo` (content_type `blog.post`/`blog.memo`)
- **Ban system**: `BanCheckMiddleware` logs out banned users. Admin `_ban_chain()` recursively bans invite-tree successors. `is_permanent_ban` flag exempts from recursive unban.
- **Registration**: invite-code only (`InviteCode` model, atomic `UPDATE … WHERE is_used=False`)
- **REST API**: `blog/api/` (django-ninja) mounted at `/api/v1/` — docs at `/api/v1/docs`, schema at `/api/v1/openapi.json`, snapshot in `docs/openapi.json`. `routers/` holds endpoint groups, `common.py` the shared helpers, `auth.py` the three auth tiers, `serializers.py` all model→dict conversions. **Business rules are never re-implemented here**: posts/memos/likes/comments/uploads/invites/notifications all call the same functions `blog/views.py` exposes (`create_comment`, `toggle_like`, `store_uploaded_image`, `record_post_view`, `issue_invite_code`, `mark_notifications_read`, …).

## Quirks

- `Post.unique_id` (UUID) exists for Obsidian sync — referenced by views, not user-facing
- Slug dedup via `_unique_slug(model_cls, base_slug, author)` — all per-author unique
- Tests mock DiceBear avatar download (`blog_models.generate_default_avatar = _mock_generate`) to avoid network calls
- `start.sh` auto-copies `.env.example` → `.env` if missing, runs collectstatic + migrate, then starts gunicorn (prod) or runserver (dev)
- Production nginx uses `proxy_protocol`; `X-Forwarded-For` derived from `$proxy_protocol_addr` (nginx.conf.reference)
- `management/commands/`: `seed_data`, `cleanup_view_logs`, `cleanup_expired_invites`, `export_openapi`
- `main.py` at repo root is a no-op placeholder — not the entry point

## Fonts

- **UI Subset (`Sarasa UI`)**: Tiny subset (82/83 KB) extracted from template static CJK chars, loaded eagerly in `<head>`. Covers all navigation, buttons, labels, legal text.
- **Full Sarasa (`Sarasa UI SC`)**: Chunked font via cn-font-split, loaded via preload+onload swap. Only triggered when post content contains characters outside the UI subset.
- **Rebuild subset**: `uv run python3 tools/extract_ui_chars.py --rebuild && uv run python manage.py collectstatic --noinput`

## Existing References

- **`CLAUDE.md`** — commands, architecture summary, UI constraints, dark theme tokens, config table
- **`ARCHITECTURE.md`** (gitignored, *not* committed) — full model/URL/deployment reference
- `.gitignore` also excludes `.claude/`, `.vscode/`, `db.sqlite3`, `staticfiles/`, `media/`, `tailwindcss-cli`, `tools/`
