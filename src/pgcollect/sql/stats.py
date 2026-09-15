"""Статистика и качество автовакуума по таблицам.

Возвращает словарь (schema, table) → AutovacInfo. Порог срабатывания
autovacuum/analyze считается из глобальных GUC с учётом per-table reloptions;
флаг over_* показывает, что таблица уже перешла порог (autovacuum «не успевает»).
Также берётся возраст relfrozenxid → оценка риска wraparound.
"""

from __future__ import annotations

import re

import psycopg

from ..db import query
from ..models import AutovacInfo

_RELOPT_RE = re.compile(r"^([^=]+)=(.*)$")

# Пространство XID 32-битное: реальный горизонт wraparound — 2^31 транзакций.
# autovacuum_freeze_max_age (default 200M) — лишь порог, при котором PostgreSQL
# ФОРСИРУЕТ anti-wraparound autovacuum, а не граница катастрофы. Поэтому риск
# считаем как долю от 2^31, а превышение freeze_max_age трактуем отдельно
# (форсированный freeze назрел / autovacuum отстаёт), но не как риск wraparound.
_WRAPAROUND_LIMIT = 2**31  # 2 147 483 648


def _globals(conn: psycopg.Connection) -> dict[str, float]:
    rows = query(
        conn,
        """
        SELECT name, setting
        FROM pg_settings
        WHERE name IN (
            'autovacuum_vacuum_threshold',
            'autovacuum_vacuum_scale_factor',
            'autovacuum_analyze_threshold',
            'autovacuum_analyze_scale_factor',
            'autovacuum_freeze_max_age'
        )
        """,
    )
    return {r["name"]: float(r["setting"]) for r in rows}


def _reloptions(reloptions: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for opt in reloptions or []:
        m = _RELOPT_RE.match(opt)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def collect(conn: psycopg.Connection) -> dict[tuple[str, str], AutovacInfo]:
    g = _globals(conn)
    freeze_max_age = g.get("autovacuum_freeze_max_age", 200_000_000)

    rows = query(
        conn,
        """
        SELECT
            s.schemaname AS schema_name,
            s.relname    AS table_name,
            s.last_vacuum::text,
            s.last_autovacuum::text,
            s.last_analyze::text,
            s.last_autoanalyze::text,
            s.n_live_tup,
            s.n_dead_tup,
            s.n_mod_since_analyze,
            c.reloptions,
            age(c.relfrozenxid) AS relfrozenxid_age
        FROM pg_stat_user_tables s
        JOIN pg_class c ON c.oid = s.relid
        WHERE c.relkind IN ('r','m')   -- партиционированные родители без хранения пропускаем
        ORDER BY s.schemaname, s.relname
        """,
    )

    out: dict[tuple[str, str], AutovacInfo] = {}
    for r in rows:
        opts = _reloptions(r["reloptions"])
        live = r["n_live_tup"] or 0
        dead = r["n_dead_tup"] or 0
        mod = r["n_mod_since_analyze"] or 0

        vac_thr = float(opts.get("autovacuum_vacuum_threshold",
                                 g.get("autovacuum_vacuum_threshold", 50)))
        vac_scale = float(opts.get("autovacuum_vacuum_scale_factor",
                                   g.get("autovacuum_vacuum_scale_factor", 0.2)))
        ana_thr = float(opts.get("autovacuum_analyze_threshold",
                                 g.get("autovacuum_analyze_threshold", 50)))
        ana_scale = float(opts.get("autovacuum_analyze_scale_factor",
                                   g.get("autovacuum_analyze_scale_factor", 0.1)))

        vacuum_threshold = int(vac_thr + vac_scale * live)
        analyze_threshold = int(ana_thr + ana_scale * live)

        age = r["relfrozenxid_age"]
        # Риск wraparound — доля от реального лимита 2^31, а не от freeze_max_age.
        wrap_pct = round(100.0 * age / _WRAPAROUND_LIMIT, 1) if age else None
        # Превышение freeze_max_age = форсированный freeze-autovacuum назрел/идёт.
        # Норма при небольшом превышении; кратное превышение → autovacuum отстаёт.
        over_freeze = bool(age and age > freeze_max_age)

        out[(r["schema_name"], r["table_name"])] = AutovacInfo(
            last_vacuum=r["last_vacuum"],
            last_autovacuum=r["last_autovacuum"],
            last_analyze=r["last_analyze"],
            last_autoanalyze=r["last_autoanalyze"],
            n_live_tup=live,
            n_dead_tup=dead,
            dead_pct=round(100.0 * dead / live, 1) if live else None,
            n_mod_since_analyze=mod,
            vacuum_threshold=vacuum_threshold,
            analyze_threshold=analyze_threshold,
            over_vacuum_threshold=dead > vacuum_threshold,
            over_analyze_threshold=mod > analyze_threshold,
            reloptions=r["reloptions"] or [],
            relfrozenxid_age=age,
            wraparound_pct=wrap_pct,
            freeze_max_age=int(freeze_max_age),
            over_freeze_max_age=over_freeze,
        )
    return out
