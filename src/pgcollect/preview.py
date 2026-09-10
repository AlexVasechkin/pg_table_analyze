"""HTML-препросмотр Confluence storage format.

Конвертирует storage-format фрагмент в автономную HTML-страницу, приближённую к
рендеру Confluence: таблицы/заголовки проходят как есть, макросы info/note/
warning/code превращаются в стилизованные блоки. Тот же источник (шаблоны
storage-format), что и для Confluence, поэтому HTML и Confluence не расходятся.

Портировано из ../doc-gen/src/pgdoc/preview.py.
"""

from __future__ import annotations

import html
import xml.etree.ElementTree as ET

AC = "http://atlassian.com/content"
RI = "http://atlassian.com/resource/identifier"


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _inner(el: ET.Element) -> str:
    parts: list[str] = []
    if el.text:
        parts.append(html.escape(el.text))
    for child in el:
        parts.append(_conv(child))
        if child.tail:
            parts.append(html.escape(child.tail))
    return "".join(parts)


def _macro(el: ET.Element) -> str:
    name = el.get(_q(AC, "name"))
    if name in ("info", "note", "warning", "tip"):
        body = el.find(_q(AC, "rich-text-body"))
        return f'<div class="cf-{name}">{_inner(body) if body is not None else ""}</div>'
    if name == "code":
        body = el.find(_q(AC, "plain-text-body"))
        text = (body.text if body is not None else "") or ""
        return f'<pre class="cf-code"><code>{html.escape(text)}</code></pre>'
    return _inner(el)


def _attrs(el: ET.Element) -> str:
    """Сериализовать не-неймспейсные атрибуты (class/colspan и т.п.)."""
    parts = []
    for k, v in el.attrib.items():
        if k.startswith("{"):  # ac:/ri: атрибуты в HTML не нужны
            continue
        parts.append(f' {k}="{html.escape(v, quote=True)}"')
    return "".join(parts)


def _conv(el: ET.Element) -> str:
    tag = el.tag
    if tag == _q(AC, "structured-macro"):
        return _macro(el)
    if tag.startswith("{"):  # прочие ac:/ri: элементы — только содержимое
        return _inner(el)
    return f"<{tag}{_attrs(el)}>{_inner(el)}</{tag}>"  # обычный XHTML-тег


def storage_to_fragment(storage: str) -> str:
    """Storage format → HTML-фрагмент."""
    wrapped = f'<root xmlns:ac="{AC}" xmlns:ri="{RI}">{storage}</root>'
    root = ET.fromstring(wrapped)
    return _inner(root)


_CSS = """
body { font: 14px/1.5 -apple-system, Segoe UI, Roboto, sans-serif; color: #172b4d;
       max-width: 1100px; margin: 24px auto; padding: 0 16px; }
h1,h2,h3,h4 { color: #172b4d; }
h2 { border-bottom: 2px solid #dfe1e6; padding-bottom: 4px; margin-top: 32px; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }
th,td { border: 1px solid #dfe1e6; padding: 5px 9px; text-align: left; vertical-align: top; }
th { background: #f4f5f7; }
.cf-info,.cf-note,.cf-warning,.cf-tip { border: 1px solid #dfe1e6; border-left: 4px solid #4c9aff;
       background: #deebff; padding: 8px 12px; margin: 12px 0; border-radius: 3px; }
.cf-warning { border-left-color: #ffab00; background: #fffae6; }
.cf-note { border-left-color: #6554c0; background: #eae6ff; }
pre.cf-code { background: #f4f5f7; border: 1px solid #dfe1e6; padding: 10px; overflow: auto; }
code { background: #f4f5f7; padding: 1px 4px; border-radius: 3px; }
pre.cf-code code { background: none; padding: 0; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px;
         font-weight: 600; color: #fff; }
.badge.ok { background: #36b37e; } .badge.warn { background: #ff991f; }
.badge.crit { background: #de350b; } .badge.unknown { background: #97a0af; }
tr.sev-crit > td { background: #ffebe6; }
tr.sev-crit > td:first-child { box-shadow: inset 4px 0 0 #de350b; }
tr.sev-warn > td { background: #fffae6; }
tr.sev-warn > td:first-child { box-shadow: inset 4px 0 0 #ff991f; }
"""

_DOC = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>{title}</title>
<style>{css}</style>
</head><body><h1>{title}</h1>
{body}
</body></html>"""


def storage_to_html(storage: str, title: str) -> str:
    """Storage format → автономная HTML-страница для просмотра в браузере."""
    return _DOC.format(title=html.escape(title), css=_CSS, body=storage_to_fragment(storage))
