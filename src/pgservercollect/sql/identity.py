"""Идентичность экземпляра и его старт: версия, timeline, checksums, uptime."""

from __future__ import annotations

import psycopg

from pgcollect.db import query, scalar
from ..models import Identity


def collect(conn: psycopg.Connection) -> Identity:
    idn = Identity()
    idn.version = scalar(conn, "SELECT version()") or ""
    idn.version_num = conn.info.server_version
    idn.in_recovery = bool(scalar(conn, "SELECT pg_is_in_recovery()"))
    idn.cluster_name = scalar(conn, "SELECT current_setting('cluster_name', true)") or None

    row = query(
        conn,
        """
        SELECT
          to_char(pg_postmaster_start_time(), 'YYYY-MM-DD HH24:MI:SS TZ') AS start_time,
          to_char(pg_conf_load_time(),        'YYYY-MM-DD HH24:MI:SS TZ') AS conf_load_time,
          to_char(now(),                      'YYYY-MM-DD HH24:MI:SS TZ') AS current_time,
          (now() - pg_postmaster_start_time())::text                      AS uptime
        """,
    )[0]
    idn.start_time = row["start_time"]
    idn.conf_load_time = row["conf_load_time"]
    idn.current_time = row["current_time"]
    idn.uptime = row["uptime"]

    # system_identifier и timeline — из управляющих функций (доступны всем).
    sysid = query(conn, "SELECT system_identifier FROM pg_control_system()")
    if sysid:
        idn.system_identifier = str(sysid[0]["system_identifier"])
    tl = query(conn, "SELECT timeline_id FROM pg_control_checkpoint()")
    if tl:
        idn.timeline = tl[0]["timeline_id"]

    checksums = scalar(conn, "SELECT current_setting('data_checksums', true)")
    if checksums is not None:
        idn.data_checksums = checksums == "on"

    ssl = scalar(conn, "SELECT current_setting('ssl', true)")
    if ssl is not None:
        idn.ssl_in_use = ssl == "on"

    return idn
