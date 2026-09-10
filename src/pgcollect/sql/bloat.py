"""Анализ распухания (bloat) таблиц и TOAST — гибридный метод.

По умолчанию — оценочный запрос по каталогу (адаптация общеизвестной методики
ioguix/check_postgres: сравнение фактического числа страниц с ожидаемым по
средней ширине строки и fillfactor). Оценка лёгкая, но приблизительная и
требует свежего ANALYZE.

Если доступно расширение pgstattuple и объект меньше порога размера (или задан
--deep-bloat), для точности берём реальные цифры через pgstattuple_approx.

Возвращает словарь (schema, table) → BloatInfo.
"""

from __future__ import annotations

import psycopg

from ..db import query
from ..models import BloatInfo, Status

# Оценочный запрос: на выходе schemaname, tblname, tblid, bloat_size, bloat_ratio, is_na.
_ESTIMATE_SQL = """
SELECT schemaname, tblname, tblid, toastpages, bs,
       (tblpages - est_tblpages_ff) * bs AS bloat_size,
       CASE WHEN tblpages - est_tblpages_ff > 0
            THEN 100 * (tblpages - est_tblpages_ff) / tblpages::float
            ELSE 0 END AS bloat_ratio,
       is_na
FROM (
  SELECT ceil( reltuples / ((bs-page_hdr)*fillfactor/(tpl_size*100)) )
             + ceil( toasttuples / 4 ) AS est_tblpages_ff,
         tblpages, bs, tblid, schemaname, tblname, toastpages, is_na
  FROM (
    SELECT
      ( 4 + tpl_hdr_size + tpl_data_size + (2*ma)
        - CASE WHEN tpl_hdr_size%ma = 0 THEN ma ELSE tpl_hdr_size%ma END
        - CASE WHEN ceil(tpl_data_size)::int%ma = 0 THEN ma
               ELSE ceil(tpl_data_size)::int%ma END
      ) AS tpl_size,
      (heappages + toastpages) AS tblpages, reltuples, toasttuples,
      bs, page_hdr, tblid, schemaname, tblname, fillfactor, is_na, toastpages
    FROM (
      SELECT
        tbl.oid AS tblid, ns.nspname AS schemaname, tbl.relname AS tblname,
        tbl.reltuples, tbl.relpages AS heappages,
        coalesce(toast.relpages, 0) AS toastpages,
        coalesce(toast.reltuples, 0) AS toasttuples,
        coalesce(substring(array_to_string(tbl.reloptions, ' ')
                 FROM 'fillfactor=([0-9]+)')::smallint, 100) AS fillfactor,
        current_setting('block_size')::numeric AS bs,
        CASE WHEN version() ~ '64-bit|x86_64|ppc64|ia64|amd64'
             THEN 8 ELSE 4 END AS ma,
        24 AS page_hdr,
        23 + CASE WHEN MAX(coalesce(s.null_frac,0)) > 0
                  THEN ( 7 + count(s.attname) ) / 8 ELSE 0::int END AS tpl_hdr_size,
        sum( (1-coalesce(s.null_frac, 0)) * coalesce(s.avg_width, 0) ) AS tpl_data_size,
        bool_or(att.atttypid = 'pg_catalog.name'::regtype)
          OR sum(CASE WHEN att.attnum > 0 THEN 1 ELSE 0 END) <> count(s.attname)
          AS is_na
      FROM pg_attribute AS att
      JOIN pg_class AS tbl ON att.attrelid = tbl.oid
      JOIN pg_namespace AS ns ON ns.oid = tbl.relnamespace
      LEFT JOIN pg_stats AS s
             ON s.schemaname = ns.nspname AND s.tablename = tbl.relname
            AND s.inherited = false AND s.attname = att.attname
      LEFT JOIN pg_class AS toast ON tbl.reltoastrelid = toast.oid
      WHERE NOT att.attisdropped
        AND tbl.relkind IN ('r','m')
        AND att.attnum > 0
        AND ns.nspname NOT IN ('pg_catalog','information_schema')
      GROUP BY tbl.oid, ns.nspname, tbl.relname, tbl.reltuples, tbl.relpages,
               toast.relpages, toast.reltuples, tbl.reloptions, tbl.reltoastrelid
    ) AS s1
  ) AS s2
) AS s3
"""


def collect(
    conn: psycopg.Connection,
    thresholds,
    has_pgstattuple: bool = False,
    deep: bool = False,
) -> dict[tuple[str, str], BloatInfo]:
    rows = query(conn, _ESTIMATE_SQL)

    out: dict[tuple[str, str], BloatInfo] = {}
    for r in rows:
        key = (r["schemaname"], r["tblname"])
        toast_bytes = int((r["toastpages"] or 0) * (r["bs"] or 0))
        if r["is_na"]:
            info = BloatInfo(method="estimate", status=Status.unknown)
        else:
            pct = round(float(r["bloat_ratio"]), 1)
            info = BloatInfo(
                method="estimate",
                heap_bloat_pct=pct,
                heap_bloat_bytes=int(r["bloat_size"]),
                status=_status(pct, thresholds),
            )
        info.toast_size_bytes = toast_bytes
        info.toast_size_pretty = _pretty(conn, toast_bytes)

        # Точное измерение через pgstattuple, если доступно и объект не крупнее порога.
        if has_pgstattuple:
            heap_mb = _heap_mb(conn, r["tblid"])
            if deep or heap_mb <= thresholds.pgstattuple_max_mb:
                _apply_pgstattuple(conn, r["tblid"], info, thresholds)

        out[key] = info

    return out


def _heap_mb(conn: psycopg.Connection, oid: int) -> float:
    b = query(conn, "SELECT pg_relation_size(%s) AS b", (oid,))[0]["b"] or 0
    return b / 1024 / 1024


def _apply_pgstattuple(conn, oid: int, info: BloatInfo, thresholds) -> None:
    try:
        row = query(
            conn,
            "SELECT approx_free_percent, dead_tuple_percent "
            "FROM pgstattuple_approx(%s)",
            (oid,),
        )
    except Exception:
        return
    if not row:
        return
    free = float(row[0]["approx_free_percent"] or 0)
    dead = float(row[0]["dead_tuple_percent"] or 0)
    pct = round(free + dead, 1)
    info.method = "pgstattuple"
    info.heap_bloat_pct = pct
    info.status = _status(pct, thresholds)


def index_fragmentation(
    conn: psycopg.Connection,
    thresholds,
    deep: bool = False,
) -> dict[tuple[str, str], tuple[float, float]]:
    """Фрагментация и распухание btree-индексов через pgstatindex.

    Возвращает {(schema, index_name): (bloat_pct, fragmentation_pct)}.
    Требует pgstattuple; сканирует индекс, поэтому без --deep берём только
    индексы меньше порога размера (pgstattuple_max_mb).
    """
    max_bytes = thresholds.pgstattuple_max_mb * 1024 * 1024
    size_filter = "" if deep else f"AND pg_relation_size(i.oid) < {max_bytes}"
    rows = query(
        conn,
        f"""
        SELECT n.nspname AS schema_name,
               i.relname AS index_name,
               GREATEST(100 - st.avg_leaf_density, 0)::float AS bloat_pct,
               st.leaf_fragmentation::float AS fragmentation_pct
        FROM pg_index x
        JOIN pg_class i ON i.oid = x.indexrelid
        JOIN pg_class t ON t.oid = x.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_am am ON am.oid = i.relam
        CROSS JOIN LATERAL pgstatindex(i.oid::regclass) st
        WHERE am.amname = 'btree'
          AND i.relpages > 1
          AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname NOT LIKE 'pg_%'
          {size_filter}
        """,
    )
    return {
        (r["schema_name"], r["index_name"]): (
            round(r["bloat_pct"], 1),
            round(r["fragmentation_pct"], 1),
        )
        for r in rows
    }


def _status(pct: float, thresholds) -> Status:
    if pct >= thresholds.bloat_crit_pct:
        return Status.crit
    if pct >= thresholds.bloat_warn_pct:
        return Status.warn
    return Status.ok


def _pretty(conn: psycopg.Connection, size: int) -> str:
    return query(conn, "SELECT pg_size_pretty(%s::bigint) AS p", (size,))[0]["p"]
