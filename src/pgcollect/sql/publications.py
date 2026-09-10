"""Публикации логической репликации и их состав таблиц.

Показывает, какие таблицы текущей БД входят в какие публикации (pg_publication),
с перечнем опубликованных колонок и фильтром строк по каждой таблице. Состав
берётся из представления pg_publication_tables, которое корректно раскрывает
и `FOR ALL TABLES`, и обычные публикации. Списки колонок и фильтры строк
(attnames/rowfilter) появились в PG15 — на PG14 колонки не заполняются.
"""

from __future__ import annotations

import psycopg

from ..db import query
from ..models import Publication, PublicationTable


def collect(conn: psycopg.Connection) -> list[Publication]:
    pubs = query(
        conn,
        """
        SELECT p.pubname AS name,
               pg_get_userbyid(p.pubowner) AS owner,
               p.puballtables AS all_tables,
               p.pubinsert, p.pubupdate, p.pubdelete, p.pubtruncate,
               p.pubviaroot AS via_root
        FROM pg_publication p
        ORDER BY p.pubname
        """,
    )
    if not pubs:
        return []

    # attnames/rowfilter доступны с PG15 — иначе берём только имена таблиц.
    has_colinfo = conn.info.server_version >= 150000
    cols = ("pubname, schemaname, tablename, attnames, rowfilter"
            if has_colinfo else "pubname, schemaname, tablename")
    members = query(
        conn,
        f"""
        SELECT {cols}
        FROM pg_publication_tables
        ORDER BY pubname, schemaname, tablename
        """,
    )
    tables_by_pub: dict[str, list[PublicationTable]] = {}
    for m in members:
        tables_by_pub.setdefault(m["pubname"], []).append(
            PublicationTable(
                name=f'{m["schemaname"]}.{m["tablename"]}',
                columns=list(m["attnames"]) if has_colinfo and m["attnames"] else [],
                row_filter=m["rowfilter"] if has_colinfo else None,
            )
        )

    result: list[Publication] = []
    for p in pubs:
        ops = [
            name for name, flag in (
                ("insert", p["pubinsert"]),
                ("update", p["pubupdate"]),
                ("delete", p["pubdelete"]),
                ("truncate", p["pubtruncate"]),
            ) if flag
        ]
        result.append(
            Publication(
                name=p["name"],
                owner=p["owner"],
                all_tables=p["all_tables"],
                operations=", ".join(ops),
                via_root=p["via_root"],
                tables=tables_by_pub.get(p["name"], []),
            )
        )
    return result
