"""Оркестрация сбора: обход БД кластера и сборка модели `Database`.

Порядок: подключиться к maintenance-БД → перечислить БД → по каждой БД собрать
разделы (overview/schemas/roles/tables) и доклеить к таблицам bulk-данные
(indexes/constraints/bloat/autovac), затем посчитать статусы и доп. метрики.
Недоступные из-за прав разделы не роняют сбор, а уходят в `Database.warnings`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg

from .config import Target
from .db import NoAccess, connect, has_extension
from .models import Database, Issue, RolesSection, Status
from .sql import (
    bloat as sql_bloat,
    constraints as sql_constraints,
    extras as sql_extras,
    indexes as sql_indexes,
    objects as sql_objects,
    overview as sql_overview,
    publications as sql_publications,
    roles as sql_roles,
    schemas as sql_schemas,
    security as sql_security,
    stats as sql_stats,
    tables as sql_tables,
    triggers as sql_triggers,
)


def collect_database(
    target: Target,
    dbname: str,
    deep_bloat: bool = False,
    skip: set[str] | None = None,
    stat_statements: bool = False,
) -> Database:
    skip = skip or set()
    db = Database(
        name=dbname,
        title=None,
        target=target.name,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )

    with connect(target, dbname) as conn:
        db.overview = sql_overview.collect(conn)

        if "schemas" not in skip:
            _guard(db, "schemas", lambda: _set(db, "schemas", sql_schemas.collect(conn)))

        if "roles" not in skip:
            _guard(db, "roles", lambda: _set(db, "roles", sql_roles.collect(conn)))

        if "tables" not in skip:
            _collect_tables(conn, db, target, deep_bloat, skip)

        if "objects" not in skip:
            _collect_objects(conn, db)

        if "security" not in skip:
            _collect_security(conn, db, skip)

        if "publications" not in skip:
            _collect_publications(conn, db)

        if "extras" not in skip:
            _collect_extras(conn, db, stat_statements)

    _rollup_extras(db)
    _collect_issues(db, target)
    return db


def _collect_tables(conn, db: Database, target: Target, deep_bloat: bool, skip: set[str]) -> None:
    try:
        tables = sql_tables.collect(conn)
    except NoAccess as exc:
        db.warnings.append(f"tables: нет прав ({exc})")
        return

    idx = _try(db, "indexes", lambda: sql_indexes.collect(conn)) or {}
    cons = _try(db, "constraints", lambda: sql_constraints.collect(conn)) or {}
    trig = _try(db, "triggers", lambda: sql_triggers.collect(conn)) or {}
    avac = _try(db, "autovacuum", lambda: sql_stats.collect(conn)) or {}

    bloat = {}
    idx_frag = {}
    if "bloat" not in skip:
        has_pgs = has_extension(conn, "pgstattuple")
        bloat = _try(
            db, "bloat",
            lambda: sql_bloat.collect(conn, target.thresholds, has_pgs, deep_bloat),
        ) or {}
        if has_pgs:
            idx_frag = _try(
                db, "index_fragmentation",
                lambda: sql_bloat.index_fragmentation(conn, target.thresholds, deep_bloat),
            ) or {}

    for t in tables:
        key = (t.schema_name, t.name)
        t.indexes = idx.get(key, [])
        t.constraints = cons.get(key, [])
        t.triggers = trig.get(key, [])
        for i in t.indexes:
            fr = idx_frag.get((t.schema_name, i.name))
            if fr:
                i.bloat_pct, i.fragmentation_pct = fr
        if key in avac:
            t.autovac = avac[key]
        if key in bloat:
            t.bloat = bloat[key]
        _score_table(t, target)

    db.tables = tables


def _collect_objects(conn, db: Database) -> None:
    db.views = _try(db, "views", lambda: sql_objects.views(conn)) or []
    db.routines = _try(db, "routines", lambda: sql_objects.routines(conn)) or []
    db.types = _try(db, "types", lambda: sql_objects.types(conn)) or []
    db.foreign_servers = _try(
        db, "foreign_servers", lambda: sql_objects.foreign_servers(conn)) or []
    db.foreign_tables = _try(
        db, "foreign_tables", lambda: sql_objects.foreign_tables(conn)) or []


def _collect_security(conn, db: Database, skip: set[str]) -> None:
    db.policies = _try(db, "policies", lambda: sql_security.policies(conn)) or []
    # Привилегии по умолчанию (ALTER DEFAULT PRIVILEGES) — это привилегии, поэтому
    # они относятся и к разделу ролей: --skip roles их тоже отключает.
    if "roles" not in skip:
        db.default_privileges = _try(
            db, "default_privileges", lambda: sql_security.default_privileges(conn)) or []
    db.event_triggers = _try(
        db, "event_triggers", lambda: sql_security.event_triggers(conn)) or []


def _collect_publications(conn, db: Database) -> None:
    pubs = _try(db, "publications", lambda: sql_publications.collect(conn))
    if pubs is None:
        return
    db.publications = pubs
    # Обратная привязка: у каждой таблицы — список публикаций, в которые она входит.
    pub_of: dict[str, list[str]] = {}
    for p in pubs:
        for pt in p.tables:
            pub_of.setdefault(pt.name, []).append(p.name)
    for t in db.tables:
        if t.fqname in pub_of:
            t.publications = pub_of[t.fqname]


def _collect_extras(conn, db: Database, stat_statements: bool = False) -> None:
    db.extras.sequences = _try(db, "sequences", lambda: sql_extras.sequences(conn)) or []
    db.extras.long_transactions = _try(
        db, "long_transactions", lambda: sql_extras.long_transactions(conn)) or []
    db.extras.top_objects = _try(db, "top_objects", lambda: sql_extras.top_objects(conn)) or []
    if stat_statements:
        db.extras.top_queries = _try(
            db, "top_queries", lambda: sql_extras.top_queries(conn)) or []
    try:
        db.extras.fk_without_index = sql_constraints.fk_without_index(conn)
    except NoAccess as exc:
        db.warnings.append(f"fk_without_index: нет прав ({exc})")


def _score_table(t, target: Target) -> None:
    """Итоговый статус таблицы = максимум по bloat / dead tuples / wraparound."""
    thr = target.thresholds
    statuses = [t.bloat.status]

    dead_pct = t.autovac.dead_pct
    if dead_pct is not None:
        if dead_pct >= thr.dead_tuple_crit_pct:
            t.autovac.status = Status.crit
        elif dead_pct >= thr.dead_tuple_warn_pct:
            t.autovac.status = Status.warn

    wrap = t.autovac.wraparound_pct
    if wrap is not None:
        if wrap >= thr.wraparound_crit_pct:
            t.autovac.status = Status.crit
        elif wrap >= thr.wraparound_warn_pct and t.autovac.status == Status.ok:
            t.autovac.status = Status.warn

    statuses.append(t.autovac.status)
    for i in t.indexes:
        if not i.valid:
            i.status = Status.crit
        elif i.unused or i.duplicate_of:
            i.status = Status.warn
        # фрагментация/распухание индекса (pgstatindex)
        frag = i.fragmentation_pct
        if frag is not None:
            if frag >= thr.bloat_crit_pct:
                i.status = Status.crit
            elif frag >= thr.bloat_warn_pct and i.status == Status.ok:
                i.status = Status.warn
        statuses.append(i.status)

    t.status = _worst(statuses)


def _rollup_extras(db: Database) -> None:
    """Агрегаты по уже собранным таблицам."""
    db.extras.tables_without_pk = [
        t.fqname for t in db.tables if not t.has_pk and not t.partitioned
    ]
    db.extras.tables_without_index = [
        t.fqname for t in db.tables if not t.partitioned and not t.indexes
    ]
    db.extras.unused_indexes = [
        f"{t.fqname}.{i.name}" for t in db.tables for i in t.indexes if i.unused
    ]
    db.extras.unused_index_bytes = sum(
        i.size_bytes for t in db.tables for i in t.indexes if i.unused
    )
    db.extras.unused_index_pretty = _human_bytes(db.extras.unused_index_bytes)
    db.extras.invalid_indexes = [
        f"{t.fqname}.{i.name}" for t in db.tables for i in t.indexes if not i.valid
    ]
    # Колонки с ручным attstattarget (отклонение от default_statistics_target).
    db.extras.custom_stats_columns = [
        f"{t.fqname}.{c.name}={c.stats_target}"
        for t in db.tables for c in t.columns if c.stats_target is not None
    ]
    db.extras.partition_outliers = _partition_outliers(db)


def _collect_issues(db: Database, target: Target) -> None:
    """Собрать плоский список проблемных сущностей для сводки в шапке отчёта."""
    thr = target.thresholds
    issues: list[Issue] = []

    for t in db.tables:
        b = t.bloat
        if b.status in (Status.warn, Status.crit) and b.heap_bloat_pct is not None:
            issues.append(Issue(severity=b.status, category="Распухание таблицы",
                                 entity=t.fqname,
                                 detail=f"{b.heap_bloat_pct}% ({b.method})"))
        av = t.autovac
        if av.dead_pct is not None:
            if av.dead_pct >= thr.dead_tuple_crit_pct:
                issues.append(Issue(severity=Status.crit, category="Мёртвые кортежи",
                                    entity=t.fqname, detail=f"dead {av.dead_pct}%"))
            elif av.dead_pct >= thr.dead_tuple_warn_pct:
                issues.append(Issue(severity=Status.warn, category="Мёртвые кортежи",
                                    entity=t.fqname, detail=f"dead {av.dead_pct}%"))
        if av.wraparound_pct is not None:
            if av.wraparound_pct >= thr.wraparound_crit_pct:
                issues.append(Issue(severity=Status.crit, category="Риск wraparound",
                                    entity=t.fqname,
                                    detail=f"age {av.relfrozenxid_age} "
                                           f"({av.wraparound_pct}% от лимита 2^31)"))
            elif av.wraparound_pct >= thr.wraparound_warn_pct:
                issues.append(Issue(severity=Status.warn, category="Риск wraparound",
                                    entity=t.fqname,
                                    detail=f"age {av.relfrozenxid_age} "
                                           f"({av.wraparound_pct}% от лимита 2^31)"))
        # Отдельный, более мягкий сигнал: форсированный freeze-autovacuum давно
        # должен был отработать (age кратно превысил freeze_max_age), но age всё
        # ещё высок → autovacuum не справляется. Само по себе превышение
        # freeze_max_age — норма, поэтому порог кратности, а не факт превышения.
        if (av.over_freeze_max_age and av.freeze_max_age
                and av.relfrozenxid_age is not None
                and av.relfrozenxid_age >= 2 * av.freeze_max_age
                and (av.wraparound_pct is None
                     or av.wraparound_pct < thr.wraparound_warn_pct)):
            issues.append(Issue(severity=Status.warn, category="Autovacuum отстаёт (freeze)",
                                entity=t.fqname,
                                detail=f"age {av.relfrozenxid_age} > 2×freeze_max_age "
                                       f"({av.freeze_max_age})"))
        for i in t.indexes:
            if not i.valid:
                issues.append(Issue(severity=Status.crit, category="Невалидный индекс",
                                    entity=f"{t.fqname}.{i.name}", detail=""))
            frag = i.fragmentation_pct
            if frag is not None and frag >= thr.bloat_warn_pct:
                sev = Status.crit if frag >= thr.bloat_crit_pct else Status.warn
                issues.append(Issue(severity=sev, category="Фрагментация индекса",
                                    entity=f"{t.fqname}.{i.name}",
                                    detail=f"{frag}% (bloat {i.bloat_pct}%)"))
        for tr in t.triggers:
            if not tr.enabled:
                issues.append(Issue(severity=Status.warn, category="Отключённый триггер",
                                    entity=f"{t.fqname}.{tr.name}",
                                    detail=tr.enabled_state))
        # Кандидат на индекс: крупная таблица с преобладанием seq scan.
        if (not t.partitioned and t.row_estimate >= 10000 and t.seq_scan
                and t.seq_scan > (t.idx_scan or 0)):
            issues.append(Issue(severity=Status.warn, category="Преобладание seq scan",
                                entity=t.fqname,
                                detail=f"seq {t.seq_scan} > idx {t.idx_scan or 0}"))

    for s in db.extras.sequences:
        if s.status in (Status.warn, Status.crit):
            issues.append(Issue(severity=s.status, category="Исчерпание sequence",
                                entity=f"{s.schema_name}.{s.name}",
                                detail=f"{s.pct_used}%"))
    for x in db.extras.long_transactions:
        issues.append(Issue(severity=Status.warn, category="Долгая транзакция",
                            entity=f"pid {x.pid}", detail=f"{x.xact_age_seconds} с"))
    for e in db.overview.extensions:
        if e.outdated:
            issues.append(Issue(severity=Status.warn, category="Устаревшее расширение",
                                entity=e.name,
                                detail=f"{e.version} → {e.available_version}"))
    for r in db.roles.roles:
        if r.expired:
            issues.append(Issue(severity=Status.warn, category="Просроченная роль",
                                entity=r.name, detail=f"VALID UNTIL {r.valid_until}"))
    for rt in db.routines:
        if rt.security_definer:
            issues.append(Issue(severity=Status.warn, category="SECURITY DEFINER функция",
                                entity=f"{rt.schema_name}.{rt.name}", detail=rt.language or ""))
    for et in db.event_triggers:
        if not et.enabled:
            issues.append(Issue(severity=Status.warn, category="Отключённый event trigger",
                                entity=et.name, detail=et.enabled_state))

    # Агрегированные (списочные) проблемы — по одной строке, чтобы не раздувать сводку.
    ex = db.extras
    if ex.unused_indexes:
        issues.append(Issue(severity=Status.warn, category="Неиспользуемые индексы",
                            entity=f"{len(ex.unused_indexes)} шт.",
                            detail=f"вернётся ~{ex.unused_index_pretty}"))
    if ex.tables_without_pk:
        issues.append(Issue(severity=Status.warn, category="Таблицы без PK",
                            entity=f"{len(ex.tables_without_pk)} шт.",
                            detail=", ".join(ex.tables_without_pk)))
    if ex.tables_without_index:
        issues.append(Issue(severity=Status.warn, category="Таблицы без индексов",
                            entity=f"{len(ex.tables_without_index)} шт.",
                            detail=", ".join(ex.tables_without_index)))
    if ex.fk_without_index:
        issues.append(Issue(severity=Status.warn, category="FK без индекса",
                            entity=f"{len(ex.fk_without_index)} шт.",
                            detail=", ".join(ex.fk_without_index)))
    for po in ex.partition_outliers:
        issues.append(Issue(severity=Status.warn, category="Аномалия партиций",
                            entity=po, detail=""))

    # Крит — вперёд, затем по категории.
    order = {Status.crit: 0, Status.warn: 1}
    issues.sort(key=lambda i: (order.get(i.severity, 2), i.category, i.entity))
    db.issues = issues


def _partition_outliers(db: Database) -> list[str]:
    """Пустые партиции и партиционированные таблицы без DEFAULT-партиции."""
    out: list[str] = []
    for t in db.tables:
        if not t.partitioned:
            continue
        if t.partition_strategy in ("RANGE", "LIST") and not t.has_default_partition:
            out.append(f"{t.fqname}: нет DEFAULT-партиции")
        for p in t.partitions:
            if p.row_estimate == 0:
                out.append(f"{t.fqname}: пустая партиция {p.name}")
    return out


def _human_bytes(n: int) -> str:
    """Байты → человекочитаемо (как pg_size_pretty, приблизительно)."""
    size = float(n)
    for unit in ("bytes", "kB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "bytes" else f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.0f} TB"


# --- вспомогательное -------------------------------------------------------

def _worst(statuses: list[Status]) -> Status:
    order = {Status.ok: 0, Status.unknown: 1, Status.warn: 2, Status.crit: 3}
    return max(statuses, key=lambda s: order[s]) if statuses else Status.ok


def _set(db: Database, attr: str, value) -> None:
    setattr(db, attr, value)


def _guard(db: Database, name: str, fn) -> None:
    try:
        fn()
    except NoAccess as exc:
        db.warnings.append(f"{name}: нет прав ({exc})")
        if name == "roles":
            db.roles = RolesSection(accessible=False)


def _try(db: Database, name: str, fn):
    try:
        return fn()
    except NoAccess as exc:
        db.warnings.append(f"{name}: нет прав ({exc})")
        return None
