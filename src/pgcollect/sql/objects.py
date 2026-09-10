"""Прочие объекты БД: представления, функции/процедуры, сторонние таблицы, типы."""

from __future__ import annotations

import psycopg

from ..db import query
from ..models import ForeignServer, ForeignTable, Routine, TypeDef, View

_USER_SCHEMAS = """
  AND n.nspname NOT IN ('pg_catalog','information_schema')
  AND n.nspname NOT LIKE 'pg_toast%'
  AND n.nspname NOT LIKE 'pg_temp%'
"""


def _not_extension(classid: str, oid_col: str) -> str:
    """Исключить объекты, принадлежащие расширениям (pg_depend deptype='e')."""
    return f"""
      AND NOT EXISTS (
          SELECT 1 FROM pg_depend dep
          WHERE dep.classid = '{classid}'::regclass
            AND dep.objid = {oid_col}
            AND dep.deptype = 'e'
      )
    """


def views(conn: psycopg.Connection) -> list[View]:
    rows = query(
        conn,
        f"""
        SELECT n.nspname AS schema_name,
               c.relname AS name,
               pg_get_userbyid(c.relowner) AS owner,
               (c.relkind = 'm') AS materialized,
               c.relispopulated AS populated,
               pg_size_pretty(pg_total_relation_size(c.oid)) AS size_pretty,
               obj_description(c.oid, 'pg_class') AS comment,
               pg_get_viewdef(c.oid, true) AS definition
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('v','m') {_USER_SCHEMAS} {_not_extension('pg_class', 'c.oid')}
        ORDER BY n.nspname, c.relname
        """,
    )
    return [View(**r) for r in rows]


def routines(conn: psycopg.Connection) -> list[Routine]:
    rows = query(
        conn,
        f"""
        SELECT n.nspname AS schema_name,
               p.proname AS name,
               CASE p.prokind WHEN 'f' THEN 'function'
                              WHEN 'p' THEN 'procedure'
                              WHEN 'a' THEN 'aggregate'
                              WHEN 'w' THEN 'window' END AS kind,
               l.lanname AS language,
               pg_get_function_result(p.oid) AS returns,
               p.prosecdef AS security_definer,
               CASE p.provolatile WHEN 'i' THEN 'immutable'
                                  WHEN 's' THEN 'stable'
                                  WHEN 'v' THEN 'volatile' END AS volatility,
               obj_description(p.oid, 'pg_proc') AS comment
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        JOIN pg_language l ON l.oid = p.prolang
        WHERE true {_USER_SCHEMAS} {_not_extension('pg_proc', 'p.oid')}
        ORDER BY n.nspname, p.proname
        """,
    )
    return [Routine(**r) for r in rows]


def foreign_servers(conn: psycopg.Connection) -> list[ForeignServer]:
    rows = query(
        conn,
        """
        SELECT s.srvname AS name,
               w.fdwname AS fdw,
               s.srvversion AS version,
               array_to_string(s.srvoptions, ', ') AS options
        FROM pg_foreign_server s
        JOIN pg_foreign_data_wrapper w ON w.oid = s.srvfdw
        ORDER BY s.srvname
        """,
    )
    return [ForeignServer(**r) for r in rows]


def foreign_tables(conn: psycopg.Connection) -> list[ForeignTable]:
    rows = query(
        conn,
        f"""
        SELECT n.nspname AS schema_name,
               c.relname AS name,
               s.srvname AS server,
               array_to_string(ft.ftoptions, ', ') AS options
        FROM pg_foreign_table ft
        JOIN pg_class c ON c.oid = ft.ftrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_foreign_server s ON s.oid = ft.ftserver
        WHERE true {_USER_SCHEMAS}
        ORDER BY n.nspname, c.relname
        """,
    )
    return [ForeignTable(**r) for r in rows]


def types(conn: psycopg.Connection) -> list[TypeDef]:
    rows = query(
        conn,
        f"""
        SELECT n.nspname AS schema_name,
               t.typname AS name,
               CASE t.typtype WHEN 'e' THEN 'enum'
                              WHEN 'c' THEN 'composite'
                              WHEN 'd' THEN 'domain'
                              WHEN 'r' THEN 'range' END AS kind,
               pg_get_userbyid(t.typowner) AS owner,
               CASE
                 WHEN t.typtype = 'e' THEN
                   (SELECT string_agg(e.enumlabel, ', ' ORDER BY e.enumsortorder)
                    FROM pg_enum e WHERE e.enumtypid = t.oid)
                 WHEN t.typtype = 'd' THEN
                   format_type(t.typbasetype, t.typtypmod)
               END AS detail
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typtype IN ('e','c','d','r')
          -- исключаем автогенерируемые composite-типы таблиц/представлений
          AND (t.typtype <> 'c' OR NOT EXISTS (
                SELECT 1 FROM pg_class c
                WHERE c.reltype = t.oid AND c.relkind IN ('r','p','v','m','S','f')))
          {_USER_SCHEMAS} {_not_extension('pg_type', 't.oid')}
        ORDER BY n.nspname, t.typname
        """,
    )
    return [TypeDef(**r) for r in rows]
