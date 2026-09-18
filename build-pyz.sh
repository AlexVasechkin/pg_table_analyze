#!/usr/bin/env bash
# Сборка переносимого исполняемого файла .pyz (shiv) под целевую машину.
#
# Собирает ОБА инструмента дистрибутива (один wheel включает пакеты pgcollect и
# pgservercollect); какой из них станет точкой входа .pyz — задаёт ENTRY/OUT:
#     ./build-pyz.sh                                             # pgcollect.pyz (по умолчанию)
#     ENTRY=pgservercollect.cli:app OUT=pgservercollect.pyz ./build-pyz.sh
#
# Цель: Astra Linux x86_64 с python3 3.11, где НЕТ pip и venv.
# Результат: один файл .pyz. На целевой машине запуск, напр.:
#     python3 pgcollect.pyz all local -c targets.yaml -o out
#     python3 pgservercollect.pyz all local -c targets.yaml -o out
# (при первом запуске распакуется в ~/.shiv/, дальше — из кэша).
#
# Почему через Docker: в зависимостях есть НАТИВНЫЕ расширения
# (psycopg[binary] с встроенным libpq, pydantic-core). Их нужно собрать
# именно под linux/amd64 + cp311. Эта машина — macOS/arm64, поэтому
# бинарники берём из контейнера linux/amd64 + Python 3.11.
set -euo pipefail

cd "$(dirname "$0")"

PY_TAG="${PY_TAG:-3.11-slim}"                # версия Python на целевой машине
PLATFORM="${PLATFORM:-linux/amd64}"          # архитектура целевой машины
ENTRY="${ENTRY:-pgcollect.cli:app}"          # console-script: pgcollect.cli:app | pgservercollect.cli:app
OUT="${OUT:-pgcollect.pyz}"

echo "==> Сборка $OUT (entry $ENTRY) для $PLATFORM (python:$PY_TAG)"
docker run --rm --platform "$PLATFORM" -v "$PWD":/src -w /src "python:$PY_TAG" bash -c '
  set -e
  pip install --quiet --root-user-action=ignore shiv
  export SHIV_ROOT=/tmp/shivbuild
  shiv . -o "/src/'"$OUT"'" -e "'"$ENTRY"'" -p "/usr/bin/env python3" --compressed
'
echo "==> Готово: $OUT"
ls -la "$OUT"
echo
echo "Проверка (чистый контейнер только с python3, без pip/venv):"
docker run --rm --platform "$PLATFORM" -v "$PWD/$OUT":/opt/app.pyz:ro "python:$PY_TAG" \
  python3 /opt/app.pyz --help | head -5
