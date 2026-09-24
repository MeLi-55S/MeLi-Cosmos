#!/usr/bin/env bash
# =============================================================================
# e2e_api_acceptance.sh — /api/v1 的真 HTTP 端到端验收（143 项断言）
#
# 用法：  ./scripts/e2e_api_acceptance.sh            # 全跑，退出码 0 == 全绿
#          PORT=8021 ./scripts/e2e_api_acceptance.sh  # 换端口（8011 被占时）
#
# 它证明的是单测证明不了的那一段：一个真的 gunicorn 之外的进程、真的 HTTP、
# 真的 multipart 上传、真的限流计时器，按移动端客户端的调用顺序走一遍。
#
# 安全边界（三条，都是硬编码的）：
#   1. 数据库是 db.sqlite3 的**副本**，落在 /tmp；仓库里的库一个字节都不改。
#   2. 上传的图片写进临时 MEDIA_ROOT（settings 支持 MEDIA_ROOT 环境变量覆盖），
#      不在仓库 media/ 下留孤儿文件。
#   3. 全程只连 127.0.0.1，绝不 ssh、绝不碰云端。
#
# 为什么要在副本上跑 migrate：云端上线时的状态就是"老库 + 新代码"，
# 只有复现它才能验证 blog.0016_apitoken 是纯增量、不会要求回填数据。
#
# 产物：$TMP/<时分秒>/transcript.log（逐条 ✔/✘ + 响应片段）、server.log（HTTP 访问日志）
#       末尾打印 "通过 N 项，失败 M 项"；✔ 行数与计数器必然一致。
# =============================================================================
# 对着一个只存在于 /tmp 的数据库副本跑 runserver，用 curl 走完移动端会做的每一件事。
set -uo pipefail
REPO="${REPO:-/home/meli/blog}"
TMP="${TMP:-/tmp/blog-e2e}"
PORT="${PORT:-8011}"
cd "$REPO" || { echo "仓库不在 $REPO（用 REPO= 指定）" >&2; exit 2; }

BASE="http://127.0.0.1:$PORT"
WORK=$TMP/$(date +%H%M%S)
mkdir -p "$WORK"
DB="$WORK/e2e.sqlite3"
MEDIA="$WORK/media"   # 上传的图片也留在 /tmp，不脏仓库的 media/（settings 支持 MEDIA_ROOT 覆盖）
JAR="$WORK/cookies.txt"
LOG="$WORK/transcript.log"
exec > >(tee -a "$LOG") 2>&1   # 唯一事实来源：✔ 行数 == PASS 计数器
PASS=0; FAIL=0

say()   { printf '%s\n' "$*" ; }
head_() { printf '\n\033[36m── %s\033[0m\n' "$*"  >&2; }

# step <名称> <期望HTTP码> <curl 参数...>
step() {
  local name="$1" want="$2"; shift 2
  local code
  code="$(curl -sS -o "$WORK/resp.json" -w '%{http_code}' "$@" 2>>"$WORK/err.txt")" || code="000"
  if [ "$code" = "$want" ]; then
    PASS=$((PASS+1)); printf '  \033[32m✔\033[0m %-46s %s\n' "$name" "$code" 
  else
    FAIL=$((FAIL+1)); printf '  \033[31m✘\033[0m %-46s 得到 %s 期望 %s\n' "$name" "$code" "$want" 
  fi
  printf '     ← %s\n' "$(head -c 200 "$WORK/resp.json" | tr -d '\n')" 
}

# step_any <名称> "200 201" <curl 参数...>：允许一组可接受的状态码
step_any() {
  local name="$1" wants="$2"; shift 2
  local code
  code="$(curl -sS -o "$WORK/resp.json" -w '%{http_code}' "$@" 2>>"$WORK/err.txt")" || code="000"
  case " $wants " in
    *" $code "*) PASS=$((PASS+1))
      printf '  \033[32m✔\033[0m %-46s %s（可接受: %s）\n' "$name" "$code" "$wants"  ;;
    *) FAIL=$((FAIL+1))
      printf '  \033[31m✘\033[0m %-46s 得到 %s，期望其中之一: %s\n' "$name" "$code" "$wants"  ;;
  esac
  printf '     ← %s\n' "$(head -c 200 "$WORK/resp.json" | tr -d '\n')" 
}

# 取响应体里的字段：json "['token']" 或 json '["user"]["username"]'
# check <名称> <对响应体 d 的 python 表达式> [额外参数…，表达式里写成 A[0]/A[1]]
check() {
  local name="$1" expr="$2"; shift 2
  python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as e:
    print("  \033[31m✘\033[0m " + sys.argv[3] + " ← 响应不是 JSON: " + repr(e))
    sys.exit(1)
A = sys.argv[4:]
ok = bool(eval(sys.argv[2], {"d": d, "A": A, "json": json, "re": __import__("re")}))
print(("  \033[32m✔\033[0m " if ok else "  \033[31m✘\033[0m ") + sys.argv[3])
print("     ← " + json.dumps(d, ensure_ascii=False)[:200])
sys.exit(0 if ok else 1)
' "$WORK/resp.json" "$expr" "$name" "$@" 2>>"$WORK/err.txt" 
  if [ "${PIPESTATUS[0]}" -eq 0 ]; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); fi
}

json() {
  python3 -c 'import json,sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
try:
    print(eval("d" + sys.argv[2]))
except Exception:
    sys.exit(1)
' "$WORK/resp.json" "$1" 2>/dev/null; }

# ── 准备：数据库副本 + 迁移 + 验收账号 ─────────────────────────────────
cp db.sqlite3 "$DB"
say "数据库副本：$DB（$(du -h "$DB" | cut -f1)）"

head_ "给副本补迁移（复现云端上线时的「老库 + 新代码」，只增量长出 ApiToken 表）"
DB_NAME="$DB" MEDIA_ROOT="$MEDIA" uv run python manage.py migrate --noinput 2>&1  | tail -3
DB_NAME="$DB" MEDIA_ROOT="$MEDIA" uv run python manage.py shell -c "
from django.db import connection
t = [x for x in connection.introspection.table_names() if 'apitoken' in x]
print('迁移后可见的新表:', t)
assert t, 'ApiToken 表没建出来'
" 2>&1 | tail -1 

DB_NAME="$DB" MEDIA_ROOT="$MEDIA" uv run python manage.py shell -c "
from blog import models as m
m.generate_default_avatar = lambda *a, **k: None
from django.contrib.auth.models import User
u = User.objects.filter(username='e2e_bot').first() or User.objects.create(username='e2e_bot', is_active=True)
u.set_password('E2e-Pass-2026-x'); u.save()
p = m.Post.objects.filter(status='published').first()
print('验收账号就绪:', u.username, 'id=', u.id)
print('测试目标文章:', p.unique_id, p.slug, p.title[:24])
" 2>&1  | tail -2

head_ "启动本机验收服务器（DB 指向副本）"
DB_NAME="$DB" MEDIA_ROOT="$MEDIA" uv run python manage.py runserver "127.0.0.1:$PORT" --noreload --skip-checks \
  >"$WORK/server.log" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT   # 副本与转写留在 /tmp 供事后查看
for _ in $(seq 1 60); do curl -s -o /dev/null "$BASE/api/v1/health" && break; sleep 0.5; done

POST_REF="$(DB_NAME="$DB" MEDIA_ROOT="$MEDIA" uv run python manage.py shell -c "
from blog.models import Post; print(Post.objects.filter(status='published').first().unique_id)" 2>/dev/null | tail -1)"
say "已有文章 unique_id = $POST_REF"
BOT_ID="$(DB_NAME="$DB" MEDIA_ROOT="$MEDIA" uv run python manage.py shell -c "
from django.contrib.auth.models import User; print(User.objects.get(username='e2e_bot').pk)" 2>/dev/null | tail -1)"
say "验收账号 id = $BOT_ID"

J='Content-Type: application/json'

head_ "1. 匿名可读（移动端首屏）"
step "GET  /api/v1/health"           200 "$BASE/api/v1/health"
step "GET  /api/v1/meta"             200 "$BASE/api/v1/meta"
step "GET  /api/v1/posts"            200 "$BASE/api/v1/posts?page_size=3"
step "GET  /api/v1/posts/{ref}"      200 "$BASE/api/v1/posts/$POST_REF"
POST_WEB_URL="$(json "['url']")"
step "GET  /api/v1/posts?q=&order="    200 "$BASE/api/v1/posts?q=Django&order=views&page_size=2"
step "GET  /api/v1/memos"            200 "$BASE/api/v1/memos"
step "GET  /api/v1/categories"       200 "$BASE/api/v1/categories"
step "GET  /api/v1/tags"             200 "$BASE/api/v1/tags"
step "GET  /api/v1/series"           200 "$BASE/api/v1/series"
step "GET  /api/v1/archives"         200 "$BASE/api/v1/archives"
step "GET  /api/v1/search?q=Django"  200 "$BASE/api/v1/search?q=Django"
step "GET  /api/v1/users"            200 "$BASE/api/v1/users"
step "GET  /api/v1/comments 全站最新" 200 "$BASE/api/v1/comments"
step "GET  /api/v1/openapi.json"     200 "$BASE/api/v1/openapi.json"
step "GET  /api/v1/docs"             200 "$BASE/api/v1/docs"
step "GET  / (站内首页)"              200 "$BASE/"
step "GET  /posts/ (文章列表)"        200 "$BASE/posts/"
step "GET  API 给出的站内文章 URL"    200 "$POST_WEB_URL"

head_ "2. 错误契约（移动端要靠这些码分支）"
step "404 不存在的文章"              404 "$BASE/api/v1/posts/999999"
step "404 随机 UUID"                 404 "$BASE/api/v1/posts/11111111-1111-1111-1111-111111111111"
step "401 未登录写文章"              401 -X POST "$BASE/api/v1/posts" -H "$J" -d '{"title":"x"}'
step "401 未登录发碎碎念"            401 -X POST "$BASE/api/v1/memos" -H "$J" -d '{}'
step "401 坏令牌"                    401 -H "Authorization: Bearer mlc_0000000000000000000000000000000000000000" "$BASE/api/v1/memos"
step "401 格式错的令牌"              401 -H "Authorization: Bearer garbage" "$BASE/api/v1/memos"
step "401 收件箱未登录"              401 "$BASE/api/v1/inbox"
step "404 未知端点"                  404 "$BASE/api/v1/nope"
check "未知端点给 JSON 契约（不是站内 HTML 404 页）" \
  "d['error']['code'] == 'not_found' and '/api/v1/nope' in d['error']['message']"
step "405 方法不允许"                405 -X DELETE "$BASE/api/v1/health"
check "405 同样带 JSON 契约，并在文案里给出可用方法" \
  "d['error']['code'] == 'method_not_allowed' and 'GET' in d['error']['message']"
step "404 未知嵌套路径"              404 "$BASE/api/v1/posts/x/likes/nope"

head_ "3. 认证两条路：浏览器会话（CSRF）与 API 令牌"
# 3a 会话路线：站内页面用的就是这条，Cookie + CSRF
curl -s -c "$JAR" "$BASE/accounts/login/" -o "$WORK/login.html"
CSRF="$(grep csrftoken "$JAR" | awk '{print $NF}')"
say "  登录页 CSRF：${CSRF:0:8}…"
step "Web 表单登录 → 302"            302 -b "$JAR" -c "$JAR" -X POST "$BASE/accounts/login/" \
  -H 'Content-Type: application/x-www-form-urlencoded' -H "X-CSRFToken: $CSRF" \
  -e "$BASE/accounts/login/" \
  --data-urlencode "username=e2e_bot" --data-urlencode "password=E2e-Pass-2026-x"
CSRF="$(grep csrftoken "$JAR" | awk '{print $NF}')"; say "  登录后 CSRF 轮换为 ${CSRF:0:8}…"
step "GET  /auth/me（会话）"         200 -b "$JAR" "$BASE/api/v1/auth/me"
step "POST /auth/tokens 缺 CSRF→403" 403 -b "$JAR" -X POST "$BASE/api/v1/auth/tokens" \
  -H "$J" -d '{"name":"缺 token 的伪装请求"}'
step "POST /auth/tokens 坏 CSRF→403" 403 -b "$JAR" -X POST "$BASE/api/v1/auth/tokens" \
  -H "$J" -H "X-CSRFToken: deadbeef" -e "$BASE/accounts/login/" -d '{"name":"伪造来源"}'
step "POST /auth/tokens（带 CSRF）"  201 -b "$JAR" -X POST "$BASE/api/v1/auth/tokens" \
  -H "$J" -H "X-CSRFToken: $CSRF" -e "$BASE/accounts/login/" \
  -d '{"name":"e2e 验收令牌"}'
RAW="$(json "['token']")"; TOKEN_ID="$(json "['id']")"
say "  明文令牌：${RAW:0:12}…（只显示一次，长度 ${#RAW}）"
B="Authorization: Bearer $RAW"

# 3b 令牌路线：移动端只靠用户名+密码换 PAT，服务端刻意不建会话
step "POST /auth/login 错误密码"     401 -X POST "$BASE/api/v1/auth/login" -H "$J" \
  -d '{"username":"e2e_bot","password":"wrong-password"}'
step "POST /auth/login 正确密码"     200 -D "$WORK/login.h" -X POST "$BASE/api/v1/auth/login" -H "$J" \
  -d '{"username":"e2e_bot","password":"E2e-Pass-2026-x","device_name":"e2e 验收机"}'
API_TOKEN="$(json "['token']")"; API_TOKEN_ID="$(json "['id']")"
python3 -u - "$WORK/login.h" <<'PY'
import re, sys
hdr = open(sys.argv[1]).read().lower()
sess = re.findall(r"set-cookie:[^\n]*sessionid", hdr)
print(("  \033[32m✔\033[0m" if not sess else "  \033[31m✘\033[0m"),
      "API 登录响应不下发会话 Cookie（设计：只发 PAT）", sess or "")
sys.exit(0 if not sess else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
step "API 登录的令牌即刻可用"       200 -H "Authorization: Bearer $API_TOKEN" "$BASE/api/v1/auth/me"
python3 -u - "$WORK/resp.json" "$API_TOKEN" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
tok = d.get("token") or {}
ok = d.get("username") == "e2e_bot" and tok.get("prefix") == sys.argv[2][:12]
print(("  \033[32m✔\033[0m" if ok else "  \033[31m✘\033[0m"), "/auth/me 带出本次令牌元信息:", tok)
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
step "GET  /auth/tokens 列表（Bearer）" 200 -H "$B" "$BASE/api/v1/auth/tokens"
python3 -u - "$WORK/resp.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
names = [(t["id"], t["name"], t["prefix"], bool(t["revoked_at"])) for t in d["results"]]
ok = d["count"] == len(names) == 2
print(("  \033[32m✔\033[0m" if ok else "  \033[31m✘\033[0m"), "两条路各一枚令牌:", names)
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
step "GET  /auth/me（Bearer）"       200 -H "$B" "$BASE/api/v1/auth/me"
step "会话与令牌同时可用"            200 -b "$JAR" -H "$B" "$BASE/api/v1/auth/me"

head_ "4. 写：文章 → 发布 → 浏览量"
step "422 文章缺标题"                422 -H "$B" -H "$J" -X POST "$BASE/api/v1/posts" -d '{"body":"没有标题"}'
step "422 非法 status"               422 -H "$B" -H "$J" -X POST "$BASE/api/v1/posts" \
  -d '{"title":"x","body":"y","status":"published_now"}'
step "POST /posts 草稿"              201 -H "$B" -H "$J" -X POST "$BASE/api/v1/posts" \
  -d '{"title":"端到端验收文章","body":"# 验收\n\n这是 curl 写进来的正文，**支持 Markdown**。","excerpt":"草稿","status":"draft"}'
NEW_REF="$(json "['unique_id']")"; NEW_ID="$(json "['id']")"; NEW_SLUG="$(json "['slug']")"
say "  新文章 id=$NEW_ID unique_id=$NEW_REF slug=$NEW_SLUG"
step "GET  自己的草稿（带令牌可见）" 200 -H "$B" "$BASE/api/v1/posts/$NEW_REF"
step "404 匿名看草稿"                404 "$BASE/api/v1/posts/$NEW_REF"
step "正文已渲染为 HTML"             200 -H "$B" "$BASE/api/v1/posts/$NEW_REF"
python3 -u - "$WORK/resp.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
ok = "<strong>支持 Markdown</strong>" in d.get("body_html", "")
print(("  \033[32m✔\033[0m" if ok else "  \033[31m✘\033[0m"), "body_html 里出现 <strong>（Markdown+净化链路生效）")
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
step "404 用数字 id 取文章（设计上只认 ref）" 404 -H "$B" "$BASE/api/v1/posts/$NEW_ID"
step "PATCH /posts/{ref} 改正文"     200 -H "$B" -H "$J" -X PATCH "$BASE/api/v1/posts/$NEW_REF" \
  -d '{"body":"# 验收\n\n改过的正文。","category_id":null}'
step "POST /posts/{ref}/publish"     200 -H "$B" -X POST "$BASE/api/v1/posts/$NEW_REF/publish"
step "匿名可读已发布"                200 "$BASE/api/v1/posts/$NEW_SLUG"
step "PATCH 别人的文章→403"          403 -H "$B" -H "$J" -X PATCH "$BASE/api/v1/posts/$POST_REF" \
  -d '{"title":"改别人的文章"}'
step "POST /posts/{ref}/view"        200 -H "$J" -X POST "$BASE/api/v1/posts/$NEW_REF/view" -d '{"fingerprint":"e2e-fp-1"}'
step "POST /view 重复指纹不再计数"   200 -H "$J" -X POST "$BASE/api/v1/posts/$NEW_REF/view" -d '{"fingerprint":"e2e-fp-1"}'
step "GET  /posts/{ref} 带出浏览量"  200 -H "$B" "$BASE/api/v1/posts/$NEW_REF"
python3 -u - "$WORK/resp.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
ok = d.get("views") == 1
print(("  \033[32m✔\033[0m" if ok else "  \033[31m✘\033[0m"), "浏览量字段 views =", d.get("views"), "（同指纹两次应为 1）")
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
step "GET  /posts?mine=true（带令牌）" 200 -H "$B" "$BASE/api/v1/posts?mine=true"
step "403 /posts?mine=true 无令牌"      403 "$BASE/api/v1/posts?mine=true"
step "GET  /posts?mine&status=draft"    200 -H "$B" "$BASE/api/v1/posts?mine=true&status=draft"
step "GET  /posts/{ref}?author=消歧" 200 "$BASE/api/v1/posts/$NEW_SLUG?author=e2e_bot"

head_ "5. 写：碎碎念（含私密）"
step "422 碎碎念缺 content"          422 -H "$B" -H "$J" -X POST "$BASE/api/v1/memos" -d '{}'
step "POST /memos 公开"              201 -H "$B" -H "$J" -X POST "$BASE/api/v1/memos" \
  -d '{"content":"端到端验收：公开的碎碎念","is_public":true}'
MEMO_ID="$(json "['id']")"
step "POST /memos 私密"              201 -H "$B" -H "$J" -X POST "$BASE/api/v1/memos" \
  -d '{"content":"端到端验收：只有我自己看得到","is_public":false}'
PRIV_ID="$(json "['id']")"
step "GET  /memos/{id}（本人带令牌）" 200 -H "$B" "$BASE/api/v1/memos/$PRIV_ID"
step "404 匿名看别人私密"            404 "$BASE/api/v1/memos/$PRIV_ID"
step "匿名列表不含他人私密"          200 "$BASE/api/v1/memos?page_size=100"
python3 -u - "$WORK/resp.json" "$PRIV_ID" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
leaked = any(m["id"] == int(sys.argv[2]) for m in d["results"])
print(("  \033[32m✔\033[0m" if not leaked else "  \033[31m✘\033[0m"), "私密碎碎念泄漏给匿名:", leaked, "（必须 False）")
sys.exit(0 if not leaked else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
step "带令牌列表能看到自己的私密"    200 -H "$B" "$BASE/api/v1/memos?page_size=100"
python3 -u - "$WORK/resp.json" "$PRIV_ID" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
seen = any(m["id"] == int(sys.argv[2]) for m in d["results"])
print(("  \033[32m✔\033[0m" if seen else "  \033[31m✘\033[0m"), "本人令牌列表含私密:", seen, "（必须 True）")
sys.exit(0 if seen else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
step "DELETE /memos/{id}"            200 -H "$B" -X DELETE "$BASE/api/v1/memos/$MEMO_ID"
step "404 删除后再取"                404 -H "$B" "$BASE/api/v1/memos/$MEMO_ID"

head_ "6. 评论与点赞（含访客审核、蜜罐、限流、收件箱）"
step "POST /comments 登录用户"       201 -H "$B" -H "$J" -X POST "$BASE/api/v1/comments" \
  -d "{\"content_type\":\"blog.post\",\"object_id\":$NEW_ID,\"content\":\"验收评论：来自 API\"}"
CMT_ID="$(json "['id']")"
step_any "POST /comments 访客首评→待审" "201 202" -H "$J" -X POST "$BASE/api/v1/comments" \
  -d "{\"content_type\":\"blog.post\",\"object_id\":$NEW_ID,\"content\":\"访客也能评论\",\"guest_name\":\"验收访客\",\"guest_email\":\"guest@example.com\"}"
python3 -u - "$WORK/resp.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("  访客评论 is_pending =", d.get("is_pending"), "is_visible =", d.get("is_visible"))
PY
step "422 访客缺昵称"                422 -H "$J" -X POST "$BASE/api/v1/comments" \
  -d "{\"content_type\":\"blog.post\",\"object_id\":$NEW_ID,\"content\":\"匿名的访客发言\"}"
step "422 蜜罐 website 非空"         422 -H "$J" -X POST "$BASE/api/v1/comments" \
  -d "{\"content_type\":\"blog.post\",\"object_id\":$NEW_ID,\"content\":\"机器人\",\"guest_name\":\"spam\",\"website\":\"http://bet365.com\"}"
step "409 30 秒内重复评论"           409 -H "$B" -H "$J" -X POST "$BASE/api/v1/comments" \
  -d "{\"content_type\":\"blog.post\",\"object_id\":$NEW_ID,\"content\":\"验收评论：来自 API\"}"
step "400 类型白名单外的目标"        400 -H "$B" -H "$J" -X POST "$BASE/api/v1/comments" \
  -d "{\"content_type\":\"auth.user\",\"object_id\":$BOT_ID,\"content\":\"用户不是可评论的对象\"}"
step "422 评论内容过短"              422 -H "$B" -H "$J" -X POST "$BASE/api/v1/comments" \
  -d "{\"content_type\":\"blog.post\",\"object_id\":$NEW_ID,\"content\":\"x\"}"
step "GET  /comments?post 分页"      200 "$BASE/api/v1/comments?content_type=blog.post&object_id=$NEW_ID"
step "GET  /comments?mine=true"      200 -H "$B" "$BASE/api/v1/comments?mine=true"
step "POST /likes 点赞"              200 -H "$B" -H "$J" -X POST "$BASE/api/v1/likes" \
  -d "{\"content_type\":\"blog.post\",\"object_id\":$NEW_ID}"
python3 -u - "$WORK/resp.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
ok = d.get("liked") is True and d.get("count") == 1 and d.get("display_text")
print(("  \033[32m✔\033[0m" if ok else "  \033[31m✘\033[0m"), "点赞返回:", d)
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
step "POST /likes 再点取消"          200 -H "$B" -H "$J" -X POST "$BASE/api/v1/likes" \
  -d "{\"content_type\":\"blog.post\",\"object_id\":$NEW_ID}"
step "401 未登录不能点赞"            401 -H "$J" -X POST "$BASE/api/v1/likes" \
  -d "{\"content_type\":\"blog.post\",\"object_id\":$NEW_ID}"
step "POST /likes 打到碎碎念"        200 -H "$B" -H "$J" -X POST "$BASE/api/v1/likes" \
  -d "{\"content_type\":\"blog.memo\",\"object_id\":$PRIV_ID}"
step "GET  /inbox（作者收到通知）"   200 -H "$B" "$BASE/api/v1/inbox"
INBOX_N="$(json "['count']")"; say "  收件箱条数：$INBOX_N"
step "GET  /inbox/unread"            200 -H "$B" "$BASE/api/v1/inbox/unread"
step "POST /inbox/read 标记全部已读" 200 -H "$B" -H "$J" -X POST "$BASE/api/v1/inbox/read" -d '{"mark_all":true}'
step "GET  /unread 归零"             200 -H "$B" "$BASE/api/v1/inbox/unread"
python3 -u - "$WORK/resp.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
ok = d.get("count", d.get("unread")) in (0, None)
print(("  \033[32m✔\033[0m" if ok else "  \033[31m✘\033[0m"), "未读数：", d)
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
say "  连发访客评论直到触发限流（COMMENT_RATE_LIMIT）："
for i in $(seq 1 6); do
  curl -sS -o "$WORK/resp.json" -w "%{http_code}\n" -X POST "$BASE/api/v1/comments" -H "$J" \
    -d "{\"content_type\":\"blog.post\",\"object_id\":$NEW_ID,\"content\":\"限流探针 $i\",\"guest_name\":\"探针\",\"guest_email\":\"probe$i@example.com\"}" \
    >>"$WORK/rate.txt"
done
tail -2 "$WORK/rate.txt" 
grep -q '^429$' "$WORK/rate.txt" && { printf '  \033[32m✔\033[0m %s\n' "限流按预期返回 429"; PASS=$((PASS+1)); } \
  || { printf '  \033[31m✘ %s\033[0m\n' "未触发 429"; FAIL=$((FAIL+1)); }
CODE="$(curl -sS -o "$WORK/resp.json" -D "$WORK/h.txt" -w '%{http_code}' -X POST "$BASE/api/v1/comments" -H "$J" \
  -d "{\"content_type\":\"blog.post\",\"object_id\":$NEW_ID,\"content\":\"限流探针 7\",\"guest_name\":\"探针\",\"guest_email\":\"probe7@example.com\"}")"
say "  限流后的响应码：$CODE"
printf '     ← %s\n' "$(head -c 200 "$WORK/resp.json" | tr -d '\n')" 
if [ "$CODE" = "429" ] && grep -qi '^retry-after:' "$WORK/h.txt"; then
  printf '  \033[32m✔\033[0m %s\n' "429 带 Retry-After：$(grep -i '^retry-after' "$WORK/h.txt" | tr -d '\r')"
  PASS=$((PASS+1))
else
  printf '  \033[31m✘ %s\033[0m\n' "期望 429 + Retry-After，实际 $CODE / $(grep -ci '^retry-after:' "$WORK/h.txt") 个头"
  FAIL=$((FAIL+1))
fi

head_ "7. 分类法 / 系列 / 邀请码 / 资料"
step "422 分类缺 name"               422 -H "$B" -H "$J" -X POST "$BASE/api/v1/categories" -d '{}'
step "POST /categories"              201 -H "$B" -H "$J" -X POST "$BASE/api/v1/categories" -d '{"name":"端到端验收分类"}'
CAT_ID="$(json "['id']")"
step "POST /tags"                    201 -H "$B" -H "$J" -X POST "$BASE/api/v1/tags" -d '{"name":"端到端验收标签"}'
step "POST /series"                  201 -H "$B" -H "$J" -X POST "$BASE/api/v1/series" -d '{"name":"端到端验收系列","description":"curl 建的"}'
SER_ID="$(json "['id']")"
step "POST /series/{id}/posts 挂文章" 200 -H "$B" -H "$J" -X POST "$BASE/api/v1/series/$SER_ID/posts" \
  -d "{\"action\":\"add\",\"post_id\":$NEW_ID}"
step "GET  /series/{id}/posts"       200 "$BASE/api/v1/series/$SER_ID/posts"
step "200 重复名分类→复用既有项"      200 -H "$B" -H "$J" -X POST "$BASE/api/v1/categories" -d '{"name":"端到端验收分类"}'
check "同名分类返回同一 id（幂等复用而非报错）" "str(d.get('id')) == str(A[0])" "$CAT_ID"
step "PATCH 文章挂分类+标签"         200 -H "$B" -H "$J" -X PATCH "$BASE/api/v1/posts/$NEW_REF" \
  -d "{\"category_id\":$CAT_ID,\"tag_names\":[\"端到端验收标签\",\"新建标签\"]}"
step "GET  /categories 带出计数"     200 "$BASE/api/v1/categories"
step "POST /account/invites 领邀请码" 201 -H "$B" -X POST "$BASE/api/v1/account/invites" -H "$J" -d '{}'
INVITE="$(json "['code']")"; say "  邀请码：$INVITE"
step "GET  /account/invites"         200 -H "$B" "$BASE/api/v1/account/invites"
step "PATCH /account/profile"        200 -H "$B" -H "$J" -X PATCH "$BASE/api/v1/account/profile" \
  -d '{"display_name":"验收机器人","bio":"这条 bio 来自端到端验收"}'
step "GET  /account/profile"         200 -H "$B" "$BASE/api/v1/account/profile"
step "GET  /users/{username}"        200 "$BASE/api/v1/users/e2e_bot"

head_ "8. 邀请码注册 → 新账号令牌（移动端注册闭环）"
step_any "422/409 无效邀请码注册" "400 404 409 422" -H "$J" -X POST "$BASE/api/v1/auth/register" \
  -d '{"username":"e2e_guest","password1":"Gu3st-Pass-2026-x","password2":"Gu3st-Pass-2026-x","code":"NOPE0000"}'
step "201 用刚领到的邀请码注册"  201 -H "$J" -X POST "$BASE/api/v1/auth/register" \
  -d "{\"username\":\"e2e_guest\",\"password1\":\"Gu3st-Pass-2026-x\",\"password2\":\"Gu3st-Pass-2026-x\",\"code\":\"$INVITE\",\"device_name\":\"e2e 注册测试\"}"
GUEST_RAW="$(json "['token']")"; GUEST_USER="$(json "['user']['username']")"
say "  新账号 e2e_guest 直接拿到令牌：${GUEST_RAW:0:12}…（username=$GUEST_USER）"
step "GB 用新令牌读 /auth/me"     200 -H "Authorization: Bearer $GUEST_RAW" "$BASE/api/v1/auth/me"
python3 -u - "$WORK/resp.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
ok = d.get("username") == "e2e_guest" and d.get("token")
print(("  \033[32m✔\033[0m" if ok else "  \033[31m✘\033[0m"), "/auth/me 认得出新账号，且带出当前令牌元信息")
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
step_any "409 邀请码重复使用"      "400 404 409 422" -H "$J" -X POST "$BASE/api/v1/auth/register" \
  -d "{\"username\":\"e2e_guest2\",\"password1\":\"Gu3st-Pass-2026-x\",\"password2\":\"Gu3st-Pass-2026-x\",\"code\":\"$INVITE\"}"
step "新账号看不到 e2e_bot 的私密碎碎念" 404 -H "Authorization: Bearer $GUEST_RAW" "$BASE/api/v1/memos/$PRIV_ID"
step "新账号可评论（未过审→待审）" 201 -H "Authorization: Bearer $GUEST_RAW" -H "$J" -X POST "$BASE/api/v1/comments" \
  -d "{\"content_type\":\"blog.post\",\"object_id\":$NEW_ID,\"content\":\"新注册的账号也来评论\"}"
step "GET  /comments/{id}"         200 -H "Authorization: Bearer $GUEST_RAW" "$BASE/api/v1/comments/$CMT_ID"
step "GET  /series/{id}"           200 "$BASE/api/v1/series/$SER_ID"
step "403 非作者不能改他人评论可见性" 403 -H "Authorization: Bearer $GUEST_RAW" -H "$J" \
  -X POST "$BASE/api/v1/comments/$CMT_ID/visibility" -d '{"visible":false}'

head_ "9. 上传（multipart，真文件）"
python3 -u - "$WORK/px.png" <<'PY'
import struct, zlib, sys
def chunk(t, d):
    return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
raw = b"\x00" + b"\x40\x80\xc0" * 4
png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
       + chunk(b"IDAT", zlib.compress(raw * 4)) + chunk(b"IEND", b""))
open(sys.argv[1], "wb").write(png)
PY
step "POST /uploads/image"           200 -H "$B" -X POST "$BASE/api/v1/uploads/image" -F "image=@$WORK/px.png;type=image/png"
UP_URL="$(json "['url']")"; say "  图片 URL：$UP_URL"
step "GET  上传回来的图片"           200 "$BASE$UP_URL"
step "POST /uploads/image 同图去重"  200 -H "$B" -X POST "$BASE/api/v1/uploads/image" -F "image=@$WORK/px.png;type=image/png"
check "同图二次上传：dedup=True 且复用同一 URL" "d.get('dedup') is True and d.get('url') == A[0]" "$UP_URL"
step_any "非图片文件伪装成图片被拒"  "400 413 415" -H "$B" -X POST "$BASE/api/v1/uploads/image" \
  -F "image=@$WORK/e2e.sqlite3;type=image/png"
step "401 匿名上传"                  401 -X POST "$BASE/api/v1/uploads/image" -F "image=@$WORK/px.png;type=image/png"
step "GET  /uploads/images"          200 -H "$B" "$BASE/api/v1/uploads/images"
step "POST /uploads/avatar"          200 -H "$B" -X POST "$BASE/api/v1/uploads/avatar" -F "image=@$WORK/px.png;type=image/png"

head_ "10. 删除文章 → 令牌吊销 → 登出"
step "DELETE /posts/{ref}"           200 -H "$B" -X DELETE "$BASE/api/v1/posts/$NEW_REF"
step "404 删除后再取"                404 -H "$B" "$BASE/api/v1/posts/$NEW_REF"
step "DELETE /auth/tokens/{id}"      200 -H "$B" -X DELETE "$BASE/api/v1/auth/tokens/$TOKEN_ID"
step "401 已吊销令牌不能再用"        401 -H "$B" "$BASE/api/v1/memos"
step "GET  /auth/tokens 列表（会话）" 200 -b "$JAR" "$BASE/api/v1/auth/tokens"
python3 -u - "$WORK/resp.json" <<'PY'
import json, re, sys
raw = open(sys.argv[1]).read()
d = json.load(open(sys.argv[1]))
leaked = re.findall(r"mlc_[0-9a-f]{40}", raw)
rev = [t for t in d.get("results", []) if t.get("revoked_at")]
ok = not leaked and all("token" not in t and "hash" not in t for t in d.get("results", []))
print(("  \033[32m✔\033[0m" if ok else "  \033[31m✘\033[0m"),
      "列表里既无明文也无哈希；已吊销项:", rev, "（软吊销，保留审计）")
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
step "POST /auth/logout（会话）"     200 -b "$JAR" -X POST "$BASE/api/v1/auth/logout" \
  -H "X-CSRFToken: $CSRF" -e "$BASE/accounts/login/"
step "401 登出后 /auth/me"           401 "$BASE/api/v1/auth/me"
step "401 登出后写文章"              401 -H "$J" -X POST "$BASE/api/v1/posts" -d '{"title":"x"}'

head_ "11. 收尾体检"
step "站内首页仍然正常"               200 "$BASE/"
step "/posts/ 列表页仍然正常"          200 "$BASE/posts/"
step "/@e2e_bot/ 用户空间"               200 "$BASE/@e2e_bot/"
step "站内 404 页仍是 HTML（未被 API 污染）" 404 "$BASE/no-such-page/"
step "health 仍然 ok"                200 "$BASE/api/v1/health"
say ""
say "服务器日志里的报错（应为空，除刻意触发的 4xx/5xx 契约测试外）："
grep -iE "traceback|internal server error|OperationalError|DoesNotExist" "$WORK/server.log" | head -10 
say "  server.log 行数：$(grep -c "" "$WORK/server.log" 2>/dev/null || echo 0)"
say ""
say "验收结果：通过 $PASS 项，失败 $FAIL 项"
say "转写记录：$LOG"
[ "$FAIL" -eq 0 ]
