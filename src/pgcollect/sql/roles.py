"""Роли, членство в группах и привилегии на объекты.

Роли в PostgreSQL кластерные (общие для всех БД), но привилегии на объекты —
внутри каждой БД. Собираем и то и другое, привязывая раздел к странице БД.
`NoAccess` пробрасывается наверх — collector пометит раздел недоступным.
"""

from __future__ import annotations

import psycopg

from ..db import query
from ..models import Grant, Role, RolesSection


def collect(conn: psycopg.Connection) -> RolesSection:
    section = RolesSection()

    roles = query(
        conn,
        """
        SELECT
            r.rolname        AS name,
            r.rolcanlogin    AS can_login,
            r.rolsuper       AS superuser,
            r.rolcreaterole  AS createrole,
            r.rolcreatedb    AS createdb,
            r.rolreplication AS replication,
            r.rolbypassrls   AS bypassrls,
            r.rolconnlimit   AS conn_limit,
            r.rolvaliduntil::text AS valid_until,
            (r.rolvaliduntil IS NOT NULL AND r.rolvaliduntil < now()) AS expired,
            ARRAY(
                SELECT g.rolname
                FROM pg_auth_members m
                JOIN pg_roles g ON g.oid = m.roleid
                WHERE m.member = r.oid
                ORDER BY g.rolname
            ) AS member_of
        FROM pg_roles r
        ORDER BY r.rolname
        """,
    )
    section.roles = [Role(**r) for r in roles]

    # Привилегии на объекты текущей БД: таблицы/представления/секвенции.
    grants = query(
        conn,
        """
        SELECT grantee,
               object_type,
               schema_name,
               object_name,
               string_agg(DISTINCT privilege_type, ', '
                          ORDER BY privilege_type) AS privileges
        FROM (
            SELECT pg_get_userbyid(g.grantee) AS grantee,
                   CASE c.relkind WHEN 'S' THEN 'sequence' ELSE 'table' END
                       AS object_type,
                   n.nspname AS schema_name,
                   c.relname AS object_name,
                   g.privilege_type AS privilege_type
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL aclexplode(c.relacl) g
            WHERE c.relkind IN ('r','p','v','m','S')
              AND n.nspname NOT IN ('pg_catalog','information_schema')
              AND n.nspname NOT LIKE 'pg_%'
        ) x
        GROUP BY grantee, object_type, schema_name, object_name
        ORDER BY schema_name, object_name, grantee
        """,
    )
    section.grants = [Grant(**g) for g in grants]
    return section
