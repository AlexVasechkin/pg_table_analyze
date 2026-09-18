"""Блокировки: backend'ы, ожидающие снятия локов (граф blocked → blocking)."""

from __future__ import annotations

import psycopg

from pgcollect.db import query
from ..models import BlockedLock


def collect(conn: psycopg.Connection) -> list[BlockedLock]:
    rows = query(
        conn,
        """
        SELECT a.pid                                        AS blocked_pid,
               a.usename                                    AS blocked_user,
               array_to_string(pg_blocking_pids(a.pid), ', ') AS blocking_pids,
               EXTRACT(epoch FROM now() - a.query_start)::int AS wait_seconds,
               left(a.query, 200)                           AS query
        FROM pg_stat_activity a
        WHERE cardinality(pg_blocking_pids(a.pid)) > 0
        ORDER BY wait_seconds DESC NULLS LAST
        """,
    )
    return [
        BlockedLock(
            blocked_pid=r["blocked_pid"],
            blocked_user=r["blocked_user"],
            blocking_pids=r["blocking_pids"] or "",
            wait_seconds=r["wait_seconds"],
            query=r["query"],
        )
        for r in rows
    ]
