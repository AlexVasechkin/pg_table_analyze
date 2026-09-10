"""Триггеры пользовательских таблиц.

Возвращает словарь (schema, table) → list[Trigger]. Внутренние триггеры,
реализующие ограничения (FK и т.п.), пропускаются (tgisinternal). Тип/события/
уровень декодируются из битовой маски tgtype.
"""

from __future__ import annotations

import psycopg

from ..db import query
from ..models import Trigger

_ENABLED = {"O": "enabled", "D": "disabled", "R": "replica", "A": "always"}


def collect(conn: psycopg.Connection) -> dict[tuple[str, str], list[Trigger]]:
    rows = query(
        conn,
        """
        SELECT n.nspname AS schema_name,
               t.relname AS table_name,
               tg.tgname AS name,
               CASE WHEN (tg.tgtype & 64) <> 0 THEN 'INSTEAD OF'
                    WHEN (tg.tgtype & 2)  <> 0 THEN 'BEFORE'
                    ELSE 'AFTER' END AS timing,
               array_to_string(ARRAY[
                   CASE WHEN tg.tgtype & 4  <> 0 THEN 'INSERT'   END,
                   CASE WHEN tg.tgtype & 8  <> 0 THEN 'DELETE'   END,
                   CASE WHEN tg.tgtype & 16 <> 0 THEN 'UPDATE'   END,
                   CASE WHEN tg.tgtype & 32 <> 0 THEN 'TRUNCATE' END
               ], ', ') AS events,
               CASE WHEN tg.tgtype & 1 <> 0 THEN 'ROW' ELSE 'STATEMENT' END AS level,
               pn.nspname || '.' || p.proname AS function,
               tg.tgenabled AS enabled_state,
               pg_get_triggerdef(tg.oid) AS definition
        FROM pg_trigger tg
        JOIN pg_class t ON t.oid = tg.tgrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_proc p ON p.oid = tg.tgfoid
        JOIN pg_namespace pn ON pn.oid = p.pronamespace
        WHERE NOT tg.tgisinternal
          AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname NOT LIKE 'pg_%'
        ORDER BY n.nspname, t.relname, tg.tgname
        """,
    )
    out: dict[tuple[str, str], list[Trigger]] = {}
    for r in rows:
        state = _ENABLED.get(r["enabled_state"], r["enabled_state"])
        out.setdefault((r["schema_name"], r["table_name"]), []).append(
            Trigger(
                name=r["name"],
                timing=r["timing"],
                events=r["events"] or "",
                level=r["level"],
                function=r["function"],
                enabled=(r["enabled_state"] != "D"),
                enabled_state=state,
                definition=r["definition"],
            )
        )
    return out
