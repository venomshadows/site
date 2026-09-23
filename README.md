# site.venomshadows.ru

Минимальное Flask-приложение. Требуется Python 3.12 или новее.

Локальный запуск в PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev,deploy]"
$env:SECRET_KEY = (.venv\Scripts\python -c "import secrets; print(secrets.token_hex(32))")
$env:AUTH_USERNAME = "admin"
$env:AUTH_PASSWORD_HASH = (.venv\Scripts\python deploy/gen_password_hash.py | Select-Object -Last 1)
$env:AUTH_SECOND_PASSWORD_HASH = (.venv\Scripts\python deploy/gen_password_hash.py | Select-Object -Last 1)
$env:SESSION_COOKIE_SECURE = "0"
.venv\Scripts\python -m flask --app 'site_app.webapp:create_app()' run --debug
```

Откройте http://127.0.0.1:5000/. Проверка работоспособности: `/healthz`.
Настройки перечислены в `.env.example`; файл `.env` автоматически не загружается.
Приложение требует `SECRET_KEY`, `AUTH_USERNAME`, `AUTH_PASSWORD_HASH` и `AUTH_SECOND_PASSWORD_HASH`.

Запуск тестов:

```powershell
.venv\Scripts\pytest -q
```

Развёртывание описано в [deploy/](deploy/). Gunicorn запускается на Linux
с фабрикой `site_app.webapp:create_app()` за одним доверенным прокси nginx.

## Настройка входа

Вход состоит из двух шагов: логин и основной пароль, затем дополнительный пароль.
Скопируйте `.env.example` в `.env` и заполните все четыре обязательные переменные.
Для каждого пароля отдельно запустите `python deploy/gen_password_hash.py`: скрипт
скрывает ввод и выводит хеш для `AUTH_PASSWORD_HASH` или `AUTH_SECOND_PASSWORD_HASH`.
Сами пароли и заполненный `.env` не сохраняйте в git. На сервере рабочий каталог — `/opt/site`.
Файл `.env` не загружается автоматически: передайте значения через окружение
(локально — команды PowerShell выше, на сервере — EnvironmentFile сервиса).

В продакшене используйте HTTPS и `SESSION_COOKIE_SECURE=1`; значение `0` нужно
только для локального HTTP. Полная сессия действует 12 часов; выход — кнопкой «Выйти».
После пяти неудачных попыток с одного IP оба шага блокируются до истечения
пятиминутного окна. Счётчик общий для потоков одного процесса и сбрасывается
при перезапуске; для общего лимита запускайте один процесс с потоками.
`/healthz` остаётся публичным, главная страница открывается только после обоих шагов.
