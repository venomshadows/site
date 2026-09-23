#!/usr/bin/env bash
#
# Первичная настройка чистого Ubuntu 24.04 под site (site.venomshadows.ru).
# Запускать ОДИН РАЗ от root на самой VPS (не здесь):
#
#   scp deploy/setup-server.sh root@<VPS_IP>:/root/
#   ssh root@<VPS_IP>
#   nano /root/setup-server.sh   # проверить/поправить переменные ниже
#   bash /root/setup-server.sh
#
# Скрипт идемпотентный: повторный запуск безопасен (пропускает то, что
# уже сделано) — удобно, если что-то прервалось на середине.
#
# Сервис работает как пользовательский systemd-юнит (systemctl --user)
# от имени APP_USER, а не как системный сервис от root. Это осознанный
# выбор: тогда GitHub Actions может перезапускать сервис после деплоя без
# sudo вообще — не нужно городить NOPASSWD-правила в sudoers (на Ubuntu
# 24.04 с этим есть нестыковка: sudo -l показывает NOPASSWD-правило, но
# сама команда без tty всё равно требует пароль — похоже на пограничный
# случай самого sudo при связке use_pty + не-интерактивная SSH-сессия).
# systemctl --user такой проблемы не имеет в принципе, т.к. root там
# просто не участвует.
#
# Копирование юнитов, restart и проверка /healthz и /login — в
# deploy/update.sh. Этот скрипт только готовит машину и один раз вызывает
# update.sh.

set -euo pipefail

# ── Переменные — проверь перед запуском ─────────────────────────────
DOMAIN="site.venomshadows.ru"
REPO_SSH_URL="git@github.com:venomshadows/site.git"
APP_USER="site"
APP_DIR="/opt/site"
CERTBOT_EMAIL="venomshadows8@gmail.com"   # для уведомлений Let's Encrypt об истечении сертификата
# Файл, куда скрипт кладёт логин/пароли сайта и приватный ключ для
# GitHub Actions. Только для root (chmod 600), в репозиторий не попадает.
SECRETS_FILE="/root/site-secrets.txt"
# ─────────────────────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
  echo "Запускать от root (sudo bash setup-server.sh)" >&2
  exit 1
fi

# Команды пользовательского systemd от APP_USER. USER_RUNTIME_DIR задаётся
# ниже, когда известен uid; до этого run_as_app вызывать нельзя.
# -H обязателен: без него sudo оставляет HOME=root, а update.sh кладёт
# юниты в ${HOME}/.config/systemd/user. Тот же helper — везде, где команда
# и зависит от HOME пользователя, и от шины systemd.
run_as_app() {
  sudo -H -u "$APP_USER" env "XDG_RUNTIME_DIR=${USER_RUNTIME_DIR}" "$@"
}

# ssh-keyscan не удостоверяет ключ. Пишем в known_hosts только те строки,
# чей отпечаток есть в официальном списке GitHub (RSA, ECDSA, Ed25519):
# https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
pin_github_host_keys() {
  local known_hosts scanned line fp pin
  local matched ok
  local -a pins
  known_hosts="/home/${APP_USER}/.ssh/known_hosts"
  matched=0
  pins=(
    "SHA256:uNiVztksCsDhcc0u9e8BujQXVUpKZIDTMczCvj3tD2s"
    "SHA256:p2QAMXNIC1TJYWeIOttrVc98/R1BUFWu3/LiyKgUfQM"
    "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"
  )
  scanned="$(mktemp)"
  if ! ssh-keyscan github.com >"$scanned" 2>/dev/null; then
    rm -f "$scanned"
    echo "!! ssh-keyscan github.com не удался — ключи GitHub не записаны." >&2
    exit 1
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    fp="$(printf '%s\n' "$line" | ssh-keygen -lf - | awk '{print $2}')"
    ok=0
    for pin in "${pins[@]}"; do
      if [[ "$fp" == "$pin" ]]; then
        ok=1
        break
      fi
    done
    if [[ "$ok" -eq 1 ]]; then
      printf '%s\n' "$line" | sudo -u "$APP_USER" tee -a "$known_hosts" >/dev/null
      matched=$((matched + 1))
    fi
  done <"$scanned"
  rm -f "$scanned"
  if [[ "$matched" -eq 0 ]]; then
    echo "!! Ни один ключ github.com не совпал с официальными отпечатками SHA256. Установка прервана." >&2
    exit 1
  fi
  chown "${APP_USER}:${APP_USER}" "$known_hosts"
}

echo "== 1/10: часовой пояс сервера (Europe/Moscow) =="
# На само приложение не влияет (пока оно ничего не показывает по времени),
# но делает понятными системные логи и `ls -la` при заходе по SSH, а также
# задаёт локальное время для будущих systemd-таймеров (OnCalendar
# интерпретируется в локальном времени системы).
timedatectl set-timezone Europe/Moscow
echo "Часовой пояс: $(timedatectl show --property=Timezone --value)"

echo "== 2/10: apt update && пакеты =="
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3-venv python3-pip git nginx certbot python3-certbot-nginx ufw dnsutils curl >/dev/null

echo "== 3/10: пользователь ${APP_USER} =="
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$APP_USER"
  echo "Создан пользователь ${APP_USER}"
else
  echo "Пользователь ${APP_USER} уже существует — пропуск"
fi

echo "== 4/10: deploy-ключ для клонирования репозитория (VPS -> GitHub, read-only) =="
DEPLOY_KEY="/home/${APP_USER}/.ssh/id_ed25519_deploy"
if [[ ! -f "$DEPLOY_KEY" ]]; then
  sudo -u "$APP_USER" mkdir -p "/home/${APP_USER}/.ssh"
  sudo -u "$APP_USER" chmod 700 "/home/${APP_USER}/.ssh"
  sudo -u "$APP_USER" ssh-keygen -t ed25519 -N "" -f "$DEPLOY_KEY" -C "site-deploy" >/dev/null
  sudo -u "$APP_USER" bash -c "cat >> /home/${APP_USER}/.ssh/config" <<EOF
Host github.com
    HostName github.com
    User git
    IdentityFile ${DEPLOY_KEY}
    IdentitiesOnly yes
EOF
  # В known_hosts попадают только ключи с официальным отпечатком GitHub.
  # Файл создаёт сам APP_USER (tee под sudo -u), затем chown закрепляет владельца.
  pin_github_host_keys
  echo
  echo "  >>> Добавь этот публичный ключ в GitHub: Settings -> Deploy keys -> Add deploy key"
  echo "  >>> (репозиторий venomshadows/site, доступ read-only, галку 'Allow write' НЕ ставить)"
  echo
  cat "${DEPLOY_KEY}.pub"
  echo
  echo "  После добавления ключа запусти этот скрипт ещё раз, чтобы продолжить клонирование."
  NEED_DEPLOY_KEY_CONFIRM=1
else
  echo "Deploy-ключ уже создан — пропуск"
  NEED_DEPLOY_KEY_CONFIRM=0
fi

echo "== 5/10: клонирование / обновление репозитория =="
if [[ ! -d "${APP_DIR}/.git" ]]; then
  if [[ "${NEED_DEPLOY_KEY_CONFIRM:-0}" -eq 1 ]]; then
    echo "Пропускаю клонирование: сначала добавь deploy-ключ в GitHub (см. вывод выше) и перезапусти скрипт."
    exit 0
  fi
  mkdir -p "$APP_DIR"
  chown "${APP_USER}:${APP_USER}" "$APP_DIR"
  # -H: git читает ~/.ssh/config (deploy-ключ) из HOME пользователя.
  sudo -H -u "$APP_USER" git clone "$REPO_SSH_URL" "$APP_DIR"
elif ! sudo -u "$APP_USER" git -C "$APP_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || ! sudo -u "$APP_USER" git -C "$APP_DIR" rev-parse --verify HEAD >/dev/null 2>&1 \
  || [[ ! -f "${APP_DIR}/pyproject.toml" ]]; then
  # Оборвавшийся клон оставляет .git, но без коммита или без исходников
  # (rev-parse --is-inside-work-tree при этом всё равно успешен). Молча
  # продолжать нельзя — поставим зависимости из битого каталога. Удаляем не
  # сами: там могут быть .env и данные, которые дороже повторного клона.
  echo "!! В ${APP_DIR} есть .git, но это не полный клон (нет HEAD или pyproject.toml)." >&2
  echo "!! Проверь каталог вручную и удали его, если он не нужен, затем запусти скрипт снова." >&2
  exit 1
else
  echo "Репозиторий уже склонирован в ${APP_DIR} — пропуск (обновления идут через GitHub Actions)"
fi

echo "== 6/10: venv и зависимости =="
if [[ ! -d "${APP_DIR}/.venv" ]]; then
  sudo -H -u "$APP_USER" python3 -m venv "${APP_DIR}/.venv"
fi
# Полный цикл «юниты → restart → health» остаётся только в deploy/update.sh.
# Здесь pip нужен раньше: werkzeug приходит вместе с flask, а хеши паролей
# считаются до первого запуска сервиса. update.sh поставит тот же extra ещё
# раз — установка идемпотентна, дублировать restart нельзя (сервису уже
# нужен готовый .env).
# -H: кэш pip — в ~/.cache пользователя, не root.
sudo -H -u "$APP_USER" bash -c "source ${APP_DIR}/.venv/bin/activate && pip install -q --upgrade pip && pip install -q -e '${APP_DIR}[deploy]'"

echo "== 7/10: .env (SECRET_KEY + логин/пароли для входа) =="
# Пароли генерируются случайными, в .env кладутся только их хеши, а сами
# пароли — в SECRETS_FILE (доступен только root). Так деплой получается
# одной командой, а не останавливается на ручном заполнении .env; сменить
# пароль потом можно через deploy/gen_password_hash.py.
ENV_FILE="${APP_DIR}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  GEN_PY="${APP_DIR}/.venv/bin/python3"
  GENERATED_SECRET="$(sudo -u "$APP_USER" "$GEN_PY" -c "import secrets; print(secrets.token_hex(32))")"
  GENERATED_PASSWORD="$(sudo -u "$APP_USER" "$GEN_PY" -c "import secrets; print(secrets.token_urlsafe(18))")"
  GENERATED_PASSWORD2="$(sudo -u "$APP_USER" "$GEN_PY" -c "import secrets; print(secrets.token_urlsafe(18))")"
  # Пароль передаём через stdin, а не аргументом командной строки: аргументы
  # видны в /proc/<pid>/cmdline любому пользователю системы.
  HASH_SCRIPT="import sys
from werkzeug.security import generate_password_hash
print(generate_password_hash(sys.stdin.readline().rstrip('\n')))"
  PASSWORD_HASH="$(printf '%s\n' "$GENERATED_PASSWORD" | sudo -u "$APP_USER" "$GEN_PY" -c "$HASH_SCRIPT")"
  PASSWORD2_HASH="$(printf '%s\n' "$GENERATED_PASSWORD2" | sudo -u "$APP_USER" "$GEN_PY" -c "$HASH_SCRIPT")"

  # umask 077 до открытия файла: при umask 022 cat создал бы .env как 0644,
  # и секрет успел бы утечь до chmod. Подоболочка — от APP_USER, поэтому
  # владельцем сразу становится он.
  sudo -u "$APP_USER" bash -c "umask 077; cat > '${ENV_FILE}'" <<EOF
# Сгенерировано setup-server.sh. Сами пароли (не хеши) — в ${SECRETS_FILE}.
# Сменить пароль: ${APP_DIR}/.venv/bin/python3 ${APP_DIR}/deploy/gen_password_hash.py
# и вписать новый хеш сюда, затем перезапустить сервис:
#   sudo -H -u ${APP_USER} env XDG_RUNTIME_DIR=/run/user/\$(id -u ${APP_USER}) systemctl --user restart site
SECRET_KEY=${GENERATED_SECRET}
AUTH_USERNAME=admin
AUTH_PASSWORD_HASH=${PASSWORD_HASH}
AUTH_SECOND_PASSWORD_HASH=${PASSWORD2_HASH}
EOF
  chmod 600 "$ENV_FILE"
  chown "${APP_USER}:${APP_USER}" "$ENV_FILE"

  # umask — в подоболочке: иначе он остался бы действовать до конца скрипта
  # и все создаваемые дальше файлы получили бы права 600.
  ( umask 077; cat >> "$SECRETS_FILE" <<EOF
=== site: доступ на сайт https://${DOMAIN} ($(date '+%Y-%m-%d %H:%M %Z')) ===
Логин:          admin
Пароль (шаг 1): ${GENERATED_PASSWORD}
Пароль (шаг 2): ${GENERATED_PASSWORD2}

EOF
  )
  chmod 600 "$SECRETS_FILE"
  echo "Создан ${ENV_FILE}; логин и пароли записаны в ${SECRETS_FILE} (только root)."
else
  # Повторный запуск после обрыва до chmod: файл уже есть, но мог остаться 0644.
  chown "${APP_USER}:${APP_USER}" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "${ENV_FILE} уже существует — содержимое не меняю, владелец ${APP_USER}, режим 600"
fi

echo "== 8/10: пользовательский systemd и запуск через deploy/update.sh =="
# lingering — чтобы systemd-инстанс пользователя жил без активной сессии
# (иначе systemctl --user недоступен из неинтерактивной SSH-команды)
loginctl enable-linger "$APP_USER"

USER_UID="$(id -u "$APP_USER")"
USER_RUNTIME_DIR="/run/user/${USER_UID}"

# enable-linger поднимает user@<uid>.service асинхронно: сразу после него
# сокет systemd пользователя может ещё не существовать, и первый же
# systemctl --user падает с "Failed to connect to bus". Ждём сокет.
systemctl start "user@${USER_UID}.service"
for _ in $(seq 30); do
  [[ -S "${USER_RUNTIME_DIR}/systemd/private" ]] && break
  sleep 1
done
if [[ ! -S "${USER_RUNTIME_DIR}/systemd/private" ]]; then
  echo "!! Не дождался systemd-сессии пользователя ${APP_USER} (${USER_RUNTIME_DIR})." >&2
  exit 1
fi

# Юниты, restart и /healthz — внутри update.sh (тот же скрипт, что и деплой).
run_as_app bash "${APP_DIR}/deploy/update.sh"

echo "== 9/10: ключ для автодеплоя из GitHub Actions =="
# Раньше nginx/certbot: сбой выпуска сертификата не должен оставлять сервер
# без ключа, который нужен для секрета VPS_SSH_KEY.
ACTIONS_KEY="/home/${APP_USER}/.ssh/id_ed25519_actions"
# Признак "ключ уже создан" — именно .pub: саму приватную часть скрипт ниже
# удаляет с диска, и проверка по ней заставляла бы каждый повторный запуск
# генерировать новый ключ.
if [[ ! -f "${ACTIONS_KEY}.pub" ]]; then
  sudo -u "$APP_USER" ssh-keygen -t ed25519 -N "" -f "$ACTIONS_KEY" -C "site-actions" >/dev/null
  sudo -u "$APP_USER" bash -c "cat '${ACTIONS_KEY}.pub' >> /home/${APP_USER}/.ssh/authorized_keys"
  sudo -u "$APP_USER" chmod 600 "/home/${APP_USER}/.ssh/authorized_keys"
  # umask — в подоболочке: иначе он остался бы действовать до конца скрипта.
  ( umask 077
    {
      echo "=== site: приватный ключ для секрета VPS_SSH_KEY ($(date '+%Y-%m-%d %H:%M %Z')) ==="
      cat "$ACTIONS_KEY"
      echo
    } >> "$SECRETS_FILE"
  )
  chmod 600 "$SECRETS_FILE"
  # Приватный ключ нужен только чтобы один раз перенести его в GitHub Secrets.
  # Оставлять его на сервере незачем: тот, кто получит доступ к APP_USER,
  # заодно получил бы и ключ для входа. Публичная часть остаётся в
  # authorized_keys, так что автодеплой продолжит работать.
  rm -f "$ACTIONS_KEY"
  echo "Ключ создан, приватная часть перенесена в ${SECRETS_FILE} и удалена с диска"
else
  echo "Ключ для Actions уже есть — пропуск"
fi

echo "== 10/10: nginx + SSL =="
NGINX_CONF=/etc/nginx/sites-available/site.conf
# Конфиг пишем только если его ещё нет: certbot дописывает в этот же файл
# server{} на 443 и редирект с 80, и повторный запуск скрипта, перезаписав
# файл шаблоном из репозитория, молча снёс бы весь HTTPS.
if [[ ! -f "$NGINX_CONF" ]]; then
  sed "s/site.venomshadows.ru/${DOMAIN}/g" "${APP_DIR}/deploy/nginx.conf.example" > "$NGINX_CONF"
else
  echo "${NGINX_CONF} уже существует — не трогаю (там могут быть настройки SSL от certbot)"
fi
ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/site.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null

if [[ -d "/etc/letsencrypt/live/${DOMAIN}" ]]; then
  echo "Сертификат для ${DOMAIN} уже выпущен — пропуск (обновляется таймером certbot)"
else
  # Только A (IPv4). dig +short без типа отдаёт и AAAA, и tail -n1 часто
  # берёт именно его — тогда сверка с IPv4 сервера ложно пропускает certbot.
  RESOLVED_IPS="$(dig +short A "$DOMAIN" @1.1.1.1 || true)"
  # Локальные IPv4; ifconfig.me — только запасной вариант за NAT, и его
  # недоступность не должна отменять выпуск сертификата: настоящий судья
  # здесь всё равно certbot, который сам проверит домен.
  LOCAL_IPS="$(hostname -I 2>/dev/null || true)"
  PUBLIC_IP="$(curl -s -4 --max-time 10 https://ifconfig.me || true)"
  MATCHED_IP=""
  for ip in $RESOLVED_IPS; do
    [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
    if [[ " ${LOCAL_IPS} " == *" ${ip} "* ]] || [[ -n "$PUBLIC_IP" && "$ip" == "$PUBLIC_IP" ]]; then
      MATCHED_IP="$ip"
      break
    fi
  done
  if [[ -z "$RESOLVED_IPS" ]]; then
    echo "!! ${DOMAIN} не резолвится в A-запись — сертификат пропущен."
    echo "!! Когда A-запись появится, выполни: certbot --nginx -d ${DOMAIN} --agree-tos -m ${CERTBOT_EMAIL} --redirect"
  elif [[ -n "$MATCHED_IP" ]]; then
    echo "DNS ${DOMAIN} -> ${MATCHED_IP} указывает на этот сервер, выпускаю сертификат..."
    # Сбой certbot не обрывает скрипт: ключ Actions уже создан на шаге 9,
    # а сертификат можно выпустить той же командой позже.
    if ! certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$CERTBOT_EMAIL" --redirect; then
      echo "!! certbot не выпустил сертификат для ${DOMAIN}."
      echo "!! Когда будет готово, выполни вручную:"
      echo "!!   certbot --nginx -d ${DOMAIN} --agree-tos -m ${CERTBOT_EMAIL} --redirect"
    fi
  else
    echo "!! DNS ${DOMAIN} (A: ${RESOLVED_IPS}) не совпадает с IPv4 этого сервера (${LOCAL_IPS})."
    echo "!! Сертификат пропущен. Когда A-запись обновится, выполни вручную:"
    echo "!!   certbot --nginx -d ${DOMAIN} --agree-tos -m ${CERTBOT_EMAIL} --redirect"
  fi
fi

SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
SERVER_IP="${SERVER_IP:-<IP сервера>}"

echo
echo "===================================================================="
echo "Готово. Проверка: curl -I https://${DOMAIN}/healthz"
echo
echo "Логин и пароли (два шага входа) и приватный ключ для Actions: cat ${SECRETS_FILE}"
echo "Хеши паролей и SECRET_KEY — в ${ENV_FILE} (chmod 600), открытые пароли — только в ${SECRETS_FILE}."
echo
echo "Осталось включить автодеплой — в GitHub repo -> Settings -> Secrets"
echo "and variables -> Actions добавить:"
echo "     VPS_HOST = ${SERVER_IP}"
echo "     VPS_USER = ${APP_USER}"
echo "     VPS_SSH_KEY = <приватный ключ из ${SECRETS_FILE}>"
echo "     VPS_PORT = 22"
# Именно ECDSA: appleboy/ssh-action (Go x/crypto/ssh) при согласовании
# выбирает ECDSA-ключ хоста раньше Ed25519, и отпечаток любого другого
# типа даёт "host key fingerprint mismatch".
echo "     VPS_SSH_FINGERPRINT = $(ssh-keygen -lf /etc/ssh/ssh_host_ecdsa_key.pub | awk '{print $2}')"
echo "===================================================================="
