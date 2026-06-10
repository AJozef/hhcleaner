"""Печать итоговых таблиц rich (сводка прогона и статистика чатов).

Вынесено из точки входа, чтобы и CLI-обработчики, и визард рисовали итоги
одинаково, без дублирования.
"""
from __future__ import annotations

from rich.table import Table

from config import console, file_console
from steps import STEP_LABELS


def print_summary(results: dict[str, int], elapsed: float, dry_run: bool) -> None:
    """Выводит итоговую таблицу с количеством обработанных элементов."""
    table = Table(
        title="[bold]Результаты[/bold]" + (" [dim](dry-run)[/dim]" if dry_run else ""),
        show_header=True, header_style="bold cyan", box=None, padding=(0, 2),
    )
    table.add_column("Операция", style="default", no_wrap=True)
    table.add_column("Кол-во", justify="right", style="bold green")
    for step_id, count in results.items():
        table.add_row(STEP_LABELS.get(step_id, step_id), str(count))

    for out in (console, file_console()):
        if out is None:
            continue
        out.print()
        out.rule("[bold cyan]Готово[/bold cyan]")
        out.print(table)
        out.print(f"[dim]Время выполнения: {elapsed:.1f} с[/dim]")


def print_stats(stats: dict[str, int], days: int) -> None:
    """Выводит таблицу статистики чатов."""
    rows = [
        ("total",            "Всего чатов"),
        ("unread",           "Непрочитанных"),
        ("rejected",         "Чатов-отказов"),
        ("archived_vacancy", "По архивным вакансиям (кроме собеседований)"),
        ("old",              f"Старше {days} дней"),
    ]
    table = Table(
        title="[bold]Статистика чатов[/bold]",
        show_header=True, header_style="bold cyan", box=None, padding=(0, 2),
    )
    table.add_column("Показатель", style="default", no_wrap=True)
    table.add_column("Кол-во", justify="right", style="bold green")
    for key, label in rows:
        table.add_row(label, str(stats.get(key, 0)))

    for out in (console, file_console()):
        if out is None:
            continue
        out.print()
        out.print(table)
