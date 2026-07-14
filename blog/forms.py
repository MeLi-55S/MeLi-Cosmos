from django import forms
from django.utils.text import slugify

from .models import Post, Memo, Series, Tag


class SeriesForm(forms.ModelForm):
    class Meta:
        model = Series
        fields = ["name", "description"]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-slate-100 text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 outline-none transition",
                "placeholder": "系列名称",
            }),
            "description": forms.Textarea(attrs={
                "class": "w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-slate-100 text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 outline-none transition",
                "rows": 3,
                "placeholder": "系列简介（可选）",
            }),
        }


class PostForm(forms.ModelForm):
    """Custom form with manual tag input (comma-separated) and autocomplete."""
    tag_names = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-slate-100 text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 outline-none transition",
            "placeholder": "输入标签，空格确认……",
            "list": "tag-datalist",
            "autocomplete": "off",
        }),
    )

    class Meta:
        model = Post
        fields = ["title", "cover", "body", "excerpt", "category", "series", "series_order", "status"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-slate-100 text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 outline-none transition",
                "placeholder": "文章标题",
            }),
            "cover": forms.URLInput(attrs={
                "class": "w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-slate-100 text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 outline-none transition",
                "placeholder": "封面图片 URL（可选）",
            }),
            "body": forms.Textarea(attrs={
                "class": "w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-slate-100 text-sm font-mono focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 outline-none transition",
                "rows": 20,
                "placeholder": "Markdown 格式正文...",
            }),
            "excerpt": forms.Textarea(attrs={
                "class": "w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-slate-100 text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 outline-none transition",
                "rows": 2,
                "placeholder": "文章摘要（留空则自动生成）",
            }),
            "category": forms.Select(attrs={
                "class": "w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-slate-100 text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 outline-none transition",
            }),
            "series": forms.Select(attrs={
                "class": "w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-slate-100 text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 outline-none transition",
            }),
            "series_order": forms.NumberInput(attrs={
                "class": "w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-slate-100 text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 outline-none transition",
            }),
            "status": forms.Select(attrs={
                "class": "w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-slate-100 text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 outline-none transition",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-populate tag_names from existing tags on edit
        if self.instance and self.instance.pk:
            self.fields["tag_names"].initial = ", ".join(
                self.instance.tags.values_list("name", flat=True)
            )

    def _save_tags(self, instance):
        """Parse tag_names and attach/create Tag objects."""
        raw = self.cleaned_data.get("tag_names", "")
        # Split by spaces or commas
        names = [n.strip() for n in raw.replace(",", " ").split() if n.strip()]
        tags = []
        for name in names:
            slug = slugify(name, allow_unicode=True)
            tag, _ = Tag.objects.get_or_create(
                slug=slug, defaults={"name": name}
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
                "class": "w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-slate-100 text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 outline-none transition",
                "rows": 4,
                "placeholder": "此刻的想法...",
            }),
            "is_public": forms.CheckboxInput(attrs={
                "class": "rounded bg-slate-900 border-slate-700 text-cyan-500 focus:ring-cyan-500",
            }),
        }
