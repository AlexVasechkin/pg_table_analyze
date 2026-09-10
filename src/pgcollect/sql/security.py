"""Безопасность и доступ: RLS-политики, default privileges, event triggers."""

from __future__ import annotations

import psycopg

from ..db import query
from ..models import DefaultPrivilege, EventTrigger, Policy

_ENABLED = {"O": "enabled", "D": "disabled", "R": "replica", "A": "always"}


def policies(conn: psycopg.Connection) -> list[Policy]:
    rows = query(
        conn,
        """
        SELECT schemaname AS schema_name,
               tablename  AS table_name,
               policyname AS name,
               cmd        AS command,
               (permissive = 'PERMISSIVE') AS permissive,
               array_to_string(roles, ', ') AS roles,
               qual       AS using_expr,
               with_check AS check_expr
        FROM pg_policies
        WHERE schemaname NOT IN ('pg_catalog','information_schema')
        ORDER BY schemaname, tablename, policyname
        """,
    )
    return [Policy(**r) for r in rows]


def default_privileges(conn: psycopg.Connection) -> list[DefaultPrivilege]:
    rows = query(
        conn,
        """
        SELECT pg_get_userbyid(d.defaclrole) AS owner,
               n.nspname AS schema_name,
               CASE d.defaclobjtype WHEN 'r' THEN 'tables'
                                    WHEN 'S' THEN 'sequences'
                                    WHEN 'f' THEN 'functions'
                                    WHEN 'T' THEN 'types'
                                    WHEN 'n' THEN 'schemas' END AS object_type,
               pg_get_userbyid((a.grantee)) AS grantee,
               string_agg(DISTINCT a.privilege_type, ', '
                          ORDER BY a.privilege_type) AS privileges
        FROM pg_default_acl d
        LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
        CROSS JOIN LATERAL aclexplode(d.defaclacl) a
        GROUP BY d.defaclrole, n.nspname, d.defaclobjtype, a.grantee
        ORDER BY owner, schema_name, object_type
        """,
    )
    return [DefaultPrivilege(**r) for r in rows]


def event_triggers(conn: psycopg.Connection) -> list[EventTrigger]:
    rows = query(
        conn,
        """
        SELECT et.evtname AS name,
               et.evtevent AS event,
               pn.nspname || '.' || p.proname AS function,
               et.evtenabled AS enabled_state,
               array_to_string(et.evttags, ', ') AS tags
        FROM pg_event_trigger et
        JOIN pg_proc p ON p.oid = et.evtfoid
        JOIN pg_namespace pn ON pn.oid = p.pronamespace
        ORDER BY et.evtname
        """,
    )
    return [
        EventTrigger(
            name=r["name"],
            event=r["event"],
            function=r["function"],
            enabled=(r["enabled_state"] != "D"),
            enabled_state=_ENABLED.get(r["enabled_state"], r["enabled_state"]),
            tags=r["tags"] or None,
        )
        for r in rows
    ]
