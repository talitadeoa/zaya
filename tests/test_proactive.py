from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zora.controller.proactive import (
    classify_voice_control,
    greeting_for,
    in_quiet_hours,
    parse_reminder,
)

NOW = datetime(2026, 11, 8, 10, 0, tzinfo=UTC)


def test_relative_minutes():
    spec = parse_reminder("me lembre de beber água em 30 minutos", now=NOW)
    assert spec is not None
    assert spec.label == "beber água"
    assert spec.due == NOW + timedelta(minutes=30)


def test_relative_hours_and_days():
    spec = parse_reminder("lembre-me de ligar daqui a 2 horas", now=NOW)
    assert spec is not None
    assert spec.label == "ligar"
    assert spec.due == NOW + timedelta(hours=2)

    spec = parse_reminder("me avise de revisar em 1 dia", now=NOW)
    assert spec is not None
    assert spec.due == NOW + timedelta(days=1)


def test_meia_hora():
    spec = parse_reminder("me lembre de tomar remédio em meia hora", now=NOW)
    assert spec is not None
    assert spec.due == NOW + timedelta(minutes=30)


def test_absolute_tomorrow_and_today():
    spec = parse_reminder("lembre-me de estudar amanhã às 9h", now=NOW)
    assert spec is not None
    assert spec.label == "estudar"
    assert spec.due == datetime(2026, 11, 9, 9, 0, tzinfo=UTC)

    spec = parse_reminder("me lembre de pagar o boleto hoje às 18:30", now=NOW)
    assert spec is not None
    assert spec.label == "pagar o boleto"
    assert spec.due.hour == 18 and spec.due.minute == 30

    spec = parse_reminder("me lembre de ligar às 9h", now=NOW)
    assert spec is not None
    # 9h já passou; agenda para amanhã
    assert spec.due == datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


def test_meio_dia_e_meia_noite():
    spec = parse_reminder("me lembre de almoçar ao meio-dia", now=NOW)
    assert spec is not None
    assert spec.due.hour == 12

    spec = parse_reminder("me lembre de dormir à meia-noite", now=NOW)
    assert spec is not None
    assert spec.due.hour == 0
    assert spec.due.date() == (NOW + timedelta(days=1)).date()


def test_prefix_required_for_natural_language():
    assert parse_reminder("vou estudar em 2 horas", now=NOW) is None
    assert parse_reminder("lembre que eu prefiro café", now=NOW) is None
    spec = parse_reminder("beber água em 2 horas", now=NOW, require_prefix=False)
    assert spec is not None
    assert spec.label == "beber água"


def test_no_time():
    assert parse_reminder("me lembre de comprar pão", now=NOW) is None


def test_quiet_hours():
    assert in_quiet_hours(datetime(2026, 11, 8, 23, 30), "23:00-08:00")
    assert in_quiet_hours(datetime(2026, 11, 8, 7, 59), "23:00-08:00")
    assert not in_quiet_hours(datetime(2026, 11, 8, 8, 0), "23:00-08:00")
    assert not in_quiet_hours(datetime(2026, 11, 8, 12, 0), "23:00-08:00")
    assert in_quiet_hours(datetime(2026, 11, 8, 13, 0), "12:00-14:00")
    assert not in_quiet_hours(datetime(2026, 11, 8, 13, 0), "")


def test_voice_control():
    assert classify_voice_control("parar") == "stop"
    assert classify_voice_control("para") == "stop"
    assert classify_voice_control("texto") == "stop"
    assert classify_voice_control("Zaia, modo texto.") == "stop"
    assert classify_voice_control("sair") == "quit"
    assert classify_voice_control("tchau!") == "quit"
    assert classify_voice_control("continuar o projeto") is None


def test_greeting_for():
    assert greeting_for(datetime(2026, 11, 8, 9, 0)) == "Bom dia"
    assert greeting_for(datetime(2026, 11, 8, 15, 0)) == "Boa tarde"
    assert greeting_for(datetime(2026, 11, 8, 22, 0)) == "Boa noite"
