"""HTML- и Markdown-рендер из Confluence storage format.

Конвертирует storage-format фрагмент в автономную HTML-страницу (приближённую к
рендеру Confluence: таблицы/заголовки как есть, макросы info/note/warning/code —
стилизованные блоки) и в Markdown-документ. Тот же источник (шаблоны
storage-format), что и для Confluence, поэтому форматы не расходятся.

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


# --- Markdown -----------------------------------------------------------------
# Тот же источник (storage-format), что HTML и Confluence, поэтому Markdown с ними
# не расходится. Ограничения формата: строчная подсветка строк таблиц (sev-*),
# CSS-бейджи и т.п. в Markdown не переносятся — остаётся только их текст
# (✓/✗/emoji уже несут смысл).

_MACRO_LABELS = {
    "info": "ℹ️ Инфо",
    "note": "📝 Примечание",
    "tip": "💡 Совет",
    "warning": "⚠️ Внимание",
}


def _local(tag: str) -> str:
    """Локальное имя тега без namespace-префикса."""
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag


def _md_inline(el: ET.Element) -> str:
    """Строчное содержимое элемента (текст + строчные потомки) как Markdown."""
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(_md_inline_el(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _md_inline_el(el: ET.Element) -> str:
    tag = _local(el.tag)
    inner = _md_inline(el)
    if tag == "code":
        return f"`{inner}`"
    if tag in ("strong", "b"):
        return f"**{inner}**"
    if tag in ("em", "i"):
        return f"*{inner}*"
    if tag == "br":
        return " "
    if tag == "a":
        href = el.get("href", "")
        return f"[{inner}]({href})" if href else inner
    # span (бейджи), ac:*-строчные и прочее — только текст
    return inner


def _md_cell(text: str) -> str:
    """Строка для ячейки Markdown-таблицы: без переносов, с экранированием '|'."""
    return " ".join(text.split()).replace("|", "\\|")


def _md_table(el: ET.Element) -> str:
    rows: list[list[str]] = []
    for tr in el.iter("tr"):
        rows.append([_md_cell(_md_inline(c)) for c in tr])
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    first_tr = next(el.iter("tr"))
    cells = list(first_tr)
    # Обычная таблица: первая строка целиком из <th> → это заголовок.
    # key/value-таблица (<th>ключ</th><td>значение</td>) → заголовка нет.
    header_row = bool(cells) and all(_local(c.tag) == "th" for c in cells)
    header = rows[0] if header_row else [""] * ncol
    body = rows[1:] if header_row else rows
    out = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * ncol) + " |",
    ]
    out.extend("| " + " | ".join(r) + " |" for r in body)
    return "\n".join(out)


def _md_list(el: ET.Element, ordered: bool) -> str:
    out: list[str] = []
    for i, li in enumerate(el.findall("li"), start=1):
        marker = f"{i}." if ordered else "-"
        out.append(f"{marker} {_md_inline(li).strip()}")
    return "\n".join(out)


def _md_macro(el: ET.Element) -> str:
    name = el.get(_q(AC, "name"))
    if name == "code":
        body = el.find(_q(AC, "plain-text-body"))
        text = (body.text if body is not None else "") or ""
        return f"```\n{text}\n```"
    body = el.find(_q(AC, "rich-text-body"))
    inner = _md_blocks(body) if body is not None else ""
    label = _MACRO_LABELS.get(name or "", name or "")
    quoted = "\n".join(("> " + ln) if ln else ">" for ln in inner.split("\n"))
    return f"> **{label}**\n>\n{quoted}"


def _md_block(el: ET.Element) -> str:
    tag = _local(el.tag)
    if tag == _local(_q(AC, "structured-macro")):
        return _md_macro(el)
    if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
        return "#" * int(tag[1]) + " " + _md_inline(el).strip()
    if tag == "p":
        return " ".join(_md_inline(el).split())
    if tag == "table":
        return _md_table(el)
    if tag in ("ul", "ol"):
        return _md_list(el, ordered=(tag == "ol"))
    if tag == "hr":
        return "---"
    # неизвестный контейнер — попробуем как последовательность блоков
    nested = _md_blocks(el)
    return nested or _md_inline(el).strip()


def _md_blocks(container: ET.Element) -> str:
    blocks = [b for b in (_md_block(el) for el in container) if b]
    return "\n\n".join(blocks)


def storage_to_markdown(storage: str, title: str) -> str:
    """Storage format → Markdown-документ."""
    wrapped = f'<root xmlns:ac="{AC}" xmlns:ri="{RI}">{storage}</root>'
    root = ET.fromstring(wrapped)
    return f"# {title}\n\n{_md_blocks(root)}\n"
