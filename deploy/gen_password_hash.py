#!/usr/bin/env python3
"""
Генератор хеша пароля для .env (AUTH_PASSWORD_HASH / AUTH_SECOND_PASSWORD_HASH).

Запускать прямо на сервере, внутри venv проекта:
    cd /opt/site && source .venv/bin/activate
    python3 deploy/gen_password_hash.py

Пароль вводится скрыто (не отображается и не остаётся в истории команд),
на выходе — строка-хеш, которую нужно вставить в .env.
"""

from __future__ import annotations

import getpass
import sys

from werkzeug.security import generate_password_hash


def main() -> None:
    password = getpass.getpass("Пароль: ")
    if not password:
        print("Пустой пароль не годится.", file=sys.stderr)
        sys.exit(1)
    confirm = getpass.getpass("Повтори пароль: ")
    if password != confirm:
        print("Пароли не совпадают, попробуй ещё раз.", file=sys.stderr)
        sys.exit(1)
    print()
    print(generate_password_hash(password))


if __name__ == "__main__":
    main()
