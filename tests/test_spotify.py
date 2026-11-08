from __future__ import annotations

import zora.spotify.controller as controller
from zora.spotify.controller import SpotifyCommand, execute
from zora.spotify.intents import parse_spotify_command


def test_parse_comandos_de_spotify():
    assert parse_spotify_command("pausa a música").action == "pause"
    assert parse_spotify_command("toca a música").action == "play"
    assert parse_spotify_command("próxima música").action == "next"
    assert parse_spotify_command("música anterior").action == "previous"
    assert parse_spotify_command("aumenta o volume").action == "volume_up"
    assert parse_spotify_command("abaixa o volume").action == "volume_down"
    assert parse_spotify_command("que música está tocando").action == "status"
    assert parse_spotify_command("spotify").action == "status"


def test_parse_toggle_de_volume():
    command = parse_spotify_command("volume 40")
    assert command is not None
    assert command.action == "volume_set"
    assert command.value == 40


def test_parse_ignora_frases_comuns():
    assert parse_spotify_command("continuar o projeto de automação") is None
    assert parse_spotify_command("pausar") is None


def test_execute_pause(monkeypatch):
    calls: list[str] = []

    def fake_tell(command: str) -> str:
        calls.append(command)
        return ""

    monkeypatch.setattr(controller, "is_installed", lambda: True)
    monkeypatch.setattr(controller, "_tell", fake_tell)
    message = execute(SpotifyCommand("pause"))
    assert "pausado" in message
    assert calls == ["pause"]


def test_execute_volume_up(monkeypatch):
    calls: list[str] = []

    def fake_tell(command: str) -> str:
        calls.append(command)
        if command == "sound volume":
            return "40"
        return ""

    monkeypatch.setattr(controller, "is_installed", lambda: True)
    monkeypatch.setattr(controller, "_tell", fake_tell)
    message = execute(SpotifyCommand("volume_up"))
    assert "50%" in message
    assert "set sound volume to 50" in calls
