"""Движок рекомендаций по конфигурации (таблица «дефолт / текущее / оптимизация»).

Rule-based, не автотюнинг. Каждое правило сравнивает текущее значение GUC с
best-practice и, при наличии, с вводными о железе/нагрузке из targets.yaml.
Природа рекомендации помечается в `TuningItem.basis`:
  - inputs    — посчитано от заданного RAM/CPU/типа диска/нагрузки;
  - pg-data   — обосновано рантайм-сигналами (temp_files, req-checkpoint, cache hit);
  - heuristic — общая best-practice или формула-ориентир (вводных не хватило).

Без вводных из targets.yaml числовые рекомендации (shared_buffers и т.п.) остаются
формулой-ориентиром; список недостающих полей возвращается в `notes`.
"""

from __future__ import annotations

from pgcollect.config import Hardware, Workload
from .models import Status, TuningItem

_MULT = {"kb": 1024, "mb": 1024**2, "gb": 1024**3, "8kb": 8 * 1024, "16mb": 16 * 1024**2}


def _to_bytes(setting: str | None, unit: str | None) -> int | None:
    """Значение pg_settings + unit → байты (для параметров памяти)."""
    if setting is None:
        return None
    try:
        val = int(setting)
    except (TypeError, ValueError):
        return None
    if not unit:
        return val
    mult = _MULT.get(unit.lower())
    return val * mult if mult else None


def _human(nbytes: int) -> str:
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if nbytes < 1024 or unit == "TB":
            return f"{nbytes:.0f}{unit}" if unit != "B" else f"{nbytes}B"
        nbytes /= 1024
    return f"{nbytes:.0f}TB"


def build(
    settings: dict[str, dict],
    version_num: int,
    hardware: Hardware,
    workload: Workload,
    signals: dict | None = None,
) -> tuple[list[TuningItem], list[str]]:
    signals = signals or {}
    items: list[TuningItem] = []
    notes: list[str] = []

    def cur(name: str) -> dict | None:
        return settings.get(name)

    def add(name, category, recommended, rationale, priority, basis, unit=None):
        s = cur(name)
        if s is None:
            return
        u = unit or s["unit"]
        # Параметры памяти показываем человекочитаемо (128MB вместо «16384 8kB»).
        mem = bool(u and u.lower() in _MULT)
        db, cb = _to_bytes(s["boot_val"], u), _to_bytes(s["reset_val"], u)
        default = _human(db) if (mem and db is not None) else s["boot_val"]
        current = _human(cb) if (mem and cb is not None) else s["reset_val"]
        items.append(TuningItem(
            name=name, category=category,
            default=default, current=current,
            unit=None if mem else u,
            needs_restart=(s["context"] == "postmaster"),
            recommended=recommended, rationale=rationale,
            basis=basis, priority=priority,
        ))

    ram_mb = hardware.total_ram_mb
    cpu = hardware.cpu_count
    storage = (hardware.storage_type or "").lower() or None
    wl = (workload.kind or "").lower() or None

    _memory(add, cur, ram_mb, workload.connections, settings, signals)
    _wal(add, cur, signals)
    _planner(add, cur, storage)
    _parallelism(add, cur, cpu)
    _autovacuum(add, cur)
    _diagnostics(add, cur, version_num, wl)

    # Чего не хватило для точных чисел.
    if ram_mb is None:
        notes.append("hardware.total_ram_mb не задан → shared_buffers, effective_cache_size, "
                     "work_mem, maintenance_work_mem даны формулой-ориентиром, а не числом.")
    if cpu is None:
        notes.append("hardware.cpu_count не задан → рекомендации по параллелизму "
                     "(max_parallel_workers*) даны ориентиром.")
    if storage is None:
        notes.append("hardware.storage_type не задан (ssd/hdd/nvme) → random_page_cost и "
                     "effective_io_concurrency оставлены ориентиром.")
    if wl is None:
        notes.append("workload.kind не задан (oltp/olap/mixed) → часть рекомендаций "
                     "(jit, размер work_mem) обобщённые.")
    if workload.connections is None:
        notes.append("workload.connections не задан → work_mem рассчитан от max_connections "
                     "(обычно завышен). Укажите ожидаемое число одновременных соединений "
                     "для корректного work_mem.")
    return items, notes


def _memory(add, cur, ram_mb, connections, settings, signals) -> None:
    # shared_buffers ≈ 25% RAM.
    sb = cur("shared_buffers")
    if sb is not None:
        cur_bytes = _to_bytes(sb["reset_val"], sb["unit"])
        if ram_mb is not None:
            rec_bytes = int(ram_mb * 1024**2 * 0.25)
            rec = _human(rec_bytes)
            basis, prio = "inputs", Status.ok
            if cur_bytes is not None and cur_bytes < rec_bytes * 0.5:
                prio = Status.warn
            add("shared_buffers", "memory", rec,
                f"≈25% RAM ({ram_mb} МБ). Больше — если рабочий набор велик.",
                prio, basis)
        else:
            prio = Status.ok
            if cur_bytes is not None and cur_bytes <= 128 * 1024**2:
                prio = Status.warn  # дефолтные 128MB почти всегда мало на сервере
            add("shared_buffers", "memory", "≈25% RAM",
                "Ориентир 25% RAM; задайте hardware.total_ram_mb для числа.",
                prio, "heuristic")

    # effective_cache_size ≈ 75% RAM (влияет только на планировщик).
    ecs = cur("effective_cache_size")
    if ecs is not None:
        if ram_mb is not None:
            add("effective_cache_size", "memory", _human(int(ram_mb * 1024**2 * 0.75)),
                "≈75% RAM: оценка планировщиком объёма кэша ОС+PG.", Status.ok, "inputs")
        else:
            add("effective_cache_size", "memory", "≈75% RAM",
                "Ориентир 75% RAM; задайте total_ram_mb.", Status.ok, "heuristic")

    # maintenance_work_mem ≈ RAM/16, кап ~1–2 GB.
    mwm = cur("maintenance_work_mem")
    if mwm is not None:
        if ram_mb is not None:
            rec = min(int(ram_mb * 1024**2 / 16), 2 * 1024**3)
            add("maintenance_work_mem", "memory", _human(rec),
                "≈RAM/16 (кап 2 ГБ): ускоряет VACUUM/CREATE INDEX.", Status.ok, "inputs")
        else:
            add("maintenance_work_mem", "memory", "≈RAM/16 (кап 1–2 ГБ)",
                "Ориентир RAM/16; задайте total_ram_mb.", Status.ok, "heuristic")

    # work_mem — с учётом temp_files (рантайм-сигнал).
    wm = cur("work_mem")
    if wm is not None:
        temp_files = signals.get("temp_files_total", 0)
        if temp_files and temp_files > 0:
            add("work_mem", "memory", "увеличить (напр. ×2)",
                f"Замечены temp_files ({temp_files}) — запросам не хватает work_mem "
                "для сортировок/хэшей (данные PG).", Status.warn, "pg-data")
        elif ram_mb is not None:
            # Делим на ОЖИДАЕМОЕ число соединений (workload.connections), а не на
            # завышенный max_connections. Без вводных — падаем на max_connections.
            if connections:
                conns, src = connections, f"workload.connections ({connections})"
            else:
                mc = int(cur("max_connections")["reset_val"]) if cur("max_connections") else 100
                conns, src = mc, f"max_connections ({mc})"
            rec = int(ram_mb * 1024**2 * 0.25 / max(conns, 1))
            add("work_mem", "memory", _human(max(rec, 4 * 1024**2)),
                f"≈25% RAM / {src}. Осторожно: множится на операции.",
                Status.ok, "inputs")


def _wal(add, cur, signals) -> None:
    cct = cur("checkpoint_completion_target")
    if cct is not None and float(cct["reset_val"]) < 0.9:
        add("checkpoint_completion_target", "wal", "0.9",
            "Растягивает запись чекпоинта → сглаживает всплески I/O.",
            Status.warn, "heuristic")

    wc = cur("wal_compression")
    if wc is not None and wc["reset_val"] == "off":
        add("wal_compression", "wal", "on",
            "Сжатие full-page images уменьшает объём WAL (ценой CPU).",
            Status.ok, "heuristic")

    mws = cur("max_wal_size")
    if mws is not None:
        req_pct = signals.get("req_checkpoint_pct")
        cur_bytes = _to_bytes(mws["reset_val"], mws["unit"])
        if req_pct is not None and req_pct >= 30:
            add("max_wal_size", "wal", "увеличить (напр. ×2–4)",
                f"{req_pct}% чекпоинтов «по требованию» — WAL переполняет max_wal_size "
                "между таймерами (данные PG).", Status.warn, "pg-data")
        elif cur_bytes is not None and cur_bytes <= 1024**3:
            add("max_wal_size", "wal", "≥2–4 GB на нагруженном кластере",
                "Дефолт 1 ГБ часто мал → частые req-checkpoint.", Status.ok, "heuristic")


def _planner(add, cur, storage) -> None:
    rpc = cur("random_page_cost")
    if rpc is not None:
        val = float(rpc["reset_val"])
        if storage in ("ssd", "nvme"):
            if val > 1.5:
                add("random_page_cost", "planner", "1.1",
                    f"Для {storage} произвольный доступ дёшев — снизьте с {val}.",
                    Status.warn, "inputs")
        elif storage == "hdd":
            pass  # дефолт 4.0 адекватен HDD
        else:
            add("random_page_cost", "planner", "1.1 для SSD/NVMe",
                "Задайте hardware.storage_type для точной рекомендации.",
                Status.ok, "heuristic")

    eic = cur("effective_io_concurrency")
    if eic is not None:
        rec = {"ssd": "200", "nvme": "300", "hdd": "2"}.get(storage)
        if rec and eic["reset_val"] != rec:
            add("effective_io_concurrency", "planner", rec,
                f"Соответствует типу диска {storage} (параллельные предвыборки).",
                Status.ok, "inputs")
        elif not rec:
            add("effective_io_concurrency", "planner", "ssd:200 / nvme:300 / hdd:2",
                "Задайте hardware.storage_type.", Status.ok, "heuristic")


def _parallelism(add, cur, cpu) -> None:
    if cpu is None:
        add("max_parallel_workers_per_gather", "parallelism", "≈CPU/2 (OLAP) / 2 (OLTP)",
            "Задайте hardware.cpu_count для числа.", Status.ok, "heuristic")
        return
    mpw = cur("max_parallel_workers")
    if mpw is not None and int(mpw["reset_val"]) < cpu:
        add("max_parallel_workers", "parallelism", str(cpu),
            f"Не ниже числа ядер ({cpu}).", Status.ok, "inputs")
    mwp = cur("max_worker_processes")
    if mwp is not None and int(mwp["reset_val"]) < cpu:
        add("max_worker_processes", "parallelism", str(cpu),
            f"Не ниже числа ядер ({cpu}); требует рестарта.", Status.ok, "inputs")


def _autovacuum(add, cur) -> None:
    av = cur("autovacuum")
    if av is not None and av["reset_val"] == "off":
        add("autovacuum", "autovacuum", "on",
            "Выключенный autovacuum ведёт к распуханию и риску wraparound.",
            Status.crit, "heuristic")
    cl = cur("autovacuum_vacuum_cost_limit")
    if cl is not None and cl["reset_val"] in ("-1", "200"):
        add("autovacuum_vacuum_cost_limit", "autovacuum", "1000–2000 на нагруженном кластере",
            "Дефолт троттлит autovacuum; поднимите, если он не успевает.",
            Status.ok, "heuristic")


def _diagnostics(add, cur, version_num, wl) -> None:
    lmd = cur("log_min_duration_statement")
    if lmd is not None and lmd["reset_val"] == "-1":
        add("log_min_duration_statement", "diagnostics", "напр. 1000 (мс)",
            "Логирование медленных запросов — база для разбора производительности.",
            Status.ok, "heuristic")

    tit = cur("track_io_timing")
    if tit is not None and tit["reset_val"] == "off":
        add("track_io_timing", "diagnostics", "on",
            "Даёт I/O-тайминги в EXPLAIN/pg_stat_*; накладные расходы малы.",
            Status.ok, "heuristic")

    spl = cur("shared_preload_libraries")
    if spl is not None and "pg_stat_statements" not in (spl["reset_val"] or ""):
        add("shared_preload_libraries", "diagnostics", "+ pg_stat_statements",
            "pg_stat_statements — ключевой источник для анализа нагрузки (нужен рестарт).",
            Status.ok, "heuristic")

    jit = cur("jit")
    if jit is not None and jit["reset_val"] == "on" and wl == "oltp":
        add("jit", "planner", "off",
            "Для OLTP JIT обычно добавляет задержку компиляции без выгоды.",
            Status.ok, "inputs")
