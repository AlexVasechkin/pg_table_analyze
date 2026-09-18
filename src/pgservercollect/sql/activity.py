"""Активность по всему кластеру из pg_stat_activity + фоновые процессы."""

from __future__ import annotations

import psycopg

from pgcollect.db import query, scalar
from ..models import Activity, ActivityBucket


def collect(conn: psycopg.Connection) -> Activity:
    act = Activity()
    act.max_connections = int(scalar(conn, "SELECT current_setting('max_connections')") or 0)

    # Клиентские backend'ы (без walsender и системных фоновых процессов).
    act.total = scalar(
        conn,
        "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend'",
    ) or 0
    if act.max_connections:
        act.used_pct = round(100.0 * act.total / act.max_connections, 1)

    act.by_state = _bucket(
        conn,
        """
        SELECT COALESCE(state, 'unknown') AS key, count(*) AS n
        FROM pg_stat_activity WHERE backend_type = 'client backend'
        GROUP BY 1 ORDER BY n DESC
        """,
    )
    act.by_wait = _bucket(
        conn,
        """
        SELECT wait_event_type || COALESCE(':' || wait_event, '') AS key, count(*) AS n
        FROM pg_stat_activity
        WHERE backend_type = 'client backend' AND wait_event_type IS NOT NULL
        GROUP BY 1 ORDER BY n DESC LIMIT 10
        """,
    )
    act.by_database = _bucket(
        conn,
        """
        SELECT datname AS key, count(*) AS n
        FROM pg_stat_activity
        WHERE backend_type = 'client backend' AND datname IS NOT NULL
        GROUP BY 1 ORDER BY n DESC LIMIT 10
        """,
    )

    counts = query(
        conn,
        """
        SELECT
          count(*) FILTER (WHERE state = 'active')                     AS active,
          count(*) FILTER (WHERE state = 'idle in transaction')        AS iit,
          EXTRACT(epoch FROM max(now() - xact_start))::int             AS oldest_xact,
          EXTRACT(epoch FROM max(now() - query_start))::int            AS oldest_query
        FROM pg_stat_activity WHERE backend_type = 'client backend'
        """,
    )[0]
    act.active = counts["active"] or 0
    act.idle_in_transaction = counts["iit"] or 0
    act.oldest_xact_age_s = counts["oldest_xact"]
    act.oldest_query_age_s = counts["oldest_query"]
    return act


def _bucket(conn: psycopg.Connection, sql: str) -> list[ActivityBucket]:
    return [ActivityBucket(key=str(r["key"]), count=r["n"]) for r in query(conn, sql)]
