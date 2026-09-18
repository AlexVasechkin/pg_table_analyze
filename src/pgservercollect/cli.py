"""CLI: pgservercollect collect | render | preview | markdown | all.

Собирает кластерный (instance-wide) отчёт через одно подключение к postgres.
Артефакты — один на цель: out/cluster.<target>.{json,confluence.xml,html,md}.

collect  — подключается к кластеру и пишет out/cluster.<target>.json.
render   — json → out/cluster.<target>.confluence.xml (Confluence storage format).
preview  — json → out/cluster.<target>.html (автономный HTML).
markdown — json → out/cluster.<target>.md.
all      — collect + render + preview + markdown за прогон.
"""

from __future__ import annotations

from pathlib import Path

import typer

from pgcollect.config import Config, LoadError, Target, load_config
from pgcollect.preview import storage_to_html, storage_to_markdown

from .collector import collect_server
from .models import Server
from .render import render_server

app = typer.Typer(
    add_completion=False,
    help="pgservercollect — сбор кластерных метаданных PostgreSQL и генерация отчёта.",
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
        typer.secho(f"Цель '{name}' не найдена. Доступны: {available}",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    return t


def _path(out: Path, target: str, ext: str) -> Path:
    """Путь артефакта: cluster.<target>.<ext> (json/confluence.xml/html/md)."""
    return out / f"cluster.{target}.{ext}"


def _artifacts(out: Path, srv: Server) -> None:
    storage = render_server(srv)
    title = f"{srv.target} — кластер PostgreSQL"
    _path(out, srv.target, "json").write_text(srv.dump_json(), encoding="utf-8")
    _path(out, srv.target, "confluence.xml").write_text(storage, encoding="utf-8")
    _path(out, srv.target, "html").write_text(
        storage_to_html(storage, title), encoding="utf-8")
    _path(out, srv.target, "md").write_text(
        storage_to_markdown(storage, title), encoding="utf-8")
    typer.echo(f"→ out/cluster.{srv.target}.{{json,confluence.xml,html,md}}")


def _emit(srv: Server) -> None:
    for n in srv.tuning_notes:
        typer.secho(f"  · тюнинг: {n}", fg=typer.colors.CYAN, err=True)
    for w in srv.warnings:
        typer.secho(f"  ! {w}", fg=typer.colors.YELLOW)


@app.command()
def collect(
    target: str = typer.Argument(..., help="Имя цели из targets.yaml."),
    config: Path = typer.Option(DEFAULT_CONFIG, "--config", "-c", help="Файл целей."),
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Каталог артефактов."),
    skip: list[str] = typer.Option([], "--skip", help="Пропустить раздел (tuning/replication/…)."),
) -> None:
    """Собрать кластерные метаданные и записать out/cluster.<target>.json."""
    cfg = _load(config)
    tgt = _target(cfg, target)
    out.mkdir(parents=True, exist_ok=True)
    typer.echo(f"[collect] {target} …")
    srv = collect_server(tgt, skip=set(skip))
    _path(out, target, "json").write_text(srv.dump_json(), encoding="utf-8")
    typer.echo(f"→ out/cluster.{target}.json")
    _emit(srv)
    typer.secho("Сбор завершён.", fg=typer.colors.GREEN)


@app.command()
def render(
    target: str = typer.Argument(..., help="Имя цели."),
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Каталог артефактов."),
) -> None:
    """out/cluster.<target>.json → .confluence.xml."""
    srv = _read(out, target)
    _path(out, target, "confluence.xml").write_text(
        render_server(srv), encoding="utf-8")
    typer.secho(f"→ out/cluster.{target}.confluence.xml", fg=typer.colors.GREEN)


@app.command()
def preview(
    target: str = typer.Argument(..., help="Имя цели."),
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Каталог артефактов."),
) -> None:
    """out/cluster.<target>.json → .html."""
    srv = _read(out, target)
    title = f"{srv.target} — кластер PostgreSQL"
    _path(out, target, "html").write_text(
        storage_to_html(render_server(srv), title), encoding="utf-8")
    typer.secho(f"→ out/cluster.{target}.html", fg=typer.colors.GREEN)


@app.command()
def markdown(
    target: str = typer.Argument(..., help="Имя цели."),
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Каталог артефактов."),
) -> None:
    """out/cluster.<target>.json → .md."""
    srv = _read(out, target)
    title = f"{srv.target} — кластер PostgreSQL"
    _path(out, target, "md").write_text(
        storage_to_markdown(render_server(srv), title), encoding="utf-8")
    typer.secho(f"→ out/cluster.{target}.md", fg=typer.colors.GREEN)


@app.command()
def all(
    target: str = typer.Argument(..., help="Имя цели из targets.yaml."),
    config: Path = typer.Option(DEFAULT_CONFIG, "--config", "-c", help="Файл целей."),
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Каталог артефактов."),
    skip: list[str] = typer.Option([], "--skip", help="Пропустить раздел."),
) -> None:
    """collect + render + preview + markdown за прогон."""
    cfg = _load(config)
    tgt = _target(cfg, target)
    out.mkdir(parents=True, exist_ok=True)
    typer.echo(f"[all] {target} …")
    srv = collect_server(tgt, skip=set(skip))
    _artifacts(out, srv)
    _emit(srv)
    typer.secho("Готово.", fg=typer.colors.GREEN)


def _read(out: Path, target: str) -> Server:
    path = _path(out, target, "json")
    if not path.is_file():
        typer.secho(f"Нет {path} (сначала выполните collect)", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    return Server.load_json(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    app()
