"""Конфигурация целей сбора: чтение и валидация targets.yaml.

Секреты в файле не хранятся: пароль берётся из переменной окружения
(`password_env`) или из ~/.pgpass (стандартный механизм libpq).
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
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
