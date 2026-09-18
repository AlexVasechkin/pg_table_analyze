"""Роли кластерного уровня: атрибуты, членство, срок действия."""

from __future__ import annotations

import psycopg

from pgcollect.db import query
from ..models import ServerRole


def collect(conn: psycopg.Connection) -> list[ServerRole]:
    rows = query(
        conn,
        """
        SELECT r.rolname AS name, r.rolcanlogin AS can_login,
               r.rolsuper AS superuser, r.rolcreaterole AS createrole,
               r.rolcreatedb AS createdb, r.rolreplication AS replication,
               r.rolbypassrls AS bypassrls, r.rolconnlimit AS conn_limit,
               to_char(r.rolvaliduntil, 'YYYY-MM-DD') AS valid_until,
               (r.rolvaliduntil IS NOT NULL AND r.rolvaliduntil < now()) AS expired,
               ARRAY(
                 SELECT g.rolname FROM pg_auth_members m
                 JOIN pg_roles g ON g.oid = m.roleid
                 WHERE m.member = r.oid ORDER BY g.rolname
               ) AS member_of
        FROM pg_roles r
        ORDER BY r.rolname
        """,
    )
    return [
        ServerRole(
            name=r["name"],
            can_login=r["can_login"],
            superuser=r["superuser"],
            createrole=r["createrole"],
            createdb=r["createdb"],
            replication=r["replication"],
            bypassrls=r["bypassrls"],
            conn_limit=r["conn_limit"],
            valid_until=r["valid_until"],
            expired=bool(r["expired"]),
            member_of=list(r["member_of"] or []),
        )
        for r in rows
    ]
