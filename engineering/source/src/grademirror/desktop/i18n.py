"""Pure presentation text. No Qt, image, project or compute dependencies."""
import json
import re
from pathlib import Path

ENGLISH = json.loads(Path(__file__).with_name('translations_en.json').read_text(encoding='utf-8'))
_language = 'zh_CN'


def set_language(language):
    global _language
    if language not in ('zh_CN', 'en'):
        raise ValueError('Unsupported UI language')
    _language = language


def render(value):
    if isinstance(value, Text):
        if value.parts is not None:
            return ''.join(render(part) for part in value.parts)
        template = ENGLISH.get(value.source, value.source) if _language == 'en' else value.source
        if value.args or value.kwargs:
            return template.format(*(render(v) if isinstance(v,Text) else v for v in value.args),
                **{k:render(v) if isinstance(v,Text) else v for k,v in value.kwargs.items()})
        return template
    return str(value)


class Text(str):
    """A rendered string retaining its template and unmodified user values."""
    def __new__(cls, source='', args=(), kwargs=None, *, parts=None):
        # Render through a temporary node, then keep the source for live updates.
        node = str.__new__(cls, '')
        node.source, node.args, node.kwargs, node.parts = source, args, kwargs or {}, parts
        result = str.__new__(cls, render(node))
        result.source, result.args, result.kwargs, result.parts = node.source,node.args,node.kwargs,node.parts
        return result

    def __add__(self, other):
        if not isinstance(other,str):return NotImplemented
        return Text(parts=(self,other))

    def __radd__(self, other):
        if not isinstance(other,str):return NotImplemented
        return Text(parts=(other,self))

    def __str__(self):
        return render(self)

    def __format__(self, spec):
        return format(render(self),spec)

    def join(self, values):
        parts=[]
        for value in values:
            if parts:parts.append(self)
            parts.append(value)
        return Text(parts=tuple(parts))


def tr(source, *args, **kwargs):
    if isinstance(source,Text) and not args and not kwargs:return source
    return Text(source,args,kwargs)


def source_text(value):
    return value.source if isinstance(value,Text) and value.parts is None else str(value)


def geometry_text(issue):
    """Localize the existing validator's explanation, without repeating its rules."""
    positive=re.fullmatch(r'(原图|参考图)尺寸须为正整数像素',issue)
    if positive:return tr('{0}尺寸须为正整数像素',tr(positive[1]))
    match=re.fullmatch(r'(原图|参考图) (\d+) × (\d+)（([\d,]+) 像素）超出精细模式的(.+)',issue)
    if not match:return issue
    constraints=[]
    for part in match[5].split('、'):
        pixel=re.fullmatch(r'像素数上限 ([\d,]+)',part)
        axis=re.fullmatch(r'最长边上限 ([\d,]+) 像素',part)
        constraints.append(tr('像素数上限 {0}',pixel[1]) if pixel else
            tr('最长边上限 {0} 像素',axis[1]) if axis else part)
    return tr('{0} {1} × {2}（{3} 像素）超出精细模式的{4}',
        tr(match[1]),match[2],match[3],match[4],tr('、').join(constraints))


def external_text(message):
    """Translate known engine message formats; keep unknown technical text intact."""
    if isinstance(message,Text):return message
    patterns=[
        (r'图片 (\d+) × (\d+) 超出上限 ([\d,]+) 像素（2.1 亿）',
         '图片 {0} × {1} 超出上限 {2} 像素（2.1 亿）'),
        (r'此尺寸预计需要至少 ([\d.]+) (?:GiB|GB) 可用内存，当前 ([\d.]+) (?:GiB|GB)。请关闭占用内存的程序后重试。',
         '此尺寸预计需要至少 {0} GiB 可用内存，当前 {1} GiB。请关闭占用内存的程序后重试。'),
        (r'图片 (\d+) × (\d+)，最长边至少需要 512 像素',
         '图片 {0} × {1}，最长边至少需要 512 像素'),
        (r'原图 (\d+) × (\d+)，参考图 (\d+) × (\d+)。请使用同一张照片、相同构图比例的参考图。',
         '原图 {0} × {1}，参考图 {2} × {3}。请使用同一张照片、相同构图比例的参考图。'),
    ]
    for pattern,template in patterns:
        match=re.fullmatch(pattern,message)
        if match:return tr(template,*match.groups())
    return tr(message)
