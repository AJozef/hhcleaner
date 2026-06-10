"""
hh_cleaner.py — точка входа.

Удаляет на hh.ru отклики и чаты со статусом «отказ», а также старые чаты.
Что делать — выбирается подкомандами и флагами, без правки кода (см. cli.py).

Авторизация — через сохранённую сессию (вход вручную один раз, см. auth.py).
Пароль нигде не хранится; капчу и код из почты проходит сам пользователь.

Двойной клик по собранному .exe (без аргументов) запускает дружелюбный визард
(см. wizard.py). Запуск с аргументами — обычный CLI с подкомандами (см. cli.py):

    hhcleaner                  все шаги по умолчанию
    hhcleaner login            войти и сохранить сессию
    hhcleaner status           статистика без удаления
    hhcleaner doctor           диагностика окружения
    hhcleaner --help           все команды и опции

Коды выхода: 0 — успех, 2 — вход не удался, 3 — нужен ручной вход (hhcleaner login).
"""
from __future__ import annotations

import sys

import cli
import wizard
from config import package_version

# Версия — единый источник истины в pyproject.toml; читаем из метаданных пакета.
__version__ = package_version()


def main() -> int:
    """Двойной клик → визард; иначе разбираем команду и выполняем её."""
    if wizard.should_run():
        return wizard.run()

    args = cli.parse_args()
    cli.apply_output(args)
    return cli.dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
