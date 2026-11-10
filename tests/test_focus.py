from __future__ import annotations

from datetime import datetime, timedelta

from zaya.agenda.client import lunation_note_id
from zaya.focus import (
    PHASES,
    FocusStore,
    moon_phase,
    period_label,
    principal_phase_today,
)


def test_moon_phase_valida():
    key, name, illumination = moon_phase(datetime(2026, 11, 8, 12, 0))
    assert key in PHASES
    assert name
    assert 0.0 <= illumination <= 1.0


def test_focus_store_ciclo(tmp_path):
    store = FocusStore(tmp_path / "focus.json")
    focus = store.set("week", "Foco da semana", ["estudar", "correr"])
    assert focus.period == "week"
    assert store.current is not None
    assert store.current.label == "Foco da semana"
    assert store.current.goals == ["estudar", "correr"]
    assert period_label("week") == "semanal"
    assert store.is_expired(datetime.now() + timedelta(days=30)) is True


def test_fase_principal_no_dia_da_lua_nova():
    phase = principal_phase_today(datetime(2026, 11, 9, 12, 0))
    assert phase is not None
    assert phase[0] == "new"


def test_lunation_note_id_estavel():
    assert lunation_note_id("2026-11-09") == "0d55fde0-2fb9-45d8-82b9-ba9131b9b8fe"


def test_focus_last_moon_prompt(tmp_path):
    store = FocusStore(tmp_path / "focus.json")
    assert store.last_moon_prompt() == ""
    store.set_last_moon_prompt("2026-11-08")
    assert store.last_moon_prompt() == "2026-11-08"
