from django import forms
from django.utils.text import slugify

from .models import Category, Post, Memo, Series, Tag, UserProfile

# Reusable widget class string for text inputs
INPUT_CLASS = (
    "w-full bg-slate-100 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 "
    "rounded-xl px-4 py-3 text-slate-900 dark:text-slate-100 text-sm "
    "focus:border-cyan-600 dark:focus:border-cyan-500 focus:ring-1 "
    "focus:ring-cyan-600 dark:focus:ring-cyan-500 outline-none transition"
)

# For textareas (same as input but kept separate for future customization)
TEXTAREA_CLASS = INPUT_CLASS

# For select dropdowns
SELECT_CLASS = INPUT_CLASS


class SeriesForm(forms.ModelForm):
    class Meta:
        model = Series
        fields = ["name", "description"]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": INPUT_CLASS,
                "placeholder": "系列名称",
            }),
            "description": forms.Textarea(attrs={
                "class": TEXTAREA_CLASS,
                "rows": 3,
                "placeholder": "系列简介（可选）",
            }),
        }


class PostForm(forms.ModelForm):
    tag_names = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": INPUT_CLASS,
            "placeholder": "输入标签，空格确认……",
            "list": "tag-datalist",
            "autocomplete": "off",
        }),
    )

    class Meta:
        model = Post
        fields = ["title", "cover", "body", "excerpt", "category", "series", "license", "status"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": INPUT_CLASS,
                "placeholder": "文章标题",
            }),
            "cover": forms.URLInput(attrs={
                "class": INPUT_CLASS,
                "placeholder": "封面图片 URL（可选）",
            }),
            "body": forms.Textarea(attrs={
                "class": "w-full bg-slate-100 dark:bg-slate-900 border border-slate-300 dark:border-slate-700 "
                         "rounded-xl px-4 py-3 text-slate-900 dark:text-slate-100 text-sm font-mono "
                         "focus:border-cyan-600 dark:focus:border-cyan-500 focus:ring-1 "
                         "focus:ring-cyan-600 dark:focus:ring-cyan-500 outline-none transition",
                "rows": 20,
                "placeholder": "Markdown 格式正文...",
            }),
            "excerpt": forms.Textarea(attrs={
                "class": TEXTAREA_CLASS,
                "rows": 2,
                "placeholder": "文章摘要（留空则自动生成）",
            }),
            "category": forms.Select(attrs={
                "class": SELECT_CLASS,
            }),
            "series": forms.Select(attrs={
                "class": SELECT_CLASS,
            }),
            "license": forms.Select(attrs={
                "class": SELECT_CLASS,
            }),
            "status": forms.Select(attrs={
                "class": SELECT_CLASS,
            }),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if not self.instance or not self.instance.pk:
            self.fields["status"].initial = "published"
        if self.instance and self.instance.pk:
            self.fields["tag_names"].initial = ", ".join(
                self.instance.tags.values_list("name", flat=True)
            )
        if self.user:
            self.fields["category"].queryset = Category.objects.filter(author=self.user)
            self.fields["series"].queryset = Series.objects.filter(author=self.user)

    def _save_tags(self, instance):
        raw = self.cleaned_data.get("tag_names", "")
        names = [n.strip() for n in raw.replace(",", " ").split() if n.strip()]
        tags = []
        for name in names:
            slug = slugify(name, allow_unicode=True)
            tag, _ = Tag.objects.get_or_create(
                slug=slug, author=self.user, defaults={"name": name}
            )
            tags.append(tag)
        instance.tags.set(tags)

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save()
            self._save_tags(instance)
        return instance


class MemoForm(forms.ModelForm):
    class Meta:
        model = Memo
        fields = ["content", "is_public"]
        widgets = {
            "content": forms.Textarea(attrs={
                "class": TEXTAREA_CLASS,
                "rows": 4,
                "placeholder": "此刻的想法...",
            }),
            "is_public": forms.CheckboxInput(attrs={
                "class": "rounded bg-slate-100 dark:bg-slate-900 border-slate-300 dark:border-slate-700 "
                         "text-cyan-600 dark:text-cyan-500 focus:ring-cyan-600 dark:focus:ring-cyan-500",
            }),
        }


class CommentForm(forms.Form):
    guest_name = forms.CharField(
        label='昵称',
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={
            "class": INPUT_CLASS,
            "placeholder": "你的昵称",
        }),
    )
    guest_email = forms.EmailField(
        label='邮箱',
        max_length=254,
        required=False,
        widget=forms.EmailInput(attrs={
            "class": INPUT_CLASS,
            "placeholder": "邮箱（不会公开）",
        }),
    )
    content = forms.CharField(
        label='评论内容',
        min_length=2,
        max_length=2000,
        widget=forms.Textarea(attrs={
            "class": TEXTAREA_CLASS,
            "rows": 3,
            "placeholder": "写下你的评论...",
        }),
    )
    # Honeypot field — hidden from humans, filled by bots
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "hidden",
            "tabindex": "-1",
            "autocomplete": "off",
            "name": "website",
        }),
    )

    def __init__(self, *args, is_guest=False, **kwargs):
        self.is_guest = is_guest
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        if self.is_guest:
            guest_name = (cleaned_data.get('guest_name') or '').strip()
            guest_email = (cleaned_data.get('guest_email') or '').strip()
            if not guest_name:
                self.add_error('guest_name', '请输入昵称')
            if not guest_email:
                self.add_error('guest_email', '请输入邮箱')
        return cleaned_data


class UserProfileForm(forms.ModelForm):
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={
            "class": INPUT_CLASS,
            "placeholder": "邮箱地址（可选）",
        }),
    )
    github = forms.CharField(
        required=False,
        max_length=39,
        widget=forms.TextInput(attrs={
            "class": "w-full bg-slate-100 dark:bg-slate-900 py-3 px-4 text-slate-900 dark:text-slate-100 text-sm outline-none transition",
            "placeholder": "用户名",
        }),
    )

    class Meta:
        model = UserProfile
        fields = ["display_name", "title", "bio", "website", "github"]
        widgets = {
            "display_name": forms.TextInput(attrs={
                "class": INPUT_CLASS,
                "placeholder": "显示名称",
            }),
            "title": forms.TextInput(attrs={
                "class": INPUT_CLASS,
                "placeholder": "头衔 / 一句话介绍",
            }),
            "bio": forms.Textarea(attrs={
                "class": TEXTAREA_CLASS,
                "rows": 4,
                "placeholder": "个人简介...",
            }),
            "website": forms.URLInput(attrs={
                "class": INPUT_CLASS,
                "placeholder": "个人网站 URL（可选）",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.user:
            self.fields["email"].initial = self.instance.user.email
            self.fields["github"].initial = self.instance.github_username

    def clean_github(self):
        raw = self.cleaned_data.get("github", "").strip()
        # Strip full URL if pasted
        for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
            if raw.lower().startswith(prefix):
                raw = raw[len(prefix):]
        username = raw.lstrip("@").strip("/")
        return "https://github.com/" + username if username else ""

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit:
            user = instance.user
            user.email = self.cleaned_data.get("email", "")
            user.save(update_fields=["email"])
        return instance
