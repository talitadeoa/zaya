from __future__ import annotations

from datetime import datetime

from zora.controller.smalltalk import fast_reply

NOW = datetime(2026, 11, 8, 9, 0)
GREETING_TAILS = {
    "Como posso ajudar?",
    "Tô por aqui. Como posso ajudar?",
    "O que você precisa?",
    "Em que posso ajudar?",
}


def _tail(reply: str, greeting: str) -> str:
    prefix = f"{greeting}!"
    assert reply.startswith(prefix), reply
    return reply[len(prefix) :].strip()


def test_greeting_uses_time_of_day():
    reply = fast_reply("oi", now=NOW)
    assert reply is not None
    assert _tail(reply, "Bom dia") in GREETING_TAILS


def test_greeting_with_zaia_and_punctuation():
    reply = fast_reply("Oi, Zaia!", now=NOW)
    assert reply is not None
    assert _tail(reply, "Bom dia") in GREETING_TAILS


def test_greeting_with_preferred_name():
    reply = fast_reply("bom dia", now=NOW, name="Talita")
    assert reply is not None
    assert reply.startswith("Bom dia, Talita!")


def test_mirrors_greeting_instead_of_time():
    reply = fast_reply("boa tarde", now=NOW)
    assert reply is not None
    assert reply.startswith("Boa tarde!")


def test_how_are_you():
    reply = fast_reply("tudo bem?", now=NOW)
    assert reply is not None
    assert "você" in reply.lower()


def test_greeting_and_how_are_you():
    reply = fast_reply("oi, tudo bem?", now=NOW)
    assert reply is not None
    assert reply.startswith("Bom dia!")
    assert "Tudo ótimo" in reply


def test_thanks():
    reply = fast_reply("obrigada!", now=NOW)
    assert reply is not None
    assert "nada" in reply.lower() or "imagina" in reply.lower()


def test_identity():
    reply = fast_reply("quem é você?", now=NOW)
    assert reply is not None
    assert "Zaia" in reply


def test_time():
    reply = fast_reply("que horas são?", now=NOW)
    assert reply == "Agora são 09:00."


def test_date():
    reply = fast_reply("que dia é hoje?", now=NOW)
    assert reply is not None
    assert "domingo" in reply
    assert "8 de novembro de 2026" in reply


def test_does_not_hijack_real_requests():
    assert fast_reply("oi, preciso de ajuda com o projeto", now=NOW) is None
    assert fast_reply("registre a decisão: usar OpenCode", now=NOW) is None
    assert fast_reply("qual é a capital da França?", now=NOW) is None
    assert fast_reply("me lembre de beber água em 30 minutos", now=NOW) is None
    assert fast_reply("", now=NOW) is None
