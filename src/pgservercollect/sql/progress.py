"""Прогресс длительных операций: что прямо сейчас выполняется на кластере."""

from __future__ import annotations

import psycopg

from pgcollect.db import query
from ..models import ProgressOp

# view → (kind, есть ли колонка datname)
_VIEWS = [
    ("pg_stat_progress_vacuum", "vacuum"),
    ("pg_stat_progress_analyze", "analyze"),
    ("pg_stat_progress_create_index", "create index"),
    ("pg_stat_progress_cluster", "cluster"),
    ("pg_stat_progress_basebackup", "basebackup"),
]


def collect(conn: psycopg.Connection) -> list[ProgressOp]:
    out: list[ProgressOp] = []
    for view, kind in _VIEWS:
        has_phase = _has_col(conn, view, "phase")
        has_db = _has_col(conn, view, "datid")
        phase = "phase" if has_phase else "NULL::text AS phase"
        db = ("(SELECT datname FROM pg_database WHERE oid = v.datid)"
              if has_db else "NULL::text")
        rows = query(
            conn,
            f"SELECT pid, {phase}, {db} AS datname FROM {view} v",
        )
        for r in rows:
            out.append(ProgressOp(
                kind=kind, pid=r["pid"], database=r["datname"], phase=r["phase"],
            ))
    return out


def _has_col(conn: psycopg.Connection, view: str, col: str) -> bool:
    return bool(query(
        conn,
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        """,
        (view, col),
    ))
