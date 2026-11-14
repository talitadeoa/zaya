from __future__ import annotations

import asyncio
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


class _BootOrchestrator:
    """Orquestrador minimo para provar o que o `serve` liga ao subir."""

    last_error = ""

    def __init__(self) -> None:
        self.ligados: list[str] = []

    def start_briefing(self) -> None:
        self.ligados.append("briefing")

    def start_career(self) -> None:
        self.ligados.append("career")

    async def answer_once(self, text, speak=False, close=False):
        return ""


class _GetMeClient:
    async def get_me(self):
        return {"username": "teste"}

    async def get_updates(self, offset=None):
        # Dorme ate o teste cancelar, sem girar em loop.
        await asyncio.sleep(3600)
        return []


@pytest.mark.asyncio
async def test_serve_liga_briefing_e_rotina_de_vagas_ao_subir():
    """`telegram serve` nao chama bootstrap; os workers sobem no boot."""
    import asyncio
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    orch = _BootOrchestrator()
    service = TelegramService(
        cfg, orch, _GetMeClient(), None, TerminalUI(quiet=True), logging.getLogger("t")
    )

    task = asyncio.create_task(service.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert orch.ligados == ["briefing", "career"]


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



class _NewSessionOrchestrator:
    last_error = ""

    def __init__(self) -> None:
        self.novas = 0

    async def new_session(self) -> None:
        self.novas += 1

    async def answer_once(self, text, speak=False, close=False):
        return "resposta do agente"


@pytest.mark.asyncio
async def test_novo_cria_sessao_e_nao_chama_o_agente():
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    tg = _FakeTelegramClient()
    orch = _NewSessionOrchestrator()
    service = TelegramService(cfg, orch, tg, None, TerminalUI(quiet=True), logging.getLogger("t"))

    await service._handle_update({"message": {"chat": {"id": 42}, "text": "/novo"}})

    assert orch.novas == 1
    assert all("resposta do agente" not in text for text in tg.sent)
    assert any("sessão nova" in text for text in tg.sent)


class _ModelOrchestrator:
    last_error = ""

    def __init__(self) -> None:
        self.argumentos: list[str] = []

    async def model_messages(self, argument: str):
        self.argumentos.append(argument)
        if not argument:
            return [
                ("info", " 1. ● deepseek/deepseek-flash"),
                ("info", " 2. ○ deepseek/deepseek-chat"),
                ("info", "use /modelo <número> ou /modelo provider/model"),
            ]
        return [("success", f"modelo da próxima mensagem: {argument}"), ("info", "salvo")]

    async def answer_once(self, text, speak=False, close=False):
        return "resposta do agente"


@pytest.mark.asyncio
async def test_modelo_no_telegram_lista_modelos():
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    tg = _FakeTelegramClient()
    orch = _ModelOrchestrator()
    service = TelegramService(cfg, orch, tg, None, TerminalUI(quiet=True), logging.getLogger("t"))

    await service._handle_update({"message": {"chat": {"id": 42}, "text": "/modelo"}})

    assert orch.argumentos == [""]
    texto = tg.sent[-1]
    assert "deepseek/deepseek-flash" in texto
    assert "deepseek/deepseek-chat" in texto
    assert all("resposta do agente" not in item for item in tg.sent)


@pytest.mark.asyncio
async def test_modelo_no_telegram_troca_pelo_slug():
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    tg = _FakeTelegramClient()
    orch = _ModelOrchestrator()
    service = TelegramService(cfg, orch, tg, None, TerminalUI(quiet=True), logging.getLogger("t"))

    await service._handle_update(
        {"message": {"chat": {"id": 42}, "text": "/modelo deepseek/deepseek-chat"}}
    )

    assert orch.argumentos == ["deepseek/deepseek-chat"]
    assert "modelo da próxima mensagem" in tg.sent[-1]


@pytest.mark.asyncio
async def test_modelos_sem_argumento_no_telegram_lista():
    import logging

    cfg = Config()
    cfg.telegram.allowed_chat_id = "42"
    tg = _FakeTelegramClient()
    orch = _ModelOrchestrator()
    service = TelegramService(cfg, orch, tg, None, TerminalUI(quiet=True), logging.getLogger("t"))

    await service._handle_update({"message": {"chat": {"id": 42}, "text": "/modelos"}})

    assert orch.argumentos == [""]
    assert "deepseek/deepseek-flash" in tg.sent[-1]
