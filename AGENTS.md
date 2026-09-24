# AGENTS.md — MeLi Cosmos

## Commands

```bash
uv sync                                                  # install deps
uv run python manage.py runserver                        # dev → 127.0.0.1:8000
uv run python manage.py migrate                          # apply migrations
uv run python manage.py test                             # 238 tests (104 web + 134 API)
uv run python manage.py test blog.tests_api              # API 层测试单独跑
./scripts/e2e_api_acceptance.sh                          # 真 HTTP 端到端验收（143 项，只跑 /tmp 数据库副本）
uv run python manage.py export_openapi                   # 重新生成 docs/openapi.json 快照
uv run python manage.py seed_data                        # admin user debris/admin
uv run python manage.py collectstatic --noinput           # static files
./tailwindcss-cli -i static/css/tailwind-input.css -o static/css/tailwind.min.css --minify  # rebuild CSS
./start.sh [dev] [port]                                  # alternative: gunicorn or runserver
gunicorn my_cosmos.wsgi -b 127.0.0.1:9999               # production
./sync-aliserver.sh status                               # 只读：本机/云端差在哪、服务与依赖状态
./sync-aliserver.sh check [--skip-tests]                 # 预检：本机测试 + 云端只读体检 + 待部署提交校验
./sync-aliserver.sh deploy --yes                         # 备份 → ff 拉代码 → 依赖/迁移/静态 → 重启 → 体检 → 不健康自动回滚
./sync-aliserver.sh selftest                             # 离线：远端脚本语法 + dry-run 保护 + 备份/部署/回滚影子彩排
```

Deployment goes through git: commit → `push origin main` → `./sync-aliserver.sh deploy --yes`
(aliserver `/home/admin/blog`, systemd `blog.service`, gunicorn on 127.0.0.1:9999). Without
`--yes` the script writes **nothing** to the cloud — it only prints the plan; it never even
opens an ssh connection on the write paths. Every deploy first lands a mirrored backup
(remote `$HOME/backups/<ts>` + local `~/blog-backups/<ts>`, sha256-verified both sides; the
sqlite file is copied through the online-backup API because WAL-mode `cp` can grab half-written
pages). The server venv has neither uv nor pip, so the deploy step bootstraps pip via
`ensurepip` and installs `django-ninja` pinned to `uv.lock`'s version. Whether ninja is
present is probed with `importlib.metadata.version("django-ninja")`, **never** with a bare
`python -c "import ninja"` — ninja reads its own settings at import time, so that probe fails
whether or not the package is installed (it aborted the first real deploy on 2026-09-24). The
real gate is `manage.py check`, run after install and *before* migrate/restart. `deploy.sh` is
only a shim that forwards here; `start.sh` is unrelated (it runs the service on the machine
itself).

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
- API URL-space errors (typo'd path → 404, wrong method → 405) never reach ninja's exception handler; `blog.api.errors.ApiUrlErrorShapeMiddleware` (last in `MIDDLEWARE`) rewrites them into the same JSON envelope. Non-`/api/` 404 pages stay HTML on purpose.
- No `CACHES` in settings → LocMemCache per process, so login/comment/API rate limits effectively multiply by the gunicorn worker count.
- `scripts/e2e_api_acceptance.sh` runs 143 real-HTTP assertions against a throwaway `/tmp` copy of `db.sqlite3` on port 8011 (`REPO`/`TMP`/`PORT` overridable); it never touches the repo DB or the cloud. Report: `docs/api-deploy-acceptance.md`
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
