"""Физическая топология репликации: роль узла, standby, слоты, wal receiver.

Всё строится из PG-представлений (pg_stat_replication, pg_replication_slots,
pg_stat_wal_receiver). Это ФАКТИЧЕСКАЯ картина по данным PostgreSQL, а не
состояние Patroni/DCS (лежащие узлы, теги членов, история failover через PG
недоступны — см. server-plan.md).
"""

from __future__ import annotations

import re

import psycopg

from pgcollect.db import query, scalar
from ..models import Replication, ReplicationSlot, ReplicationStandby, WalReceiver

_PW_RE = re.compile(r"password=('[^']*'|\S+)")


def collect(conn: psycopg.Connection) -> Replication:
    rep = Replication()
    in_recovery = bool(scalar(conn, "SELECT pg_is_in_recovery()"))
    rep.role = "standby" if in_recovery else "primary"
    rep.synchronous_standby_names = (
        scalar(conn, "SELECT current_setting('synchronous_standby_names', true)") or None
    )
    rep.synchronous_commit = scalar(conn, "SELECT current_setting('synchronous_commit', true)")

    if in_recovery:
        _standby_side(conn, rep)
    else:
        _primary_side(conn, rep)

    rep.slots = _slots(conn)
    return rep


def _primary_side(conn: psycopg.Connection, rep: Replication) -> None:
    rows = query(
        conn,
        """
        SELECT application_name, client_addr::text AS client_addr, state, sync_state,
               EXTRACT(epoch FROM write_lag)::float  AS write_lag_s,
               EXTRACT(epoch FROM flush_lag)::float  AS flush_lag_s,
               EXTRACT(epoch FROM replay_lag)::float AS replay_lag_s,
               pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)::bigint AS lag_bytes
        FROM pg_stat_replication
        ORDER BY application_name
        """,
    )
    for r in rows:
        lag_bytes = r["lag_bytes"]
        pretty = None
        if lag_bytes is not None:
            pretty = query(
                conn, "SELECT pg_size_pretty(%s::bigint) AS p", (lag_bytes,)
            )[0]["p"]
        rep.standbys.append(
            ReplicationStandby(
                application_name=r["application_name"],
                client_addr=r["client_addr"],
                state=r["state"],
                sync_state=r["sync_state"],
                write_lag_s=r["write_lag_s"],
                flush_lag_s=r["flush_lag_s"],
                replay_lag_s=r["replay_lag_s"],
                lag_bytes=lag_bytes,
                lag_pretty=pretty,
            )
        )


def _standby_side(conn: psycopg.Connection, rep: Replication) -> None:
    rep.replay_paused = bool(scalar(conn, "SELECT pg_is_wal_replay_paused()"))
    row = query(
        conn,
        """
        SELECT pg_last_wal_receive_lsn()::text AS receive_lsn,
               pg_last_wal_replay_lsn()::text  AS replay_lsn,
               EXTRACT(epoch FROM now() - pg_last_xact_replay_timestamp())::float AS lag_s
        """,
    )[0]
    rep.receive_lsn = row["receive_lsn"]
    rep.replay_lsn = row["replay_lsn"]
    rep.replay_lag_s = row["lag_s"]

    conninfo = scalar(conn, "SELECT current_setting('primary_conninfo', true)")
    if conninfo:
        rep.primary_conninfo = _PW_RE.sub("password=***", conninfo)

    # В pg_stat_wal_receiver нет «received_lsn» — берём flushed_lsn как принятый.
    wr = query(
        conn,
        """
        SELECT status, sender_host, sender_port,
               flushed_lsn::text AS received_lsn,
               latest_end_lsn::text AS latest_end_lsn,
               to_char(last_msg_receipt_time, 'YYYY-MM-DD HH24:MI:SS TZ') AS last_msg
        FROM pg_stat_wal_receiver
        """,
    )
    if wr:
        w = wr[0]
        rep.wal_receiver = WalReceiver(
            status=w["status"],
            sender_host=w["sender_host"],
            sender_port=w["sender_port"],
            received_lsn=w["received_lsn"],
            latest_end_lsn=w["latest_end_lsn"],
            last_msg_receipt_time=w["last_msg"],
        )


def _slots(conn: psycopg.Connection) -> list[ReplicationSlot]:
    # restart_lsn удерживает WAL от текущей позиции; wal_status — PG13+.
    rows = query(
        conn,
        """
        SELECT slot_name, slot_type, active, plugin, wal_status,
               pg_wal_lsn_diff(
                 CASE WHEN pg_is_in_recovery()
                      THEN pg_last_wal_receive_lsn() ELSE pg_current_wal_lsn() END,
                 restart_lsn)::bigint AS retained_bytes
        FROM pg_replication_slots
        ORDER BY slot_name
        """,
    )
    out: list[ReplicationSlot] = []
    for r in rows:
        rb = r["retained_bytes"]
        pretty = None
        if rb is not None:
            pretty = query(conn, "SELECT pg_size_pretty(%s::bigint) AS p", (rb,))[0]["p"]
        out.append(
            ReplicationSlot(
                slot_name=r["slot_name"],
                slot_type=r["slot_type"],
                active=bool(r["active"]),
                plugin=r["plugin"],
                wal_status=r["wal_status"],
                retained_wal_bytes=rb,
                retained_wal_pretty=pretty,
            )
        )
    return out
