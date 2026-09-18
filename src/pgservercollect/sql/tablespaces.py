"""Tablespaces кластера: владелец, путь (pg_tablespace_location), размер."""

from __future__ import annotations

import psycopg

from pgcollect.db import query
from ..models import Tablespace


def collect(conn: psycopg.Connection) -> list[Tablespace]:
    rows = query(
        conn,
        """
        SELECT spcname AS name,
               pg_get_userbyid(spcowner) AS owner,
               pg_tablespace_location(oid) AS location,
               pg_tablespace_size(oid) AS size_bytes,
               pg_size_pretty(pg_tablespace_size(oid)) AS size_pretty
        FROM pg_tablespace
        ORDER BY spcname
        """,
    )
    return [
        Tablespace(
            name=r["name"],
            owner=r["owner"],
            location=r["location"] or None,   # пусто для pg_default/pg_global
            size_bytes=r["size_bytes"] or 0,
            size_pretty=r["size_pretty"],
        )
        for r in rows
    ]
