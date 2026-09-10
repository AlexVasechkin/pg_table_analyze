"""Индексы: определения, размеры, использование, дубли, невалидные.

Возвращает словарь (schema, table) → list[Index]. Дубликаты определяются по
нормализованной сигнатуре определения (без имени индекса) в пределах таблицы.
"""

from __future__ import annotations

import re

import psycopg

from ..db import query
from ..models import Index

_SIG_RE = re.compile(r"^CREATE (?:UNIQUE )?INDEX \S+ ", re.IGNORECASE)


def collect(conn: psycopg.Connection) -> dict[tuple[str, str], list[Index]]:
    rows = query(
        conn,
        """
        SELECT
            n.nspname AS schema_name,
            t.relname AS table_name,
            ic.relname AS name,
            pg_get_indexdef(i.indexrelid) AS definition,
            am.amname AS method,
            i.indisunique  AS unique,
            i.indisprimary AS primary,
            i.indisvalid   AS valid,
            pg_relation_size(i.indexrelid) AS size_bytes,
            pg_size_pretty(pg_relation_size(i.indexrelid)) AS size_pretty,
            s.idx_scan AS idx_scan
        FROM pg_index i
        JOIN pg_class ic ON ic.oid = i.indexrelid
        JOIN pg_class t  ON t.oid  = i.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_am am ON am.oid = ic.relam
        LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = i.indexrelid
        WHERE n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname NOT LIKE 'pg_toast%'
          AND n.nspname NOT LIKE 'pg_temp%'
        ORDER BY n.nspname, t.relname, ic.relname
        """,
    )

    out: dict[tuple[str, str], list[Index]] = {}
    for r in rows:
        idx = Index(
            name=r["name"],
            definition=r["definition"],
            method=r["method"],
            unique=r["unique"],
            primary=r["primary"],
            valid=r["valid"],
            size_bytes=r["size_bytes"],
            size_pretty=r["size_pretty"],
            idx_scan=r["idx_scan"],
            unused=(r["idx_scan"] == 0 and not r["primary"] and not r["unique"]),
        )
        out.setdefault((r["schema_name"], r["table_name"]), []).append(idx)

    _mark_duplicates(out)
    return out


def _signature(definition: str) -> str:
    """Определение индекса без имени — для сравнения на дубликаты."""
    return _SIG_RE.sub("", definition).strip()


def _mark_duplicates(by_table: dict[tuple[str, str], list[Index]]) -> None:
    for indexes in by_table.values():
        seen: dict[str, str] = {}
        for idx in indexes:
            sig = _signature(idx.definition)
            if sig in seen:
                idx.duplicate_of = seen[sig]
            else:
                seen[sig] = idx.name
