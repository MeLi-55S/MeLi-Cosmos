#!/usr/bin/env bash
# =============================================================================
# MeLi Cosmos v2.0 一键启动脚本
#
# 用法:
#   ./start.sh           生产模式 (Gunicorn, 默认端口 8000)
#   ./start.sh dev       开发模式 (Django runserver, 端口 8000)
#   ./start.sh 8080      生产模式, 指定端口
#   ./start.sh dev 8080  开发模式, 指定端口
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")"

# ---------------------------------------------------------------------------
# 环境检查
# ---------------------------------------------------------------------------
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "[info] .env 不存在, 已从 .env.example 自动创建, 请检查后重新启动"
        cp .env.example .env
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# 解析参数
# ---------------------------------------------------------------------------
MODE="prod"
PORT="8000"

for arg in "$@"; do
    case "$arg" in
        dev) MODE="dev" ;;
        [0-9]*) PORT="$arg" ;;
    esac
done

# ---------------------------------------------------------------------------
# 静态文件 & 数据库迁移
# ---------------------------------------------------------------------------
echo "[info] 收集静态文件..."
uv run python manage.py collectstatic --no-input 2>/dev/null || true

echo "[info] 应用数据库迁移..."
uv run python manage.py migrate --no-input

# ---------------------------------------------------------------------------
# 启动服务
# ---------------------------------------------------------------------------
if [ "$MODE" = "dev" ]; then
    echo "[info] 开发模式启动 (runserver) → http://127.0.0.1:${PORT}"
    exec uv run python manage.py runserver "0.0.0.0:${PORT}"
else
    # 根据 CPU 核数估算 worker 数，范围 1-4
    WORKERS="${GUNICORN_WORKERS:-$(( $(nproc 2>/dev/null || echo 2) < 4 ? $(nproc 2>/dev/null || echo 1) : 4 ))}"
    echo "[info] 生产模式启动 (Gunicorn) → http://0.0.0.0:${PORT} (workers: ${WORKERS})"
    exec uv run gunicorn my_cosmos.wsgi:application \
        --bind "0.0.0.0:${PORT}" \
        --workers "${WORKERS}" \
        --forwarded-allow-ips "127.0.0.1" \
        --access-logformat '%({x-forwarded-for}i)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"' \
        --access-logfile - \
        --error-logfile - \
        --log-level info
fi
