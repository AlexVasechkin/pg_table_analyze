"""Рендер модели Server в Confluence storage format через Jinja2.

Переиспользует фильтр `d` и подход из pgcollect.render; HTML/Markdown получаются
из того же storage-format через pgcollect.preview (форматы не расходятся).
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .glossary import GLOSSARY
from .models import Server
from .sql.settings import CONNECTION_SETTINGS, QUERY_PARALLEL_SETTINGS

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_NAME = "server.xml.j2"


def _dash(value: object) -> object:
    if value is None or value == "":
        return "—"
    return value


def build_env(templates_dir: str | Path | None = None) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(templates_dir or TEMPLATES_DIR)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["d"] = _dash
    # Тест для selectattr/rejectattr: разделение настроек по префиксу имени
    # (напр. autovacuum* в отдельную таблицу).
    env.tests["startingwith"] = lambda value, prefix: str(value).startswith(prefix)
    return env


def render_server(srv: Server, templates_dir: str | Path | None = None) -> str:
    env = build_env(templates_dir)
    return env.get_template(TEMPLATE_NAME).render(
        srv=srv, CONNECTION_SETTINGS=CONNECTION_SETTINGS,
        QUERY_PARALLEL_SETTINGS=QUERY_PARALLEL_SETTINGS, GLOSSARY=GLOSSARY)
