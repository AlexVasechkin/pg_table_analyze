"""Сводка по всем БД кластера: размеры, wraparound на уровне БД, статистика."""

from __future__ import annotations

import psycopg

from pgcollect.db import query
from ..models import DatabaseSummary

_WRAPAROUND_LIMIT = 2**31


def collect(conn: psycopg.Connection) -> list[DatabaseSummary]:
    rows = query(
        conn,
        """
        SELECT
          d.datname                                    AS name,
          pg_get_userbyid(d.datdba)                    AS owner,
          pg_database_size(d.datname)                  AS size_bytes,
          pg_size_pretty(pg_database_size(d.datname))  AS size_pretty,
          pg_encoding_to_char(d.encoding)              AS encoding,
          d.datcollate                                 AS collate,
          d.datconnlimit                               AS connection_limit,
          age(d.datfrozenxid)                          AS frozenxid_age,
          (SELECT count(*) FROM pg_stat_activity a WHERE a.datname = d.datname) AS connections,
          s.xact_commit, s.xact_rollback, s.deadlocks, s.temp_files, s.temp_bytes,
          s.blks_hit, s.blks_read
        FROM pg_database d
        LEFT JOIN pg_stat_database s ON s.datname = d.datname
        WHERE d.datallowconn AND NOT d.datistemplate
        ORDER BY pg_database_size(d.datname) DESC
        """,
    )
    out: list[DatabaseSummary] = []
    for r in rows:
        age = r["frozenxid_age"]
        wrap = round(100.0 * age / _WRAPAROUND_LIMIT, 1) if age else None
        h, rd = r["blks_hit"] or 0, r["blks_read"] or 0
        total = h + rd
        temp_pretty = query(
            conn, "SELECT pg_size_pretty(%s::bigint) AS p", (r["temp_bytes"] or 0,)
        )[0]["p"]
        out.append(
            DatabaseSummary(
                name=r["name"],
                owner=r["owner"],
                size_bytes=r["size_bytes"],
                size_pretty=r["size_pretty"],
                encoding=r["encoding"],
                collate=r["collate"],
                connection_limit=r["connection_limit"],
                connections=r["connections"] or 0,
                frozenxid_age=age,
                wraparound_pct=wrap,
                xact_commit=r["xact_commit"] or 0,
                xact_rollback=r["xact_rollback"] or 0,
                deadlocks=r["deadlocks"] or 0,
                temp_files=r["temp_files"] or 0,
                temp_bytes_pretty=temp_pretty,
                cache_hit_ratio=round(100.0 * h / total, 2) if total else None,
            )
        )
    return out
