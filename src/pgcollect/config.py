"""Конфигурация целей сбора: чтение и валидация targets.yaml.

Секреты в файле не хранятся: пароль берётся из переменной окружения
(`password_env`) или из ~/.pgpass (стандартный механизм libpq).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

_MODEL = ConfigDict(extra="forbid", coerce_numbers_to_str=False)


class LoadError(Exception):
    """Ошибка чтения или разбора YAML (не связана с валидацией схемы)."""


class Thresholds(BaseModel):
    """Пороги для расчёта статусов ok/warn/crit."""

    model_config = _MODEL

    bloat_warn_pct: float = 20.0
    bloat_crit_pct: float = 40.0
    dead_tuple_warn_pct: float = 10.0
    dead_tuple_crit_pct: float = 20.0
    # Выше этого размера точный pgstattuple не вызывается без --deep-bloat.
    pgstattuple_max_mb: int = 512
    # Доля возраста relfrozenxid от РЕАЛЬНОГО лимита wraparound (2^31 транзакций),
    # а НЕ от autovacuum_freeze_max_age (тот — лишь порог форсированного freeze-
    # autovacuum). 50% ≈ 1.07 млрд XID, 75% ≈ 1.61 млрд (≈ vacuum_failsafe_age).
    wraparound_warn_pct: float = 50.0
    wraparound_crit_pct: float = 75.0

    # --- Серверный уровень (pgservercollect) -------------------------------
    # Отставание физической реплики (по времени применения WAL), секунды.
    replica_lag_warn_s: float = 60.0
    replica_lag_crit_s: float = 300.0
    # Запас до max_connections: доля использованных backend'ов.
    connections_warn_pct: float = 80.0
    connections_crit_pct: float = 95.0
    # Доля «внеплановых» (по требованию) checkpoint'ов от общего числа.
    req_checkpoint_warn_pct: float = 30.0
    # WAL, удерживаемый неактивным слотом репликации (риск переполнения pg_wal), МБ.
    slot_retained_wal_warn_mb: int = 1024
    slot_retained_wal_crit_mb: int = 8192


class Hardware(BaseModel):
    """Подсказки о железе для рекомендаций тюнинга (pgservercollect).

    PG-only не может надёжно узнать общий RAM/CPU/тип диска, поэтому эти вводные
    задаются вручную. Без них рекомендации выдаются формулой-ориентиром, а не
    конкретным числом.
    """

    model_config = _MODEL

    total_ram_mb: int | None = None
    cpu_count: int | None = None
    # ssd / hdd / nvme — влияет на random_page_cost и effective_io_concurrency.
    storage_type: str | None = None


class Workload(BaseModel):
    """Профиль нагрузки для рекомендаций тюнинга: oltp / olap / mixed."""

    model_config = _MODEL

    kind: str | None = None          # oltp | olap | mixed
    # Ожидаемое число ОДНОВРЕМЕННЫХ соединений (не max_connections). От него, а не
    # от завышенного max_connections, считается work_mem = ~25% RAM / connections.
    connections: int | None = None


# Множители единиц объёма памяти → МБ (для поля Node.mem).
_MEM_UNITS = {
    "": 1, "mb": 1, "m": 1, "gb": 1024, "g": 1024,
    "tb": 1024**2, "t": 1024**2, "kb": 1 / 1024, "k": 1 / 1024,
}


def _parse_mem_mb(value: str | int | float | None) -> int | None:
    """ОЗУ узла → МБ. Число трактуем как МБ; строку — с единицей ("16GB", "512MB")."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*", str(value))
    if not m:
        return None
    mult = _MEM_UNITS.get(m.group(2).lower())
    if mult is None:
        return None
    return int(float(m.group(1)) * mult)


class Node(BaseModel):
    """Узел кластера: имя + железо для рекомендаций тюнинга (pgservercollect).

    Массив `nodes` описывает железо каждого узла кластера. pgservercollect
    подключается к ОДНОМУ экземпляру, поэтому для таблицы тюнинга берётся железо
    именно того узла, к которому подключились. Узел выбирается по имени: см.
    приоритет в `Target._find_node` (единственный узел / GUC cluster_name / имя
    цели). Надёжнее всего — назвать цель так же, как узел (одна цель = один узел).
    """

    model_config = _MODEL

    name: str
    # Роль в кластере: leader (primary) | replica. Рекомендации тюнинга считаются
    # от ресурсов узла с role: leader (конфиг кластера задаёт primary).
    role: str | None = None
    cpu: int | None = None            # число ядер CPU
    # ОЗУ: число = МБ, либо строка с единицей ("16GB", "16384MB", "512M").
    mem: str | int | None = None
    disk_type: str | None = None      # ssd | hdd | nvme

    @field_validator("role")
    @classmethod
    def _check_role(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v2 = str(v).strip().lower()
        if v2 not in ("leader", "replica"):
            raise ValueError("role должен быть 'leader' или 'replica'")
        return v2

    @property
    def mem_mb(self) -> int | None:
        return _parse_mem_mb(self.mem)

    def to_hardware(self) -> "Hardware":
        """Свести узел к общему контракту Hardware для движка тюнинга."""
        return Hardware(
            total_ram_mb=self.mem_mb,
            cpu_count=self.cpu,
            storage_type=self.disk_type,
        )


class Target(BaseModel):
    """Один кластер PostgreSQL как цель сбора."""

    model_config = _MODEL

    name: str
    title: str | None = None
    host: str = "127.0.0.1"
    port: int = 5432
    user: str = "postgres"
    password_env: str | None = None
    maintenance_db: str = "postgres"
    sslmode: str = "prefer"

    statement_timeout_ms: int = 30000
    lock_timeout_ms: int = 3000

    # Явный список БД; если None — обходим все пользовательские.
    databases: list[str] | None = None
    exclude_databases: list[str] = Field(default_factory=list)

    thresholds: Thresholds = Field(default_factory=Thresholds)

    # Подсказки для рекомендаций тюнинга (pgservercollect); pgcollect их игнорирует.
    # `nodes` — железо по каждому узлу кластера (приоритетнее одиночного `hardware`).
    hardware: Hardware = Field(default_factory=Hardware)
    workload: Workload = Field(default_factory=Workload)
    nodes: list[Node] = Field(default_factory=list)
    # Явный выбор узла из `nodes` для этой цели (по name). Приоритетнее авто-подбора;
    # нужен, когда имя цели не совпадает с именем узла, а cluster_name = scope (Patroni).
    node: str | None = None

    def _find_node(self, cluster_name: str | None) -> tuple["Node | None", str | None]:
        """Выбрать узел из `nodes` для подключённого экземпляра.

        Приоритет (pgservercollect подключается к ОДНОМУ узлу):
          1) явное поле `node` цели == node.name — прямой выбор оператором;
          2) единственный узел в списке — берётся он;
          3) node.name == GUC cluster_name (в Patroni cluster_name обычно = scope,
             общий для всех членов, поэтому сработает лишь если cluster_name задан
             per-member; для одиночного сервера — надёжно);
          4) node.name == имя цели (target.name): одна цель = один узел, назовите
             цель так же, как узел — полностью под контролем оператора.
        Возвращает (узел | None, как_сопоставлен | None).
        """
        if not self.nodes:
            return None, None
        if self.node:
            for n in self.nodes:
                if n.name == self.node:
                    return n, "по явному полю node цели"
            # node задан, но не найден в nodes — не подменяем молча авто-подбором.
            return None, None
        if len(self.nodes) == 1:
            return self.nodes[0], "единственный узел в списке"
        if cluster_name:
            for n in self.nodes:
                if n.name == cluster_name:
                    return n, "по GUC cluster_name"
        for n in self.nodes:
            if n.name == self.name:
                return n, "по имени цели (target.name)"
        return None, None

    def hardware_for(self, cluster_name: str | None = None) -> tuple[Hardware, str | None]:
        """Железо для тюнинга + пояснение выбора.

        Рекомендации считаются от ресурсов LEADER-узла: в кластере конфиг единый и
        задаётся нагрузкой на primary (checkpoint'ы, work_mem под пишущие запросы),
        независимо от того, к какому узлу подключились. Приоритет:
          1) узел с role: leader;
          2) (leader не помечен) авто-подбор по подключённому экземпляру (_find_node);
          3) одиночный блок `hardware` (обратная совместимость).
        Второй элемент кортежа — заметка для отчёта.
        """
        if not self.nodes:
            return self.hardware, None
        leaders = [n for n in self.nodes if n.role == "leader"]
        if leaders:
            n = leaders[0]
            extra = "" if len(leaders) == 1 else f" (из {len(leaders)} помеченных leader взят первый)"
            return n.to_hardware(), f"железо взято из leader-узла «{n.name}»{extra}."
        node, how = self._find_node(cluster_name)
        if node is not None:
            return node.to_hardware(), (
                f"role: leader не задан ни одному узлу → железо взято из узла «{node.name}» "
                f"({how})."
            )
        names = ", ".join(n.name for n in self.nodes)
        return self.hardware, (
            f"ни один узел не помечен role: leader и не сопоставлен с экземпляром "
            f"(cluster_name={cluster_name!r}, target.name={self.name!r}, node={self.node!r}) "
            f"среди [{names}] → числовые рекомендации по блоку hardware/ориентиру. "
            f"Пометьте leader-узел role: leader."
        )

    def password(self) -> str | None:
        """Пароль из окружения (по имени password_env). None → libpq/.pgpass."""
        if self.password_env:
            return os.environ.get(self.password_env)
        return None


class Config(BaseModel):
    """Корневой контейнер конфигурации — список целей."""

    model_config = _MODEL

    targets: list[Target]

    def get(self, name: str) -> Target | None:
        for t in self.targets:
            if t.name == name:
                return t
        return None


def load_config(path: str | Path) -> Config:
    """Прочитать targets.yaml и провалидировать по схеме `Config`."""
    path = Path(path)
    if not path.is_file():
        raise LoadError(f"Файл не найден: {path}")

    yaml = YAML(typ="safe")
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.load(f)
    except YAMLError as exc:
        raise LoadError(f"Не удалось разобрать YAML ({path}): {exc}") from exc

    if data is None:
        raise LoadError(f"Файл пуст: {path}")

    return Config.model_validate(data)
