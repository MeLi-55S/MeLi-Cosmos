"""Admin Dashboard — statistics, appeals, user tree."""
import time
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.models import User
from django.db import connection
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from blog.models import Post, Memo, Like, Comment, BanAppeal, InviteCode, ViewLog


def _build_user_tree():
    edges = defaultdict(list)
    codes = InviteCode.objects.filter(is_used=True).exclude(invitee=None).select_related('inviter', 'invitee')
    all_invitees = set()
    for c in codes:
        edges[c.inviter_id].append(c.invitee_id)
        all_invitees.add(c.invitee_id)

    roots = [uid for uid in edges if uid not in all_invitees]
    users_map = {u.id: u for u in User.objects.filter(
        id__in=set(edges) | all_invitees
    ).select_related('profile')}

    return edges, roots, users_map


def _render_tree_node(uid, edges, users_map, is_root=True):
    import html
    if uid not in users_map:
        return ''
    u = users_map[uid]
    name = html.escape(u.profile.display_name or u.username)
    ban = u.profile.is_banned
    children = edges.get(uid, [])

    # Status pill
    if u.profile.is_permanent_ban:
        pill = '<span class="pill pill-perm">永久</span>'
    elif ban:
        pill = '<span class="pill pill-temp">已封</span>'
    else:
        pill = '<span class="pill pill-ok">正常</span>'

    has_children = len(children) > 0
    li_class = 'tree-root' if is_root else ''

    html = f'<li class="{li_class}">'
    html += '<div class="tree-node">'

    if has_children:
        html += '<button class="tree-toggle" aria-label="展开/折叠" onclick="this.parentElement.parentElement.classList.toggle(\'collapsed\')">▼</button>'
    else:
        html += '<span class="tree-toggle-spacer"></span>'

    html += f'<span class="font-mono text-sm font-bold text-slate-800 dark:text-slate-200">@{u.username}</span>'
    if name != u.username:
        html += f'<span class="text-xs text-slate-400 ml-1.5">({name})</span>'
    html += pill

    html += '</div>'

    if has_children:
        html += '<ul class="tree">'
        for child_id in children:
            html += _render_tree_node(child_id, edges, users_map, is_root=False)
        html += '</ul>'
    html += '</li>'
    return html


@staff_member_required
def dashboard(request):
    start = time.monotonic()
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    db_latency_ms = round((time.monotonic() - start) * 1000, 2)

    total_users = User.objects.count()
    total_posts = Post.objects.count()
    published_posts = Post.objects.filter(status='published').count()
    draft_posts = Post.objects.filter(status='draft').count()
    total_memos = Memo.objects.count()
    total_likes = Like.objects.count()
    total_comments = Comment.objects.count()
    total_views = Post.objects.aggregate(s=Sum('views'))['s'] or 0

    week_ago = timezone.now() - timedelta(days=7)
    posts_7d = Post.objects.filter(created_time__gte=week_ago).count()
    views_7d = ViewLog.objects.filter(created_at__gte=week_ago).count()
    users_7d = User.objects.filter(date_joined__gte=week_ago).count()

    appeals = BanAppeal.objects.filter(is_resolved=False).select_related('user').order_by('-created_at')

    edges, roots, users_map = _build_user_tree()
    tree_html = '<ul class="tree">'
    for root_id in sorted(roots):
        tree_html += _render_tree_node(root_id, edges, users_map, is_root=True)
    tree_html += '</ul>'

    import platform as pm
    context = {
        'nav_section': 'dashboard',
        'server': {
            'hostname': getattr(settings, 'SERVER_DISPLAY_NAME', '') or pm.node(),
            'python_version': pm.python_version(),
            'db_latency_ms': db_latency_ms,
            'db_engine': connection.settings_dict['ENGINE'].split('.')[-1],
        },
        'stats': {
            'total_users': total_users,
            'total_posts': total_posts,
            'published_posts': published_posts,
            'draft_posts': draft_posts,
            'total_memos': total_memos,
            'total_likes': total_likes,
            'total_comments': total_comments,
            'total_views': total_views,
            'posts_7d': posts_7d,
            'views_7d': views_7d,
            'users_7d': users_7d,
        },
        'appeals': appeals,
        'appeal_count': appeals.count(),
        'tree_html': tree_html,
        'tree_user_count': len(users_map),
        'orphan_count': User.objects.exclude(id__in=users_map).filter(is_superuser=False).count(),
    }
    return render(request, 'dashboard/index.html', context)
