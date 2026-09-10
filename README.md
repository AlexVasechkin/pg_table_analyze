# pgcollect

Инструмент read-only обхода кластера PostgreSQL 14+ (в основном 16). По каждой БД
собирает: обзор, схемы, роли/группы и их связи с объектами (членство + привилегии),
структуру таблиц (колонки, партиции, индексы, ограничения), анализ распухания
(bloat) таблиц/индексов/TOAST, качество автовакуума и свежесть статистики, а также
публикации логической репликации (какие таблицы в какие публикации входят). На каждую
БД генерирует **HTML-документ** и **документ для Confluence** (storage-format XML).

## Установка

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Права БД

Достаточно read-only учётки: суперпользователь ЛИБО роль с `pg_monitor` и правом
`CONNECT` на нужные БД. Все сессии открываются как `default_transaction_read_only=on`
с `statement_timeout`/`lock_timeout` — сбор ничего не меняет и ограничен по времени.
Для точного анализа bloat желательно расширение `pgstattuple` (иначе используется
оценочный метод по каталогу).

## Использование

```bash
cp targets.example.yaml targets.yaml   # заполнить подключения
export PGCOLLECT_PASSWORD=...           # пароль (имя переменной — из password_env)

# полный цикл: сбор + Confluence + HTML + Markdown для всех БД цели `local`
pgcollect all local

# по шагам:
pgcollect collect local            # → out/<db>.json
pgcollect render                   # out/<db>.json → out/<db>.confluence.xml
pgcollect preview                  # out/<db>.json → out/<db>.html
pgcollect markdown                 # out/<db>.json → out/<db>.md

# только одна БД / точный bloat / пропуск раздела
pgcollect all local --db billing --deep-bloat --skip roles
```

Артефакты в `out/`:

- `<db>.json` — сырые собранные данные (для отладки и дифов между прогонами);
- `<db>.confluence.xml` — вставляется в Confluence (редактор → «…» → `<>` вставка
  storage format);
- `<db>.html` — автономная страница для просмотра в браузере;
- `<db>.md` — Markdown-отчёт (для Git-репозиториев, wiki, code review).

Разделение `collect` и `render`/`preview` позволяет собрать данные один раз и
перегенерировать документы без повторного обращения к БД.

## Тестовый кластер

```bash
docker compose -f docker/compose.yaml up -d   # postgres:16 + фикстура
export PGCOLLECT_PASSWORD=postgres
pgcollect all local
```
