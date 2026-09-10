"""Рендер модели БД в Confluence storage format (XHTML) через Jinja2.

Адаптация ../doc-gen/src/pgdoc/render.py. Один шаблон `database.xml.j2` со
всеми разделами; HTML-препросмотр получается из того же storage-format через
preview.storage_to_html.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .models import Database

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_NAME = "database.xml.j2"


def _dash(value: object) -> object:
    """Пустые значения показываем как тире."""
    if value is None or value == "":
        return "—"
    return value


def build_env(templates_dir: str | Path | None = None) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(templates_dir or TEMPLATES_DIR)),
        autoescape=True,      # значения экранируются, разметка шаблона — нет
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["d"] = _dash
    return env


def render_database(db: Database, templates_dir: str | Path | None = None) -> str:
    """Вернуть storage-format разметку страницы для одной БД."""
    env = build_env(templates_dir)
    template = env.get_template(TEMPLATE_NAME)
    return template.render(db=db)
