"""Pydantic-модели собранных данных — единый контракт для шаблонов и JSON-дампа.

Одна БД описывается моделью `Database`. Все размеры хранятся в байтах, плюс
человекочитаемое поле `*_pretty`. Статусы (ok/warn/crit/unknown) считаются в
`collector.py` по порогам из конфига и используются шаблоном для бейджей.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Status(str, Enum):
    ok = "ok"
    warn = "warn"
    crit = "crit"
    unknown = "unknown"


# --- Обзор -----------------------------------------------------------------

class Extension(BaseModel):
    name: str
    version: str | None = None
    schema_name: str | None = None
    available_version: str | None = None
    outdated: bool = False


class Overview(BaseModel):
    version_num: int = 0
    version: str = ""
    size_bytes: int = 0
    size_pretty: str = "0 bytes"
    encoding: str | None = None
    collate: str | None = None
    ctype: str | None = None
    connections: int = 0
    connection_limit: int = -1
    n_schemas: int = 0
    n_tables: int = 0
    n_indexes: int = 0
    cache_hit_ratio: float | None = None
    temp_files: int = 0
    temp_bytes: int = 0
    temp_bytes_pretty: str = "0 bytes"
    deadlocks: int = 0
    default_statistics_target: int | None = None
    extensions: list[Extension] = Field(default_factory=list)


# --- Схемы -----------------------------------------------------------------

class Schema(BaseModel):
    name: str
    owner: str | None = None
    size_bytes: int = 0
    size_pretty: str = "0 bytes"
    n_tables: int = 0
    n_indexes: int = 0
    n_functions: int = 0


# --- Роли и привилегии -----------------------------------------------------

class Role(BaseModel):
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


class Grant(BaseModel):
    grantee: str
    object_type: str          # table / sequence / schema / function
    schema_name: str | None = None
    object_name: str | None = None
    privileges: str = ""      # агрегированный список: "SELECT, INSERT"


class RolesSection(BaseModel):
    accessible: bool = True
    roles: list[Role] = Field(default_factory=list)
    grants: list[Grant] = Field(default_factory=list)


# --- Таблицы ---------------------------------------------------------------

class Column(BaseModel):
    name: str
    type: str
    not_null: bool = False
    default: str | None = None
    identity: str | None = None
    stats_target: int | None = None   # attstattarget, если задан вручную
    comment: str | None = None


class Partition(BaseModel):
    name: str
    bound: str | None = None
    size_bytes: int = 0
    size_pretty: str = "0 bytes"
    row_estimate: int = 0


class Index(BaseModel):
    name: str
    definition: str
    method: str | None = None
    unique: bool = False
    primary: bool = False
    valid: bool = True
    size_bytes: int = 0
    size_pretty: str = "0 bytes"
    idx_scan: int | None = None
    unused: bool = False
    duplicate_of: str | None = None
    bloat_pct: float | None = None          # распухание = 100 - плотность листьев, %
    fragmentation_pct: float | None = None  # фрагментация листьев (pgstatindex), %
    status: Status = Status.ok


class Constraint(BaseModel):
    name: str
    type: str                 # p / f / u / c / x (PK/FK/UNIQUE/CHECK/EXCLUDE)
    definition: str
    valid: bool = True


class Trigger(BaseModel):
    name: str
    timing: str = ""          # BEFORE / AFTER / INSTEAD OF
    events: str = ""          # "INSERT, UPDATE"
    level: str = ""           # ROW / STATEMENT
    function: str = ""        # schema.func
    enabled: bool = True
    enabled_state: str = "enabled"   # enabled / disabled / replica / always
    definition: str = ""


class BloatInfo(BaseModel):
    method: str = "estimate"          # estimate | pgstattuple
    heap_bloat_pct: float | None = None
    heap_bloat_bytes: int | None = None
    toast_size_bytes: int = 0
    toast_size_pretty: str = "0 bytes"
    toast_bloat_pct: float | None = None
    status: Status = Status.unknown


class AutovacInfo(BaseModel):
    last_vacuum: str | None = None
    last_autovacuum: str | None = None
    last_analyze: str | None = None
    last_autoanalyze: str | None = None
    n_live_tup: int = 0
    n_dead_tup: int = 0
    dead_pct: float | None = None
    n_mod_since_analyze: int = 0
    vacuum_threshold: int | None = None
    analyze_threshold: int | None = None
    over_vacuum_threshold: bool = False
    over_analyze_threshold: bool = False
    reloptions: list[str] = Field(default_factory=list)
    relfrozenxid_age: int | None = None
    wraparound_pct: float | None = None
    status: Status = Status.ok


class Table(BaseModel):
    schema_name: str
    name: str
    owner: str | None = None
    partitioned: bool = False
    partition_strategy: str | None = None
    partition_key: str | None = None
    has_default_partition: bool = False  # для партиционированной — есть ли DEFAULT-партиция
    parent: str | None = None            # для партиции — родитель
    persistence: str = "permanent"       # permanent / temporary / unlogged
    row_estimate: int = 0
    has_pk: bool = False
    rls_enabled: bool = False             # включён row-level security
    comment: str | None = None
    cache_hit_ratio: float | None = None
    seq_scan: int | None = None
    idx_scan: int | None = None
    hot_update_pct: float | None = None   # доля HOT-обновлений
    total_bytes: int = 0
    total_pretty: str = "0 bytes"
    heap_bytes: int = 0
    heap_pretty: str = "0 bytes"
    index_bytes: int = 0
    index_pretty: str = "0 bytes"
    publications: list[str] = Field(default_factory=list)  # публикации, включающие таблицу
    columns: list[Column] = Field(default_factory=list)
    partitions: list[Partition] = Field(default_factory=list)
    indexes: list[Index] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    triggers: list[Trigger] = Field(default_factory=list)
    bloat: BloatInfo = Field(default_factory=BloatInfo)
    autovac: AutovacInfo = Field(default_factory=AutovacInfo)
    status: Status = Status.ok

    @property
    def fqname(self) -> str:
        return f"{self.schema_name}.{self.name}"


# --- Дополнительные метрики (db-уровень) -----------------------------------

class SequenceUsage(BaseModel):
    schema_name: str
    name: str
    owned_by: str | None = None
    data_type: str | None = None
    last_value: int | None = None
    max_value: int | None = None
    pct_used: float | None = None
    status: Status = Status.ok


class LongTransaction(BaseModel):
    pid: int
    usename: str | None = None
    state: str | None = None
    xact_age_seconds: int | None = None
    query: str | None = None


class TopObject(BaseModel):
    kind: str                 # table / index / toast
    schema_name: str
    name: str
    size_bytes: int
    size_pretty: str


class QueryStat(BaseModel):
    queryid: int | None = None
    calls: int = 0
    total_time_ms: float = 0.0
    mean_time_ms: float = 0.0
    rows: int = 0
    query: str = ""


class Extras(BaseModel):
    sequences: list[SequenceUsage] = Field(default_factory=list)
    long_transactions: list[LongTransaction] = Field(default_factory=list)
    top_objects: list[TopObject] = Field(default_factory=list)
    tables_without_pk: list[str] = Field(default_factory=list)
    tables_without_index: list[str] = Field(default_factory=list)
    unused_indexes: list[str] = Field(default_factory=list)
    unused_index_bytes: int = 0
    unused_index_pretty: str = "0 bytes"
    invalid_indexes: list[str] = Field(default_factory=list)
    fk_without_index: list[str] = Field(default_factory=list)
    custom_stats_columns: list[str] = Field(default_factory=list)
    partition_outliers: list[str] = Field(default_factory=list)
    top_queries: list[QueryStat] = Field(default_factory=list)


# --- Прочие объекты БД ------------------------------------------------------

class View(BaseModel):
    schema_name: str
    name: str
    owner: str | None = None
    materialized: bool = False
    populated: bool = True            # для matview: заполнено ли
    size_pretty: str = "0 bytes"      # для matview
    comment: str | None = None
    definition: str = ""


class Routine(BaseModel):
    schema_name: str
    name: str
    kind: str                         # function / procedure / aggregate / window
    language: str | None = None
    returns: str | None = None
    security_definer: bool = False
    volatility: str | None = None     # immutable / stable / volatile
    comment: str | None = None


class ForeignServer(BaseModel):
    name: str
    fdw: str
    version: str | None = None
    options: str | None = None


class ForeignTable(BaseModel):
    schema_name: str
    name: str
    server: str
    options: str | None = None


class TypeDef(BaseModel):
    schema_name: str
    name: str
    kind: str                         # enum / composite / domain / range
    owner: str | None = None
    detail: str | None = None         # значения enum / базовый тип домена


# --- Безопасность и доступ --------------------------------------------------

class Policy(BaseModel):
    schema_name: str
    table_name: str
    name: str
    command: str = "ALL"              # ALL / SELECT / INSERT / UPDATE / DELETE
    permissive: bool = True
    roles: str = ""
    using_expr: str | None = None
    check_expr: str | None = None


class DefaultPrivilege(BaseModel):
    owner: str
    schema_name: str | None = None
    object_type: str                  # tables / sequences / functions / types
    grantee: str
    privileges: str = ""


class EventTrigger(BaseModel):
    name: str
    event: str
    function: str
    enabled: bool = True
    enabled_state: str = "enabled"
    tags: str | None = None


# --- Сводка проблем ---------------------------------------------------------

class Issue(BaseModel):
    severity: Status                 # warn / crit
    category: str                    # человекочитаемая категория
    entity: str                      # объект (fqname / имя)
    detail: str = ""                 # пояснение


# --- Публикации (логическая репликация) ------------------------------------

class PublicationTable(BaseModel):
    name: str                          # fqname schema.table
    columns: list[str] = Field(default_factory=list)  # опубликованные колонки (пусто = все)
    row_filter: str | None = None      # WHERE-фильтр строк (PG15+)


class Publication(BaseModel):
    name: str
    owner: str | None = None
    all_tables: bool = False           # FOR ALL TABLES
    operations: str = ""               # "insert, update, delete, truncate"
    via_root: bool = False             # publish_via_partition_root
    tables: list[PublicationTable] = Field(default_factory=list)


# --- Корень ----------------------------------------------------------------

class Database(BaseModel):
    name: str
    title: str | None = None
    target: str = ""
    generated_at: str = ""
    overview: Overview = Field(default_factory=Overview)
    issues: list[Issue] = Field(default_factory=list)
    schemas: list[Schema] = Field(default_factory=list)
    roles: RolesSection = Field(default_factory=RolesSection)
    tables: list[Table] = Field(default_factory=list)
    views: list[View] = Field(default_factory=list)
    routines: list[Routine] = Field(default_factory=list)
    types: list[TypeDef] = Field(default_factory=list)
    foreign_servers: list[ForeignServer] = Field(default_factory=list)
    foreign_tables: list[ForeignTable] = Field(default_factory=list)
    policies: list[Policy] = Field(default_factory=list)
    default_privileges: list[DefaultPrivilege] = Field(default_factory=list)
    event_triggers: list[EventTrigger] = Field(default_factory=list)
    publications: list[Publication] = Field(default_factory=list)
    extras: Extras = Field(default_factory=Extras)
    warnings: list[str] = Field(default_factory=list)   # недоступные разделы и т.п.

    def dump_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def load_json(cls, text: str) -> "Database":
        return cls.model_validate_json(text)
