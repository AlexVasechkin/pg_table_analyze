"""Таблицы: базовый список, колонки, партиции, размеры.

Возвращает список `Table` с заполненными columns/partitions и размерами.
Индексы, ограничения, bloat и статистику доклеивают отдельные модули в
`collector.py` (bulk-запросами, чтобы не плодить обращения по каждой таблице).
"""

from __future__ import annotations

import psycopg

from ..db import query
from ..models import Column, Partition, Table

_PERSISTENCE = {"p": "permanent", "t": "temporary", "u": "unlogged"}


def collect(conn: psycopg.Connection) -> list[Table]:
    rels = query(
        conn,
        """
        SELECT
            c.oid,
            n.nspname AS schema_name,
            c.relname AS name,
            pg_get_userbyid(c.relowner) AS owner,
            (c.relkind = 'p') AS partitioned,
            c.relispartition   AS is_partition,
            c.relpersistence   AS persistence,
            c.reltuples::bigint AS row_estimate,
            c.relrowsecurity   AS rls_enabled,
            obj_description(c.oid, 'pg_class') AS comment,
            pg_relation_size(c.oid)        AS heap_bytes,
            pg_indexes_size(c.oid)         AS index_bytes,
            pg_total_relation_size(c.oid)  AS total_bytes,
            pg_size_pretty(pg_relation_size(c.oid))       AS heap_pretty,
            pg_size_pretty(pg_indexes_size(c.oid))        AS index_pretty,
            pg_size_pretty(pg_total_relation_size(c.oid)) AS total_pretty,
            CASE WHEN c.relkind = 'p'
                 THEN pg_get_partkeydef(c.oid) END AS partition_key,
            (SELECT pt.partstrat FROM pg_partitioned_table pt
                WHERE pt.partrelid = c.oid)      AS partition_strategy,
            EXISTS (SELECT 1 FROM pg_constraint k
                    WHERE k.conrelid = c.oid AND k.contype = 'p') AS has_pk
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p')
          AND NOT c.relispartition          -- сами партиции показываем внутри родителя
          AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname NOT LIKE 'pg_toast%'
          AND n.nspname NOT LIKE 'pg_temp%'
        ORDER BY n.nspname, c.relname
        """,
    )

    tables: list[Table] = []
    oids: list[int] = [r["oid"] for r in rels]
    columns_by_oid = _columns(conn, oids)
    cachehit_by_oid = _cache_hit(conn, oids)
    access_by_oid = _access_stats(conn, oids)

    for r in rels:
        t = Table(
            schema_name=r["schema_name"],
            name=r["name"],
            owner=r["owner"],
            partitioned=r["partitioned"],
            partition_strategy=_strategy(r["partition_strategy"]),
            partition_key=r["partition_key"],
            persistence=_PERSISTENCE.get(r["persistence"], "permanent"),
            row_estimate=r["row_estimate"] or 0,
            has_pk=r["has_pk"],
            rls_enabled=r["rls_enabled"],
            comment=r["comment"],
            heap_bytes=r["heap_bytes"],
            heap_pretty=r["heap_pretty"],
            index_bytes=r["index_bytes"],
            index_pretty=r["index_pretty"],
            total_bytes=r["total_bytes"],
            total_pretty=r["total_pretty"],
            columns=columns_by_oid.get(r["oid"], []),
            cache_hit_ratio=cachehit_by_oid.get(r["oid"]),
            seq_scan=access_by_oid.get(r["oid"], {}).get("seq_scan"),
            idx_scan=access_by_oid.get(r["oid"], {}).get("idx_scan"),
            hot_update_pct=access_by_oid.get(r["oid"], {}).get("hot_pct"),
        )
        if t.partitioned:
            t.partitions = _partitions(conn, r["oid"])
            t.has_default_partition = any(
                (p.bound or "").strip().upper() == "DEFAULT" for p in t.partitions
            )
        tables.append(t)

    return tables


def _cache_hit(conn: psycopg.Connection, oids: list[int]) -> dict[int, float | None]:
    """Cache hit ratio по таблице (heap + индексы + toast), из pg_statio_user_tables."""
    if not oids:
        return {}
    rows = query(
        conn,
        """
        SELECT relid AS oid,
               coalesce(heap_blks_hit,0) + coalesce(idx_blks_hit,0)
                 + coalesce(toast_blks_hit,0) + coalesce(tidx_blks_hit,0) AS hit,
               coalesce(heap_blks_read,0) + coalesce(idx_blks_read,0)
                 + coalesce(toast_blks_read,0) + coalesce(tidx_blks_read,0) AS read
        FROM pg_statio_user_tables
        WHERE relid = ANY(%s)
        """,
        (oids,),
    )
    out: dict[int, float | None] = {}
    for r in rows:
        total = r["hit"] + r["read"]
        out[r["oid"]] = round(100.0 * r["hit"] / total, 2) if total else None
    return out


def _strategy(code: str | None) -> str | None:
    return {"r": "RANGE", "l": "LIST", "h": "HASH"}.get(code) if code else None


def _columns(conn: psycopg.Connection, oids: list[int]) -> dict[int, list[Column]]:
    if not oids:
        return {}
    rows = query(
        conn,
        """
        SELECT
            a.attrelid AS oid,
            a.attname  AS name,
            format_type(a.atttypid, a.atttypmod) AS type,
            a.attnotnull AS not_null,
            pg_get_expr(ad.adbin, ad.adrelid) AS default,
            CASE a.attidentity
                 WHEN 'a' THEN 'GENERATED ALWAYS'
                 WHEN 'd' THEN 'GENERATED BY DEFAULT' END AS identity,
            NULLIF(a.attstattarget, -1) AS stats_target,
            col_description(a.attrelid, a.attnum) AS comment
        FROM pg_attribute a
        LEFT JOIN pg_attrdef ad
               ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
        WHERE a.attrelid = ANY(%s)
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY a.attrelid, a.attnum
        """,
        (oids,),
    )
    out: dict[int, list[Column]] = {}
    for r in rows:
        out.setdefault(r["oid"], []).append(
            Column(
                name=r["name"],
                type=r["type"],
                not_null=r["not_null"],
                default=r["default"],
                identity=r["identity"],
                stats_target=r["stats_target"],
                comment=r["comment"],
            )
        )
    return out


def _access_stats(conn: psycopg.Connection, oids: list[int]) -> dict[int, dict]:
    """seq/idx scan и доля HOT-обновлений из pg_stat_user_tables."""
    if not oids:
        return {}
    rows = query(
        conn,
        """
        SELECT relid AS oid, seq_scan, idx_scan,
               n_tup_upd, n_tup_hot_upd
        FROM pg_stat_user_tables
        WHERE relid = ANY(%s)
        """,
        (oids,),
    )
    out: dict[int, dict] = {}
    for r in rows:
        upd = r["n_tup_upd"] or 0
        hot = r["n_tup_hot_upd"] or 0
        out[r["oid"]] = {
            "seq_scan": r["seq_scan"],
            "idx_scan": r["idx_scan"],
            "hot_pct": round(100.0 * hot / upd, 1) if upd else None,
        }
    return out


def _partitions(conn: psycopg.Connection, parent_oid: int) -> list[Partition]:
    rows = query(
        conn,
        """
        SELECT
            c.relname AS name,
            pg_get_expr(c.relpartbound, c.oid) AS bound,
            pg_total_relation_size(c.oid) AS size_bytes,
            pg_size_pretty(pg_total_relation_size(c.oid)) AS size_pretty,
            c.reltuples::bigint AS row_estimate
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        WHERE i.inhparent = %s
        ORDER BY c.relname
        """,
        (parent_oid,),
    )
    return [
        Partition(
            name=r["name"],
            bound=r["bound"],
            size_bytes=r["size_bytes"],
            size_pretty=r["size_pretty"],
            row_estimate=r["row_estimate"] or 0,
        )
        for r in rows
    ]
