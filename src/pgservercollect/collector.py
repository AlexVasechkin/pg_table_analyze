"""Оркестрация сбора серверного (кластерного) отчёта.

Один read-only коннект к maintenance_db → последовательный вызов sql-модулей
через `_try` (недоступные из-за прав разделы уходят в warnings, не роняя прогон)
→ таблица тюнинга по правилам → расчёт статусов и сводки проблем.
"""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg

from pgcollect.config import Target
from pgcollect.db import NoAccess, connect

from . import tuning
from .models import Issue, Server, Status
from .sql import (
    activity as sql_activity,
    bgwriter as sql_bgwriter,
    databases as sql_databases,
    identity as sql_identity,
    locks as sql_locks,
    memory as sql_memory,
    prepared as sql_prepared,
    progress as sql_progress,
    replication as sql_replication,
    roles as sql_roles,
    settings as sql_settings,
    subscriptions as sql_subscriptions,
    tablespaces as sql_tablespaces,
    wal as sql_wal,
)


def collect_server(target: Target, skip: set[str] | None = None) -> Server:
    skip = skip or set()
    srv = Server(
        target=target.name,
        title=target.title,
        host=target.host,
        port=target.port,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )

    with connect(target, target.maintenance_db) as conn:
        srv.identity = sql_identity.collect(conn)

        raw_settings = _try(srv, "settings", lambda: sql_settings.raw(conn)) or {}
        if "settings" not in skip:
            srv.settings = sql_settings.collect(conn, raw_settings)
        srv.shared_preload_libraries = (
            raw_settings.get("shared_preload_libraries", {}).get("reset_val")
        )

        if "databases" not in skip:
            srv.databases = _try(srv, "databases", lambda: sql_databases.collect(conn)) or []
        if "replication" not in skip:
            srv.replication = (
                _try(srv, "replication", lambda: sql_replication.collect(conn))
                or srv.replication
            )
        if "wal" not in skip:
            srv.wal = _try(srv, "wal", lambda: sql_wal.collect(conn)) or srv.wal
        if "activity" not in skip:
            srv.activity = _try(srv, "activity", lambda: sql_activity.collect(conn)) or srv.activity
        if "bgwriter" not in skip:
            srv.bgwriter = _try(srv, "bgwriter", lambda: sql_bgwriter.collect(conn)) or srv.bgwriter
        if "locks" not in skip:
            srv.locks = _try(srv, "locks", lambda: sql_locks.collect(conn)) or []
        if "tablespaces" not in skip:
            srv.tablespaces = _try(srv, "tablespaces", lambda: sql_tablespaces.collect(conn)) or []
        if "roles" not in skip:
            srv.roles = _try(srv, "roles", lambda: sql_roles.collect(conn)) or []
        if "subscriptions" not in skip:
            srv.subscriptions = _try(
                srv, "subscriptions", lambda: sql_subscriptions.collect(conn)) or []
        if "prepared" not in skip:
            srv.prepared_xacts = _try(srv, "prepared", lambda: sql_prepared.collect(conn)) or []
        if "progress" not in skip:
            srv.progress = _try(srv, "progress", lambda: sql_progress.collect(conn)) or []

        # Железо leader-узла — общая основа для тюнинга и модели потребления ОЗУ.
        hw, node_note = target.hardware_for(srv.identity.cluster_name)

        if "tuning" not in skip and raw_settings:
            signals = _signals(srv)
            srv.tuning, srv.tuning_notes = tuning.build(
                raw_settings, srv.identity.version_num,
                hw, target.workload, signals,
            )
            if node_note:
                srv.tuning_notes.insert(0, node_note)

        if "memory" not in skip and raw_settings:
            srv.memory = _try(
                srv, "memory",
                lambda: sql_memory.collect(conn, raw_settings, hw.total_ram_mb),
            )

    _score(srv, target)
    _issues(srv, target)
    return srv


def _signals(srv: Server) -> dict:
    """Рантайм-сигналы для движка тюнинга (данные из уже собранных разделов)."""
    return {
        "temp_files_total": sum(d.temp_files for d in srv.databases),
        "req_checkpoint_pct": srv.bgwriter.req_pct,
        "cache_hit_min": min((d.cache_hit_ratio for d in srv.databases
                              if d.cache_hit_ratio is not None), default=None),
    }


def _score(srv: Server, target: Target) -> None:
    thr = target.thresholds

    # Реплики (со стороны primary).
    for sb in srv.replication.standbys:
        lag = sb.replay_lag_s
        if lag is not None:
            if lag >= thr.replica_lag_crit_s:
                sb.status = Status.crit
            elif lag >= thr.replica_lag_warn_s:
                sb.status = Status.warn

    # Слоты, удерживающие WAL.
    for slot in srv.replication.slots:
        rb_mb = (slot.retained_wal_bytes or 0) / 1024**2
        if slot.wal_status == "lost" or rb_mb >= thr.slot_retained_wal_crit_mb:
            slot.status = Status.crit
        elif not slot.active and rb_mb >= thr.slot_retained_wal_warn_mb:
            slot.status = Status.warn

    # Активность.
    if srv.activity.used_pct is not None:
        if srv.activity.used_pct >= thr.connections_crit_pct:
            srv.activity.status = Status.crit
        elif srv.activity.used_pct >= thr.connections_warn_pct:
            srv.activity.status = Status.warn

    # Чекпоинты по требованию.
    if srv.bgwriter.req_pct is not None and srv.bgwriter.req_pct >= thr.req_checkpoint_warn_pct:
        srv.bgwriter.status = Status.warn

    # Архивация.
    if srv.wal.archiving_behind:
        srv.wal.status = Status.warn

    # Базы: wraparound.
    for d in srv.databases:
        if d.wraparound_pct is not None:
            if d.wraparound_pct >= thr.wraparound_crit_pct:
                d.status = Status.crit
            elif d.wraparound_pct >= thr.wraparound_warn_pct:
                d.status = Status.warn

    # Подписки.
    for s in srv.subscriptions:
        if s.last_error:
            s.status = Status.crit
        elif not s.enabled:
            s.status = Status.warn


def _issues(srv: Server, target: Target) -> None:
    thr = target.thresholds
    issues: list[Issue] = []

    for sb in srv.replication.standbys:
        if sb.status in (Status.warn, Status.crit):
            issues.append(Issue(severity=sb.status, category="Отставание реплики",
                                entity=sb.application_name or sb.client_addr or "standby",
                                detail=f"replay lag {sb.replay_lag_s:.0f} c"
                                       if sb.replay_lag_s is not None else "лаг"))
    if srv.replication.role == "standby" and srv.replication.replay_paused:
        issues.append(Issue(severity=Status.warn, category="Применение WAL приостановлено",
                            entity="standby", detail="pg_is_wal_replay_paused = true"))
    for slot in srv.replication.slots:
        if slot.status in (Status.warn, Status.crit):
            issues.append(Issue(severity=slot.status, category="Слот удерживает WAL",
                                entity=slot.slot_name,
                                detail=f"{slot.retained_wal_pretty or '?'}"
                                       f"{'' if slot.active else ', неактивен'}"
                                       f"{', wal_status=' + slot.wal_status if slot.wal_status else ''}"))

    if srv.wal.status == Status.warn:
        issues.append(Issue(severity=Status.warn, category="Архивация WAL отстаёт",
                            entity="archiver",
                            detail=f"last_failed_wal={srv.wal.last_failed_wal}"))

    if srv.activity.status in (Status.warn, Status.crit):
        issues.append(Issue(severity=srv.activity.status, category="Близко к max_connections",
                            entity=f"{srv.activity.total}/{srv.activity.max_connections}",
                            detail=f"{srv.activity.used_pct}%"))
    if srv.activity.idle_in_transaction:
        sev = Status.warn if (srv.activity.oldest_xact_age_s or 0) > 300 else Status.warn
        issues.append(Issue(severity=sev, category="idle in transaction",
                            entity=f"{srv.activity.idle_in_transaction} backend(s)",
                            detail=f"старейший xact {srv.activity.oldest_xact_age_s or '?'} c"))

    if srv.bgwriter.status == Status.warn:
        issues.append(Issue(severity=Status.warn, category="Много req-checkpoint",
                            entity="checkpointer", detail=f"{srv.bgwriter.req_pct}% по требованию"))

    for d in srv.databases:
        if d.status in (Status.warn, Status.crit):
            issues.append(Issue(severity=d.status, category="Риск wraparound (БД)",
                                entity=d.name,
                                detail=f"age {d.frozenxid_age} ({d.wraparound_pct}% от 2^31)"))

    for px in srv.prepared_xacts:
        if (px.age_seconds or 0) > 60:
            issues.append(Issue(severity=Status.warn, category="Зависшая prepared-транзакция",
                                entity=px.gid, detail=f"возраст {px.age_seconds} c"))

    for s in srv.subscriptions:
        if s.status in (Status.warn, Status.crit):
            issues.append(Issue(severity=s.status, category="Проблема подписки",
                                entity=s.name,
                                detail=s.last_error or ("отключена" if not s.enabled else "")))

    for st in srv.settings:
        if st.pending_restart:
            issues.append(Issue(severity=Status.warn, category="Требуется рестарт (GUC)",
                                entity=st.name,
                                detail=f"{st.setting} (ожидает применения)"))

    for r in srv.roles:
        if r.expired:
            issues.append(Issue(severity=Status.warn, category="Просроченная роль",
                                entity=r.name, detail=f"VALID UNTIL {r.valid_until}"))

    for lk in srv.locks:
        issues.append(Issue(severity=Status.warn, category="Ожидание блокировки",
                            entity=f"pid {lk.blocked_pid}",
                            detail=f"ждёт {lk.blocking_pids} ({lk.wait_seconds or '?'} c)"))

    for it in srv.tuning:
        if it.priority in (Status.warn, Status.crit):
            issues.append(Issue(severity=it.priority, category="Рекомендация тюнинга",
                                entity=it.name,
                                detail=f"{it.current} → {it.recommended}"))

    order = {Status.crit: 0, Status.warn: 1}
    issues.sort(key=lambda i: (order.get(i.severity, 2), i.category, i.entity))
    srv.issues = issues


def _try(srv: Server, name: str, fn):
    try:
        return fn()
    except NoAccess as exc:
        srv.warnings.append(f"{name}: нет прав ({exc})")
        return None
    except psycopg.Error as exc:
        srv.warnings.append(f"{name}: ошибка сбора ({exc})")
        return None
