#!/usr/bin/env bash
#
# Обновление зависимостей и пользовательских systemd-юнитов site.
# Запускать от пользователя приложения (не от root):
#   bash /opt/site/deploy/update.sh
#
# Так делает и deploy/setup-server.sh (через sudo -u), и GitHub Actions
# после git pull. Логика «скопировать юниты → daemon-reload → restart →
# /healthz и /login» живёт только здесь, чтобы setup и workflow её не дублировали.
#
# Каталог приложения берётся из расположения этого файла
# (deploy/update.sh → родитель), поэтому текущий каталог при вызове не важен.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$APP_DIR"

SERVICE="site"
HEALTH_URL="http://127.0.0.1:8000/healthz"
LOGIN_URL="http://127.0.0.1:8000/login"

# Один пробник на оба URL: с непустым вторым аргументом тело должно
# содержать подстроку (curl -f, не-2xx — провал); без него достаточно
# HTTP 200. Так /healthz и /login не копируют curl друг в друга.
http_ready() {
  local url="$1"
  local body="${2:-}"
  if [[ -n "$body" ]]; then
    curl -fsS --max-time 3 "$url" 2>/dev/null | grep -q "$body"
  else
    local code
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 "$url" 2>/dev/null || true)"
    [[ "$code" == "200" ]]
  fi
}

# site.db хранит smtp_password, brand_api_key, telegram_bot_token и api_key.
# UMask=0077 в юните закрывает только файлы, созданные после рестарта;
# на уже развёрнутом сервере БД могла остаться 0644 от umask 0022.
# Каталог не создаём: его заводит само приложение (db.py).
# Выравниваем только путь по умолчанию (DATABASE_PATH не задан — так
# разворачивает setup-server.sh). БД по нестандартному пути, созданная
# уже под UMask=0077, и так закрыта; старую — выровнять вручную.
tighten_data_perms() {
  local data_dir="${APP_DIR}/data"
  if [[ ! -d "$data_dir" ]]; then
    echo "Каталога ${data_dir} ещё нет — права выставит приложение при создании БД"
    return 0
  fi
  chmod 700 "$data_dir"
  # nullglob: нет site.db / -wal / -shm — не ошибка при set -euo pipefail.
  local db_files=()
  shopt -s nullglob
  db_files=( "${data_dir}/site.db"* )
  shopt -u nullglob
  if [[ ${#db_files[@]} -gt 0 ]]; then
    local db_file
    for db_file in "${db_files[@]}"; do
      # Пока старый процесс ещё обслуживает запросы, SQLite может удалить
      # -wal/-shm между glob и chmod. Пропавший путь — не ошибка деплоя;
      # отказ chmod на файле, который всё ещё есть, — настоящая ошибка.
      if ! chmod 600 "$db_file"; then
        if [[ -e "$db_file" ]]; then
          echo "Не удалось выставить 600 на ${db_file}" >&2
          return 1
        fi
        echo "Пропуск ${db_file}: файл исчез между списком и chmod (гонка с WAL/SHM)"
      fi
    done
  fi
}

# systemctl --user без этой переменной не находит сокет шины — особенно
# в неинтерактивном SSH из GitHub Actions, где pam её не выставляет.
XDG_RUNTIME_DIR="/run/user/$(id -u)"
export XDG_RUNTIME_DIR

echo "== venv и зависимости (${APP_DIR}) =="
if [[ ! -d "${APP_DIR}/.venv" ]]; then
  python3 -m venv "${APP_DIR}/.venv"
fi
"${APP_DIR}/.venv/bin/pip" install -q --upgrade pip
# Кавычки обязательны: иначе bash раскрывает [deploy] как glob.
"${APP_DIR}/.venv/bin/pip" install -q -e "${APP_DIR}[deploy]"

echo "== права каталога данных =="
tighten_data_perms

echo "== синхронизация systemd-юнитов =="
UNIT_DIR="${HOME}/.config/systemd/user"
mkdir -p "$UNIT_DIR"

# nullglob: пустой каталог не должен превращаться в литерал
# «deploy/*.service» и падать на cp несуществующего файла.
shopt -s nullglob
unit_paths=( "${APP_DIR}/deploy/"*.service "${APP_DIR}/deploy/"*.timer )
shopt -u nullglob

if [[ ${#unit_paths[@]} -eq 0 ]]; then
  echo "В ${APP_DIR}/deploy нет файлов *.service / *.timer — копировать нечего"
else
  for unit_path in "${unit_paths[@]}"; do
    cp "$unit_path" "${UNIT_DIR}/$(basename "$unit_path")"
  done
fi

systemctl --user daemon-reload

echo "== перезапуск ${SERVICE} =="
# enable нужен, чтобы юнит поднимался с пользовательской сессией (linger).
# Именно restart, а не «enable --now»: если сервис уже был запущен,
# --now не перечитает только что скопированный юнит.
systemctl --user enable "$SERVICE"
systemctl --user restart "$SERVICE"

shopt -s nullglob
timer_paths=( "${APP_DIR}/deploy/"*.timer )
shopt -u nullglob
for timer_path in "${timer_paths[@]}"; do
  systemctl --user enable --now "$(basename "$timer_path")"
done

echo "== проверка ${SERVICE}, ${HEALTH_URL} и ${LOGIN_URL} =="
# После restart сокет появляется не мгновенно. Ждём, пока юнит станет
# active, GET /healthz вернёт тело с «ok», а GET /login — HTTP 200
# (главная без сессии редиректит на логин, поэтому проверяем сам /login).
healthy=0
for _ in $(seq 20); do
  if systemctl --user is-active --quiet "$SERVICE" \
    && http_ready "$HEALTH_URL" "ok" \
    && http_ready "$LOGIN_URL"; then
    healthy=1
    break
  fi
  sleep 1
done

if [[ "$healthy" -ne 1 ]]; then
  echo "!! ${SERVICE} не поднялся, ${HEALTH_URL} не ответил ok или ${LOGIN_URL} не вернул 200. Журнал:" >&2
  journalctl --user -u "$SERVICE" -n 50 --no-pager >&2 || true
  exit 1
fi

echo "${SERVICE}: активен, ${HEALTH_URL} -> ok, ${LOGIN_URL} -> 200"

for timer_path in "${timer_paths[@]}"; do
  timer_name="$(basename "$timer_path")"
  if systemctl --user is-active --quiet "$timer_name"; then
    echo "${timer_name}: активен"
  else
    echo "!! ${timer_name} не активен. Журнал ${SERVICE}:" >&2
    journalctl --user -u "$SERVICE" -n 50 --no-pager >&2 || true
    exit 1
  fi
done
