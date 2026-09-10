# pg-collector — план работ

Инструмент read-only обхода кластера PostgreSQL 14+ (в основном 16): по каждой БД
собирает схемы, роли/группы и их связи с объектами, структуру таблиц (колонки,
партиции, индексы, ограничения, триггеры), анализ распухания (bloat) таблиц/индексов/TOAST,
качество автовакуума и свежесть статистики, публикации логической репликации, прочие
объекты (представления, функции, типы, FDW) и безопасность (RLS, default privileges,
event triggers). Отчёт начинается со «Сводки проблем». На каждую БД генерирует
HTML-документ и документ для Confluence (storage-format XML-артефакт).

Стек: Python 3.10+, psycopg (v3), pydantic v2, Jinja2, typer. Слой рендера
переиспользуется из соседнего проекта `../doc-gen/src/pgdoc` (`render.py`, `preview.py`).

## Каркас проекта
- [x] pyproject.toml (hatchling; deps: psycopg[binary], pydantic, jinja2, typer, ruamel.yaml); console_script `pgcollect`
- [x] README.md с примерами запуска и требованиями к правам БД
- [x] targets.example.yaml — реестр кластеров/подключений (секреты из env/.pgpass, не в файле)
- [x] .gitignore (out/, .venv/, __pycache__)

## Подключение и обход
- [x] config.py — pydantic-модель targets.yaml; чтение секретов из окружения/.pgpass
- [x] db.py — psycopg read-only коннект: `default_transaction_read_only=on`, `statement_timeout`, `lock_timeout`
- [x] db.enumerate_databases() — список БД из pg_database (искл. datistemplate и datallowconn=false)
- [x] db.py — обёртка «нет прав/недоступно» вместо падения (для не-суперпользователя)

## Сбор данных (sql/*, один модуль на раздел)
- [x] overview.py — версия, размер БД, кодировка/локаль/collate, расширения, активные подключения
- [x] schemas.py — схемы (pg_namespace), владельцы, суммарные размеры, число объектов
- [x] roles.py — роли (pg_roles), членство (pg_auth_members, граф групп→ролей), атрибуты; ACL/привилегии (aclexplode) и владельцы объектов
- [x] tables.py — колонки (тип/NULL/default/identity/attstattarget); партиции (стратегия/ключ/границы relpartbound); размеры heap/index/TOAST
- [x] indexes.py — определения (pg_get_indexdef), метод, уникальность, размер; unused (idx_scan=0), duplicate, invalid; FK без покрывающего индекса
- [x] constraints.py — PK/FK/UNIQUE/CHECK/EXCLUDE (pg_get_constraintdef); NOT VALID; действия FK (ON DELETE/UPDATE)
- [x] bloat.py — оценочный bloat heap/index/TOAST (метод ioguix/check_postgres); опц. pgstattuple/pgstattuple_approx под порогом размера; флаг --deep-bloat
- [x] stats.py — pg_stat_user_tables: last_(auto)vacuum/analyze, n_live_tup/n_dead_tup, n_mod_since_analyze
- [x] autovacuum.py — GUC + per-table reloptions; расчёт порогов срабатывания; «долги» (перешла порог, но не обработана); возраст relfrozenxid (wraparound)

## Модель и оркестрация
- [x] models.py — pydantic-модели собранных данных (единый контракт для шаблонов и JSON-дампа)
- [x] collector.py — обход БД, вызов sql-модулей, сборка Database, расчёт статусов (ok/warn/crit) по порогам из конфига

## Рендер (переиспользование pgdoc)
- [x] render.py — Jinja2 → Confluence storage-format (адаптация ../doc-gen/src/pgdoc/render.py: build_env, фильтр `d`)
- [x] preview.py — storage-format → автономный HTML (перенос ../doc-gen/src/pgdoc/preview.py; таблицы, макросы info/note/warning, Mermaid)
- [x] templates/_macros.xml.j2 — макросы таблиц и бейджи статусов
- [x] templates/database.xml.j2 — страница одной БД со всеми разделами

## CLI (typer)
- [x] collect — сбор из БД → out/<db>.json
- [x] render — out/<db>.json → out/<db>.confluence.xml
- [x] preview — out/<db>.json → out/<db>.html
- [x] all — collect + render + preview за один прогон
- [x] флаги: --target, --db, --skip <раздел>, --deep-bloat, --stat-statements, --out

## Дополнительные метрики (сверх запрошенного)
- [x] Wraparound-риск: age(relfrozenxid) по таблицам/БД против autovacuum_freeze_max_age; топ «старых»
- [x] Cache hit ratio по БД и крупным таблицам (pg_statio_user_tables)
- [x] Долгие/`idle in transaction` транзакции (pg_stat_activity), старейший xact_start — блокируют очистку dead tuples
- [x] Sequences на исчерпание: текущее значение против max типа колонки (риск переполнения int4-PK)
- [x] Партиции-аутлайеры: пустые/переполненные, отсутствие DEFAULT-партиции, близость к границе диапазона
- [x] Дубли/избыточность индексов и FK; FK на неиндексированные колонки
- [x] Таблицы без PK и без индексов
- [x] Топ-N крупнейших объектов (таблицы/индексы/TOAST)
- [x] Temp files/bytes и work_mem-давление (pg_stat_database)
- [x] Расширения и версии против доступных (pg_available_extension_versions)
- [x] default_statistics_target отклонения и колонки с ручным attstattarget
- [x] (опц.) Раздел pg_stat_statements, если расширение установлено — по умолчанию выключено

## Публикации логической репликации (sql/publications.py) — добавлено сверх плана
- [x] Список публикаций (pg_publication): владелец, операции (insert/update/delete/truncate), FOR ALL TABLES
- [x] publish_via_partition_root — при false состав раскрывается до листовых партиций
- [x] Состав по каждой публикации (pg_publication_tables): таблицы, опубликованные колонки, фильтр строк (attnames/rowfilter, PG15+; версионно-зависимо)
- [x] Обратная привязка: у каждой таблицы список публикаций, в которые она входит

## Триггеры (sql/triggers.py) — добавлено сверх плана
- [x] Триггеры таблиц: когда (BEFORE/AFTER/INSTEAD OF), события, уровень (ROW/STATEMENT), функция, состояние, определение
- [x] Внутренние триггеры (реализация FK и пр., tgisinternal) отфильтрованы; отключённые помечаются

## Сводка проблем (Database.issues, collector._collect_issues) — добавлено сверх плана
- [x] Плоский список проблемных сущностей вверху отчёта (важность/категория/объект/детали), крит вперёд
- [x] Баннер warning/note/tip со счётчиком crit/warn; данные попадают и в JSON
- [x] Категории: распухание, мёртвые кортежи, wraparound, невалидный/неиспользуемые индексы, FK без индекса, таблицы без PK/индексов, аномалии партиций, отключённые триггеры/event triggers, sequence, долгие транзакции, устаревшие расширения, SECURITY DEFINER, просроченные роли, преобладание seq scan
- [x] Подсветка строк по важности (sev-crit/sev-warn) в списке таблиц и ролей

## Прочие объекты БД (sql/objects.py) — добавлено сверх плана
- [x] Представления и материализованные представления (определение, владелец, размер, populated, комментарий)
- [x] Функции/процедуры: язык, возвращаемый тип, волатильность, флаг SECURITY DEFINER
- [x] Пользовательские типы: enum (значения), domain (базовый тип), composite, range
- [x] FDW: сторонние серверы и таблицы с опциями
- [x] Объекты расширений исключены (pg_depend deptype='e'), чтобы не засорять отчёт

## Безопасность и доступ (sql/security.py) — добавлено сверх плана
- [x] RLS-политики (pg_policies): команда, permissive/restrictive, роли, USING/WITH CHECK; флаг rls_enabled у таблицы
- [x] Привилегии по умолчанию (pg_default_acl, ALTER DEFAULT PRIVILEGES)
- [x] Event triggers (pg_event_trigger): событие, функция, теги, состояние
- [x] Роли: VALID UNTIL + подсветка superuser/bypassrls/просроченных

## Метрики по конкретной БД (tables.py) — добавлено сверх плана
- [x] Комментарии таблиц и колонок (obj_description/col_description)
- [x] seq/idx сканы (pg_stat_user_tables) — кандидаты на индекс при преобладании seq scan
- [x] HOT-update ratio (n_tup_hot_upd/n_tup_upd)

## Исправления по ходу разработки
- [x] Ложный wraparound-crit на партиционированных родителях (age(relfrozenxid)=age(0)) → фильтр relkind IN ('r','m') в stats.py
- [x] Ложное «устаревшее расширение» из-за лексического сравнения версий ("1.9" > "1.10") → числовое сравнение (_ver_key)
- [x] Пропуск sequence без владельца (NULL-идентификатор в format('%I'))
- [x] Корректный запрос FK без покрывающего индекса (сравнение ведущих колонок индекса с conkey)
- [x] docker: wal_level=logical и shared_preload_libraries=pg_stat_statements; фикстура пополнена демонстрационными объектами (публикации, триггеры, view/matview, SECURITY DEFINER, типы/домены, RLS, default privileges, event trigger, комментарии, просроченная роль)

## Проверка (end-to-end)
- [x] docker: postgres:16 с фикстурой (несколько схем, партиционированная таблица, роли+группы с грантами, «раздутая» таблица, таблица с устаревшей статистикой)
- [x] pgcollect all --target local → out/testdb.{json,html,confluence.xml}
- [x] HTML открывается в браузере, все разделы заполнены; статусы bloat/автовакуума корректны
- [x] confluence.xml — well-formed XML (проверка ET.fromstring как в preview.py)
- [x] безопасность: не-SELECT падает, statement_timeout применён, template-БД пропущены
- [x] деградация: без pgstattuple работает оценочный путь; недоступные из-за прав разделы помечаются, не роняют прогон
- [x] публикации: состав с колонками/фильтром, via_root=false раскрывается до листовых партиций, обратная привязка к таблицам
- [x] триггеры: декодирование tgtype (когда/события/уровень), отключённый помечен, внутренние отфильтрованы
- [x] сводка проблем: все планированные сигналы фикстуры (crit/warn) попадают в список и подсветку строк
- [x] прочие объекты и безопасность: объекты расширений отфильтрованы, SECURITY DEFINER/RLS/default privileges/event triggers/просроченная роль детектируются
