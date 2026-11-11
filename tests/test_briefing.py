from __future__ import annotations

from datetime import date

from zaya.briefing.compose import (
    collect_habits,
    collect_tasks,
    due_slot,
    habits_from_routines,
    morning_message,
    noon_message,
    routine_occurs_on,
)

DAY = date(2026, 11, 10)  # terca-feira


def _notes() -> list[dict]:
    return [
        {
            "id": "t1",
            "text": "Revisar currículo",
            "is_checkbox": True,
            "completed": False,
            "parent_id": None,
            "routine_id": None,
            "order": 1,
        },
        {
            "id": "t1a",
            "text": "Ajustar seção de dados",
            "is_checkbox": True,
            "completed": True,
            "parent_id": "t1",
            "routine_id": None,
            "order": 1,
        },
        {
            "id": "r",
            "text": "Rotina matinal",
            "is_checkbox": True,
            "completed": False,
            "parent_id": None,
            "routine_id": "rot1",
            "order": 2,
        },
        {
            "id": "r1",
            "text": "alongar",
            "is_checkbox": True,
            "completed": True,
            "parent_id": "r",
            "routine_id": "rot1",
            "order": 1,
        },
        {
            "id": "r2",
            "text": "meditar",
            "is_checkbox": True,
            "completed": False,
            "parent_id": "r",
            "routine_id": "rot1",
            "order": 2,
        },
        {
            "id": "n1",
            "text": "nota solta",
            "is_checkbox": False,
            "completed": False,
            "parent_id": None,
            "routine_id": None,
            "order": 3,
        },
    ]


def test_due_slot_respeita_janelas():
    assert due_slot("07:59", "08:00", "12:00", "18:00") is None
    assert due_slot("08:00", "08:00", "12:00", "18:00") == "morning"
    assert due_slot("11:59", "08:00", "12:00", "18:00") == "morning"
    assert due_slot("12:00", "08:00", "12:00", "18:00") == "noon"
    assert due_slot("17:59", "08:00", "12:00", "18:00") == "noon"
    assert due_slot("18:00", "08:00", "12:00", "18:00") is None


def test_collect_tasks_separa_rotinas_e_aninha_subtarefas():
    tasks = collect_tasks(_notes())

    assert len(tasks) == 1
    assert tasks[0].text == "Revisar currículo"
    assert tasks[0].done is False
    assert tasks[0].children[0].text == "Ajustar seção de dados"
    assert tasks[0].children[0].done is True


def test_collect_habits_usa_notas_da_rotina_matinal():
    habits, found = collect_habits(_notes(), "matinal")

    assert found is True
    assert [habit.text for habit in habits] == ["alongar", "meditar"]
    assert habits[0].done is True
    assert habits[1].done is False


def test_habits_from_routines_com_recorrencia_do_dia():
    routines = [
        {
            "text": "Rotina matinal",
            "type": "daily",
            "frequency_config": {"days": [2]},
            "subtasks": [{"id": "1", "text": "água"}, {"id": "2", "text": "alongar"}],
        }
    ]

    habits, found = habits_from_routines(routines, DAY, "matinal")

    assert found is True
    assert [habit.text for habit in habits] == ["água", "alongar"]
    assert all(habit.done is False for habit in habits)


def test_routine_occurs_on_semanal_respeita_intervalo():
    routine = {
        "text": "Semanal",
        "type": "weekly",
        "frequency_config": {"days": [2], "weekInterval": 2},
        "start_date": "2026-11-03",
    }

    assert routine_occurs_on(routine, date(2026, 11, 3)) is True
    assert routine_occurs_on(routine, date(2026, 11, 10)) is False
    assert routine_occurs_on(routine, date(2026, 11, 17)) is True


def test_morning_message_lista_so_as_tarefas_normais():
    tasks = collect_tasks(_notes())

    text = morning_message("Talita", DAY, tasks)

    assert text.startswith("Bom dia, Talita!")
    assert "terça, 10/11" in text
    assert "☐ Revisar currículo" in text
    assert "  ☑ Ajustar seção de dados" in text
    assert "Rotina matinal" not in text
    assert "alongar" not in text
    assert "meditar" not in text


def test_noon_message_separa_feito_e_pendente():
    tasks = collect_tasks(_notes())

    text = noon_message("Talita", DAY, tasks)

    assert "Tarefas: 1/2 concluídas" in text
    assert "Feito até agora" in text
    assert "☑ Ajustar seção de dados" in text
    assert "Ainda falta" in text
    assert "☐ Revisar currículo" in text


def test_noon_message_nao_mostra_rotina():
    notes = _notes()
    tasks = collect_tasks(notes)

    text = noon_message("Talita", DAY, tasks)

    assert "Rotina matinal" not in text
    assert "alongar" not in text
    assert "meditar" not in text
