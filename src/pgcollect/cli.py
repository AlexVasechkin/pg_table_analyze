"""CLI: pgcollect collect | render | preview | markdown | all.

collect  — подключается к кластеру и пишет out/<db>.json (сырые собранные данные).
render   — out/<db>.json → out/<db>.confluence.xml (Confluence storage format).
preview  — out/<db>.json → out/<db>.html (автономный HTML для браузера).
markdown — out/<db>.json → out/<db>.md (Markdown-отчёт).
all      — collect + render + preview + markdown за один прогон.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from .collector import collect_database
from .config import Config, LoadError, Target, load_config
from .db import connect, enumerate_databases
from .models import Database
from .preview import storage_to_html, storage_to_markdown
from .render import render_database

app = typer.Typer(
    add_completion=False,
    help="pgcollect — сбор метаданных PostgreSQL и генерация документации.",
)

DEFAULT_CONFIG = Path("targets.yaml")
DEFAULT_OUT = Path("out")


def _load(config: Path) -> Config:
    try:
        return load_config(config)
    except LoadError as exc:
        typer.secho(f"Ошибка: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


def _target(cfg: Config, name: str) -> Target:
    t = cfg.get(name)
    if t is None:
        available = ", ".join(x.name for x in cfg.targets)
        typer.secho(
            f"Цель '{name}' не найдена. Доступны: {available}",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(1)
    return t


def _databases(target: Target, only: Optional[str]) -> list[str]:
    with connect(target, target.maintenance_db) as conn:
        dbs = enumerate_databases(conn, target)
    if only:
        dbs = [d for d in dbs if d == only]
        if not dbs:
            typer.secho(f"БД '{only}' не найдена в кластере", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
    return dbs


@app.command()
def collect(
    target: str = typer.Argument(..., help="Имя цели из targets.yaml."),
    config: Path = typer.Option(DEFAULT_CONFIG, "--config", "-c", help="Файл целей."),
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Каталог артефактов."),
    db: Optional[str] = typer.Option(None, "--db", help="Только одна БД."),
    deep_bloat: bool = typer.Option(False, "--deep-bloat", help="Точный pgstattuple по всем таблицам."),
    skip: list[str] = typer.Option([], "--skip", help="Пропустить раздел (roles/bloat/…)."),
    stat_statements: bool = typer.Option(False, "--stat-statements", help="Собрать топ запросов из pg_stat_statements."),
) -> None:
    """Собрать метаданные и записать out/<db>.json."""
    cfg = _load(config)
    tgt = _target(cfg, target)
    out.mkdir(parents=True, exist_ok=True)
    skipset = set(skip)

    for dbname in _databases(tgt, db):
        typer.echo(f"[collect] {dbname} …")
        data = collect_database(tgt, dbname, deep_bloat=deep_bloat, skip=skipset,
                                stat_statements=stat_statements)
        path = out / f"{dbname}.json"
        path.write_text(data.dump_json(), encoding="utf-8")
        typer.echo(f"→ {path}")
        if data.warnings:
            for w in data.warnings:
                typer.secho(f"  ! {w}", fg=typer.colors.YELLOW)

    typer.secho("Сбор завершён.", fg=typer.colors.GREEN)


@app.command()
def render(
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Каталог артефактов."),
    db: Optional[str] = typer.Option(None, "--db", help="Только одна БД."),
) -> None:
    """out/<db>.json → out/<db>.confluence.xml (Confluence storage format)."""
    for data, path in _iter_json(out, db):
        target = out / f"{data.name}.confluence.xml"
        target.write_text(render_database(data), encoding="utf-8")
        typer.echo(f"→ {target}")
    typer.secho("Готово.", fg=typer.colors.GREEN)


@app.command()
def preview(
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Каталог артефактов."),
    db: Optional[str] = typer.Option(None, "--db", help="Только одна БД."),
) -> None:
    """out/<db>.json → out/<db>.html (автономный HTML)."""
    for data, path in _iter_json(out, db):
        title = f"{data.name} — {data.target}"
        html_doc = storage_to_html(render_database(data), title)
        target = out / f"{data.name}.html"
        target.write_text(html_doc, encoding="utf-8")
        typer.echo(f"→ {target}")
    typer.secho("Готово. Откройте файлы в браузере.", fg=typer.colors.GREEN)


@app.command()
def markdown(
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Каталог артефактов."),
    db: Optional[str] = typer.Option(None, "--db", help="Только одна БД."),
) -> None:
    """out/<db>.json → out/<db>.md (Markdown-отчёт)."""
    for data, path in _iter_json(out, db):
        title = f"{data.name} — {data.target}"
        target = out / f"{data.name}.md"
        target.write_text(storage_to_markdown(render_database(data), title), encoding="utf-8")
        typer.echo(f"→ {target}")
    typer.secho("Готово.", fg=typer.colors.GREEN)


@app.command()
def all(
    target: str = typer.Argument(..., help="Имя цели из targets.yaml."),
    config: Path = typer.Option(DEFAULT_CONFIG, "--config", "-c", help="Файл целей."),
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Каталог артефактов."),
    db: Optional[str] = typer.Option(None, "--db", help="Только одна БД."),
    deep_bloat: bool = typer.Option(False, "--deep-bloat", help="Точный pgstattuple по всем таблицам."),
    skip: list[str] = typer.Option([], "--skip", help="Пропустить раздел."),
    stat_statements: bool = typer.Option(False, "--stat-statements", help="Собрать топ запросов из pg_stat_statements."),
) -> None:
    """collect + render + preview + markdown за один прогон."""
    cfg = _load(config)
    tgt = _target(cfg, target)
    out.mkdir(parents=True, exist_ok=True)
    skipset = set(skip)

    for dbname in _databases(tgt, db):
        typer.echo(f"[all] {dbname} …")
        data = collect_database(tgt, dbname, deep_bloat=deep_bloat, skip=skipset,
                                stat_statements=stat_statements)
        (out / f"{dbname}.json").write_text(data.dump_json(), encoding="utf-8")
        storage = render_database(data)
        (out / f"{dbname}.confluence.xml").write_text(storage, encoding="utf-8")
        title = f"{data.name} — {data.target}"
        (out / f"{dbname}.html").write_text(storage_to_html(storage, title), encoding="utf-8")
        (out / f"{dbname}.md").write_text(storage_to_markdown(storage, title), encoding="utf-8")
        typer.echo(f"→ out/{dbname}.{{json,confluence.xml,html,md}}")
        for w in data.warnings:
            typer.secho(f"  ! {w}", fg=typer.colors.YELLOW)

    typer.secho("Готово.", fg=typer.colors.GREEN)


def _iter_json(out: Path, db: Optional[str]):
    files = sorted(out.glob("*.json"))
    if db:
        files = [out / f"{db}.json"]
    if not files or (db and not files[0].is_file()):
        typer.secho(f"Нет JSON-артефактов в {out} (сначала выполните collect)",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    for f in files:
        yield Database.load_json(f.read_text(encoding="utf-8")), f


if __name__ == "__main__":
    app()
