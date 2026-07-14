from .models import Memo

PROFILE_DATA = {
    "name": "Debris",
    "title": "数字矿工 / 泰拉旅人",
    "avatar_seed": "Debris",
    "bio": (
        "罗德岛（兼职），Obsidian 重度用户。"
        "学习底层工程，持续从原始文本中挖掘价值……"
    ),
    "stats": [
        {"icon": "🎮", "label": "当前项目", "value": "Django Blog v2", "color": ""},
        {"icon": "📡", "label": "服务器延迟", "value": "4ms (武汉-本地)", "color": "green"},
        {"icon": "📚", "label": "CET-4 备考", "value": "进行中 ⏳", "color": "amber"},
    ],
}


def profile(request):
    return {
        "profile": PROFILE_DATA,
        "latest_memo": Memo.objects.filter(is_public=True).first(),
    }
