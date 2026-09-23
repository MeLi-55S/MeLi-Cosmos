#!/usr/bin/env bash
#
# sync-aliserver.sh — 用 git 通道把本机 blog 项目同步到 aliserver（blog.melichem.cn）
#
#   ./sync-aliserver.sh status                只读：本机与云端差在哪、服务与依赖状态
#   ./sync-aliserver.sh check                 只读 + 本机测试：部署前预检
#   ./sync-aliserver.sh backup --yes          在云端+本机各落一份带校验的备份（不改动现状）
#   ./sync-aliserver.sh deploy --yes          备份 → 拉代码 → 依赖/迁移/静态 → 重启 → 健康检查
#   ./sync-aliserver.sh rollback --yes [ref]  回到上一次记录的提交（或指定 ref）并重启
#   ./sync-aliserver.sh push --yes            把本机分支推到 origin（deploy 的前置）
#   ./sync-aliserver.sh logs [n]              看远端服务日志（只读）
#   ./sync-aliserver.sh rehearse              只在临时目录里彩排备份/部署/回滚（绝不联网）
#   ./sync-aliserver.sh selftest              校验远端脚本 + dry-run 保护 + 备份/部署/回滚彩排（绝不联网）
#
#   选项：--yes 才真正写云端（backup/deploy/rollback/push 默认 dry-run）
#         --ref <sha>  指定要部署/回滚的提交或分支
#         --skip-tests 预检时不跑本机测试
#         --server/--dir/--branch  覆盖默认目标
#
# 三条铁律：
#   1. 没有 --yes，backup / deploy / rollback / push 一个字节都不写到云端（只打印将执行什么）。
#   2. 云端任何改动之前先落两份备份：远端 ~/backups/<ts> 与本机 ~/blog-backups/<ts>，并校验 sha256。
#   3. 重启后健康检查不过 → 自动 reset --hard 回部署前的提交再重启，绝不把站点留在坏状态。
#
set -euo pipefail

SERVER="${BLOG_SSH_ALIAS:-aliserver}"
REMOTE_DIR="${BLOG_REMOTE_DIR:-/home/admin/blog}"
BRANCH="${BLOG_BRANCH:-main}"
LOCAL_BACKUP_ROOT="${BLOG_LOCAL_BACKUP_ROOT:-$HOME/blog-backups}"
HEALTH_BASE="http://127.0.0.1:9999"          # 只在远端机器上访问
PUBLIC_URL="${BLOG_PUBLIC_URL:-https://blog.melichem.cn}"
REMOTE_PY="$REMOTE_DIR/.venv/bin/python"
STATE_FILE="$REMOTE_DIR/.deploy-state"        # 每行：时间 TAB 旧HEAD TAB 新HEAD TAB 结果

TARGET_REF=""
YES=0
RUN_TESTS=1
TS="$(date +%Y%m%dT%H%M%S)"
REMOTE_BACKUP_REL="backups/$TS"             # 远端路径 = 远端自己的 $HOME/这个相对路径

c_info() { printf '\033[36m▸ %s\033[0m\n' "$*"; }
c_ok()   { printf '\033[32m✔ %s\033[0m\n' "$*"; }
c_warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
c_err()  { printf '\033[31m✘ %s\033[0m\n' "$*" >&2; }
die()    { c_err "$*"; exit 1; }
# 帮助直接从文件头部的注释块取：第 3 行到 "三条铁律" 之前，改注释即改进度帮助
usage()  { awk 'NR>=3 && /^# 三条铁律/{exit} NR>=3{sub(/^# ?/,"");print}' "$0"; exit "${1:-0}"; }

# ── 执行层 ────────────────────────────────────────────────────────────────
ssh_ro() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$SERVER" "$@"; }

# run_remote <说明> <远端脚本> [KEY=VAL ...]
#   返回 0 = 已执行且成功；2 = dry-run 跳过；1 = 失败
#   参数只能写进脚本头部：ssh 默认不会把本地环境变量带到远端（只转发 LC_* 之类），
#   用 `env K=V ssh …` 传的话远端拿到的是空值，配合 set -u 直接当场退出。
run_remote() {
  local desc="$1" script="$2"; shift 2
  # 整段远端逻辑是个字符串，先在本机过一遍语法检查：引号被吃掉、heredoc 提前闭合这类
  # 错误如果到服务器上才暴露，往往服务已经停了、代码已经切了一半，最难收拾。
  printf '%s\n' "$script" | bash -n || die "远端脚本语法不通过，已中止（云端未被触碰）"
  if [ "$YES" -ne 1 ]; then
    c_warn "dry-run：$desc —— 云端未执行（要真做请加 --yes）" >&2
    return 2
  fi
  c_info "$desc"
  local kv preamble=""
  for kv in "$@"; do
    preamble+="${kv%%=*}=$(printf '%q' "${kv#*=}")"$'\n'
  done
  printf '%s%s' "$preamble" "$script" \
    | ssh -o BatchMode=yes -o ConnectTimeout=20 "$SERVER" "bash -s"
}

# 待部署提交：必须已经存在于 origin 的某个分支上，否则远端 git pull 拿不到
resolve_target() {
  local sha ref="${TARGET_REF:-HEAD}"
  sha="$(git rev-parse --verify "$ref^{commit}" 2>/dev/null)" || die "无法解析 ref：$ref"
  git fetch -q origin || c_warn "git fetch origin 失败，改用本地缓存的远端引用判断"
  # 云端是先 checkout $BRANCH 再 merge --ff-only，所以目标必须落在 origin/$BRANCH 这条线上，
  # 在别的分支上（哪怕已经 push 了）也照样部署不了。
  if ! git merge-base --is-ancestor "$sha" "origin/$BRANCH" 2>/dev/null; then
    die "提交 ${sha:0:8} 不在 origin/$BRANCH 上。先把它并进 $BRANCH 再 ./sync-aliserver.sh push --yes"
  fi
  printf '%s' "$sha"
}

ninja_version() {
  local v
  v="$(awk '/^name = "django-ninja"$/{f=1} f && /^version = /{gsub(/[^0-9.]/,"",$0); print $0; exit}' uv.lock 2>/dev/null)"
  [ -n "$v" ] || die "uv.lock 里找不到 django-ninja 的版本"
  printf '%s' "$v"
}

# ── status / check：只读 ──────────────────────────────────────────────────
# 只读片段同样写成字符串（heredoc）：内联在双引号 ssh 里的话，一个没转义的双引号就会
# 把整段撕开，而这类损坏本机看不出来（bash -n 只检查剩下的部分）。写成字符串后
# selftest 能在上云之前把它 bash -n 一遍。
S_STATUS="$(cat <<'REMOTE_S_STATUS'
set -uo pipefail
cd "$REMOTE_DIR" || { echo "  ✘ 打不开 $REMOTE_DIR"; exit 1; }
echo "  分支 $(git rev-parse --abbrev-ref HEAD)   HEAD $(git rev-parse --short HEAD)  $(git log -1 --format=%s)"
D=$(git status --porcelain)
if [ -n "$D" ]; then echo '  ! 云端工作树不干净（部署会在这里被拒绝）'; printf '%s\n' "$D" | head -10 | sed 's/^/    /'
else echo '  云端工作树干净'; fi
S=$(sudo -n systemctl is-active blog 2>/dev/null || true)
N=$("$REMOTE_PY" -c 'import ninja; print(ninja.__version__)' 2>/dev/null || echo '未安装')
echo "  服务 blog: ${S:-未知}   django-ninja: $N"
echo "  磁盘可用 $(df -hP /home | awk 'NR==2{print $4}')   内存可用 $(free -m | awk 'NR==2{print $7}') MiB"
echo "  后端首页 → $(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$HEALTH_BASE/" || echo 无响应)"
echo "  /api/v1/health → $(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$HEALTH_BASE/api/v1/health" || echo 无响应)"
git fetch -q origin 2>/dev/null || echo '  ! git fetch origin 失败（下面按本地缓存的远端引用比较）'
echo "  云端落后 origin/$BRANCH: $(git rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null || echo '?') 个提交"
if [ -f "$STATE_FILE" ]; then echo '  最近部署记录:'; tail -3 "$STATE_FILE" | sed 's/^/    /'; fi
exit 0
REMOTE_S_STATUS
)"

S_CHECK="$(cat <<'REMOTE_S_CHECK'
set -uo pipefail
cd "$REMOTE_DIR" || { echo "  ✘ 打不开部署目录 $REMOTE_DIR"; exit 1; }
ok()   { printf '  ✔ %s\n' "$*"; }
bad()  { printf '  ! %s\n' "$*"; BAD=$((BAD + 1)); }
info() { printf '    %s\n' "$*"; }
BAD=0

[ -w "$REMOTE_DIR" ] && ok "部署目录可写" || bad "部署目录不可写，git pull 会失败"
[ -x "$REMOTE_PY" ] && ok "虚拟环境 python: $("$REMOTE_PY" -V 2>&1)" || bad "找不到 $REMOTE_PY"
sudo -n true 2>/dev/null && ok "sudo 免密可用（restart 服务要用）" || bad "sudo 需要口令，部署会卡在重启那一步"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 && ok "部署目录是 git 仓库" || bad "部署目录不是 git 仓库"
info "origin: $(git remote get-url origin 2>/dev/null || echo 无)"

KB=$(df -kP /home | awk 'NR==2{print $4}')
GB=$(( KB / 1048576 ))
if [ "${KB:-0}" -lt 1048576 ]; then bad "可用空间不足 1G（约 ${GB}G），先清理再部署"
else ok "可用空间 ${GB}G"; fi

if command -v uv >/dev/null 2>&1; then ok "uv 已装 → 依赖走 uv sync --frozen"
else
  if "$REMOTE_PY" -m pip --version >/dev/null 2>&1; then ok "venv 内有 pip → 依赖走 pip install"
  elif "$REMOTE_PY" -c 'import ensurepip' >/dev/null 2>&1; then ok "venv 内无 pip，但有 ensurepip → 部署时先 bootstrap"
  else bad "既没有 uv，也没有 pip/ensurepip → 装不了 django-ninja"; fi
fi
"$REMOTE_PY" -c 'import ninja' 2>/dev/null && info "django-ninja 已在环境里" || info "django-ninja 待安装（版本取自 uv.lock）"

[ -f db.sqlite3 ] && info "数据库 $(du -h db.sqlite3 | awk '{print $1}')（部署前会在线备份并双份留存）"
[ -d media ] && info "media $(du -sh media | awk '{print $1}')（部署前打包备份）"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$HEALTH_BASE/" || echo 000)
[ "$code" = "200" ] && ok "当前后端在 $HEALTH_BASE 上是活的（回滚才有意义）" || bad "当前后端 health=$code，先确认站点没挂"

if [ "$BAD" -gt 0 ]; then echo "  ✘ 预检未通过：$BAD 项需要先处理"; exit 1; fi
echo "  ✔ 云端预检通过"
REMOTE_S_CHECK
)"

# 把一段只读脚本送到远端执行（永远不写），同样先在本机做语法检查
ssh_script() {
  local script="$1"; shift
  printf '%s\n' "$script" | bash -n || die "远端只读脚本语法不通过，已中止（云端未被触碰）"
  local kv preamble=""
  for kv in "$@"; do
    preamble+="${kv%%=*}=$(printf '%q' "${kv#*=}")"$'\n'
  done
  printf '%s%s' "$preamble" "$script" \
    | ssh -o BatchMode=yes -o ConnectTimeout=15 "$SERVER" "bash -s"
}

cmd_status() {
  c_info "本机 ($(git rev-parse --show-toplevel))"
  printf '  分支 %s   HEAD %s  %s\n' "$(git rev-parse --abbrev-ref HEAD)" \
    "$(git rev-parse --short HEAD)" "$(git log -1 --format=%s)"
  local dirty; dirty="$(git status --porcelain)"
  if [ -n "$dirty" ]; then
    c_warn "本机有未提交改动，git 通道不会带上它们："
    printf '%s\n' "$dirty" | sed 's/^/    /'
  else
    printf '  工作树干净\n'
  fi
  git fetch -q origin 2>/dev/null || true
  printf '  本机领先 origin/%s：%s 个提交\n' "$BRANCH" \
    "$(git rev-list --count "origin/$BRANCH..HEAD" 2>/dev/null || echo '?')"

  c_info "云端 $SERVER:$REMOTE_DIR"
  ssh_script "$S_STATUS" REMOTE_DIR="$REMOTE_DIR" REMOTE_PY="$REMOTE_PY" \
    HEALTH_BASE="$HEALTH_BASE" STATE_FILE="$STATE_FILE" BRANCH="$BRANCH" \
    || die "ssh 到 $SERVER 失败"
}

cmd_check() {
  if [ "$RUN_TESTS" -eq 1 ]; then
    c_info "本机测试（失败即禁止部署）"
    ( cd "$(git rev-parse --show-toplevel)" && uv run python manage.py test ) || die "本机测试未通过"
    c_ok "本机测试全部通过"
  else
    c_warn "已按 --skip-tests 跳过本机测试"
  fi

  c_info "云端预检（只读）"
  ssh_script "$S_CHECK" REMOTE_DIR="$REMOTE_DIR" REMOTE_PY="$REMOTE_PY" \
    HEALTH_BASE="$HEALTH_BASE" || die "云端预检未通过（或未连上 $SERVER）"

  if target="$(resolve_target 2>/dev/null)"; then
    c_ok "待部署提交可用：${target:0:8}"
  else
    c_warn "待部署提交还不在 origin/$BRANCH 上 —— 需要：并进 $BRANCH → push --yes → deploy --yes"
  fi
}

# ── backup ────────────────────────────────────────────────────────────────
# 备份清单：一次部署能弄坏什么，这里就存什么。
#   代码 → git 本身能回滚，只记 HEAD/状态/日志；数据库、media、.env、unit、依赖清单是不可再生资产。
# 每一项都必须存在（缺就落一个空占位），否则 SHA256SUMS 会漏项，"校验通过"就成了假信号。
S_BACKUP="$(cat <<'REMOTE_S_BACKUP'
set -euo pipefail
cd "$REMOTE_DIR"
DEST="$HOME/$REMOTE_BACKUP_REL"
mkdir -p "$DEST"
log() { printf "  [远端] %s\n" "$*"; }

git rev-parse HEAD            > "$DEST/git-head.txt"
git status --porcelain        > "$DEST/git-status.txt"
git log --oneline -8          > "$DEST/git-log.txt"
git rev-parse --abbrev-ref HEAD > "$DEST/git-branch.txt"

# 数据库：用 sqlite 在线备份 API，直接 cp 在 WAL 模式下可能拿到半截页
"$REMOTE_PY" - "$DEST/db.sqlite3" <<PY
import sqlite3, sys
src = sqlite3.connect("db.sqlite3")
dst = sqlite3.connect(sys.argv[1])
src.backup(dst)
rows = dst.execute("select count(*) from sqlite_master").fetchone()[0]
dst.close(); src.close()
print(f"  [远端] 数据库已在线备份（{rows} 个对象）")
PY

cp -a .env "$DEST/dot-env" 2>/dev/null || log "无 .env，已留空占位"
[ -f "$DEST/dot-env" ] || : > "$DEST/dot-env"
tar -czf "$DEST/media.tar.gz" media 2>/dev/null || log "无 media，已留空占位"
[ -s "$DEST/media.tar.gz" ] || : > "$DEST/media.tar.gz"
sudo -n systemctl cat blog > "$DEST/blog.service" 2>/dev/null || log "读不到 unit（sudo 不可用），已留空占位"
[ -f "$DEST/blog.service" ] || : > "$DEST/blog.service"
"$REMOTE_PY" -m pip freeze > "$DEST/pip-freeze.txt" 2>/dev/null || echo "# pip 不可用" > "$DEST/pip-freeze.txt"
"$REMOTE_PY" -V > "$DEST/python-version.txt" 2>&1

# 空占位允许存在，真正不可再生的一样都不能少
[ -s "$DEST/db.sqlite3" ]   || { log "✘ 数据库备份是空的，中止"; exit 1; }
[ -s "$DEST/git-head.txt" ] || { log "✘ 拿不到 git HEAD，中止"; exit 1; }
[ -s "$DEST/media.tar.gz" ] || log "! media.tar.gz 为空（部署不会动 media，可接受）"

cd "$DEST"
sha256sum db.sqlite3 dot-env media.tar.gz blog.service pip-freeze.txt \
          python-version.txt git-head.txt git-status.txt git-log.txt git-branch.txt > SHA256SUMS
sed 's/^/  [远端] /' SHA256SUMS
sha256sum -c --quiet SHA256SUMS || { log "✘ 远端自检都没过，这份备份不可信"; exit 1; }
log "远端自检通过（sha256 -c）"
du -sh "$DEST" | sed 's/^/  [远端] 备份体积 /'
echo "BACKUP_PATH $DEST"
REMOTE_S_BACKUP
)"

cmd_backup() {
  local out rc=0
  out="$(run_remote "备份云端现状 → 远端 \$HOME/$REMOTE_BACKUP_REL" "$S_BACKUP" \
          REMOTE_DIR="$REMOTE_DIR" REMOTE_PY="$REMOTE_PY" REMOTE_BACKUP_REL="$REMOTE_BACKUP_REL")" || rc=$?
  if [ "$rc" -eq 2 ]; then return 2; fi
  [ "$rc" -eq 0 ] || { printf '%s\n' "$out" >&2; die "云端备份失败"; }
  printf '%s\n' "$out"

  local dest
  dest="$(printf '%s\n' "$out" | awk '/^BACKUP_PATH/{print $2}')"
  [ -n "$dest" ] || die "远端没有回传备份路径"

  printf '  云端备份落在 %s\n' "$dest"
  c_info "镜像到本机 $LOCAL_BACKUP_ROOT/$TS（备份只放一台机器等于没有备份）"
  mkdir -p "$LOCAL_BACKUP_ROOT/$TS"
  scp -q -o BatchMode=yes "$SERVER:$dest/*" "$LOCAL_BACKUP_ROOT/$TS/"
  ( cd "$LOCAL_BACKUP_ROOT/$TS" && sha256sum -c SHA256SUMS ) || die "本机镜像与云端校验不一致"
  # 清单里有 10 项，scp 少传一个文件也会让 -c 通过（它只校验清单里有的）
  local want got
  want="$(wc -l < "$LOCAL_BACKUP_ROOT/$TS/SHA256SUMS")"
  got="$(find "$LOCAL_BACKUP_ROOT/$TS" -maxdepth 1 -type f ! -name SHA256SUMS | wc -l)"
  [ "$want" = "$got" ] || die "镜像到本机的文件数 $got 与清单 $want 不符，scp 可能漏了"
  c_ok "两份备份就位且校验一致：$TS"
}

# backup 子命令：备份也要 --yes——往云端写文件同样是对云端的改动
cmd_backup_only() {
  local rc=0
  cmd_backup || rc=$?
  case "$rc" in
    0) c_ok "备份完成，云端现状未被改动（工作树、数据库、服务都没碰）" ;;
    2) c_warn "dry-run：未连接云端、未落备份。真要备份：./sync-aliserver.sh backup --yes" >&2 ;;
    *) exit "$rc" ;;
  esac
}

# ── deploy ────────────────────────────────────────────────────────────────
S_DEPLOY="$(cat <<'REMOTE_S_DEPLOY'
set -euo pipefail
cd "$REMOTE_DIR"
log() { printf "  [远端] %s\n" "$*"; }
fail() { log "✘ $*"; exit 1; }

PREV="$(git rev-parse HEAD)"
CHECKED=0     # 工作树是否已被本次部署动过——动过才需要回滚
ROLLED=0
RECORDED=0

# 中途任何一步失败都不能把站点留在"新代码 + 没重启的旧进程"这类半新不旧的状态：
# 退出前统一把代码退回部署前那一刻并重启，健康检查那一关另有兜底。
on_exit() {
  local rc=$?
  [ "$rc" = 0 ] && return 0
  if [ "$CHECKED" = 1 ] && [ "$ROLLED" = 0 ]; then
    log "部署中断（退出码 $rc）→ 回滚代码到 ${PREV:0:7} 并重启"
    git reset --hard "$PREV" >/dev/null 2>&1 || log "（git 回滚没成功，需要人工确认）"
    sudo -n systemctl restart blog 2>/dev/null || log "（重启没成功，需要人工确认）"
    log "已退回旧代码；新装的依赖、新建的数据表不会被旧代码引用，可留着不管"
    # 中断也要记一行：PREV == 回滚后的 HEAD，rollback 挑目标时会自动跳过这种原地记录
    if [ "$RECORDED" = 0 ]; then
      { printf "%s\t%s\t%s\taborted\n" "$(date -Is)" "$PREV" "$(git rev-parse HEAD)" \
          >> "$STATE_FILE"; } 2>/dev/null || true
    fi
    echo "DEPLOY_RESULT aborted"
  fi
  return "$rc"
}
trap on_exit EXIT

[ "$CLOUD_DIRTY" = "clean" ] || fail "云端工作树不干净，先人工确认再部署"
git fetch -q origin || fail "git fetch 失败"
git merge-base --is-ancestor "$TARGET" "origin/$BRANCH" \
  || fail "$TARGET 不在 origin/$BRANCH 这条线上（部署只认 $BRANCH）"

git checkout -q "$BRANCH" || fail "切到 $BRANCH 失败"
CHECKED=1
if [ "$PREV" != "$TARGET" ]; then
  git merge --ff-only "$TARGET" >/dev/null \
    || fail "无法快进到 $TARGET（云端 $BRANCH 被单独改过，需要人工处理）"
fi
log "代码 ${PREV:0:7} → $(git rev-parse --short HEAD)"

# 依赖：ninja 缺失、或依赖清单变了才动
NEED=0
"$REMOTE_PY" -c "import ninja" 2>/dev/null || NEED=1
git diff --name-only "$PREV" HEAD | grep -qE "^(pyproject\.toml|uv\.lock)$" && NEED=1
if [ "$NEED" = 1 ]; then
  if command -v uv >/dev/null 2>&1; then
    log "uv sync --frozen"; uv sync --frozen || fail "uv sync 失败"
  else
    log "pip install django-ninja==$NINJA_VER（venv 里没有 pip 就先 ensurepip）"
    "$REMOTE_PY" -m pip --version >/dev/null 2>&1 || "$REMOTE_PY" -m ensurepip --upgrade >/dev/null 2>&1
    "$REMOTE_PY" -m pip install -q --upgrade "django-ninja==$NINJA_VER" || fail "pip 安装失败"
    "$REMOTE_PY" -c "import ninja" || fail "装完仍然 import ninja 失败"
  fi
else
  log "依赖无需变动"
fi

log "migrate --noinput（只增表/字段，不改老数据）"
"$REMOTE_PY" manage.py migrate --noinput 2>&1 | tail -8 || fail "migrate 失败"

if git diff --name-only "$PREV" HEAD | grep -qE "^(static/|templates/)|fonts/"; then
  log "collectstatic --noinput"
  "$REMOTE_PY" manage.py collectstatic --noinput 2>&1 | tail -2 || fail "collectstatic 失败"
else
  log "静态文件无需重收集"
fi

log "systemctl restart blog"
sudo -n systemctl restart blog || fail "重启失败（sudo 免密是否还在？）"
sleep 3

health() {
  local body code url c
  code="$(curl -s -o /tmp/blog_health.json -w "%{http_code}" --max-time 10 "$HEALTH_BASE/api/v1/health" || echo 000)"
  body="$(cat /tmp/blog_health.json 2>/dev/null || true)"
  [ "$code" = "200" ] || { log "health 返回 $code"; return 1; }
  printf '%s' "$body" | grep -q "database[^,]*ok" || { log "health 里数据库不是 ok"; return 1; }
  for url in / /api/v1/posts /api/v1/openapi.json; do
    c="$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$HEALTH_BASE$url" || echo 000)"
    [ "$c" = "200" ] || { log "$url 返回 $c"; return 1; }
  done
  c="$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST "$HEALTH_BASE/api/v1/posts" \
        -H 'Content-Type: application/json' -d '{}' || echo 000)"
  [ "$c" = "401" ] || { log "未带令牌的写请求返回 $c（应为 401）"; return 1; }
  return 0
}

RESULT=ok
if health; then
  log "健康检查通过（页面 + 公开端点 + 认证链路）"
else
  ROLLED=1
  log "健康检查失败 → 自动回滚到 ${PREV:0:7}"
  git reset --hard "$PREV" >/dev/null || log "（回滚失败，需要人工介入）"
  sudo -n systemctl restart blog || log "（回滚后重启失败，需要人工介入）"
  sleep 3
  if health; then RESULT=rolled_back; log "已回到 ${PREV:0:7}，服务恢复正常，本次部署作废"
  else RESULT=failed; log "回滚后仍不健康，必须人工介入：sudo journalctl -u blog -n 80"; fi
fi

RECORDED=1
{ printf "%s\t%s\t%s\t%s\n" "$(date -Is)" "$PREV" "$(git rev-parse HEAD)" "$RESULT"; } >> "$STATE_FILE" 2>/dev/null \
  || log "写 $STATE_FILE 失败（不影响部署结果）"
echo "DEPLOY_RESULT $RESULT"
echo "DEPLOY_HEAD $(git rev-parse --short HEAD) (was ${PREV:0:7})"
[ "$RESULT" = ok ]
REMOTE_S_DEPLOY
)"

cmd_deploy() {
  local target dirty rc=0
  if ! target="$(resolve_target 2>/dev/null)"; then
    if [ "$YES" -eq 1 ]; then
      resolve_target; die "待部署提交不可用"
    fi
    target="$(git rev-parse HEAD)"
    c_warn "dry-run 拿本机 HEAD 演示；这个提交还没并进 origin/$BRANCH，真部署前必须先 push"
  fi
  c_info "目标提交 ${target:0:8} → $SERVER:$REMOTE_DIR ($BRANCH)"

  cmd_backup || rc=$?
  if [ "$rc" -eq 2 ]; then
    c_warn "dry-run：下面每一步都只是计划，云端保持原样（要落地请加 --yes）"
    printf '  1. 备份  远端 $HOME/%s + 本机 %s/%s，sha256 双向校验\n' \
      "$REMOTE_BACKUP_REL" "$LOCAL_BACKUP_ROOT" "$TS"
    printf '  2. 代码  git fetch → merge --ff-only %s（云端工作树脏则直接拒绝）\n' "${target:0:8}"
    printf '  3. 依赖  有 uv 走 uv sync --frozen，否则 ensurepip + pip install django-ninja==%s\n' \
      "$(ninja_version 2>/dev/null || echo '?')"
    printf '  4. 数据库 manage.py migrate --noinput（本次只新增 ApiToken 表，不动老数据）\n'
    printf '  5. 静态  仅当 static/ templates/ fonts/ 有变更才 collectstatic\n'
    printf '  6. 重启  sudo systemctl restart blog\n'
    printf '  7. 体检  / + /api/v1/health(数据库 ok) + /api/v1/posts + /api/v1/openapi.json + 无令牌 POST 必须 401\n'
    printf '  8. 兜底  体检任一不过 → reset --hard 回部署前提交再重启，并在 .deploy-state 记一行\n'
    return 0
  fi
  [ "$rc" -eq 0 ] || die "备份失败，取消部署"

  dirty="$(ssh_ro "cd $REMOTE_DIR && test -z \"\$(git status --porcelain)\" && echo clean || echo dirty")"
  run_remote "部署 ${target:0:8}" "$S_DEPLOY" \
    REMOTE_DIR="$REMOTE_DIR" REMOTE_PY="$REMOTE_PY" BRANCH="$BRANCH" TARGET="$target" \
    CLOUD_DIRTY="$dirty" NINJA_VER="$(ninja_version)" STATE_FILE="$STATE_FILE" \
    HEALTH_BASE="$HEALTH_BASE" || die "部署失败（详见上面的远端日志）"

  c_info "从本机验证公网链路"
  curl -s -o /dev/null -w "  $PUBLIC_URL/ → %{http_code}\n" --max-time 20 "$PUBLIC_URL/" || c_warn "公网首页无响应"
  curl -s --max-time 20 "$PUBLIC_URL/api/v1/health" | head -c 220; printf '\n'
  curl -s -o /dev/null -w "  $PUBLIC_URL/api/v1/docs → %{http_code}\n" --max-time 20 "$PUBLIC_URL/api/v1/docs" || true
}

# ── rollback ──────────────────────────────────────────────────────────────
S_ROLLBACK="$(cat <<'REMOTE_S_ROLLBACK'
set -euo pipefail
cd "$REMOTE_DIR"
log() { printf "  [远端] %s\n" "$*"; }
CUR="$(git rev-parse HEAD)"
WANT="$WANTED"
if [ -z "$WANT" ]; then
  # 取最近一条"代码真的动了"的记录（PREV != 部署后 HEAD），
  # 否则 aborted / rolled_back 这种原地不动的记录会把回滚目标多退一步
  WANT="$(tail -20 "$STATE_FILE" 2>/dev/null | awk -F'\t' '$2 != $3 {t=$2} END{print t}')"
  [ -n "$WANT" ] || { log "$STATE_FILE 里没有可回滚的记录（没有真实发生过代码变更）"; exit 3; }
fi
log "回滚 ${CUR:0:7} → ${WANT:0:7}"
git reset --hard "$WANT" >/dev/null || { echo "  [远端] ✘ git reset 失败"; exit 1; }
log "代码已退回，正在重启服务"
sudo -n systemctl restart blog || { echo "  [远端] ✘ 重启失败"; exit 1; }
sleep 3
code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$HEALTH_BASE/api/v1/health" || echo 000)"
log "/api/v1/health → $code"
printf "%s\t%s\t%s\trollback\n" "$(date -Is)" "$CUR" "$WANT" >> "$STATE_FILE" 2>/dev/null || true
echo "ROLLBACK_RESULT $( [ "$code" = "200" ] && echo ok || echo unhealthy )"
REMOTE_S_ROLLBACK
)"

cmd_rollback() {
  local wanted="" rc=0
  if [ -n "$TARGET_REF" ]; then
    wanted="$(git rev-parse --verify "$TARGET_REF^{commit}" 2>/dev/null || echo "$TARGET_REF")"
  fi
  if [ "$YES" -ne 1 ]; then
    local plan="${wanted:-上一次记录在 .deploy-state 里的提交}"
    c_warn "dry-run：git reset --hard $plan → restart blog → 检查 /api/v1/health"
    c_warn "         只回滚代码：已建的 api_apitoken 表、已装的 django-ninja 都留着不引用，无害"
    c_warn "         要真回滚：./sync-aliserver.sh rollback --yes [提交号]"
    return 0
  fi
  run_remote "回滚云端代码" "$S_ROLLBACK" \
    REMOTE_DIR="$REMOTE_DIR" WANTED="$wanted" STATE_FILE="$STATE_FILE" HEALTH_BASE="$HEALTH_BASE" \
    || die "回滚失败或无记录（没有 .deploy-state 时用 rollback --yes <提交号> 指定目标）"
  c_ok "回滚指令执行完毕（数据库与文件的备份仍在远端 \$HOME/$REMOTE_BACKUP_REL 一层层目录里和本机 $LOCAL_BACKUP_ROOT/）"
}

cmd_push() {
  local here; here="$(git rev-parse --abbrev-ref HEAD)"
  if [ "$YES" -ne 1 ]; then
    c_warn "dry-run：git push -u origin $here —— 未执行（要真推请加 --yes）"
    return 0
  fi
  git push -u origin "$here" && c_ok "已推送 origin/$here"
}

# 本地彩排：临时造一个 WAL 模式、提交完故意不 checkpoint 的 sqlite 库 + git 仓库，
# 用与云端同一份 S_BACKUP 跑一遍，再把"这份备份到底能不能用"验到底。
# 兜底逻辑自己没被验证过，就等于没有兜底；全程只写临时目录，不联网、不碰云端。
cmd_rehearse() {
  local py tmp repo home dest out kv pre
  py="${BLOG_REHEARSE_PY:-$(command -v python3 || true)}"
  [ -n "$py" ] || die "本机没有 python3，无法彩排备份流程"
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/blog-backup-rehearse.XXXXXX")"
  # shellcheck disable=SC2064
  trap "rm -rf '$tmp'" RETURN
  repo="$tmp/repo"; home="$tmp/home"
  mkdir -p "$repo/media" "$home"

  ( cd "$repo"
    git init -q . && git config user.email rehearse@local && git config user.name rehearse
    printf 'SECRET=keep-me\n' > .env
    printf 'pic' > media/a.txt
    "$py" - <<'PY'
import os, sqlite3
c = sqlite3.connect("db.sqlite3")
c.execute("pragma journal_mode=wal")
c.execute("create table t(x text)")
c.executemany("insert into t values (?)", [("a",), ("b",), ("c",)])
c.commit()
os._exit(0)                     # 故意不 checkpoint：数据留在 -wal 里，考验在线备份 API
PY
    git add -A >/dev/null 2>&1; git commit -qm "rehearse baseline" )

  c_info "本地彩排备份流程（临时目录，不联网）"
  pre=""
  for kv in "REMOTE_DIR=$repo" "REMOTE_PY=$py" "REMOTE_BACKUP_REL=backups/$TS"; do
    pre+="${kv%%=*}=$(printf '%q' "${kv#*=}")"$'\n'
  done
  out="$(cd "$repo" && HOME="$home" bash -s <<<"$pre$S_BACKUP")" || {
    printf '%s\n' "$out" >&2; die "彩排失败：备份脚本本身有问题（还没轮到云端）"
  }
  printf '%s\n' "$out" | sed 's/^/  /'

  dest="$(printf '%s\n' "$out" | awk '/^BACKUP_PATH/{print $2}')"
  [ "$dest" = "$home/backups/$TS" ] || die "彩排：回传的备份路径不对（得到 '${dest:-空}'）"
  ( cd "$dest" && sha256sum -c --quiet SHA256SUMS ) || die "彩排：SHA256SUMS 校验未通过"
  "$py" - "$dest/db.sqlite3" <<'PY' || die "彩排：备份出来的库不可用"
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
check = c.execute("pragma integrity_check").fetchone()[0]
rows = c.execute("select count(*) from t").fetchone()[0]
print(f"  备份库 integrity_check={check}  表 t 行数={rows}")
assert check == "ok" and rows == 3, "WAL 里的数据没有完整进入备份"
PY
  c_ok "彩排通过：备份脚本能产出可用、可校验、含 WAL 数据的备份"
}

# 部署脚本的本地影子彩排：临时造一个"云端仓库"，把 sudo/curl/sleep/python 换成桩，
# 用与真部署完全同一份 S_DEPLOY 跑三种结局：全绿、体检失败自动回滚、重启失败中途退出。
# 这段逻辑会在生产机上切代码、重启服务，不能等到那天才第一次执行。
# 用法：cmd_rehearse_deploy（selftest 会自动调用；全程只写临时目录，不联网）
cmd_rehearse_deploy() {
  local tmp bin repo bare ok_sha target pre rc out
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/blog-deploy-rehearse.XXXXXX")"
  # shellcheck disable=SC2064
  trap "rm -rf '$tmp'" RETURN
  bin="$tmp/bin"; repo="$tmp/site"; bare="$tmp/origin.git"
  mkdir -p "$bin" "$repo"

  # ── 桩：只替掉"会碰到真机器"的四件事 ──────────────────────────────
  cat > "$bin/sudo" <<'STUB'
#!/bin/sh
case "$*" in
  *restart*blog*)
    [ -n "$SUDO_FAIL" ] && { echo "stub sudo: 拒绝重启" >&2; exit 1; }
    echo "  [桩] systemctl restart blog"; exit 0 ;;
esac
exit 0
STUB
  cat > "$bin/curl" <<'STUB'
#!/bin/sh
# 只认这套脚本用到的调用形态：-o 文件 / -w 格式 / -X POST / URL
out=""; fmt=""; post=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift ;;
    -w) fmt="$2"; shift ;;
    -X) post="$2"; shift ;;
    http*) url="$1" ;;
  esac
  shift
done
head_now="$(git -C "$STUB_REPO" rev-parse HEAD 2>/dev/null || echo none)"
code=200; body='{"status": "ok", "database": "ok"}'
if [ "$head_now" = "$BAD_SHA" ]; then code=500; body='{"error": "boom"}'; fi
[ -n "$post" ] && [ "$code" = 200 ] && code=401
[ -n "$out" ] && printf '%s' "$body" > "$out"
case "$fmt" in *http_code*) printf '%s' "$code" ;; esac
exit 0
STUB
  cat > "$bin/sleep" <<'STUB'
#!/bin/sh
exit 0
STUB
  cat > "$bin/python-stub" <<'STUB'
#!/bin/sh
case "$1" in
  -c) exit 0 ;;                      # import ninja 探测
  -V) echo "Python 3.14 (stub)"; exit 0 ;;
  -m) exit 0 ;;                      # pip / ensurepip
esac
if [ "$1" = "manage.py" ]; then
  case "$2" in
    migrate)       echo "  Applying blog.0016_apitoken... OK" ;;
    collectstatic) echo "1 static file copied" ;;
  esac
fi
exit 0
STUB
  chmod +x "$bin"/*

  # ── 影子"云端"：仓库停在 pre，origin/main 已经前进到 target ────────
  git init -q --bare "$bare"
  ( cd "$repo"
    git init -q . && git config user.email r@r.local && git config user.name rehearse
    git checkout -qb main
    mkdir -p blog; echo "# MeLi Cosmos" > README.md; echo "old" > blog/views.py
    git add -A && git commit -qm "云端当前状态"
    git remote add origin "$bare" && git push -q -u origin main
    pre="$(git rev-parse HEAD)"
    echo "new" > blog/views.py && git commit -qam "待部署的改动"
    git push -q origin main
    target="$(git rev-parse HEAD)"
    git reset -q --hard "$pre"
    printf '%s\n%s\n' "$pre" "$target" > "$tmp/shas" )
  pre="$(sed -n 1p "$tmp/shas")"; target="$(sed -n 2p "$tmp/shas")"

  deploy_run() {     # deploy_run <场景名> <期望结果> <期望结束时的提交> <被视为坏掉的提交>
    local name="$1" want="$2" want_sha="$3" bad="${4:-}" rc=0 out payload
    c_info "影子部署 · $name"
    rm -f "$repo/.deploy-state"
    # 每个场景都从"云端停在 pre"起步（上一个场景可能已经把仓库推进到了 target）
    git -C "$repo" reset -q --hard "$pre"
    payload="$(printf 'REMOTE_DIR=%q\nREMOTE_PY=%q\nBRANCH=main\nTARGET=%q\nCLOUD_DIRTY=clean\nNINJA_VER=0\nSTATE_FILE=%q\nHEALTH_BASE=http://127.0.0.1:9\n' \
      "$repo" "$bin/python-stub" "$target" "$repo/.deploy-state")"
    out="$(STUB_REPO="$repo" BAD_SHA="$bad" SUDO_FAIL="${SUDO_FAIL:-}" PATH="$bin:$PATH" \
      bash -s <<<"$payload
$S_DEPLOY")" || rc=$?
    printf '%s\n' "$out" | sed 's/^/  /'
    local got; got="$(printf '%s\n' "$out" | awk '/^DEPLOY_RESULT/{print $2}')"
    [ "$got" = "$want" ] || die "影子部署·$name：DEPLOY_RESULT 得到 '${got:-无}'，期望 '$want'"
    local at; at="$(git -C "$repo" rev-parse HEAD)"
    [ "$at" = "$want_sha" ] || die "影子部署·$name：结束时 HEAD 是 ${at:0:7}，期望 ${want_sha:0:7}（回滚没回到该回的地方）"
    [ -f "$repo/.deploy-state" ] || die "影子部署·$name：没有写 .deploy-state，下次 rollback 无从查起"
    printf '  ✔ 结果=%s  HEAD=%s  已记账：%s\n' "$got" "$(git -C "$repo" rev-parse --short HEAD)" \
      "$(tail -1 "$repo/.deploy-state" | cut -f4)"
  }

  # 1) 一切正常：留在 target，并记录 ok
  SUDO_FAIL="" deploy_run "正常路径" ok "$target" ""
  # 2) 起来了但不健康：自动退回 pre，退出码非 0
  SUDO_FAIL="" deploy_run "体检失败自动回滚" rolled_back "$pre" "$target"
  # 3) 连重启都没成功：中途退出也要退回 pre
  SUDO_FAIL="1" deploy_run "重启失败中途回滚" aborted "$pre" ""

  # 4) 回滚：站在 target 上，让 .deploy-state 里既有真实变更也有 aborted 原地记录，
  #    验证 rollback 退回的是"上一次真的动过代码"的那个提交，而不是多退一步
  c_info "影子回滚（含 aborted 干扰记录）"
  git -C "$repo" reset -q --hard "$target"
  printf '%s\t%s\t%s\tok\n'      "$(date -Is)" "$pre"    "$target" >  "$repo/.deploy-state"
  printf '%s\t%s\t%s\taborted\n' "$(date -Is)" "$target" "$target" >> "$repo/.deploy-state"
  out="$(STUB_REPO="$repo" BAD_SHA="" REMOTE_DIR="$repo" WANTED="" \
          STATE_FILE="$repo/.deploy-state" HEALTH_BASE=http://127.0.0.1:9 \
          PATH="$bin:$PATH" bash -s <<<"$S_ROLLBACK")" || die "影子回滚：脚本以非 0 退出"
  printf '%s\n' "$out" | sed 's/^/  /'
  at="$(git -C "$repo" rev-parse HEAD)"
  [ "$at" = "$pre" ] || die "影子回滚：停在 ${at:0:7}，应回到 ${pre:0:7}（aborted 记录把目标多退了一步）"
  printf '%s\n' "$out" | grep -q '^ROLLBACK_RESULT ok$' || die "影子回滚：结果不是 ok"

  c_ok "影子部署三种结局 + 回滚目标选择都符合预期"
}

# selftest：完全不碰云端，只验证三段远端脚本没被引号/here-doc 吃掉。
# 整段部署逻辑是以字符串下发的，这类损坏在本机能查出来，就不该拿到服务器上去撞。
cmd_selftest() {
  local name body marker
  for name in S_STATUS S_CHECK S_BACKUP S_DEPLOY S_ROLLBACK; do
    body="${!name}"
    printf '%s\n' "$body" | bash -n || die "$name 语法不通过"
    printf '  ✔ %-12s %s 行\n' "$name" "$(printf '%s' "$body" | wc -l)"
  done
  for marker in "Content-Type: application/json" "-d '{}'" "git merge --ff-only" \
                "ensurepip" "manage.py migrate --noinput" "collectstatic --noinput" \
                "systemctl restart blog" "reset --hard" "DEPLOY_RESULT"; do
    grep -qF -- "$marker" <<<"$S_DEPLOY" || die "S_DEPLOY 里找不到 \"$marker\"（字符串被截断了？）"
  done
  grep -qF 'src.backup(dst)' <<<"$S_BACKUP" || die "S_BACKUP 不再使用 sqlite 在线备份"
  grep -qF 'BACKUP_PATH' <<<"$S_BACKUP" || die "S_BACKUP 缺少回传备份路径"
  grep -qF 'ROLLBACK_RESULT' <<<"$S_ROLLBACK" || die "S_ROLLBACK 缺少结果标记"
  grep -qF 'is-active blog' <<<"$S_STATUS" || die "S_STATUS 缺少服务状态探测"
  grep -qF 'BAD=$((BAD + 1))' <<<"$S_CHECK" || die "S_CHECK 不再统计阻塞项"
  cmd_rehearse
  cmd_rehearse_deploy

  # dry-run 保护：YES=0 时任何 run_remote 都必须原样返回 2，一个字节都不下发
  local rc=0
  YES=0
  run_remote "自检用的假写入" 'echo 不该被执行' K=V || rc=$?
  [ "$rc" -eq 2 ] || die "dry-run 保护失效：run_remote 返回 $rc（应为 2，且不连接云端）"
  c_ok "远端脚本完整且 dry-run 保护有效（未连接云端）"
}

cmd_logs() {
  local n="${1:-40}"
  ssh_ro "sudo -n journalctl -u blog --no-pager -n $n"
}

# ── 参数 ──────────────────────────────────────────────────────────────────
CMD="${1:-status}"; shift || true
POSITIONAL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y)     YES=1 ;;
    --dry-run)    YES=0 ;;
    --skip-tests) RUN_TESTS=0 ;;
    --ref)        TARGET_REF="${2:?--ref 需要一个提交号或分支}"; shift ;;
    --server)     SERVER="${2:?--server 需要 ssh 别名}"; shift ;;
    --dir)        REMOTE_DIR="${2:?--dir 需要远端路径}"; REMOTE_PY="$REMOTE_DIR/.venv/bin/python"
                  STATE_FILE="$REMOTE_DIR/.deploy-state"; shift ;;
    --branch)     BRANCH="${2:?--branch 需要分支名}"; shift ;;
    -h|--help)    usage ;;
    -*)           die "未知参数：$1" ;;
    *)            POSITIONAL="$1" ;;
  esac
  shift
done
if [ -n "$POSITIONAL" ]; then TARGET_REF="$POSITIONAL"; fi

git rev-parse --git-dir >/dev/null 2>&1 || die "请在 blog 仓库目录里运行本脚本"

case "$CMD" in
  status)   cmd_status ;;
  check)    cmd_check ;;
  deploy)   cmd_deploy ;;
  backup)   cmd_backup_only ;;
  rollback) cmd_rollback ;;
  push)     cmd_push ;;
  logs)     cmd_logs "${POSITIONAL:-40}" ;;
  selftest) cmd_selftest ;;
  rehearse) cmd_rehearse ;;
  -h|--help) usage ;;          # 子命令位上写 --help 也算
  *)        c_err "未知子命令：$CMD（-h 看用法）"; usage 1 ;;
esac
