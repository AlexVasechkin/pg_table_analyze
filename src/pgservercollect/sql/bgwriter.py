"""Фоновая запись и чекпоинты: pg_stat_bgwriter (+ pg_stat_checkpointer на PG17+)."""

from __future__ import annotations

import psycopg

from pgcollect.db import query
from ..models import BgWriter


def collect(conn: psycopg.Connection) -> BgWriter:
    bw = BgWriter()
    if conn.info.server_version >= 170000:
        _pg17(conn, bw)
    else:
        _legacy(conn, bw)

    timed, req = bw.checkpoints_timed or 0, bw.checkpoints_req or 0
    total = timed + req
    if total:
        bw.req_pct = round(100.0 * req / total, 1)
    return bw


def _legacy(conn: psycopg.Connection, bw: BgWriter) -> None:
    r = query(
        conn,
        """
        SELECT checkpoints_timed, checkpoints_req,
               checkpoint_write_time, checkpoint_sync_time,
               buffers_checkpoint, buffers_clean, buffers_backend,
               maxwritten_clean,
               to_char(stats_reset, 'YYYY-MM-DD HH24:MI:SS TZ') AS stats_reset
        FROM pg_stat_bgwriter
        """,
    )[0]
    bw.checkpoints_timed = r["checkpoints_timed"]
    bw.checkpoints_req = r["checkpoints_req"]
    bw.checkpoint_write_time_ms = r["checkpoint_write_time"]
    bw.checkpoint_sync_time_ms = r["checkpoint_sync_time"]
    bw.buffers_checkpoint = r["buffers_checkpoint"]
    bw.buffers_clean = r["buffers_clean"]
    bw.buffers_backend = r["buffers_backend"]
    bw.maxwritten_clean = r["maxwritten_clean"]
    bw.stats_reset = r["stats_reset"]


def _pg17(conn: psycopg.Connection, bw: BgWriter) -> None:
    # PG17: checkpoint-поля переехали в pg_stat_checkpointer; buffers_backend убран.
    c = query(
        conn,
        """
        SELECT num_timed, num_requested, write_time, sync_time, buffers_written,
               to_char(stats_reset, 'YYYY-MM-DD HH24:MI:SS TZ') AS stats_reset
        FROM pg_stat_checkpointer
        """,
    )[0]
    bw.checkpoints_timed = c["num_timed"]
    bw.checkpoints_req = c["num_requested"]
    bw.checkpoint_write_time_ms = c["write_time"]
    bw.checkpoint_sync_time_ms = c["sync_time"]
    bw.buffers_checkpoint = c["buffers_written"]
    bw.stats_reset = c["stats_reset"]
    b = query(conn, "SELECT buffers_clean, maxwritten_clean FROM pg_stat_bgwriter")[0]
    bw.buffers_clean = b["buffers_clean"]
    bw.maxwritten_clean = b["maxwritten_clean"]
