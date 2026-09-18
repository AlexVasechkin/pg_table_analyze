"""Модель потребления ОЗУ по компонентам в трёх сценариях (min/avg/max).

Оценивает, сколько RAM потребляют компоненты и процессы кластера ИСХОДЯ ИЗ ТЕКУЩИХ
настроек (pg_settings): разделяемая память (shared_buffers, wal_buffers, прочие
структуры), maintenance/autovacuum-пул и приватная память backend'ов (базовый
оверхед + work_mem + temp_buffers).

Три сценария различаются числом соединений и интенсивностью work_mem:
  - Минимальное  — текущее число client backend'ов (pg_stat_activity), work_mem не
                   используется (простой/простые запросы) → «пол» потребления;
  - Среднее      — то же число соединений, по 1 аллокации work_mem на backend;
  - Максимальное — насыщение до max_connections, до WORK_MEM_OPS_MAX аллокаций
                   work_mem на backend + temp_buffers → «потолок».

Разделяемая память и maintenance-пул одинаковы во всех сценариях (не зависят от
числа клиентов).

Источники значений по строкам «Соединения»:
  - базовый оверхед — ФИКСИРОВАННАЯ оценка BACKEND_BASE_BYTES (не GUC): моделирует
    приватную память процесса backend'а (кэш системного каталога/relcache, кэш
    планов, prepared-операторы); реальная величина зависит от активности сессии;
  - work_mem — из GUC `work_mem` (на КАЖДЫЙ узел сортировки/хэша в плане). Для
    хэш-узлов (Hash Join/Aggregate) допустимый предел — `work_mem × hash_mem_multiplier`
    (PG13+), поэтому в максимальном сценарии размер аллокации берётся с этим множителем;
  - temp_buffers — из GUC `temp_buffers` (буфер временных таблиц на сессию, максимум).
"""

from __future__ import annotations

import psycopg

from pgcollect.db import NoAccess, query
from ..models import MemoryLine, MemoryReport, MemoryScenario

_MULT = {"kb": 1024, "mb": 1024**2, "gb": 1024**3, "8kb": 8 * 1024, "16mb": 16 * 1024**2}

# Приблизительная приватная память одного backend'а (кэши каталога/relcache/планов).
# Реальное значение варьируется; используется как оценка базового оверхода соединения.
BACKEND_BASE_BYTES = 5 * 1024**2
# Сколько одновременных аллокаций work_mem держит сложный план (сортировки/хэши) —
# для «максимального» сценария. work_mem выделяется на КАЖДЫЙ такой узел плана.
WORK_MEM_OPS_MAX = 3


def _to_bytes(setting: str | None, unit: str | None) -> int | None:
    if setting is None:
        return None
    try:
        val = int(setting)
    except (TypeError, ValueError):
        return None
    if not unit:
        return val
    mult = _MULT.get(unit.lower())
    return val * mult if mult else val


def _human(nbytes: float) -> str:
    n = float(nbytes)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def collect(
    conn: psycopg.Connection,
    raw_settings: dict[str, dict],
    ram_mb: int | None = None,
) -> MemoryReport:
    def b(name: str) -> int:
        s = raw_settings.get(name, {})
        return _to_bytes(s.get("reset_val"), s.get("unit")) or 0

    def i(name: str, default: int) -> int:
        s = raw_settings.get(name, {})
        try:
            return int(s.get("reset_val"))
        except (TypeError, ValueError):
            return default

    sb, wb, wm, tb = b("shared_buffers"), b("wal_buffers"), b("work_mem"), b("temp_buffers")
    mwm = b("maintenance_work_mem")
    awm = mwm if i("autovacuum_work_mem", -1) < 0 else (b("autovacuum_work_mem") or mwm)
    avw = i("autovacuum_max_workers", 3)
    mc = i("max_connections", 100)
    # hash-узлы (Hash Join/Aggregate) берут work_mem × hash_mem_multiplier (PG13+).
    try:
        hmm = float(raw_settings.get("hash_mem_multiplier", {}).get("reset_val") or 2.0)
    except (TypeError, ValueError):
        hmm = 2.0

    report = MemoryReport(ram_mb=ram_mb)

    # Текущее число client backend'ов (для min/avg сценариев).
    current = query(
        conn,
        "SELECT count(*) AS n FROM pg_stat_activity WHERE backend_type = 'client backend'",
    )[0]["n"] or 0

    # Реальная суммарная разделяемая память, если доступно pg_shmem_allocations.
    other_shared = 0
    # pg_shmem_allocations доступна только superuser/pg_read_all_stats — при нехватке
    # прав (NoAccess) деградируем к shared_buffers+wal_buffers, не роняя весь раздел.
    try:
        r = query(conn, "SELECT sum(allocated_size)::bigint AS s FROM pg_shmem_allocations")
        total_shared = int(r[0]["s"]) if r and r[0]["s"] is not None else None
    except (NoAccess, psycopg.Error):
        total_shared = None
    if total_shared is not None:
        other_shared = max(total_shared - sb - wb, 0)
    else:
        report.notes.append(
            "pg_shmem_allocations недоступна (нужен superuser/pg_read_all_stats) → прочие "
            "разделяемые структуры (locks, PGPROC и т.п.) не учтены; показаны только "
            "shared_buffers и wal_buffers."
        )

    maint = avw * awm  # autovacuum-воркеры держат до maintenance_work_mem каждый

    # (имя, число соединений, множитель work_mem, учитывать temp_buffers, worst-case hash, описание)
    plans = [
        ("Минимальное", current, 0, False, False,
         f"текущие {current} client backend'ов (pg_stat_activity); work_mem не используется "
         "(простаивающие/простые запросы)"),
        ("Среднее", current, 1, False, False,
         f"текущие {current} соединений; по 1 аллокации work_mem на backend"),
        ("Максимальное", mc, WORK_MEM_OPS_MAX, True, True,
         f"насыщение до max_connections ({mc}); до {WORK_MEM_OPS_MAX} аллокаций work_mem "
         f"(для хэшей × hash_mem_multiplier) на backend + temp_buffers на сессию"),
    ]

    for name, n, ops, with_temp, hash_worst, basis in plans:
        lines: list[MemoryLine] = [
            MemoryLine(component="shared_buffers", detail="разделяемый пул страниц",
                       kind="shared", total_bytes=sb, total_pretty=_human(sb)),
            MemoryLine(component="wal_buffers", detail="буфер WAL",
                       kind="shared", total_bytes=wb, total_pretty=_human(wb)),
        ]
        if other_shared:
            lines.append(MemoryLine(
                component="Прочие разделяемые структуры",
                detail="locks, PGPROC и др. (pg_shmem_allocations)",
                kind="shared", total_bytes=other_shared, total_pretty=_human(other_shared)))
        lines.append(MemoryLine(
            component="maintenance / autovacuum",
            detail=f"{avw} × {_human(awm)} (autovacuum_max_workers × maintenance_work_mem)",
            kind="maintenance", total_bytes=maint, total_pretty=_human(maint)))
        base_total = n * BACKEND_BASE_BYTES
        lines.append(MemoryLine(
            component="Соединения: базовый оверхед",
            detail=f"{n} backend'ов × {_human(BACKEND_BASE_BYTES)} — ФИКСИРОВАННАЯ оценка "
                   "приватной памяти процесса (кэш каталога/relcache, планы, prepared); "
                   "не задаётся GUC",
            kind="connections", total_bytes=base_total, total_pretty=_human(base_total)))
        # work_mem — из GUC work_mem; в максимальном сценарии для хэшей ×hash_mem_multiplier.
        wm_unit = int(wm * hmm) if hash_worst else wm
        if ops == 0:
            wm_detail = f"{n} backend'ов × 0 — сортировки/хэши не выполняются (простой)"
        elif hash_worst:
            wm_detail = (f"{n} backend'ов × {ops} × work_mem×hash_mem_multiplier "
                         f"({_human(wm)}×{hmm:g} = {_human(wm_unit)}) — на каждый узел сортировки/хэша")
        else:
            wm_detail = (f"{n} backend'ов × {ops} × work_mem ({_human(wm)}) "
                         "— на каждый узел сортировки/хэша в плане")
        wm_total = n * ops * wm_unit
        lines.append(MemoryLine(
            component="Соединения: work_mem",
            detail=wm_detail,
            kind="connections", total_bytes=wm_total, total_pretty=_human(wm_total)))
        if with_temp:
            temp_total = n * tb
            lines.append(MemoryLine(
                component="Соединения: temp_buffers",
                detail=f"{n} backend'ов × temp_buffers ({_human(tb)}) — буфер временных таблиц "
                       "на сессию (макс.)",
                kind="connections", total_bytes=temp_total, total_pretty=_human(temp_total)))

        total = sum(line.total_bytes for line in lines)
        pct = round(100.0 * total / (ram_mb * 1024**2), 1) if ram_mb else None
        report.scenarios.append(MemoryScenario(
            name=name, connections=n, basis=basis, lines=lines,
            total_bytes=total, total_pretty=_human(total),
            pct_of_ram=pct, over_ram=bool(ram_mb and total > ram_mb * 1024**2)))

    if ram_mb:
        mx = report.scenarios[-1]
        if mx.over_ram:
            report.notes.append(
                f"Максимальный сценарий ({mx.total_pretty}) превышает ОЗУ leader-узла "
                f"({_human(ram_mb * 1024**2)}) — риск нехватки памяти/OOM при насыщении соединений."
            )
    else:
        report.notes.append(
            "ОЗУ leader-узла не задано → доля от RAM (%) не рассчитана; укажите mem у "
            "leader-узла в targets.yaml."
        )
    return report
