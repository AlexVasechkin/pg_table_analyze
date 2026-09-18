"""Логическая репликация со стороны подписчика: pg_subscription + статистика."""

from __future__ import annotations

import psycopg

from pgcollect.db import query
from ..models import Subscription


def collect(conn: psycopg.Connection) -> list[Subscription]:
    subs = query(
        conn,
        """
        SELECT s.subname AS name, s.subenabled AS enabled, s.subslotname AS slot_name,
               (SELECT count(*) FROM pg_stat_subscription ss WHERE ss.subid = s.oid) AS workers,
               (SELECT max(received_lsn)::text FROM pg_stat_subscription ss
                  WHERE ss.subid = s.oid) AS received_lsn,
               (SELECT max(latest_end_lsn)::text FROM pg_stat_subscription ss
                  WHERE ss.subid = s.oid) AS latest_end_lsn
        FROM pg_subscription s
        ORDER BY s.subname
        """,
    )
    errors = _errors(conn)
    out: list[Subscription] = []
    for r in subs:
        out.append(
            Subscription(
                name=r["name"],
                enabled=bool(r["enabled"]),
                slot_name=r["slot_name"],
                worker_count=r["workers"] or 0,
                received_lsn=r["received_lsn"],
                latest_end_lsn=r["latest_end_lsn"],
                last_error=errors.get(r["name"]),
            )
        )
    return out


def _errors(conn: psycopg.Connection) -> dict[str, str]:
    """apply/sync-ошибки из pg_stat_subscription_stats (PG15+)."""
    if conn.info.server_version < 150000:
        return {}
    rows = query(
        conn,
        """
        SELECT subname, apply_error_count, sync_error_count
        FROM pg_stat_subscription_stats
        WHERE apply_error_count > 0 OR sync_error_count > 0
        """,
    )
    return {
        r["subname"]: f"apply_errors={r['apply_error_count']}, sync_errors={r['sync_error_count']}"
        for r in rows
    }
