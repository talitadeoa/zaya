from __future__ import annotations

from datetime import datetime, timedelta

from zora.focus import PHASES, FocusStore, moon_phase, period_label


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


def test_focus_last_moon_prompt(tmp_path):
    store = FocusStore(tmp_path / "focus.json")
    assert store.last_moon_prompt() == ""
    store.set_last_moon_prompt("2026-11-08")
    assert store.last_moon_prompt() == "2026-11-08"
