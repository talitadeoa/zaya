from __future__ import annotations

import json

import httpx
import pytest

from zaya.config import Config, TelegramConfig
from zaya.interface.terminal import TerminalUI
from zaya.telegram.client import TelegramClient, TelegramError, _split_text
from zaya.telegram.service import TelegramService, telegram_permission


def _cfg(**overrides) -> TelegramConfig:
    data = {
        "enabled": True,
        "bot_token": "123:abc",
        "allowed_chat_id": "42",
    }
    data.update(overrides)
    return TelegramConfig(**data)


@pytest.mark.asyncio
async def test_send_message_endpoint_e_payload():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TelegramClient(_cfg(), http=http)
    result = await client.send_message("olá")
    await http.aclose()

    assert result["message_id"] == 7
    request = captured[0]
    assert request.url.path == "/bot123:abc/sendMessage"
    payload = json.loads(request.content)
    assert payload["chat_id"] == "42"
    assert payload["text"] == "olá"


@pytest.mark.asyncio
async def test_send_voice_usa_sendvoice(tmp_path, monkeypatch):
    import zaya.telegram.client as mod

    source = tmp_path / "a.mp3"
    source.write_bytes(b"fake")
    converted = tmp_path / "a.ogg"
    converted.write_bytes(b"OggS")
    monkeypatch.setattr(mod, "_to_ogg_opus", lambda _path: converted)

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TelegramClient(_cfg(), http=http)
    result = await client.send_audio(source, as_voice=True)
    await http.aclose()

    assert result["message_id"] == 5
    assert calls[0].endswith("/sendVoice")


@pytest.mark.asyncio
async def test_get_updates_e_download():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(
                200,
                json={"ok": True, "result": [{"update_id": 1, "message": {"text": "oi"}}]},
            )
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voz/1.ogg"}})
        if request.url.path == "/file/bot123:abc/voz/1.ogg":
            return httpx.Response(200, content=b"OggS")
        return httpx.Response(404, json={"ok": False, "description": "not found"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TelegramClient(_cfg(), http=http)
    updates = await client.get_updates()
    path = await client.get_file("file-1")
    data = await client.download_file(path)
    await http.aclose()

    assert updates[0]["update_id"] == 1
    assert path == "voz/1.ogg"
    assert data == b"OggS"


@pytest.mark.asyncio
async def test_erro_do_telegram_vira_excecao():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TelegramClient(_cfg(), http=http)
    with pytest.raises(TelegramError) as exc:
        await client.send_message("oi")
    await http.aclose()
    assert exc.value.status == 401


def test_split_text_respeita_limite():
    text = ("palavra " * 1000).strip()
    chunks = _split_text(text, limit=100)
    assert chunks
    assert all(len(chunk) <= 100 for chunk in chunks)
    assert " ".join(chunks).startswith("palavra")


def test_config_redige_token_do_telegram():
    cfg = Config()
    cfg.telegram.bot_token = "segredo"
    assert cfg.public_dict()["telegram"]["bot_token"] == "***"


def test_permissao_do_telegram_segue_o_flag():
    assert telegram_permission(_cfg(auto_approve=True))("write", ["a.txt"]) == "always"
    assert telegram_permission(_cfg(auto_approve=True))("bash", ["pytest"]) == "always"
    assert telegram_permission(_cfg())("write", ["a.txt"]) == "reject"
    assert telegram_permission(_cfg())("bash", ["pytest"]) == "reject"


def test_permissao_do_telegram_exige_chat_fixado():
    cfg = _cfg(auto_approve=True, allowed_chat_id="")
    assert telegram_permission(cfg)("bash", ["pytest"]) == "reject"


def test_service_restringe_chat_configurado():
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    service = TelegramService(cfg, None, None, None, TerminalUI(quiet=True), logging.getLogger("t"))
    assert service._allowed(42) is True
    assert service._allowed(99) is False


def test_service_adota_primeiro_chat_quando_vazio():
    import logging

    cfg = Config()
    service = TelegramService(cfg, None, None, None, TerminalUI(quiet=True), logging.getLogger("t"))
    assert service._allowed(7) is True
    assert service._allowed(7) is True
    assert service._allowed(8) is False


class _FakeOrchestrator:
    last_error = "Location not found: /Users/talita/zora"

    async def answer_once(self, text, speak=False, close=False):
        return ""


class _FakeTelegramClient:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_chat_action(self, *args, **kwargs) -> None:
        pass

    async def send_message(self, text, chat_id=None, **kwargs) -> None:
        self.sent.append(text)


@pytest.mark.asyncio
async def test_erro_do_agente_chega_no_telegram():
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    tg = _FakeTelegramClient()
    service = TelegramService(
        cfg, _FakeOrchestrator(), tg, None, TerminalUI(quiet=True), logging.getLogger("t")
    )

    await service._handle_update({"message": {"chat": {"id": 42}, "text": "oi"}})

    assert any("Location not found" in text for text in tg.sent)
    assert "Concluído." not in tg.sent

