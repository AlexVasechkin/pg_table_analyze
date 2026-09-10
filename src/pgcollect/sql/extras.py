"""Дополнительные метрики уровня БД.

Секвенции на исчерпание, долгие/idle-in-transaction транзакции, топ крупнейших
объектов. Остальные агрегаты (таблицы без PK, unused/invalid индексы,
FK без индекса) собираются в collector из уже полученных данных.
"""

from __future__ import annotations

import psycopg

from ..db import has_extension, query
from ..models import LongTransaction, QueryStat, SequenceUsage, Status, TopObject


def sequences(conn: psycopg.Connection) -> list[SequenceUsage]:
    """Секвенции и близость к максимуму типа (риск переполнения)."""
    seqs = query(
        conn,
        """
        SELECT n.nspname AS schema_name,
               c.relname AS name,
               CASE WHEN d.relname IS NOT NULL
                    THEN format('%I.%I', dn.nspname, d.relname) || '.' || a.attname
               END AS owned_by
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_depend dep
               ON dep.objid = c.oid AND dep.deptype = 'a'
        LEFT JOIN pg_class d ON d.oid = dep.refobjid
        LEFT JOIN pg_namespace dn ON dn.oid = d.relnamespace
        LEFT JOIN pg_attribute a
               ON a.attrelid = dep.refobjid AND a.attnum = dep.refobjsubid
        WHERE c.relkind = 'S'
          AND n.nspname NOT IN ('pg_catalog','information_schema')
        ORDER BY n.nspname, c.relname
        """,
    )
    out: list[SequenceUsage] = []
    for s in seqs:
        try:
            meta = query(
                conn,
                "SELECT last_value, max_value FROM pg_sequences "
                "WHERE schemaname = %s AND sequencename = %s",
                (s["schema_name"], s["name"]),
            )
        except Exception:
            meta = []
        last_value = meta[0]["last_value"] if meta else None
        max_value = meta[0]["max_value"] if meta else None
        pct = (
            round(100.0 * last_value / max_value, 1)
            if last_value is not None and max_value else None
        )
        status = Status.ok
        if pct is not None:
            if pct >= 90:
                status = Status.crit
            elif pct >= 75:
                status = Status.warn
        out.append(
            SequenceUsage(
                schema_name=s["schema_name"],
                name=s["name"],
                owned_by=s["owned_by"],
                last_value=last_value,
                max_value=max_value,
                pct_used=pct,
                status=status,
            )
        )
    return out


def long_transactions(conn: psycopg.Connection, min_seconds: int = 60) -> list[LongTransaction]:
    rows = query(
        conn,
        """
        SELECT pid, usename, state,
               extract(epoch FROM (now() - xact_start))::int AS xact_age_seconds,
               left(query, 200) AS query
        FROM pg_stat_activity
        WHERE datname = current_database()
          AND xact_start IS NOT NULL
          AND state <> 'idle'
          AND now() - xact_start > make_interval(secs => %s)
        ORDER BY xact_start
        """,
        (min_seconds,),
    )
    return [LongTransaction(**r) for r in rows]


def top_queries(conn: psycopg.Connection, limit: int = 20) -> list[QueryStat]:
    """Топ запросов по суммарному времени из pg_stat_statements (если установлено).

    Раздел про нагрузку, а не структуру, поэтому собирается только по запросу
    (флаг --stat-statements) и только при наличии расширения.
    """
    if not has_extension(conn, "pg_stat_statements"):
        return []
    rows = query(
        conn,
        """
        SELECT queryid,
               calls,
               round(total_exec_time::numeric, 1) AS total_time_ms,
               round(mean_exec_time::numeric, 1)  AS mean_time_ms,
               rows,
               left(query, 200) AS query
        FROM pg_stat_statements
        WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
        ORDER BY total_exec_time DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [QueryStat(**r) for r in rows]


def top_objects(conn: psycopg.Connection, limit: int = 20) -> list[TopObject]:
    rows = query(
        conn,
        """
        SELECT CASE c.relkind
                    WHEN 'i' THEN 'index'
                    WHEN 'I' THEN 'index'
                    WHEN 't' THEN 'toast'
                    ELSE 'table' END AS kind,
               n.nspname AS schema_name,
               c.relname AS name,
               pg_relation_size(c.oid) AS size_bytes,
               pg_size_pretty(pg_relation_size(c.oid)) AS size_pretty
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r','i','I','t','m')
          AND n.nspname NOT IN ('pg_catalog','information_schema')
        ORDER BY pg_relation_size(c.oid) DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [TopObject(**r) for r in rows]
