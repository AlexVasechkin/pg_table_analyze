"""Pydantic-модели серверного (кластерного) отчёта pgservercollect.

Корень — `Server`. Переиспользуются `Status`/`Issue` из pgcollect.models: единый
контракт статусов и сводки проблем с per-database инструментом. Размеры — в
байтах плюс человекочитаемое `*_pretty`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from pgcollect.models import Issue, Status


# --- Идентичность / старт ---------------------------------------------------

class Identity(BaseModel):
    version: str = ""
    version_num: int = 0
    system_identifier: str | None = None
    cluster_name: str | None = None
    in_recovery: bool = False          # реплика (standby), если True
    timeline: int | None = None
    data_checksums: bool | None = None
    ssl_in_use: bool | None = None
    start_time: str | None = None       # pg_postmaster_start_time
    conf_load_time: str | None = None   # pg_conf_load_time
    uptime: str | None = None
    current_time: str | None = None


# --- Конфигурация -----------------------------------------------------------

class Setting(BaseModel):
    name: str
    setting: str | None = None          # текущее (эффективное) значение
    unit: str | None = None
    boot_val: str | None = None         # заводской дефолт
    reset_val: str | None = None
    source: str | None = None           # default / configuration file / …
    context: str | None = None          # postmaster / sighup / user …
    pending_restart: bool = False
    category: str | None = None
    short_desc: str | None = None
    is_default: bool = True             # setting == boot_val


# --- Таблица тюнинга --------------------------------------------------------

class TuningItem(BaseModel):
    name: str
    category: str = ""                  # memory / wal / planner / …
    default: str | None = None          # boot_val (человекочитаемо)
    current: str | None = None          # reset_val (человекочитаемо)
    unit: str | None = None
    needs_restart: bool = False         # context = postmaster
    recommended: str | None = None      # предлагаемое значение или формула-ориентир
    rationale: str = ""                 # краткое «почему»
    basis: str = "heuristic"            # inputs | heuristic | pg-data
    priority: Status = Status.ok        # crit / warn(=medium) / ok


# --- Потребление ОЗУ по компонентам -----------------------------------------

class MemoryLine(BaseModel):
    component: str                      # компонент/процесс — потребитель RAM
    detail: str = ""                    # расчёт/пояснение ("512MB × 20 backends")
    total_bytes: int = 0
    total_pretty: str = "0 bytes"
    kind: str = "shared"                # shared | maintenance | connections


class MemoryScenario(BaseModel):
    name: str                           # Минимальное / Среднее / Максимальное
    connections: int = 0                # число client backend'ов в сценарии
    basis: str = ""                     # краткое описание допущений сценария
    lines: list[MemoryLine] = Field(default_factory=list)
    total_bytes: int = 0
    total_pretty: str = "0 bytes"
    pct_of_ram: float | None = None     # доля от ОЗУ leader-узла, если задан mem
    over_ram: bool = False              # итог превышает ОЗУ leader-узла


class MemoryReport(BaseModel):
    ram_mb: int | None = None           # ОЗУ leader-узла (для % и предупреждений)
    notes: list[str] = Field(default_factory=list)
    scenarios: list[MemoryScenario] = Field(default_factory=list)


# --- Базы данных (сводка по кластеру) ---------------------------------------

class DatabaseSummary(BaseModel):
    name: str
    owner: str | None = None
    size_bytes: int = 0
    size_pretty: str = "0 bytes"
    encoding: str | None = None
    collate: str | None = None
    connection_limit: int = -1
    connections: int = 0
    frozenxid_age: int | None = None    # age(datfrozenxid) — wraparound на уровне БД
    wraparound_pct: float | None = None
    xact_commit: int = 0
    xact_rollback: int = 0
    deadlocks: int = 0
    temp_files: int = 0
    temp_bytes_pretty: str = "0 bytes"
    cache_hit_ratio: float | None = None
    status: Status = Status.ok


# --- Репликация -------------------------------------------------------------

class ReplicationStandby(BaseModel):
    """Строка pg_stat_replication (со стороны primary): подключённый standby."""
    application_name: str | None = None
    client_addr: str | None = None
    state: str | None = None
    sync_state: str | None = None       # sync / async / potential / quorum
    write_lag_s: float | None = None
    flush_lag_s: float | None = None
    replay_lag_s: float | None = None
    lag_bytes: int | None = None        # sent_lsn - replay_lsn
    lag_pretty: str | None = None
    status: Status = Status.ok


class ReplicationSlot(BaseModel):
    slot_name: str
    slot_type: str | None = None        # physical / logical
    active: bool = False
    plugin: str | None = None
    retained_wal_bytes: int | None = None
    retained_wal_pretty: str | None = None
    wal_status: str | None = None       # reserved / extended / unreserved / lost (PG13+)
    status: Status = Status.ok


class WalReceiver(BaseModel):
    """pg_stat_wal_receiver (со стороны standby): upstream primary."""
    status: str | None = None
    sender_host: str | None = None
    sender_port: int | None = None
    received_lsn: str | None = None
    latest_end_lsn: str | None = None
    last_msg_receipt_time: str | None = None


class Replication(BaseModel):
    role: str = "primary"               # primary / standby
    replay_paused: bool = False
    replay_lag_s: float | None = None   # на standby: now - last_xact_replay_timestamp
    receive_lsn: str | None = None
    replay_lsn: str | None = None
    synchronous_standby_names: str | None = None
    synchronous_commit: str | None = None
    primary_conninfo: str | None = None  # на standby: upstream (пароль вычищен)
    standbys: list[ReplicationStandby] = Field(default_factory=list)
    slots: list[ReplicationSlot] = Field(default_factory=list)
    wal_receiver: WalReceiver | None = None


# --- WAL / архивация --------------------------------------------------------

class WalInfo(BaseModel):
    current_lsn: str | None = None
    current_wal_file: str | None = None
    wal_level: str | None = None
    archive_mode: str | None = None
    archive_command: str | None = None
    archived_count: int | None = None
    failed_count: int | None = None
    last_archived_wal: str | None = None
    last_archived_time: str | None = None
    last_failed_wal: str | None = None
    last_failed_time: str | None = None
    archiving_behind: bool = False      # last_failed свежее last_archived
    status: Status = Status.ok


# --- Активность / фоновые процессы / локи -----------------------------------

class ActivityBucket(BaseModel):
    key: str                            # state / wait_event / db / user
    count: int = 0


class Activity(BaseModel):
    total: int = 0
    max_connections: int = 0
    used_pct: float | None = None
    by_state: list[ActivityBucket] = Field(default_factory=list)
    by_wait: list[ActivityBucket] = Field(default_factory=list)
    by_database: list[ActivityBucket] = Field(default_factory=list)
    oldest_xact_age_s: int | None = None
    oldest_query_age_s: int | None = None
    idle_in_transaction: int = 0
    active: int = 0
    status: Status = Status.ok


class BgWriter(BaseModel):
    checkpoints_timed: int | None = None
    checkpoints_req: int | None = None
    req_pct: float | None = None        # доля checkpoint'ов «по требованию»
    checkpoint_write_time_ms: float | None = None
    checkpoint_sync_time_ms: float | None = None
    buffers_checkpoint: int | None = None
    buffers_clean: int | None = None
    buffers_backend: int | None = None
    maxwritten_clean: int | None = None
    stats_reset: str | None = None
    status: Status = Status.ok


class BlockedLock(BaseModel):
    blocked_pid: int
    blocked_user: str | None = None
    blocking_pids: str = ""             # "123, 456"
    wait_seconds: int | None = None
    query: str | None = None


# --- Прочее кластерное ------------------------------------------------------

class Tablespace(BaseModel):
    name: str
    owner: str | None = None
    location: str | None = None
    size_bytes: int = 0
    size_pretty: str = "0 bytes"


class ServerRole(BaseModel):
    name: str
    can_login: bool = False
    superuser: bool = False
    createrole: bool = False
    createdb: bool = False
    replication: bool = False
    bypassrls: bool = False
    conn_limit: int = -1
    valid_until: str | None = None
    expired: bool = False
    member_of: list[str] = Field(default_factory=list)


class Subscription(BaseModel):
    name: str
    enabled: bool = True
    slot_name: str | None = None
    worker_count: int = 0
    received_lsn: str | None = None
    latest_end_lsn: str | None = None
    last_error: str | None = None
    status: Status = Status.ok


class PreparedXact(BaseModel):
    gid: str
    database: str | None = None
    owner: str | None = None
    prepared: str | None = None
    age_seconds: int | None = None


class ProgressOp(BaseModel):
    kind: str                           # vacuum / analyze / create index / …
    pid: int
    database: str | None = None
    phase: str | None = None
    detail: str | None = None


# --- Корень ------------------------------------------------------------------

class Server(BaseModel):
    target: str = ""
    title: str | None = None
    host: str = ""
    port: int = 5432
    generated_at: str = ""

    identity: Identity = Field(default_factory=Identity)
    issues: list[Issue] = Field(default_factory=list)
    tuning: list[TuningItem] = Field(default_factory=list)
    tuning_notes: list[str] = Field(default_factory=list)   # чего не хватает для точных чисел
    settings: list[Setting] = Field(default_factory=list)
    memory: MemoryReport | None = None
    databases: list[DatabaseSummary] = Field(default_factory=list)
    replication: Replication = Field(default_factory=Replication)
    wal: WalInfo = Field(default_factory=WalInfo)
    activity: Activity = Field(default_factory=Activity)
    bgwriter: BgWriter = Field(default_factory=BgWriter)
    locks: list[BlockedLock] = Field(default_factory=list)
    tablespaces: list[Tablespace] = Field(default_factory=list)
    roles: list[ServerRole] = Field(default_factory=list)
    subscriptions: list[Subscription] = Field(default_factory=list)
    prepared_xacts: list[PreparedXact] = Field(default_factory=list)
    progress: list[ProgressOp] = Field(default_factory=list)
    shared_preload_libraries: str | None = None
    warnings: list[str] = Field(default_factory=list)

    def dump_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def load_json(cls, text: str) -> "Server":
        return cls.model_validate_json(text)


__all__ = ["Server", "Status", "Issue"]
