"""Зависшие prepared-транзакции (2PC): держат locks/xmin, риск wraparound."""

from __future__ import annotations

import psycopg

from pgcollect.db import query
from ..models import PreparedXact


def collect(conn: psycopg.Connection) -> list[PreparedXact]:
    rows = query(
        conn,
        """
        SELECT gid, database, owner,
               to_char(prepared, 'YYYY-MM-DD HH24:MI:SS TZ') AS prepared,
               EXTRACT(epoch FROM now() - prepared)::int AS age_seconds
        FROM pg_prepared_xacts
        ORDER BY prepared
        """,
    )
    return [
        PreparedXact(
            gid=r["gid"],
            database=r["database"],
            owner=r["owner"],
            prepared=r["prepared"],
            age_seconds=r["age_seconds"],
        )
        for r in rows
    ]
