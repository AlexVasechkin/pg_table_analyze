"""Обзор БД: версия, размер, кодировка, подключения, расширения, cache hit."""

from __future__ import annotations

import psycopg

from ..db import query, scalar
from ..models import Extension, Overview


def collect(conn: psycopg.Connection) -> Overview:
    ov = Overview()
    ov.version_num = conn.info.server_version
    ov.version = scalar(conn, "SELECT version()") or ""

    row = query(
        conn,
        """
        SELECT
            pg_database_size(d.datname)                    AS size_bytes,
            pg_size_pretty(pg_database_size(d.datname))    AS size_pretty,
            pg_encoding_to_char(d.encoding)                AS encoding,
            d.datcollate                                   AS collate,
            d.datctype                                     AS ctype,
            d.datconnlimit                                 AS connection_limit
        FROM pg_database d
        WHERE d.datname = current_database()
        """,
    )[0]
    ov.size_bytes = row["size_bytes"]
    ov.size_pretty = row["size_pretty"]
    ov.encoding = row["encoding"]
    ov.collate = row["collate"]
    ov.ctype = row["ctype"]
    ov.connection_limit = row["connection_limit"]

    ov.connections = scalar(
        conn,
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()",
    ) or 0

    counts = query(
        conn,
        """
        SELECT
          (SELECT count(*) FROM pg_namespace
             WHERE nspname NOT IN ('pg_catalog','information_schema')
               AND nspname NOT LIKE 'pg_toast%'
               AND nspname NOT LIKE 'pg_temp%')                       AS n_schemas,
          (SELECT count(*) FROM pg_class WHERE relkind IN ('r','p'))  AS n_tables,
          (SELECT count(*) FROM pg_class WHERE relkind IN ('i','I'))  AS n_indexes
        """,
    )[0]
    ov.n_schemas = counts["n_schemas"]
    ov.n_tables = counts["n_tables"]
    ov.n_indexes = counts["n_indexes"]

    stat = query(
        conn,
        """
        SELECT blks_hit, blks_read, temp_files, temp_bytes, deadlocks
        FROM pg_stat_database
        WHERE datname = current_database()
        """,
    )
    if stat:
        s = stat[0]
        h, r = s["blks_hit"] or 0, s["blks_read"] or 0
        total = h + r
        ov.cache_hit_ratio = round(100.0 * h / total, 2) if total else None
        ov.temp_files = s["temp_files"] or 0
        ov.temp_bytes = s["temp_bytes"] or 0
        ov.temp_bytes_pretty = query(
            conn, "SELECT pg_size_pretty(%s::bigint) AS p", (ov.temp_bytes,)
        )[0]["p"]
        ov.deadlocks = s["deadlocks"] or 0

    dst = scalar(conn, "SELECT current_setting('default_statistics_target')")
    ov.default_statistics_target = int(dst) if dst is not None else None

    ext = query(
        conn,
        """
        SELECT e.extname AS name,
               e.extversion AS version,
               n.nspname AS schema_name,
               (SELECT array_agg(av.version)
                  FROM pg_available_extension_versions av
                  WHERE av.name = e.extname) AS available_versions
        FROM pg_extension e
        JOIN pg_namespace n ON n.oid = e.extnamespace
        ORDER BY e.extname
        """,
    )
    for e in ext:
        # Наибольшая доступная версия по числовому сравнению (не лексическому:
        # "1.10" > "1.9"). Непарсящиеся версии игнорируются.
        avail = e["available_versions"] or []
        latest = max(avail, key=_ver_key) if avail else None
        outdated = bool(
            latest and e["version"] and _ver_key(latest) > _ver_key(e["version"])
        )
        ov.extensions.append(
            Extension(
                name=e["name"],
                version=e["version"],
                schema_name=e["schema_name"],
                available_version=latest,
                outdated=outdated,
            )
        )
    return ov


def _ver_key(v: str) -> tuple:
    """Числовой ключ версии для сравнения: '1.10' → (1, 10). Нечисловые части → 0."""
    parts = []
    for token in v.replace("-", ".").split("."):
        parts.append(int(token) if token.isdigit() else 0)
    return tuple(parts)
