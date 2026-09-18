"""Конфигурация из pg_settings: НЕдефолтные значения + курируемый набор важных GUC.

`raw(conn)` возвращает сырые строки pg_settings по имени — общий источник и для
таблицы настроек, и для движка рекомендаций (tuning.py), чтобы не читать каталог
дважды.
"""

from __future__ import annotations

import psycopg

from pgcollect.db import query
from ..models import Setting

# Настройки подключения + таймауты подключений и транзакций — отдельная таблица в
# отчёте. Единый источник имён (используется и для IMPORTANT, и для фильтра в шаблоне).
# Несуществующие в конкретной версии PG имена безвредны — просто не появятся.
CONNECTION_SETTINGS = [
    # соединения
    "max_connections", "superuser_reserved_connections", "reserved_connections",
    "listen_addresses", "port",
    # таймауты подключения / keepalive
    "authentication_timeout", "client_connection_check_interval",
    "tcp_keepalives_idle", "tcp_keepalives_interval", "tcp_keepalives_count",
    "tcp_user_timeout",
    # таймауты транзакций / операторов
    "statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout",
    "idle_session_timeout", "transaction_timeout", "deadlock_timeout",
]

# Настройки планировщика SELECT-запросов и параллелизма — отдельная таблица. В отчёте
# критерий: category 'Query Tuning / *' (стоимости/методы планировщика) ЛИБО имя из этого
# списка (воркеры параллелизма и async I/O живут в 'Resource Usage / Asynchronous Behavior').
QUERY_PARALLEL_SETTINGS = [
    # планировщик (стоимости/поведение для SELECT)
    "seq_page_cost", "random_page_cost",
    "cpu_tuple_cost", "cpu_index_tuple_cost", "cpu_operator_cost",
    "effective_cache_size", "default_statistics_target", "jit",
    "from_collapse_limit", "join_collapse_limit", "constraint_exclusion",
    "cursor_tuple_fraction", "plan_cache_mode",
    # параллелизм / async I/O
    "max_worker_processes", "max_parallel_workers",
    "max_parallel_workers_per_gather", "max_parallel_maintenance_workers",
    "parallel_setup_cost", "parallel_tuple_cost",
    "min_parallel_table_scan_size", "min_parallel_index_scan_size",
    "effective_io_concurrency", "maintenance_io_concurrency",
]

# Курируемый набор — показываем даже если равен дефолту (важен для тюнинга/аудита).
IMPORTANT = [
    # память
    "shared_buffers", "effective_cache_size", "work_mem", "maintenance_work_mem",
    "huge_pages", "temp_buffers", "hash_mem_multiplier",
    # WAL / чекпоинты / архивация (category 'Write-Ahead Log / *' → отдельная таблица)
    "wal_level", "wal_compression", "max_wal_size", "min_wal_size",
    "checkpoint_completion_target", "checkpoint_timeout", "wal_buffers",
    "synchronous_commit", "fsync", "full_page_writes", "wal_sync_method",
    "wal_log_hints", "archive_mode", "archive_command", "archive_timeout",
    # autovacuum
    "autovacuum", "autovacuum_max_workers", "autovacuum_naptime",
    "autovacuum_vacuum_cost_limit", "autovacuum_vacuum_cost_delay",
    "autovacuum_vacuum_scale_factor", "autovacuum_analyze_scale_factor",
    "autovacuum_freeze_max_age",
    # репликация (category 'Replication / *' → отдельная таблица в отчёте)
    "max_wal_senders", "max_replication_slots", "wal_keep_size",
    "max_slot_wal_keep_size", "wal_sender_timeout", "synchronous_standby_names",
    "hot_standby", "hot_standby_feedback", "wal_receiver_timeout",
    "max_standby_streaming_delay", "max_standby_archive_delay",
    "primary_conninfo", "primary_slot_name",
    # диагностика (не логирование)
    "track_io_timing", "shared_preload_libraries",
    # логирование (category 'Reporting and Logging / *' → отдельная таблица)
    "logging_collector", "log_destination", "log_min_messages",
    "log_min_error_statement", "log_min_duration_statement",
    "log_checkpoints", "log_connections", "log_disconnections", "log_duration",
    "log_statement", "log_error_verbosity", "log_line_prefix", "log_hostname",
    "log_lock_waits", "log_temp_files", "log_autovacuum_min_duration",
    "log_replication_commands",
] + CONNECTION_SETTINGS + QUERY_PARALLEL_SETTINGS


def raw(conn: psycopg.Connection) -> dict[str, dict]:
    """Все строки pg_settings по имени (для settings.collect и tuning.py)."""
    rows = query(
        conn,
        """
        SELECT name, setting, unit, boot_val, reset_val, source, context,
               pending_restart, category, short_desc
        FROM pg_settings
        """,
    )
    return {r["name"]: r for r in rows}


def collect(conn: psycopg.Connection, settings: dict[str, dict] | None = None) -> list[Setting]:
    settings = settings if settings is not None else raw(conn)
    important = set(IMPORTANT)
    out: list[Setting] = []
    for name, r in sorted(settings.items()):
        non_default = r["source"] not in (None, "default")
        if not non_default and name not in important:
            continue
        out.append(
            Setting(
                name=name,
                setting=r["setting"],
                unit=r["unit"],
                boot_val=r["boot_val"],
                reset_val=r["reset_val"],
                source=r["source"],
                context=r["context"],
                pending_restart=bool(r["pending_restart"]),
                category=r["category"],
                short_desc=r["short_desc"],
                is_default=(r["reset_val"] == r["boot_val"]),
            )
        )
    return out
