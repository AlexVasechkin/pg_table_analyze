"""Подключение к PostgreSQL (read-only) и перечисление БД.

Каждая сессия открывается только на чтение: `default_transaction_read_only=on`
плюс `statement_timeout`/`lock_timeout`, чтобы сбор не мешал prod-нагрузке и не
мог ничего изменить. Запросы выполняются через psycopg (v3).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from .config import Target


class NoAccess(Exception):
    """Раздел недоступен из-за нехватки прав — сбор продолжается без него."""


@contextmanager
def connect(target: Target, dbname: str) -> Iterator[psycopg.Connection]:
    """Открыть read-only подключение к конкретной БД кластера.

    Опции сессии задаются через `options` (GUC на старте), само подключение
    остаётся в autocommit — мы только читаем.
    """
    options = " ".join(
        f"-c {k}={v}"
        for k, v in {
            "default_transaction_read_only": "on",
            "statement_timeout": target.statement_timeout_ms,
            "lock_timeout": target.lock_timeout_ms,
            "idle_in_transaction_session_timeout": target.statement_timeout_ms,
        }.items()
    )
    conn = psycopg.connect(
        host=target.host,
        port=target.port,
        user=target.user,
        password=target.password(),
        dbname=dbname,
        sslmode=target.sslmode,
        options=options,
        autocommit=True,
        application_name="pgcollect",
        row_factory=dict_row,
    )
    try:
        yield conn
    finally:
        conn.close()


def query(conn: psycopg.Connection, sql: str, params: dict | tuple | None = None) -> list[dict[str, Any]]:
    """Выполнить SELECT и вернуть список строк-словарей.

    Ошибки нехватки прав (`InsufficientPrivilege`) превращаются в `NoAccess`,
    чтобы вызывающий помечал раздел как недоступный, а не падал.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            return cur.fetchall()
    except psycopg.errors.InsufficientPrivilege as exc:
        raise NoAccess(str(exc)) from exc


def scalar(conn: psycopg.Connection, sql: str, params: dict | tuple | None = None) -> Any:
    """Выполнить запрос и вернуть первое поле первой строки (или None)."""
    rows = query(conn, sql, params)
    if not rows:
        return None
    first = rows[0]
    return next(iter(first.values()))


def enumerate_databases(conn: psycopg.Connection, target: Target) -> list[str]:
    """Список БД для сбора.

    Если в конфиге задан `databases` — берём его (пересекая с реально
    доступными). Иначе — все БД, к которым можно подключиться, кроме template*
    и явно исключённых.
    """
    rows = query(
        conn,
        """
        SELECT datname
        FROM pg_database
        WHERE datallowconn
          AND NOT datistemplate
        ORDER BY datname
        """,
    )
    available = [r["datname"] for r in rows]

    if target.databases is not None:
        wanted = set(target.databases)
        return [d for d in available if d in wanted]

    excluded = set(target.exclude_databases)
    return [d for d in available if d not in excluded]


def server_version(conn: psycopg.Connection) -> tuple[int, str]:
    """Числовая версия сервера (например 160002) и человекочитаемая строка."""
    num = conn.info.server_version
    text = scalar(conn, "SELECT version()")
    return num, text


def has_extension(conn: psycopg.Connection, name: str) -> bool:
    """Установлено ли расширение в текущей БД."""
    return bool(
        scalar(conn, "SELECT 1 FROM pg_extension WHERE extname = %s", (name,))
    )
