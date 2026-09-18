"""WAL и архивация: текущий LSN, настройки, отставание архиватора."""

from __future__ import annotations

import psycopg

from pgcollect.db import query, scalar
from ..models import WalInfo


def collect(conn: psycopg.Connection) -> WalInfo:
    w = WalInfo()
    w.wal_level = scalar(conn, "SELECT current_setting('wal_level', true)")
    w.archive_mode = scalar(conn, "SELECT current_setting('archive_mode', true)")
    w.archive_command = scalar(conn, "SELECT current_setting('archive_command', true)") or None

    # На standby pg_current_wal_lsn() и pg_walfile_name() недоступны во время
    # recovery — берём receive_lsn и не запрашиваем имя WAL-файла.
    in_recovery = bool(scalar(conn, "SELECT pg_is_in_recovery()"))
    if in_recovery:
        w.current_lsn = scalar(conn, "SELECT pg_last_wal_receive_lsn()::text")
    else:
        row = query(
            conn,
            "SELECT pg_current_wal_lsn()::text AS lsn, "
            "pg_walfile_name(pg_current_wal_lsn()) AS wal_file",
        )[0]
        w.current_lsn = row["lsn"]
        w.current_wal_file = row["wal_file"]

    arch = query(
        conn,
        """
        SELECT archived_count, failed_count,
               last_archived_wal,
               to_char(last_archived_time, 'YYYY-MM-DD HH24:MI:SS TZ') AS last_archived_time,
               last_failed_wal,
               to_char(last_failed_time,   'YYYY-MM-DD HH24:MI:SS TZ') AS last_failed_time,
               (last_failed_time IS NOT NULL
                AND (last_archived_time IS NULL OR last_failed_time > last_archived_time)
               ) AS behind
        FROM pg_stat_archiver
        """,
    )
    if arch:
        a = arch[0]
        w.archived_count = a["archived_count"]
        w.failed_count = a["failed_count"]
        w.last_archived_wal = a["last_archived_wal"]
        w.last_archived_time = a["last_archived_time"]
        w.last_failed_wal = a["last_failed_wal"]
        w.last_failed_time = a["last_failed_time"]
        w.archiving_behind = bool(a["behind"]) and w.archive_mode != "off"
    return w
