"""Ограничения: PK/FK/UNIQUE/CHECK/EXCLUDE, NOT VALID, действия FK.

Возвращает словарь (schema, table) → list[Constraint]. Также умеет находить
внешние ключи без покрывающего индекса (частая причина медленных DELETE/UPDATE).
"""

from __future__ import annotations

import psycopg

from ..db import query
from ..models import Constraint


def collect(conn: psycopg.Connection) -> dict[tuple[str, str], list[Constraint]]:
    rows = query(
        conn,
        """
        SELECT
            n.nspname AS schema_name,
            t.relname AS table_name,
            c.conname AS name,
            c.contype AS type,
            pg_get_constraintdef(c.oid) AS definition,
            c.convalidated AS valid
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE c.contype IN ('p','f','u','c','x')
          AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname NOT LIKE 'pg_toast%'
          AND n.nspname NOT LIKE 'pg_temp%'
        ORDER BY n.nspname, t.relname, c.contype, c.conname
        """,
    )
    out: dict[tuple[str, str], list[Constraint]] = {}
    for r in rows:
        out.setdefault((r["schema_name"], r["table_name"]), []).append(
            Constraint(
                name=r["name"],
                type=r["type"],
                definition=r["definition"],
                valid=r["valid"],
            )
        )
    return out


def fk_without_index(conn: psycopg.Connection) -> list[str]:
    """Список 'schema.table (fk_name)' для FK, чьи колонки не покрыты индексом.

    Индекс считается покрывающим, если ведущие колонки индекса совпадают с
    колонками FK (по началу списка).
    """
    rows = query(
        conn,
        """
        SELECT n.nspname AS schema_name,
               t.relname AS table_name,
               c.conname AS fk_name
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE c.contype = 'f'
          AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname NOT LIKE 'pg_%'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_index i
              WHERE i.indrelid = c.conrelid
                -- ведущие колонки индекса совпадают с колонками FK (в порядке)
                AND (string_to_array(i.indkey::text, ' ')::int2[])
                        [1:array_length(c.conkey, 1)] = c.conkey
          )
        ORDER BY n.nspname, t.relname, c.conname
        """,
    )
    return [f'{r["schema_name"]}.{r["table_name"]} ({r["fk_name"]})' for r in rows]
