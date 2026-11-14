"""Composicao deterministica dos briefings diarios, sem chamar o modelo."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

_WEEKDAYS = (
    "segunda",
    "terça",
    "quarta",
    "quinta",
    "sexta",
    "sábado",
    "domingo",
)

_CHECKED = "☑"
_UNCHECKED = "☐"


@dataclass(frozen=True)
class Item:
    text: str
    done: bool = False
    children: tuple[Item, ...] = ()


def normalize(text: object) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(value.casefold().split())


def _order(note: dict[str, Any]) -> tuple[int, str]:
    try:
        order = int(note.get("order") or 0)
    except (TypeError, ValueError):
        order = 0
    return order, str(note.get("created_at") or "")


def _item(note: dict[str, Any], children: Iterable[Item] = ()) -> Item:
    return Item(
        text=str(note.get("text") or "").strip(),
        done=bool(note.get("completed")),
        children=tuple(children),
    )


def _flatten(items: Iterable[Item]) -> Iterator[tuple[int, Item]]:
    for item in items:
        yield 0, item
        for child in item.children:
            yield 1, child


def collect_tasks(notes: list[dict[str, Any]]) -> list[Item]:
    """Tarefas do dia: checkboxes fora das rotinas, com subtarefas aninhadas."""
    by_parent: dict[Any, list[dict[str, Any]]] = {}
    for note in notes:
        if note.get("routine_id") or not note.get("is_checkbox"):
            continue
        by_parent.setdefault(note.get("parent_id"), []).append(note)

    items: list[Item] = []
    for root in sorted(by_parent.get(None, []), key=_order):
        kids = sorted(by_parent.get(root.get("id"), []), key=_order)
        items.append(_item(root, (_item(kid) for kid in kids)))
    return items


def collect_habits(
    notes: list[dict[str, Any]], pattern: str
) -> tuple[list[Item], bool]:
    """Habitos da rotina matinal a partir das notas geradas pela Agenda."""
    needle = normalize(pattern)
    parents = [
        note
        for note in notes
        if note.get("routine_id") and not note.get("parent_id")
    ]
    for parent in sorted(parents, key=_order):
        if needle not in normalize(parent.get("text")):
            continue
        children = [
            note for note in notes if note.get("parent_id") == parent.get("id")
        ]
        habits = [_item(child) for child in sorted(children, key=_order)]
        return (habits or [_item(parent)]), True
    return [], False


def _js_weekday(day: date) -> int:
    """Dia da semana no formato do JS: 0 = domingo."""
    return (day.weekday() + 1) % 7


def _routine_days(config: dict[str, Any]) -> list[int]:
    days = config.get("days")
    if isinstance(days, list):
        return [int(day) for day in days if isinstance(day, int)]
    return [1, 2, 3, 4, 5]


def routine_occurs_on(routine: dict[str, Any], day: date) -> bool:
    """Mesma regra de recorrencia da Agenda (lunar fica de fora)."""
    kind = str(routine.get("type") or "daily")
    raw_config = routine.get("frequency_config")
    config = raw_config if isinstance(raw_config, dict) else {}
    if kind == "daily":
        return _js_weekday(day) in _routine_days(config)

    raw_start = str(routine.get("start_date") or "")[:10]
    try:
        start = date.fromisoformat(raw_start) if raw_start else date(2026, 1, 1)
    except ValueError:
        start = date(2026, 1, 1)
    if day < start:
        return False

    week_index = (day - start).days // 7
    if kind == "weekly":
        interval = int(config.get("weekInterval") or 1)
        return week_index % interval == 0 and _js_weekday(day) in _routine_days(config)
    if kind == "biweekly":
        return week_index % 2 == 0 and _js_weekday(day) in _routine_days(config)
    if kind == "monthly":
        if config.get("lastDayOfMonth") or config.get("last_day_of_month"):
            return (day + timedelta(days=1)).day == 1
        days = config.get("monthDays")
        if not isinstance(days, list):
            single = config.get("monthlyDay")
            days = [single] if single else []
        return day.day in [int(value) for value in days if isinstance(value, int)]
    return False


def habits_from_routines(
    routines: list[dict[str, Any]], day: date, pattern: str
) -> tuple[list[Item], bool]:
    """Fallback: usa a tabela de rotinas quando as notas do dia nao existem."""
    needle = normalize(pattern)
    for routine in routines:
        if needle not in normalize(routine.get("text")):
            continue
        if not routine_occurs_on(routine, day):
            continue
        subtasks = routine.get("subtasks")
        habits = [
            Item(str(subtask.get("text") or "").strip())
            for subtask in (subtasks if isinstance(subtasks, list) else [])
            if isinstance(subtask, dict) and str(subtask.get("text") or "").strip()
        ]
        return (habits or [Item(str(routine.get("text") or "").strip())]), True
    return [], False


def _date_label(day: date) -> str:
    return f"{_WEEKDAYS[day.weekday()]}, {day:%d/%m}"


def _check(done: bool) -> str:
    return _CHECKED if done else _UNCHECKED


def _task_lines(items: list[Item], limit: int) -> list[str]:
    flat = list(_flatten(items))
    lines = [f"{'  ' * depth}{_check(item.done)} {item.text}" for depth, item in flat[:limit]]
    if len(flat) > limit:
        lines.append(f"… e mais {len(flat) - limit}")
    return lines


def _flat_lines(flat: list[tuple[int, Item]], limit: int) -> list[str]:
    lines = [f"{'  ' * depth}{_check(item.done)} {item.text}" for depth, item in flat[:limit]]
    if len(flat) > limit:
        lines.append(f"… e mais {len(flat) - limit}")
    return lines


def morning_message(
    name: str,
    day: date,
    tasks: list[Item],
    max_items: int = 15,
) -> str:
    greeting = f"Bom dia, {name}!" if name else "Bom dia!"
    lines = [greeting, _date_label(day), "", "Tarefas do dia"]
    task_lines = _task_lines(tasks, max_items)
    lines.extend(task_lines or ["Nenhuma tarefa marcada na Agenda."])
    return "\n".join(lines)


def noon_message(
    name: str,
    day: date,
    tasks: list[Item],
    max_items: int = 15,
) -> str:
    """Resumo do meio-dia: so as tarefas normais, sem rotinas."""
    greeting = f"Meio-dia, {name}." if name else "Meio-dia."
    flat = list(_flatten(tasks))
    done = [(depth, item) for depth, item in flat if item.done]
    pending = [(depth, item) for depth, item in flat if not item.done]

    lines = [greeting, _date_label(day), ""]
    if flat:
        lines.append(f"Tarefas: {len(done)}/{len(flat)} concluídas")
    else:
        lines.append("Nenhuma tarefa na Agenda para hoje.")

    lines.extend(["", "Feito até agora"])
    lines.extend(_flat_lines(done, max_items) or ["Nada marcado como feito ainda."])
    lines.extend(["", "Ainda falta"])
    lines.extend(_flat_lines(pending, max_items) or ["Tudo certo por aqui."])
    return "\n".join(lines)


def due_slot(
    now_hm: str, morning_at: str, noon_at: str, noon_until: str
) -> str | None:
    """Qual briefing deve sair agora, se houver."""
    if morning_at <= now_hm < noon_at:
        return "morning"
    if noon_at <= now_hm < noon_until:
        return "noon"
    return None


def evening_due(now_hm: str, at: str, until: str) -> bool:
    """O fecho da noite vale da hora marcada ate o limite (exclusive)."""
    return at <= now_hm < until


def partition(items: list[Item]) -> tuple[list[Item], list[Item]]:
    """Achata as subtasks e devolve (feitos, pendentes) na ordem da Agenda."""
    flat = [item for _, item in _flatten(items)]
    return ([item for item in flat if item.done], [item for item in flat if not item.done])


def evening_message(
    name: str,
    day: date,
    tasks: list[Item],
    habits: list[Item],
    max_items: int = 15,
) -> str:
    """Fecho do dia: o que foi feito e o que nao foi, rotina junto."""
    greeting = f"Boa noite, {name}." if name else "Boa noite."
    flat = list(_flatten(tasks))
    done = [(depth, item) for depth, item in flat if item.done]
    pending = [(depth, item) for depth, item in flat if not item.done]
    done.extend((0, item) for item in habits if item.done)
    pending.extend((0, item) for item in habits if not item.done)
    if not done and not pending:
        return "\n".join([greeting, _date_label(day), "", "Nada marcado na Agenda hoje."])

    lines = [
        greeting,
        _date_label(day),
        "",
        f"Feito: {len(done)}/{len(done) + len(pending)}",
        "",
        "Feito hoje",
    ]
    lines.extend(_flat_lines(done, max_items) or ["Nada marcado como feito."])
    lines.extend(["", "Não foi"])
    lines.extend(_flat_lines(pending, max_items) or ["Tudo fechado. 🎉"])
    return "\n".join(lines)


def day_recap(
    tasks: list[Item], habits: list[Item], metas: list[str]
) -> str:
    """Recap do dia em texto puro: entra no prompt do insight da noite."""
    done_tasks, pending_tasks = partition(tasks)
    done_habits = [item for item in habits if item.done]
    pending_habits = [item for item in habits if not item.done]

    def bullet(items: list[Item]) -> list[str]:
        return [f"- {item.text}" for item in items] or ["- (nenhum)"]

    total = len(done_tasks) + len(pending_tasks)
    lines = [
        f"Tarefas feitas ({len(done_tasks)}/{total}):",
        *bullet(done_tasks),
        "Tarefas que não foram:",
        *bullet(pending_tasks),
        "Rotina feita:",
        *bullet(done_habits),
        "Rotina que não foi:",
        *bullet(pending_habits),
    ]
    if metas:
        lines.append("Metas ativas:")
        lines.extend(f"- {meta}" for meta in metas)
    return "\n".join(lines)
