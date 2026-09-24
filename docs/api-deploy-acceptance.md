# /api/v1 上云前验收报告（本机 · 未触云）

日期：2026-09-23 ｜ 分支：`feature/api-ninja`（HEAD `8192662` + 本报告随附的收尾提交）
约束：`没有我明确同意，不得修改云端文件`。本轮全程成立——云端只被 `ssh` 读过，一个字节都没写过。

## 结论

django-ninja 版的 `/api/v1` 已经在本机做到"能直接给移动端联调"的程度，三层验证全绿：

| 层 | 命令 | 结果 |
| --- | --- | --- |
| 单元 / 集成（Django TestCase，造数据打视图） | `uv run python manage.py test` | **238 项通过**（其中 API 134 项） |
| 部署流水线彩排（离线，临时 git 仓库 + WAL 数据库） | `./sync-aliserver.sh selftest` | **全 ✔**，含备份可用性、影子部署三种结局、回滚目标选择、dry-run 保护 |
| 真 HTTP 端到端（起进程 + curl 走客户端全流程） | `./scripts/e2e_api_acceptance.sh` | **143 项通过 / 0 失败**，135 次 HTTP 请求，**0 个 5xx** |

上云本身只剩两条需要点头的命令（见最后一节）。目前云端仍是 `main @ 8863bd1`、`blog.service active`、`django-ninja 未安装`、`/api/v1/health → 404`——即"代码没上去"的真实状态，不是故障。

## 交付了什么

- **API 层**（`blog/api/`，2745 行）：`app.py` 装配、`auth.py` 三档鉴权、`errors.py` 统一错误、`serializers.py` 与站内同源的渲染/计数逻辑、8 个 router。
- **端点规模**：35 条路径 / 49 个操作。按标签：分类法 9、认证与令牌 7、文章 7、评论与点赞 6、账号 4、发现 4、碎碎念 4、收件箱 3、上传 3、站点 2。
- **鉴权**：站内「账号设置 → 访问令牌」或 `POST /api/v1/auth/login` 签发 PAT，明文 `mlc_` + 40 hex 只在签发时返回一次，库里只存 SHA-256（`blog_apitoken`，迁移 `blog.0016_apitoken`，纯增量建表）。三档：`OptionalBearerAuth` / `BearerAuth` / `SessionOrBearerAuth`（最后一档把 CSRF 检查加回来，因为 ninja 对整个 API 前缀免 CSRF）。
- **文档**：`/api/v1/docs`（Swagger）、`/api/v1/openapi.json`，仓库快照 `docs/openapi.json` 由 `manage.py export_openapi` 生成，并有测试盯着它别和代码漂移。
- **同步脚本** `sync-aliserver.sh`（753 行）：`status / check / backup / deploy / rollback / push / logs / rehearse / selftest`。三条铁律——无 `--yes` 不写云端；写之前先落远端 + 本机双备份并 sha256 校验；重启后健康检查不过自动退回部署前提交。
- **验收脚本** `scripts/e2e_api_acceptance.sh`（491 行，本轮新增，见下节）。

## 端到端验收怎么做的

单测能证明函数正确，证明不了"一个真实进程按 HTTP 语义服务时客户端会不会踩坑"。所以这轮刻意不复用测试夹具：

1. `cp db.sqlite3` 到 `/tmp/blog-e2e/<时刻>/e2e.sqlite3`，仓库里的库不动；
2. 在副本上 `migrate --noinput`——**这是故意复现云端上线那一刻的"老库 + 新代码"**，结果只多出 `blog_apitoken` 一张表，没有回填、没有破坏性操作；
3. 造一个 `e2e_bot` 账号（口令 `E2e-Pass-2026-x`）和它的 PAT；
4. `runserver 127.0.0.1:8011`，用 `DB_NAME` / `MEDIA_ROOT` 两个环境变量把库和上传目录都指到临时目录（`MEDIA_ROOT` 的环境变量入口本轮加进 `settings.py`，默认值不变，仓库 `media/` 不留孤儿文件）；
5. 用 curl 走完移动端会做的每一件事，逐条断言状态码 + JSON 形状 + 业务字段。

分组结果（转写记录里 ✔ 行数与计数器已核对一致）：

| 组 | 覆盖 | ✔ |
| --- | --- | --- |
| 1 匿名可读 | health/meta、文章列表/详情/排序、碎碎念、分类/标签/系列/归档、搜索、作者列表、全站最新评论、openapi/docs，以及 3 个站内页面与 API 给出的站内 URL 是否自洽 | 18 |
| 2 错误契约 | 401（未登录 / 坏令牌 / 令牌格式错）、404（不存在的文章、随机 UUID、未知端点、未知嵌套路径）、405；其中未知端点与 405 额外断言"必须是 JSON 契约且 405 带可用方法文案" | 12 |
| 3 认证两条路 | Web 表单登录 302 → 会话可用；缺/坏 CSRF 均 403；`/auth/login` 只发 PAT、不下发会话 Cookie；会话与令牌同时可用 | 14 |
| 4 文章写 | 建草稿 → 改 → 发布 → 匿名可读 → 浏览量指纹去重 → 别人的文章 403 | 20 |
| 5 碎碎念 | 公开/私密可见性、删除 | 11 |
| 6 评论与点赞 | 登录评论、访客评论进审核、蜜罐、重复 409、限流 429、收件箱 | 21 |
| 7 分类法 | 分类/标签/系列/邀请码/资料，含同名幂等 | 15 |
| 8 邀请码注册闭环 | 邀请码 → 新账号 → 新令牌 → 立即可用 | 10 |
| 9 上传 | 真 multipart PNG 上传 + 回读、同图去重（`dedup`）、非图片伪装拒 400、匿名 401、图片列表与头像上传 | 8 |
| 10 删除与吊销 | 删文章、软吊销令牌、登出后再取 401 | 9 |
| 11 收尾体检 | 站内首页/列表/用户空间仍正常，站内 404 页仍是 HTML，`/health` ok | 5 |
| | **合计** | **143** |

服务端访问日志的状态码分布：`200×75  201×11  404×12  401×11  422×7  403×5  202×5  429×3  409×2  400×2  405×1  302×1`，合计 135 次请求，5xx 为 0，stderr 为空。

复现与取证：

```bash
./scripts/e2e_api_acceptance.sh            # 退出码 0 == 全绿；PORT=8021 可换端口
# 产物：/tmp/blog-e2e/<时刻>/transcript.log（逐条 ✔ + 响应片段）、server.log
```

## 本轮发现的唯一真问题（已修）

现象：拼错 API 路径或方法用错时，返回的是**站内 HTML 404 页 / Django 默认 405 文本**，不是 JSON。
原因：ninja 的异常处理器只覆盖"已经进入某个视图"的请求；URL 解析阶段的失败根本不走它。移动端只按 `error.code` 分支，拿到 HTML 就得整段解析。
先试过在 `api/v1/` 末尾挂 `re_path` 兜底视图——未知路径管用了，但错误方法不行（405 由 ninja 在视图内部直接应答）。最终改成响应阶段的中间件：

- `blog/api/errors.py` 新增 `ApiUrlErrorShapeMiddleware`：只作用于 `/api/` 前缀，把非 JSON 的 404/405 换成统一形状，405 保留 `Allow` 头并把可用方法写进文案；已是 JSON 的响应（含业务 404）原样放过。
- `my_cosmos/settings.py`：中间件放最后一位（它只在响应阶段改形状，越早挂越容易被别人覆盖）。
- `blog/tests_api.py`：新增 `ApiUrlSpaceTests` 6 项，含"兜底不吃掉真实路由""站内 404 页不受影响"两条防误伤断言。
- `blog/api/app.py` 的 OpenAPI 描述与 `docs/openapi.json` 同步更新。

另外给 `sync-aliserver.sh` 收了一个小毛病：远端 bash 会因为转发过去的 `LC_ALL=zh_CN.UTF-8`（服务器没生成该 locale）刷 `setlocale` 警告，把 `status` 的输出弄脏；现在客户端侧 `env -u LC_ALL` 不再转发。

## 移动端集成须知（契约里反直觉的几处）

| 主题 | 实际行为 |
| --- | --- |
| 错误体 | 一律 `{"error":{"code","message"}}`，参数错误多带 `details`；本轮实测出现过的 `code`：`unauthorized` `forbidden` `not_found` `method_not_allowed` `validation_error` `throttled` `duplicate` `spam_detected` `invalid_credentials` `invalid_content_type` `invalid_image` `invite_not_found` `invite_used` `csrf_failed` |
| 限流 | 429 带 `Retry-After` 头；登录 5 次/300 秒、注册邀请码 3 次、评论 5 次（`COMMENT_RATE_LIMIT`） |
| 删除 | 返回 **200 + `{"ok":true}`**，不是 204 |
| 上传 | 恒 **200**（不是 201），复用已有文件时 `dedup: true` 且 URL 不变 |
| 分类/标签同名 | **200 幂等复用同一个 id**，不报 409 |
| 访客首条评论 | **202**（进审核队列），登录用户直接 201 |
| 文章定位 | 只认 slug 或 `unique_id`；**数字 id 一律 404**；跨作者同名 slug 用 `?author=<用户名>` 消歧，歧义且未消歧 → 409 `ambiguous_slug` |
| 点赞目标 | `content_type` 白名单只有 `blog.post` / `blog.memo`，越界 400 `invalid_content_type` |
| 令牌 | 明文只出现一次；列表含已吊销项（`revoked_at` 非空，软吊销留审计）；同时最多 10 枚 |
| 登录 | `POST /auth/login` **只发 PAT、不下发会话 Cookie**；浏览器会话走 Web 表单那条路，且写操作要带 CSRF 头 |
| 可见性 | 匿名只见已发布 / `is_public` 内容；带本人令牌可读自己的草稿与私密项 |

## 上云流程

云端事实（`status` 只读所见）：`aliserver:/home/admin/blog`，gunicorn 监听 `127.0.0.1:9999`，磁盘可用 9.6G、内存可用约 57–71 MiB，venv 有 `ensurepip` 但没有 `pip`/`uv`。

### 2026-09-24 首次真实部署：中断并自动回滚（已修）

`deploy --yes` 在"装完依赖后的探测"这一关失败，按铁律回滚：代码退回 `8863bd1` 并重启，`migrate` 与 `collectstatic` 都没执行，数据库一个字节没变，站点仍在旧版本上服务。**`django-ninja 1.7.1` 其实装成功了**——失败的是探测写法本身。

根因：脚本用 `"$REMOTE_PY" -c "import ninja"` 判断装没装，而 django-ninja 在导入期就读自己的配置（`ninja/conf.py` 顶层 `Settings.model_validate(django_settings)`）。`python -c` 里没有 `DJANGO_SETTINGS_MODULE`，于是 pydantic 抛 9 个 `ValidationError`，退出码非 0，被翻译成"装完仍然 import ninja 失败"。这是个**假阴性**：包在与不在都失败，本机从没暴露，是因为本机一直用 `manage.py test` 验证，从没单独跑那条探测；`selftest` 的影子部署里 python 是桩，也演不出"真实环境里探测方式不对"。

修法（三条都在 `sync-aliserver.sh`）：

1. 探测改成查已发行版本的元数据，不执行包代码——`importlib.metadata.version("django-ninja")`，`status` / `check` / `deploy` 三处共用同一个 `NINJA_PROBE`；装完后拿它比对 `uv.lock` 里的期望版本。
2. 真正的导入把关换成 `"$REMOTE_PY" manage.py check`，放在 `migrate` 与 `restart` **之前**：它有配置上下文，既验 ninja 也验本项目 `blog.api` 导得动，坏代码因此根本没机会去掀服务。
3. `selftest` 补两个场景（"依赖已在环境里"不再重复安装、"`manage.py check` 失败中途回滚"）与三条字符串守卫：任何一处重新出现裸 `import ninja`、或漏发 `NINJA_PROBE`，自检立刻红。

顺带的现象，看 `status` 时要知道：修好之前 `django-ninja:` 那一列会一直显示"未安装"，即使已经装上——同一个坏探测。

回滚还留下第二个坑：部署记账文件 `.deploy-state` 被写在仓库目录里、未被版本控制，于是"云端工作树干净"这条保护从此永久失败——**第一次部署自己的记录会挡住第二次部署**。已改成 `git status --porcelain -- . ':(exclude).deploy-state'`（`S_STATUS` 与 `cmd_deploy` 两处同规则，`.gitignore` 里也补了该文件名），记账文件另起一行如实显示但不参与干净与否；selftest 加了守卫，两处排除规则被改回去就红。

两处修好后对着真云端只读复核的结果：`云端工作树干净` + `部署记账 .deploy-state：1 条` + `django-ninja: 1.7.1` + `venv 内有 pip`（上次 ensurepip 自举出来的）+ `待部署提交可用`。


### 命令序列

```bash
git checkout main && git merge --ff-only feature/api-ninja   # 部署目标必须在 origin/main 这条线上
./sync-aliserver.sh push --yes                               # = git push -u origin main
./sync-aliserver.sh check                                    # 只读预检：本机测试 + 云端体检
./sync-aliserver.sh deploy --yes                             # 双备份 → 拉代码 → 装依赖 → migrate → collectstatic → 重启 → 健康检查
./sync-aliserver.sh logs 80                                  # 上线后观察
./sync-aliserver.sh rollback --yes                           # 需要时；健康检查失败会自动触发同样的回退
```

`deploy` 依次做：双备份（远端 + 本机，sha256 校验）→ `git merge --ff-only` → 依赖有变才 `python -m ensurepip` 自举 pip 并装 `django-ninja==1.7.1`（版本取自 `uv.lock`）→ **`manage.py check`（导入把关，跑在 migrate/restart 之前）** → `migrate --noinput`（按端到端验收的结果只新增 `blog_apitoken` 一张表）→ `collectstatic` → `systemctl restart blog` → 打 `/api/v1/health` 等公网体检。任一步失败都会把代码退回部署前的提交并重启，同时在 `.deploy-state` 记一行 `aborted` / `rolled_back`。已有备份：远端 `~/backups/20260923T165502` 与本机 `~/blog-backups/20260923T165502`（含 db、media、`.env`、systemd unit、依赖清单，带 SHA256SUMS），首次真实部署那次也在动代码之前另落了一份（时间戳见远端 `~/backups/` 与本机 `~/blog-backups/`）。

## 遗留风险（不阻塞，但上线前该知道）

- **限流按进程算**：`settings.py` 没配 `CACHES`，默认 LocMemCache 每进程一份。gunicorn 多 worker 时，登录/评论/API 的实际阈值是"配置值 × worker 数"，且 `Retry-After` 可能不准。上云后建议换 `django.core.cache.backends.db`（无需新依赖）或 redis。
- **内存余量小**：可用约 57–71 MiB。`collectstatic` 与 `migrate` 都没问题，但别在部署窗口里同时跑字体子集重建之类的重活。
- **静态 CSS 需手工重编译**：Tailwind v4 预编译，模板里新增工具类要跑 `./tailwindcss-cli ...`；本轮 API 未引入新 class，故未重编译（`templates/blog/base.html` 的改动与字体子集文件仍留在工作树，不属于本次交付，未纳入提交）。
- **测试会往仓库 `media/` 写头像**：Django 测试跑完留下 `alice_*/bob_*` 文件，是既有行为；端到端脚本已通过临时 `MEDIA_ROOT` 避开了这点，本轮清理了自己产生的 23 个孤儿文件（确认 `db.sqlite3` 无引用后才删）。
- **验收脚本是"跑一次绿一次"而非 CI 常驻**：它依赖本机 `uv`、`curl`、空闲端口，且会真实起服务。
