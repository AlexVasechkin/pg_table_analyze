"""Схемы БД: владельцы, размеры, число объектов."""

from __future__ import annotations

import psycopg

from ..db import query
from ..models import Schema


def collect(conn: psycopg.Connection) -> list[Schema]:
    rows = query(
        conn,
        """
        SELECT
            n.nspname AS name,
            pg_get_userbyid(n.nspowner) AS owner,
            COALESCE(SUM(pg_total_relation_size(c.oid))
                     FILTER (WHERE c.relkind IN ('r','p','m')), 0) AS size_bytes,
            count(*) FILTER (WHERE c.relkind IN ('r','p')) AS n_tables,
            count(*) FILTER (WHERE c.relkind IN ('i','I')) AS n_indexes
        FROM pg_namespace n
        LEFT JOIN pg_class c ON c.relnamespace = n.oid
        WHERE n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname NOT LIKE 'pg_toast%'
          AND n.nspname NOT LIKE 'pg_temp%'
        GROUP BY n.nspname, n.nspowner
        ORDER BY n.nspname
        """,
    )
    result: list[Schema] = []
    for r in rows:
        n_functions = query(
            conn,
            """
            SELECT count(*) AS n
            FROM pg_proc p
            JOIN pg_namespace ns ON ns.oid = p.pronamespace
            WHERE ns.nspname = %s
            """,
            (r["name"],),
        )[0]["n"]
        result.append(
            Schema(
                name=r["name"],
                owner=r["owner"],
                size_bytes=r["size_bytes"],
                size_pretty=_pretty(conn, r["size_bytes"]),
                n_tables=r["n_tables"],
                n_indexes=r["n_indexes"],
                n_functions=n_functions,
            )
        )
    return result


def _pretty(conn: psycopg.Connection, size: int) -> str:
    return query(conn, "SELECT pg_size_pretty(%s::bigint) AS p", (size,))[0]["p"]
