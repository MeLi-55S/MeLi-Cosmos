"""把 ``/api/v1`` 的 OpenAPI 3.1 规范导出到文件，供移动端离线生成客户端代码。

用法::

    uv run python manage.py export_openapi                # 写 docs/openapi.json
    uv run python manage.py export_openapi --stdout       # 只打印
    uv run python manage.py export_openapi --output x.json
    uv run python manage.py export_openapi --server https://example.com

规范由 ``blog.api.app.api`` 现生成，永远和代码一致；``docs/openapi.json``
只是随仓库发布的快照，改动 API 后请重新跑一遍本命令。

线上 ``/api/v1/openapi.json`` 的 ``servers`` 由 django-ninja 按请求主机现填，
离线生成时拿不到请求，所以这里显式写两个入口，避免快照里留着占位域名。
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from blog.api.app import api

DEFAULT_SERVERS = [
    {"url": "http://127.0.0.1:8000", "description": "本地开发（runserver）"},
    {"url": "https://blog.melichem.cn", "description": "线上站点"},
]


class Command(BaseCommand):
    help = "导出 REST API 的 OpenAPI 3.1 规范（默认 docs/openapi.json）"

    def add_arguments(self, parser):
        parser.add_argument("--output", default="docs/openapi.json",
                            help="输出路径，相对于仓库根目录")
        parser.add_argument("--stdout", action="store_true",
                            help="只打印到标准输出，不写文件")
        parser.add_argument("--server", action="append", dest="servers",
                            help="覆盖 servers 列表（可重复），例如 --server https://api.example.com")

    def handle(self, *args, **options):
        schema = api.get_openapi_schema(path_prefix="api/v1")
        schema["servers"] = (
            [{"url": url} for url in options["servers"]] if options["servers"]
            else DEFAULT_SERVERS
        )
        payload = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)

        if options["stdout"]:
            self.stdout.write(payload)
            return

        path = Path(options["output"])
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")

        paths = schema.get("paths", {})
        operations = sum(1 for item in paths.values()
                         for key in item if key in ("get", "post", "patch", "put", "delete"))
        self.stdout.write(self.style.SUCCESS(
            f"已写出 {path.relative_to(Path.cwd())}："
            f"{len(paths)} 条路径 / {operations} 个操作"
        ))
        self.stdout.write("在线文档：/api/v1/docs")

